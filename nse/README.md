# NSE RSI + EMA + VWAP — autoresearch strategy lab

An autonomous research loop (the repo's `autoresearch` idea applied to trading): one editable file
(`strategy.py`), a fixed evaluation harness (`prepare.py`), one metric (`sharpe_is`), keep/discard.

## Layout

| file | role |
|---|---|
| `prepare.py` | **fixed** — constants, data loading/cleaning, point-in-time Nifty 50 universe, RBI repo-rate cash yield, next-open execution engine with costs, metrics, lookahead detector |
| `indicators.py` | **fixed** — causal RSI (Wilder), EMA, rolling & anchored VWAP, ATR, realized vol, cross-sectional rank |
| `strategy.py` | **the agent edits this** — signals, ranking, sizing, parameters |
| `run.py` | runs one experiment, prints the summary block, writes `runs/latest/` |
| `program.md` | the research-loop instructions for the agent |
| `results.tsv` | experiment log (commit, metrics, keep/discard, description) |
| `tests/` | pytest suite for the harness and indicators |
| `data/raw/*.csv` | daily OHLCV, 69 series, 2016-06-01 → 2026-10-09 (3.5 years of indicator warm-up before the backtest) |
| `data/universe/` | monthly/rebalance Nifty 50 membership matrix 2019-12 → 2025-08 + later index changes |
| `data/rf_repo_rate.csv` | RBI repo-rate schedule used as the cash yield |
| `fetch_fmp.py` | reproduction fetcher (needs an FMP API key) |
| `ingest_tool_results.py` | dev helper that converted the MCP tool dumps into `data/raw` |

Run: `python run.py` (≈10 s, most of it the dense lookahead check). Tests: `python -m pytest -q tests`.

## Data

* **Prices**: Financial Modeling Prep EOD for every stock that was a Nifty 50 member at any time in
  2020-2026 (66 names), plus `NIFTY50` and `BANKNIFTY` (signal only) and the `NIFTYBEES` ETF
  (tradable index proxy). Prices are split/bonus-adjusted by the vendor and the vendor also adjusts
  the four demergers in the window (RIL→Jio Financial 2023-07-20, ITC→ITC Hotels 2025-01-06,
  Tata Motors→TMCV 2025-10-14, HUL→Kwality Wall's 2025-12-05). Three vendor factors differ slightly
  from the NSE special-session ex-prices (RIL by 1.6 %, ITC and Tata Motors by 0.2-0.3 %); `prepare.py`
  rescales the pre-ex-date history so the ex-date open/previous-close gap is the true market gap
  (`CORPORATE_ACTION_FIXES`). Tata Motors continues under its post-demerger ticker (TMPV). Bharti
  Infratel continues as Indus Towers; Zomato's history is under its new name Eternal.
* **ETF opens**: the vendor's NIFTYBEES opens are first prints (open equals the day's high or low on
  half the bars). The harness replaces the ETF's open/high/low with the NIFTY 50 open/high/low gaps
  applied to the ETF's previous close, so ETF fills happen at a realistic open.
* **Cleaning**: zero-volume flat bars (vendor gaps: 8 stock-days, all in 2025) are treated as
  missing: the previous close is carried into O/H/L/C with zero volume, the day is marked
  non-tradable and the engine keeps the position unchanged. The trading calendar is the NIFTY 50
  calendar intersected with days on which at least 30 constituents have a bar, minus the one-hour
  Muhurat sessions. This also drops the Saturday special sessions of 2020-02-01, 2024-01-20,
  2024-03-02 and 2024-05-18 (the vendor has no index bar for them; their moves are absorbed into
  the next session) and keeps the full-day Budget session of 2025-02-01.
* **Universe**: the membership matrix is the "Historical Nifty 50 Constituent Weights (20Y)"
  dataset (Bera, 2025, CC BY-NC-SA 4.0; sourced from NSE Indices), rows at month-ends and on every
  rebalance date, extended with the official Sept-2025 (IndiGo, Max Healthcare in; Hero MotoCorp,
  IndusInd out) and Sept-2026 (BSE in; Wipro out) changes. Three rows of the source matrix were
  re-dated to the NSE effective dates (HDFC→LTIM 2023-07-13, Sept-2024 reshuffle 2024-09-30,
  Mar-2025 reshuffle 2025-03-28). A stock can only be held while it is a
  member (point-in-time, no survivorship bias). Spin-offs that sat in the index at a constant price
  for a few days (Jio Financial 2023, ITC Hotels 2025, TMCV 2025) are not tradable instruments and
  are excluded.
* **Cash yield**: RBI repo rate (ACT/365). This approximates a liquid-fund / T-bill yield; it
  over-states it slightly in 2020-22 and under-states it slightly in 2023-24.

## Execution and cost model

Signals are computed on the close of day *t*; the target weights are executed at the **open of t+1**.
The engine keeps exact portfolio state: the overnight gap is earned on the weights as they drifted
to the previous close, the rebalance trades from those drifted weights to the new target (cost on the
actual traded fraction), the intraday leg is earned on the new weights, and the drifted end-of-day
state carries into the next day. A name with no fresh bar keeps its position (it cannot be traded);
a name that leaves the index is sold at the next open; a name that stops trading is liquidated at
its last close. Long-only, no leverage,
Indian cash equities cannot be shorted overnight. One-way cost **15 bps** of traded value: STT on
delivery 10 bps + exchange/SEBI/stamp duty/GST ≈ 1 bp + slippage/impact ≈ 4 bps (large caps, modest
size). Uninvested cash earns the repo rate. Sharpe = annualized mean/std of daily net returns in excess
of the repo rate.

## Evaluation protocol

* **In-sample / development**: 2020-01-01 → 2023-12-31. `sharpe_is` is the only keep/discard metric.
* **Out-of-sample**: 2024-01-01 → 2026-10-09. Logged for every experiment, never used for decisions.
* **Lookahead detector**: every run re-computes the weights on data truncated at ~60 dates spread
  over the backtest, and on ~30 further dates where every later bar is replaced by a random
  permutation of later bars (prices, volumes, membership, index, rate). Weights up to each date
  must be identical to the full-sample run; any dependence on future bars, including "neutral when
  the future is missing" constructions, fails the run. The run also reports how many check dates
  had open positions so an inert check set is visible.
* Final validation (`validate.py`) adds cost sensitivity, parameter sensitivity, yearly breakdown,
  bootstrap confidence intervals and the deflated Sharpe ratio over the number of trials in `results.tsv`.

## Caveats

Indicators must be fully warmed up before 2020-01-01 (3.5 years of history are provided); an indicator
that is NaN must never be read as a signal. Daily bars: VWAP here is a rolling / anchored VWAP of the typical price, not an intraday session VWAP
(the vendor's intraday history for NSE starts in 2023, so a session-VWAP backtest cannot cover 2020-2026).
Dividends are not included for the stocks (price returns), which understates long-only returns by
roughly 1-1.5 %/yr versus a total-return benchmark. The cost model is a flat estimate; results at
25 and 40 bps are reported in the validation.
