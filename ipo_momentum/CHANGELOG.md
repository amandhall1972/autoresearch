# Changelog

## v1.1 (2026-09-26)

v1.0 was committed unchanged (`6097028`), then run twice:

* **As given.** Yahoo Finance was unreachable (HTTP 403 at the sandbox egress
  proxy). The engine logged `Failed to build historical context. Aborting.`
  with no cause and exited 0.
* **Unmodified code on 944 real SWIGGY bars.** It never traded. The simulated
  breakout bar never closed, so it was never evaluated. The run ended with
  `Final Inventory State: set()`.

The defects below were found by an adversarial review of v1.0. Four reviewers
each took one area (live path, alpha math, execution, data/infra) and had to
reproduce every finding against the unmodified file. A second reviewer per area
then tried to refute each finding with an independent reproduction.

* **Verdict** is the second reviewer's call: ✔ confirmed, or ◐ confirmed
  with part of the claim overstated. No finding was refuted.
* **Severity** is the second reviewer's rating, often lower than the first
  reviewer's. All 40 findings were reproduced; none was refuted.
* A few items were caught while building v1.1. They are marked *self* and are
  pinned by tests like the rest.

Severity scale: **critical** means wrong or unsafe orders, or a crash of the
live path. **High** means a documented feature does not work. **Medium** means
the engine is wrong in realistic edge cases. **Low** means a minor but real
defect.

### Live tick ingestion and bar synthesis

| Sev | Verdict | v1.0 defect | v1.1 fix | Pinned by |
| --- | --- | --- | --- | --- |
| critical | ✔ | Real Kite ticks carry an int `instrument_token`, but history is keyed by tradingsymbol. The first bar close raised `KeyError` and killed the aggregator. | Token→symbol map from the instrument dump. Unknown tokens are dropped and counted. | `test_kite_full_mode_payload_uses_token_map_exchange_time_and_cumulative_volume`, `test_unknown_instrument_tokens_are_dropped_instead_of_crashing` |
| high | ◐ | A bar closed only when a later tick arrived. An illiquid name's bar, or the session's last bar, closed and traded at the next open. | Bar clock closes bars 2 s after their bucket ends. Signals older than 60 s are refused. | `test_bar_clock_closes_a_bar_that_gets_no_further_ticks`, `test_bar_clock_task_flushes_on_its_own`, `test_signal_is_stale_once_its_bar_is_long_closed` |
| high | ✔ | Bar volume summed `last_traded_quantity` per snapshot (about 100× too small), and LTP ticks invented 100 shares. RVOL on live bars was meaningless. | Volume comes from deltas of the cumulative `volume_traded`. LTP ticks count 0. | `test_kite_full_mode_payload_uses_token_map_exchange_time_and_cumulative_volume`, `test_ltp_mode_ticks_carry_no_volume`, `test_cumulative_volume_counter_restarts_each_session` |
| high | ✔ | Any exception in `process_ticks` ended the task silently. `main()` never checked its tasks and exited 0. | Per-tick error isolation, workers supervised by `main()`, exit code 1 when a worker dies. | `test_process_ticks_survives_an_exception_and_keeps_counting_tasks`, `test_a_crashed_worker_stops_the_engine_with_a_failure_code` |
| high | ✔ | The demo could never order: its breakout tick only opened a bar. | The simulator continues the real tape at the next session and sends the tick that closes the breakout bar. | `test_offline_run_on_real_bars_trades_the_simulated_breakout` |
| medium | ✔ | A live bar whose timestamp was already in history overwrote that row, losing the true open, high and low. | History fetches drop the still-forming bar. The first live bar is discarded if the engine started inside it. Overlapping live bars are refused. | `test_first_bar_is_discarded_when_the_engine_started_mid_bar`, `test_first_bar_is_kept_when_the_engine_started_before_it`, `test_yahoo_keeps_only_complete_traded_session_bars` |
| medium | ◐ | `main()` hard-coded the "listing" as now − 20 days, so the base and AVWAP moved with the run date. | `--listing-date`. Symbols whose history does not reach the listing are excluded unless `--allow-partial-history` is set. | `test_orchestrator_excludes_symbols_whose_history_misses_the_listing` |
| low | ◐ | Ticks were stamped with receive time (`exchange_timestamp` ignored). A naive timestamp corrupted the index. | Exchange time is used, naive host-local times are converted to IST, and a zeroed (1970) time falls back to receive time. | `test_kite_full_mode_payload_uses_token_map_exchange_time_and_cumulative_volume`, `test_zeroed_exchange_time_falls_back_to_receive_time` |
| low | ◐ | A late tick for an earlier bucket was merged into the current bar. | Late and out-of-order ticks are dropped and counted. | `test_late_and_out_of_order_ticks_are_dropped_not_merged` |
| *self* | — | (v1.1 draft) An engine started mid-session would have credited the day's entire cumulative volume to its first complete bar. That fakes a volume spike. | A day's first print counts from zero only if the engine was running at the 09:15 open. | `test_joining_mid_session_does_not_dump_the_days_volume_into_one_bar` |
| *self* | — | (v1.1 draft) A failing evaluation also skipped opening the next bar, which lost the triggering tick. | Evaluation errors are contained inside `close_bar`. | `test_process_ticks_survives_an_exception_and_keeps_counting_tasks` |

