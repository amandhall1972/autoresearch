# IPO Momentum Engine

A single-file, asyncio trading engine for **NSE IPO base breakouts**. It syncs
5-minute history, turns live ticks into bars, fires when a recent listing
closes above its post-IPO base on heavy volume, and routes risk-sized orders to a
paper broker or to Zerodha Kite.

```
python engine.py --source csv      # fully offline, on real SWIGGY bars: history -> ticks -> signal -> order
```

> **Paper execution is the default.** Real orders need
> `--source kite --listing-date YYYY-MM-DD --live-feed --live-orders --expect-ip <static IP>`
> and Kite credentials. Nothing here is investment advice. The one real breakout in the
> bundled data **failed** (see [Validation](#validation-on-real-data)).

---

## Contents

1. [Quick start](#quick-start)
2. [What happened when the original file was run](#what-happened-when-the-original-file-was-run)
3. [Run modes and safety rules](#run-modes-and-safety-rules)
4. [Architecture](#architecture)
5. [Strategy specification](#strategy-specification)
6. [Live bar synthesis](#live-bar-synthesis)
7. [Execution and risk](#execution-and-risk)
8. [CLI reference](#cli-reference)
9. [Validation on real data](#validation-on-real-data)
10. [How it was reviewed](#how-it-was-reviewed)
11. [Known limitations](#known-limitations)
12. [Tests](#tests)

---

## Quick start

```bash
cd ipo_momentum
uv sync --extra dev                 # Python >= 3.10; pandas, numpy, kiteconnect, pytest, uvloop (pinned in uv.lock)
uv run python engine.py --source csv
uv run pytest                       # 468 tests, ~5 min, fully offline
```

Without uv: `pip install pandas numpy` (add `kiteconnect` for Zerodha and
`pytest` for the tests; without `kiteconnect`, `test_kite_gateway.py` and the
two Kite adapter tests in `test_data.py` are skipped; without `uvloop`
(`pip install uvloop`, not on Windows), so are the uvloop cases of the signal
tests in `test_end_to_end.py`; the rest pass), then `python engine.py --source csv`.

| Mode | Command |
| --- | --- |
| Offline demo on real bars | `python engine.py --source csv` |
| Original demo (Yahoo history) | `python engine.py` (needs HTTPS to `query1.finance.yahoo.com`) |
| Zerodha history + simulated tape | `python engine.py --source kite --listing-date YYYY-MM-DD` |
| Zerodha live ticks, paper orders | `python engine.py --source kite --listing-date YYYY-MM-DD --live-feed --run-seconds 0` |
| Zerodha live ticks, **real orders** | `python engine.py --source kite --listing-date YYYY-MM-DD --live-feed --live-orders --expect-ip <static IP> --run-seconds 0` |

Kite modes need `KITE_API_KEY` and `KITE_ACCESS_TOKEN` in the environment.
Real orders also need a funded account.

**Exit codes**

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | No usable history, a failed IP check, a crashed worker, a dead or silent websocket, a router that could not settle in time, or a live order that could not be settled or protected (the run lists each under `ATTENTION`) |
| `2` | Invalid configuration, including malformed or out-of-range arguments |
| `128 + N` | Stopped by signal N after the normal shutdown and halt report: `130` Ctrl-C (SIGINT), `143` `kill`/`systemctl stop`/`docker stop` (SIGTERM), `129` a closed terminal (SIGHUP) |

A failure outranks a signal: a shutdown that a signal started but that then
fails still exits `1`, so a supervisor keyed on `1` never misses one.

---

## What happened when the original file was run

The file was first committed exactly as provided (commit `6097028`) and run
in two ways, unmodified.

**Run 1: as given.** The sandbox's egress policy blocks
`query1.finance.yahoo.com` (HTTP 403 at the proxy), so the Yahoo fetch failed.
The engine logged nothing about the failure and **exited 0**:

```
17:54:16.367 | INFO     | [QUANT_ENGINE] | === INITIALIZING INSTITUTIONAL ENGINE ===
17:54:16.367 | INFO     | [QUANT_ENGINE] | [SWIGGY] Fetching public exchange prints via direct HTTP...
17:54:20.209 | CRITICAL | [QUANT_ENGINE] | Failed to build historical context. Aborting.
```

**Run 2: same code, real data.** Only the Yahoo adapter was swapped for one
serving 944 real SWIGGY 5-minute bars ([data/README.md](data/README.md)). The
pipeline ran, but the advertised breakout could never trade. The 850,000-share
"institutional sweep" tick only *opened* a bar. Bars closed only when a later
tick arrived, and none ever did:

```
📡 [EXCHANGE] Massive volume detected. Institutional sweep crossing 285.55...
📊 [SWIGGY] 5m Bar Closed | C: 264.85 | V: 1000          <- the only bar that ever closed
=== SYSTEM HALT ===
Final Inventory State: set()                             <- no signal, no order
```

**Run 3: the current engine, same demo, offline on the same real bars:**

```
[SWIGGY] No listing date given: treating the first bar (2026-09-08 09:15) as the listing, so the base and AVWAP are anchored there (demo semantics).
[SWIGGY] Map established. 944 concrete 5m bars acquired (2026-09-08 09:15 -> 2026-09-25 15:15 IST).
📡 [EXCHANGE] Real Historical Base High to Beat: 285.55 | Last close: 264.85
📊 [SWIGGY] 5m Bar Closed 2026-09-28 09:15 | O: 264.85 H: 265.38 L: 264.85 C: 265.38 | V: 150,000
📊 [SWIGGY] 5m Bar Closed 2026-09-28 09:20 | O: 286.12 H: 290.40 L: 286.12 C: 289.83 | V: 850,000
[SWIGGY] 🟢 ALPHA TRIGGER: Base Breakout @ 289.83 (base 285.55) | RVOL: 4.44x | AVWAP: 277.08
🚀 [OMS DISPATCH] BUY 2884x SWIGGY LIMIT 291.30 (signal 289.83) | notional ≤ ₹840,109 | risk ≤ ₹14,997
🛡️ Target: 300.85 | Stop Loss: 286.10
✅ [SWIGGY] PAPER FILL (simulated, no order sent): 2884x @ 289.83.
=== SYSTEM HALT ===
Final Inventory State: {'SWIGGY'}
   Fill SWIGGY: 2884 @ 289.83 (order PAPER-1, exits: simulated OCO)
   Open SWIGGY: 2884 @ 289.83 | stop 286.10 | target 300.85
```

The simulated tape is synthetic. Only the base high (285.55) and the last
close (264.85) come from the data. It is stamped at the session after the data
ends, and the run keeps time by the tape, so the demo behaves the same on any
date.

---

## Run modes and safety rules

| Rule | Why |
| --- | --- |
| `--live-orders` requires `--live-feed`, `--source kite` and `--expect-ip` | Without a live feed, only the synthetic tape could trigger an order |
| `--live-feed` turns the synthetic tape off | Invented ticks must never mix with real ones |
| The router refuses signals whose bar has not closed yet (by the router's clock) | Defence in depth against synthetic or corrupt timestamps |
| `--source kite` requires `--listing-date` | The IPO base and AVWAP are anchored at the listing |
| History must start in the listing session itself | Otherwise the "IPO base" comes from later sessions (e.g. after Yahoo's ~60-day limit). `--allow-partial-history` overrides this. |
| Simulated runs keep time by the tape; live runs by the wall clock | Staleness checks and bar closing stay correct in both |
| History is fetched from at most `--max-lookback-days` (default 180, every source; demos at most 20) back, and Yahoo serves only ~60 days; both windows start at 00:00 IST | A window that starts mid-session would silently drop the listing morning from the base. An older listing is excluded, and the log says whether the lookback or the vendor's own window cut it. |
| A live feed silent for 15 s during the session stops the run (exit 1) | KiteTicker never pings, so a half-open socket would otherwise look connected forever |
| SIGINT, SIGTERM and SIGHUP all take the orderly shutdown, which cancels a working entry's remainder at once | `kill`, `docker stop` or a dropped SSH session must not abandon a working order |
| A signal the parent ignored stays ignored | `nohup python engine.py …` must survive a dropped SSH session |

**Supervisors.** A normal shutdown with a live entry in flight takes a few
seconds: the fill wait ends, the remainder is cancelled, and the GTT is armed.
With a degraded broker API it can take minutes, bounded by the gateway's
deadlines (at most about 6¼ minutes at the defaults). Give the supervisor's
stop grace room before it sends SIGKILL: `docker stop -t 420` or compose
`stop_grace_period: 420s`, systemd `TimeoutStopSec=420`, Kubernetes
`terminationGracePeriodSeconds: 420`. Docker's default is only 10 s.

Without `--listing-date` (Yahoo and CSV demos only), the first bar of the last
20 days is treated as the listing, as in v1.0's demo, and the run says so. SWIGGY
actually listed on 2024-11-13. With that date and the bundled data, the engine
refuses to trade.

---

## Architecture

```
 ┌───────────── 1. Data harmonization ─────────────┐
 │ PublicExchangeAdapter (Yahoo)                   │   harmonize_bars(): bars exist only if they traded,
 │ ZerodhaKiteAdapter    (Kite REST)  ──────────► │   IST index, float64, no future back-fill,
 │ CsvReplayAdapter      (offline)                 │   forming and off-grid bars dropped
 └──────────────── ProductionOrchestrator ─────────┘   history must start in the listing session (fail closed)
                           │ market_state{symbol: DataFrame}
 broker websocket thread   ▼
 KiteTicker ──on_ticks──► LiveTickAdapter.broker_on_ticks ──call_soon_threadsafe──► tick_queue
      ├─on_connect──► mark_feed_reset (re-baseline volume)   on_noreconnect ──► supervised stop
      ├─on_close────► mark_feed_down (forming bars incomplete)
      └─on_message──► note_feed_alive (heartbeats too) ◄── _watch_feed: silent 15 s in session ─► stop
                           │ event loop
                           ▼
          process_ticks ─► on_tick ─► close_bar ◄── bar_clock (closes bars with no later tick)
                                        │  hole? → back-fill from broker history, then evaluate
                                        │  notifies bar listeners (paper OCO)
                                        ▼
                          AlphaEngine.evaluate (causal, vectorized)
                                        │ Signal
                                        ▼ oms_queue
          ExecutionRouter.process_orders ─► plan (tick-rounded, risk- and notional-capped)
                                        ▼
                     PaperGateway   |   KiteOrderGateway (LIMIT entry → settle → GTT OCO, one shielded task)
```

The tick aggregator, bar clock, OMS router and (live) websocket watch all run
under supervision, and a worker that stops ends the run with exit code 1. A
failure while handling one tick, bar or order is logged and skipped; it never
kills a loop.

**Shutdown** follows the same order whether the run times out, receives
SIGINT, SIGTERM or SIGHUP, or loses a worker:
1. Stop everything that produces ticks or signals.
2. Stop accepting new signals.
3. Let the order in flight settle. The gateway stops waiting for fills,
   cancels what is still working and protects what filled. A live entry runs as
   one shielded task: even if the router has to be interrupted after its settle
   budget, the entry finishes on its own deadlines and its outcome is reported.
4. Print the halt report. A fill that completed during shutdown is listed as an
   open position.

Signals stay owned by the engine, with repeats logged and ignored, until the
process has finished exiting. Signals belong to the process, so each is taken
once, when the first engine on a loop starts, and handed back once, when the
last one ends:
- Every engine running in the loop (say, one `main()` per IPO) takes the same
  stop signal in order; none is left to be killed by the supervisor.
- While an engine runs it owns the signal outright: a host's own handler (say,
  `loop.stop`) is displaced, not chained, so it cannot cut the orderly
  shutdown short.
- On asyncio's own loops, a host that embeds `main()` gets back exactly what it
  had, including callbacks registered with `loop.add_signal_handler` and a
  plain handler set beside one. A plain handler the engine displaced comes back
  as the same handler, re-installed with `signal.signal`, so without
  `SA_RESTART` if the host had set it with `signal.siginterrupt(sig, False)`
  (see Known limitations).
- Whatever the host sets while engines run (a loop callback, a plain handler,
  `SIG_IGN`) is what is handed back. On asyncio a plain handler set mid-run
  keeps its own flags, unless an engine started after the host set it: that
  engine displaced it. An engine that starts after such a change takes the
  signal back for its own run; what the host had before the change is kept for
  the hand-back.
- A signal the host sets to `SIG_IGN` while engines run (a common guard around
  spawning workers) stays ignored, as under `nohup`: an engine that starts
  inside the guard joins the running ones. Once the host lifts the ignore,
  whatever it restores, the engines take the signal back at their next check,
  every 50 ms (`RECLAIM_POLL`) while the event loop is free, if it no longer
  reaches them, for instance because the host
  had re-registered or removed its own callback before the guard. An engine that
  starts under `SIG_IGN` while no engine runs stays unhooked, as under `nohup`.
- If the last engine ends inside such a guard, the signal is handed back
  ignored, with a warning: the handler the host saved when it set `SIG_IGN` was
  the engine's, and restoring it would swallow every later stop (and Ctrl-C).
  Restore what was there before the engine started; the warning names it.
- An engine held by `main(hold_signals=True)` keeps the signal after it returns,
  until the host calls `engine.release_signals()` (engines still running keep
  it; the last one out hands back as usual) or takes the signal back with
  `signal.signal` or `loop.add_signal_handler` (the next engine then drops the
  held one). Restoring a handler saved from `signal.getsignal()` is not enough on
  asyncio, whose process-level handler is one shared function for every callback.
  Call it from the main thread, which alone can hand signals back (elsewhere it
  raises `ValueError` before changing anything); a host that drives its loop
  with `run_until_complete` can call `engine.release_signals(loop)` between runs.
  Release before the loop closes: on asyncio the held entry stays in the loop's
  table until then, so closing the loop (as `asyncio.run` does) resets the
  signal to its default, even after a take-back with `signal.signal`. A held
  run whose loop closed without a release is reported (a warning) when the
  next engine or `run()` starts, and a dispatcher of the closed loop still in
  place (uvloop's, one the host re-registered on it, or the plain one) goes back
  to what the host had before the run: it would swallow every stop. An engine
  in a worker thread cannot change a signal, so it leaves that to the next
  engine on the main thread.
- Routes belong to the process that took them. A process forked while engines
  run (multiprocessing's `fork`, or `os.fork`) starts, from the moment it is
  forked, with what the host had before any engine ran, not the parent
  engines' dispatcher: a stop sent to the worker stops the worker (its own
  engine, once that has hooked), never the parent's engines. Anything that ran
  through a loop (a loop callback, or `asyncio.run`'s own SIGINT handler on
  3.11+) cannot run in the child, so there it is the default (Ctrl-C raises
  `KeyboardInterrupt`); a host's plain handler comes back as itself. A signal
  the host set itself during the run is left as it set it, such as a `SIG_IGN`
  guard around the fork or a plain handler, except a loop callback (on asyncio
  too, where it shares the engines' dispatcher), which goes to the default. In
  every forked process, engines or not, the parent loop's wakeup fd is cleared,
  so a stop a worker handles in Python never reaches the parent's loop (a
  worker forked before any engine hooked included). In the child, lift a guard
  with `SIG_DFL` (`signal.default_int_handler` for SIGINT) or with a plain
  handler the host had before its engines started, never with anything that
  ran through a loop, nor with the handler saved when the guard was set: that
  is the parent engines' dispatcher (on uvloop, after a host callback
  registered mid-run, that callback's dispatcher of the parent's loop), which
  in the child swallows every stop until the child's own engine hooks (whose
  hand-back, through `main()` or `run()`, then restores what the host had: the
  default, where that was a loop callback).
- uvloop's callbacks cannot be read back. A host callback it held for a stop
  signal before the run is therefore lost: the engine logs a warning, and the
  signal is back at its default (never silently swallowed). Re-register it
  after `main()` returns; with several `main()` calls in one loop, after the
  last one, since a callback registered while another engine runs displaces
  that engine until the next one starts.
- Where the loop cannot own signals (Windows), a plain signal handler hands them
  to the loop thread-safely and the previous one is restored afterwards.

---

## Strategy specification

All indicators are **causal**. The value on bar *t* uses bars ≤ *t* only. The
test suite checks this by walking the real data bar by bar
(`test_walk_forward_equals_vectorized_scan_on_real_data`).

| Quantity | Definition |
| --- | --- |
| IPO base | The first `base_bars = 150` bars since listing (v1.0's definition), or with `--base-sessions N` every bar of the first N sessions. 150 bars is two full sessions. On a real listing day continuous trading starts at 10:00 (66 bars), so it reaches into session 3's first nine bars. |
| Base high | `max(High)` over the base. Undefined on base rows, and no breakout until the base is complete. |
| AVWAP | VWAP anchored at the listing: `cumsum(TP·V) / cumsum(V)`, where `TP = (H+L+C)/3`. Undefined until volume has traded. |
| RVOL (`trailing`, default) | `V_t / mean(V over the previous 20 bars)`. The current bar is excluded, and so are zero-volume bars (vendor gaps or no-trade buckets). At least 15 valid bars are required. |
| RVOL (`time_of_day`) | `V_t / mean(V of the same 5-minute slot over the previous 10 sessions)`. Falls back to trailing until the slot has history. |
| ATR | 14-bar mean of **true range** `max(H−L, \|H−C₋₁\|, \|L−C₋₁\|)`, so gaps count. |
| Breakout | A cross: `C₋₁ ≤ base < C` **and** `C > AVWAP` **and** `RVOL > 2.0`. A close back into the base followed by a new cross counts again. |
| Stop | `max(C − 1.5·ATR, AVWAP)`: "risk bounded by 1.5 ATR, or the AVWAP floor, whichever is closer" |
| Target | `C + 3 × (C − stop)` |

**Why `time_of_day` exists.** On the bundled data, 75% of 09:15 bars have
trailing RVOL > 2, against 11% of other bars, so at the open the volume filter
barely filters. With `--rvol-mode time_of_day` the 09:15 rate drops to 17%.
Trailing stays the default because it is the original strategy's definition.

---

## Live bar synthesis

* **Symbols.** Kite ticks carry an integer `instrument_token`, which is mapped
  to the tradingsymbol with the instrument dump loaded at boot. Unknown tokens
  are dropped and counted. So are ticks for a symbol whose history did not
  load: without history there is no IPO base.
* **Session.** Only prints inside the continuous session (09:15–15:30 IST)
  make bars. Pre-open auction prints are dropped before the volume counter
  moves, so the auction volume lands in the 09:15 bar, as in broker candles.
* **Time.** A tick belongs to the bar that contains its `exchange_timestamp`,
  falling back to a `timestamp` field, then to receive time. `last_trade_time`
  is deliberately not used, because for a quiet name it can be minutes old. A
  zeroed exchange time, which the SDK parses as 1970, also falls back to
  receive time (and is not a lag sample). KiteTicker's naive host-local
  datetimes are converted to IST. A zeroed stamp's receive time is taken in
  feed time, corrected by the feed's lag (the largest of the last minute) as
  measured on every print received before it. Feed time errs early: by a
  stamp's truncation (up to a second, since stamps are whole seconds), plus
  however much that largest lag exceeds this print's own latency (one late
  packet raises it until a lag sample is taken more than a minute later, to
  several seconds). The print's latest possible time is its receive time plus a
  second minus the lowest lag sample of that window, and never earlier than its
  receive time: each sample is latency plus a stamp's truncation minus the
  host's offset, so this bound holds on a host running behind too, as long as
  the print's latency is at least the window's lowest (a faster print on such a
  host can be filed a bucket early: see Known limitations). It is never earlier
  than a stamp's truncation past feed time. So:
  - Within the bar clock's 2 s grace after the end of a bar that is still open,
    the print joins that bar: it cannot close a bar before the bar clock would,
    even when latency has just risen.
  - Received after the print that opened the current bar, it joins that bar.
  - A print that joins an open bar but may be the next bucket's (its feed time
    or latest possible time is past the bar's end) is in the wrong bar if it
    is the next bucket's: that bar would hold its shares and price, and the bar
    after it would be short. Both are discarded (logged) and the strict
    back-fill restores the exchange's two bars before the next bar is
    evaluated, so neither a short bar in later RVOL baselines nor the print's
    price in a first-crossing test can fake a breakout. A stamped print of the
    bar's own bucket received after it clears this, since in-order delivery
    puts the unstamped print before it. The session's last bar is exempt: no
    bucket follows it.
  - With no bar open, it is filed in the latest bucket it can belong to (its
    latest possible time's), not its feed time's, unless that bucket is past
    the close. Filed early, the bar it opened would be evaluated while the
    back-fill of the bucket it really traded in counted its shares again (a
    fake breakout); after the bar before closed (a stamped update of the next
    bucket closes it), it would be dropped as late, with the same result.
  - Received in session, it is never dropped as pre-open; a counter it
    re-baselines blinds every bucket it can belong to, from its feed time's to
    its latest possible time's.
  - A bar it opens may hold the bucket before's shares, at any distance from
    the boundary (latency can rise unmeasured). Unless a stamped update of its
    own bucket came first (and no later than the print could have traded), the
    bar is not evaluated if the broker's back-fill says the bucket before it
    traded, since its shares may be counted twice: filed late, the worst case
    is a missed signal. (The proof checks the stamp against the median-guarded
    receipt time, or the receipt time itself while the window holds a single
    lag sample, so a stamp up to 30 s ahead of the print's receipt cannot prove
    a bucket.) With no bar open and within 2 s after the boundary,
    the bar is also provisional: a later stamped print of the bucket before
    moves it there.
* **The 30 s rule.** A tick stamped more than 30 s ahead of the host clock is
  dropped with a critical log before it moves the lag or the market time: the
  exchange cannot stamp a print in the future, so either the stamp is corrupt
  or the host clock is too far behind to trust. Like a stall, the drop blinds
  the symbol: the bar the print belonged to is discarded (and back-filled) and
  the counter re-baselined, so no bar is built from the prints that got
  through. A lone corrupt stamp therefore costs its bar, and the next one too
  when the next accepted print falls in the next bucket; one received long
  before the open costs nothing. The run stops (exit 1), because its clock
  cannot be trusted, if every stamped tick is dropped this way for 15 s, or if
  the host runs within 1.5 s of the limit while prints keep landing just over
  it (whole-second stamps straddle the limit there).
* **Host clock.** A live feed keeps its own clock: the wall clock as read at
  start-up, advanced by a monotonic clock that on Linux keeps counting through
  a suspend (`CLOCK_BOOTTIME`). An NTP step mid-session therefore cannot shift
  bar closing, liveness, the watchdog or signal ages; the start-up offset is
  measured like any other clock skew (below).
* **Volume.** Full- and quote-mode ticks report cumulative day volume
  (`volume_traded`), and a bar receives the *difference* between prints.
  - A print only opens or extends a bar if something verifiably traded. Depth
    updates, and prints that only re-set the counter's baseline, make no bars.
  - Whenever the feed was not watching (start-up, a late connect, any
    reconnect, a stall), the counter holds trades the engine never saw. It is
    re-baselined, and every bar the blind spot touched is discarded rather than
    credited with an outage's volume. That includes the bar of the bucket in
    which the counter is re-baselined, whose first trades went into the new
    baseline: a short bar would lower the RVOL baseline and fake later breakouts.
    The back-fill restores the exchange's candles (see Holes, below).
  - The day's counter never legitimately goes down, so its highest value today
    is a high-water mark. A print below it (a zeroed or stale packet) is a
    glitch. It is never adopted as the baseline, in any feed epoch, however many
    arrive in a row, and the next good print is credited normally.
  - A zero counter after a blind spot is no baseline either (the next print
    re-baselines again), unless the name has provably not traded today: the
    packet's own `last_trade_time` is from an earlier day and no bar of today is
    known (forming, closed or in the synced history). Then the zero hid nothing,
    so it is the baseline and its re-baseline blinds no bucket: an illiquid
    name's first traded bar is kept whole, and a breakout on it is evaluated,
    when that bar's bucket began after the join or reconnect. A first trade in
    the join's own bucket still loses that bar to the back-fill (the bucket began
    before the feed was watching).
  - The first print of a day counts from zero only if the feed was up at 09:15,
    judged in exchange time.
  - LTP-mode ticks carry no volume and make no bars; the feed uses full mode.
* **Feed liveness (live feed only).** Every websocket message is proof of life,
  including the 1-byte heartbeat Kite sends when there is nothing else to send.
  - A websocket close marks every forming bar incomplete at once. Nothing
    opened before the reconnect is kept.
  - A bar closed by the bar clock is kept only if the feed delivered something
    after its bucket ended. Otherwise its tail may be missing, so it is
    discarded, the hole is back-filled, and the volume counter is re-baselined
    so the stalled tail is not credited to the next bar as well.
  - Both checks run in *feed* time. The feed's lag behind the host clock is
    measured from exchange timestamps on trades, and on newer updates once the
    symbol has traded in this connection (a subscribe snapshot's old timestamp
    does not count). Bars wait for the largest lag in the last minute, so
    network latency or a fast host clock cannot close a bar before its last
    prints arrive, and a host clock running *behind* the exchange (negative
    lag) is corrected too, with a warning.
  - The router's signal ages use a separate estimate of how far the host runs
    behind: the upper median lag of the last minute, which one late packet
    cannot move once the minute holds at least two other samples (with fewer,
    as for a name that trades or updates less than twice a minute, the
    correction follows it). Real latency is not removed: a late signal really
    is older.
  - A tick payload proves liveness only after its ticks are queued, and the bar
    clock never closes a bar while ticks already received are still queued.
  - KiteTicker's ping loop never actually sends a ping, so a half-open socket
    stays "connected" and silent. A watchdog stops the run (exit 1) after 15 s
    without any message during the session.
* **Closing.** A bar closes when a tick for a later bucket arrives, *or* when
  the bar clock sees the bucket ended more than 2 s ago.
* **Holes.** If bars are missing before a newly closed bar (a discarded blind
  spot, or a bucket with no trades), they are back-filled from the broker's
  history before that bar is evaluated. This prevents a "first crossing" from
  firing one bar late at a worse price. A session's first bar also checks the
  previous session's tail: a join, reconnect or stall near the close can cost
  it its last bars, and a breakout in the lost 15:25 bar would otherwise fire
  again at the next open. The back-fill is strict: an empty answer means
  nothing traded, and a failed fetch raises, so the bar is recorded but not
  evaluated. Without a history source, a bar after a hole is likewise not
  evaluated (the offline demo's first tape bar is one: the bundled sessions
  lack their 15:20 and 15:25 bars). Nor is a bar an unstamped print opened
  whose back-filled bucket before it traded (see Time).
* **Integrity.** Late and out-of-order prints are dropped before they can move
  the volume counter. History fetches drop the still-forming bar.

---

## Execution and risk

**Sizing.**
`qty = min(floor(risk_per_trade / (limit − stop)), floor(max_position_value / limit))`.
Risk is measured from the *worst acceptable fill*, the limit price.
- Defaults: ₹15,000 risk per trade and a ₹10,00,000 notional cap. Without the
  cap, a tight stop would size into crores.
- `risk_per_trade` is a fixed rupee amount, not a fraction of equity, and gaps
  can lose more than it.

**Prices** are placed on the tick grid in the conservative direction. The
entry limit (`signal × 1.005`) rounds up, and the stop and target round down.
Kite supplies each instrument's tick size.

**Static IP.** `--live-orders` refuses to start without `--expect-ip`.
- The check asks three IPv4-only echo services concurrently (IPv6-only ones for
  an IPv6 address). A dual-stack service would report the IPv6 address of an
  IPv4-whitelisted host.
- Any disagreeing answer aborts the run, and at least two services must
  confirm the address.
- Junk bodies (captive portals) and unreachable services are ignored.

**Guards.** The router applies these, in order:
1. Stop taking signals once shutdown begins.
2. Skip a symbol with a pending or open position, or an order in an unknown
   state.
3. Refuse a signal whose bar has not closed yet, or closed more than 60 s ago.
4. Reject degenerate geometry: NaN, a stop at or above the entry, or a target
   inside the limit.

The Kite gateway also re-checks the live LTP. It skips the entry if the price
has fallen to or below the stop, or has run above the limit.

**KiteOrderGateway.** Nothing that may exist at the broker is ever abandoned
or duplicated:
1. It places a `regular` LIMIT BUY (product `CNC`) with a unique tag.
   - If the reply is lost after the request may have been sent (a read
     timeout, a 5xx, a 429), the tag is looked up in the order book for up to
     15 s, because the broker can book an order moments later.
   - A request that never left the machine (a connect timeout, a refused or
     unreachable connection, a DNS failure, a proxy that could not be reached)
     or a broker refusal (any 4xx other than 429, e.g. insufficient margin)
     releases the symbol at once. So does a proxy that refused the tunnel (a CONNECT
     answered 403, 502...): it received the CONNECT line and nothing else.
     Kite errors are classified by HTTP status, not by name: an
     `OrderException` with a 503 is looked up, not taken as a refusal.
2. It polls `order_history` until the order reaches a terminal state, retrying
   through transient API errors.
3. If the order hasn't filled after 30 s, or as soon as shutdown begins, it
   cancels the remainder and polls until the exchange confirms a terminal
   state. If the cancel cannot be confirmed, what is already known to be bought
   still gets its GTT.
4. It places a **GTT OCO** on the final filled quantity. The stop leg is a SELL
   LIMIT 2% below its trigger (GTT legs must be LIMIT), and the target leg a
   SELL LIMIT at the target. Placement is idempotent:
   - The GTT book's ids are read before the entry is sent, so a slow book never
     delays the exits. An identical GTT from an earlier run is never adopted.
   - After an ambiguous failure (a read timeout, a 5xx, a 429), the book is
     polled for 15 s, because the broker can still be creating the GTT. A
     matching GTT created since arming began is adopted, including one that
     has already triggered (then an alert says the exit has fired). A blind
     retry would arm a second GTT that sells the whole position again. The
     poll judges on its *latest* read: if that failed, polling goes on (for at
     most one more window) until a read succeeds. A read that fails for good
     (an expired session) ends the poll at once, and the alert names it.
   - A request that never left the machine is retried through the outage for
     up to 15 s without using up an attempt. Each outage gets its own window,
     so a second outage after an ambiguous attempt is retried too; after an
     attempt that may have reached Kite (not a refused tunnel), every retry
     follows a successful read of the book, so a GTT booked during the outage
     is adopted, not armed again; a book read that fails for good (an expired
     session) ends the loop at once, and the alert names it. (A proxy that
     refused the tunnel stays on the ambiguous path here: attempts plus book
     polls ride out a longer proxy outage than one never-sent window. Such a
     request carried nothing to Kite, so no GTT can exist: refused tunnels
     alone use every attempt, even while the book, behind the same proxy, is
     unreadable, or a proxy restart in between refuses connections, and if only
     those failed, the position is reported as `POSITION OPEN WITHOUT EXITS`,
     with exits `NONE`; the alert also names an expired session the book
     answered.)
   - After any attempt that may have reached Kite (not a refused tunnel), the
     book is watched for another 15 s. A late
     second GTT raises `GTT DUPLICATE … DELETE ALL BUT GTT n`, or, if the
     duplicate has already triggered, says the exit has fired. If the watch's
     final read failed, or a read failed for good (the alert then names the
     error and the watch ends at once), `GTT n armed after an ambiguous failure
     … CHECK THE GTT BOOK` says a duplicate could not be ruled out.
   - If the book cannot be read at the end of the (extended) poll, or every
     request failed ambiguously, a `GTT STATE UNKNOWN` alert says a GTT may
     exist, and the halt report lists the position's exits as `UNKNOWN`, not
     `NONE`. A GTT for the shares that has already TRIGGERED (an adopted one,
     or a duplicate the watch found) is marked so wherever the position's
     exits are named, never reported as armed protection.
5. Steps 1–4 run as one shielded task. Shutdown never interrupts it: an entry
   that fills during shutdown is protected once and reported. Once shutdown has
   begun, no new entry is sent, even one whose task was already scheduled.
6. If an order's state cannot be confirmed, the symbol stays blocked, so
   nothing enters twice. The run then exits 1 and lists it under `ATTENTION`.
   A fill that could not be protected is reported as
   `POSITION OPEN WITHOUT EXITS`.

Zerodha disabled bracket orders (`variety=bo`) in March 2020, and current
`kiteconnect` has no `VARIETY_BO`. v1.0's commented `VARIETY_BO` call could
never have worked. GTT supports CNC, NRML and MTF, hence CNC. The gateway is
tested against the **real `kiteconnect` SDK** with only its HTTP transport
scripted, so the SDK's own parameter handling and GTT validation run. It has
not been run against a live account.

**Gap risk.** A SELL LIMIT only fills at or above its price. If price gaps
more than 2% through the stop, the triggered stop order rests unfilled until
price recovers to it. **PaperGateway** plays exactly these mechanics on every
later closed bar:
- A gap through the trigger fills at the open if the open is above the stop
  limit. It fills at the limit if price recovers to it within the bar.
  Otherwise the position is reported as `STOP TRIGGERED, LIMIT UNFILLED` and
  the limit keeps resting.
- An open beyond the target fills the target at the open.
- If one bar touches both levels from an open between them, the stop is
  assumed to have come first.
- Exits release the symbol and record realized P&L.

---

## CLI reference

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source {yahoo,csv,kite}` | `yahoo` | Historical data source |
| `--csv PATH` | bundled SWIGGY file | Bars for `--source csv`. Files are named `SYMBOL_interval_from_to.csv`; one whose first `_`-separated token is not the symbol is flagged. |
| `--symbol` | `SWIGGY` | NSE tradingsymbol |
| `--listing-date YYYY-MM-DD` | demo: first bar of last 20 days | IPO listing date. **Required** for `--source kite`. |
| `--base-sessions N` | off (first 150 bars) | Define the IPO base as the first N sessions |
| `--max-lookback-days N` | `180` | Fetch history from at most N days back, for every source (1 to 36500; demos without `--listing-date` use at most 20) |
| `--allow-partial-history` | off | Trade even if history does not start in the listing session |
| `--risk-per-trade` | `15000` | Rupees lost if the stop is hit at its trigger (finite, > 0) |
| `--max-position-value` | `1000000` | Notional cap per position (finite, > 0) |
| `--rvol-threshold` / `--rvol-mode` | `2.0` / `trailing` | Volume filter (threshold finite, > 0) |
| `--risk-reward` | `3.0` | Target distance in multiples of risk (finite, > 0) |
| `--run-seconds` | `6` | Run time, finite and ≥ 0 (`0` = until a stop signal) |
| `--no-simulate` | off | Do not inject the synthetic breakout tape |
| `--live-feed` | off | Stream Kite websocket ticks (disables the synthetic tape) |
| `--live-orders` | off | Real orders. Requires `--source kite --live-feed --expect-ip`. |
| `--expect-ip` | none | Abort unless the public IP matches. **Required** with `--live-orders`. |

---

## Validation on real data

On the 944 bundled bars, the engine's signal logic fires **exactly once**:

| Bar | Close | Base | RVOL (trailing / time-of-day) | AVWAP |
| --- | --- | --- | --- | --- |
| 2026-09-23 09:15 | 286.65 | 285.55 | 3.59 / 2.39 | 277.90 |

That breakout **failed**. The next bar traded down to 284.55 and the session
closed at 278.50:

| | Stop | Target | Size | Outcome |
| --- | --- | --- | --- | --- |
| v1.0 logic | 277.90 (AVWAP, the *farther* stop) | 312.91 | 1,713 sh | stopped at 15:00, ≈ −₹14,990 |
| current logic | 284.95 (1.5 ATR, the *closer* stop) | 291.70 | 3,471 sh (notional-capped) | stopped on the next bar, ≈ −₹5,900 at the signal price (≤ −₹10,934 at the limit) |

This is one trade. It verifies the pipeline end to end on real prints. It is
not evidence of an edge in either direction.

---

## How it was reviewed

Fourteen adversarial review rounds shaped this code. [CHANGELOG.md](CHANGELOG.md)
lists every finding with its severity, verdict, fix and the test that pins it.

1. **v1.0, the original file.** Four reviewers, one per area (live path, alpha,
   execution, data/infra), had to reproduce each defect against the unmodified
   file. Four skeptics then tried to refute each finding with independent
   reproductions. **40 findings, none refuted**, plus 2 from a completeness
   critic. These became v1.1.
2. **v1.1, the first fix.** The same process ran on a frozen snapshot of v1.1.
   It found real defects in the fixes themselves, for example:
   - The synthetic tape could reach a live order.
   - Shutdown could strand a working live entry.
   - A feed reconnect could fake a volume spike.
   - The demo would have stopped trading on 2026-09-28 09:26 IST.

   These became v1.2. Every v1.2 behavior test fails on the v1.1 snapshot,
   except six controls that pin behavior v1.1 already had (listed in the
   CHANGELOG).
3. **v1.2, the second fix.** Four reviewers (orders, live feed, lifecycle,
   alpha and docs) made 24 findings, 23 distinct defects, again several in the
   previous round's own fixes:
   - A GTT retry after a lost reply armed a second GTT that would sell the
     position twice.
   - `kill`/`docker stop` abandoned a working entry.
   - A dropped or silently dead websocket let a truncated bar trade.
   - A failed back-fill was read as "nothing traded in the hole".

   The orders skeptic confirmed all 7 of its findings (2 partly overstated).
   The other three skeptics did not run (the review hit its spend limit), so
   each of those 17 findings was reproduced again, behaviorally, against the
   frozen v1.2 snapshot, the signal cases under Python 3.10 and 3.11. These
   became v1.3. Every v1.3 regression test fails on the v1.2 snapshot, except
   seven controls that pin error classifications v1.2 already had right, and
   the corrected shutdown pin, which pins a v1.1 defect.
4. **v1.3, the third fix.** All four skeptics ran this time. 16 findings, none
   refuted, again mostly gaps in the previous fixes:
   - A GTT booked after the single book check still got a duplicate.
   - Two zeroed volume packets in a row still faked a volume spike.
   - `docker stop`'s 10 s grace killed the engine while an entry waited out
     its 30 s fill window.
   - `nohup` no longer survived a dropped SSH session.

   These became v1.4. Every v1.4 regression test fails on the v1.3 snapshot,
   except the controls listed in the CHANGELOG.
5. **v1.4, the fourth fix.** All four skeptics ran. 15 findings (13 distinct),
   none refuted, and for the first time none critical or high:
   - The GTT-book windows counted one early successful read as covering the
     whole window.
   - The bar in which a re-baseline landed was kept short, lowering the RVOL
     baseline and able to fake a later breakout.
   - A host's own asyncio signal handlers were lost.

   These, plus a rate-limiter race found by the v1.4 test matrix, became v1.5.
6. **v1.5, the fifth fix.** All four skeptics ran. 16 findings, none refuted,
   2 medium and 14 low. Several skeptics showed that the finder's fix would
   regress something, and the safer design was used:
   - A second short outage after an ambiguous GTT attempt got no retry window.
   - On uvloop, a host's signal handler was still swallowed. Two engines in one
     loop could leave signals ignored for good.
   - An NTP step of the host clock mid-session could cut a bar short, and one
     late packet could undo the host-skew correction.
   - A session's lost last bar was never restored, and the next open could
     fire a breakout that had already happened.

   These became v1.6.
7. **v1.6, the sixth fix.** All four skeptics ran. 13 findings, none refuted,
   one high: an engine that started after the host had taken a stop signal
   back (as the README advised for uvloop) joined a route that no longer
   received it, so a supervisor's stop could kill it mid-entry. The others:
   - A GTT booked during a later outage was armed a second time.
   - A host just past the 30 s limit dropped some prints and traded bars built
     from the rest; a laptop sleep left the run's clock behind for good.
   - Several pins did not fail when their fix was reverted.

   These became v1.7.
8. **v1.7, the seventh fix.** All four skeptics ran. 18 findings (15
   distinct), none refuted, none critical or high. Most were edges of the
   rules v1.6 and v1.7 had added:
   - A host just past the 30 s limit ran blind all session without stopping.
   - An engine that started while the host ignored a stop signal never got
     it back; a re-take dropped the host's pre-run state.
   - Several pins could pass for the wrong reason, one hung when reverted,
     and the signal tests raced engine start-up under load.

   These became v1.8.
9. **v1.8, the eighth fix.** All four skeptics ran. 12 findings, none refuted
   (3 partly overstated), 1 medium and 11 low:
   - An engine that joined under a host's `SIG_IGN` guard was never reached if
     the host had displaced or removed the running engines' signal first: once
     the guard was lifted, a supervisor's stop could kill it mid-entry.
   - A held `main()` could not be released by restoring the saved handler on
     asyncio; an engine that ended inside a guard left the host a handler that
     swallows every stop.
   - The zeroed-stamp grace moved prints that no open bar needed it for; an
     illiquid name lost its first traded bar after a late join.
   - Two GTT-book loops kept polling an expired session; five v1.8 guards had
     no pin, and the CHANGELOG misdescribed four test changes.

   These became v1.9.
10. **v1.9, the ninth fix.** All four skeptics ran. 16 findings, none refuted
    (3 partly overstated), 3 medium and 13 low:
    - With no bar open, a zeroed-stamp print filed at feed time could open the
      next bucket's bar and credit it with shares the back-fill counted again:
      a fake RVOL breakout.
    - A held run released outside a stopped uvloop loop left the signal
      swallowed; a worker forked while engines ran never owned its own signals.
    - A GTT whose state was unknown was reported as "exits: NONE".
    - Feed time erred early by the stamps' truncation, several v1.9 rules were
      unpinned, and some docs overclaimed.

    These became v1.10.
11. **v1.10, the tenth fix.** All four skeptics ran (the review was interrupted
    by a container restart and resumed from its journal). 12 findings, 11
    distinct, none refuted (1 partly overstated): 1 high, 1 medium and 9 low:
    - A worker forked while engines ran still inherited the parent engines'
      dispatcher before its own engine hooked and after it ended: a stop sent
      to the worker stopped the parent's engines, or was swallowed, while the
      worker traded on.
    - An unstamped trade received after a stamped quote had closed the bar
      before it was dropped as late, and its shares were counted twice; a
      latency rise past 2 s did the same.
    - A GTT that had already fired was reported as armed protection; a refused
      proxy tunnel was reported as "a GTT may exist".

    These became v1.11.
12. **v1.11, the eleventh fix.** All four skeptics ran. 15 findings, 13
    distinct, none refuted (5 partly overstated): 4 medium and 9 low:
    - After a refused proxy tunnel, the GTT loop gave up with two attempts left
      whenever the book, behind the same proxy, was unreadable too: a proxy
      outage longer than about 30 s left a filled position without exits.
    - With no bar open, an unstamped trade was still filed at feed time, which
      one late packet makes several seconds early: a bucket that never traded
      got its bar, evaluated as a breakout, while the back-fill counted its
      shares again.
    - On 3.11, a forked worker ignored its first Ctrl-C (`asyncio.run`'s own
      SIGINT handler came back in the child); after a held uvloop run closed
      unreleased, the next engine handed the dead loop's dispatcher back, and
      every later stop was swallowed.
    - Four more fork and hand-back edges; the at-fork wakeup-fd clear, an R11
      rule and the fired-GTT halt line had no pin.

    These became v1.12.
13. **v1.12, the twelfth fix.** All four skeptics ran. 13 findings, 12
    distinct, none refuted (3 partly overstated): 4 medium and 8 low:
    - On a host clock running behind the exchange, a print's latest possible
      time could fall before the print itself, so the fake breakout of an
      empty bucket came back; with a bar of the bucket before still open, a
      print of the next bucket joined it and made it a fake breakout too.
    - `run()` restored the dead dispatcher its own engine had just reset; a
      worker forked before any engine hooked forwarded its own stops to the
      parent's engine.
    - After a refused tunnel, a moment of refused connections sent the GTT
      loop into a book recheck that gave up with attempts unused; a stop could
      still let an already scheduled entry send its order.
    - Fork and thread edges, a docs window, and a pin skipped without uvloop.

    These became v1.13.
14. **v1.13, the thirteenth fix.** All four skeptics ran. 10 findings, none
    refuted, 3 medium and 7 low:
    - The v1.13 fix for a print that may spill into the next bar skipped
      evaluating that bar and the next, but kept both in history, one long and
      one short: the short bar still faked a later RVOL breakout. Both are now
      discarded and back-filled.
    - A forward-stamped quote that was the minute's only lag sample still
      proved a print's bucket, which the docs said it could not; the new
      "never fakes a signal" limitation was false on a host behind the
      exchange, and is now documented as the residual it is.
    - After refused tunnels alone, the GTT loop could adopt, or report as
      UNKNOWN, an identical GTT that could not be this entry's.
    - A uvloop child that lifted a guard kept a dead dispatcher; the suite
      failed two tests under `nohup`; docs precision.

    These became v1.14.

---

## Known limitations

* **Kite exits are not tracked.** After a live fill, the broker's GTT owns the
  exit. The engine keeps the symbol reserved until restart and does not
  reconcile positions or GTTs at boot.
* **A silent feed stops the run; it does not reconnect.** Restart it under a
  supervisor (systemd, docker) that acts on exit `1`.
* **A spurious upward jump in a volume counter** is credited to one bar, and
  prints stay uncounted until the real counter passes it. The engine logs a
  warning the first time a counter goes below its high-water mark.
* **A latency rise during heartbeat-only silence** cannot be measured, since
  heartbeats carry no timestamp. A bar can then still close before a tail
  that is more than 2 s late.
* **On uvloop, a host's own stop-signal callback is lost** after `main()`,
  because uvloop cannot hand it back. The engine warns, and the signal is at
  its default; re-register the callback. For the same reason, a plain handler
  the host sets mid-run is re-installed on hand-back, and a loop callback
  registered before it in the same run is removed.
* **`SA_RESTART` is not kept on a plain handler the engine displaced**: one the
  host had before the run, one it set mid-run before another engine started,
  or on uvloop any it set mid-run. It comes back as the same handler,
  re-installed with `signal.signal`, which cannot set the flag, and the flag
  cannot be read back in Python. PEP 475 retries interrupted calls in Python
  code anyway.
* **A stop sent soon after a host lifts a `SIG_IGN` guard** (within 50 ms,
  `RECLAIM_POLL`, or for as long as the host then blocks its event loop) can
  miss the engines if the host had displaced or removed their signal before the
  guard: they take it back on their next check. No event marks a
  `signal.signal()` call, so the engine polls.
* **An unstamped print near a bucket boundary costs signals.** With no bar
  open, one that traded before a boundary is filed in the next bucket if it is
  received after the boundary (on the host's clock, so a host running ahead of
  the exchange, whose lead cannot be told apart from latency, does this to
  every bucket's last moments), or up to a second minus the lowest lag sample
  before it, when that is positive: just under a second on a synced host, and
  up to about 31 s after a forward-stamped packet (the 30 s rule accepts up to
  30 s ahead) until a lag sample is taken more than a minute later. Unless a
  stamped print of its own bucket received later moves it back, that bar is
  not evaluated, since the back-fill says its bucket traded (this print, at
  least), and both bars hold its shares in history. Joining an open bar, it
  spills from that same point before the bar's end until the bar clock would
  close the bar (the feed's lag plus the 2 s grace after the end; one late
  packet raises the lag until a lag sample is taken more than a minute later,
  and on a quiet feed that can be much longer): that bar and the next are
  discarded and back-filled, never evaluated, unless a stamped print of the
  bar's own bucket follows it. On a feed whose packets mostly lack exchange
  time, that skips most bars.
* **On a host running behind the exchange, an unstamped print can be filed a
  bucket early**, and so fake a signal: when it arrives faster than every lag
  sample of the last minute, by more than their stamps' truncation, its latest
  possible time falls before it traded. With no bar open, such a print that
  traded just after a boundary opens a bar of the bucket before, where the name
  may not have traded, and that bar is evaluated. The host's offset has no
  upper bound the samples can show, so no margin closes this: keep the host's
  clock synced (a synced host or one running ahead is not affected).
* **An offset with no lag sample to show it** (a run's first print, on a host
  behind the exchange) cannot be corrected: such an opening print can still be
  dropped as pre-open.
* **A bar an unstamped print opened** is not evaluated when the back-filled
  bucket before it traded, unless a stamped update proved its bucket first. On a
  feed whose packets mostly lack exchange time, that can skip a genuine first
  bar after a reconnect: a missed signal, never a fake one.
* **An untraded name's zero counter is trusted** when its packet dates the last
  trade to an earlier day and no bar of today is known. If a name first traded
  while the feed was down and a stale pre-open snapshot then arrives before any
  bar of today is known, the trades since the outage are credited to one bar.
* **A host clock about 30 s or more behind the exchange** stops the run
  (exit 1) within seconds: every tick is dropped, or the stamps straddle the
  limit. A supervisor restart keeps failing until the time sync is fixed. Within 30 s
  the offset is measured and corrected (an offset smaller than the feed's
  latency cannot be told apart from latency). A live run keeps the clock it started
  with, so after NTP corrects the host mid-session, the run goes on correcting
  (and warning about) its start-up offset until it is restarted.
* **A suspended host** (laptop sleep) is handled on Linux, whose clock keeps
  counting. Elsewhere the run's clock falls behind by the sleep: the router adds
  the time it missed to every signal's age (so a signal from before the sleep
  is refused as stale), and if it is more than 30 s the run stops as above.
  There, a forward NTP step also ages signals, which can only refuse more.
* **The last bars of a session that ends the run** are restored only by the
  next run's history sync: the in-run back-fill of a session's lost tail runs
  when the next session's first bar closes.
* **Gap risk.** A stop gapped more than 2% through its trigger rests unfilled
  (see [Execution and risk](#execution-and-risk)).
* **Sizing ignores margin and liquidity.** There is no check against available
  funds or bar volume, beyond the notional cap.
* **Special sessions** outside 09:15–15:30 (e.g. Diwali Muhurat trading) are
  dropped from both Yahoo history and live ticks.
* **Exchange holidays** are not modelled. `next_session_open` skips weekends
  only, which affects the simulated tape's date, not live trading. The feed
  watchdog treats every weekday as a session and relies on Kite's idle
  heartbeats to stay quiet on a holiday.
* **The bundled data** has vendor artifacts: missing 15:20/15:25 bars and five
  zero-volume bars ([data/README.md](data/README.md)).
* **The strategy is unvalidated.** One historical signal is not a backtest.

---

## Tests

```bash
uv run pytest            # or: pytest (from this directory)
```

The 468 tests run offline in about 5 minutes, whichever way they are launched (a shell background job or `nohup` included: the harness gives the engines it starts their default stop signals). The slowest are real CLI runs that
deliver SIGINT, SIGTERM and SIGHUP mid-entry and during exit, and a shutdown
that must outlast v1.1's 10 s drain. They pass in seven configurations:
- Python 3.10 with pandas 2.2 and numpy 1.26
- Python 3.10 with pandas 2.3 and numpy 2.2 (the `uv.lock` resolution)
- Python 3.11 with pandas 3.0 and numpy 2.4
- the whole suite with the wall clock shifted to 2027 and to 2031
- the whole suite with the wall clock shifted to a Saturday
- the whole suite with the wall clock shifted to inside a session, in the
  window where v1.1's demo would have broken

Pandas `FutureWarning`s raised from engine code fail the suite.

| File | Covers |
| --- | --- |
| `test_alpha.py` | Breakout conditions and crossing semantics, the exact stop/target math, true-range ATR, RVOL baselines and modes, session-defined bases, look-ahead freedom (including inside the base), the pinned real signal |
| `test_live.py` | Tick-to-OHLCV bars, the bar clock, session gating, late ticks, Kite payloads, feed drops, stalls, reconnects and late connects, counter glitches and the high-water mark, no-trade and re-baselining prints and their blind buckets, zeroed stamps (feed time, its bounds on a host behind and after one late packet, filing in the latest bucket with no bar open, open bars a print may spill from and the bar after them (discarded and back-filled), a forward stamp alone in the lag window, provisional and ambiguous bars, late drops, the lag race) and untraded names, feed liveness in feed time, feed lag in both directions, clock skew, host clock steps, suspends, far-future stamps and the 30 s rule and its straddle stop, the silent-socket watchdog, strict hole back-fill within and across sessions, thread safety, loop survival |
| `test_execution.py` | Sizing caps, tick rounding, duplicates, future and stale signals, paper OCO mechanics incl. gaps, the halt report. No Kite dependency. |
| `test_kite_gateway.py` | The Kite gateway on the real SDK: lost and late-booked replies, requests that never left (timeouts, refused connections, unreachable proxies and refused tunnels, repeated outages, GTTs booked during an outage), broker refusals classified by HTTP status, transient errors, cancels that don't land, partial fills, shutdown mid-fill-wait, mid-`place_order` and mid-GTT, idempotent GTT placement with late-booked, triggered and duplicate GTTs and unreadable books (incl. an expired session), unknown and fired GTT states reported as such, refused tunnels that leave no GTT (incl. a proxy outage that also hides the book, or restarts in between) and start no duplicate watch, an expired session named after refused tunnels, an identical earlier or foreign GTT never adopted after refused tunnels alone, a 429 on the entry looked up by its tag, a stop handled before an already scheduled entry ran. Skipped without `kiteconnect`. |
| `test_data.py` | tzdata fallback, logging hygiene, the FIFO rate limiter (incl. wake-up order under clock jitter), the IP check, Yahoo/Kite/CSV adapters incl. malformed payloads and bad timestamps, the session's last 30m/60m bar and special sessions, retry policy, strict back-fill, midnight lookback clamps, listing dates in any zone, orchestrator anchoring and error containment, collecting and running the suite without the Kite extra |
| `test_end_to_end.py` | The CLI: offline trade under a shifted clock, exit codes and their precedence, config and numeric argument validation, `--max-lookback-days` incl. demos, vendor limits and overflow, live-mode safety, a dead websocket, shutdown with an order in flight, SIGINT/SIGTERM/SIGHUP mid-entry and during exit, signals without loop handlers, `nohup`, restoring a host's handlers (asyncio and uvloop, incl. handlers changed mid-run and `SA_RESTART`), several engines in one loop incl. one started after the host took a signal back or under its `SIG_IGN` guard, held runs and `release_signals()` (incl. between runs, from another thread, and a loop closed unreleased on asyncio, uvloop or without loop signals, then `main()`, `run()` or an engine in a thread), forked workers and helpers before, during and after their own engine (incl. Ctrl-C, a host's plain handler or mid-run loop callback, a guard lifted with the saved handler, and a worker forked before any engine hooked), `run()` from a worker thread |
