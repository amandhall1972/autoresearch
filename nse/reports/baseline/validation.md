# Validation report

Params: `{"rsi_n": 14, "rsi_lo": 50.0, "rsi_hi": 70.0, "rsi_exit": 40.0, "ema_fast": 20, "ema_slow": 50, "vwap_n": 20, "regime_ema": 200, "max_names": 20, "gross": 1.0}`

Lookahead check: {'ok': True, 'max_abs_diff': 0.0, 'first_bad_date': None, 'n_checks': 91, 'n_checks_with_holdings': 70}

## Headline

| period | Sharpe | CAGR | vol | maxDD | Sortino | turnover/yr | exposure | Nifty Sharpe | t-stat |
|---|---|---|---|---|---|---|---|---|---|
| IS | 0.7938 | 0.1645 | 0.1414 | -0.2241 | 1.0974 | 37.23 | 0.821 | 0.5854 | 1.57 |
| OOS | -0.5071 | -0.0075 | 0.12 | -0.2513 | -0.6707 | 32.09 | 0.697 | -0.2809 | -0.83 |
| FULL | 0.3121 | 0.0909 | 0.1331 | -0.2513 | 0.4252 | 35.13 | 0.77 | 0.3056 | 0.8 |

## Cost sensitivity

| cost bps | Sharpe IS | Sharpe OOS | Sharpe FULL | CAGR FULL | maxDD FULL |
|---|---|---|---|---|---|
| 0 | 1.185 | -0.1056 | 0.7063 | 0.1499 | -0.2013 |
| 10 | 0.9246 | -0.3733 | 0.4438 | 0.1102 | -0.235 |
| 15 | 0.7938 | -0.5071 | 0.3121 | 0.0909 | -0.2513 |
| 25 | 0.5311 | -0.7741 | 0.0481 | 0.0533 | -0.2829 |
| 40 | 0.1356 | -1.1718 | -0.348 | -0.0008 | -0.3823 |

## Yearly

| year | strat ret | strat Sharpe | strat maxDD | exposure | Nifty ret | Nifty Sharpe | NIFTYBEES ret | cash |
|---|---|---|---|---|---|---|---|---|
| 2020 | 0.3123 | 1.7 | -0.0773 | 0.66 | 0.149 | 0.47 | 0.1542 | 0.0444 |
| 2021 | 0.1949 | 0.89 | -0.1649 | 1.0 | 0.2412 | 1.22 | 0.2597 | 0.0408 |
| 2022 | -0.069 | -0.8 | -0.1293 | 0.72 | 0.0433 | 0.05 | 0.0546 | 0.0501 |
| 2023 | 0.2454 | 1.69 | -0.0558 | 0.91 | 0.2003 | 1.3 | 0.2101 | 0.0667 |
| 2024 | -0.0008 | -0.34 | -0.1866 | 0.98 | 0.088 | 0.21 | 0.1036 | 0.0677 |
| 2025 | 0.0092 | -0.46 | -0.101 | 0.76 | 0.1051 | 0.42 | 0.1166 | 0.0596 |
| 2026 | -0.0284 | -1.17 | -0.0538 | 0.26 | -0.1381 | -1.59 | -0.1285 | 0.0414 |

## Bootstrap / PSR / DSR

* IS: Sharpe 0.7938, 95% block-bootstrap CI [-0.2614, 1.8547], P(Sharpe>0)=0.9345, PSR(0)=0.9393, deflated Sharpe (N=1 trials, SR0=0.0): DSR=0.9392746955102778
* OOS: Sharpe -0.5071, 95% block-bootstrap CI [-1.7662, 0.569], P(Sharpe>0)=0.1835, PSR(0)=0.1985
* FULL: Sharpe 0.3121, 95% block-bootstrap CI [-0.4973, 1.158], P(Sharpe>0)=0.77, PSR(0)=0.7876

## Parameter sensitivity (one-at-a-time +-10/20%)

* 38 perturbations; median Sharpe IS 0.7833 (base 0.7938), min 0.4557, 95% within 0.2 of base; base ranks #12; median Sharpe OOS -0.5013

