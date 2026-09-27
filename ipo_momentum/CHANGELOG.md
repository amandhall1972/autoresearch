# Changelog

## v1.5 (in progress)

Found by the v1.4 test matrix, before round 5 reported. One run on Python
3.10 / pandas 2.2 failed `test_limiter_serves_waiters_in_arrival_order`. That
was not noise: under CPU load, the limiter served waiters out of order in 1–2
of 300 runs, on both Python lines.

| Sev | v1.4 defect | Fix | Pinned by |
| --- | --- | --- | --- |
| low | `TokenBucketRateLimiter` reserved start times in arrival order under its FIFO lock, but slept after releasing it. A waiter whose clock read came slightly late (scheduling jitter) could compute a longer sleep than the next waiter and be served after it, breaking the documented FIFO guarantee. The rate limit itself always held. | Each caller waits for its reserved start while still holding the lock. Reserved starts never decrease, so throughput is unchanged. Under the same load: 0 of 1,000 runs out of order. | `test_limiter_wakes_waiters_in_order_even_when_their_sleeps_differ` (deterministic: injects the late clock read; fails on v1.4) |

## v1.4 (2026-09-27)

v1.3 went through a fourth adversarial review on a frozen snapshot (`af8816f`),
with the same four areas: orders, live feed, lifecycle, and data and docs. This
time every skeptic ran. All 16 findings were reproduced by their skeptic: 14
confirmed, 2 with part of the claim overstated, none refuted. Again, most are
gaps in the previous round's own fixes.

Every v1.4 regression test fails on the v1.3 snapshot and passes here, except
these controls:
- the ten unchanged cases of `test_broker_refusals_are_permanent_and_outages_are_not`;
- `test_a_gtt_that_was_never_created_is_retried`, a v1.3 pin whose assertions
  were loosened for the new book polling;
- `test_the_suite_collects_without_the_kite_extra`, whose defect was in v1.3's
  *test file*: it fails with that file.

Verdict: ✔ confirmed by the independent skeptic, ◐ confirmed with part of the
claim overstated. Severity is the skeptic's rating.

### Real-money safety: orders

| Sev | Verdict | v1.3 defect | v1.4 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | GTT idempotency read the book **once**, straight after an ambiguous failure. A GTT the broker booked a moment later still got a second whole-position GTT, with no alert. With three late 504s, three GTTs existed while the alert said "no GTT could be placed". | After an ambiguous failure the book is polled for `cancel_grace`, through read errors, before any retry. After any ambiguous attempt the book is watched once more, and a late duplicate raises `GTT DUPLICATE … DELETE ALL BUT GTT n`. If every request failed ambiguously, the alert says a GTT may exist. | `test_a_gtt_booked_after_the_first_book_read_is_adopted`, `test_a_gtt_booked_after_the_retry_is_reported_as_a_duplicate`, `test_when_every_gtt_request_fails_ambiguously_the_alert_says_one_may_exist` |
| medium | ✔ | Adoption looked only at `active` GTTs. A lost-reply GTT that had already **triggered** was ignored, and a second one was armed on shares already being sold. | A triggered match is adopted and never re-armed, with an alert that the exit has fired. | `test_a_lost_reply_gtt_that_already_triggered_is_adopted_not_rearmed` |
| medium | ✔ | One failed book read after any GTT failure ended protection for the run. That included a connect timeout, which never left the machine, and a 429. | A connect timeout is retried without the book. The book poll tolerates read errors and gives up only if no read in `cancel_grace` succeeded. The pre-arming snapshot is tried 3 times. | `test_a_gtt_request_that_never_left_is_retried_without_the_book`, `test_one_failed_book_read_after_an_ambiguous_failure_does_not_end_protection` |
| low | ✔ | `OrderException` and `InputException` were permanent by name, whatever the HTTP status. A 503 `OrderException` therefore skipped the tag lookup and released the symbol. | Only auth errors are classified by name; every other Kite error by its HTTP status. | `test_a_5xx_order_exception_is_looked_up_not_taken_as_a_refusal`, `test_broker_refusals_are_permanent_and_outages_are_not` (2 new cases) |
| low | ◐ | `settle_timeout` assumed each SDK call ends within the client timeout, but `requests` applies it per connect and per socket read. A fill that completed during shutdown also had no `Open` line in the halt report. Overstated: the overrun path is by design (the entry is awaited, never interrupted). | The budget is documented as an estimate and now covers the GTT polls. The halt report lists fills completed during shutdown as open positions. Per-call `wait_for` was rejected: abandoning a worker thread would bring back the duplicate-GTT race. | `test_the_halt_report_lists_fills_that_completed_during_shutdown` |

