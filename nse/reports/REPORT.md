# RSI + EMA + VWAP on the Nifty 50 universe, 2020-2026: final report

Branch `autoresearch/oct9-nse`. 3428 experiments logged, 17 kept (including 1 harness re-measurement of an incumbent). Objective: in-sample Sharpe (2020-01-01..2023-12-31, net of 15 bps one-way costs, next-open execution). The 2024-01-01..2026-10-09 window was never used by the loop (validation window, see below); the 2017-2019 pre-sample hold-out was sealed until the end and scored once for the pre-registered incumbent lineage.

## Headline

| | Sharpe IS 2020-23 | Sharpe 2024-26 | Sharpe hold-out 2017-19 | Sharpe FULL 2020-26 | CAGR FULL | maxDD FULL | turnover/yr | exposure |
|---|---|---|---|---|---|---|---|---|
| **final strategy** | 5.0925 | -1.0632 | -0.7243 | 2.8343 | 0.2446 | -0.0664 | 14.74 | 0.206 |
| baseline (first run) | 0.7689 | -0.4908 | -1.0781 | 0.3022 | 0.0894 | -0.246 | 35.21 | 0.77 |
| NIFTY 50 price index | 0.5849 | -0.281 | 0.4154 | 0.3054 | 0.0971 | - | - | 1.0 |

**Verdict.** The loop raised the in-sample Sharpe from 0.7689 to 5.0925; the gain did not carry over. On 2024-26 the final strategy scores -1.0632 (baseline -0.4908, Nifty -0.281); 16 of 16 kept strategies are negative there and 16 of 16 on the sealed 2017-19 hold-out (final -0.7243, baseline -1.0781, best keep 8 `34b6420` at -0.2721; buy-and-hold Nifty 0.4154). The in-sample figure measures fit to 2020-2023, not expected return; see the lineage and diagnostics sections below.

Cash (RBI repo) returned 2020: 4.4%, 2021: 4.1%, 2022: 5.0%, 2023: 6.7%, 2024: 6.8%, 2025: 6.0%, 2026: 4.1%.

![equity](equity.png)

![rolling](rolling_sharpe.png)

![yearly](yearly.png)

## The strategy

```
RSI + EMA + VWAP strategy on the point-in-time Nifty 50 universe. Long-only, daily bars.
This is the ONLY file the research agent edits. The harness (prepare.py) executes the target
weights at the next day's open and charges costs; signals here must use data up to the close.
Final version after 8 autoresearch rounds (see results.tsv and reports/REPORT.md).

REGIME (when to be in the market). The NIFTY 50's own RSI(regime_rsi_n) is a hysteresis switch: the
book switches on when it crosses above regime_rsi_mid + regime_rsi_band (57) and goes fully to cash
below regime_rsi_mid - regime_rsi_band (53). It also stands aside while the index RSI is above
regime_rsi_max (80); when the RSI falls back under 80 with the regime still on, the book re-enters.

ENTRY (which names). On the day the regime switches on, ONE cohort is bought: every Nifty 50 member with
  EMA      : EMA(ema_fast) > EMA(ema_slow), but EMA(fast) no more than ema_spread (6 %) above EMA(slow)
  VWAP     : close > the rolling vwap_n-day VWAP
  RSI      : EMA(rsi_smooth)-smoothed Wilder RSI(rsi_n) below rsi_hi (70) and no reading above rsi_peak
             (80) in the last rsi_peak_n days (no fresh blow-off)
  calm     : its vol_n/vol_long_n realised-vol ratio is not in the members' top third (vol_calm_q)
  and none of the exit rules below would sell it that day.
  A re-entry cohort after the RSI>80 stand-aside instead buys the re_k (5) names with the largest
  (VWAP20/VWAP50 - 1)+ x (smoothed RSI - (NIFTY smoothed RSI - rsi_lag))+: medium-term volume-weighted
  momentum times momentum relative to a hot market.

WEIGHTS (how much of each). Fresh cohorts: Blume-adjusted beta (w_corr_n-day correlation, shrunk by
beta_shrink toward 1) / idiosyncratic variance (vol_n-day vol with the NIFTY-explained part removed),
i.e. single-index mean-variance weights for a timed bet on the market's up-leg, times
  min(1, days since the name's last shock or volume-spike day / vwap_n)   (news names bought small)
  (close / VWAP(vwap_short_n)) ^ rung_power                               (strongest bottom rung first)
Re-entry cohorts: weights proportional to the re-entry score above. No name may exceed w_cap (40 %) of a
cohort; the excess is parked in NIFTYBEES (index exposure, held until the regime switches off).

SIZING. A fresh-start cohort is scaled by the BANK NIFTY's variance ratio,
min(1, (vol_long_n-day vol / vol_n-day vol)^size_power): when the most liquid, most leveraged sector has
just become more volatile than its norm, the new up-leg is less trustworthy. A re-cross after
whipsaw_days or fewer days off (a whipsaw) is bought at whipsaw_size of that. Idle capital earns the
repo rate (the harness credits cash).

WHILE HELD. Weights ride with prices (no rebalancing) and no new names are added until the next
switch-on. Day by day:
  exits      a held name is sold on (a) close < vwap_exit (0.985) x VWAP20, (b) a VWAP-ladder break:
             close < VWAP(vwap_short_n) < VWAP(vwap_long_n), (c) an idiosyncratic shock: return net of
             beta x NIFTY return below -shock_k residual sigmas (previous day's vol_n/corr_n estimates),
             (d) a volume spike: volume > vspike_k x its average over the previous vwap_n days.
  exhaust    once the exits and the re-rank have sold more than 1 - exhaust (60 %) of the formation
             stock weight, the stock remainder is sold (the cohort is spent; NIFTYBEES rides on).
  re-rank    on day vol_cap_delay + 1 the riding book is re-ranked by today's own score (fresh: beta /
             idio variance; re-entry: the re-entry score) and the bottom rerank_drop (a third, rounded
             down) of its stock names are sold, once per cohort.
  vol budget from day vol_cap_delay + 1 the book is trimmed (never topped up) so its single-index model
             vol sqrt((sum w*beta)^2 x NIFTY var + sum w^2 x idio var) stays at or below vol_cap (10 %/yr);
             name i keeps min(1, lam x score_i), so the names the cohort's own weighting likes least go
             first. A name whose EMA20 ran more than ema_spread above its EMA50 counts at stretch_score
             (half) of its score in the re-rank and the trims. NIFTYBEES is never trimmed.
  dip top-up when the share of eligible members above their VWAP20 was more than dip_k (15 points)
             below its vwap_n-day mean yesterday and rises today, a fresh cohort's riding stock names are
             topped up pro rata with idle cash, up to the vol budget (no new names).
Everything is sold when the regime switches off. Names with no fresh bar stay held (they cannot trade).
```

