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
`grep -E "^(sharpe_is|sharpe_is_h1|sharpe_is_h2|sharpe_is_drop5|maxdd_is|turnover_yr_is|exposure_is|n_names_avg_is|pct_days_invested_is|lookahead_ok|n_harness_fixes):" run.log`.
`run.py` prints in-sample information only. The lead/merge step runs `python run.py --report` to
record the hold-out columns; researchers never do.

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

**Robustness gates for a keep (protocol v2, from round 2 on; all in-sample):**

1. *Both halves*: neither `sharpe_is_h1` (2020-21) nor `sharpe_is_h2` (2022-23) may fall more than
   0.10 below the incumbent's value for the same half.
2. *Not a few days*: `sharpe_is_drop5` (in-sample Sharpe with the 5 best days removed) must also be
   strictly higher than the incumbent's `sharpe_is_drop5`.
3. *Plateau*: every numeric parameter that the change introduces or moves must sit on a plateau: the
   two neighbouring values (about +-20 %, or the adjacent natural grid points such as 50/55/60) must
   each still give `sharpe_is` above the incumbent's. A gain that exists at one value only is noise.
4. *Simplification is a keep*: deleting a rule that does not bind (identical metrics) or that costs
   less than 0.02 of `sharpe_is` while passing gates 1-2 is kept: fewer moving parts generalise better.
5. *Not one episode* (protocol v3, from round 5 on): an invested spell is a maximal run of days on which
   either book holds stocks; with each spell removed in turn from both return series, the candidate's
   in-sample Sharpe must stay above the incumbent's. An improvement that rests on one episode is noise.

`python compare.py candidate.py` (incumbent = `strategy.py`) prints both books' in-sample numbers and
gates 1, 2 and 5 plus the sanity gates mechanically; it only ever loads data up to 2023-12-31.

**Stopping rule** (declared before round 5): at most 8 rounds; a round whose merged gain over the
previous incumbent is below 0.10 `sharpe_is` (about the noise of a one-step threshold change) is
*dry*; the loop stops after 2 consecutive dry rounds or after round 8.

**The first run** establishes the baseline: run `strategy.py` as is.

## Protocol v2 (amended before round 2; why)

Round 1 (six parallel researchers, 244 logged experiments) lifted `sharpe_is` from 0.79 to 1.88,
almost entirely through fast index-timing rules (index RSI hysteresis, breadth ramps). After round 1
the lead researcher looked at the yearly 2024-2026 results of all round-1 candidates (the
pre-registered report-only columns): every candidate lost money there even before costs. So:

* The 2024-2026 window is no longer a pristine hold-out for the lead researcher; it is still reported
  and still never used by the loop, and `run.py` now hides it from researchers entirely.
* A new sealed hold-out was created: the **pre-sample 2017-04-01..2019-12-31** with its own
  point-in-time Nifty 50 membership (including names that left the index before 2020). No strategy
  has ever been evaluated on it. It is scored exactly once, at the end, by `holdout.py`.
  Researchers never run `holdout.py`. **Pre-registered set of strategies to be scored** (fixed before
  round 2 started): the baseline, every merged round winner (the incumbent lineage) and the final
  strategy, nothing else. Every look is appended to `reports/holdout_log.tsv`, and the hold-out result
  can not change which strategy is final: the final strategy is the last incumbent of the loop.
* The keep rule gained the in-sample robustness gates above, to stop the loop from buying fragile
  timing rules with in-sample Sharpe.

## Outcome (loop closed 2026-10-10)

The loop ran its 8 permitted rounds (round 8 gained 0.23, so the dry-round rule never fired) and is
closed. `sharpe_is` went 0.77 (baseline `3659aab`) → 5.09 (final `c44574d`) through 15 merged
incumbents, each passing the gates above. The pre-registered lineage was then scored on the sealed
2017-04..2019-12 hold-out exactly once per strategy (`reports/holdout_log.tsv`, 16 rows): all 16 are
negative (baseline −1.08, best −0.27 at keep 8 `34b6420`, final −0.72; buy-and-hold Nifty +0.42).
On 2024-2026 every incumbent is negative as well (final −1.06). In-sample robustness gates
(halves, drop-5, plateaus, leave-one-spell-out) did not prevent the overfit: they test stability
inside 2020-2023, and the strategy's edge in those years (timed, concentrated cohorts riding the
post-Covid up-legs) did not exist before or after them. Full analysis: `reports/REPORT.md`,
`reports/lineage.md`, `reports/diagnostics.md`.