### Real-money safety: lifecycle

| Sev | Verdict | v1.3 defect | v1.4 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | After a stop signal, the entry in flight still waited out its 30 s fill window. `docker stop` (SIGKILL after 10 s by default) killed the engine with the LIMIT BUY working: no cancel, no GTT, no report. This is not a regression: v1.2 waited too, and died at once on SIGTERM. | A stop signal sets the gateway's `stopping` flag at once. The fill wait ends, the remainder is cancelled, and whatever filled gets its GTT. No new entry is sent. README documents the supervisor stop grace. | `test_shutdown_stops_waiting_for_the_fill_and_cancels_the_remainder_at_once`, `test_no_entry_is_sent_once_shutdown_began`, `test_shutdown_tells_the_gateway_to_stop_waiting_for_fills` |
| medium | ✔ | The new SIGHUP/SIGTERM hooks overrode an inherited `SIG_IGN`, so an engine started with `nohup` stopped when the SSH session dropped. Afterwards `SIG_DFL` replaced whatever handler had been there. | A signal the parent ignored stays ignored, and replaced handlers are restored. | `test_nohup_is_honoured`, `test_handlers_the_engine_replaced_are_restored_afterwards` |
| low | ✔ | The signal handlers were released before the halt report and before `asyncio.run`'s teardown, which can wait seconds for a worker thread. A signal then turned exit 1 into 130, or killed the process. | `run()` keeps the stop signals owned (and ignored) until `asyncio.run` has returned, then restores them. `main()`'s result is kept. | `test_a_signal_during_teardown_cannot_replace_the_result` (2 cases) |
| low | ✔ | `accepting` was cleared only after two awaits, so a back-fill landing at shutdown could still enter a position. | It is cleared in the signal handler, and as the first statement of shutdown. | `test_shutdown_stops_taking_signals_before_anything_can_yield` |

### Live feed