### Alpha engine

| Sev | Verdict | v1.0 defect | v1.1 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ◐ | Stop used `min()`, the *farther* of 1.5 ATR and AVWAP, while the comment says "whichever is closer". Risk was never bounded by 1.5 ATR. | `max()`, per the documented intent. | `test_breakout_fires_with_documented_stop_and_target`, `test_stop_uses_avwap_when_it_is_the_closer_floor` |
| medium | ◐ | With ≤ 150 bars, `Base_High` was the max over all bars *including the latest*, so `Close > Base_High` was impossible. Breakouts silently never fired in an IPO's first two sessions. | No breakout until the 150-bar base is complete. The base never includes later bars. | `test_no_breakout_until_the_base_is_complete`, `test_base_is_the_first_150_bars_only_and_excludes_the_latest_bar` |
| medium | ◐ | The base and AVWAP were anchored to the first *fetched* bar (clamped to 180 or 59 days), not the listing. | History must reach the listing date, or the symbol is excluded. | `test_orchestrator_excludes_symbols_whose_history_misses_the_listing`, `test_orchestrator_accepts_a_listing_just_before_a_weekend` |
| high | ✔ | Live-bar volume was not comparable to historical volume (see live path), which broke RVOL. | Cumulative-volume deltas. | as above |
| low | ✔ | The RVOL baseline included the current bar, so a 2.0 threshold was really 2.11 and RVOL was capped at 20. | The baseline uses preceding bars only, and a zero denominator gives NaN (no signal). | `test_rvol_baseline_excludes_the_current_bar`, `test_zero_volume_history_never_signals_or_divides_by_zero` |
| low | ✔ | RVOL spanned sessions and ignored the intraday profile. 75% of 09:15 bars read above 2×. | Opt-in `--rvol-mode time_of_day` (same slot, prior 10 sessions). That cuts the 09:15 rate to 17%. | `test_time_of_day_rvol_removes_the_opening_bar_bias`, `test_time_of_day_rvol_is_causal_and_keeps_the_real_breakout` |
| low | ✔ | ATR was a mean of High−Low, not true range, so gaps were ignored. | 14-bar mean of true range. | `test_atr_uses_true_range_so_gaps_count` |
| low | ✔ | Vendor bars that moved on zero volume deflated the RVOL baseline. | Such bars are left out of the baseline, which needs at least 15 of the 20 bars. | `test_vendor_gap_bars_are_left_out_of_the_volume_baseline` |

Look-ahead check: evaluating every prefix of the real data bar by bar
reproduces the vectorized scan exactly (`test_walk_forward_equals_vectorized_scan_on_real_data`,
`test_future_bars_do_not_change_past_indicators`).

### Execution and risk

| Sev | Verdict | v1.0 defect | v1.1 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ◐ | Any exception in `process_orders` killed the OMS silently, never called `task_done`, and left the symbol locked. | Per-signal error isolation. A failed entry releases the symbol. | `test_gateway_failure_releases_the_symbol_and_the_loop_keeps_running`, `test_unfilled_order_releases_the_symbol` |
| high | ◐ | No notional cap: a tight stop sized into crores of rupees. | `--max-position-value` cap (₹10 L default). Risk is measured from the limit price. | `test_quantity_risks_the_budget_and_respects_the_notional_cap`, `test_degenerate_risk_is_never_traded` |
| high | ✔ | The planned Kite call used `VARIETY_BO`, which Zerodha disabled in March 2020 and the SDK no longer defines. | `KiteOrderGateway`: a regular LIMIT entry, fill polling, then a GTT OCO on the filled quantity. Tested through the real SDK. | `test_sdk_has_no_bracket_order_variety`, `test_kite_entry_then_gtt_oco_on_the_filled_quantity`, `test_kite_partial_fill_times_out_cancels_rest_and_protects_what_filled`, `test_kite_rejected_entry_places_no_gtt`, `test_kite_gtt_failure_is_reported_loudly_and_the_position_is_kept` |
| medium | ✔ | `BROKER FILLED. Bracket Order Active.` was logged although nothing was sent. | Paper fills say `PAPER FILL (simulated, no order sent)`. Live logs follow real broker responses. | `test_paper_fill_is_labelled_as_simulated` |
| medium | ◐ | No position or exit management: the inventory was never released. | Paper positions run a simulated OCO on closed bars and release the symbol on exit. Kite exits are the broker's GTT. | `test_paper_target_exit_realizes_profit_and_frees_the_symbol`, `test_paper_stop_gapped_through_fills_at_the_open`, `test_paper_bar_touching_both_levels_assumes_the_stop_first`, `test_live_gateway_positions_are_not_simulated` |
| medium | ◐ | Signals executed at the closed bar's price with no age or LTP check. | 60 s age limit, and a LIMIT with a 0.5% slippage bound. The Kite entry is skipped if LTP ≤ stop or LTP > limit. | `test_signal_is_stale_once_its_bar_is_long_closed`, `test_kite_entry_is_skipped_when_ltp_left_the_band` |
| medium | ◐ | Prices were not on the tick grid, so the exchange rejects them. | Tick-size rounding (per instrument from Kite): the limit rounds up, the stop and target round down. | `test_plan_rounds_to_the_tick_grid_in_the_conservative_direction` |
| low | ✔ | "Fixed Fractional Risk" was really a fixed rupee amount. | Documented as fixed-rupee risk, with its limits stated. | README |
| low | ✔ | Rejected or duplicate signals were dropped without a log. | Every rejection is logged with its reason. Stop distances are rounded so float noise cannot cost a share. | `test_duplicate_signals_enter_once`, `test_float_noise_in_the_stop_distance_does_not_cost_a_share` |