Any further research must not reuse the 2017-2019 or 2024-2026 windows as tests. Both have now been
seen. The only clean test left is forward data (paper-trading with `signals.py` from 2026-10-10 on).

## Output format

```
---
sharpe_is:            1.2345
sharpe_is_h1:         1.4000
sharpe_is_h2:         1.0500
sharpe_is_drop5:      0.9000
sharpe_is_gross:      1.6000
cagr_is: ...  maxdd_is: ...  turnover_yr_is: ...  exposure_is: ...  n_names_avg_is: ...
lookahead_ok:         True
```

## Logging results

Append one row per experiment to `results.tsv` (tab-separated; commas are fine inside descriptions):

```
commit	sharpe_is	sharpe_oos	sharpe_full	maxdd_is	turnover_yr	status	description
```

1. git commit hash (short, 7 chars)
2. sharpe_is (4 dp) - 0.0000 for crashes
3. sharpe_oos (4 dp) - filled in by the lead with `run.py --report`; researchers leave it blank
4. sharpe_full (4 dp) - same
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

## Protocol v4 (opened 2026-10-10, after the loop above closed): every regime at once

**Why.** The question after the v1-v3 loop was which changes make 2024-2026 positive as well and the strategy
effective over the whole of 2020-2026. Choosing rules on 2024-2026 makes that window in-sample, and the
2017-2019 hold-out has been seen too, so protocol v4 does two things:

1. It researches on **all three seen windows together**: 2017-04..2019-12 (`pre`), 2020-2023 (`is`) and
   2024-01..2026-10 (`oos`), on the 2016-2026 point-in-time universe (`load_data(presample=True, end=END)`).
   **The objective is the lowest of the three window Sharpe ratios** (`objective` in `run4.py`), so a rule
   that buys one regime at the cost of another is not progress.
2. It builds a **new sealed test** that no strategy in this repository has ever been run on: the
   **deep hold-out, 2011-04-01..2016-05-31**, on the point-in-time Nifty 50 of 2008-2016 (`data/raw_deep`,
   built by `build_deep_holdout.py`; 2007-09..2011-03 is indicator warm-up). It is scored once, at the end, by
   `deep_holdout.py`, and the score cannot change which strategy is final.

**Pre-registered deep hold-out set** (fixed before v4 round 1): the v1 baseline `3659aab`, the v1 final
`c44574d` (the v4 starting incumbent), every v4 incumbent merged by the lead, and the v4 final strategy.
Nothing else is ever run on the deep hold-out; every look is appended to `reports/deep_holdout_log.tsv`.
Researchers never run `deep_holdout.py`, never call `load_data(deep=True)` and never read `data/raw_deep`
(the files are kept out of the repository until the final scoring).

**What you can do** is unchanged (edit `strategy.py` only; RSI, EMA and VWAP stay the core of the signal; any
causal use of `MarketData`; NIFTYBEES is a tradable instrument for index exposure), plus one new limit:
**no lookback longer than `V4_MAX_LOOKBACK` = 200 sessions** for anything that is NaN during its warm-up (the
data start on 2016-06-01, so a longer lookback would be undefined in 2017). A structural rewrite is a
candidate like any other.

**Tools.** `v4lab.py` (data cached once; `v4lab.score(path, md, **param_overrides)` in about 0.5 s, no
lookahead check) for exploration; `python run4.py --strategy FILE` (dense lookahead check over 2017-2026, about
35 s) for every candidate you report; `python compare4.py FILE` for the keep gates.

**Keep gates (protocol v4)**, mechanical in `compare4.py`:

* *sanity*: invested on at least 30 % of days, at least 5 names on average, turnover at most 40x a year, span
  maximum drawdown above -30 %, at most 3 harness fixes, `lookahead_ok` True;
* *improves*: the objective rises;
* *windows*: no window Sharpe falls more than 0.10 below the incumbent's;
* *drop10*: the 2017-04..2026-10 Sharpe without its 10 best days rises;
* *years*: leave-one-year-out, the candidate's objective stays above the incumbent's with any single
  calendar year removed;
* *consistency*: no fewer calendar years with a positive excess return;
* *parsimony*: every added `PARAMS` entry must buy at least 0.05 of objective (fewer parameters at no worse
  objective passes);

