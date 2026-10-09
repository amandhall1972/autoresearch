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
| `data/raw/*.csv` | daily OHLCV, 69 series, 2019-06-03 → 2026-10-09 |
| `data/universe/` | monthly/rebalance Nifty 50 membership matrix 2019-12 → 2025-08 + later index changes |
| `data/rf_repo_rate.csv` | RBI repo-rate schedule used as the cash yield |
| `fetch_fmp.py` | reproduction fetcher (needs an FMP API key) |
| `ingest_tool_results.py` | dev helper that converted the MCP tool dumps into `data/raw` |

Run: `python run.py` (≈2 s). Tests: `python -m pytest -q tests`.

## Data

* **Prices**: Financial Modeling Prep EOD for every stock that was a Nifty 50 member at any time in
  2020-2026 (66 names), plus `NIFTY50` and `BANKNIFTY` (signal only) and the `NIFTYBEES` ETF
  (tradable index proxy). Prices are split/bonus-adjusted by the vendor and the vendor also adjusts
  the four demergers in the window (RIL→Jio Financial 2023-07-20, ITC→ITC Hotels 2025-01-06,
  Tata Motors→TMCV 2025-10-14, HUL→Kwality Wall's 2025-12-05); this was verified against the
  published special-session prices. Tata Motors continues under its post-demerger ticker (TMPV).
  Bharti Infratel continues as Indus Towers; Zomato's history is under its new name Eternal.
* **Cleaning**: zero-volume flat bars (vendor gaps) are treated as missing and carried forward with
  a zero return; the trading calendar is the NIFTY 50 calendar intersected with days on which at
  least 30 constituents traded, which removes Muhurat sessions, Saturday special sessions and
  holiday artifacts.
* **Universe**: the membership matrix is the "Historical Nifty 50 Constituent Weights (20Y)"
  dataset (Bera, 2025, CC BY-NC-SA 4.0; sourced from NSE Indices), rows at month-ends and on every
  rebalance date, extended with the official Sept-2025 (IndiGo, Max Healthcare in; Hero MotoCorp,
  IndusInd out) and Sept-2026 (BSE in; Wipro out) changes. A stock can only be held while it is a
  member (point-in-time, no survivorship bias). Spin-offs that sat in the index at a constant price
  for a few days (Jio Financial 2023, ITC Hotels 2025, TMCV 2025) are not tradable instruments and
  are excluded.
* **Cash yield**: RBI repo rate (ACT/365). This approximates a liquid-fund / T-bill yield; it
  over-states it slightly in 2020-22 and under-states it slightly in 2023-24.

## Execution and cost model

Signals are computed on the close of day *t*; the target weights are executed at the **open of t+1**
(overnight gap earned with the old weights, intraday leg with the new weights). Long-only, no leverage,
Indian cash equities cannot be shorted overnight. One-way cost **15 bps** of traded value: STT on
delivery 10 bps + exchange/SEBI/stamp duty/GST ≈ 1 bp + slippage/impact ≈ 4 bps (large caps, modest
size). Uninvested cash earns the repo rate. Sharpe = annualized mean/std of daily net returns in excess
of the repo rate.

## Evaluation protocol

* **In-sample / development**: 2020-01-01 → 2023-12-31. `sharpe_is` is the only keep/discard metric.
* **Out-of-sample**: 2024-01-01 → 2026-10-09. Logged for every experiment, never used for decisions.
* **Lookahead detector**: every run re-computes the weights on data truncated at three dates and
  requires identical weights up to each date; a mismatch invalidates the run.
* Final validation (`validate.py`) adds cost sensitivity, parameter sensitivity, yearly breakdown,
  bootstrap confidence intervals and the deflated Sharpe ratio over the number of trials in `results.tsv`.

## Caveats

Daily bars: VWAP here is a rolling / anchored VWAP of the typical price, not an intraday session VWAP
(the vendor's intraday history for NSE starts in 2023, so a session-VWAP backtest cannot cover 2020-2026).
Dividends are not included for the stocks (price returns), which understates long-only returns by
roughly 1-1.5 %/yr versus a total-return benchmark. The cost model is a flat estimate; results at
25 and 40 bps are reported in the validation.