### Data adapters and infrastructure

| Sev | Verdict | v1.0 defect | v1.1 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | Both adapters swallowed every failure (HTTP, auth, JSON, partial chunks) into a silent empty frame. | Every attempt is logged. Only transient errors (5xx, 429, network) are retried. | `test_yahoo_failures_are_logged_and_only_transient_ones_retried`, `test_kite_permanent_errors_are_not_retried`, `test_kite_partial_history_failure_fails_closed_and_says_so` |
| high | ✔ | Exit code 0 when history failed or a worker crashed. | 0 success, 1 no history or a crashed worker, 2 bad configuration, 130 Ctrl-C. | `test_unreachable_history_aborts_with_a_nonzero_exit`, `test_unreadable_csv_exits_with_an_error`, `test_a_crashed_worker_stops_the_engine_with_a_failure_code` |
| high | ✔ | `boot()` kept only symbol→token, so there was no reverse map for ticks. | Both maps, plus per-instrument tick sizes. | `test_kite_history_is_chunked_contiguously_with_sdk_compatible_arguments` |
| high | ✔ | `verify_hardware_ip` was fail-open: its result was ignored at the call site. | `--expect-ip` aborts on mismatch. Live orders without it log a warning. | `test_hardware_ip_mismatch_is_fatal` |
| medium | ✔ | The still-forming bar was stored as complete. | Bars still forming at the fetch time are dropped. | `test_yahoo_keeps_only_complete_traded_session_bars` |
| medium | ◐ | Null Yahoo rows became zero-volume bars, and leading gaps were back-filled from *future* closes. | A bar exists only if it traded. There is no back-fill. | `test_harmonize_never_backfills_from_the_future` |
| medium | ✔ | Yahoo silently clamped the window to 59 days. | The clamp is logged, and the listing-anchor check applies to every adapter. | `test_orchestrator_excludes_symbols_whose_history_misses_the_listing` |
| low | ✔ | A naive listing date crashed `build_the_ground` with `TypeError`. | Naive dates are read as IST. | `test_orchestrator_accepts_naive_listing_dates` |
| low | ◐ | Off-grid Yahoo rows (pre-open, the 15:29:59 "live" row) were kept as bars. | Only bars on the 5-minute session grid are kept. | `test_yahoo_keeps_only_complete_traded_session_bars` |
| low | ◐ | HTTP bodies were read on the event-loop thread, and responses were never closed. | The whole request runs in a worker thread, inside a `with` block. | code structure (`_read_url` via `asyncio.to_thread`); the Yahoo tests exercise it |
| low | ✔ | The IST fallback caught `ImportError`, but a missing tz database raises `ZoneInfoNotFoundError`, so the import crashed (e.g. Windows without `tzdata`). | Any failure falls back to a fixed +05:30 offset. `tzdata` is declared for Windows. | `test_ist_falls_back_to_fixed_offset_without_a_tz_database` |
| low | ✔ | `logging.basicConfig` ran at import and took over the host application's logging. | Logging is configured only by the CLI. | `test_importing_the_engine_does_not_configure_logging` |
| low | ◐ | Only `5m` was mapped for Kite, and the 90-day chunk exceeds the 1-minute limit. | Interval table with per-request day limits. Unknown intervals raise. | `test_kite_interval_table_bounds_each_request` |
| low | ✔ | The rate limiter was not FIFO, so a waiter could starve. | Slots are reserved in arrival order. | `test_limiter_serves_waiters_in_arrival_order`, `test_rate_limiter_admits_at_most_max_calls_per_window` |

### Added

* `CsvReplayAdapter` and 944 real NSE 5-minute SWIGGY bars, with provenance
  in `data/README.md`, so the whole pipeline runs offline.
* A CLI (`--source`, `--listing-date`, risk limits, `--rvol-mode`,
  `--live-feed`, `--live-orders`, `--expect-ip`, …) with meaningful exit codes.
* `KiteOrderGateway` and `start_kite_feed` (KiteTicker in full mode).
* Tests (89 cases), `uv.lock`, and `README.md`.