| Sev | Verdict | v1.3 defect | v1.4 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | v1.3's counter-glitch fix adopted the next print unchecked. Two zeroed packets in a row, or one right after a reconnect, still credited the whole day's volume to one bar and could fake a breakout. The recovery trade's price was also dropped. | A per-day high-water mark: a print below it never becomes the baseline, in any feed epoch. The recovery print keeps its trade and its price. | `test_consecutive_zeroed_packets_never_become_the_baseline`, `test_a_zeroed_first_packet_after_a_reconnect_is_not_the_baseline`, `test_the_print_after_a_glitch_keeps_its_trade_and_price`, `test_a_counter_going_backwards_is_rebaselined_not_reset_to_zero` (corrected) |
| medium | ✔ | A feed stall across a bucket end discarded that bar and back-filled it, but also credited the stalled tail to the next live bar, so the tail was in history twice. | A liveness failure also re-baselines that symbol's volume counter. | `test_a_stall_across_a_bucket_end_does_not_credit_its_tail_twice` |
| low | ◐ | Liveness and bar closing compared host receive time with exchange-time buckets. More than 2 s of feed lag, or a fast host clock, let a bar close without its last prints. Overstated: it predates v1.3 (v1.2 behaves identically) and needs more than 2 s of skew. | Feed lag is measured on verified trades from their exchange timestamps (the maximum over a minute; a subscribe snapshot's stale timestamp is not lag), and closing and liveness are judged in feed time. Heartbeats are stamped with their receive time. The bar clock never closes a bar while received ticks are still queued. | `test_bars_close_in_feed_time_when_the_feed_lags`, `test_the_feed_lag_is_measured_on_trades_not_on_snapshots`, `test_the_bar_clock_waits_for_ticks_already_received` |

### Data, CLI and documentation

| Sev | Verdict | v1.3 defect | v1.4 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | v1.3's 15:30 cap made every bar that starts at or after 15:30 count as complete at once. A Kite fetch during a special session (Muhurat trading) stored its forming candle as a bar. | Only bars that start before the close are capped. | `test_a_special_sessions_forming_bar_is_still_dropped` |
| low | ✔ | `test_execution.py` imported `requests` before its `kiteconnect` skip, so README's non-uv setup (pandas, numpy, pytest) collected nothing at all. | `requests` is imported after the skip. | `test_the_suite_collects_without_the_kite_extra` |
| low | ✔ | "Every v1.3 regression test fails on v1.2 except seven controls" missed the corrected shutdown pin, which pins a v1.1 defect and so passes on v1.2. | Reworded below. | — |
| low | ✔ | README and CHANGELOG called the 180 days "Kite's" limit. It is the engine's own `max_lookback_days`, for every source, with no CLI flag, and the exclusion message blamed the history. | Documented correctly. `--max-lookback-days` sets it, and the exclusion names the lookback. | `test_the_lookback_can_reach_an_older_listing`, `test_the_lookback_must_be_a_positive_whole_number_of_days` (4 cases), `test_orchestrator_excludes_symbols_whose_history_misses_the_listing` |

## v1.3 (2026-09-27)

v1.2 went through a third adversarial review on a frozen snapshot (`6af258c`).
Four reviewers took one area each (orders, live feed, lifecycle, alpha and
docs) and had to reproduce every finding against the snapshot. They found 24,
which reduce to the 23 defects below (R3-MAIN-5 and R3A-5 are the same bug).
As in round 2, several are defects in the previous round's own fixes.

Only the orders skeptic ran; the other three hit the review's spend limit. Each
of their 17 findings was therefore reproduced again, behaviorally, against the
frozen v1.2 snapshot, the signal cases under both Python 3.10 and 3.11. A test
that fails only because a new API is missing did not count as a reproduction.

Every v1.3 regression test fails on the v1.2 snapshot and passes here, except
seven controls that pin error classifications v1.2 already had right (the
`InputException`, `TokenException`, 429, 504, `DataException`, 500 and
`ReadTimeout` cases of `test_broker_refusals_are_permanent_and_outages_are_not`),
and the corrected `test_shutdown_waits_for_the_order_in_flight`, which pins the
v1.1 shutdown defect: it fails on v1.1 and passes on v1.2. (The last clause was
added in v1.4.)

Verdict: ✔ confirmed by the independent skeptic, ◐ confirmed with part of the
claim overstated, ● skeptic did not run; reproduced against the v1.2 snapshot
before fixing. Severity is the skeptic's rating for ✔/◐ and the finder's for ●.

### Real-money safety: orders

| Sev | Verdict | v1.2 defect | v1.3 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | A GTT retry after a lost reply (a read timeout or 504, which may still have created the GTT) armed a **second OCO GTT for the whole position**. The engine knew only the second one and raised no alert. When triggered, both sell. v1.2's own test pinned the blind retry. | GTT placement is idempotent. The GTT book's ids are read before arming. After an ambiguous failure the book is checked and a matching GTT created since then is adopted. An identical GTT from an earlier run is never adopted. An unreadable book stops the retries with a `GTT STATE UNKNOWN` alert. | `test_a_lost_gtt_reply_adopts_the_existing_gtt_instead_of_arming_a_second`, `test_an_identical_gtt_from_an_earlier_run_is_never_adopted`, `test_an_unreadable_gtt_book_stops_the_retries_and_raises_an_alert`, `test_a_lost_gtt_reply_after_an_unreadable_snapshot_is_reported_not_guessed`, `test_a_gtt_that_was_never_created_is_retried` |
| high | ✔ | After a lost `place_order` reply, one immediate `orders()` snapshot was taken as proof that the entry never reached the exchange, and the symbol was released. The broker can book an order moments later. | The tag is polled in the order book for `cancel_grace` (15 s). If it never shows, or the book cannot be read, `OrderStateUnknown` keeps the symbol blocked. Only a connect timeout (never sent) or a broker refusal releases it at once. | `test_an_order_booked_after_the_lost_reply_is_found_by_polling_the_book`, `test_an_ambiguous_failure_whose_order_never_shows_keeps_the_symbol_blocked`, `test_a_request_that_never_left_the_machine_releases_the_symbol_without_a_lookup`, `test_an_unverifiable_place_order_failure_keeps_the_symbol_blocked` |
| medium | ◐ | Cancelling the router during `place_gtt` could not stop its worker thread, and the abort path then placed another GTT. End to end through `main()` this gave three GTTs for one position. Overstated: it needs a degraded API during an entry at shutdown. | Everything from `place_order` to the GTT runs as one shielded task that cancellation never interrupts. There is no separate abort path to duplicate it. | `test_a_shutdown_during_a_slow_gtt_placement_arms_exactly_one_gtt`, `test_an_entry_interrupted_by_shutdown_still_settles_and_its_fill_is_protected_once` |
| medium | ◐ | (v1.2 regression) A partial fill the engine had already seen got **no GTT** when the cancel of the remainder could not be confirmed. | The known filled quantity is protected. `OrderStateUnknown` then carries the fill, so the router records the position and keeps the symbol blocked. | `test_a_partial_fill_whose_cancel_is_unconfirmed_is_still_protected_and_recorded` |
| medium | ✔ | `settle_timeout` was not an upper bound on an entry: it ignored the SDK's 7 s per call. `main()` then interrupted healthy settlements, and the abort path restarted them. | The budget counts every SDK call at its client timeout. Exceeding it no longer interrupts anything: shutdown waits for the shielded entry on its own deadlines and exits 1. | `test_a_failed_shutdown_exits_1_even_when_a_signal_started_it`, `test_an_entry_interrupted_by_shutdown_still_settles_and_its_fill_is_protected_once` |
| low | ✔ | The abort path raced a `place_order` still in flight, and could report that the entry "never reached the exchange". | The same shielded task: the in-flight call's own result is used. | `test_a_shutdown_during_place_order_still_settles_the_entry` |
| low | ✔ | Kite delivers `MarginException`, `HoldingException`, `UserException` and the like as `GeneralException` with HTTP 400, and `OrderException` was treated as transient. Refusals were handled as if the order might exist. | Kite errors are classified by HTTP status (4xx other than 429 is permanent), and `OrderException` is permanent. | `test_broker_refusals_are_permanent_and_outages_are_not` (10 cases, 7 controls) |

### Real-money safety: lifecycle

| Sev | Verdict | v1.2 defect | v1.3 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ● | SIGTERM and SIGHUP (`kill`, `systemctl`/`docker stop`, a closed SSH session) killed the process mid-entry: no cancel, no GTT, no report. Reproduced: exit −15, no fill, no report. | SIGINT, SIGTERM and SIGHUP all take the orderly shutdown and exit `128 + N`. | `test_kill_and_hangup_settle_the_entry_in_flight_like_ctrl_c` (2 cases) |
| medium | ● | Where the loop cannot own signals (Windows), Ctrl-C relied on `asyncio.run`. Python 3.10 cancelled every task, the entry in flight included; 3.11 lost the halt report to a second Ctrl-C. Reproduced on both with the loop handler disabled. | A plain signal handler hands the signal to the loop thread-safely. Further signals during shutdown are logged and ignored. | `test_without_loop_signal_handlers_ctrl_c_still_settles_and_a_second_one_is_ignored` |
| medium | ● | The listing check compared dates only, and both lookback clamps (the engine's 180-day `max_lookback_days`, and Yahoo's 59 days) kept the time of day. A clamp landing inside the listing session was accepted with the listing morning, usually the heaviest bars, missing from the base and AVWAP. A listing date in another time zone shifted by a day. | Clamps start at the next 00:00 IST, and a clamped start after the listing excludes the symbol. A listing date is its own calendar date in any zone. | `test_a_lookback_clamp_never_starts_inside_the_listing_session`, `test_yahoos_window_clamp_snaps_to_the_next_midnight`, `test_a_listing_date_means_its_own_calendar_date_in_any_zone` (2 cases) |
| low | ● | A signal during a failing shutdown turned exit 1 into 130. Reproduced: 130. | Failures outrank signals. | `test_a_failed_shutdown_exits_1_even_when_a_signal_started_it` |
| low | ● | `--run-seconds nan` passed validation and ran forever. Negative risk, cap and reward, and a NaN RVOL threshold, ran, rejected or never fired every signal, and exited 0. | Numeric flags must be finite and > 0 (≥ 0 for `--run-seconds`), else exit 2. | `test_numeric_arguments_must_be_finite_and_in_range` (6 cases) |

### Live feed

| Sev | Verdict | v1.2 defect | v1.3 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ● | When an outage crossed a bucket end, the bar clock closed the bar the websocket drop had cut short as complete. That bar was evaluated and could trade. Only a reconnect marked bars incomplete. | `on_close` marks every forming bar incomplete, and nothing opened before the reconnect is kept. With a live feed, a bar closed by the clock also needs a feed message after its bucket's end. | `test_a_feed_drop_discards_the_bar_it_cut_short_and_every_bar_until_the_reconnect`, `test_a_live_bar_closed_by_the_clock_needs_the_feed_alive_to_its_end` |
| medium | ● | A failed Kite back-fill returned an empty frame, which reads as "nothing traded in the hole", so the bar after the hole was evaluated across it. | `BrokerAdapter.backfill` must raise when the fetch fails. Kite's is a strict fetch (raises after its retries, or for an unknown symbol), and `main()` wires it. | `test_a_kite_backfill_raises_instead_of_returning_nothing` (2 cases), `test_a_failed_kite_backfill_leaves_the_bar_unevaluated`, `test_sources_without_a_live_feed_refuse_to_backfill` |
| medium | ● | A half-open websocket was never detected. KiteTicker's ping loop never sends a ping, so no close, reconnect or give-up ever fires, and the engine ran blind. | Every message, heartbeats included, stamps liveness. A watchdog stops the run (exit 1) after 15 s of silence during the session. | `test_a_silent_websocket_during_the_session_stops_the_engine`, `test_the_feed_watchdog_tolerates_quiet_evenings_and_fresh_heartbeats` |
| medium | ● | A counter that went backwards became the new baseline, so the next print re-counted the dip. A zeroed packet would credit the whole day's volume to one bar. v1.2's own test pinned the over-count. | The baseline is invalidated and re-set by the next print, so nothing is counted twice. | `test_a_counter_going_backwards_is_rebaselined_not_reset_to_zero` (corrected) |
| low | ● | After a (re)connect, the re-baselining print opened a bar even when nothing traded, giving flat zero-volume bars. | Only a verified positive volume delta opens a bar. | `test_a_rebaselining_print_after_a_reconnect_opens_no_bar` |

### Data, CLI and documentation

| Sev | Verdict | v1.2 defect | v1.3 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ● | The v1.2 notes claimed every v1.2 behavior test fails on v1.1. Six pass, and the pin cited for the critical shutdown fix could not detect it: its fill landed inside v1.1's 10 s drain. | The pin's fill now lands after 10 s and fails on v1.1. Both documents name the controls. | `test_shutdown_waits_for_the_order_in_flight` |
| low | ● | Four malformed Yahoo shapes raised instead of giving an empty frame: a non-list `timestamp`, a list `indicators`, a dict `quote`, and non-numeric stamps. | Types are validated. A bad timestamp drops only its own row. | `test_malformed_yahoo_payloads_give_an_empty_frame` (4 new cases), `test_a_bad_yahoo_timestamp_drops_only_its_own_row` |
| low | ● | The session's last 30m/60m bar (15:15–15:30) was treated as forming until 15:45/16:15, and dropped. | A bar ends at the 15:30 close if that comes first. | `test_the_sessions_last_bar_is_complete_at_the_close` (2 cases) |
| low | ● | The CSV symbol check was a prefix match, so NTPCGREEN's file replayed as NTPC unflagged. | The file name's first `_`-separated token must equal the symbol. | `test_a_csv_whose_name_merely_starts_with_the_symbol_is_flagged` |
| low | ● | README's headline real-orders command omitted the required `--listing-date`, so it exited 2 as written. | Corrected. | — |
| low | ● | `data/README.md` said zero-volume bars depress the indicators. v1.2 leaves them out of RVOL, and they carry no AVWAP weight. | Corrected, and checked on the bundled data. | — |

Found while fixing: two new tests first passed on v1.2 for the wrong reason,
because `pytest.raises(Exception)` accepted the `TypeError` of an unknown
keyword. Main's back-fill became a named adapter method (`backfill`), and both
tests now assert the exact exception and the wiring `main()` uses.

Renamed or replaced from v1.2:
`test_an_entry_interrupted_by_shutdown_is_cancelled_and_its_fill_protected` →
`test_an_entry_interrupted_by_shutdown_still_settles_and_its_fill_is_protected_once`,
`test_a_place_order_failure_that_never_reached_the_exchange_releases_the_symbol` →
`test_a_request_that_never_left_the_machine_releases_the_symbol_without_a_lookup`,
`test_a_transient_gtt_failure_is_retried` → `test_a_gtt_that_was_never_created_is_retried`.

## v1.2 (2026-09-26)

v1.1 went through the same adversarial review as v1.0, run on a frozen snapshot
(`ad48d8d`). Four reviewers had to reproduce each finding against it, and four
skeptics then tried to refute each one. The 35 raw findings reduce to the 24
defects below, because several reviewers independently found the same problem.
Several are defects in v1.1's own fixes. Every behavior test added for v1.2
fails on the v1.1 snapshot and passes here, except six controls that pin
behavior v1.1 already had: `test_a_breakout_is_a_cross_so_consecutive_closes_above_fire_once`,
`test_a_re_cross_after_falling_back_into_the_base_counts_again`,
`test_paper_stop_gapped_through_fills_at_the_open_when_above_the_stop_limit`,
`test_orchestrator_needs_the_listing_session_itself[listing0-True]`, and
`test_malformed_yahoo_payloads_give_an_empty_frame[payload0]` and `[payload1]`.
(Corrected in v1.3. This sentence first claimed no exceptions, and the shutdown
pin could not detect its defect until v1.3 moved its fill past v1.1's drain.)

Verdict: ✔ confirmed, ◐ confirmed with part of the claim overstated. All 35
findings were reproduced by their skeptic; none was refuted. Severity is the
skeptic's rating.

### Real-money safety

| Sev | Verdict | v1.1 defect | v1.2 fix | Pinned by |
| --- | --- | --- | --- | --- |
| critical | ✔ | The synthetic tape stayed on under `--live-orders`/`--live-feed`. Its bars are stamped in the future, so the staleness check passed them, and invented ticks sent a **real** BUY and GTT. The README's live command even omitted `--no-simulate`. With a live feed, the synthetic bars also blocked every real tick. | `--live-orders` requires `--live-feed`, and a live feed turns the tape off. The router refuses any signal whose bar has not closed by its clock. | `test_real_orders_require_a_live_feed_so_the_synthetic_tape_can_never_trade`, `test_a_dead_websocket_stops_the_engine_and_a_live_feed_never_runs_the_synthetic_tape`, `test_signal_for_a_bar_that_has_not_closed_is_refused` |
| critical | ✔ | Shutdown (timeout, Ctrl-C, crashed worker) cancelled the router after a 10 s drain, while an entry can take 30 s. The working LIMIT order was never cancelled, any fill got no GTT, and the run exited 0. | Shutdown stops producers, stops taking signals, then waits for the gateway's bounded settle time. An interrupted entry is cancelled and any fill protected before the cancellation propagates. | `test_shutdown_waits_for_the_order_in_flight`, `test_an_entry_interrupted_by_shutdown_is_cancelled_and_its_fill_protected` |
| critical | ✔ | Any exception after `place_order` (a transient `order_history` error, a lost reply) released the symbol and abandoned a live BUY: no cancel, no GTT, and a second entry became possible. | Polling retries through transient errors. A lost reply is resolved by the order's unique tag. An unconfirmable state raises `OrderStateUnknown`: the symbol stays blocked, the run exits 1, and the order is listed under `ATTENTION`. | `test_transient_order_history_errors_keep_polling_instead_of_abandoning_the_order`, `test_a_lost_place_order_reply_is_found_by_its_tag_and_protected`, `test_a_place_order_failure_that_never_reached_the_exchange_releases_the_symbol`, `test_an_unverifiable_place_order_failure_keeps_the_symbol_blocked`, `test_an_api_refusal_releases_the_symbol_without_a_lookup` |
| high | ✔ | After cancelling a timed-out entry, one read was taken as final. A cancel that failed or had not landed released the symbol, and fills after that read got no GTT. | After cancelling, poll until the exchange confirms a terminal state (else `OrderStateUnknown`). The GTT covers the final filled quantity. | `test_a_cancel_that_never_lands_keeps_the_symbol_blocked`, `test_the_gtt_covers_the_final_filled_quantity_after_a_cancel` |
| medium | ◐ | Without `--listing-date`, Kite (including live orders) silently anchored the base at now − 20 days. | `--source kite` requires `--listing-date`. The demo default is logged as demo semantics. | `test_kite_requires_the_real_listing_date`, `test_orchestrator_without_a_listing_date_anchors_at_the_first_bar_and_says_so` |
| medium | ◐ | The stop leg was a SELL LIMIT only 0.5% under its trigger, so a small gap left it unfilled. Paper booked the stop at the open, which was optimistic. | 2% stop-limit buffer. Paper plays the same limit mechanics: fill at the open, at the limit on recovery, or rest unfilled with a CRITICAL log. | `test_paper_gap_below_the_stop_limit_fills_only_if_price_recovers_to_it`, `test_paper_stop_gapped_through_fills_at_the_open_when_above_the_stop_limit` |
| self | — | A transient GTT failure left a filled position unprotected. | The GTT is retried on transient errors. | `test_a_transient_gtt_failure_is_retried` |

### Live data and time

| Sev | Verdict | v1.1 defect | v1.2 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | Time bomb: the demo tape is dated 2026-09-28, but staleness and bar timing used the wall clock. From 09:26 IST that day, the headline demo and 8 tests would have stopped trading or failed. | Simulated runs keep time by the tape (`LiveTickAdapter.market_time`), and tests pin their clocks. | `test_the_offline_demo_does_not_depend_on_the_wall_clock`; the whole suite passes with the clock shifted into that window and to 2027 |
| high | ✔ | After a websocket outage or a late connect, the first print's cumulative-volume delta held every share traded while blind. That one bar got a fake RVOL spike and fired false breakouts. A counter dip reset the baseline to zero. | Feed epochs: every (re)connect re-baselines the counters and discards the bar spanning the blind spot. A counter dip is re-baselined, not reset. | `test_reconnect_rebaselines_volume_and_discards_the_blind_bar`, `test_a_late_feed_connect_does_not_dump_the_days_volume_into_one_bar`, `test_a_counter_going_backwards_is_rebaselined_not_reset_to_zero`, `test_a_reconnect_marks_the_forming_bar_incomplete` |
| medium | ✔ | A dead Kite websocket (retries exhausted) went unnoticed: the engine ran blind and exited 0. | `on_noreconnect` feeds a supervised watch, and the run stops with exit 1. | `test_a_dead_websocket_stops_the_engine_and_a_live_feed_never_runs_the_synthetic_tape` |
| medium | ✔ | Quote and depth updates without a trade became flat zero-volume bars, which halved the RVOL baseline on illiquid names and fired false breakouts. | Prints without a trade make no bar, and zero-volume bars never enter the baseline. | `test_quote_updates_without_a_trade_do_not_create_bars`, `test_flat_zero_volume_bars_stay_out_of_the_volume_baseline` |
| low | ◐ / ✔ | The bar forming at start-up (or at a reconnect) was permanently missing. The next bar was then evaluated across the hole, which could fire a "first crossing" one bar late at a worse price. | Holes are back-filled from broker history before the next bar is evaluated. Without a history source, that bar is not evaluated. | `test_a_hole_is_backfilled_from_the_broker_before_the_next_bar_is_evaluated`, `test_without_a_backfill_source_a_bar_after_a_hole_is_not_evaluated` |
| low | ✔ | Late or out-of-order prints moved the volume baseline before being dropped, so their shares vanished. | They are dropped before the baseline moves. | `test_a_late_print_does_not_move_the_volume_baseline` |
| low | ✔ | Ctrl-C, the only way to stop `--run-seconds 0`, skipped the halt report. | A SIGINT handler runs the orderly shutdown, prints the report and exits 130. | `test_ctrl_c_runs_the_orderly_shutdown_and_prints_the_halt_report` |

### Alpha

| Sev | Verdict | v1.1 defect | v1.2 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The 150-bar base was documented as "two sessions". On a real listing (continuous trading from 10:00), it runs into session 3 and swallows a day-3 opening breakout. | Documented accurately. `--base-sessions N` defines the base by sessions. | `test_base_sessions_lets_a_day_three_opening_breakout_count` |
| low | ✔ | `Base_High` on rows inside the base used later bars (look-ahead in the public `indicators()`). | Undefined (NaN) inside the base. | `test_base_high_is_never_visible_inside_the_base` |
| low | ✔ | With `base_bars < rvol_lookback`, `scan()` reported breakouts that `evaluate()` never emits. | The same warm-up mask in both. | `test_scan_matches_walk_forward_evaluate_when_the_base_is_shorter_than_the_rvol_window` |
| low | ◐ | The docstring promised "the first close above the base", but the code, like v1.0's, counts every crossing. | Documented as crossing semantics, and a re-cross is pinned. | `test_a_re_cross_after_falling_back_into_the_base_counts_again` |
| low | ◐ | `time_of_day` RVOL took about 100 ms per evaluation, mostly `strftime`, on the event loop. | Integer slot key: identical output, about 6× faster. | the `time_of_day` tests (identical values) |

### Data, CLI and documentation

| Sev | Verdict | v1.1 defect | v1.2 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | A 5-day listing tolerance accepted histories missing the listing sessions (e.g. after Yahoo's 59-day clamp). | The first bar must come from the listing session itself. | `test_orchestrator_needs_the_listing_session_itself` (3 cases) |
| low | ✔ | Failures outside the retry loops escaped as tracebacks: an expired token at boot, malformed Yahoo payloads, offset-aware CSV timestamps. | Contained per symbol. Malformed payloads give an empty frame with an error log, and offsets are converted. | `test_broker_start_up_failure_aborts_cleanly`, `test_one_symbol_failing_to_fetch_does_not_stop_the_others`, `test_malformed_yahoo_payloads_give_an_empty_frame` (5 cases), `test_csv_timestamps_with_utc_offsets_load` |
| low | ✔ | The CSV replay silently turned non-numeric values (e.g. `1,234,567`) into 0 volume, and replayed another symbol's file under `--symbol`. | Both are logged as warnings. | `test_non_numeric_csv_values_are_reported`, `test_a_csv_that_looks_like_another_symbol_is_flagged` |
| low | ✔ | Yahoo 30m/60m bars were all discarded (the grid was anchored to the hour, not 09:15). | The grid and `bar_floor` are anchored at the 09:15 open. | `test_thirty_minute_bars_are_anchored_at_the_open` |
| low | ✔ | Paper booked a STOP at the stop price when a bar opened beyond the target. | An open beyond the target fills the target at the open. | `test_paper_open_beyond_the_target_books_the_target_at_the_open` |
| low | ✔ / ◐ | README claims the code did not honor: a `last_trade_time` fallback, exit code 2 for every bad config, a "two sessions" base. Also, a malformed `--expect-ip` exited 1 and a negative `--run-seconds` ran forever. | README corrected. Malformed arguments exit 2. | `test_malformed_arguments_exit_2` |

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
| high | ✔ | `verify_hardware_ip` was fail-open: its result was ignored at the call site. | `--expect-ip` aborts on mismatch, and `--live-orders` refuses to start without it. | `test_hardware_ip_mismatch_is_fatal`, `test_real_orders_require_a_verified_static_ip` |
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

### Found by the completeness critic and the reviewers' notes

A final reviewer read the whole file for anything the four areas missed. Its
findings were reproduced by that reviewer only (no separate skeptic pass), and
each is pinned by a test.

| Sev | Source | v1.0 defect | v1.1 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | critic | A history load counted as success if *any* symbol loaded. The first live bar of a symbol whose history failed raised `KeyError` and killed the shared aggregator for every symbol. | Empty loads are logged. Ticks for a symbol without history are ignored, because it has no IPO base, and the other symbols are unaffected. | `test_ticks_for_a_symbol_without_history_are_ignored_not_fatal`, `test_orchestrator_reports_failure_when_nothing_loads` |
| low | critic | The IP check trusted whichever provider answered first, and its fallbacks were dual-stack. One ipify timeout on an IPv6-capable host gave a FATAL mismatch for a correctly whitelisted IPv4 address. | Single-family providers queried concurrently. Any disagreement fails, at least two must confirm, and junk bodies are ignored. | `test_ip_check_survives_one_dead_provider_and_asks_only_single_family_hosts`, `test_ip_check_ignores_junk_and_wrong_family_answers_but_needs_two_confirmations`, `test_any_disagreeing_provider_fails_the_ip_check`, `test_ip_check_rejects_a_malformed_expected_address` |
| medium | skeptic note | No session gating: pre-open or post-close prints became bars. | Only 09:15–15:30 prints make bars. Pre-open auction volume lands in the 09:15 bar. | `test_pre_open_and_post_close_prints_are_not_bars_and_auction_volume_lands_at_the_open` |

### Added

* `CsvReplayAdapter` and 944 real NSE 5-minute SWIGGY bars, with provenance
  in `data/README.md`, so the whole pipeline runs offline.
* A CLI (`--source`, `--listing-date`, risk limits, `--rvol-mode`,
  `--live-feed`, `--live-orders`, `--expect-ip`, …) with meaningful exit codes.
* `KiteOrderGateway` and `start_kite_feed` (KiteTicker in full mode).
* Tests (95 cases), `uv.lock`, and `README.md`.
