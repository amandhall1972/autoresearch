# autoresearch: NSE RSI + EMA + VWAP

This is an autonomous research loop (same shape as the LLM-pretraining loop in the repo root) applied
to a trading strategy: the agent edits one file, runs a fixed evaluation, keeps the change if the
metric improves, and repeats.

## Setup

1. **Branch**: experiments run on `autoresearch/<tag>` (this run: `autoresearch/oct9-nse`).
2. **Read the in-scope files** (all under `nse/`):
   - `README.md` - project context, data provenance, cost model, caveats.
   - `prepare.py` - fixed constants, data loading, point-in-time universe, execution/cost engine,
     metrics, lookahead detector. **Do not modify.**
   - `indicators.py` - causal indicator helpers (RSI, EMA, rolling/anchored VWAP, ATR, vol). **Do not modify.**
   - `strategy.py` - the file you modify: signals, filters, ranking, sizing, parameters.
   - `run.py` - runs one experiment and prints the summary block. **Do not modify.**
3. **Verify data**: `data/raw/*.csv` (69 series), `data/universe/*.csv`, `data/rf_repo_rate.csv`.
4. **Initialize `results.tsv`** with the header row; the baseline is recorded after the first run.

## Experimentation

Each experiment: `python run.py > run.log 2>&1` (takes ~10 s). Read the result with
`grep -E "^(sharpe_is|sharpe_oos|sharpe_full|maxdd_is|turnover_yr_is|exposure_is|n_names_avg_is|pct_days_invested_is|lookahead_ok|n_harness_fixes):" run.log`.

**What you CAN do:** modify `strategy.py` only. Any causal transformation of the data provided in
`MarketData` is fair game: indicator periods and thresholds, how RSI/EMA/VWAP are combined, anchored vs
rolling VWAP, ranking/scoring, number of names, volatility targeting, regime filters using the index,
hysteresis, holding periods, position sizing, partial cash. Keep RSI, EMA and VWAP as the core of the
signal (that is the brief); you may add supporting filters.

**What you CANNOT do:** modify `prepare.py`, `indicators.py`, `run.py`, the tests or the data; read
files, the network or the data loader from `strategy.py` (run.py refuses such a file); use future
information (the dense lookahead detector fails the run); treat a NaN indicator as a signal (3.5
years of warm-up are provided, so any lookback up to ~800 bars is valid from 2020-01-01); short
sell; exceed 100% gross exposure; trade non-members or the two indices (the harness zeroes such
weights); use `sharpe_oos`/`*_oos` numbers to make decisions. The NIFTYBEES ETF is a tradable
instrument for parking index exposure; do not let it compete as "one of the stocks".

**The goal: the highest `sharpe_is`** (annualized Sharpe of net daily excess returns over the RBI repo
rate, 2020-01-01 to 2023-12-31, at 15 bps one-way cost, next-open execution). `sharpe_oos`
(2024-01-01 to 2026-10-09) is logged for the record and reported at the end, but it is NEVER a
keep/discard criterion - that is the only thing keeping it an honest out-of-sample test.

**Simplicity criterion**: all else equal, simpler is better. A +0.01 Sharpe that adds 30 lines of
special cases is not worth it; equal Sharpe with less code is a win. Parameters that are suspiciously
specific (e.g. RSI 53.7) are a smell - prefer round, structurally motivated choices.

**Sanity gates for a keep**: `lookahead_ok` is True, `pct_days_invested_is` >= 0.30,
`n_names_avg_is` >= 5, `turnover_yr_is` <= 60, `maxdd_is` > -0.45, and the improvement is not driven
by a single day. If a change improves `sharpe_is` only by cutting exposure to near zero, discard it.

**The first run** establishes the baseline: run `strategy.py` as is.

## Output format

```
---
sharpe_is:            1.2345
sharpe_oos:           0.9876
sharpe_full:          1.1111
cagr_is: ...  maxdd_is: ...  turnover_yr_full: ...  exposure_is: ...  n_names_avg: ...
lookahead_ok:         True
```

## Logging results

Append one row per experiment to `results.tsv` (tab-separated; commas are fine inside descriptions):

```
commit	sharpe_is	sharpe_oos	sharpe_full	maxdd_is	turnover_yr	status	description
```

1. git commit hash (short, 7 chars)
2. sharpe_is (4 dp) - 0.0000 for crashes
3. sharpe_oos (4 dp) - record only
4. sharpe_full (4 dp) - record only
5. maxdd_is (4 dp)
6. turnover_yr_is (2 dp)
7. status: `keep`, `discard`, or `crash`
8. short description of what the experiment tried

## The experiment loop

LOOP:
1. Look at the git state (branch/commit).
2. Change `strategy.py` with one experimental idea.
3. `git commit -am "<idea>"`
4. `python run.py > run.log 2>&1`
5. Read the metrics; if the grep is empty, the run crashed: `tail -n 40 run.log`, fix if trivial else log `crash`.
6. Record the row in `results.tsv`.
7. If `sharpe_is` improved (strictly higher) and the sanity gates pass: keep (advance the branch).
   Otherwise `git reset --hard HEAD~1` (discard).

Ideas worth trying, roughly in order of expected value: hysteresis/holding periods (turnover is expensive
at 15 bps), anchored VWAP (month/quarter/52-week-low anchors) instead of rolling, RSI as a cross-sectional
rank rather than a fixed threshold, inverse-volatility sizing and a portfolio vol target, regime filter
variants (index EMA length, breadth = fraction of members above their EMA), pullback-to-VWAP entries
inside an EMA uptrend instead of breakout entries, number of names, partial exposure instead of binary
regime switching, combining mean-reversion (RSI low) and momentum (RSI high) sleeves.

**NEVER STOP** once the loop has begun; if out of ideas, re-read `strategy.py` and the indicator helpers
for new angles, combine near-misses, try more radical structural changes.