Parameters: `{"rsi_n": 14, "rsi_smooth": 3, "rsi_hi": 70.0, "rsi_peak": 80.0, "rsi_peak_n": 20, "rsi_lag": 16.0, "re_k": 5, "ema_fast": 20, "ema_slow": 50, "ema_spread": 0.06, "vwap_n": 20, "vwap_exit": 0.985, "shock_k": 2.5, "vspike_k": 4.0, "vwap_long_n": 50, "vwap_short_n": 3, "vol_n": 20, "vol_power": 2.0, "vol_long_n": 120, "vol_calm_q": 0.6666666666666666, "corr_n": 20, "w_corr_n": 15, "beta_shrink": 0.3333333333333333, "w_cap": 0.4, "rung_power": 20.0, "regime_rsi_n": 14, "regime_rsi_mid": 55.0, "regime_rsi_band": 2.0, "regime_rsi_max": 80.0, "size_index": "BANKNIFTY", "size_power": 2.0, "whipsaw_days": 3, "whipsaw_size": 0.5, "vol_cap": 0.1, "vol_cap_delay": 2, "rerank_drop": 0.3333333333333333, "stretch_score": 0.5, "exhaust": 0.4, "dip_k": 0.15, "gross": 1.0}`

## Incumbent lineage on every window

Each row is a strategy the loop kept (baseline, every merged round winner, final), re-scored on the final harness. The sealed pre-sample column is the first and only look at 2017-2019 for these strategies (log: `reports/holdout_log.tsv`).

| stage | commit | Sharpe IS 2020-23 | IS 2020-21 | IS 2022-23 | Sharpe 2024-26 | Sharpe FULL 2020-26 | Sharpe pre-sample 2017-19 | Nifty 2017-19 |
|---|---|---|---|---|---|---|---|---|
| baseline | 3659aab | 0.7689 | 1.2075 | 0.2065 | -0.4908 | 0.3022 | -1.0781 | 0.4154 |
| keep 1 | a1edb7e | 1.8761 | 1.9678 | 1.8888 | -1.6006 | 0.4247 | -0.9848 | 0.4154 |
| keep 2 | 7f1c2e2 | 2.4954 | 2.797 | 2.1686 | -1.6459 | 0.8529 | -0.6401 | 0.4154 |
| keep 3 | 79a93c8 | 2.5046 | 2.7984 | 2.1898 | -1.6464 | 0.8571 | -0.646 | 0.4154 |
| keep 4 | 86f94bf | 3.0392 | 3.5785 | 2.3883 | -1.5237 | 1.3896 | -0.674 | 0.4154 |
| keep 5 | 285ee05 | 3.1533 | 3.727 | 2.4591 | -1.5046 | 1.4423 | -0.4485 | 0.4154 |
| keep 6 | 49bae14 | 3.5349 | 4.0636 | 2.9269 | -1.3478 | 1.7525 | -0.4268 | 0.4154 |
| keep 7 | 060582d | 3.739 | 4.2627 | 3.1537 | -1.2809 | 2.0645 | -0.2765 | 0.4154 |
| keep 8 | 34b6420 | 3.8895 | 4.3805 | 3.4152 | -1.3364 | 2.118 | -0.2721 | 0.4154 |
| keep 9 | 8b5d6c8 | 4.0984 | 4.6385 | 3.5688 | -1.3596 | 2.245 | -0.3806 | 0.4154 |
| keep 10 | f717080 | 4.3167 | 4.9699 | 3.5689 | -1.1916 | 2.2355 | -0.4946 | 0.4154 |
| keep 11 | 5f7ef73 | 4.4963 | 5.1816 | 3.7136 | -1.1859 | 2.3796 | -0.4712 | 0.4154 |
| keep 12 | 13574d2 | 4.619 | 5.3412 | 3.7926 | -1.2534 | 2.419 | -0.5458 | 0.4154 |
| keep 13 | bb3892c | 4.8602 | 5.699 | 3.9128 | -1.2767 | 2.5415 | -0.4601 | 0.4154 |
| keep 14 | 337d8e6 | 4.981 | 5.8438 | 3.999 | -1.0675 | 2.7407 | -0.6069 | 0.4154 |
| final | c44574d | 5.0925 | 5.9789 | 4.0827 | -1.0632 | 2.8343 | -0.7243 | 0.4154 |