| param | factor | value | Sharpe IS | Sharpe OOS | Sharpe FULL |
|---|---|---|---|---|---|
| base | 1.0 | None | 0.7938 | -0.5071 | 0.3121 |
| rsi_n | 0.8 | 11 | 0.7861 | -0.4466 | 0.326 |
| rsi_n | 0.9 | 13 | 0.8359 | -0.482 | 0.3493 |
| rsi_n | 1.1 | 15 | 0.776 | -0.4893 | 0.3086 |
| rsi_n | 1.2 | 17 | 0.7561 | -0.4772 | 0.2998 |
| rsi_lo | 0.8 | 40.0 | 0.793 | -0.5038 | 0.3128 |
| rsi_lo | 0.9 | 45.0 | 0.793 | -0.5038 | 0.3128 |
| rsi_lo | 1.1 | 55.00000000000001 | 0.7804 | -0.5668 | 0.272 |
| rsi_lo | 1.2 | 60.0 | 0.6908 | -0.6614 | 0.1811 |
| rsi_hi | 0.8 | 56.0 | 0.9069 | -0.5295 | 0.3867 |
| rsi_hi | 0.9 | 63.0 | 0.7671 | -0.3027 | 0.367 |
| rsi_hi | 1.1 | 77.0 | 0.7441 | -0.5219 | 0.2736 |
| rsi_hi | 1.2 | 84.0 | 0.7294 | -0.5405 | 0.2578 |
| rsi_exit | 0.8 | 32.0 | 0.7938 | -0.5071 | 0.3121 |
| rsi_exit | 0.9 | 36.0 | 0.7938 | -0.5071 | 0.3121 |
| rsi_exit | 1.1 | 44.0 | 0.7977 | -0.4901 | 0.3208 |
| rsi_exit | 1.2 | 48.0 | 0.821 | -0.5811 | 0.2945 |
| ema_fast | 0.8 | 16 | 0.7576 | -0.4696 | 0.2991 |
| ema_fast | 0.9 | 18 | 0.7706 | -0.5235 | 0.2909 |
| ema_fast | 1.1 | 22 | 0.8221 | -0.5118 | 0.3306 |
| ema_fast | 1.2 | 24 | 0.8431 | -0.5077 | 0.3458 |
| ema_slow | 0.8 | 40 | 0.7483 | -0.5035 | 0.2801 |
| ema_slow | 0.9 | 45 | 0.8488 | -0.4992 | 0.3443 |
| ema_slow | 1.1 | 55 | 0.8427 | -0.493 | 0.349 |
| ema_slow | 1.2 | 60 | 0.8516 | -0.4567 | 0.3659 |
| vwap_n | 0.8 | 16 | 0.6197 | -0.6667 | 0.141 |
| vwap_n | 0.9 | 18 | 0.7254 | -0.6662 | 0.2109 |
| vwap_n | 1.1 | 22 | 0.7534 | -0.4424 | 0.3096 |
| vwap_n | 1.2 | 24 | 0.8035 | -0.4631 | 0.3307 |
| regime_ema | 0.8 | 160 | 0.4557 | -0.4228 | 0.132 |
| regime_ema | 0.9 | 180 | 0.545 | -0.5409 | 0.1441 |
| regime_ema | 1.1 | 220 | 0.8172 | -0.4577 | 0.3505 |
| regime_ema | 1.2 | 240 | 0.6074 | -0.2692 | 0.2849 |
| max_names | 0.8 | 16 | 0.7038 | -0.4692 | 0.2761 |
| max_names | 0.9 | 18 | 0.7687 | -0.4498 | 0.3229 |
| max_names | 1.1 | 22 | 0.7875 | -0.4784 | 0.3117 |
| max_names | 1.2 | 24 | 0.7738 | -0.4968 | 0.3005 |
| gross | 0.8 | 0.8 | 0.7938 | -0.5062 | 0.3125 |
| gross | 0.9 | 0.9 | 0.7938 | -0.5066 | 0.3123 |

## Walk-forward (expanding window, yearly re-selection over the perturbation grid)

* concatenated test years: Sharpe -0.2605, CAGR 0.0134, maxDD -0.3295

| test year | chosen | Sharpe train | Sharpe test | return test |
|---|---|---|---|---|
| 2021 | max_namesx0.8 | 1.884 | 0.636 | 0.1443 |
| 2022 | rsi_lox1.2 | 1.471 | -1.641 | -0.1595 |
| 2023 | rsi_hix0.8 | 0.843 | 1.246 | 0.2071 |
| 2024 | rsi_hix0.8 | 0.907 | -0.281 | 0.0102 |
| 2025 | rsi_hix0.8 | 0.656 | -0.799 | -0.0246 |
| 2026 | regime_emax1.1 | 0.498 | -1.625 | -0.0579 |

## Monte Carlo (stationary block bootstrap of daily net returns, full period)

* terminal wealth p5/p50/p95: 0.975/1.781/3.243, P(loss)=0.0575, maxDD median -0.2227, 5th-pct worst -0.3817

## Regime split (Nifty vs its 200-day EMA)

* above: 1288 days, Sharpe 0.327 (Nifty 0.548); below: 384 days, Sharpe 0.498 (Nifty -0.054)
