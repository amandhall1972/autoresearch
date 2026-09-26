# IPO Momentum Engine

A single-file, asyncio trading engine for **NSE IPO base breakouts**. It syncs
5-minute history, turns live ticks into bars, fires when a recent listing
closes above its post-IPO base on heavy volume, and routes risk-sized orders to a
paper broker or to Zerodha Kite.

```
python engine.py --source csv      # fully offline, on real SWIGGY bars: history -> ticks -> signal -> order
```

> **Paper execution is the default.** Real orders need `--source kite --live-orders`
> and Kite credentials. Nothing here is investment advice. The one real breakout
> in the bundled data **failed** (see [Validation](#validation-on-real-data)).

---

## Contents

1. [Quick start](#quick-start)
2. [What happened when the original file was run](#what-happened-when-the-original-file-was-run)
3. [Architecture](#architecture)
4. [Strategy specification](#strategy-specification)
5. [Live bar synthesis](#live-bar-synthesis)
6. [Execution and risk](#execution-and-risk)
7. [CLI reference](#cli-reference)
8. [Validation on real data](#validation-on-real-data)
9. [Changes from v1.0](#changes-from-v10)
10. [Known limitations](#known-limitations)
11. [Tests](#tests)

---

## Quick start

```bash
cd ipo_momentum
uv sync --extra dev                 # Python >= 3.10; pandas, numpy, kiteconnect, pytest (pinned in uv.lock)
uv run python engine.py --source csv
uv run pytest                       # 95 tests, ~13 s, fully offline
```

Without uv: `pip install pandas numpy` (add `kiteconnect` for Zerodha and
`pytest` for the tests), then `python engine.py --source csv`.

| Mode | Command | Needs |
| --- | --- | --- |
| Offline demo on real bars | `python engine.py --source csv` | nothing (bundled data) |
| Original demo (Yahoo history) | `python engine.py` | outbound HTTPS to `query1.finance.yahoo.com` |
| Zerodha history + simulated tape | `python engine.py --source kite` | `KITE_API_KEY`, `KITE_ACCESS_TOKEN` |
| Zerodha live ticks, paper orders | `python engine.py --source kite --live-feed --no-simulate --run-seconds 0` | same |
| Zerodha live ticks, **real orders** | `... --live-feed --live-orders --expect-ip <static IP>` | same, plus a funded account |

Exit codes: `0` success, `1` no usable history or a crashed worker, `2`
invalid configuration, `130` Ctrl-C.

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

**Run 3: v1.1, the same command-line demo, offline on the same real bars:**

```
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
close (264.85) come from the data. The tape is timestamped at the next
session's open, so it continues the real history.

---

## Architecture

```
 ┌───────────── 1. Data harmonization ─────────────┐
 │ PublicExchangeAdapter (Yahoo)                   │   harmonize_bars(): bars exist only if they traded,
 │ ZerodhaKiteAdapter    (Kite REST)  ──────────► │   IST index, float64, no future back-fill,
 │ CsvReplayAdapter      (offline)                 │   forming bar dropped
 └──────────────── ProductionOrchestrator ─────────┘   history must reach the listing date (fail closed)
                           │ market_state{symbol: DataFrame}
 broker websocket thread   ▼
 KiteTicker ──on_ticks──► LiveTickAdapter.broker_on_ticks ──call_soon_threadsafe──► tick_queue
                                     (normalize: token→symbol, exchange time, cumulative volume)
                           │ event loop
                           ▼
          process_ticks ─► on_tick ─► close_bar ◄── bar_clock (closes bars with no later tick)
                                        │  appends the bar, notifies bar listeners (paper exits)
                                        ▼
                          AlphaEngine.evaluate (causal, vectorized)
                                        │ Signal
                                        ▼ oms_queue
          ExecutionRouter.process_orders ─► plan (tick-rounded, risk- and notional-capped)
                                        ▼
                     PaperGateway   |   KiteOrderGateway (LIMIT entry → GTT OCO stop/target)
```

Three worker tasks run under supervision: the tick aggregator, the bar clock
and the OMS router. A worker that stops ends the run with exit code 1. A
failure while handling one tick, bar or order is logged and skipped. It never
kills a loop.

---

## Strategy specification

All indicators are **causal**. The value on bar *t* uses bars ≤ *t* only. The
test suite checks this by walking the real data bar by bar
(`test_walk_forward_equals_vectorized_scan_on_real_data`).

| Quantity | Definition |
| --- | --- |
| IPO base high | `max(High)` over the first `base_bars = 150` bars since listing (two sessions). No breakout is possible until the base is complete. |
| AVWAP | VWAP anchored at the listing: `cumsum(TP·V) / cumsum(V)`, where `TP = (H+L+C)/3`. It is undefined until volume has traded. |
| RVOL (`trailing`, default) | `V_t / mean(V over the previous 20 bars)`. The current bar is excluded. Vendor-gap bars (range > 0, volume 0) are ignored, and at least 15 valid bars are required. |
| RVOL (`time_of_day`) | `V_t / mean(V of the same 5-minute slot over the previous 10 sessions)`. It falls back to trailing until the slot has history. |
| ATR | 14-bar mean of **true range** `max(H−L, |H−C₋₁|, |L−C₋₁|)`, so gaps count. |
| Breakout | `C₋₁ ≤ base < C` **and** `C > AVWAP` **and** `RVOL > 2.0` |
| Stop | `max(C − 1.5·ATR, AVWAP)`: "risk bounded by 1.5 ATR, or the AVWAP floor, whichever is closer" |
| Target | `C + 3 × (C − stop)` |

**Why `time_of_day` exists.** On the bundled data, 75% of 09:15 bars have
trailing RVOL > 2, against 11% of other bars. At the open the volume filter
barely filters. With `--rvol-mode time_of_day` the 09:15 rate drops to 17%.
Trailing stays the default because it is the original strategy's definition.

---

## Live bar synthesis

* **Symbols.** Kite ticks carry an integer `instrument_token`, which is mapped
  to the tradingsymbol with the instrument dump loaded at boot. Unknown tokens
  are dropped and counted. So are ticks for a symbol whose history did not
  load. Without history there is no IPO base, and bars built from live ticks
  alone would anchor the base at start-up.
* **Session.** Only prints inside the continuous session (09:15–15:30 IST)
  make bars. Pre-open auction prints are dropped before the volume counter
  moves, so the auction volume lands in the 09:15 bar, as in broker candles.
* **Time.** A tick belongs to the bar that contains its `exchange_timestamp`
  (falling back to `last_trade_time`, then `timestamp`, then receive time).
  KiteTicker delivers naive host-local datetimes, which are converted to IST.
* **Volume.** Full- and quote-mode ticks report cumulative day volume
  (`volume_traded`), and a bar receives the *difference* between prints. The
  first print of a day counts from zero only if the engine was running at the
  09:15 open. Joining mid-session never dumps the day's earlier volume into one
  bar. LTP-mode ticks carry no volume and contribute 0.
* **Closing.** A bar closes when a tick for a later bucket arrives, *or* when
  the bar clock sees the bucket ended more than 2 s ago. Without the clock, an
  illiquid name's bar, or the session's 15:25 bar, would close at the next
  day's open.
* **Integrity.** Ticks for a bucket that has already closed are dropped, not
  merged. The first bucket is discarded if the engine started inside it, because
  its open and volume would be wrong. History fetches drop the still-forming bar.

---

## Execution and risk

**Sizing.** `qty = min(floor(risk_per_trade / (limit − stop)), floor(max_position_value / limit))`.
Risk is measured from the *worst acceptable fill* (the limit price). Defaults:
₹15,000 risk per trade and a ₹10,00,000 notional cap. Without the cap, a
tight stop would size into crores. `risk_per_trade` is a fixed rupee amount,
not a fraction of equity, and gaps can lose more than it.

**Prices** are placed on the tick grid in the conservative direction. The
entry limit (`signal × 1.005`) rounds up, and the stop and target round down.
Kite supplies each instrument's tick size.

**Static IP.** `--live-orders` refuses to start without `--expect-ip`. The
check asks three IPv4-only echo services concurrently (IPv6-only ones for an
IPv6 address). A dual-stack service would report the IPv6 address of an
IPv4-whitelisted host. Any disagreeing answer aborts the run, and at least two
services must confirm the address. Junk bodies (captive portals) and
unreachable services are ignored.

**Guards.** The router applies these, in order:
1. Skip a symbol that already has a pending or open position.
2. Reject a signal whose bar closed more than 60 s ago.
3. Reject degenerate geometry (NaN, a stop at or above the entry, a target
   inside the limit).

The Kite gateway also re-checks the live LTP. It skips the entry if the price
has fallen to or below the stop, or has run above the limit.

**PaperGateway** fills at the signal price and plays a simulated OCO on every
later closed bar:
* A stop gapped through fills at the open.
* If one bar touches both levels, the stop is assumed to have come first.
* Exits release the symbol and record realized P&L.

**KiteOrderGateway** runs this sequence:
1. A `regular` LIMIT BUY (product `CNC`).
2. Poll `order_history` until the order is terminal. Cancel any remainder after
   30 s.
3. Place a **GTT OCO** on the filled quantity: a stop leg with a limit 0.5%
   below its trigger, and a target leg.
4. If the GTT fails after a fill, log `CRITICAL POSITION OPEN WITHOUT EXITS`.

Zerodha disabled bracket orders (`variety=bo`) in March 2020, and current
`kiteconnect` has no `VARIETY_BO`. v1.0's commented `VARIETY_BO` call could
never have worked. GTT supports CNC, NRML and MTF, which is why CNC is the
default. The gateway is tested against the **real `kiteconnect` SDK** with only
its HTTP transport stubbed, so the SDK's own GTT payload validation runs. It
has not been run against a live account.

---

## CLI reference

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source {yahoo,csv,kite}` | `yahoo` | Historical data source |
| `--csv PATH` | bundled SWIGGY file | Bars for `--source csv` |
| `--symbol` | `SWIGGY` | NSE tradingsymbol |
| `--listing-date YYYY-MM-DD` | as-of − 20 days | IPO listing date (anchors base and AVWAP) |
| `--allow-partial-history` | off | Trade even if history does not reach the listing |
| `--risk-per-trade` | `15000` | Rupees lost if the stop is hit at its trigger |
| `--max-position-value` | `1000000` | Notional cap per position |
| `--rvol-threshold` / `--rvol-mode` | `2.0` / `trailing` | Volume filter |
| `--risk-reward` | `3.0` | Target distance in multiples of risk |
| `--run-seconds` | `6` | Run time (`0` = until Ctrl-C) |
| `--no-simulate` | off | Do not inject the synthetic breakout tape |
| `--live-feed` / `--live-orders` | off | Kite websocket ticks / real orders (`--source kite` only) |
| `--expect-ip` | none | Abort unless the public IP matches (static-IP whitelisting). **Required** with `--live-orders`. |

The default listing date (20 days before the data) mirrors v1.0's demo, which
treats SWIGGY as a fresh listing. SWIGGY actually listed on 2024-11-13. With
its real date and the bundled data, the engine correctly refuses to trade, and
`--allow-partial-history` overrides that.

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
| v1.1 logic | 284.95 (1.5 ATR, the *closer* stop) | 291.70 | 3,471 sh (notional-capped) | stopped on the next bar, ≈ −₹5,900 at the signal price (≤ −₹10,934 at the limit) |

This is one trade. It verifies the pipeline end to end on real prints. It is
not evidence of an edge in either direction.

---

## Changes from v1.0

[CHANGELOG.md](CHANGELOG.md) lists every v1.0 defect. For each it gives
severity, the second reviewer's verdict, the v1.1 fix, and the test that pins
it. Every item was reproduced against the original file by one reviewer, then
re-checked by a second reviewer trying to refute it. No finding was refuted.

---

## Known limitations

* **Special sessions** outside 09:15–15:30 (e.g. Diwali Muhurat trading) are
  dropped from both Yahoo history and live ticks.
* **Exchange holidays** are not modelled. `next_session_open` skips weekends
  only, which affects the simulated tape's date, not live trading.
* **Kite exits are not tracked.** After a live fill, the broker's GTT owns the
  exit. The engine keeps the symbol reserved until restart and does not
  reconcile positions at boot.
* **Sizing ignores margin and liquidity.** There is no check against available
  funds or bar volume, beyond the notional cap.
* **The bundled data** has vendor artifacts: missing 15:20/15:25 bars and five
  zero-volume bars ([data/README.md](data/README.md)).
* **The strategy is unvalidated.** One historical signal is not a backtest.

---

## Tests

```bash
uv run pytest            # or: pytest (from this directory)
```

The 95 tests run offline in about 13 s. They pass on Python 3.10 with pandas
2.2 and numpy 1.26, on Python 3.10 with pandas 2.3 and numpy 2.2 (the
`uv.lock` resolution), and on Python 3.11 with pandas 3.0 and numpy 2.4.
Pandas `FutureWarning`s raised from engine code fail the suite.

| File | Covers |
| --- | --- |
| `test_alpha.py` | Breakout conditions, the exact stop/target math, true-range ATR, RVOL baselines, look-ahead freedom on real data, the pinned real signal |
| `test_live.py` | Tick-to-OHLCV bars, the bar clock, partial/late ticks, Kite payloads (token map, exchange time, cumulative volume), thread safety, loop survival |
| `test_execution.py` | Sizing caps, tick rounding, duplicates, failure recovery, paper OCO exits, the Kite gateway on the real SDK |
| `test_data.py` | tzdata fallback, logging hygiene, the FIFO rate limiter, Yahoo/Kite/CSV adapters, retry policy, orchestrator anchoring |
| `test_end_to_end.py` | The CLI: offline trade, abort exit codes, config validation, worker-crash supervision |