![lineage](lineage.png)

## Diagnostics: why it fails after 2023

An invested spell runs from a regime switch-on to the switch-off; excess is its return over cash. The selection edge compares the next-20-session return of the names a cohort bought with the average member on the same days (`python diagnose.py`; every spell is listed in `reports/diagnostics.md`).

### Invested spells by period

| period | spells | hit rate | mean spell excess | median | sum | mean days | spells with NIFTY up | mean excess, NIFTY up | mean excess, NIFTY down | cost drag/yr |
|---|---|---|---|---|---|---|---|---|---|---|
| 2020-23 | 32 | 0.844 | 0.0392 | 0.0227 | 1.2545 | 12.7 | 20 | 0.0537 | 0.015 | 0.0207 |
| 2024-26 | 28 | 0.393 | -0.0032 | -0.0028 | -0.089 | 7.0 | 10 | 0.0103 | -0.0106 | 0.0242 |

### Selection edge: next-20-session return of names bought at formation vs all members

| year | formation days | names bought | all members | edge |
|---|---|---|---|---|
| 2020 | 8 | 0.0863 | 0.0573 | 0.029 |
| 2021 | 11 | 0.034 | 0.0253 | 0.0088 |
| 2022 | 7 | 0.0096 | 0.0257 | -0.0161 |
| 2023 | 6 | 0.0591 | 0.0358 | 0.0232 |
| 2024 | 12 | 0.0081 | 0.0154 | -0.0073 |
| 2025 | 9 | 0.0122 | 0.0166 | -0.0044 |
| 2026 | 7 | -0.02 | -0.0134 | -0.0066 |

## Research log (kept experiments)