and by hand: *plateau* (every moved or new numeric parameter's neighbours, about +-20 % or the adjacent natural
grid points, must also beat the incumbent's objective) and *simplification* (deleting a rule that costs less
than 0.02 of objective while passing the other gates is a keep).

**Stopping rule** (declared before v4 round 1): at most 5 rounds; stop after 2 consecutive rounds whose merged
gain in the objective is below 0.05.

**Logging.** Every experiment goes into `results_v4.tsv`:
`round	lens	objective	sharpe_pre	sharpe_is	sharpe_oos	sharpe_span	sharpe_full	turnover_yr	status	description`
(status `keep`, `discard` or `crash`). The deflated Sharpe at the end counts every row.

## Protocol v4.1 (amended after v4 round 1, before round 2, before any deep hold-out look)

**What round 1 showed.** Six lens researchers (595 logged variants, `results_v4.tsv`, candidates in `v4/r1_*.py`)
and six independent auditors. Every candidate lifted the objective far above the v1 final's -1.06 (best 0.69),
and every one failed the `windows` and `drop10` gates. Those gates compare against the v1 final, whose 2020-23
Sharpe (4.97) and drop-10 Sharpe (1.55) are the in-sample fit that v4 exists to correct, so no strategy that
repairs 2017-19 and 2024-26 can pass them. The audits found something more important: the one effect that holds
in all three windows is the NIFTY's own EMA20 > EMA50 trend, and the candidates' extra objective came mostly from
moving Sharpe out of 2020-23 into the binding windows, not from new edge. Measured against the plain EMA timer
`v4/r0_ema_timer.py` (NIFTYBEES while EMA20 > EMA50, the calibration the round-1 brief named as the bar to
beat: objective 0.20, windows 0.75 / 1.12 / 0.20, span 0.79, drop-10 0.51, 8 positive years), no round-1 candidate
passes `windows` or `drop10`.

**Amendments (in force from round 2).**

1. *Gate anchor.* R0 = `v4/r0_ema_timer.py` replaces the v1 final as the incumbent the gates are measured against.
   A keep must lift the worst window without letting any window fall more than 0.10 below the incumbent and without
   leaning more on a few days (`drop10`): a Pareto improvement on the plain EMA timer.
2. *Brief compliance is measured.* R0 uses EMA only. A candidate is *compliant* when RSI, EMA and VWAP each bind:
   replacing each family's rule by its neutral setting (always true / not applied) changes the target weights on at
   least 5 % of the 2017-04..2026-10 sessions. The final strategy must be compliant.
3. *Final-strategy rule.* The final strategy is the last compliant incumbent. If by the end of the stopping rule no
   compliant candidate has passed the gates against its incumbent, there is no compliant final: the report then
   presents R0 (the robust core) and the compliant candidate with the highest objective among those that pass every
   gate except `windows`/`drop10` against R0, states the shortfall, and scores both on the deep hold-out.
4. *Deep hold-out set (amended before any look).* v1 baseline `3659aab`, v1 final `c44574d`, R0, the six round-1
   candidates `v4/r1_*.py` (information only: they show which structural families generalise), every v4.1
   incumbent, and the final strategy (or the two strategies of rule 3). The deep scores cannot change which strategy
   is final.
5. The stopping rule restarts with round 2: at most 4 further rounds (2-5), stop after 2 consecutive rounds whose
   merged objective gain is below 0.05.

## Protocol v4.2 (amended after v4 round 2, before round 3, before any deep hold-out look)

**What round 2 showed.** Five lens researchers (892 logged variants; candidates `v4/r2_*.py`) and five audits.
Every candidate beat R0 in the worst window (0.35 to 0.75) and four passed every mechanical gate, but all five
were rejected, and the reasons exposed two flaws in the rules rather than in the candidates:

1. The *sanity* rule's "at least 5 names" was written for the v1 stock picker. NIFTYBEES holds all 50 members but
   counts as one name, so R0-based books added token equal-weight slivers (0.1 % per name) only to pass it, which
   the auditors rightly flagged as window dressing.
2. R0's EMA20/EMA50 is itself a narrow peak in the binding 2024-26 window (fast 16 or 24 gives R0 an objective of
   0.02-0.03), so every candidate that keeps R0's core fails an absolute plateau on fast/slow, although each beats
   R0 at R0's own neighbouring settings.

**Amendments (in force from round 3).**

1. *Diversification.* The sanity gate is met by at least 5 names on average **or** NIFTYBEES making up at least half
   of the gross on invested days (`etf_share_span`, now reported by `evaluate_v4`). Slivers that exist only to
   pass the old rule are simplifications to delete.
