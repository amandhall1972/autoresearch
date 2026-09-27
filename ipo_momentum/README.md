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
uv sync --extra dev                 # Python >= 3.10; pandas, numpy, kiteconnect, pytest (pinned in uv.lock)
uv run python engine.py --source csv
uv run pytest                       # 193 tests, ~65 s, fully offline
```

Without uv: `pip install pandas numpy` (add `kiteconnect` for Zerodha and
`pytest` for the tests), then `python engine.py --source csv`.

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
| Lookback limits (Kite's 180 days, Yahoo's ~60) start at 00:00 IST | A window that starts mid-session would silently drop the listing morning from the base |
| A live feed silent for 15 s during the session stops the run (exit 1) | KiteTicker never pings, so a half-open socket would otherwise look connected forever |
| SIGINT, SIGTERM and SIGHUP all take the orderly shutdown | `kill`, `docker stop` or a dropped SSH session must not abandon a working order |

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
3. Let the order in flight settle. A live entry runs as one shielded task:
   even if the router has to be interrupted after its settle budget, the entry
   finishes on its own deadlines (every SDK call has a 7 s client timeout), and
   its outcome is reported. Further signals during shutdown are logged and
   ignored.
4. Print the halt report.

Where the event loop cannot own signals (Windows), a plain signal handler hands
them to the loop thread-safely, so the same path runs on Python 3.10 and 3.11+.

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
  receive time. KiteTicker's naive host-local datetimes are converted to IST.
* **Volume.** Full- and quote-mode ticks report cumulative day volume
  (`volume_traded`), and a bar receives the *difference* between prints.
  - A print only opens or extends a bar if something verifiably traded. Depth
    updates, and prints that only re-set the counter's baseline, make no bars.
  - Whenever the feed was not watching (start-up, a late connect, any
    reconnect), the counter holds trades the engine never saw. It is
    re-baselined, and every bar the blind spot touched is discarded rather than
    credited with an outage's volume.
  - A counter that goes backwards is a glitch. The baseline is invalidated and
    re-set by the next print, so no share is counted twice. The shares between
    the last good print and that next one are not credited, which can only
    lower that bar's RVOL.
  - The first print of a day counts from zero only if the feed was up at 09:15.
  - LTP-mode ticks carry no volume and make no bars; the feed uses full mode.
* **Feed liveness (live feed only).** Every websocket message is proof of life,
  including the 1-byte heartbeat Kite sends when there is nothing else to send.
  - A websocket close marks every forming bar incomplete at once. Nothing
    opened before the reconnect is kept.
  - A bar closed by the bar clock is kept only if the feed delivered something
    after its bucket ended. Otherwise its tail may be missing, so it is
    discarded and the hole is back-filled.
  - KiteTicker's ping loop never actually sends a ping, so a half-open socket
    stays "connected" and silent. A watchdog stops the run (exit 1) after 15 s
    without any message during the session.
* **Closing.** A bar closes when a tick for a later bucket arrives, *or* when
  the bar clock sees the bucket ended more than 2 s ago.
* **Holes.** If bars are missing before a newly closed bar (a discarded blind
  spot, or a bucket with no trades), they are back-filled from the broker's
  history before that bar is evaluated. This prevents a "first crossing" from
  firing one bar late at a worse price. The back-fill is strict: an empty
  answer means nothing traded, and a failed fetch raises, so the bar is recorded
  but not evaluated. Without a history source, a bar after a hole is likewise
  not evaluated.
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
     timeout, a 5xx), the tag is looked up in the order book for up to 15 s,
     because the broker can book an order moments later.
   - A request that never left the machine (a connect timeout) or a broker
     refusal (any 4xx, e.g. insufficient margin) releases the symbol at once.
2. It polls `order_history` until the order reaches a terminal state, retrying
   through transient API errors.
3. If the order hasn't filled after 30 s, it cancels the remainder and polls
   until the exchange confirms a terminal state. If the cancel cannot be
   confirmed, what is already known to be bought still gets its GTT.
4. It places a **GTT OCO** on the final filled quantity. The stop leg is a SELL
   LIMIT 2% below its trigger (GTT legs must be LIMIT), and the target leg a
   SELL LIMIT at the target. Placement is idempotent:
   - The GTT book's ids are read before arming.
   - After an ambiguous failure, the book is checked, and a matching GTT created
     since then is adopted. A blind retry would arm a second GTT that sells
     the whole position again. An identical GTT from an earlier run is never
     adopted.
   - If the book cannot be read, the retries stop with a
     `GTT STATE UNKNOWN` alert.
5. Steps 1–4 run as one shielded task. Shutdown never interrupts it: an entry
   that fills during shutdown is protected once and reported.
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

Three adversarial review rounds shaped this code. [CHANGELOG.md](CHANGELOG.md)
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
   seven controls that pin error classifications v1.2 already had right.

---

## Known limitations

* **Kite exits are not tracked.** After a live fill, the broker's GTT owns the
  exit. The engine keeps the symbol reserved until restart and does not
  reconcile positions or GTTs at boot.
* **A silent feed stops the run; it does not reconnect.** Restart it under a
  supervisor (systemd, docker) that acts on exit `1`.
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

The 193 tests run offline in about 65 s. The slowest are real CLI runs that
deliver SIGINT, SIGTERM and SIGHUP mid-entry, and a shutdown that must outlast
v1.1's 10 s drain. They pass in seven configurations:
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
| `test_live.py` | Tick-to-OHLCV bars, the bar clock, session gating, late ticks, Kite payloads, feed drops, reconnects and late connects, counter glitches, no-trade and re-baselining prints, feed liveness and the silent-socket watchdog, strict hole back-fill, thread safety, loop survival |
| `test_execution.py` | Sizing caps, tick rounding, duplicates, future and stale signals, paper OCO mechanics incl. gaps, and the Kite gateway on the real SDK: lost and late-booked replies, requests that never left, broker refusals, transient errors, cancels that don't land, partial fills, entries interrupted mid-`place_order` and mid-GTT, idempotent GTT placement |
| `test_data.py` | tzdata fallback, logging hygiene, the FIFO rate limiter, the IP check, Yahoo/Kite/CSV adapters incl. malformed payloads and bad timestamps, the session's last 30m/60m bar, retry policy, strict back-fill, midnight lookback clamps, listing dates in any zone, orchestrator anchoring and error containment |
| `test_end_to_end.py` | The CLI: offline trade under a shifted clock, exit codes and their precedence, config and numeric argument validation, live-mode safety, a dead websocket, shutdown with an order in flight, SIGINT/SIGTERM/SIGHUP mid-entry, signals without loop handlers |