| # | commit | Sharpe IS | Sharpe OOS | Sharpe FULL | maxDD IS | turnover/yr | description |
|---|---|---|---|---|---|---|---|
| 0 | 3659aab | 0.7938 | -0.5071 | 0.3121 | -0.2241 | 37.23 | baseline: EMA20>EMA50 & close>EMA20 & close>VWAP20 & 50<RSI14<70, hold while close>EMA50 & RSI>40, Nifty>EMA200 regime, top20 equal weight (post-review harness) |
| 1 | a1edb7e | 1.8771 | -1.5995 | 0.4268 | -0.0695 | 25.45 | [r1:rsi] (lens best) KEEP v3 (run.py, lookahead ok): also stand aside while Nifty RSI14 > 80, on v2. E43 robustness: the cutoff improves all 10 regime settings (mean 1.568 -> 1.821; 76/78/82 also all improve); dropping the best IS day gives 1.827 (v2: 1.563), top-5 days dropped 1.644 (v2: 1.379); rolling 6-month Sharpe gain +0.25 mean, positive in 61% of windows |
| 2 | fcd5fcc | 1.8761 | -1.6006 | 0.4247 | -0.0695 | 25.43 | [harness] incumbent (round-1 rsi lens) re-measured after calendar repair: h1 1.9678, h2 1.8888, drop5 1.6429; this is the bar for round 2 |
| 3 | 7f1c2e2 | 2.4954 | -1.6459 | 0.8529 | -0.0482 | 16.53 | [r2:slots] MERGED (lead, corrected harness): cohort bought when the index-RSI regime turns on, all confluence names, inverse-variance weights, weights ride, exit only close<0.985xVWAP20; h1 2.797, h2 2.169, drop5 2.257 |
| 4 | 79a93c8 | 2.5046 | -1.6464 | 0.8571 | -0.049 | 16.53 | [r2:combine] delete close>EMA20 at entry (simplification, gate 4): h1 2.798, h2 2.190, drop5 2.268 |
| 5 | 86f94bf | 3.0392 | -1.5237 | 1.3896 | -0.0535 | 16.53 | [r3:entry] MERGED (lead): cohort entry adds no RSI peak>80 in 20d, RSI lag vs NIFTY <= 12, EMA20 <= 1.06 x EMA50, vol20/vol120 rank < 2/3; h1 3.579, h2 2.388, drop5 2.818 |
| 6 | 285ee05 | 3.1533 | -1.5046 | 1.4423 | -0.0488 | 16.53 | [r3:combine] inverse idiosyncratic variance weights (regime-lens idea), corr_n 20 plateau 10-60: h1 3.727, h2 2.459, drop5 2.938 |
| 7 | 49bae14 | 3.5349 | -1.3478 | 1.7525 | -0.0454 | 16.53 | [r4:volexit] MERGED (lead): exits on idiosyncratic shock (2.5 sigma), 4x volume spike, top-5% vol-expansion rank; h1 4.064, h2 2.927, drop5 3.353 |
| 8 | 060582d | 3.739 | -1.2809 | 2.0645 | -0.0212 | 13.11 | [r4:combine] K1 + 0.975 x VWAP50 trend-break stop: h1 4.263, h2 3.154, drop5 3.606; plateau 0.97/0.98 and 40/60 days all > 3.6997 |
| 9 | 34b6420 | 3.8895 | -1.3364 | 2.118 | -0.0204 | 13.13 | [r5:concentration] MERGED (lead): weights sqrt(Blume beta)/idio variance (15d corr), 40% single-name cap with excess in NIFTYBEES; h1 4.381, h2 3.415, drop5 3.760; leave-one-spell-out min +0.10 |
| 10 | 8b5d6c8 | 4.0984 | -1.3596 | 2.245 | -0.0183 | 13.1 | [r5:combine] K3 = K2 + parameter-free VWAP-ladder stop (close < VWAP3 < VWAP50) replacing 0.975 x VWAP50 (ladder lens); short 2/4/5, long 40/60 all > K2; h1 4.639, h2 3.569, drop5 3.999; vs round-4 final: leave-one-spell-out min +0.31 (32/32) |
| 11 | f717080 | 4.3167 | -1.1916 | 2.2355 | -0.017 | 13.04 | [r6:riskbudget] MERGED (lead): riding cohort trimmed to 10% single-index model vol after 2 days, Blume beta/idio variance weights, vol-rank exit deleted; h1 4.970, h2 3.569, drop5 4.187; leave-one-spell-out min +0.19 |
| 12 | 5f7ef73 | 4.4963 | -1.1859 | 2.3796 | -0.017 | 13.03 | [r6:combine] K1 ladderq lens on the merged base: re-entry weights (VWAP20/VWAP50-1)+ x rung, every weight x (close/VWAP3)^20; h1 5.182, h2 3.714, drop5 4.401; vs round-5 final leave-one-spell-out min +0.34 (32/32) |
| 13 | 13574d2 | 4.619 | -1.2534 | 2.419 | -0.017 | 13.04 | [r7:inspell] MERGED (lead): vol-budget trims by score (beta/idio var fresh, ladder height re-entry) instead of pro rata; h1 5.341, h2 3.793, drop5 4.555; leave-one-spell-out min +0.088 |
| 14 | bb3892c | 4.8602 | -1.2767 | 2.5415 | -0.017 | 13.02 | [r7:combine] K2 = K1 + top-5 height x RSI-lead re-entry pool, fresh lag filter deleted (reentry3 lens): h1 5.699, h2 3.913, drop5 4.849; re_k 3-8, lag 12.8-19.2 above K1; vs round-6 final leave-one-spell-out min +0.285 (32/32) |
| 15 | 337d8e6 | 4.981 | -1.0675 | 2.7407 | -0.017 | 13.08 | [r8:inspell2] MERGED (lead): one-time re-rank on the first trim day sells the bottom third by score; names past the EMA cap at half score; h1 5.844, h2 3.999, drop5 4.983; leave-one-spell-out min +0.087 |
| 16 | c44574d | 5.0925 | -1.0632 | 2.8343 | -0.017 | 13.75 | [r8:combine] K1 breadth-dip top-up of the riding fresh cohort (dipcohort lens), dip_k 0.10-0.20 above 4.981: h1 5.979, h2 4.083, drop5 5.090; vs round-7 final leave-one-spell-out min +0.198 (32/32). FINAL STRATEGY (stopping rule: round 8 of max 8) |

3411 experiments were discarded or crashed; see `results.tsv` for every row.

## Validation

### Headline (full metrics)

| period | Sharpe | CAGR | vol | maxDD | Sortino | turnover/yr | exposure | Nifty Sharpe | t-stat |
|---|---|---|---|---|---|---|---|---|---|
| IS | 5.0925 | 0.4415 | 0.0624 | -0.017 | 15.3666 | 13.75 | 0.228 | 0.5849 | 10.09 |
| OOS | -1.0632 | 0.0063 | 0.0498 | -0.0664 | -1.343 | 16.16 | 0.175 | -0.281 | -1.75 |
| FULL | 2.8343 | 0.2446 | 0.0586 | -0.0664 | 5.5867 | 14.74 | 0.206 | 0.3054 | 7.31 |

In-sample robustness (protocol v2 gates): Sharpe 2020-21 5.9789, 2022-23 4.0827, with the 5 best days removed 5.0902, before costs 5.3422.

