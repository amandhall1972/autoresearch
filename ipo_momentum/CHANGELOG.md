# Changelog

## v1.19 (2026-09-28)

A screener, `screener.py`, on top of the engine, added after the review loop
was stopped at v1.18 (round 19 was cancelled). No engine logic changed in this
release, only the banner's version. The screener reads several listings'
history through the engine's adapters and `ProductionOrchestrator` (the same
anchoring rules: a history that does not reach the listing is `NO_DATA` unless
`--allow-partial-history`), judges each symbol's last closed bar with the same
`AlphaEngine`, and prints one ranked row per symbol: `BREAKOUT`, `HOLDING`,
`SETUP`, `ABOVE`, `FAILED`, `BELOW`, `BASE` or `NO_DATA`, with the last bar's
close, the base high, the gap to it, AVWAP, RVOL, the sessions and signals since
the listing and, for a `BREAKOUT`, the engine's stop and target. Symbols come
from the command line (`SYMBOL=YYYY-MM-DD`) or a universe file; `--json` and
`--csv-out` write the rows. It sends no orders and opens no feed. See the
README, "Screening several IPOs".

Tests: 601 (was 563). `tests/test_screener.py` covers every status and its
boundaries (the near-pct edge, a close at the base high, a re-cross after a
dip, a window counted in sessions, no volume baseline), the ranking, the
universe file, the CSV directory and as-of rules, the root cause kept for an
unread symbol, the table, JSON and CSV reports, the CLI's exit codes, the
bundled data's reading (the failed 2026-09-23 09:15 breakout of the README's
Validation section), the Yahoo history limit, Ctrl-C, that no reading looks
past its bar, and that the screener's source names no order or feed code. The
suite passes on the same seven configurations as v1.18.

The screener then went through one adversarial review round of its own (round
19, on a frozen copy of commit `0f400dd`), with three areas: the readings, the
CLI and data sources, and tests and docs. Every finder and skeptic completed:
20 findings and 3 more from the skeptics, none refuted (18 confirmed, 1
partly: R19-CLI-DATA-6, whose usage error already named the stray argument);
2 high, 6 medium, 15 low. Where the fix is not the finder's:
- R19-READINGS-1: the finder read Kite history up to the host's now; the
  skeptic showed a host clock ahead of the exchange would then count the
  running candle as closed, and asked for the engine's own allowance. v1.19
  reads Kite history up to 30 s before the host clock (`KITE_CLOCK_ALLOWANCE`,
  the engine's `max_stamp_ahead`): the row is the last closed bar once it is
  30 s old, and a host clock more than 30 s ahead is the same fault the engine
  documents. The engine's own Kite start-up stops a whole bar earlier because
  its feed back-fills the rest, which the screener cannot.
- R19-READINGS-4 / R19-CLI-DATA-2 / R19-TESTS-DOCS-3: the finder offered to
  widen a dateless symbol's window to `--max-lookback-days` for CSV; that
  would make the screener disagree with `engine.py --source csv` on the same
  file, so the 20-day demo window stays and is documented.
- R19-CLI-DATA-S1: a table that cannot be printed (a full disk) is logged,
  the reports are still written, and the exit code is 1; what stdout still
  holds is sent nowhere so the interpreter's exit flush stays quiet.

Every v1.19 regression test fails on the round's snapshot and passes here, on
Python 3.11 and 3.10, except these pins, which pass on both:
`test_the_recent_window_counts_sessions_not_calendar_days`,
`test_cli_applies_every_reading_parameter_and_echoes_it`,
`test_cli_applies_recent_sessions_and_max_lookback_days`,
`test_a_symbol_without_a_listing_date_is_read_from_at_most_20_days_back`,
`test_rank_orders_by_gap_within_a_status_not_by_symbol`,
`test_a_close_exactly_at_the_base_high_after_a_recent_signal_is_failed`,
`test_near_pct_zero_still_admits_a_close_at_the_base_high`,
`test_last_signal_is_the_last_of_several`,
`test_yahoo_history_limit_clamps_the_start_and_excludes_an_older_listing` and
`test_with_kite_a_bar_closed_less_than_30_s_ago_is_not_counted_yet` (a control:
the snapshot's whole-bar lag showed the same earlier bar). Tests: 634 (33
added in the round). The suite passes on the seven configurations.

### Screener: the readings

| Sev | Verdict | Defect at 0f400dd | v1.19 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | With `--source kite` history stopped a whole bar before the host clock, as the engine's does, but the screener has no feed to back-fill the rest: the row was the bar *before* the last closed one for the whole five minutes, so a breakout on the last closed bar showed one bar late. | Kite history is read up to 30 s before the host clock (the engine's `max_stamp_ahead`); the row is the last closed bar once it is 30 s old. | `test_with_kite_the_row_is_the_last_closed_bar_once_it_is_30_s_old`, `test_with_kite_a_bar_closed_less_than_30_s_ago_is_not_counted_yet`, `test_yahoo_history_is_read_up_to_now_and_kite_history_30_s_before` |
| medium | ✔ | The CSV as-of time was a bar after the newest bar in any file, unconditionally: a file written during the session that ends with the running bar had that bar judged as closed, a possible fake `BREAKOUT`. | The CSV as-of time never passes the start of the bar now forming on the host clock. | `test_csv_as_of_never_passes_the_start_of_the_bar_now_forming` |
| medium | ✔ | A close exactly `--near-pct` below a round base high (97 against 100) read `BELOW`: the ratio lands at −3.0000000000000027 in floating point. | The comparison allows 1e-9 of a percent, far below any tick's share of a price. | `test_a_close_exactly_near_pct_below_a_round_base_high_is_a_setup` |
| medium | ✔ | A symbol without a listing date was fetched from at most 20 days back (the orchestrator's demo window), which the README, the CLI table and `--help` never said; a dateless CSV file more than 20 days older than the newest read `NO_DATA` with a reason that did not say why. Found by all three finders. | Docs: the paragraph, the flag row, `--help`; the code stays, so the screener agrees with `engine.py --source csv`. | `test_help_and_readme_state_the_20_day_window_of_a_symbol_without_a_date`, `test_a_symbol_without_a_listing_date_is_read_from_at_most_20_days_back` |
| low | ✔ | A close at the base high was noted as "-0.00% below the base". | The notes print the distance's magnitude. | `test_a_close_at_the_base_high_is_not_a_negative_distance_below_it` |
| low | ✔ | `BASE` was described as waiting for "the 20-bar volume baseline after" the base; evaluation starts after max(base, 20) bars, as in the engine. | Docs. | `test_evaluation_starts_after_max_of_base_and_volume_baseline_not_their_sum` |
| low | ✔ | The `ABOVE` note blamed thin volume when the AVWAP filter refused a heavy-volume cross. | The note names both. | `test_the_above_note_does_not_blame_thin_volume_alone_for_an_avwap_refusal` |
| low | ✔ | `BREAKOUT` was decided by the last fired timestamp equalling the last bar's, not by `evaluate()`'s own positional test: a frame with a duplicated last stamp (unreachable through the adapters) disagreed with the engine. | `evaluate()`'s test. | `test_breakout_is_evaluates_own_test_even_on_a_duplicated_last_timestamp` |
| low | ✔ | (Skeptic) The Kite example said "any age", but a listing older than the 180-day default lookback reads `NO_DATA`. | Docs. | (docs) |
| low | ✔ | (Skeptic) A listing date typed after the as-of time read `NO_DATA` with "No historical bars acquired", blaming the source. | The note names the date and the as-of time. | `test_a_listing_date_after_the_as_of_time_is_named_as_the_reason` |

### Screener: the CLI and data sources

| Sev | Verdict | Defect at 0f400dd | v1.19 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | A universe file saved by a spreadsheet as "CSV UTF-8" carries a byte-order mark: the header was not recognised (and rejected as a bad date), or the first symbol silently became "﻿SWIGGY" and `NO_DATA`; a file in another encoding was a traceback. Found by two finders. | The file is read as `utf-8-sig`; a decoding error is a configuration error naming the file. | `test_a_universe_saved_by_a_spreadsheet_with_a_bom_and_crlf_is_read` |
| high | ✔ | The 20-day demo window (see the readings table). | Docs. | (above) |
| medium | ✔ | The table went to a stdout with the console's strict encoding: a note carrying a path with a character outside it (a `--csv-dir` under a non-ASCII user name on a Windows code page) was a traceback, exit 1, and the reports were never written. | stdout is reconfigured with `errors="replace"`, as stderr already was. | `test_a_narrow_stdout_encoding_cannot_lose_the_table_or_the_reports` |
| low | ✔ | `--json` and `--csv-out` to the same path: the CSV silently overwrote the JSON. | A configuration error, before anything runs. | `test_json_and_csv_out_must_be_different_files` |
| low | ✔ | `--source kite` without the `kiteconnect` SDK was an `ImportError` traceback, not the documented exit 2. | Exit 2 with the SDK's own message. | `test_kite_source_without_the_sdk_is_a_configuration_error` |
| low | ◐ | Symbols on both sides of an option were "unrecognized arguments" (partly: the usage error named the argument). | `parse_intermixed_args`. | `test_symbols_may_surround_an_option` |
| low | ✔ | (Skeptic) A stdout that cannot be written (a full disk) was a traceback and the reports were not written. | Logged; the reports are written; exit 1; stdout's remainder is discarded quietly. | `test_a_table_that_cannot_be_printed_still_writes_the_reports` |

### Screener: tests and docs

| Sev | Verdict | Defect at 0f400dd | v1.19 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The Yahoo and Kite quick-start examples read nothing as written: both listings shown were older than Yahoo's 60 days, and "any age" needed a flag the Kite line omitted. | Docs: placeholder listings inside the window, the Kite line names `--max-lookback-days`. | (docs) |
| medium | ✔ | The universe file's BOM (see the CLI table). | (above) | (above) |
| medium → low | ✔ | The "sessions" window was not pinned: a window counted in calendar days passed every test. | A weekend-gap pin. | `test_the_recent_window_counts_sessions_not_calendar_days` |
| medium → low | ✔ | No test ran a non-default `--recent-sessions`, `--near-pct`, `--base-sessions`, `--max-lookback-days`, `--rvol-threshold`, `--rvol-mode` or `--risk-reward` through `main()`: dropping the wiring passed every test. | Two CLI pins that apply each and read it back from the JSON. | `test_cli_applies_every_reading_parameter_and_echoes_it`, `test_cli_applies_recent_sessions_and_max_lookback_days` |
| medium → low | ✔ | The 20-day window (see the readings table). | (above) | (above) |
| low | ✔ | The rank tests did not pin the gap ordering: every fixture's alphabetical order coincided with its gap order. | A pin whose symbol order contradicts its gap order. | `test_rank_orders_by_gap_within_a_status_not_by_symbol` |
| low | ✔ | Three documented boundaries were unpinned: a close exactly at the base high after a recent signal (`FAILED`), `--near-pct 0` admitting a close at the base high (`SETUP`), and `Last signal` being the last of several. | Three pins. | `test_a_close_exactly_at_the_base_high_after_a_recent_signal_is_failed`, `test_near_pct_zero_still_admits_a_close_at_the_base_high`, `test_last_signal_is_the_last_of_several` |
| low | ✔ | The per-source as-of rules were documented but untested. | A pin for both sources (updated for the Kite allowance). | `test_yahoo_history_is_read_up_to_now_and_kite_history_30_s_before` |
| low | ✔ | The Yahoo test replaced the method that holds the 60-day clamp, so it never exercised the limit, and its comment described the opposite of what it asserted. | A test below the clamp (`_read_url`), the comment corrected. | `test_yahoo_history_limit_clamps_the_start_and_excludes_an_older_listing` |
| low | ✔ | The `BASE` wording (see the readings table). | (above) | (above) |
| low | ✔ | The Ctrl-C test lacked the suite's Windows skip. | Skipped on Windows, and the README's skip list says so. | (harness) |
| low | ✔ | "Further columns are ignored" failed when one held `=`: the line was split on `=` before `,`. | Comma first, then `=` inside the first column; every line accepted before parses the same. | `test_a_further_column_may_contain_an_equals_sign` |
| low | ✔ | (Skeptic) The README said `--csv-out` carried the parameters and the as-of time; only the JSON does. | Docs. | (docs) |

## v1.18 (2026-09-28)

v1.17 went through an eighteenth adversarial review on a frozen snapshot
(`b24c2ab`), with the same four areas. Every finder and skeptic completed.
There were 11 findings, 10 distinct: the lifecycle and the docs reviewer each
found the stop a container's PID 1 drops before `main()` (R18-LIFECYCLE-2 is
R18-DATA-DOCS-1). None was refuted: 8 confirmed, 2 with part of the claim
overstated (R18-FEED-2, R18-DATA-DOCS-2; R18-DATA-DOCS-4 too, in its docs). 4
are medium and 6 low. One medium, R18-LIFECYCLE-1, is a v1.17 regression; the
other three are older than every round that reviewed them. Where the fix is not
the finder's:
- R18-FEED-2: the finder's rule (an unstamped print received before the bar's
  end joins it) left the two seconds of the bar clock's grace, and the finder's
  fix leaves a forward-stamped update of the *next* bucket, which closes the
  open bar directly through the ordinary boundary rule. That path cannot be
  refused: on a host behind the exchange by more than its latency, every bar's
  genuine first prints are stamped ahead of their receipt, so refusing them
  would discard every bar. It is a Known limitation instead. The join now uses
  the same clamped-lag receipt as the bar clock, so the two agree.
- R18-LIFECYCLE-1: the finder added a `rehook` method with its own bookkeeping;
  v1.18 makes `hook()` idempotent for a callback already on a route and calls
  it again in the same synchronous step that starts the run, so no stop can
  fall between the two.
- R18-LIFECYCLE-2 / R18-DATA-DOCS-1: two fixes for one hole. The docs
  reviewer's installs an `os._exit(128 + N)` handler only as PID 1; the
  lifecycle reviewer's records the signal from the script's first line and
  lets `main()` take it as a stop while starting, which logs the stop and
  exits through the normal path with `run()` handing back the default. v1.18
  ships the recorder. The interpreter's own start-up before that line cannot
  be covered by either, so the README recommends an init.
- R18-ORDERS-1: the finder put a directly stalled TLS handshake on the
  never-sent path (one 15 s window). The skeptic classifies it as a refused
  tunnel, like the same stall behind a proxy in v1.17: the entry is released,
  and the GTT loop keeps every attempt with book polls between them and no
  duplicate watch.
- R18-DATA-DOCS-2: the finder's fix to the forked-worker test is taken; its
  claim that the test no longer exercised its purpose was overstated (it still
  caught a child whose engine never hooked), so it is marked partly confirmed.
- R18-DATA-DOCS-4: docs only, without the finder's "bounded by its client
  timeout" (the timeouts are per socket operation) and without repeating that
  repeats are ignored, which the README already said.

Every v1.18 regression test fails on the v1.17 snapshot and passes here, on
Python 3.11 and 3.10 (the same results on both). The exceptions are these
controls and pins:
- `test_a_retried_hole_does_not_make_the_live_bar_before_an_unstamped_prints_bar_look_double_counted`:
  a retry must not make a live bar history kept look double-counted (a
  missed signal).
- `test_an_entry_whose_tls_handshake_timed_out_releases_the_symbol[True]`: a
  reply lost after the request was sent stays ambiguous.
- `test_under_nohup_a_hangup_before_main_is_still_ignored`: the recorder is not
  installed over `nohup`'s `SIG_IGN`.
- `test_a_forked_worker_takes_its_own_stop_signals`, whose wait now needs a
  running engine: it takes the orderly path again on both versions.

Every v1.18 rule was also mutated on a copy, and each mutant failed its pins:
- a failed back-fill's hole never remembered, as in v1.17 (1 test), never
  cleared (1), its old bars played again (1), and the ambiguity check applied
  to a retried live bar (1, the control);
- the bar clock trusting a negative lag, as in v1.17 (2), an unstamped print's
  join trusting it (2), and the join held only until the bar's end, as the
  finder had it (1);
- the Kite start-up sync at the host's now, as in v1.17 (1);
- a directly stalled handshake still counting as possibly sent, as in v1.17
  (4);
- no re-hook as the run starts, as in v1.17 (6), and `hook()` appending a
  callback twice (6 older tests, over the whole end-to-end file);
- no early-stop check in `main()`, as in v1.17 (2), no yield after it (1), the
  recorder installed over `SIG_IGN` too (1, the `nohup` control), and `run()`
  handing the recorder back instead of the default (1).

Existing tests changed:
- `test_a_host_clock_behind_the_exchange_is_corrected_not_ignored`: on a host
  19.7 s behind, the bar now closes at its end plus the grace on the host
  clock, not 19.7 s earlier. Signal ages are still corrected.
- `test_a_forked_worker_takes_its_own_stop_signals`: the worker waits for a
  running engine, not a hooked one, so it exercises the orderly path again
  (the v1.17 helper change had missed this wait).
- The PID 1 tests fall back to `unshare --user --map-root-user --pid` where
  plain `unshare --pid` is refused, so they run unprivileged where user
  namespaces are enabled.
- The tests that land a stop while `python engine.py` loads its imports gate
  `asyncio` (its second import, after the recorder) rather than `pandas`, and
  the TLS-handshake test builds its throwaway certificate with literal dates:
  the suite's clock-shifted runs preload `pandas` and replace
  `datetime.datetime`, which had failed four tests only there.

### Live feed

| Sev | Verdict | v1.17 defect | v1.18 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | A failed back-fill's hole was never retried. The bar after it was not evaluated, but the next one was, on a history that lacked the hole's bars: a surge during a websocket outage (or a discarded spill pair) was missing from the 20-bar RVOL baseline, and a breakout the exchange's bars do not show could fire. v1.3's "a failed back-fill read as nothing traded", one bar later. | The hole stays pending: every later bar retries it (from its start) before it is evaluated, and is not evaluated while the fetch fails. A retry adds only missing bars, plays only the new hole's bars to the paper OCO, and judges an unstamped print's bar only on a bar before it that the retry added. | `test_a_failed_backfills_hole_is_retried_and_no_later_bar_is_evaluated_across_it`, and a control |
| medium | ◐ | A forward-stamped update (up to 30 s ahead, which the 30 s rule accepts) that was the lag window's only sample read as a host running behind. The bar clock closed the open bar up to 30 s early, and an unstamped print received in its last seconds closed it too: the tail was dropped as late (or filed in the next bucket) and credited to the next bar, which could fake a breakout on a synced host. (Overstated: the proposed fix left a forward stamp of the *next* bucket, which closes the bar directly and cannot be refused without discarding every bar on a host slightly behind: now a Known limitation.) | A negative lag no longer closes a bar early: the bar clock closes on the host clock (a positive lag still delays it), and an unstamped print received before the bar's end plus the grace joins it (a spill). On a host really behind, a bar no later print closes closes up to the offset late; signal ages stay corrected. | `test_a_lone_forward_stamped_update_does_not_close_a_bar_before_its_end` (the bar clock, an unstamped print before the end and within the grace) |
| low | ✔ | The Kite start-up sync used the host's now. On a host running ahead, a start within its lead before a bucket's end kept the exchange's running candle as complete, and the feed never replaced it (its own bar was partial, the next bar contiguous): a short bar in every later RVOL baseline. | The Kite start-up sync stops a bar before the host's now; the feed's first kept bar back-fills the rest with the request it makes anyway. | `test_the_kite_start_up_history_stops_a_bar_before_the_host_clock` |

### Lifecycle