2. *Plateau, mechanical* (`plateau4.py`). New or moved parameters: each +-20 % neighbour must beat the incumbent's
   objective and keep at least 60 % of the margin (unchanged). Parameters inherited unchanged from the incumbent:
   each neighbour must beat the incumbent evaluated at the same neighbour value (relative rule): the change must help
   wherever the inherited core sits. The inherited core's own fragility is reported, not hidden: R0's fast/slow
   neighbours are part of the final report and of the deep hold-out reading.
3. Round 2 merged nothing (dry round 1 of 2 under the v4.1 stopping rule).

## v4 round 3 and the first merge (lead decision, before any deep hold-out look)

Round 3 (three lenses, 1,072 logged rows including plateau neighbours and joint-perturbation draws; candidates
`v4/r3_clean.py`, `v4/r3_core.py` (the same rules as r3_clean under other parameter names) and `v4/r3_combine.py`)
converged on one strategy: R0 at 20 % of the book plus an 80 % pullback sleeve (objective 0.745; every mechanical
gate against R0, plateau4, compliance, costs and the joint perturbation pass). All three audits rejected on
protocol grounds only: deleting the 20 % EMA-timer core RAISES the objective to 0.789 and still passes every gate,
so the simplification rule makes the deletion the keep; one sell rule duplicated the harness (dead code). The
combine lens's R0-side additions (distribution exit, washout re-entry) each rested on one or two episodes and were
not kept.

**Merge (incumbent 1): `v4/inc1_pullback.py`** = the round-3 sleeve without the core and without the dead rule.
Verified by the lead: lookahead_ok, every mechanical gate against R0 (leave-one-year-out gain at least +0.19 in all
ten years), compliant (RSI 0.30, EMA 0.68, VWAP 0.84), plateau4 (minimum neighbour 0.578 against a floor of 0.553),
objective 0.557 at 25 bps and 0.209 at 40 bps (R0 0.168 / 0.120), joint +-20 % perturbation mean 0.597 (p10 0.370,
min 0.189, 98 % of draws above R0). Windows 0.789 / 1.121 / 0.847, span 0.956, 2020-26 1.018, drop-10 0.559.
Disclosed weaknesses: exposure averages 16 % of the book; with each window's own 5 best days removed, 2017-19 falls to
0.06 (R0 0.35); the VWAP-depth ranking is no better than random choice among eligible names; the RSI(3) filter costs
about 0.02 of objective and is kept as the brief's RSI.

The with-core version is somewhat sturdier on event days (2017-19 without its 5 best days 0.22), costs (0.28 at
40 bps) and the joint-perturbation tail (p10 0.42). Because the choice between the two hinged on the simplification
rule, **`v4/r3_clean.py` is added to the deep hold-out set for information** (amendment made before any deep look).

Round 3 gained 0.589 over R0 (not dry). Under the stopping rule, rounds 4 and 5 remain; gates are now measured
against incumbent 1.

## v4 round 4 (dry)

Three lenses against incumbent 1 (468 logged rows; candidates `v4/r4_*.py`), three audits, nothing merged. Each lens
found one lever that raised the objective but failed gates: a cap of 5 new names per trigger day (0.910; fails
sanity at 4.91 names, leave-one-year-out without 2020 and 2024, plateau4), 10 slots instead of 12 (0.843; fails
leave-one-year-out in 4 years, consistency as 2018 turns negative, plateau4), and an exit at the 7-session VWAP
instead of the 10-session one (0.987; fails sanity at 28 % of days invested, leave-one-year-out without 2020,
plateau4). Second sources of return on ordinary days (NIFTYBEES cores, low-volatility, momentum or equal-weight
sleeves) all cut 2024-26. Dry round 1 of 2 since the last merge; round 5 is the last the stopping rule allows.

## v4 round 5 (dry) and the final strategy (lead decision, before any deep hold-out look)

Three lenses against incumbent 1 (530 logged rows; candidates `v4/r5_*.py`), three audits, nothing merged:

* **simplify** (`v4/r5_simplify.py`, 0.792): the only deletion within 0.02 of the objective ties the time stop to
  `vwap_n` (6 parameters). The audit showed it is `max_hold` moved from 20 to 10 rather than removed (identical
  weights), with an implicit multiplier at a local minimum; it fails sanity (28 % of days invested), drop10,
  leave-one-year-out (2018, 2019, 2020, 2025), consistency (2018 turns negative) and plateau4. Every other deletion
  lost more than 0.1: the time stop, the RSI filter, the index filter, the own-uptrend test and the breadth trigger
  each carry the strategy.