## Cost sensitivity

| cost bps | Sharpe IS | Sharpe OOS | Sharpe FULL | CAGR FULL | maxDD FULL |
|---|---|---|---|---|---|
| 0 | 5.3422 | -0.5783 | 3.1844 | 0.2724 | -0.0535 |
| 10 | 5.1791 | -0.903 | 2.9535 | 0.2538 | -0.0608 |
| 15 | 5.0925 | -1.0632 | 2.8343 | 0.2446 | -0.0664 |
| 25 | 4.9092 | -1.3773 | 2.5894 | 0.2264 | -0.0775 |
| 40 | 4.6106 | -1.8267 | 2.2089 | 0.1996 | -0.1144 |

## Yearly

| year | strat ret | strat Sharpe | strat maxDD | exposure | Nifty ret | Nifty Sharpe | NIFTYBEES ret | cash |
|---|---|---|---|---|---|---|---|---|
| 2020 | 0.6706 | 6.67 | -0.0141 | 0.24 | 0.149 | 0.46 | 0.1542 | 0.0444 |
| 2021 | 0.4644 | 5.24 | -0.0104 | 0.19 | 0.2412 | 1.22 | 0.2597 | 0.0408 |
| 2022 | 0.1364 | 2.11 | -0.0109 | 0.12 | 0.0433 | 0.05 | 0.0546 | 0.0501 |
| 2023 | 0.5131 | 5.56 | -0.017 | 0.36 | 0.2003 | 1.3 | 0.2101 | 0.0667 |
| 2024 | -0.0025 | -1.48 | -0.058 | 0.19 | 0.088 | 0.21 | 0.1036 | 0.0677 |
| 2025 | 0.0503 | -0.12 | -0.0551 | 0.22 | 0.1051 | 0.42 | 0.1166 | 0.0596 |
| 2026 | -0.0291 | -2.24 | -0.0501 | 0.1 | -0.1381 | -1.59 | -0.1285 | 0.0414 |

## Bootstrap / PSR / DSR

* IS: Sharpe 5.0925, 95% block-bootstrap CI [3.8084, 6.3338], P(Sharpe>0)=1.0, PSR(0)=1.0, deflated Sharpe (N=3419 trials, SR0=4.0801): DSR=0.9961
* OOS: Sharpe -1.0632, 95% block-bootstrap CI [-2.2002, 0.239], P(Sharpe>0)=0.0575, PSR(0)=0.0332
* FULL: Sharpe 2.8343, 95% block-bootstrap CI [1.4652, 4.1184], P(Sharpe>0)=1.0, PSR(0)=1.0

## Parameter sensitivity (one-at-a-time +-10/20%)

* 144 perturbations; median Sharpe IS 4.9607 (base 5.0925), min 0.9212, 62% within 0.2 of base; base ranks #15; median Sharpe OOS -1.0632