| Sev | Verdict | v1.17 defect | v1.18 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | v1.17 hooked the stop signals in `main()`'s first step, which runs at the host's next yield, and never again. A host whose `SIG_IGN` guard (the README's own pattern around a fork or a subprocess) spanned that step, or that set its own handler after it but before the run began, left the engine with no route for its whole run, and nothing was logged. Alone, SIGTERM then killed it mid-entry; beside a later engine only that one took the stop; with a host `loop.stop` callback the entry was cut short. A v1.17 regression. | `hook()` is idempotent for a callback already on a route, and `main()` hooks again in the synchronous step that starts the run: a signal still owned is a no-op, one the host took is re-taken (what the host set is kept for the hand-back), one still ignored joins with a reclaim, one lifted since is taken fresh; `nohup`'s is left alone. | `test_an_engine_takes_the_stop_signals_again_as_its_run_starts` (a guard, a guard beside a later engine, a handler set while starting; asyncio and uvloop) |
| low | ✔ | As a container's PID 1, a stop during the interpreter's start-up and `engine.py`'s imports (about 0.4 s, longer on a cold start) was still dropped by the kernel, since SIGTERM and SIGHUP were at their default until `main()` hooked. The engine then started and traded until the supervisor's SIGKILL, the consequence v1.17 listed as fixed. Found by two reviewers (R18-DATA-DOCS-1 rated it medium). | Run as a script, `engine.py` records SIGTERM and SIGHUP from its first line where they are at their default (`nohup`'s `SIG_IGN` is kept), and `main()` takes a recorded stop as a stop while starting before any start-up work; `run()` hands back the default in the recorder's place. The README recommends an init for the interpreter's own start. | `test_as_a_containers_pid_1_a_stop_before_main_is_not_lost`, `test_as_a_containers_pid_1_a_stop_while_the_engine_loads_its_imports_is_not_lost`, and the `nohup` control |

### Real-money safety: orders

| Sev | Verdict | v1.17 defect | v1.18 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | v1.17 counted a Kite TLS handshake that stalled behind a proxy as a refused tunnel, but the same stall on a direct connection (no proxy, or Kite in `NO_PROXY`) raised its timeout from urllib3's pre-request connect step with the request's path, like a lost reply. An unsent entry was looked up and blocked the symbol for the session; a filled position gave up after one GTT attempt with GTT STATE UNKNOWN, although no request byte was sent. | A read timeout raised from urllib3's connect step (`_validate_conn`, before the request is written) counts as a refused tunnel, directly or through a proxy; the docs say no request byte was sent. | `test_a_tls_handshake_that_timed_out_counts_as_a_refused_tunnel_with_or_without_a_proxy`, `test_an_entry_whose_tls_handshake_timed_out_releases_the_symbol`, `test_stalled_handshakes_are_ridden_out_like_refused_tunnels`, with controls |

### Tests and docs

| Sev | Verdict | v1.17 defect | v1.18 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The README presented the PID 1 case as solved, though a stop before `main()` was still dropped, and never recommended an init. | See R18-LIFECYCLE-2 above; the README's start-up paragraph, Supervisors section and a new Known limitations entry say what is covered, what is not, and to run under an init. | (the PID 1 import-window test above) |
| low | ◐ | `test_a_forked_worker_takes_its_own_stop_signals` kept its old "hooked" wait, so since v1.17 it always stopped the worker while it started and no test took a forked worker's running engine through the orderly path. The v1.17 no-abandon mutant count (5) was 6 over the whole file. (Overstated: the test still caught a child whose engine never hooked.) | The worker waits for a running engine; the v1.17 count is corrected (marked). | `test_a_forked_worker_takes_its_own_stop_signals` |
| low | ✔ | The v1.17 CHANGELOG's mutation evidence for the newest-stamp pin came from a `min()` mutant that keeps the connection's earliest stamp and passes the pin (it failed only genuine-proof controls); a test row it said was replaced was kept beside the new one; the claim that the feed-time pin was caught by no other test was wrong. | Corrected in the v1.17 section, each marked. | (docs) |
| low | ◐ | The README said a stop while starting ends the process "at once"; it exits once the start-up request in flight returns, since a read in a worker thread cannot be interrupted. (Overstated: the finder's own wording, and its claim about a second Ctrl-C on 3.11, were inaccurate.) | Docs: the exit table and the start-up paragraph say so. | (docs) |
| low | ✔ | The PID 1 test needed `unshare --pid`, which needs `CAP_SYS_ADMIN`, so it was skipped for every non-root developer and CI runner, and the README's skip list did not say so. | The tests fall back to a user namespace, and the README's skip list names the case that remains. | (the PID 1 tests, run unprivileged) |

## v1.17 (2026-09-28)

v1.16 went through a seventeenth adversarial review on a frozen snapshot
(`a3cba49`), with the same four areas. Every finder and skeptic completed.
There were 8 findings, with no duplicates, and none refuted: 6 confirmed, 2
with part of the claim overstated (R17-FEED-1, R17-DATA-DOCS-3). 1 is high, 2
medium and 5 low. The high one, in the lifecycle area that v1.16 did not
change, is older than every round that reviewed it: a stop that arrived while
an engine was starting was lost. Where the fix is not the finder's:
- R17-LIFECYCLE-1: the finder's version hooked the signals early and kept a
  phase table; the skeptic's does the same with one callback whose handler
  changes at the start of the run, in the same synchronous step, so no stop can
  fall between the two phases. A stop while starting cancels only main()'s own
  task (on 3.11+ the cancel is taken back, so an inline host's task is left
  alone), and a host's own cancel during start-up still propagates.
- R17-LIFECYCLE-2: Python allows no way for a signal to reach a worker thread,
  so the fix is a warning at start and a documented way to forward a stop
  (cancel main()'s task from the main thread, which settles in order).
- R17-ORDERS-1: the skeptic also counts the same timeout from an https:// proxy
  whose TLS handshake never finished (urllib3 wraps that one in ProxyError).
- R17-FEED-1: the finder's code fix (try the earliest stamp of the bucket that
  passes both checks) would also prove a forward-stamped twin that the host
  cannot tell from the genuine case: a possible fake signal to recover missed
  ones. v1.17 keeps v1.16's rule (only the newest stamp is tried), states it in
  the README with its cost, and pins it.

Every v1.17 regression test fails on the v1.16 snapshot and passes here, on
Python 3.11 and 3.10. The exceptions are these controls and pins:
- `test_an_entry_whose_connect_the_proxy_never_answered_releases_the_symbol[True]`
  and `test_stalled_connects_alone_leave_a_position_known_to_have_no_gtt[True]`:
  a reply lost after the request was sent stays ambiguous.
- `test_an_accepted_cancel_whose_state_stays_open_names_nothing_more`: an
  accepted cancel and a read that later succeeded add nothing to the message.
- `test_a_later_update_of_the_bucket_voids_an_earlier_stamps_proof`: a pin of
  v1.16's newest-stamp rule. It fails on the finder's earliest-stamp change.
- The new synced-host row of
  `test_a_stamp_later_than_an_unstamped_prints_feed_time_does_not_prove_its_bucket`:
  a pin of v1.16's feed-time check that no other test caught alone.
  (Corrected in v1.18: reverting that check alone already failed the two
  host-ahead rows and, on a synced host, the changed `filed_in_that_bucket`
  test, though only through the bar's ambiguous flag; this row pins the
  evaluated bar there.)

Every v1.17 rule was also mutated on a copy, and each mutant failed its pins:
- a stop while starting not abandoning the start (5 tests; corrected in
  v1.18: 6 over the whole file, as `test_a_forked_worker_takes_its_own_stop_signals`
  stopped its worker while it started, see below), the engine's own cancel
  left on the host's task (1), and the start-up handler left in place after
  main() (1);
- no warning when no stop signal can reach the engine (1);
- a stalled CONNECT still counting as possibly sent, as in v1.16 (4), any read
  timeout counting as a refused tunnel (3), and a TLS proxy's stalled
  handshake not counted (1);
- the cancel error not named (1), the state-read error not named (1), and a
  successful read not clearing it (1);
- the earliest proving stamp kept instead of the newest (5; corrected in
  v1.18: that mutant, `min()`, kept the connection's earliest stamp, which
  proves nothing, so it failed only the genuine-proof controls and passed the
  pin's `after_feed_time` row; the finder's change, the earliest stamp of the
  newest bucket, fails that row alone);
- the stamp checked only against the print's receipt, as in v1.15 (5, the new
  synced-host row among them).

Existing tests changed:
- The end-to-end sync helpers `hooked()`, `until_hooked()` and
  `stop_once_hooked` wait for running engines, not hooked ones: an engine now
  hooks before it starts. (Corrected in v1.18: the forked worker of
  `test_a_forked_worker_takes_its_own_stop_signals` still waited for a hooked
  engine, so its stop always came while it started and no test stopped a
  forked worker's running engine; v1.18 makes it wait for a running one.)
- `test_a_stamp_later_than_an_unstamped_prints_feed_time_does_not_prove_its_bucket`:
  its synced-host row (a stamp 0.45 s ahead, which the own-receipt check
  already rejects) is replaced by one only the feed-time check rejects (a stamp
  ahead by less than its update's latency, and a print slower than it).
  (Corrected in v1.18: the 0.45 s row is kept, and the new row added beside it,
  as the v1.16 section says.)

### Lifecycle

| Sev | Verdict | v1.16 defect | v1.17 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | main() took the stop signals only after its start-up (the IP check, the instrument dump, the history sync). A stop in those seconds was lost in two realistic set-ups. As a container's PID 1, the kernel drops a signal left at its default, so the engine started, traded, and the supervisor's SIGKILL came up to 420 s later, possibly mid-entry. In a host running one engine per IPO, the running engines alone took the stop, and the one still starting then ran on. It is older than every review round. | The stop signals are taken before the first await, for the whole call. A stop while starting abandons the start (nothing can be in flight) and returns 128+N; from the start of the run the same route takes the orderly shutdown. | `test_as_a_containers_pid_1_a_stop_while_starting_is_not_lost`, `test_an_engine_still_starting_beside_running_ones_takes_their_stop` (asyncio and uvloop), `test_a_stop_while_the_engine_starts_is_taken_not_left_to_the_default`, `test_a_stop_while_starting_is_the_engines_own_and_leaves_the_hosts_task_alone` |
| medium | ✔ | An engine off the main thread (run() on a worker thread, which the suite supports, or main() in a worker thread's loop) took no stop signal, since Python runs handlers in the main thread only, and said nothing. SIGTERM then killed it mid-entry, with no settlement and no halt report. | A warning at start names the signals that cannot reach the engine and how to forward a stop (cancel main()'s task from the main thread). Known limitations says so. | `test_an_engine_that_no_stop_signal_can_reach_says_so` |

### Real-money safety: orders

| Sev | Verdict | v1.16 defect | v1.17 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | A proxy that accepted the CONNECT but never answered it (squid while its path to Kite is black-holed) raised ReadTimeout from urllib3's tunnel set-up, before any request byte was written. The gateway treated it as possibly sent: an unsent entry blocked the symbol for the session, and a filled position gave up after one GTT attempt with GTT STATE UNKNOWN, although no GTT could exist. | A timeout of the tunnel set-up (a ReadTimeoutError naming the proxy's URL, also inside ProxyError for a TLS proxy) counts as a refused tunnel. | `test_a_tunnel_set_up_that_timed_out_counts_as_a_refused_tunnel`, `test_an_entry_whose_connect_the_proxy_never_answered_releases_the_symbol`, `test_a_proxy_outage_that_stalls_the_connect_is_ridden_out_with_every_attempt`, `test_stalled_connects_alone_leave_a_position_known_to_have_no_gtt`, with controls |
| low | ✔ | An expired session during the fill or cancel wait was never named: every state read and cancel failed, and the ATTENTION line said only that the order was "not terminal … after cancelling". | The ATTENTION line names the last cancel error when no cancel was accepted, and the last state read's error when it failed. No waiting or cancelling changes. | `test_an_unconfirmed_entry_names_the_failed_cancels_and_state_reads`, and a control |

### Live feed

| Sev | Verdict | v1.16 defect | v1.17 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ◐ | Only the newest stamp of a symbol's updates is tried as proof. Under v1.16's feed-time check, a later update of the same bucket, received shortly before the print, voids an earlier stamp that would have proved it: on a busy name a synced host lost 37-74% of genuine proofs (v1.15 proved them all), so more real breakouts after a traded bucket were skipped. The README said a genuine stamp still proves the bucket. (Overstated: trying the earlier stamp too would also prove a forward-stamped twin the host cannot tell apart, a possible fake signal.) | Docs: the README states that the newest stamp decides, and the cost on a busy name. | `test_a_later_update_of_the_bucket_voids_an_earlier_stamps_proof` (a pin, with controls) |

### Tests and docs

| Sev | Verdict | v1.16 defect | v1.17 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | The synced-host row listed as a pin of the feed-time check did not test it: the own-receipt check already rejected that stamp, so it failed only with both checks reverted. The feed-time mutant's count also left out the changed older test. | A row that only the feed-time check rejects; the v1.16 count is corrected (marked). | the new row of `test_a_stamp_later_than_an_unstamped_prints_feed_time_does_not_prove_its_bucket` |
| low | ✔ | The v1.16 row for the last refused tunnel said the delay dated from v1.14. It dates from v1.11, and from v1.12 with the book behind the refusing proxy. | Corrected (marked). | (docs) |
| low | ◐ | The v1.16 intro said the own-receipt check closed the hole on a synced host, and the feed-time check whenever the window held an honest sample. Neither holds for a stamp that leads by less than its update's latency, or a print slower than every sample. (Overstated: the same paragraph, like the README, then stated the combined residual correctly.) | Corrected (marked). | (docs) |

## v1.16 (2026-09-28)

v1.15 went through a sixteenth adversarial review on a frozen snapshot
(`b9de4eb`), with the same four areas. Every finder and skeptic completed; the
data/docs skeptic was cut off by a container restart and re-ran from the
review's journal. The lifecycle finder found nothing. There were 6 findings,
with no duplicates, and none refuted: 5 confirmed, 1 with part of the claim
refuted (R16-DATA-DOCS-3). 2 are medium and 4 low. Where the fix is not the
finder's:
- R16-FEED-1 and R16-DATA-DOCS-1 are one hole, seen from two hosts: a
  forward-stamped update could still prove an unstamped print's bucket. The
  feed fix (a stamp proves only if it is no later than its own receipt) closes
  leads beyond the update's own latency plus the host's lead: the feed finder's
  3 s and 29 s on a synced host, but not a sub-second lead there, nor one of up
  to the host's lead on a host running ahead, whose receipts read late. The
  data/docs fix (a stamp proves only if it is no later than the print's feed
  time) closes the rest for a print no slower than the window's slowest honest
  sample, which on a host running ahead also holds its lead, but on its own
  misses the feed finder's repro, in which the print's latency just exceeded
  the window's. (Corrected in v1.17: this said the feed fix closed the hole on
  a synced host, and the data/docs fix whenever the window held an honest
  sample. Alone, the feed fix lets through a stamp that leads by less than its
  update's latency, and neither fix covers a print slower than every sample.)
  v1.16 applies both. A forward stamp now proves a bucket only if its
  lead is within the update's own latency plus the host's lead and, as well,
  the print's latency plus the host's lead exceeds the minute's largest lag
  sample: every sample forward-stamped on a host running ahead, or a print
  slower than every sample. No sample can tell such stamps from honest
  latency; this is documented as a limitation.
- R16-ORDERS-1: the regression test's timing margin is half a second, not the
  finder's 0.2 s, so it cannot flake under load. The exact pin is the route
  assertion: one book read after the last request.
- R16-DATA-DOCS-3: of its four claims, the one about the saved-dispatcher pin
  was refuted (the CHANGELOG counted 2 failures for that mutation, the uvloop
  cases). The other three are corrected in the v1.15 section below, each marked
  "corrected in v1.16".

Every v1.16 regression test fails on the v1.15 snapshot and passes here, on
Python 3.11 and 3.10. The exceptions are these controls:
- `test_the_read_after_the_last_refused_tunnel_still_names_an_expired_session`:
  the one read kept after the last refused tunnel still names an expired
  session.
- `test_a_forward_stamp_no_later_than_an_unstamped_prints_receipt_does_not_prove_its_bucket[-0.1]`
  and `test_on_a_host_running_ahead_a_stamp_of_its_own_bucket_received_first_still_proves_an_unstamped_prints_bar`:
  a genuine stamp still proves the bucket, on a synced host and on one running
  ahead. (Corrected in v1.17: only if it is the newest stamp no later than its
  own receipt received before the print. A later update of the bucket voids
  it, so on a busy name a synced host loses many genuine proofs, often more
  than a host slightly behind; v1.15 proved them all. R17-FEED-1.)
- `test_a_partial_bar_is_played_to_the_paper_oco_when_the_brokers_bars_do_not_replace_it[replaced]`:
  the broker's bars are played once each.
- `test_a_bar_kept_after_a_discarded_one_clears_its_mark_so_a_later_weekend_session_is_back_filled`
  (spill and partial): a pin for a v1.15 rule no test covered.

Every v1.16 rule was also mutated on a copy, and each mutant failed its pins:
- the last refused tunnel still polling its window, as in v1.15 (2 tests), and
  no read after it (3);
- a stamp ahead of its own receipt still proving, as in v1.15 (2);
- the stamp checked only against the print's receipt, as in v1.15 (2;
  corrected in v1.17: 3, with the older test changed below; the synced-host
  row failed only with both checks reverted, so v1.17 adds one that only this
  check catches, which makes 4);
- partial bars not held, as in v1.15 (2);
- a kept bar not clearing the discarded-bar mark (2).

One mutant passes every test, as it must: clamping the feed-time bound at the
print's receipt cannot matter while a proving stamp must be no later than its
own receipt, which comes before the print's. The clamp stays as defence in
depth.

Existing tests changed:
- `test_an_unstamped_trade_received_after_a_stamp_of_the_next_bucket_is_filed_in_that_bucket`:
  its quote is stamped after the print's feed time, so the print's bar is now
  ambiguous. It is still filed in 10:05 and evaluated, because the back-filled
  10:00 did not trade.

### Real-money safety: orders

| Sev | Verdict | v1.15 defect | v1.16 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | After the third refused tunnel, no attempt was left and no GTT could exist, yet the loop still polled the book for a whole window (two while the book sat behind the refusing proxy). The filled position had no exits the whole time, but the `POSITION OPEN WITHOUT EXITS` alert, the halt report and a shutdown in progress waited 15 s at the defaults, up to about 37 s. It dates from v1.11 (v1.12 with the book behind the refusing proxy); until v1.14 that poll could even adopt a GTT that could not be this entry's. (Corrected in v1.17: this said it dated from v1.14.) | After the last refused tunnel, one book read (which still names an expired session) replaces the poll, and the alert follows at once. | `test_the_last_refused_tunnel_is_reported_at_once` (book readable or not), and a control |

### Live feed

| Sev | Verdict | v1.15 defect | v1.16 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | v1.15 checked a proving stamp only against the unstamped print's receipt. A quote whose update happened in 10:00, stamped 10:05:00 (up to 30 s ahead, which the 30 s rule accepts), proved bucket 10:05 for any print received at or after 10:05:00, including one that traded at 10:04:59.9. That print's bar was evaluated while the back-fill of 10:00 counted its shares again: a fake breakout on a synced host, with no clock warning. It predates v1.15. | A stamp proves a bucket only if it is no later than its own receipt (a stamp ahead of it may be forward). | `test_a_forward_stamp_no_later_than_an_unstamped_prints_receipt_does_not_prove_its_bucket` (leads of 3 s and 29 s, and a genuine control) |
| low | ✔ | v1.15 played discarded spill-pair bars to the paper OCO when the broker's bars could not replace them, but not a bar discarded as partial (a reconnect, a closed websocket, a stall), whose seen prints are just as real. A stop or target reached only in that bucket was missed in paper trading, and the symbol stayed blocked. It predates v1.15. | Every discarded bar is held, and played once, in order, when the back-fill fails or there is no history source. | `test_a_partial_bar_is_played_to_the_paper_oco_when_the_brokers_bars_do_not_replace_it` (a failed fetch, no history source, and a control) |

### Tests and docs

| Sev | Verdict | v1.15 defect | v1.16 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | On a host running ahead of the exchange, receipts read late by its lead, so a stamp ahead of its trade by less than that lead plus the print's latency still proved an unstamped print's bucket, and the double count and fake signal of R15-DATA-DOCS-1 came back. On a synced host, a stamp ahead by less than the print's latency did the same. The docs said only a host running behind was affected, at the cost of missed signals only. It predates v1.15. | A stamp must also be no later than the print's feed time (its receipt less the largest lag sample, which holds the host's lead). The README states both checks and the case left, as a new Known limitations entry. | `test_a_stamp_later_than_an_unstamped_prints_feed_time_does_not_prove_its_bucket` (a host 3 s ahead with 2 of 3 and 3 of 5 samples forward, and a synced host with one stamp 0.45 s ahead; corrected in v1.17: that stamp is also ahead of its own receipt, so its row failed only with both checks reverted, and v1.17 adds one 0.35 s ahead and received after it, which only this check catches), and the host-ahead control |
| low | ✔ | Nothing pinned that every kept bar clears the discarded-bar mark. With the mark kept, a stale one from a late join refused a later weekend session's discarded bar, and Monday's first bar fired R15-FEED-1's fake crossing; all 510 tests passed. | Pinned. | `test_a_bar_kept_after_a_discarded_one_clears_its_mark_so_a_later_weekend_session_is_back_filled` (spill and partial) |
| low | ◐ | The v1.15 CHANGELOG's test evidence was partly wrong. The proof mutant counted "(5)" called `clock_skew`, a property, and crashed (a faithful revert fails 3). The weekend rule table's rows were listed among the controls that pass on v1.14, though all fail there. The corrected v1.14 row still lacked the "behind by more than the print's latency" condition. (Refuted: the saved-dispatcher pin's text was accurate.) | Corrected in the v1.15 section, each marked. | (docs) |

## v1.15 (2026-09-28)

v1.14 went through a fifteenth adversarial review on a frozen snapshot
(`181b2c7`), with the same four areas. Every finder and skeptic completed.
There were 8 findings, with no duplicates, and none was refuted: 6 confirmed,
2 with part of the claim overstated (R15-FEED-2, R15-LIFECYCLE-1). 2 are medium
and 6 low. Where the fix is not the finder's:
- R15-FEED-1: the finder started the hole at the first weekday after history's
  last bar. That skipped a special Saturday session's own head, which v1.14
  back-filled, so the hole starts at the earlier of that weekday and the bar's
  own session. v1.15 also closes the case both leave open: a weekend special
  session (the exchange holds some, e.g. on a Budget day) whose every bar was
  discarded. The first bar the feed saw but did not keep since history's last
  (partial, a spill bar or the bar after one) now marks its own session as part
  of the next kept bar's hole, on any day. The weekday rule still covers a
  weekday on which the feed saw none of the name's bars.
- R15-FEED-2: the finder cleared the whole hold after playing one window. v1.15
  releases only that window's bars, so a later hole's bars survive back-fills
  that finish out of order. The path without a history source plays them too.
- R15-LIFECYCLE-1: the owner resolution is one helper shared with the at-fork
  reset, so the two rules cannot drift apart again. A control pins the child's
  own `asyncio.run` handler, which must survive.
- R15-DATA-DOCS-1: the finder's docs-only alternative would have documented a
  fake signal. No number of lag samples tells a host running behind from
  forward-stamped packets, so the proof now checks the stamp against the print's
  receipt time itself. A bar's latest possible time is unchanged. The cost is a
  missed signal on a host running behind, never a fake one. (Corrected in
  v1.16: R16-FEED-1 and R16-DATA-DOCS-1 found forward stamps this still let
  prove a bucket.)
- R15-DATA-DOCS-3: the corrected margin also states that the host must run
  behind by more than the print's latency, which makes the condition exact.
- R15-DATA-DOCS-4: the docs name what the harness does. The finder's wording
  ("every engine a test sends a stop signal") was itself inaccurate.

Every v1.15 regression test fails on the v1.14 snapshot and passes here, on
Python 3.11 and 3.10. The six `asyncio.run` handler tests are skipped on 3.10,
which has no such handler. The rule table
`test_a_discarded_bar_marks_its_weekend_session_as_part_of_the_hole` fails on
v1.14 as a whole, since `_hole_before` has no `discarded` argument there.
Without the weekend rule, only its two weekend rows fail. The exceptions are
these controls:
- `test_an_identical_gtt_is_still_reported_when_no_read_after_the_refused_tunnel_succeeded`:
  with no successful read, there is nothing to learn.
- `test_a_session_whose_every_bar_was_discarded_is_back_filled_before_the_next_sessions_first_bar[False]`
  and `test_a_weekend_session_whose_every_bar_was_discarded_is_back_filled_before_mondays_first_bar[untraded]`:
  with no bar lost, Monday's genuine crossing fires. With no bar seen on a
  weekend, there is no weekend fetch.
- The rows of `test_the_hole_before_a_bar_spans_every_session_since_historys_last_bar`
  that earlier versions already got right (consecutive sessions, the previous
  session's tail, the session's own head, a weekend, a special Saturday's
  head). (Corrected in v1.16: the weekend rule table's rows were listed here
  too, but all of them fail on v1.14, as said above.)
- `test_a_discarded_spill_pair_is_played_to_the_paper_oco_when_the_brokers_bars_do_not_replace_it[replaced]`:
  the broker's bars are played once each.
- `test_a_childs_own_asyncio_run_sigint_handler_survives_its_engine`: an
  over-broad mapping must not touch the child's own handler.
- `test_a_child_that_lifted_a_guard_with_the_saved_dispatcher_gets_the_hosts_plain_handler_after_its_engine`:
  a pin for a v1.14 rule that no test covered. It fails on the mutation that
  swaps the two checks.

Every v1.15 rule was also mutated on a copy, and each mutant failed its pins:
- reads after refused tunnels not kept (4 tests);
- reads kept after an ambiguous attempt too (2);
- the hole starting at the bar's own session, as in v1.14 (3), or at the
  next weekday only, as the finder had it (1);
- a partial bar not marking its session (1), no bar marking one (2), the hole
  starting at the discarded bar instead of its session's open (4), and a bar
  before history's last still counting (1);
- held bars not played when the fetch fails (1) or without a history source
  (1), played on success too (1), not held at all (2), and a release dropping
  a later hole's bars (2);
- the proof reading a majority of forward stamps as the host's offset, as in
  v1.14 (3; corrected in v1.16: the mutant first counted here called
  `clock_skew`, a property, and crashed, which also failed two older tests);
- the inherited map ignoring `asyncio.run`'s handler, as in v1.14 (4), or
  mapping every loop-bound handler (2);
- the inherited map's two checks swapped (2).

Existing tests changed: none.

### Real-money safety: orders

| Sev | Verdict | v1.14 defect | v1.15 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | v1.14 fixed refused tunnels alone. After a refused tunnel, the poll reads the book for a whole window, before any request that could book a GTT, and then threw those reads away. A later ambiguous attempt (a 504, a read timeout) polled with the same pre-entry snapshot. With the snapshot unreadable, an earlier run's identical GTT that every read had shown raised GTT STATE UNKNOWN with an attempt unused, even when this entry's own GTT was also booked (a fired one was not marked). With a readable snapshot, a foreign identical GTT booked before the first request was adopted silently, and the duplicate watch then said to delete the entry's own. It predates v1.14. | A book read taken while no request could have reached Kite counts as a snapshot: its ids are never adopted. | `test_an_identical_gtt_read_after_a_refused_tunnel_is_not_taken_for_a_later_504s`, `test_a_foreign_gtt_read_after_a_refused_tunnel_is_not_adopted_after_a_later_504`, `test_a_gtt_booked_by_a_read_timeout_after_a_refused_tunnel_is_adopted` (active and triggered), and a control |

### Live feed

| Sev | Verdict | v1.14 defect | v1.15 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The hole check assumed sessions follow one another: it covered the previous session's tail and the new bar's own session, never a whole session between. If every bar of a weekday was discarded (a spill pair that was the day's only trades, or a zeroed packet at each boundary), that day was never back-filled. The next session's first bar was then evaluated against the day before, and a crossing that happened on the lost day fired again as a first crossing, a real order with `--live-orders`. v1.14's spill discard made it easier to reach; with a reconnect it predates v1.14. | The hole also spans every weekday since history's last bar (a holiday costs one empty fetch) and the session of any bar the feed saw but did not keep, a weekend special session included. | `test_a_session_whose_every_bar_was_discarded_is_back_filled_before_the_next_sessions_first_bar`, `test_a_weekend_session_whose_every_bar_was_discarded_is_back_filled_before_mondays_first_bar` (spill, partial), the rule tables `test_the_hole_before_a_bar_spans_every_session_since_historys_last_bar` and `test_a_discarded_bar_marks_its_weekend_session_as_part_of_the_hole`, with controls |
| low | ◐ | v1.14 discarded a spill bar and the bar after it without playing them to the bar listeners. When the next kept bar's back-fill failed, the paper OCO never saw those buckets, so a stop or target reached only inside them was missed, and the symbol stayed blocked. v1.13 played both. (Overstated: a partial bar whose back-fill fails has never been played, and a run that ends first is the same gap as the forming bar at shutdown.) | The discarded bars are held. If the broker's bars do not replace them (a failed fetch, or no history source), they are played before the live bar, once each. The log no longer says "the broker's bar replaces it". | `test_a_discarded_spill_pair_is_played_to_the_paper_oco_when_the_brokers_bars_do_not_replace_it` (a failed fetch, no history source, and a control that plays the broker's bars once), `test_releasing_one_holes_held_bars_keeps_a_later_holes` |

### Lifecycle

| Sev | Verdict | v1.14 defect | v1.15 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ◐ | v1.14's inherited map read a handler as the parent loop's only if its `__self__` was that loop. `asyncio.run`'s own SIGINT handler (3.11+) is a partial of the Runner, so a child that lifted a guard with it (after a take-back with the handler saved at start) got it back from its own engine, and its first Ctrl-C was swallowed. (Overstated: the README tells a child never to lift a guard with anything that ran through a loop. It predates v1.14.) | The owner is resolved as the at-fork reset does (a Runner stands for its loop), in one shared helper. | `test_a_child_that_lifted_a_guard_with_asyncio_runs_sigint_handler_gets_the_default_after_its_engine` (asyncio and uvloop, `main()` and `run()`), and the control `test_a_childs_own_asyncio_run_sigint_handler_survives_its_engine` |

### Tests and docs

| Sev | Verdict | v1.14 defect | v1.15 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | v1.14's guard covered only a lag window with a single sample. When forward-stamped updates were most of the window (two quotes 15 s ahead on an illiquid name, 2 of 3, 3 of 5), their upper median read as a host running behind, and a stamp of the next bucket proved an unstamped print's bucket: its bar was evaluated with the print's shares counted twice, on a synced host. The README said such a stamp cannot prove a bucket. | The proof checks the stamp against the print's receipt time itself; `_receipt_skew` is gone. On a host running behind, more such bars go unproven (a missed signal, documented). | `test_forward_stamps_that_are_most_of_the_lag_window_do_not_prove_an_unstamped_prints_bucket` (2 of 2, 2 of 3, 3 of 5) |
| low | ✔ | The uvloop half of v1.14's inherited-map rule had no pin. On uvloop the engines' dispatcher is itself bound to the parent's loop, so swapping `_inherited_host`'s two checks passed all 468 tests, and a child whose host had a plain SIGTERM handler was then killed after its own engine. | Pinned. | `test_a_child_that_lifted_a_guard_with_the_saved_dispatcher_gets_the_hosts_plain_handler_after_its_engine` (asyncio and uvloop, `main()` and `run()`) |
| low | ✔ | The host-behind limitation stated its margin backwards ("by more than their stamps' truncation"): with a large truncation it called a print safe that is filed early and evaluated. | Docs: faster than every sample by more than a second minus its truncation, on a host behind by more than the print's latency (the v1.14 row is corrected too). | (docs) |
| low | ✔ | The README and CHANGELOG said the harness gives every engine it starts the default stop signals. The `cli()` engines, two direct launchers and every in-process engine keep SIGHUP ignored under `nohup` (no test depends on it). | Docs: `Child`, `HOST` and `SCRIPT` start their interpreters with SIGHUP and SIGTERM at their defaults, and pytest gives SIGINT back its default (the v1.14 row is corrected too). | (docs) |

## v1.14 (2026-09-28)

v1.13 went through a fourteenth adversarial review on a frozen snapshot
(`c556e10`), with the same four areas. Every finder and skeptic completed.
There were 10 findings, with no duplicates, and all were confirmed; none was
overstated or refuted. 3 are medium and 7 low. Where the fix is not the
finder's:
- R14-FEED-1: the finder would still have kept the long spill bar and the short
  bar after it. v1.14 discards both, as it does a partial bar, so the strict
  back-fill restores the exchange's two bars before the next bar is evaluated.
  That also covers a spill bar that is partial (a reconnect mid-bucket), which
  v1.13 did not record. The bar after a spill bar is recognised by its exact
  time, which fixes R14-FEED-2 as well.
- R14-ORDERS-1: the finder cleared a match after the poll returned. A foreign
  match then ended the poll at its first read, so three refused tunnels used up
  every attempt at once. Instead, the poll after refused tunnels alone waits out
  its whole window and never returns a match.
- R14-DATA-DOCS-1: the proof corrects for the host only when a second lag
  sample backs the offset, which is narrower than the finder's change. Nothing
  changes with two samples or more.
- R14-DATA-DOCS-2: docs only. The samples show no upper bound on how far a host
  runs behind, so no margin can close this, and treating every bar an unstamped
  print opens as a spill bar would skip far more bars. The limitation is
  documented as a fake-signal risk on a host behind the exchange.
- R14-DATA-DOCS-5: the harness resets SIGHUP and SIGTERM in the children it
  starts, not in pytest itself, so a `nohup` run of the operator's own is still
  honoured.

Every v1.14 regression test fails on the v1.13 snapshot and passes here, on
Python 3.11 and 3.10. `test_the_fast_modules_pass_without_the_kite_extra` fails
there too, because it runs the fast modules. The exceptions are these controls:
- `test_a_forward_stamp_alone_in_the_lag_window_does_not_prove_an_unstamped_prints_bucket[True]`:
  with a second sample the proof was already right.
- The asyncio cases of
  `test_a_child_that_lifted_a_guard_after_the_host_moved_to_a_loop_callback_gets_the_default_after_its_engine`
  (v1.13's pin), now also run through `run()`.
- The pins for v1.13 rules that no test covered:
  - `test_an_open_bar_after_a_hole_joined_by_a_print_that_may_be_the_next_buckets_is_not_evaluated`
    (the spill rule on the back-fill path, with a control);
  - `test_a_429_on_place_order_is_looked_up_by_its_tag_and_keeps_the_symbol_blocked`
    (the behaviour the README misstated).

  Each fails on the mutation that removes its rule.
- The harness change. Under `nohup`, the v1.13 harness fails
  `test_kill_and_hangup_settle_the_entry_in_flight_like_ctrl_c[SIGHUP]` and
  `test_release_signals_from_another_thread_refuses_before_changing_anything`,
  and the v1.14 harness passes them.

Every v1.14 rule was also mutated on a copy, and each mutant failed its pins:
- the spill discard removed (6 tests);
- the bar after a spill bar kept (3);
- that bar matched by position rather than by time (1);
- a partial spill bar not recorded (1);
- the lone-sample proof guard removed (1);
- the poll after refused tunnels adopting again (3);
- the uvloop dispatcher mapping in the child removed (2).

Existing tests changed:
- `test_a_child_that_lifted_a_guard_after_the_host_moved_to_a_loop_callback_gets_the_default_after_its_engine`
  runs on asyncio and uvloop, through `main()` and `run()`.
- `Child`, `HOST` and `SCRIPT` start their interpreters with SIGHUP and SIGTERM
  at their defaults, as a normal launch gives them.

### Real-money safety: orders

| Sev | Verdict | v1.13 defect | v1.14 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | A refused tunnel carried nothing to Kite, yet the book poll after it could still adopt a matching GTT. Suppose the pre-arming snapshot was unreadable and an identical earlier GTT was in the book. The loop then raised GTT STATE UNKNOWN and stopped with two attempts unused, leaving the new shares without exits. With a readable snapshot, someone else's identical GTT booked since could be adopted silently as this entry's exit. It predates v1.13. | After refused tunnels alone, the poll waits out its window (so it still rides out the outage) but never returns a match. | `test_after_refused_tunnels_alone_an_identical_earlier_gtt_is_neither_adopted_nor_reported`, `test_after_refused_tunnels_alone_a_foreign_gtt_booked_since_the_snapshot_is_not_adopted`, `test_a_foreign_match_does_not_cut_short_the_poll_after_a_refused_tunnel` |
| low | ✔ | The README said any 4xx on `place_order` releases the symbol at once. A 429 (rate limit) is instead looked up by its tag, which keeps the symbol blocked if the order never shows. That is the safe behaviour, but it was undocumented. | Docs: "any 4xx other than 429". | `test_a_429_on_place_order_is_looked_up_by_its_tag_and_keeps_the_symbol_blocked` |

### Live feed

| Sev | Verdict | v1.13 defect | v1.14 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | R13-FEED-2's fix skipped evaluating a spill bar and the bar after it, but kept both in history. The spill bar held the print's shares, and the next bar was short by exactly those shares. The short bar lowered later RVOL baselines, in trailing mode at bar N+21 and in time-of-day mode in later sessions, so a breakout the exchange's bars never show still fired. A spill bar that was also partial (a reconnect) was not recorded at all, and the short bar after it was even evaluated. This predates v1.13. | Both bars are discarded (logged), a partial spill bar included, and the strict back-fill restores the exchange's two bars before the next bar is evaluated. | `test_a_spill_bar_and_the_next_are_replaced_by_the_brokers_so_no_short_bar_stays_in_history` (with and without a reconnect) |
| low | ✔ | The next-bar skip compared only the bar before it in history. When the back-fill of an empty bucket after a spill bar returned nothing, a genuine bar two or more buckets later was skipped, with a log blaming the spill bar's close. This included the next session's opening bar. It is a v1.13 regression. | The bar after a spill bar is matched by its exact time. | `test_a_bar_after_an_empty_bucket_that_follows_a_spill_bar_is_evaluated` |

### Lifecycle

| Sev | Verdict | v1.13 defect | v1.14 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | Take a uvloop host that registered a loop callback mid-run. The handler a `SIG_IGN` guard saved around the fork is that callback's own dispatcher of the parent's loop, not the engines'. A child that lifted the guard with it got that dead dispatcher back from its own engine (`main()` or `run()`) and ignored every later stop. v1.13's inherited-map fix covered asyncio only. | The child's inherited map also records the parent's loop. A handler bound to that loop, other than the engines' dispatcher, stands for the default. | `test_a_child_that_lifted_a_guard_after_the_host_moved_to_a_loop_callback_gets_the_default_after_its_engine[*-uvloop]` |

### Tests and docs

| Sev | Verdict | v1.13 defect | v1.14 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The README said the proof's guard means "a stamp up to 30 s ahead cannot prove a bucket". It did prove one when the forward-stamped update was the window's only lag sample, which is common on an illiquid name. The engine read that sample as the host's offset, and the stamp then proved an unstamped print's bucket, so the print's shares were counted twice in an evaluated bar. | The proof corrects for the host only when a second lag sample backs the offset; the README says "ahead of the print's receipt". | `test_a_forward_stamp_alone_in_the_lag_window_does_not_prove_an_unstamped_prints_bucket` (and a control with a second sample) |
| medium | ✔ | The v1.13 limitation said an unstamped print near a boundary "costs signals, never fakes one". On a host running behind the exchange by more than a print's latency, a print faster than every lag sample of the minute, by more than a second minus that sample's truncation, is still filed a bucket early. With no bar open, its bar is evaluated. | Docs: a new Known limitations entry for this residual (keep the host's clock synced), with a pointer from the live-bar section. | (docs) |
| low | ✔ | The v1.13 spill skip on the back-fill path had no pin: removing it passed all 454 tests. | Pinned (the v1.14 discard now covers that path). | `test_an_open_bar_after_a_hole_joined_by_a_print_that_may_be_the_next_buckets_is_not_evaluated` |
| low | ✔ | The rewritten boundary limitation misstated its window. With no bar open, a late packet does not widen it. The open-bar spill window runs to the bar clock's close, which is wider by the 2 s grace. On a host running ahead, the documented formula gives a time before receipt. | Docs: the entry and the live-bar section state the actual bounds. | (docs) |
| low | ✔ | The suite was not independent of how it is launched. Under `nohup pytest`, two SIGHUP tests failed, because the harness restored only SIGINT. | `Child`, `HOST` and `SCRIPT` start their interpreters with SIGHUP and SIGTERM at their defaults. | the two SIGHUP tests, under `nohup` |

## v1.13 (2026-09-27)

v1.12 went through a thirteenth adversarial review on a frozen snapshot
(`85900f5`), with the same four areas. Every finder and skeptic completed.
There were 13 findings, with one duplicate across areas: R13-DATA-DOCS-1 is
R13-LIFECYCLE-5. That leaves 12 defects. None was refuted: 10 confirmed, 3
with part of the claim overstated. Of the 12 distinct defects, 4 are medium
and 8 low. Where the fix is not the finder's:
- R13-ORDERS-1: the finder's `recheck = maybe_booked` on its own lost a
  diagnosis. After a refused tunnel, a book answering 403 and then the proxy
  down, the alert no longer named the expired session. It ships with
  R13-ORDERS-3's fix, which remembers a permanent book error and names it, and
  a control pins the pair.
- R13-FEED-2: the finder's flag skipped only the open bar that a print from the
  next bucket may have joined. That print's price stays the bar's Close, which
  the next bar's first-crossing test compares against, so a stale crossing
  still fired one bar late. The bar after a spill bar is skipped too. A print
  received after the close cannot spill, and a stamped print of the bar's own
  bucket received after it clears the flag.
- R13-FEED-1: the skeptic added the cost the finder left out. The true upper
  bound files prints received up to a second before a boundary late on every
  host, synced ones included.
- R13-LIFECYCLE-5 / R13-DATA-DOCS-1: v1.13 takes the data/docs version. It also
  records the default in the child's inherited map, so a child that lifts a
  guard with the saved handler gets the default after its engine as well. A
  test pins this.
- R13-LIFECYCLE-3: a route whose dead dispatcher a worker thread cannot reset
  is kept for the next main-thread engine (or `run()`), not dropped.
- R13-LIFECYCLE-4: docs only. The finder's code option added state for a
  documented misuse, and it could not cover the window before the child's
  engine hooks.

Every v1.13 regression test fails on the v1.12 snapshot and passes here, on
Python 3.11 and 3.10 (`test_the_fast_modules_pass_without_the_kite_extra`
fails there too, because it runs the fast modules). The exceptions are these
controls:
- `test_a_refused_tunnel_then_a_dead_proxy_still_names_the_expired_session`:
  v1.12 named the session through the recheck that v1.13 drops. It fails when
  the permanent book error is not kept.
- `test_an_open_bar_joined_by_a_print_that_may_be_the_next_buckets_is_not_evaluated`
  for a print that traded 2 s before the boundary, whose bar is still
  evaluated.
- `test_a_stamped_print_of_the_bar_received_after_a_print_that_may_spill_clears_it`
  and `test_the_sessions_last_bar_joined_by_a_print_received_after_the_close_is_still_evaluated`.
  Each fails when its exemption is removed.
- `test_a_forked_helper_does_not_get_back_a_plain_handler_the_host_replaced_with_a_loop_callback_mid_run`
  on uvloop, which was already right.
- `test_a_held_run_whose_loop_closed_unreleased_leaves_no_dead_dispatcher_behind[asyncio-a loop without signal support]`.
  This R12 pin now runs without uvloop, and there it fails when the reset is
  removed.

Every v1.13 rule was also mutated on a copy, and each mutant failed its pins:
- the wakeup-fd clear back under `if routes:` (4 tests);
- the thread engine dropping the route (2);
- `run()` without the closed-route cleanup (2), or without the inherited
  mapping (2);
- the inherited map recording the stale plain handler (1);
- the book error not kept (2);
- the spill exemption at the close (1), and the spill flag never cleared (1);
- the next-bar skip removed (1);
- the proof checked against the new bound instead of the guarded one (1).

Existing tests changed:
- The forward-stamp control is now
  `test_after_a_forward_stamp_a_rebaselining_print_still_blinds_the_bucket_it_traded_in`.
  Under the true bound, a forward stamp also blinds the next bucket, which is
  back-filled. The assertion now checks only that the bucket the print traded
  in is blinded, never kept short.
- `test_a_child_that_lifted_a_guard_with_the_saved_dispatcher_is_stopped_after_its_own_engine`
  also runs the child's engine through `run()`.
- `test_a_held_run_whose_loop_closed_unreleased_leaves_no_dead_dispatcher_behind`
  skips only the cases that need uvloop. Without it, the asyncio case of the
  dead plain-handler reset still runs (R13-DATA-DOCS-2).

### Real-money safety: orders

| Sev | Verdict | v1.12 defect | v1.13 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ◐ | After a refused tunnel, a never-sent error (a proxy restart refusing connections) sent the GTT loop into a book recheck, which only reads the book. The book sits behind the same proxy and stayed unreadable, so the loop gave up with attempts unused: POSITION OPEN WITHOUT EXITS after about 3 windows, not the promised full retries. Overstated: it needs refusals that outlast a window after the connect failure, and no documented guarantee named this path. | The book is read before a retry only after an attempt that may have reached Kite. | `test_a_proxy_restart_inside_a_tunnel_outage_still_uses_every_attempt` |
| low | ✔ | A stop handled between `execute()`'s check and the entry task's first step still sent `place_order`, contradicting "once shutdown has begun, no new entry is sent". The window is narrow: a stop and the LTP reply both pending while the loop is busy. | The entry task checks again right before sending, with no await in between. | `test_a_stop_handled_after_the_entry_was_scheduled_but_before_it_ran_sends_nothing` |
| low | ✔ | With refused tunnels alone, an expired session reported by the GTT book was never named. The alert named only the proxy, so the operator learned only that the position was unprotected. | The book's permanent error is kept and named: "…; the GTT book answered TokenException(…)". | `test_refused_tunnels_name_the_expired_session_the_gtt_book_answered`, `test_a_refused_tunnel_then_a_dead_proxy_still_names_the_expired_session` (control) |

### Live feed

| Sev | Verdict | v1.12 defect | v1.13 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ◐ | Each lag sample is latency plus truncation minus the host's offset, so it gives only a lower bound on how far the host runs behind. On a host running behind, the lowest-sample bound subtracted that sample's latency too. When the late packet was the guard's upper median, the offset was dropped altogether. A print's "latest possible time" then fell before the print itself. B was filed in a bucket where the name never traded, and that bar was evaluated: R12-FEED-1's fake breakout, back. On the same host, the opening print was dropped as pre-open, and a re-baseline left the next bar short. Overstated: with many samples in the window, the misfiling window is about one stamp's truncation, and an offset with no sample stays uncorrectable. | The latest possible time is receipt + 1 s − the lowest sample. That is a true upper bound whenever the print's latency is at least the window's lowest. The proof keeps the median-guarded bound, so a forward stamp still proves nothing. The cost: prints received up to a second before a boundary are filed late on every host, and more after a forward stamp. These are missed signals, documented. | `test_on_a_host_behind_the_exchange_an_unstamped_trade_is_not_filed_in_a_bucket_before_it_traded` (three samples, and two), `test_on_a_host_behind_the_exchange_an_unstamped_opening_print_with_samples_is_not_dropped_as_pre_open`, `test_on_a_host_behind_the_exchange_a_rebaselining_print_blinds_the_bucket_it_traded_in` |
| medium | ✔ | When a bar of the bucket before was still open, an unstamped print that could belong to the next bucket joined it. It did so either inside the 2 s grace, or through a feed time pulled back by a late packet. It became that bar's Close and part of its volume, and the bar was evaluated with no log: a fake breakout the exchange bars never show. This is the bar-open sibling of R12-FEED-1, and it predates v1.12. | Such a print marks the bar as a spill bar. Neither that bar nor the next one (whose first-crossing test would compare against the print's price) is evaluated, and each skip is logged. A stamped print of the bar's own bucket received after it clears the mark. The session's last bar is exempt. | `test_an_open_bar_joined_by_a_print_that_may_be_the_next_buckets_is_not_evaluated` (within the grace, after a late packet, and a control), `test_the_bar_after_one_whose_close_may_be_the_next_buckets_print_is_not_evaluated`, `test_a_stamped_print_of_the_bar_received_after_a_print_that_may_spill_clears_it` (control), `test_the_sessions_last_bar_joined_by_a_print_received_after_the_close_is_still_evaluated` (control) |
| low | ✔ | The new limitation understated which prints are filed late. It missed prints received before the boundary: up to 1 s minus the lag on a synced host, and every bucket's last moments on a host running ahead. It also said "for a minute" where a late packet's lag holds until the next sample. | Docs: the limitation gives the real window, including the host-ahead lead, forward stamps, the open-bar spill and its cost, and the unmeasurable offset. | (docs) |

### Lifecycle

| Sev | Verdict | v1.12 defect | v1.13 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | `run()` saved the signal handlers before its engine started and restored them afterwards. After a held run closed unreleased, it put back the dead dispatcher that its own engine had just reset. In a forked child that had lifted a guard with the saved handler, it put back the parent engines' dispatcher. Either way every later stop was swallowed, so the R12 fixes worked only through `main()`. | `run()` first drops closed routes (resetting their dead dispatchers), then maps an inherited dispatcher to the host's handler, as `_take` does, before it saves anything. | `test_run_after_a_held_run_closed_unreleased_leaves_no_dead_dispatcher_behind` (uvloop, and a loop without signal support), `test_a_child_that_lifted_a_guard_with_the_saved_dispatcher_is_stopped_after_its_own_engine[run-*]` |
| medium | ✔ | The at-fork reset cleared the wakeup fd only when engines were routed. uvloop points the wakeup fd at its self-pipe whenever it runs, and asyncio does once any loop callback exists. A worker forked before the engine hooked, such as a pool started first, therefore wrote every stop it handled in Python into the parent's pipe. That covers its own graceful handler, SIGINT's default, and 3.11's Runner. The parent's engine then shut down on a signal sent to the worker. It predates v1.12. | The wakeup fd is cleared at every fork, as CPython 3.12's asyncio does. | `test_a_worker_forked_before_the_engine_hooked_does_not_forward_its_stops_to_the_parent` (SIGTERM and SIGINT; asyncio and uvloop) |
| low | ✔ | An engine in a worker thread cannot reset a signal. Its `hook()` still dropped the closed route, losing the only record that the handler in place was dead. The next main-thread engine then handed it back. | The route is kept until a main-thread engine (or `run()`) can reset it. | `test_an_engine_in_a_thread_leaves_a_dead_dispatcher_for_the_main_thread_to_reset` (uvloop, and a loop without signal support) |
| low | ◐ | The README told a child to lift a guard with "what the host had before its engines started". On 3.11 under `asyncio.run`, that is the Runner's SIGINT handler, which the same paragraph says cannot run in the child, so the child's first Ctrl-C was swallowed. Overstated: a second Ctrl-C stops it. | Docs: lift a guard with `SIG_DFL` (`signal.default_int_handler` for SIGINT) or with a plain handler, never with anything that ran through a loop. | (docs) |
| low | ✔ | R13-LIFECYCLE-5, and the same defect as R13-DATA-DOCS-1. On asyncio, a loop callback the host registered mid-run sits under the engines' shared dispatcher. At fork the child therefore got the plain handler the host had used before the run, which the host had replaced. The README promises the default here, and the parent's hand-back keeps the callback. It predates v1.12. | An asyncio table entry that is not the engines' gives the child the default, both at fork and in its inherited map. | `test_a_forked_helper_does_not_get_back_a_plain_handler_the_host_replaced_with_a_loop_callback_mid_run` (asyncio and uvloop; before and after its own engine), `test_a_child_that_lifted_a_guard_after_the_host_moved_to_a_loop_callback_gets_the_default_after_its_engine` |

### Tests and docs

| Sev | Verdict | v1.12 defect | v1.13 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | Without uvloop, the only non-uvloop pin of the dead plain-dispatcher reset was skipped, although the README says only uvloop cases are. A mutant without the reset passed the whole suite on the documented pip path. | The test skips only the cases that need uvloop, and imports it lazily. | `test_a_held_run_whose_loop_closed_unreleased_leaves_no_dead_dispatcher_behind[asyncio-a loop without signal support]` |

## v1.12 (2026-09-27)

v1.11 went through a twelfth adversarial review on a frozen snapshot
(`607a31f`), with the same four areas. Every finder and skeptic completed. 15
findings, two duplicates across areas: R12-DATA-DOCS-4 is R12-LIFECYCLE-3, and
R12-DATA-DOCS-5 is the window part of R12-FEED-1. That leaves 13 defects. None
refuted: 10 confirmed, 5 with part of the claim overstated. 4 medium, 9 low
(the 13 distinct). Where the fix is not the finder's:
- R12-FEED-1: the skeptic dated an unstamped print by a stamped update received
  before it, and left the case with no stamp (and the one-late-packet window,
  R12-DATA-DOCS-5) to the docs. v1.12 goes further. With no bar open, the print
  is filed in the latest bucket it can belong to, and that bar is ambiguous as
  before, which removes the fake breakout and the double count in both cases.
  The price is a missed signal for a print that really traded in a bucket's
  last moments (Known limitations). A print whose latest bucket is past the
  close stays in the session's last bar.
- R12-LIFECYCLE-3: the finder's fix reset any dispatcher still in place after
  a closed held loop. On asyncio that is the shared no-op, which by then is a
  host's new loop callback: the host lost it. Only a dispatcher bound to the
  dead loop, or the route's own plain handler, goes back. The data/docs skeptic
  (R12-DATA-DOCS-4) added a callback the host re-registered on the dead loop,
  and restores the host's plain handler rather than always the default. v1.12
  also keeps a host handler bound to a loop that is still running (one that
  forwards the signal to another thread's loop). Only in a forked child, where
  no loop of the parent runs, does every loop-bound handler go to the default.
- R12-LIFECYCLE-4: the finder's warning condition read "reset to the default",
  which missed SIGINT under 3.11's `asyncio.run` (its Runner installs its own
  handler). The skeptic's dead-dispatcher test is used instead.
- R12-DATA-DOCS-1: a precise type check for `asyncio.Runner`'s SIGINT handler,
  not the finder's "a partial whose owner has a `_loop`", which would also have
  reset a host's own handler of that shape.
- R12-ORDERS-3: the finder's "under GTT n" still read as protection.

Every v1.12 regression test fails on the v1.11 snapshot and passes here
(`test_the_fast_modules_pass_without_the_kite_extra` fails there too, because
it runs the fast modules; the Ctrl-C tests fail there on Python 3.11, whose
`asyncio.run` installs the handler, and pass on 3.10). The exceptions are these
controls:
- `test_a_gtt_armed_after_a_504_is_still_watched_for_a_duplicate`: a request
  that may have reached Kite still starts the duplicate watch;
- `test_an_unstamped_print_received_just_after_the_close_stays_in_the_sessions_last_bar`:
  filing late stops at the close. It fails when that guard is removed;
- `test_a_forked_helper_is_stopped_by_its_own_stop_after_the_host_registered_a_callback_mid_run`
  on asyncio (its shared no-op was always reset) and
  `test_a_forked_helper_gets_the_hosts_live_plain_handler[set beside a loop callback-uvloop]`
  (uvloop has no readable loop entry under the plain handler);
- `test_after_a_held_asyncio_run_closed_unreleased_a_hosts_new_loop_callback_is_kept`:
  it fails with the finder's version of the R12-LIFECYCLE-3 fix;
- `test_a_held_signal_taken_back_before_its_loop_closed_is_reported_only_where_the_close_undid_it[asyncio]`:
  the documented asyncio reset is still reported;
- the pins for v1.11 rules that no test covered:
  `test_a_forked_helper_with_a_host_plain_handler_does_not_forward_its_stop_to_the_parent`
  (the at-fork wakeup-fd clear),
  `test_a_bar_an_unstamped_print_opened_while_closing_a_partial_bar_is_not_evaluated_if_the_bucket_before_traded`
  (R11-FEED-2 on the close-and-open path) and the halt-report assertion added
  to `test_a_fill_during_shutdown_whose_gtt_had_fired_is_not_reported_as_exits_armed`.
  Each fails on the mutation that removes its rule.

Every v1.12 rule was also mutated on a copy, and each mutant failed its pins:
- the Runner check (5 tests);
- the child's map of inherited dispatchers (2);
- the dead-loop match narrowed to the engines' own dispatcher (2);
- the warning on any dead dispatcher (2);
- the wakeup-fd clear (10);
- the in-process restore treated like a fork's (1);
- the close guard (1).

Existing tests changed:
- `test_gtt_requests_refused_only_at_the_proxys_tunnel_leave_a_position_known_to_have_no_gtt`
  expects 3 attempts where the book is unreadable (was 1).
- `test_an_unconfirmed_cancel_does_not_call_a_fired_gtt_cover` expects "NOT
  surely covered (GTT 700; …)", with no "covered by GTT".
- `test_a_fill_during_shutdown_whose_gtt_had_fired_is_not_reported_as_exits_armed`
  also checks the halt report's line.
- The suite restores SIGINT's default when it starts with SIGINT ignored. A
  shell's background job (`pytest &`) starts that way, and the engine keeps an
  ignored signal ignored, as under nohup. The first v1.12 matrix was launched
  like that and failed the 12 SIGINT tests in every configuration.
- A CLI child still running when its test ends is killed. A stop that never
  arrived used to leave a `--run-seconds 0` engine running for good.

### Real-money safety: orders

| Sev | Verdict | v1.11 defect | v1.12 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | After a refused tunnel, if the GTT book could not be read either, v1.11 left the loop ("only refused tunnels: no GTT can exist") with two attempts unused. The book goes through the same proxy, so in a real proxy outage it is refused too. An outage longer than about two windows (~30 s) left a filled position without exits. The alert also blamed the proxy even when the next attempt would have named an expired session. v1.10 gave up here too, reporting UNKNOWN. | When no GTT can exist (only refused tunnels), an unreadable book no longer stops the retries: every attempt is used, and the last one names its own error. | `test_a_proxy_outage_that_also_hides_the_gtt_book_is_ridden_out_with_every_attempt`, `test_a_refused_tunnel_then_an_expired_session_names_the_session`, `test_gtt_requests_refused_only_at_the_proxys_tunnel_leave_a_position_known_to_have_no_gtt[False]` (now 3 attempts) |
| low | ✔ | The duplicate watch still ran after refused tunnels alone, which cannot book a GTT. A book that failed at the end of the watch raised "could not rule out a duplicate" and exit 1 for a cleanly protected run. | The watch runs only after an attempt that may have reached Kite. | `test_a_gtt_armed_after_refused_tunnels_alone_starts_no_duplicate_watch`, `test_a_gtt_armed_after_a_504_is_still_watched_for_a_duplicate` (control) |
| low | ◐ | An unconfirmed cancel still said the bought shares were "covered by GTT n" when that GTT had fired. For a fired duplicate, it named the GTT that the DUPLICATE alert says to DELETE. Overstated: the TRIGGERED note was in the same sentence, and the path needs a partial fill, an unconfirmed cancel, a lost reply and a trigger during the watch. | "NOT surely covered (GTT n; a GTT for these shares has already TRIGGERED: …)"; an active GTT still reads "covered by GTT n". | `test_an_unconfirmed_cancel_does_not_call_a_fired_gtt_cover` (updated) |

### Live feed

| Sev | Verdict | v1.11 defect | v1.12 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ◐ | With no bar open, an unstamped trade was filed at feed time. That errs early by the stamps' truncation plus however much the minute's largest lag exceeds the trade's own latency, which is seconds after one late packet. In the repro, a stamped quote of 10:05:00 arrived just before the trade, yet the trade was filed in 10:00, where the name never traded. The quote, a stamp of a later bucket, even "proved" that bucket. The bar was evaluated: a fake breakout at 102, while the true 10:05 bar closed below the base high. When nothing else traded in 10:05, its back-fill counted the shares again, contradicting "not counted twice". R11-FEED-1's fix had covered only the case where a bar of 10:00 had closed. Overstated: it predates v1.11 (v1.10 fired the same signal), and the proof does not cause the fake signal. R12-DATA-DOCS-5 found that the README's "about a second" understated the window. | With no bar open, an unstamped print is filed in the latest bucket it can belong to (its latest possible time's), unless that bucket is past the close. This generalizes R11-FEED-1's rule, which it replaces. A stamp proves a print's bucket only if it belongs to that bucket; this is defense in depth and cannot be reached now. The README gives the real error of feed time and the missed-signal residual. | `test_an_unstamped_trade_received_after_a_stamp_of_the_next_bucket_is_filed_in_that_bucket`, `test_with_no_bar_open_an_unstamped_trade_is_filed_in_the_latest_bucket_it_can_belong_to` (a late packet of 2.6 s, 4 s and 1.3 s), `test_an_unstamped_trade_filed_late_is_not_counted_twice_when_the_bucket_it_can_belong_to_is_back_filled`, `test_an_unstamped_print_received_just_after_the_close_stays_in_the_sessions_last_bar` (control) |

### Lifecycle

| Sev | Verdict | v1.11 defect | v1.12 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | Take a held run whose loop closed without a release. uvloop cannot remove its dispatcher once the loop has stopped, and the plain handler (for a loop without signal support) outlives its loop. The next engine warned, then took that dead dispatcher for the host's handler and handed it back. Every later SIGTERM, SIGINT and SIGHUP was swallowed while no engine ran, and the host's own handler was lost. It predates v1.11. The data/docs reviewer found the same defect (R12-DATA-DOCS-4). | The next engine resets any dispatcher bound to the closed loop to what the host had before the run: the engines' dispatcher, a callback the host re-registered on that loop, or the closed route's plain handler. The host gets back its plain handler (including one bound to a loop still running elsewhere), or the default. asyncio's shared dispatcher is left alone: asyncio's close already reset its own, and one in place now belongs to a host's new callback. | `test_a_held_run_whose_loop_closed_unreleased_leaves_no_dead_dispatcher_behind` (held on uvloop, on a loop without signal support, and on uvloop with a host callback re-registered; next on uvloop and asyncio), `test_a_host_plain_handler_comes_back_after_a_held_uvloop_run_closed_unreleased`, `test_a_host_handler_that_forwards_to_a_live_loop_comes_back_after_a_held_run_closed_unreleased`, `test_after_a_held_asyncio_run_closed_unreleased_a_hosts_new_loop_callback_is_kept` (control) |
| medium | ✔ | R12-DATA-DOCS-1: on 3.11+, `asyncio.run` (and `uvloop.run`) installs its own SIGINT handler, bound to the parent's loop and task. The at-fork reset did not count it as running through a loop, so the child got it back, and there the first Ctrl-C only woke the parent's loop. A Ctrl-C to the process group stopped the parent in order, and a worker went on to run its engine to the end. CPython does the same with no engine. | asyncio's Runner handler counts as loop-bound, so the child gets the default (`KeyboardInterrupt`). | `test_a_forked_worker_is_stopped_by_its_own_ctrl_c` (asyncio and uvloop, before its engine hooks and after it ended), `test_a_forked_helper_that_runs_no_engine_is_stopped_by_its_own_ctrl_c` |
| low | ◐ | On uvloop each `loop.add_signal_handler` installs a new dispatcher. At fork, a host callback registered mid-run was taken for the host's own choice and left in the child, bound to the parent's loop. The child swallowed every stop before its own engine ran and, once that engine handed it back, after it too. Overstated: not a regression (v1.10 also forwarded the stop), and the README already says such a callback displaces the running engine. | At fork, any handler bound to a loop goes to the default in the child. | `test_a_forked_helper_is_stopped_by_its_own_stop_after_the_host_registered_a_callback_mid_run` (uvloop, before and after its own engine; asyncio controls) |
| low | ◐ | A child could lift a `SIG_IGN` guard with the handler saved when the guard was set, which is the parent engines' dispatcher. It then swallowed every stop until its own engine hooked. That engine took the dispatcher for the host's handler and handed it back, so the child swallowed every stop after its engine too. Overstated: the host restores a handler the README warns against, and the child does start with the guard as it was set. | At fork, the child records the parent engines' dispatcher and what the host had. An engine that finds that dispatcher in place hands back the host's handler. The window before the child's engine hooks is documented: lift a guard with `SIG_DFL`. | `test_a_child_that_lifted_a_guard_with_the_saved_dispatcher_is_stopped_after_its_own_engine` (asyncio and uvloop) |
| low | ✔ | A held signal taken back with `signal.signal`, as documented, survives uvloop's close, yet v1.11 warned that it had never been released. On asyncio the held entry, still in the loop's table, makes the close reset the signal to its default even after the take-back, which was undocumented. | The warning stays only where the close undid something. That is always the case on asyncio. On uvloop or the plain route, it is only while the engines' own dead dispatcher still holds the signal. The README says to release before the loop closes. | `test_a_held_signal_taken_back_before_its_loop_closed_is_reported_only_where_the_close_undid_it` (uvloop; asyncio pins the documented reset) |
| low | ✔ | The at-fork reset gave a child the default whenever the host's loop callback sat under its plain handler, or a re-take had lost that callback. Yet the parent's hand-back restores the plain handler. The child was killed instead of running the host's handler. | Decided from what the host had alone, in one helper shared with the closed-loop hand-back. | `test_a_forked_helper_gets_the_hosts_live_plain_handler` (beside a loop callback, and set mid-run before a second engine; asyncio and uvloop) |

### Tests and docs

| Sev | Verdict | v1.11 defect | v1.12 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | The at-fork wakeup-fd clear, part of R11's high fix, had no test: removing it passed all 386. A plain handler restored in the child runs in Python, so the signal is also written to the parent loop's self-pipe, which stopped the PARENT's engines. | Pinned. | `test_a_forked_helper_with_a_host_plain_handler_does_not_forward_its_stop_to_the_parent` (SIGTERM and SIGINT). The Ctrl-C and plain-handler fork tests fail on that mutation too. |
| low | ✔ | R11-FEED-2's rule was unpinned on the close-and-open path. An unstamped print past the grace that closed a partial bar and opened the next could have been evaluated with shares the back-fill counts again, and every test would still pass. | Pinned. | `test_a_bar_an_unstamped_print_opened_while_closing_a_partial_bar_is_not_evaluated_if_the_bucket_before_traded` |
| low | ✔ | The fired-GTT note on the halt report's "filled during shutdown" line was unpinned. | Pinned. | `test_a_fill_during_shutdown_whose_gtt_had_fired_is_not_reported_as_exits_armed` (the halt line) |

## v1.11 (2026-09-27)

v1.10 went through an eleventh adversarial review on a frozen snapshot
(`8a5cb18`), with the same four areas. The run was interrupted by a container
restart and resumed from its journal; every finder and skeptic completed. 12
findings, one duplicate across areas (the data/docs reviewer's R11-DATA-DOCS-1
is R11-LIFECYCLE-1), which leaves 11 defects. None refuted: 11 confirmed, 1
with part of the claim overstated. 1 high, 1 medium, 9 low. The skeptics'
refinements replaced the finder's fix four times:
- R11-FEED-3: the finder bounded a print's latest time by the lowest lag
  sample. One forward-stamped packet (the 30 s rule accepts up to 30 s) then
  dragged it up to 30 s late and blinded the wrong bucket. The bound is never
  more than 1 s below the upper median.
- R11-FEED-4: the finder let any stamp of the bucket prove an unstamped
  print's bucket. A quote stamped 15 s ahead then "proved" it, the late print
  of the bucket before was dropped, and R10's fake breakout returned. A stamp
  proves the bucket only if it is no later than the print could have traded.
- R11-FEED-2: `ambiguous` is decoupled from the 2 s window, but only together
  with R11-FEED-4's guarded proof. On a feed whose packets mostly lack exchange
  time, it can now skip a genuine first bar after a reconnect: a missed
  signal, never a fake one.
- R11-DATA-DOCS-1: the at-fork reset maps a handler the host had that runs
  through a loop (its own loop callback, or a stale dispatcher bound to any
  loop, such as a closed uvloop loop's) to the default: restored in the child,
  it would swallow every stop.

Every v1.11 regression test fails on the v1.10 snapshot and passes here
(`test_the_fast_modules_pass_without_the_kite_extra` fails there too, because
it runs the fast modules). The exceptions are these controls:
- `test_a_504_before_a_refused_tunnel_still_leaves_the_gtt_state_unknown` and
  `test_a_duplicate_that_had_fired_marks_the_fill[active]`: a request that
  may have reached Kite is still UNKNOWN, and an active duplicate marks nothing;
- `test_a_stamp_ahead_of_an_unstamped_prints_latest_time_proves_nothing` and
  `test_one_forward_stamp_does_not_drag_a_prints_latest_time_late`: the
  skeptics' guards. Each fails with the finder's version of its fix;
- `test_a_host_guard_is_left_alone_in_a_forked_worker`: a host's `SIG_IGN`
  around the fork stays the host's choice in the child;
- the pins for v1.10 rules that no test covered (R11-ORDERS-3,
  R11-DATA-DOCS-3): `test_a_never_sent_window_that_expires_after_an_ambiguous_attempt_reports_the_gtt_state_unknown`,
  the `exits_unknown` assertions added to
  `test_when_every_gtt_request_fails_ambiguously_the_alert_says_one_may_exist`,
  `test_a_permanent_error_while_rechecking_the_book_ends_the_loop_and_is_named`
  and `test_a_lost_gtt_reply_after_an_unreadable_snapshot_is_reported_not_guessed`,
  and `test_a_provisional_bar_a_stamped_print_of_its_own_bucket_joined_is_not_moved`,
  `test_a_provisional_bar_moved_into_a_bucket_the_feed_did_not_watch_whole_is_discarded`,
  `test_a_provisional_bar_is_not_moved_into_a_bucket_that_already_closed`.
  Each fails on the mutation that removes its rule.

Existing tests changed:
- The blind-bucket assertions check membership in the blinded span, through a
  helper that also reads earlier versions' single bucket; the R9 pin also
  asserts the bucket before is not blinded.
- The release-from-a-thread test checks the new message.

### Real-money safety: orders

| Sev | Verdict | v1.10 defect | v1.11 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ◐ | A GTT that had already fired (adopted TRIGGERED after a lost reply, or a TRIGGERED duplicate) was reported as the position's armed protection: "exits armed (GTT 700)" and "covered by GTT 700" next to the alert that it had fired; for a fired duplicate, the fill named the GTT the alert said to DELETE. Overstated: the halt line only names the GTT, the TRIGGERED alert is always printed too, and it predates v1.10. | A `Fill` carries `exits_fired`. Every report that names the position's exits adds "(a GTT for these shares has already TRIGGERED: CHECK ORDERS AND HOLDINGS)", and a late fill says "exits: GTT n (…)" instead of "exits armed". | `test_a_gtt_that_had_already_fired_is_not_reported_as_the_positions_armed_exit`, `test_an_unconfirmed_cancel_does_not_call_a_fired_gtt_cover`, `test_a_fill_during_shutdown_whose_gtt_had_fired_is_not_reported_as_exits_armed`, `test_a_duplicate_that_had_fired_marks_the_fill` (triggered, and an active control) |
| low | ✔ | A GTT request the proxy refused at the tunnel carried nothing to Kite, yet when only those failed, v1.10 reported "GTT STATE UNKNOWN" and `exits: UNKNOWN` (v1.9's halt line said `NONE`), contradicting "a position that surely has no GTT still says NONE". | A refused tunnel is still retried as ambiguous (the proxy-outage coverage is unchanged), but only an attempt that may have reached Kite makes a GTT possible. Refused tunnels alone end as `POSITION OPEN WITHOUT EXITS`, exits `NONE`. | `test_gtt_requests_refused_only_at_the_proxys_tunnel_leave_a_position_known_to_have_no_gtt` (book readable and not), `test_a_504_before_a_refused_tunnel_still_leaves_the_gtt_state_unknown` (control) |
| low | ✔ | R11-ORDERS-3: two of v1.10's three `exits_unknown` sites (a match adopted while the pre-arming book was unreadable; every request ambiguous) had no test: setting either to False passed every test. | Pinned. | `test_a_lost_gtt_reply_after_an_unreadable_snapshot_is_reported_not_guessed`, `test_when_every_gtt_request_fails_ambiguously_the_alert_says_one_may_exist`, `test_a_permanent_error_while_rechecking_the_book_ends_the_loop_and_is_named`, `test_a_never_sent_window_that_expires_after_an_ambiguous_attempt_reports_the_gtt_state_unknown` |

### Live feed

| Sev | Verdict | v1.10 defect | v1.11 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | A stamped quote of bucket N closed the N-1 bar; an unstamped trade received just after it had feed time in N-1 and was dropped as late. Its shares rode into a later bar while the back-fill of N counted them again: a fake RVOL breakout. It predates v1.10, but contradicts v1.10's documentation ("not counted twice"). | A print received after the symbol's last bar closed, whose latest possible time is past that bar's end, is filed after it: its bar is then provisional/ambiguous as any other. | `test_an_unstamped_trade_received_after_a_stamped_quote_closed_the_bar_is_not_dropped_as_late` |
| low | ✔ | R10-FEED-1's guard covered only feed times within 2 s of the boundary. An unmeasured latency rise past that brought back the double count and the fake breakout. | Any bar an unstamped print opens is ambiguous (not evaluated if the back-filled bucket before it traded), unless a stamp proves its bucket (below). The reclaim keeps its 2 s window. | `test_a_bar_an_unstamped_print_opened_long_after_a_boundary_is_still_not_evaluated_if_the_bucket_before_traded` |
| low | ✔ | A print's latest possible time came from the median lag sample, which the latency hides a host's offset in: for a host even 0.5 s behind, the opening print was dropped as pre-open and a re-baseline blinded the previous bucket. | The lowest sample bounds the offset, never more than 1 s below the upper median (the skeptic's refinement). | `test_on_a_host_behind_the_exchange_a_rebaselining_print_still_blinds_the_bucket_it_can_belong_to`, `test_one_forward_stamp_does_not_drag_a_prints_latest_time_late` (control) |
| low | ✔ | A bar was marked ambiguous (and not evaluated) even when a stamped update of its own bucket, received before its unstamped opener, proved the bucket: a genuine breakout missed. | Such a stamp proves the bucket, if it is no later than the print's latest possible time (the skeptic's guard against forward stamps). | `test_a_stamp_of_its_own_bucket_received_first_proves_an_unstamped_prints_bar`, `test_a_stamp_ahead_of_an_unstamped_prints_latest_time_proves_nothing` (control) |

### Lifecycle

| Sev | Verdict | v1.10 defect | v1.11 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | v1.10's fork fix dropped the parent's routes only when the worker's own engine hooked. Before that (a worker's kite start-up takes seconds) and after its engine ended, the worker kept the parent engines' dispatcher and (on 3.10/3.11 asyncio, which has no at-fork reset) the parent loop's wakeup fd. A stop sent to the worker stopped the PARENT's engines or was swallowed, and the worker traded on; a helper that ran no engine did the same. The data/docs reviewer found the same defect (R11-DATA-DOCS-1) plus the case of a host with its own loop callback. | At fork time, in the child, the parent's routes are dropped and each signal still on an engine dispatcher goes back to what the host had before any engine ran (the default where that was a loop's callback); the wakeup fd is cleared. A signal the host set itself (a `SIG_IGN` guard) is left alone. The pid check stays as a backstop. | `test_a_forked_worker_takes_its_own_stop_signals_before_and_after_its_engine` (asyncio and uvloop), `test_a_forked_helper_that_runs_no_engine_is_stopped_by_its_own_stop` (with and without a host loop callback), `test_a_forked_helper_does_not_inherit_a_stale_loop_dispatcher_the_host_had`, `test_a_host_guard_is_left_alone_in_a_forked_worker` (control) |

### Tests and docs

| Sev | Verdict | v1.10 defect | v1.11 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | R11-DATA-DOCS-2: a re-baselining unstamped print blinded one bucket (v1.9 the earliest it could belong to, v1.10 the latest), so each version kept a short bar in the other case; on a host well behind, even the latest estimate fell before the trade. | Every bucket it can belong to is blinded, from its feed time's to its latest possible time's, which is at least a stamp's truncation past its feed time. Both only discard more bars (back-filled). | `test_a_rebaselining_unstamped_print_that_traded_before_the_boundary_blinds_that_bucket_too`, `test_on_a_host_well_behind_a_rebaselining_unstamped_print_still_blinds_the_bucket_it_can_belong_to`, `test_on_a_host_well_behind_an_unstamped_opening_print_is_not_dropped_as_pre_open` (2 s and 5 s) |
| low | ✔ | R11-DATA-DOCS-3: three rules of the provisional-bar move (not into a closed bucket; settled once a stamped print joins; partial recomputed) and two `exits_unknown` sites had no test. | Pinned (the GTT ones under R11-ORDERS-3). | `test_a_provisional_bar_is_not_moved_into_a_bucket_that_already_closed`, `test_a_provisional_bar_a_stamped_print_of_its_own_bucket_joined_is_not_moved`, `test_a_provisional_bar_moved_into_a_bucket_the_feed_did_not_watch_whole_is_discarded` |
| low | ✔ | R11-DATA-DOCS-4: `release_signals()`'s error advised deferring the release to the loop, which the R10 skeptic had rejected: a loop closed first leaves the signals held (on uvloop, swallowed). | The message says when that works. A held run whose loop closed unreleased is reported when the next engine starts. | `test_release_signals_from_another_thread_refuses_before_changing_anything`, `test_a_held_run_whose_loop_closed_unreleased_is_reported_when_the_next_engine_starts` |

## v1.10 (2026-09-27)

v1.9 went through a tenth adversarial review on a frozen snapshot
(`18afd78`), with the same four areas. Every skeptic ran. 16 findings, no
duplicates, none refuted: 13 confirmed, 3 with part of the claim overstated.
None is critical or high: 3 medium, 13 low. Several skeptics' refinements
replaced the finder's fix:
- R10-FEED-1: the finder's reclaim alone still double-counted when no stamped
  print of the earlier bucket arrived. A bar the reclaim cannot settle is not
  evaluated if the broker's back-fill says the bucket before it traded.
- R10-FEED-2: the finder dropped an unstamped print whose bucket was unclear
  when no bar was open. On an illiquid name that moved its shares into a later
  bar, which the back-fill then counted again (a new fake breakout). The print
  is kept, and the residual is documented.
- R10-LIFECYCLE-3: the finder deferred a release from another thread to the
  loop. If that loop never ran again, the release was lost and the host's
  handler with it. `release_signals()` now refuses up front, off the main
  thread, before anything changes.
- R10-DATA-DOCS-1: the finder's code alternative would have trusted the zero
  counter in the join's own bucket, which in the documented stale-snapshot
  residual keeps a bar carrying the whole outage's volume, and a signal. Docs
  only.

Every v1.10 regression test fails on the v1.9 snapshot and passes here
(`test_the_fast_modules_pass_without_the_kite_extra` fails there too, because
it runs the fast modules, which include the new feed and order tests). The
exceptions are these controls:
- `test_a_position_that_surely_has_no_gtt_still_reports_none`: a GTT that
  cannot exist is still "NONE";
- `test_a_bar_an_unstamped_print_opened_just_past_a_boundary_is_not_evaluated_if_the_bucket_before_traded[False]`:
  when the broker says the bucket before did not trade, the bar is evaluated;
- the pins for v1.9 rules that no test covered (R10-DATA-DOCS-2):
  `test_a_zeroed_rebaselining_print_joined_to_the_open_bar_blinds_the_bucket_of_its_feed_time`
  (it also fails on v1.8) and
  `test_a_zeroed_print_past_the_grace_does_not_join_a_bar_the_bar_clock_has_not_closed_yet`.
  Each fails on the mutation that removes its rule.

Existing tests changed:
- The Ctrl-C CLI test waits for the paper fill instead of sleeping 5 s, which
  failed under heavy load (R10-DATA-DOCS-3).
- Four hand-back tests assert that their engine ran (R10-DATA-DOCS-4). With an
  engine that exits before hooking, each now fails with its fix reverted.

### Real-money safety: orders

| Sev | Verdict | v1.9 defect | v1.10 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | A fill whose GTT state is unknown (a lost reply, then an unreadable book, including v1.9's expired-session exit) was reported like one with no GTT. The halt report said `exits: NONE`, an unconfirmed cancel said the shares were "NOT covered by a GTT" and a late fill "exits NOT armed", next to an alert saying a GTT may exist, and in the lost-reply case one did. That invites a manual second exit. It predates v1.9. | A `Fill` carries `exits_unknown` (not part of equality). Those reports now say `UNKNOWN` / "of UNKNOWN GTT state (a GTT may exist: CHECK THE GTT BOOK)". A position that surely has no GTT still says `NONE`. | `test_a_fill_whose_gtt_state_is_unknown_is_reported_as_unknown_not_as_unprotected`, `test_an_unconfirmed_cancel_says_the_bought_shares_gtt_state_is_unknown`, `test_a_fill_during_shutdown_whose_gtt_state_is_unknown_says_so`, `test_a_position_that_surely_has_no_gtt_still_reports_none` (control) |

### Live feed

| Sev | Verdict | v1.9 defect | v1.10 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | With no bar open, v1.9 filed a zeroed-stamp print at feed time. When latency rose at a boundary, a trade from the last second of an illiquid bucket opened the next bucket's bar. A late stamped print of the earlier bucket was then dropped as out of order, both went into the next bar, and the back-fill of the earlier bucket counted them again: a fake RVOL breakout, where v1.8 was right. The skeptic showed it needs no dropped print: the zeroed print alone is counted twice. | Such a bar is provisional: a later stamped print of the bucket before moves it there. A bar that is still unsettled when it closes is not evaluated if the broker's back-fill says the bucket before traded (its shares may be counted twice). That errs toward a missed signal, never toward an order. | `test_a_bar_an_unstamped_print_opened_is_given_back_to_the_bucket_its_late_neighbour_proves`, `test_a_bar_an_unstamped_print_opened_just_past_a_boundary_is_not_evaluated_if_the_bucket_before_traded` (the broker says it traded, and a control where it did not) |
| low | ◐ | Feed time is a lower bound: the lag includes the stamps' whole-second truncation. So within about a second after a boundary, v1.9's zeroed-stamp fixes did not hold: a re-baseline blinded the previous bucket, a print just after the bar opened was dropped as out of order, and after a lag left over from the last session the opening print was dropped as pre-open. Overstated: the pre-open case needs a run spanning sessions; a bar opened for an empty bucket only moves shares one bucket early. | The print's latest possible time is its receive time on the exchange's clock. A re-baseline blinds the latest bucket it can belong to; a print received after the one that opened the bar joins it; one received in session is filed at 09:15, not dropped. The empty-bucket case is documented (dropping the print instead would double-count). | `test_a_rebaselining_unstamped_print_blinds_the_latest_bucket_it_can_belong_to`, `test_an_unstamped_print_received_after_a_bar_opened_joins_it_even_when_feed_time_says_earlier`, `test_an_unstamped_opening_print_is_not_dropped_on_a_lag_left_over_from_the_last_session` |
| low | ◐ | The broker thread fixed an unstamped print's feed time with the lag of that moment. A queued print that raised the lag was processed after it, so the print missed the clamp and closed the bar before the bar clock would, dropping its tail. Overstated: the window is at most the bar clock's 1 s tick; it predates v1.9. | Feed time is recomputed on the loop, with the lag of every print received before it. | `test_an_unstamped_print_is_judged_on_the_lag_of_every_print_received_before_it` |

### Lifecycle

| Sev | Verdict | v1.9 defect | v1.10 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | On uvloop, `release_signals(loop)` between runs (a host on `run_until_complete`) relied on `remove_signal_handler`, which uvloop ignores while the loop is stopped. The signal stayed on uvloop's dispatcher with nothing behind it, swallowed for good, while the log said it was "back at its default". | When the removal does nothing and our handler is still installed, the default is set as uvloop's removal would have set it. | `test_on_uvloop_release_signals_outside_the_loop_really_restores_the_default` |
| medium | ✔ | A worker forked while engines ran inherited the parent's routes, on a loop that is not closed, so its engine never took a signal. A SIGTERM sent to the worker reached the parent's self-pipe and stopped the parent's engines while the worker's traded on; with a guard, it killed the worker mid-entry. It predates v1.9. | A route records the process that took it; a forked child drops the parent's routes and takes its own. | `test_a_forked_worker_takes_its_own_stop_signals` |
| low | ✔ | `release_signals(loop)` from another thread dropped SIGINT's route, then failed in the hand-back: Ctrl-C was swallowed for the rest of the process, and a main-thread retry could not repair it. | Off the main thread, with something to hand back, it raises `ValueError` before anything changes. | `test_release_signals_from_another_thread_refuses_before_changing_anything` |
| low | ✔ | The v1.9 warning for a signal handed back ignored was skipped on the plain-handler path (a loop that cannot own signals, as on Windows), where restoring the saved handler swallows the signal too. | The same warning there. | `test_without_loop_signal_handlers_an_engine_that_ends_inside_the_ignore_says_what_to_restore` |
| low | ✔ | The reclaim was a task. Released after `run_until_complete`, or abandoned by a loop that stopped inside the guard, it was never really cancelled, and asyncio logged "Task was destroyed but it is pending!". | The reclaim is a self-rescheduling timer handle: cancelling it takes effect at once, and one left behind is dropped silently. | `test_a_reclaim_left_pending_by_a_loop_that_stopped_inside_the_guard_is_dropped_silently` |

### Tests and docs

| Sev | Verdict | v1.9 defect | v1.10 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ◐ | R10-DATA-DOCS-1: the README said an untraded name's first traded bar is kept whole; not when it falls in the join's own bucket, which began before the feed was watching. Overstated: v1.7 did the same, so it is not an R9-LT-2 regression. | Documented (README and the v1.9 row). The finder's code alternative was rejected (see above). | documentation |
| low | ✔ | R10-DATA-DOCS-2: two parts of R9-LT-1 (the blind bucket from feed time, the 2 s bound on joining the open bar) had no test; removing either passed every test. | Pinned. | `test_a_zeroed_rebaselining_print_joined_to_the_open_bar_blinds_the_bucket_of_its_feed_time`, `test_a_zeroed_print_past_the_grace_does_not_join_a_bar_the_bar_clock_has_not_closed_yet` |
| low | ✔ | R10-DATA-DOCS-3: the Ctrl-C CLI test still slept a fixed 5 s and failed under heavy load; the CHANGELOG said no fixed-delay signal test remained. | It waits for the paper fill. | `test_ctrl_c_runs_the_orderly_shutdown_and_prints_the_halt_report` |
| low | ✔ | R10-DATA-DOCS-4: four more hand-back tests ignored `main()`'s result and passed with their fixes reverted when the engine exited early. | They assert it. | `test_handlers_the_engine_replaced_are_restored_afterwards`, `test_a_hosts_own_loop_signal_handlers_survive_the_engine`, `test_handing_a_signal_back_keeps_its_sa_restart_flag`, `test_a_live_plain_handler_beside_a_stale_loop_entry_comes_back_too` |
| low | ✔ | R10-DATA-DOCS-5: on asyncio a plain handler the host had before the run comes back without `SA_RESTART`; the README said the host gets back exactly what it had. | Documented (README signals section and Known limitations). The flag cannot be read back in Python. | documentation |
| low | ✔ | R10-DATA-DOCS-6: "within 50 ms" is not a bound: while the host blocks its event loop after lifting the guard, stops keep missing the engines. | Documented: at the next check, every 50 ms while the event loop is free. | documentation |
| low | ✔ | R10-DATA-DOCS-7: the Quick start still said 312 tests. | Corrected. | documentation |

## v1.9 (2026-09-27)

v1.8 went through a ninth adversarial review on a frozen snapshot
(`bf43ba6`), with the same four areas. Every skeptic ran. 12 findings, no
duplicates, none refuted: 9 confirmed, 3 with part of the claim overstated.
None is critical or high: 1 medium, 11 low. Three times the skeptic's
refinement replaced the finder's fix:
- R9-SR-1: the finder's reclaim re-took a route that only finished, held
  runs were left on. It then displaced the host's own handler and swallowed
  every later stop. A route is re-taken only while an engine still runs on it.
- R9-LT-2: the finder adopted a zero counter whenever the packet's last trade
  predated 09:15 today. A stale pre-open snapshot of a name that *had* traded
  then brought R8's fake 42x RVOL back. The skeptic's rule needs an earlier
  day's last trade *and* no bar of today.
- R9-SR-3: the warning covers the uvloop path too, not only asyncio's table.

Every v1.9 regression test fails on the v1.8 snapshot and passes here
(`test_the_fast_modules_pass_without_the_kite_extra` fails there too, because
it runs the fast modules, which include the new feed tests). The exceptions
are these controls:
- `test_a_transient_error_while_polling_for_a_lost_reply_gtt_still_extends_the_poll`:
  only a permanent error ends the GTT poll early;
- `test_a_zero_counter_is_adopted_only_when_nothing_says_the_name_traded_today`
  (three cases): the refinement's guard. The earlier-day case fails with the
  finder's version of the fix;
- `test_a_route_left_to_held_runs_is_not_taken_back_from_the_host_after_its_guard`:
  fails with the finder's reclaim;
- the pins for v1.8 guards that no test covered (R9-TD-2, R9-TD-3, R9-TD-4):
  `test_one_stamp_just_over_the_limit_does_not_stop_a_synced_host`,
  `test_a_just_over_drop_an_hour_ago_does_not_stop_a_host_that_drifted_close_to_the_limit`,
  `test_the_default_steady_clock_consults_the_wall_clock_where_the_monotonic_clock_misses_a_suspend`,
  `test_on_uvloop_a_re_take_keeps_the_warning_that_the_hosts_callback_was_lost`,
  and the wakeup-fd check added to
  `test_a_plain_handler_or_ignore_the_host_sets_during_a_run_is_kept[asyncio]`.
  Each fails on the mutation that removes its guard: the straddle stop's skew
  band, its 60 s recency window, the wall-clock detection off Linux, the
  uvloop "lost" carry on a re-take, the wakeup-fd reset.

Existing tests changed:
- `hooked()` fails the test if the engine exits before hooking, and the
  flags pin and the held-run pin assert that their engines ran (R9-TD-5).
  Each now fails on an engine that exits before hooking, which let the pins
  pass with their fixes reverted.
- The last two signal tests that slept a fixed second before signalling wait
  for the hook too.

### Real-money safety: orders

| Sev | Verdict | v1.8 defect | v1.9 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | v1.8 ended the re-check loop on a permanent error, but the two other GTT-book loops on the ambiguous path did not. After an expired session (`TokenException`, 403), the lost-reply poll read the book for two windows (30 s by default) and the duplicate watch for one. Neither alert named the error, only the earlier 504. | A permanent error ends both loops at once, and the alerts name it: `placing failed (…) and the GTT book could not be read (TokenException(…))`, and `GTT n armed after an ambiguous failure, but the GTT book could not be read (TokenException(…)) to rule out a duplicate`. Transient errors keep the extended poll. | `test_a_permanent_error_while_polling_for_a_lost_reply_gtt_ends_the_poll_and_is_named`, `test_a_permanent_error_during_the_duplicate_watch_ends_the_watch_and_is_named`, `test_a_transient_error_while_polling_for_a_lost_reply_gtt_still_extends_the_poll` (control) |

### Live feed

| Sev | Verdict | v1.8 defect | v1.9 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | R8-TD-9's fix filed every zeroed-stamp print 2 s early, whether or not a bar was open for the grace to protect. Received just after a bucket boundary, such a print did three things v1.7 did not (v1.7 did them too, but only within about a second of the lag: corrected in v1.10, R10-FEED-2). It marked the previous bucket blind when it re-baselined the counter, so the new bar was kept short (wrong Open and Volume). It opened a bar for a bucket in which nothing traded. And received just after 09:15:00, it was dropped as pre-open, so the opening bar lost its Open and High (which feed the ATR). The skeptic added a fourth: just after a bar opened, it was dropped as out of order. | A zeroed stamp is filed at feed time. Only if that falls within the bar clock's 2 s grace after the end of a bar that is still open does it join that bar, which keeps R8-TD-9's guarantee. A counter it re-baselines blinds the bucket of its feed time. | `test_a_zeroed_first_print_after_a_join_blinds_its_own_bucket_not_the_previous_one`, `test_a_zeroed_trade_with_no_bar_open_makes_no_bar_for_the_bucket_before_it`, `test_a_zeroed_opening_print_is_not_dropped_as_pre_open`, `test_a_zeroed_print_just_after_a_bar_opened_joins_it`; `test_a_zeroed_stamp_cannot_close_a_bar_when_latency_has_just_risen` still holds |
| low | ◐ | Since R8, a zero counter is never adopted after a blind spot. So a name that had not traded today, after a late join, reconnect or far-ahead drop, re-baselined on its first trade: its first traded bar was discarded and back-filled, and a breakout on it was never evaluated. v1.7 signalled. Overstated: the rule was documented (its cost was not), the trigger needs an illiquid name, and a missed signal is fail-safe. | A zero counter is adopted when the packet itself proves the name has not traded today (a real `last_trade_time` from an earlier day) *and* no bar of today is known (forming, closed or in the synced history). Then no bucket is blind, except the join's own bucket, which began before the feed was watching (corrected in v1.10, R10-DATA-DOCS-1). The residual case (first trades during an outage, then a stale snapshot) is listed under Known limitations. | `test_a_name_not_traded_today_keeps_its_first_traded_bar_after_a_join`, `test_only_a_zero_counter_with_an_earlier_days_last_trade_marks_a_name_untraded`, `test_a_zero_counter_is_adopted_only_when_nothing_says_the_name_traded_today` (control) |

### Lifecycle

| Sev | Verdict | v1.8 defect | v1.9 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ◐ | v1.8's fix for an engine that starts under a host's `SIG_IGN` guard joins the route that exists. But that route no longer received the signal if the host had re-registered or removed its own callback mid-run (both supported). Once the guard was lifted, a supervisor's stop went to the host's callback, or killed the process, with the engine's entry in flight. Overstated: the finder's third case (a guard after a held run the host had taken back) is the documented `nohup` rule. | An engine that joins under `SIG_IGN` starts a reclaim task. Once the host lifts the ignore, whatever it restores, the engines take the signal back within 50 ms (`RECLAIM_POLL`), while the event loop is free (corrected in v1.10, R10-DATA-DOCS-6), if it no longer reaches them. The skeptic's refinement: only while an engine still runs on the route. | `test_an_engine_that_joins_under_sig_ign_a_route_the_host_took_back_still_gets_the_signal` (re-register and remove, asyncio and uvloop), `test_a_route_left_to_held_runs_is_not_taken_back_from_the_host_after_its_guard` (control) |
| low | ✔ | On asyncio, `main(hold_signals=True)` could not be released as its docstring said ("until the caller restores them"). The handler a host with its own loop callback saves is asyncio's one shared `_sighandler_noop`, so restoring it changed nothing. The held run kept the signal, the host's callback was never re-armed, and every later stop was "ignored". | `engine.release_signals(loop=None)` hands back what finished held runs still hold. Engines still running keep the signal, and the last one out hands back as usual. main()'s docstring and the README point to it. | `test_release_signals_hands_back_what_a_held_run_kept` |
| low | ◐ | If the last engine ended while the host ignored a signal, the signal was handed back ignored. The handler the host had saved when it set `SIG_IGN` was the engine's, and restoring it swallowed every later SIGTERM, and on 3.11 Ctrl-C even after `asyncio.run` returned. Overstated: v1.7 did the same, the hand-back follows the documented rule, and no engine is at risk. | A warning says the signal is handed back ignored and names what to restore (what was there before the engine started). The hand-back is unchanged: keeping the table entry would let asyncio's `loop.close()` reset the host's `SIG_IGN` (the R8-SR-4 concern). The README says the same. | `test_an_engine_that_ends_inside_the_hosts_ignore_says_what_to_restore` (asyncio and uvloop) |

### Tests and docs

| Sev | Verdict | v1.8 defect | v1.9 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | R9-TD-1: on asyncio, a plain handler the host set mid-run loses `SA_RESTART` if another engine starts after it and re-takes the signal. The README promised its flags and listed the loss only for uvloop. It cannot be read back in pure Python. | Documented: README (signals, Known limitations) and the v1.8 R8-SR-4 row. | documentation |
| low | ✔ | R9-TD-2: the straddle stop's skew band and its 60 s recency window were unpinned; removing either passed every test. | Pinned: a synced host with one stamp 31 s ahead, and a host now 29 s behind whose only just-over drop was an hour ago, both keep running. | `test_one_stamp_just_over_the_limit_does_not_stop_a_synced_host`, `test_a_just_over_drop_an_hour_ago_does_not_stop_a_host_that_drifted_close_to_the_limit` |
| low | ✔ | R9-TD-3: the rule that turns on `wall_gain` off Linux (a default `time.monotonic`) was unpinned on every platform; forcing it off passed every test. | Pinned with the platform's own default. | `test_the_default_steady_clock_consults_the_wall_clock_where_the_monotonic_clock_misses_a_suspend` |
| low | ✔ | R9-TD-4: the uvloop "lost" carry on a re-take and the asyncio wakeup-fd reset were unpinned. Without the reset, a later signal wrote its byte into whatever file reused the closed self-pipe's fd. | Pinned. | `test_on_uvloop_a_re_take_keeps_the_warning_that_the_hosts_callback_was_lost`, `test_a_plain_handler_or_ignore_the_host_sets_during_a_run_is_kept[asyncio]` |
| low | ✔ | R9-TD-5: two v1.8 pins passed with their fixes reverted when the engine exited before hooking, since `hooked()` returned silently and the pins discarded `main()`'s result. | `hooked()` fails the test on an early exit, and both pins assert their results. | `test_a_plain_handler_set_mid_run_keeps_its_flags`, `test_a_held_engine_is_not_carried_into_a_later_run_once_the_host_restores_its_handler` |
| low | ✔ | R9-TD-6: four statements in the v1.8 CHANGELOG about test changes were wrong (see the note there). | Corrected. The two remaining fixed-delay signal tests now wait for the hook (one more, the Ctrl-C CLI test, still slept 5 s: converted in v1.10, R10-DATA-DOCS-3). | documentation |

## v1.8 (2026-09-27)

v1.7 went through an eighth adversarial review on a frozen snapshot
(`06bbaef`), with the same four areas. 18 findings, 3 of them duplicates
across areas (the data/docs reviewer's R8-TD-1, R8-TD-7 and R8-TD-8 are
R8-LT-1, R8-LT-3 and R8-SR-1), which leaves the 15 defects below.
Every finding was reproduced by its skeptic: 13 confirmed, 2 with part of the
claim overstated, none refuted. None is critical or high: 2 medium, 13 low. Three times the skeptic's
refinement replaced the finder's fix:
- R8-LT-1: only drops *just* over the limit count as straddling it, so one
  far-corrupt stamp cannot stop a healthy host.
- R8-LT-3: the pre-open margin is twice the limit.
- R8-SR-4: uvloop keeps v1.7's hand-back path. The "do nothing" variant left
  a stale entry, which made a host's later `loop.remove_signal_handler`
  reset its handler.

Every v1.8 regression test fails on the v1.7 snapshot and passes here, except
these controls:
- `test_a_host_just_inside_the_limit_is_not_stopped_by_one_corrupt_stamp` and
  `test_a_host_just_past_the_limit_still_loses_the_opening_bar_whose_first_print_was_dropped`:
  they pin the skeptics' refinements, and each fails with the finder's
  version of its fix;
- `test_on_a_clock_that_counts_a_suspend_the_wall_clock_is_never_consulted`:
  the Linux clock must stay immune to NTP steps;
- `test_the_steady_clock_keeps_counting_through_a_suspend` (rewritten,
  R8-TD-2): it now simulates a suspend, and fails when `_uptime` falls back
  to `CLOCK_MONOTONIC`;
- `test_a_corrupt_stamp_dated_another_day_does_not_credit_the_day_to_one_bar`
  (R8-TD-6): since R8-LT-3's guard, a stamp dated later fails the session
  guard, so keying the counter to the stamp's date no longer brings the fake
  bar back. The test keeps that property pinned.

Existing tests changed:
- The mid-run signal tests that raced start-up now wait until the engine has
  hooked its signals, instead of sleeping a fixed time (R8-TD-4). Two
  single-engine tests kept a fixed 1 s margin (converted in v1.9).
- The far-ahead watchdog test is bounded, so a regression fails instead of
  hanging (R8-TD-3).
- The steady-clock name pin reads `steady_clock.<locals>.now`.
- The no-extra guard stays strict: the suspend test no longer skips
  (R8-TD-5).

(Four statements in this section about test changes were corrected in v1.9,
R9-TD-6: a bullet about the no-extra guard and one about dropped uvloop
expectations described changes that were not in v1.8, and two overstated
how widely the signal tests wait and how far the platform skips go.)

Verdict: ✔ confirmed by the independent skeptic, ◐ confirmed with part of the
claim overstated. Severity is the skeptic's rating.

### Real-money safety: orders

| Sev | Verdict | v1.7 defect | v1.8 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | After an ambiguous attempt, a book re-check that failed for good (an expired session: `TokenException`, 403) was retried until the window ended. The alert then blamed the earlier refused connection. v1.6 stopped at once and named the TokenException. | A permanent error ends the re-check loop at once, and the alert names it. | `test_a_permanent_error_while_rechecking_the_book_ends_the_loop_and_is_named` |

### Live feed

| Sev | Verdict | v1.7 defect | v1.8 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | A host 30.2–31 s behind straddled the 30 s limit (whole-second stamps). Almost every bar held a dropped print and was discarded, while the prints that passed kept resetting the blind run, so the new stop never fired: a whole session of no bars and no exit. | The watchdog also stops the run when prints keep landing *just* over the limit (under 32 s ahead) and the measured skew is within 1.5 s of it. The skeptic's refinement: a far-off corrupt stamp does not count, so a host 29.x s behind is not stopped by one. | `test_a_host_straddling_the_30s_limit_stops_instead_of_discarding_every_bar`, `test_a_host_just_inside_the_limit_is_not_stopped_by_one_corrupt_stamp` (control) |
| low | ✔ | Where the monotonic clock stops during a suspend (not Linux), the bar a sleep interrupted was closed on wake and its 20-minute-old signal judged 1 s old, so a real order could go out before the watchdog stopped the run. The finder rated it medium; the skeptic rated it low for its tiny exposure. | On such a clock, the router adds the wall time the run's clock missed to every signal's age (`wall_gain`). This can only refuse more signals. Linux (`CLOCK_BOOTTIME`) is unchanged. | `test_on_a_clock_that_misses_a_suspend_a_signal_is_aged_by_the_time_it_missed`, `test_on_a_clock_that_counts_a_suspend_the_wall_clock_is_never_consulted` (control) |
| low | ✔ | A lone corrupt stamp could cost two bars, not the one documented. Received before the open, it cost the 09:15 opening bar, because it re-baselined a counter that off-session prints never move. | Only a drop that could hold session shares re-baselines. The margin is twice the limit, the skeptic's refinement, so a host just past it still loses an opening bar whose first print was dropped. The docs now say "its bar, and the next one too when...". | `test_a_corrupt_stamp_before_the_open_does_not_cost_the_opening_bar`, `test_a_host_just_past_the_limit_still_loses_the_opening_bar_whose_first_print_was_dropped` (control) |
| low | ◐ | A zeroed packet (`volume_traded` 0) accepted after a drop or a late join became the baseline, and the next print was credited with the whole day (a fake 42x RVOL). Overstated: v1.6 did the same, so this predates v1.7. | A zeroed counter is never adopted as a blind baseline: the next print re-baselines again. | `test_a_zeroed_packet_is_never_adopted_as_a_blind_baseline` (after a drop, and after a late join) |

### Lifecycle

| Sev | Verdict | v1.7 defect | v1.8 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | An engine that started while the host had a stop signal at `SIG_IGN` (say, around spawning workers) was left out of the route. Once the host restored its handler, a supervisor's stop reached only the other engines, and the next one killed it mid-run. The skeptic added a worse variant: if the other engine ended inside the window, the stop was swallowed silently. | The engine joins the existing route while the signal is ignored (nohup is still honoured: nothing is taken). It is reached once the host restores the signal, and a later re-take carries it along. | `test_an_engine_that_starts_while_the_host_ignores_the_signal_still_gets_it_later` (asyncio and uvloop) |
| low | ✔ | A re-take carried a finished `main(hold_signals=True)` callback along. After that, the host's restored handler was never handed back, and every later stop was "ignored". The finder rated it medium; the skeptic rated it low, since `run()` can never reach it. | A held callback is marked, and is not carried into a re-take. | `test_a_held_engine_is_not_carried_into_a_later_run_once_the_host_restores_its_handler` |
| low | ✔ | A re-take after the host changed only its plain handler recorded the engine's own dispatcher as the host's loop callback. The host's callback was lost, the stale entry later reset the host's handler when the loop closed, and on uvloop the warning was lost. | The re-take keeps the pre-run loop callback and the uvloop "lost" flag the host did not change. | `test_a_re_take_keeps_the_hosts_pre_run_loop_callback` |
| low | ◐ | Hand-back re-installed a plain handler the host set mid-run, clearing its `SA_RESTART`, and on uvloop it also removed a loop callback registered before it. Overstated: v1.6 was worse, and PEP 475 hides EINTR from Python code. | On asyncio only our table entry is changed, and the handler stays exactly as set, flags included (unless a later engine re-took the signal after the host set it, as v1.9 documents: R9-TD-1). uvloop keeps v1.7's path and its documented limits. | `test_a_plain_handler_set_mid_run_keeps_its_flags` |

### Tests and docs

| Sev | Verdict | v1.7 defect | v1.8 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | R8-TD-2: the suspend pin passed with `CLOCK_MONOTONIC`, because on a host that never slept the two clocks agree. | The pin simulates an hour's suspend. It is platform-neutral: elsewhere it checks the fallback, so the fast modules have no platform skip. | `test_the_steady_clock_keeps_counting_through_a_suspend` (control) |
| low | ✔ | R8-TD-3: the far-ahead watchdog test hung instead of failing when its fix was reverted. | Bounded (it fails within 2 s). | `test_a_run_whose_every_tick_is_far_ahead_stops_instead_of_running_blind` |
| low | ✔ | R8-TD-4: the mid-run signal tests raced engine start-up with fixed sleeps. One failed under load, and one passed on v1.6. | The mid-run signal tests that raced start-up wait until the engine has hooked its signals (two single-engine tests kept a fixed 1 s margin until v1.9). | the signal tests in `test_end_to_end.py` |
| low | ✔ | R8-TD-5: the no-extra guard asserted "no skips", and the Linux-only suspend test broke it on macOS and Windows. | The suspend test no longer skips, and the guard stays strict. | `test_the_fast_modules_pass_without_the_kite_extra` |
| low | ✔ | R8-TD-6: the receive-date keying of the re-baselined counter was unpinned. | Pinned (a control since R8-LT-3's guard, see above). | `test_a_corrupt_stamp_dated_another_day_does_not_credit_the_day_to_one_bar` |
| low | ✔ | R8-TD-9: a zeroed-stamp print stamped only by the last minute's largest lag could still close a bar early when latency rose at a bucket boundary. Its last genuine print was then dropped, changing the bar's Close. | The fallback subtracts the bar clock's own grace (`BAR_CLOSE_GRACE`, shared with `flush_due_bars`), so such a print cannot close a bar before the bar clock would. | `test_a_zeroed_stamp_cannot_close_a_bar_when_latency_has_just_risen` |
| medium | ✔ | R8-TD-1 is R8-LT-1 (the straddling host), seen from the docs. | Fixed there; its scenario (a third of the prints just over the limit) is pinned too. | `test_prints_every_two_seconds_a_third_of_them_just_over_the_limit_stop_the_run` |
| low | ✔ | README and CHANGELOG: "a lone corrupt stamp costs one bar" (R8-LT-3 / R8-TD-7), "takes the signal back" for `SIG_IGN` (R8-SR-1 / R8-TD-8). | Corrected. | documentation |

## v1.7 (2026-09-27)

v1.6 went through a seventh adversarial review on a frozen snapshot
(`ce04700`), with the same four areas. Every skeptic ran. All 13 findings were
reproduced by their skeptic: 9 confirmed, 4 with part of the claim overstated,
none refuted, no duplicates. One is high, 2 medium, 10 low. Twice the skeptic's
refined fix replaced the finder's:
- R7-KGW-1: the finder's re-check adopted a GTT that had already triggered
  without the TRIGGERED alert.
- R7-KGW-2: the finder's "refused tunnel = never sent everywhere" shrank the
  GTT path's proxy-outage coverage from about two windows to one. It now
  applies to the entry only.

Every v1.7 regression test fails on the v1.6 snapshot and passes here, except
these controls:
- `test_an_outage_after_an_ambiguous_attempt_whose_gtt_never_lands_still_arms_one`,
  `test_a_gtt_refused_at_the_proxys_tunnel_stays_on_the_ambiguous_path` and
  `test_one_corrupt_stamp_does_not_stop_the_run`: cases the fixes must leave
  alone. The last one fails if the run of far-ahead drops is not reset by a
  believable tick;
- the pins for v1.6 rules that no test covered (R7-DT-1, R7-DT-2, R7-DT-3):
  `test_a_host_clock_step_mid_session_does_not_cut_a_bar` (now drives the
  production bar clock, host ahead and behind),
  `test_the_live_routers_clock_does_not_step_with_the_host_clock`,
  `test_with_the_host_behind_a_first_print_after_the_first_bucket_is_blind`,
  `test_a_tick_handled_after_the_feed_went_down_is_not_a_crash`, and
  `test_when_the_vendor_cuts_the_listing_the_message_does_not_blame_the_lookback`
  (now on a vendor's rolling window). Each fails on the mutation that reverts
  its rule: the bar clock or the receive stamp back on the wall clock,
  `exchange_clock` on the wall clock, the first-print rule in host time, the
  feed-down guard removed, the vendor cutoff dropped.

Existing tests changed:
- `test_a_refused_tunnel_is_still_looked_up` pinned R7-KGW-2's wrong premise.
  It is replaced by `test_an_entry_refused_at_the_proxys_tunnel_releases_the_symbol`
  and the GTT-path control above.
- `test_a_host_clock_step_mid_session_does_not_cut_a_bar` and
  `test_when_the_vendor_cuts_the_listing_the_message_does_not_blame_the_lookback`
  were strengthened as described.
- StubKite gained `gtt_booked`: whether each failed GTT request still booked
  its GTT.

Verdict: ✔ confirmed by the independent skeptic, ◐ confirmed with part of the
claim overstated. Severity is the skeptic's rating.

### Real-money safety: orders

| Sev | Verdict | v1.6 defect | v1.7 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ◐ | After an ambiguous GTT attempt and its empty 15 s poll, a never-sent retry window kept calling `place_gtt` without reading the book again. A GTT the ambiguous request booked during that outage was not adopted, and a second whole-position GTT was armed. The duplicate watch then reported it. v1.6's per-outage window widened this path. Overstated: a booking later than the window already duplicates without any outage, and the watch reports it. | After an ambiguous attempt, every retry follows a successful read of the book. A match is adopted through the same code as the poll, so the "earlier run" and TRIGGERED alerts still apply. An unreadable book counts as part of the outage: nothing is placed. | `test_a_gtt_booked_during_a_later_outage_is_adopted_not_armed_again` (active and triggered), `test_after_an_ambiguous_attempt_nothing_is_placed_while_the_book_is_unreadable`, `test_an_outage_after_an_ambiguous_attempt_whose_gtt_never_lands_still_arms_one` (control) |
| low | ◐ | v1.6 kept a refused tunnel (a proxy answering CONNECT with 403/407/502/503) ambiguous, on the premise that the proxy "got the request". It got only the CONNECT line: urllib3 writes the request only through an open tunnel. Behind such a proxy, a provably unsent entry blocked the symbol for the session. Overstated: the effect was conservative, and it matched the documentation. | For the entry, a refused tunnel is never sent, and the symbol is released (`_tunnel_refused`). The GTT path keeps it ambiguous on purpose, because attempts plus book polls cover a longer proxy outage than one never-sent window. | `test_an_entry_refused_at_the_proxys_tunnel_releases_the_symbol`, `test_a_gtt_refused_at_the_proxys_tunnel_stays_on_the_ambiguous_path` (control) |

### Live feed

| Sev | Verdict | v1.6 defect | v1.7 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The 30 s rule dropped ticks one at a time. KiteTicker's whole-second stamps meant a host 30–31.5 s behind lost a random share of the prints, and the bar built from the rest was kept, evaluated and traded; the dropped shares landed in the next bar. The skeptic added that with a host far behind, one late print that slipped through was credited with the whole day's counter (a fake 70x RVOL). | A drop blinds the symbol, as a stall does. The forming bar is discarded (and back-filled), and the counter is re-baselined, even when nothing had been accepted that day. A lone corrupt stamp now costs one bar. | `test_a_print_dropped_as_far_ahead_blinds_its_bar_and_the_counter`, `test_a_late_print_that_slips_past_the_rule_is_not_credited_with_the_day` |
| medium | ◐ | `steady_clock` ran on `CLOCK_MONOTONIC`, which stops while the host is suspended. After a laptop sleep of more than 30 s, every tick was dropped for the rest of the run and the watchdog, judging on the same stalled clock, never tripped. The skeptic added that the bar interrupted by the sleep could be kept, and its signal judged as fresh. Overstated: a socket left half-open by the sleep does trip the watchdog. | On Linux the clock counts suspended time (`CLOCK_BOOTTIME`). Everywhere, a run in which every stamped tick has been dropped as far ahead for 15 s stops (exit 1) instead of running blind. | `test_the_steady_clock_keeps_counting_through_a_suspend`, `test_a_run_whose_every_tick_is_far_ahead_stops_instead_of_running_blind`, `test_one_corrupt_stamp_does_not_stop_the_run` (control) |
| low | ✔ | A zeroed-stamp print was bucketed by the raw host clock, outside feed time and the 30 s rule. A fast host plus one zeroed print closed a bar up to the offset early and traded it (predates v1.6). With a host far behind, zeroed prints still built bars. | The fallback is in feed time (the host clock minus the feed's lag), which errs early and so never cuts a bar. The far-behind case is covered by the two fixes above. | `test_a_zeroed_stamp_is_bucketed_in_feed_time` |

### Lifecycle

| Sev | Verdict | v1.6 defect | v1.7 fix | Pinned by |
| --- | --- | --- | --- | --- |
| high | ✔ | An engine that started after the host had re-registered or removed a stop signal joined the existing route without checking that it still received the signal. The README advised re-registering for uvloop. A supervisor's stop then went to the host's callback (`loop.stop` cut the entry off) or killed the process mid-entry. v1.5 re-hooked on every `main()` and did not have this. | A route the host changed is taken back when the next engine hooks it: `_owned` checks the process-level handler and, where readable, the loop table. What the host set is recorded as what to hand back. | `test_an_engine_that_starts_after_the_host_took_the_signal_back_owns_it` (re-register and remove, on asyncio and uvloop) |
| low | ✔ | A plain handler or `SIG_IGN` the host set with `signal.signal` during a run was replaced by the default afterwards. A host that ignored SIGHUP was then killed by it. On uvloop, a callback the host registered mid-run was removed without the warning. | On hand-back, the process-level handler is judged too: a plain handler or `SIG_IGN` set mid-run is kept, and a uvloop callback registered mid-run is left in place. The uvloop warning says which applied. | `test_a_plain_handler_or_ignore_the_host_sets_during_a_run_is_kept` (asyncio and uvloop), `test_on_uvloop_a_callback_the_host_registers_during_a_run_is_kept` |

### Tests and docs

| Sev | Verdict | v1.6 defect | v1.7 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | The steady-clock pins never ran the production bar clock and covered one step direction. Reverting the bar clock or the receive stamps to the wall clock left all 273 tests green. | The pin drives `bar_clock` as `main()` does, with the host ahead and behind. A router pin checks that `exchange_clock` ignores a host step. | see controls above |
| low | ◐ | The "watching since, in exchange time" rule at the first-print site and the feed-down overflow guard were each unpinned. Overstated: the mutation's error is only what traded before the connect. | Two pins. | see controls above |
| low | ✔ | v1.5's vendor-window pin went through the CSV file's own cutoff, so Yahoo's rolling-window path was untested. | The test's vendor uses `BrokerAdapter.history_cutoff`. | see controls above |
| low | ✔ | README, CHANGELOG and the docstring said one late packet cannot move the skew median; it can when the minute holds only one other sample. | Qualified in all three. | documentation |
| low | ✔ | The README's setup without uv did not say that the uvloop tests are skipped there. | Said. | documentation |
| low | ✔ | The README said five review rounds and listed six. | Corrected (now seven). | documentation |

## v1.6 (2026-09-27)

v1.5 went through a sixth adversarial review on a frozen snapshot (`68f13c7`),
with the same four areas. Every skeptic ran. All 16 findings were reproduced by
their skeptic: 11 confirmed, 5 with part of the claim overstated, none refuted,
no duplicates. None is critical or high: 2 medium, 14 low. The skeptics also
rated three of the finders' fixes as regressions, and one of their own
suggestions was declined:
- On uvloop, the finder's plain handler let a host's `loop.stop` run in the
  middle of the engine's shutdown. The engine now owns the signal exclusively.
- Two engines in one loop: the finder's hash-keyed registry crashed on
  unhashable host callbacks and leaked finished engines. The skeptic's
  hand-off kept "only the newest engine gets the signal". A process-wide router
  now reaches every engine and hands each signal back once.
- For the stale plain handler, the finder's unconditional `signal.signal`
  cleared `SA_RESTART`. It is now restored only when it is not already in place.
- For the NTP step, the finder's "shift every sample" double-shifted samples
  taken before the shift, and missed the watchdog. A live feed now runs on a
  steady clock instead.
- For the first bar of a session behind a skewed host, the finder's formula
  overflowed when the feed was down. That case is now guarded.
- For a session's lost last bar, the skeptic suggested documenting it, or a
  background fetch after the close. The next session's first bar now waits
  for the back-fill instead: evaluated across the hole, it can fire a stale
  breakout a day late (pinned below).

Every v1.6 regression test fails on the v1.5 snapshot and passes here, except
these controls:
- `test_a_host_that_stops_its_loop_on_sigterm_cannot_cut_the_shutdown_short`
  (asyncio and uvloop) and `test_handing_a_signal_back_keeps_its_sa_restart_flag`:
  properties v1.5 had, which two of the rejected fixes would have broken;
- `test_a_refused_tunnel_is_still_looked_up` and
  `test_with_a_synced_host_a_connect_before_the_open_counts_the_first_print`:
  the cases the fixes must leave alone;
- `test_an_update_that_is_not_newer_is_not_lag` and
  `test_the_live_router_judges_signal_ages_on_the_feeds_clock`: pins for v1.5
  rules that no test covered (R6-TD-5). Each fails on the mutation that removes
  its rule;
- `test_the_fast_modules_pass_without_the_kite_extra`, whose defect was in
  v1.5's `test_live.py`: it fails with that file.

Existing tests changed:
- `test_a_host_clock_behind_the_exchange_is_corrected_not_ignored` records a
  positive lag sample instead of setting `feed_lag`, since the router now uses
  the samples' median.
- `test_orchestrator_excludes_symbols_whose_history_misses_the_listing` expects
  the corrected CSV message.
- `test_process_ticks_survives_an_exception_and_keeps_counting_tasks` ends its
  history at 15:25. A session that ends at 15:20 is now a hole before the next
  session.
- `test_only_heartbeats_and_text_stamp_liveness_from_on_message` no longer
  imports `kiteconnect`.

Verdict: ✔ confirmed by the independent skeptic, ◐ confirmed with part of the
claim overstated. Severity is the skeptic's rating.

### Real-money safety: orders

| Sev | Verdict | v1.5 defect | v1.6 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The never-sent retry window was opened once for the whole GTT loop. An ambiguous attempt's 15 s book poll used it up, so a second short outage (refused, then 504, then refused) broke out at once with attempts unused. The filled position had no GTT, and the alert read `GTT STATE UNKNOWN`. | Each outage gets its own window: at most 3, one before each attempt. `settle_timeout` counts them (12 windows and 20 calls; about 6¼ minutes at the defaults), and the README's supervisor stop grace is now 420 s. | `test_a_second_outage_after_an_ambiguous_attempt_gets_its_own_window` (3 sequences), `test_the_shutdown_estimate_counts_a_never_sent_window_before_each_gtt_attempt` |
| low | ✔ | Behind an HTTP(S) proxy, no failure counted as never sent. A refused or timed-out *proxy* (requests' `ProxyError`) blocked the symbol for the session, and the GTT path treated it as ambiguous. | A `ProxyError` whose proxy could not be reached is never sent (urllib3's own test). A proxy that was reached but refused the tunnel stays ambiguous. | `test_an_unreachable_proxy_counts_as_never_sent_but_a_refused_tunnel_does_not`, `test_an_unreachable_proxy_releases_the_symbol_without_a_lookup`, `test_a_refused_tunnel_is_still_looked_up` (control) |

### Live feed

| Sev | Verdict | v1.5 defect | v1.6 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ◐ | The NTP fix v1.5's warning asks for, a step of the host clock mid-session, left the stored lag in the old frame. The bar clock could close a bar S s early, drop its tail and trade the cut bar. The skeptic added that a step of 15 s or more also tripped the feed watchdog (exit 1). Overstated: a name that trades every second discards the bar instead. | A live feed runs on a steady clock: the wall clock read at start-up, advanced by the monotonic clock. Receive stamps, liveness, bar closing, the watchdog and the router's clock all use it, so a host clock step cannot move them. | `test_a_host_clock_step_mid_session_does_not_cut_a_bar`, `test_a_host_clock_step_does_not_trip_the_feed_watchdog`, `test_a_live_feed_runs_on_a_steady_clock` |
| low | ◐ | The router's skew correction was the *largest* lag sample. One trade 8 s late cut a 20 s correction to 12 s, so tick-closed signals were refused as "not closed yet" again, and the warning misstated the skew. Overstated: never worse than v1.4. | The skew is the upper median of the last minute's samples, which one packet cannot move (`clock_skew`). Bars still wait for the largest lag. | `test_one_late_packet_does_not_move_the_router_clock` |
| low | ✔ | The zeroed-exchange-time guard tested the fallback, not the raw field, so a zeroed stamp became a 0 s lag sample and cancelled a behind-host correction for a minute. | The raw `exchange_timestamp` is judged. | `test_a_zeroed_exchange_time_is_not_a_lag_sample` |
| low | ✔ | One packet stamped 10 minutes ahead became a -600 s lag. A future-dated bar closed at once, a still-forming candle was back-filled, and the router's clock moved with it, bypassing its "not closed yet" defence. | A tick stamped more than 30 s ahead of the host clock is dropped, with a rate-limited critical log, before it moves anything. | `test_a_tick_stamped_far_in_the_future_is_dropped_before_it_moves_anything` |
| low | ✔ | With the host behind, a connect just after 09:15 was stamped before it. The day's first print counted from zero and the 09:15 bar was kept, missing its opening seconds (predates v1.5). | "Watching since" is judged in exchange time, using the skew and the tick's own lag. A feed that is down stays down (no overflow). | `test_with_the_host_behind_a_connect_after_the_open_still_blinds_the_first_bar`, `test_with_a_synced_host_a_connect_before_the_open_counts_the_first_print` (control) |

### Lifecycle

| Sev | Verdict | v1.5 defect | v1.6 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | On uvloop, the host-handler fix did nothing: uvloop does not expose its callbacks. v1.5 re-installed uvloop's dispatcher with no entry behind it, and every later SIGTERM was swallowed. | The engine owns the signal exclusively on every loop. On uvloop, a host callback cannot be read back, so afterwards the signal is back at its default, with a warning to re-register it. It is never swallowed. uvloop is now a dev dependency, so this is tested. | `test_on_uvloop_the_engine_owns_the_signal_and_never_leaves_it_swallowed`, `test_a_host_that_stops_its_loop_on_sigterm_cannot_cut_the_shutdown_short` (control) |
| low | ◐ | Restore acted on a stale snapshot. With two engines in one loop, the first to finish removed the other's handler, so `docker stop` killed it outright. The second then re-armed the first's dead handler, and SIGINT and SIGTERM were ignored for good. A handler the host replaced or removed mid-run was undone. Overstated: not a v1.5 regression, and not a documented mode. | Stop signals are routed process-wide (`_StopRoutes`). The first engine hooks a signal, every engine receives it, and the last one out hands it back. Callbacks are compared by identity, never hashed. A registration the host changed during the run is left as set. | `test_one_stop_signal_stops_every_engine_in_the_loop_in_order`, `test_a_finished_engine_never_keeps_the_signal`, `test_a_handler_the_host_replaces_during_a_run_is_kept`, `test_a_handler_the_host_removes_during_a_run_stays_removed` |
| low | ✔ | With a loop entry and a live plain handler for the same signal, only the stale loop entry came back. | Both come back. The plain handler is re-installed only if it is not already in place, which keeps `SA_RESTART`. | `test_a_live_plain_handler_beside_a_stale_loop_entry_comes_back_too`, `test_handing_a_signal_back_keeps_its_sa_restart_flag` (control) |
| low | ◐ | When both the lookback and the vendor's window cut the listing, the message named neither. Overstated: the date and the remedy were right. | Sources declare `history_cutoff()`. When both cut it, the message names both dates and the `--max-lookback-days` that reaches the source's earliest day. | `test_when_both_the_lookback_and_the_vendor_cut_the_listing_the_message_names_both` |

### Data, tests and docs

| Sev | Verdict | v1.5 defect | v1.6 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | Without `kiteconnect`, a v1.5 feed test imported it and failed, and the README said only `test_kite_gateway.py` was skipped. The guard only collected tests, so it could not see an import inside a test body. | The test stubs `kiteconnect` in `sys.modules`. The guard also *runs* `test_live.py`, `test_execution.py` and `test_alpha.py` without the extra. The README names the two skipped Kite history tests. | `test_the_fast_modules_pass_without_the_kite_extra` (control, see above) |
| low | ✔ | For a CSV (the README's own SWIGGY 2024-11-13 example), the exclusion log blamed the lookback and prescribed `--max-lookback-days`, which only led to a second exclusion. | A CSV source's history starts at its file's first day (`history_cutoff`), so the message says where the data starts and offers `--allow-partial-history` only. | `test_orchestrator_excludes_symbols_whose_history_misses_the_listing` (corrected) |
| low | ✔ | The README said both GTT-book windows keep polling past a failed last read. The duplicate watch alerts instead, and its alert was not documented. | README corrected, and the watch's alert documented. | documentation |
| low | ◐ | A bar discarded at a session's tail (a join, reconnect or stall near the close) was never back-filled in the run, although the README said the back-fill restores it. While fixing it, a second effect turned up: the next 09:15 bar was evaluated across the hole, and a breakout in the lost 15:25 bar fired again at the next open, a day late. Overstated: v1.4 also lost the bar after a late join. | A session's first bar also checks the previous session's tail and back-fills it before being evaluated, like any other hole. This costs one fetch, and only when the previous session lost bars. The offline demo, whose fixture lacks every session's 15:20 and 15:25 bars, logs that gap and does not evaluate its first tape bar. | `test_a_sessions_lost_last_bar_is_backfilled_before_the_next_session_trades`, `test_a_breakout_in_a_lost_last_bar_does_not_fire_again_at_the_next_open` |
| low | ✔ | Two rules v1.5 listed as pinned were pinned by no test: main() giving the router the feed's clock, and "only newer updates are lag". Both mutations survived the suite. | Pinned. | `test_the_live_router_judges_signal_ages_on_the_feeds_clock`, `test_an_update_that_is_not_newer_is_not_lag` (controls, see above) |

## v1.5 (2026-09-27)

v1.4 went through a fifth adversarial review on a frozen snapshot (`accab1d`),
with the same four areas. Every skeptic ran. All 15 findings were reproduced by
their skeptic: 14 confirmed, 1 with part of the claim overstated, none
refuted. Two pairs are duplicates, which leaves the 13 defects below: no
critical or high findings, 3 medium, 10 low. Where a skeptic showed that the
finder's fix would regress something (never-sent GTTs, the snapshot's timing,
negative lag), the skeptic's refined fix was used.

Every v1.5 regression test fails on the v1.4 snapshot and passes here, except
these controls:
- the pre-existing `0`, `-5`, `1.5` and `nan` cases of
  `test_the_lookback_must_be_a_positive_whole_number_of_days`;
- `test_the_print_after_a_glitch_keeps_its_trade_and_price`, whose setup moved
  its first print out of the bar it checks;
- `test_the_suite_collects_without_the_kite_extra`, whose defect was in v1.4's
  test-file layout: it fails with that layout.

Verdict: ✔ confirmed by the independent skeptic, ◐ confirmed with part of the
claim overstated. Severity is the skeptic's rating.

### Real-money safety: orders

| Sev | Verdict | v1.4 defect | v1.5 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | The GTT-book poll and the duplicate watch counted the book as read if *any* read had succeeded. If only the first read got through, a GTT booked just after it was never seen, and a second whole-position GTT was armed, or a late duplicate went unreported. | Both windows judge on their latest read. After the deadline, the poll continues (for at most one more window) until a read succeeds, since a booked GTT stays in the book. The watch alerts whenever its final read failed. | `test_a_gtt_booked_while_the_book_is_unreadable_is_still_adopted`, `test_a_duplicate_watch_whose_last_read_failed_says_so` |
| low | ✔ | A duplicate that had already **triggered** produced "DELETE ALL BUT GTT n", which means: keep the new GTT, over shares the fired one is already selling. | Then the alert says the exit has fired, and to delete the new GTT and check orders and holdings. | `test_a_duplicate_that_already_triggered_is_reported_as_a_fired_exit` |
| low | ◐ | Only a connect timeout counted as never sent. A refused or unreachable connection, or a DNS failure (a plain `ConnectionError` whose reason is urllib3's `NewConnectionError`), was treated as a lost reply: the entry's symbol was blocked, and the GTT path gave up with "a GTT may exist". Overstated: the finder's fix, three quick retries, would have covered less of a short outage than today's 15 s window. | These errors count as never sent. The entry releases the symbol. The GTT is retried through the outage for up to `cancel_grace` without using up an attempt, and then "POSITION OPEN WITHOUT EXITS", not "state unknown". | `test_a_refused_connection_releases_the_symbol_without_a_lookup`, `test_a_gtt_is_retried_through_a_short_outage_that_refuses_connections`, `test_a_refused_gtt_request_that_outlasts_the_window_says_no_gtt_exists` |
| low | ✔ | The 3-try GTT-book snapshot ran after the fill, so a slow book delayed the first GTT request by up to 24 s. | The snapshot is taken at the start of `execute()`, before the LTP check, so the LTP check and `place_order` stay back to back. No sleep follows the last try. | `test_kite_entry_then_gtt_oco_on_the_filled_quantity` (route order) |

### Live feed

| Sev | Verdict | v1.4 defect | v1.5 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | A re-baselining print (after a stall, a reconnect or a late join) swallowed whatever had traded earlier in its bucket. That bar was kept, with the wrong open and too little volume. A short bar is **not** fail-safe: it lowers the 20-bar RVOL baseline and can fake a later breakout, which the finder reproduced. | The bucket of a re-baselining print is blind. Its bar is discarded, and the next complete bar's back-fill restores the exchange's candle. | `test_a_stall_across_a_bucket_end_does_not_credit_its_tail_twice` (rewritten), `test_joining_mid_session_does_not_dump_the_days_volume_into_one_bar`, `test_a_late_feed_connect_does_not_dump_the_days_volume_into_one_bar` (both corrected) |
| low | ✔ | Lag was measured only on trades, so a latency rise shown only by depth updates left a stale lag, and the bar closed before its tail arrived. KiteTicker also calls `on_message` before `on_ticks`, so a payload could vouch for its own tail. | Newer exchange updates after a trade in the current connection are sampled too. A tick payload proves liveness only after its ticks are queued, and `on_message` stamps only heartbeats and text. A lag rise during heartbeat-only silence stays undetectable, since heartbeats carry no timestamp (README, Known limitations). | `test_a_lag_rise_seen_on_depth_updates_holds_the_bar_open`, `test_a_tick_payload_proves_liveness_only_after_its_ticks_are_queued`, `test_only_heartbeats_and_text_stamp_liveness_from_on_message` |
| low | ✔ | A host clock *behind* the exchange showed up as negative lag, which was clamped to 0 without a word. Bars then closed late, and the router misjudged signal ages by the skew: tick-closed signals were refused as "not closed yet", and old clock-closed ones were accepted. This predates v1.4. | The lag is kept signed, and a warning names the skew. Bars close in feed time in both directions. The live router's clock adds back a host lag, but not real latency, which does age a signal. | `test_a_host_clock_behind_the_exchange_is_corrected_not_ignored` |

### Lifecycle and CLI

| Sev | Verdict | v1.4 defect | v1.5 fix | Pinned by |
| --- | --- | --- | --- | --- |
| medium | ✔ | A host that had registered its own `loop.add_signal_handler` callbacks lost them to `main()`, and afterwards SIGINT and SIGTERM were swallowed, leaving only SIGKILL. | The host's loop callbacks are re-registered after the engine's are removed. | `test_a_hosts_own_loop_signal_handlers_survive_the_engine` |
| low | ✔ | `run()` called from a worker thread raised `ValueError` after a complete run, discarding the exit code. | Restoring handlers outside the main thread is skipped. | `test_run_from_a_worker_thread_returns_the_exit_code` |
| low | ✔ | A huge `--max-lookback-days` passed validation and then crashed with an `OverflowError` traceback (exit 1). | The flag takes 1 to 36500 days (exit 2 otherwise), and the orchestrator caps the lookback at 36500 days for library callers. | `test_the_lookback_must_be_a_positive_whole_number_of_days` (2 new cases), `test_a_huge_lookback_is_capped_not_a_crash` |
| low | ✔ | With Yahoo, the exclusion message blamed the 180-day lookback and prescribed `--max-lookback-days`, which cannot get past Yahoo's own ~60 days. | An adapter declares its own history limit (`history_limit_days`), and when the vendor cut the listing, the message says the history starts later and offers only `--allow-partial-history`. | `test_when_the_vendor_cuts_the_listing_the_message_does_not_blame_the_lookback` |
| low | ✔ | `--max-lookback-days` was ignored without `--listing-date`: demos always fetched 20 days. | Demos fetch at most `min(20, N)` days. | `test_a_demo_honours_a_shorter_lookback` |

### Tests

| Sev | Verdict | v1.4 defect | v1.5 fix | Pinned by |
| --- | --- | --- | --- | --- |
| low | ✔ | Without `kiteconnect`, *all* of `test_execution.py` was skipped, including the sizing, paper-OCO and halt-report tests. The collection test accepted "nothing collected". | The Kite gateway tests moved to `test_kite_gateway.py`, and `test_execution.py` has no Kite dependency. The collection test collects the whole suite without the extra and asserts that the non-Kite tests are still there. | `test_the_suite_collects_without_the_kite_extra` |

### Found by the v1.4 test matrix

One run on Python 3.10 / pandas 2.2 failed
`test_limiter_serves_waiters_in_arrival_order`. That was not noise: under CPU
load the limiter served waiters out of order in 1–2 of 300 runs, on both
Python lines.

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
