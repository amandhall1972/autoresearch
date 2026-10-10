# NSE RSI + EMA + VWAP — autoresearch strategy lab

An autonomous research loop (the repo's `autoresearch` idea applied to trading): one editable file
(`strategy.py`), a fixed evaluation harness (`prepare.py`), one metric (`sharpe_is`), keep/discard.

## Layout

| file | role |
|---|---|
| `prepare.py` | **fixed** — constants, data loading/cleaning, point-in-time Nifty 50 universe, RBI repo-rate cash yield, next-open execution engine with costs, metrics, lookahead detector |
| `indicators.py` | **fixed** — causal RSI (Wilder), EMA, rolling & anchored VWAP, ATR, realized vol, cross-sectional rank |
| `strategy.py` | **the agent edits this** — signals, ranking, sizing, parameters |
| `run.py` | runs one experiment, prints the in-sample summary block, writes `runs/latest/` (`--report` adds the 2024-2026 and full-period metrics; lead/report use only) |
| `holdout.py` | scores a strategy on the **sealed 2017-2019 pre-sample hold-out**; every look is appended to `reports/holdout_log.tsv` |
| `lineage.py` | scores every incumbent of the loop (baseline → each merged winner → final) on all windows; `--holdout` adds the sealed hold-out |
| `validate.py` / `make_report.py` | final validation of `strategy.py` and the generated `reports/REPORT.md` |
| `program.md` | the research-loop instructions for the agent |
| `results.tsv` | experiment log (commit, metrics, keep/discard, description) |
| `tests/` | pytest suite for the harness and indicators |
| `data/raw/*.csv` | daily OHLCV, 69 series, 2016-06-01 → 2026-10-09 (3.5 years of indicator warm-up before the backtest) |
| `data/raw_presample/*.csv` | daily OHLCV 2016-06 → 2020-03 for the 11 names that left the Nifty 50 before 2020 (hold-out only) |
| `data/universe/` | monthly/rebalance Nifty 50 membership matrix 2019-12 → 2025-08 + later index changes; 2016-01 → 2019-12 rows for the hold-out |
| `data/rf_repo_rate.csv` | RBI repo-rate schedule used as the cash yield |
| `fetch_fmp.py` | reproduction fetcher (needs an FMP API key) |
| `ingest_tool_results.py` | dev helper that converted the MCP tool dumps into `data/raw` (`--out data/raw_presample` for the hold-out names) |

Run: `python run.py` (≈10 s, most of it the dense lookahead check). Tests: `python -m pytest -q tests` (30 tests).

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
  non-tradable and the engine keeps the position unchanged.
* **Calendar**: every day on which at least 30 stocks have a fresh bar, minus the one-hour Muhurat
  sessions and the two short 2024 disaster-recovery live sessions (2024-03-02, 2024-05-18; ~1h45 with
  5 % bands). The vendor's NIFTY 50 series lacks 7 regular sessions (2016-08-12, 2018-01-01,
  2019-01-01, 2019-02-13, 2019-03-29, the 2020-02-01 Budget Saturday and the 2024-01-20 Saturday
  session). Those bars are rebuilt from the constituents (free-float weights of the latest membership
  row, drifted by price; validated against the real index on 1,398 days at 4.7 bps RMS daily error)
  and pinned to the published NSE closes (`INDEX_CLOSE_PATCHES`); open/high/low of those 7 bars are
  approximations. BANKNIFTY (signal-only) is carried flat on those days. Before this repair the 7
  sessions were dropped, which moved the incumbent's in-sample Sharpe by 0.001 and the baseline's by 0.025.
* **Universe**: the membership matrix is the "Historical Nifty 50 Constituent Weights (20Y)"
  dataset (Bera, 2025, CC BY-NC-SA 4.0; sourced from NSE Indices), rows at month-ends and on every
  rebalance date, extended with the official Sept-2025 (IndiGo, Max Healthcare in; Hero MotoCorp,
  IndusInd out) and Sept-2026 (BSE in; Wipro out) changes. Three rows of the source matrix were
  re-dated to the NSE effective dates (HDFC→LTIM 2023-07-13, Sept-2024 reshuffle 2024-09-30,
  Mar-2025 reshuffle 2025-03-28). For the 2016-2019 hold-out rows, seven change rows were re-dated to
  the NSE effective dates (2017-03-31, 2017-05-26, 2017-09-29, 2018-04-02, 2018-09-28, 2019-03-29,
  2019-09-27; e.g. the dataset dates Nestle-for-Indiabulls-Housing to 2019-08-27, NSE made it effective
  2019-09-27). A stock can only be held while it is a
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
  From round 2 on (protocol v2, `program.md`) a keep must also pass in-sample robustness gates: both
  in-sample halves (`sharpe_is_h1` 2020-21, `sharpe_is_h2` 2022-23) within 0.10 of the incumbent,
  `sharpe_is_drop5` (5 best days removed) above the incumbent's, and every new parameter on a plateau
  (both neighbours also beat the incumbent).
* **2024-2026 window**: 2024-01-01 → 2026-10-09. Never used by the loop; since protocol v2 `run.py` hides
  it from researchers entirely. The lead researcher did look at its yearly numbers after round 1 (that
  is what triggered protocol v2), so it is reported as a *validation* window, not a pristine hold-out.
* **Sealed pre-sample hold-out**: 2017-04-01 → 2019-12-31 with its own point-in-time Nifty 50 (incl. the
  11 names that left the index before 2020). Never evaluated during research; scored once at the end
  for the pre-registered incumbent lineage only; it cannot change which strategy is final.
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