| param | factor | value | Sharpe IS | Sharpe OOS | Sharpe FULL |
|---|---|---|---|---|---|
| base | 1.0 | None | 5.0925 | -1.0632 | 2.8343 |
| rsi_n | 0.8 | 11 | 4.5291 | -0.6315 | 2.6615 |
| rsi_n | 0.9 | 13 | 4.9114 | -1.1588 | 2.6779 |
| rsi_n | 1.1 | 15 | 4.4993 | -0.9788 | 2.3853 |
| rsi_n | 1.2 | 17 | 4.1487 | -0.8671 | 2.2375 |
| rsi_smooth | 0.8 | 2 | 4.7598 | -0.9986 | 2.6091 |
| rsi_smooth | 1.2 | 4 | 4.6306 | -1.0662 | 2.4186 |
| rsi_hi | 0.8 | 56.0 | 2.8123 | -0.7714 | 1.3447 |
| rsi_hi | 0.9 | 63.0 | 3.6435 | -1.0123 | 1.9801 |
| rsi_hi | 1.1 | 77.0 | 3.9015 | -1.2249 | 2.0023 |
| rsi_hi | 1.2 | 84.0 | 3.8845 | -1.2397 | 1.9912 |
| rsi_peak | 0.8 | 64.0 | 2.6459 | -0.5503 | 1.4256 |
| rsi_peak | 0.9 | 72.0 | 4.5455 | -0.8641 | 2.6285 |
| rsi_peak | 1.1 | 88.0 | 4.9448 | -1.0693 | 2.6939 |
| rsi_peak | 1.2 | 96.0 | 4.9448 | -1.0693 | 2.6939 |
| rsi_peak_n | 0.8 | 16 | 5.0925 | -1.0808 | 2.8282 |
| rsi_peak_n | 0.9 | 18 | 5.0925 | -1.0808 | 2.8282 |
| rsi_peak_n | 1.1 | 22 | 5.0925 | -1.0632 | 2.8343 |
| rsi_peak_n | 1.2 | 24 | 5.0925 | -1.0445 | 2.8409 |
| rsi_lag | 0.8 | 12.8 | 5.0067 | -1.1566 | 2.7755 |
| rsi_lag | 0.9 | 14.4 | 5.0737 | -1.1494 | 2.7982 |
| rsi_lag | 1.1 | 17.6 | 5.0884 | -0.9756 | 2.8349 |
| rsi_lag | 1.2 | 19.2 | 5.0872 | -1.0534 | 2.7845 |
| re_k | 0.8 | 4 | 5.1133 | -0.9648 | 2.8781 |
| re_k | 0.9 | 4 | 5.1133 | -0.9648 | 2.8781 |
| re_k | 1.1 | 6 | 5.1095 | -1.1383 | 2.7933 |
| re_k | 1.2 | 6 | 5.1095 | -1.1383 | 2.7933 |
| ema_fast | 0.8 | 16 | 4.8429 | -1.0173 | 2.7725 |
| ema_fast | 0.9 | 18 | 4.9276 | -1.0806 | 2.7766 |
| ema_fast | 1.1 | 22 | 5.0148 | -1.0642 | 2.7514 |
| ema_fast | 1.2 | 24 | 4.8683 | -1.0506 | 2.6537 |
| ema_slow | 0.8 | 40 | 4.7616 | -0.9949 | 2.6024 |
| ema_slow | 0.9 | 45 | 4.9373 | -1.0516 | 2.7017 |
| ema_slow | 1.1 | 55 | 4.8965 | -1.0496 | 2.7806 |
| ema_slow | 1.2 | 60 | 4.5115 | -0.9839 | 2.5609 |
| ema_spread | 0.8 | 0.048 | 4.5706 | -1.0785 | 2.595 |
| ema_spread | 0.9 | 0.054 | 4.8746 | -1.0668 | 2.7666 |
| ema_spread | 1.1 | 0.066 | 5.0165 | -1.0703 | 2.7553 |
| ema_spread | 1.2 | 0.072 | 4.9813 | -1.0796 | 2.7306 |
| vwap_n | 0.8 | 16 | 4.308 | -1.0981 | 2.2712 |
| vwap_n | 0.9 | 18 | 4.7818 | -1.0787 | 2.6333 |
| vwap_n | 1.1 | 22 | 4.662 | -0.9563 | 2.5576 |
| vwap_n | 1.2 | 24 | 4.4791 | -1.0246 | 2.4196 |
| vwap_exit | 0.8 | 0.988 | 4.942 | -1.0478 | 2.7643 |
| vwap_exit | 0.9 | 0.9864999999999999 | 4.9494 | -1.0245 | 2.7624 |
| vwap_exit | 1.1 | 0.9835 | 5.0523 | -0.9808 | 2.8354 |
| vwap_exit | 1.2 | 0.982 | 5.0213 | -1.0123 | 2.7712 |
| shock_k | 0.8 | 2.0 | 4.6983 | -1.0788 | 2.6091 |
| shock_k | 0.9 | 2.25 | 4.8015 | -1.1758 | 2.6196 |
| shock_k | 1.1 | 2.75 | 4.9383 | -1.1483 | 2.6997 |
| shock_k | 1.2 | 3.0 | 4.7749 | -1.1899 | 2.622 |
| vspike_k | 0.8 | 3.2 | 4.9779 | -1.1159 | 2.7341 |
| vspike_k | 0.9 | 3.6 | 4.995 | -1.158 | 2.7218 |
| vspike_k | 1.1 | 4.4 | 4.9762 | -1.0531 | 2.7815 |
| vspike_k | 1.2 | 4.8 | 4.7094 | -1.0707 | 2.606 |
| vwap_long_n | 0.8 | 40 | 5.0508 | -1.2747 | 2.7777 |
| vwap_long_n | 0.9 | 45 | 5.1021 | -1.1376 | 2.8171 |
| vwap_long_n | 1.1 | 55 | 5.055 | -0.965 | 2.8193 |
| vwap_long_n | 1.2 | 60 | 5.0118 | -0.9525 | 2.7844 |
| vwap_short_n | 0.8 | 2 | 5.0979 | -1.0842 | 2.8414 |
| vwap_short_n | 1.2 | 4 | 4.9141 | -1.0951 | 2.708 |
| vol_n | 0.8 | 16 | 4.2702 | -0.8919 | 2.3956 |
| vol_n | 0.9 | 18 | 4.3359 | -1.0406 | 2.39 |
| vol_n | 1.1 | 22 | 4.3616 | -1.131 | 2.3058 |
| vol_n | 1.2 | 24 | 4.363 | -0.9672 | 2.3536 |
| vol_power | 0.8 | 1.6 | 5.0688 | -1.0307 | 2.8442 |
| vol_power | 0.9 | 1.8 | 5.0924 | -1.0427 | 2.8478 |
| vol_power | 1.1 | 2.2 | 4.9693 | -1.0419 | 2.7555 |
| vol_power | 1.2 | 2.4 | 4.9673 | -1.0679 | 2.7382 |
| vol_long_n | 0.8 | 96 | 4.6316 | -1.2276 | 2.433 |
| vol_long_n | 0.9 | 108 | 4.8293 | -1.1466 | 2.5447 |
| vol_long_n | 1.1 | 132 | 5.0296 | -1.185 | 2.6533 |
| vol_long_n | 1.2 | 144 | 4.8086 | -1.0952 | 2.5948 |
| vol_calm_q | 0.8 | 0.5333333333333333 | 4.5074 | -0.9811 | 2.5239 |
| vol_calm_q | 0.9 | 0.6 | 4.7086 | -1.048 | 2.6437 |
| vol_calm_q | 1.1 | 0.7333333333333334 | 4.5092 | -1.1112 | 2.4162 |
| vol_calm_q | 1.2 | 0.7999999999999999 | 4.28 | -1.115 | 2.3081 |
| corr_n | 0.8 | 16 | 4.9155 | -1.1097 | 2.7146 |
| corr_n | 0.9 | 18 | 5.0073 | -1.0232 | 2.8044 |
| corr_n | 1.1 | 22 | 4.8938 | -1.0456 | 2.718 |
| corr_n | 1.2 | 24 | 4.8647 | -1.0481 | 2.6898 |
| w_corr_n | 0.8 | 12 | 4.8868 | -1.1344 | 2.6994 |
| w_corr_n | 0.9 | 14 | 5.0103 | -1.068 | 2.7903 |
| w_corr_n | 1.1 | 16 | 5.0547 | -1.1304 | 2.7991 |
| w_corr_n | 1.2 | 18 | 4.8113 | -1.1236 | 2.6631 |
| beta_shrink | 0.8 | 0.26666666666666666 | 5.0871 | -1.0296 | 2.8454 |
| beta_shrink | 0.9 | 0.3 | 5.0952 | -1.0598 | 2.839 |
| beta_shrink | 1.1 | 0.3666666666666667 | 4.974 | -1.0421 | 2.7582 |
| beta_shrink | 1.2 | 0.39999999999999997 | 4.8989 | -1.0389 | 2.7144 |
| w_cap | 0.8 | 0.32000000000000006 | 5.0553 | -1.1199 | 2.7859 |
| w_cap | 0.9 | 0.36000000000000004 | 5.0912 | -1.0846 | 2.8231 |
| w_cap | 1.1 | 0.44000000000000006 | 5.0738 | -1.0428 | 2.8282 |
| w_cap | 1.2 | 0.48 | 5.0612 | -1.0245 | 2.8246 |
| rung_power | 0.8 | 16.0 | 5.0861 | -1.0533 | 2.8318 |
| rung_power | 0.9 | 18.0 | 5.0905 | -1.0582 | 2.834 |
| rung_power | 1.1 | 22.0 | 5.0659 | -1.0682 | 2.8185 |
| rung_power | 1.2 | 24.0 | 5.0658 | -1.073 | 2.8176 |
| regime_rsi_n | 0.8 | 11 | 3.9066 | -0.7954 | 2.0232 |
| regime_rsi_n | 0.9 | 13 | 4.7319 | -0.901 | 2.6367 |
| regime_rsi_n | 1.1 | 15 | 4.2322 | -0.8314 | 2.3946 |
| regime_rsi_n | 1.2 | 17 | 3.0095 | -1.0482 | 1.5351 |
| regime_rsi_mid | 0.8 | 44.0 | 1.6471 | -0.849 | 0.7292 |
| regime_rsi_mid | 0.9 | 49.5 | 1.7937 | -0.5135 | 0.9121 |
| regime_rsi_mid | 1.1 | 60.50000000000001 | 2.451 | -1.6202 | 1.1629 |
| regime_rsi_mid | 1.2 | 66.0 | 0.9212 | -0.8609 | 0.4671 |
| regime_rsi_band | 0.8 | 1.6 | 4.7869 | -1.191 | 2.5979 |
| regime_rsi_band | 0.9 | 1.8 | 4.878 | -1.0059 | 2.7027 |
| regime_rsi_band | 1.1 | 2.2 | 4.6899 | -1.075 | 2.5362 |
| regime_rsi_band | 1.2 | 2.4 | 4.4014 | -1.0728 | 2.3058 |
| regime_rsi_max | 0.8 | 64.0 | 1.1057 | -1.3304 | 0.0858 |
| regime_rsi_max | 0.9 | 72.0 | 2.9169 | -1.3362 | 1.2791 |
| regime_rsi_max | 1.1 | 88.0 | 3.9012 | -1.0631 | 1.9525 |
| regime_rsi_max | 1.2 | 96.0 | 3.9012 | -1.0631 | 1.9525 |
| size_power | 0.8 | 1.6 | 5.0682 | -1.0436 | 2.821 |
| size_power | 0.9 | 1.8 | 5.078 | -1.0531 | 2.8266 |
| size_power | 1.1 | 2.2 | 5.107 | -1.0678 | 2.8441 |
| size_power | 1.2 | 2.4 | 5.1112 | -1.0671 | 2.8488 |
| whipsaw_days | 0.8 | 2 | 4.9256 | -1.131 | 2.7052 |
| whipsaw_days | 1.2 | 4 | 4.9901 | -1.009 | 2.813 |
| whipsaw_size | 0.8 | 0.4 | 5.0909 | -1.0171 | 2.8597 |
| whipsaw_size | 0.9 | 0.45 | 5.091 | -1.0409 | 2.8468 |
| whipsaw_size | 1.1 | 0.55 | 5.0736 | -1.0839 | 2.8064 |
| whipsaw_size | 1.2 | 0.6 | 5.048 | -1.1031 | 2.7737 |
| vol_cap | 0.8 | 0.08000000000000002 | 5.0655 | -1.0898 | 2.7717 |
| vol_cap | 0.9 | 0.09000000000000001 | 5.0839 | -1.0884 | 2.8056 |
| vol_cap | 1.1 | 0.11000000000000001 | 5.0662 | -1.0813 | 2.829 |
| vol_cap | 1.2 | 0.12 | 5.0287 | -1.1154 | 2.8144 |
| rerank_drop | 0.8 | 0.26666666666666666 | 5.0367 | -1.1563 | 2.7564 |
| rerank_drop | 0.9 | 0.3 | 5.0357 | -1.1295 | 2.7679 |
| rerank_drop | 1.1 | 0.3666666666666667 | 5.0929 | -1.0606 | 2.8312 |
| rerank_drop | 1.2 | 0.39999999999999997 | 5.0676 | -1.0523 | 2.8142 |
| stretch_score | 0.8 | 0.4 | 5.0943 | -1.0632 | 2.8354 |
| stretch_score | 0.9 | 0.45 | 5.0936 | -1.0632 | 2.835 |
| stretch_score | 1.1 | 0.55 | 5.0911 | -1.0632 | 2.8335 |
| stretch_score | 1.2 | 0.6 | 5.0898 | -1.0632 | 2.8326 |
| exhaust | 0.8 | 0.32000000000000006 | 4.929 | -1.02 | 2.7416 |
| exhaust | 0.9 | 0.36000000000000004 | 4.9974 | -1.0031 | 2.7909 |
| exhaust | 1.1 | 0.44000000000000006 | 4.9451 | -1.0093 | 2.7479 |
| exhaust | 1.2 | 0.48 | 4.9542 | -0.9273 | 2.7846 |
| dip_k | 0.8 | 0.12 | 5.097 | -1.1276 | 2.7881 |
| dip_k | 0.9 | 0.135 | 5.097 | -1.0157 | 2.8464 |
| dip_k | 1.1 | 0.165 | 5.0923 | -1.0632 | 2.8343 |
| dip_k | 1.2 | 0.18 | 5.0923 | -1.0632 | 2.8343 |
| gross | 0.8 | 0.8 | 5.0524 | -1.1247 | 2.8466 |
| gross | 0.9 | 0.9 | 5.0778 | -1.0841 | 2.8401 |