* **reconcile** (`v4/r5_reconcile.py`, 0.934): the round-4 VWAP7 exit made conditional on NIFTYBEES still being
  below its own VWAP (one parameter). It passes every mechanical gate but `years` (without 2020: 0.739 against
  0.774) and fails plateau4 (breadth_cut 0.72, rsi_max 16). The audit found the gain rests on three failing-rebound
  months (2018-09, 2024-10, 2023-10; 325 % of the net gain), a 2024-26 lift that is volatility reduction only (the
  same mean excess), knife-edge hidden constants, and an iid placebo flag at the same base rate reaching 0.934 in
  13 % of seeds.
* **trigger** (`v4/r5_trigger.py`, 0.847): about thirty structurally different trigger definitions (multi-length,
  relative, smoothed, change-of and turn-of breadth, index confirmations) were tested; none was both higher and
  flatter. The best (NIFTYBEES below its VWAP as a confirmation) removes 6 of 204 trigger days, gains only in 2017,
  and fails `years`, consistency and plateau4. Its diagnosis is the most useful finding of the round: the
  breadth-cut surface is one sharp peak (0.45..0.75 give 0.28 / 0.46 / 0.55 / 0.79 / 0.65 / 0.56 / 0.39) because a
  looser cut fires a day earlier and fills the slots; averaging the book over neighbouring cuts or VWAP lengths
  gives about 0.75, so the honest level of the neighbourhood is 0.70-0.75 rather than 0.79 (the joint +-20 %
  perturbation, which moves every parameter at once, averages 0.60).

Round 5 is dry round 2 of 2: **the stopping rule ends protocol v4**. By the final-strategy rule the final strategy
is the last compliant incumbent, **incumbent 1 (`v4/inc1_pullback.py`), now installed as `strategy.py`** (byte
identical; the v1 final stays in `v4/v1_final.py`). Before the deep look only its docstring was corrected: the
exposure figure (16 % of the book on average, not a quarter) and one line naming it final; no rule or parameter
changed (objective 0.7887, lookahead_ok, as before).

Deep hold-out set, as registered: v1 baseline and final, R0, the six round-1 candidates and `v4/r3_clean.py`
(information), incumbent 1 = the final (scored once; `deep4.py` scores identical file contents once). The round-4
and round-5 candidates were not registered and are not scored.

## Deep hold-out result (scored once, 2026-10-10, commit 3a9453e)

`python deep4.py --final strategy.py --incumbents v4/inc1_pullback.py` scored the registered set once on
2011-04-01..2016-05-31 (1,268 sessions; `reports/v4/deep_holdout.md`, every look in `reports/deep_holdout_log.tsv`).
`strategy.py` and `v4/inc1_pullback.py` are the same file and were scored once. The data checks
(`deep_holdout.py --data-report`) and the deep-loader tests passed before the look.

| strategy | role | Sharpe | CAGR | max DD |
|---|---|---|---|---|
| `v4/inc1_pullback.py` = `strategy.py` | incumbent / final | 0.683 | 13.5 % | -5.0 % |
| `v4/r3_clean.py` (with the 20 % R0 core) | information | 0.467 | 11.7 % | -4.7 % |
| `v4/r1_pullback.py` (round-1 pullback lens) | information | 0.435 | 11.6 % | -6.6 % |
| `v4/v1_final.py` | information | -0.088 | 7.4 % | -5.2 % |
| `v4/r0_ema_timer.py` (R0) | information | -0.277 | 4.2 % | -17.3 % |
| other round-1 lenses (timing, trend basket, relative strength, repair, overlay) | information | -0.53 to -0.22 | | |
| `v4/v1_baseline.py` | information | -0.606 | -0.8 % | -23.9 % |
| NIFTY 50 price index | benchmark | 0.012 | | |

Reading: the final strategy held up on data no rule was chosen on (0.68 against 0.79 on the worst seen window,
with the NIFTY itself flat over cash). The structural finding is sharper than the single number: the three
pullback strategies are the only positive ones, and every trend-timing family, including the EMA timer R0 that
v4.1 made the bar, is negative over 2011-2016. As registered, these scores cannot change which strategy is final.