## Walk-forward (expanding window, yearly re-selection over the perturbation grid)

* concatenated test years: Sharpe 1.9079, CAGR 0.1752, maxDD -0.0664

| test year | chosen | Sharpe train | Sharpe test | return test |
|---|---|---|---|---|
| 2021 | rsi_hix1.1 | 6.716 | 4.601 | 0.4159 |
| 2022 | beta_shrinkx0.8 | 5.99 | 2.036 | 0.1327 |
| 2023 | size_powerx1.2 | 4.96 | 5.546 | 0.5078 |
| 2024 | re_kx0.8 | 5.113 | -1.175 | 0.0088 |
| 2025 | re_kx0.8 | 4.037 | -0.124 | 0.0503 |
| 2026 | re_kx0.8 | 3.354 | -2.236 | -0.0291 |

## Monte Carlo (stationary block bootstrap of daily net returns, full period)

* terminal wealth p5/p50/p95: 2.608/4.276/7.379, P(loss)=0.0, maxDD median -0.0551, 5th-pct worst -0.0907

## Regime split (Nifty vs its 200-day EMA)

* above: 1289 days, Sharpe 3.334 (Nifty 0.558); below: 385 days, Sharpe 0.742 (Nifty -0.071)


## How to read this

* `sharpe_is` is the number the loop optimised; it is subject to selection bias across all logged trials. The deflated Sharpe ratio above accounts for the number and dispersion of those trials.
* `sharpe_oos` (2024-2026) was never used by the loop to choose rules or parameters, and since protocol v2 researchers cannot see it. The lead researcher did inspect its yearly values after round 1, which is why it is called a validation window here and why a second, sealed hold-out (2017-2019) was created before round 2.
* The sealed pre-sample hold-out (2017-2019) uses its own point-in-time Nifty 50 including 11 names that left the index before 2020; it was scored once at the end for the pre-registered lineage and could not change the final choice.
* The walk-forward row re-selects parameters each year from a perturbation grid using only prior years; it tests the parameter choice, not the rule structure (which was chosen in-sample).
* Costs: 15 bps one-way (STT 10 + fees ~1 + slippage ~4). The cost table shows how quickly the edge decays if execution is worse.
* Stock returns are price returns (no dividends), which understates long-only returns by roughly 1-1.5 %/yr versus a total-return benchmark; cash earns the repo rate.
