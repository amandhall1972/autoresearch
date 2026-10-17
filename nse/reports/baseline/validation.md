# Validation report

Params: `{"rsi_n": 14, "rsi_lo": 50.0, "rsi_hi": 70.0, "rsi_exit": 40.0, "ema_fast": 20, "ema_slow": 50, "vwap_n": 20, "regime_ema": 200, "max_names": 20, "gross": 1.0}`

Lookahead check: {'ok': True, 'max_abs_diff': 0.0, 'first_bad_date': None, 'n_checks': 91, 'n_checks_with_holdings': 70}

## Headline

| period | Sharpe | CAGR | vol | maxDD | Sortino | turnover/yr | exposure | Nifty Sharpe | t-stat |
|---|---|---|---|---|---|---|---|---|---|
| IS | 0.7689 | 0.1603 | 0.1413 | -0.2241 | 1.061 | 37.38 | 0.821 | 0.5849 | 1.52 |
| OOS | -0.4908 | -0.0057 | 0.12 | -0.246 | -0.6488 | 32.05 | 0.696 | -0.281 | -0.81 |
| FULL | 0.3022 | 0.0894 | 0.1331 | -0.246 | 0.4112 | 35.21 | 0.77 | 0.3054 | 0.78 |

In-sample robustness (protocol v2 gates): Sharpe 2020-21 1.2075, 2022-23 0.2065, with the 5 best days removed 0.4736, before costs 1.162.

## Cost sensitivity

| cost bps | Sharpe IS | Sharpe OOS | Sharpe FULL | CAGR FULL | maxDD FULL |
|---|---|---|---|---|---|
| 0 | 1.162 | -0.0897 | 0.6975 | 0.1484 | -0.1959 |
| 10 | 0.9004 | -0.3571 | 0.4343 | 0.1087 | -0.2297 |
| 15 | 0.7689 | -0.4908 | 0.3022 | 0.0894 | -0.246 |
| 25 | 0.505 | -0.7577 | 0.0376 | 0.0517 | -0.2777 |
| 40 | 0.1075 | -1.1555 | -0.3596 | -0.0024 | -0.3792 |

## Yearly

| year | strat ret | strat Sharpe | strat maxDD | exposure | Nifty ret | Nifty Sharpe | NIFTYBEES ret | cash |
|---|---|---|---|---|---|---|---|---|
| 2020 | 0.2945 | 1.6 | -0.0773 | 0.66 | 0.149 | 0.46 | 0.1542 | 0.0444 |
| 2021 | 0.1949 | 0.89 | -0.1649 | 1.0 | 0.2412 | 1.22 | 0.2597 | 0.0408 |
| 2022 | -0.069 | -0.8 | -0.1293 | 0.72 | 0.0433 | 0.05 | 0.0546 | 0.0501 |
| 2023 | 0.2454 | 1.69 | -0.0558 | 0.91 | 0.2003 | 1.3 | 0.2101 | 0.0667 |
| 2024 | -0.0028 | -0.35 | -0.1866 | 0.98 | 0.088 | 0.21 | 0.1036 | 0.0677 |
| 2025 | 0.0163 | -0.39 | -0.0946 | 0.75 | 0.1051 | 0.42 | 0.1166 | 0.0596 |
| 2026 | -0.0284 | -1.17 | -0.0538 | 0.26 | -0.1381 | -1.59 | -0.1285 | 0.0414 |

## Bootstrap / PSR / DSR

* IS: Sharpe 0.7689, 95% block-bootstrap CI [-0.2774, 1.8489], P(Sharpe>0)=0.9245, PSR(0)=0.9334, deflated Sharpe (N=244 trials, SR0=1.1171): DSR=0.2483
* OOS: Sharpe -0.4908, 95% block-bootstrap CI [-1.7718, 0.5292], P(Sharpe>0)=0.188, PSR(0)=0.2061
* FULL: Sharpe 0.3022, 95% block-bootstrap CI [-0.5101, 1.1552], P(Sharpe>0)=0.769, PSR(0)=0.7803

## Parameter sensitivity (one-at-a-time +-10/20%)

* 38 perturbations; median Sharpe IS 0.7585 (base 0.7689), min 0.4289, 92% within 0.2 of base; base ranks #12; median Sharpe OOS -0.4846

| param | factor | value | Sharpe IS | Sharpe OOS | Sharpe FULL |
|---|---|---|---|---|---|
| base | 1.0 | None | 0.7689 | -0.4908 | 0.3022 |
| rsi_n | 0.8 | 11 | 0.7674 | -0.426 | 0.3227 |
| rsi_n | 0.9 | 13 | 0.811 | -0.4593 | 0.3415 |
| rsi_n | 1.1 | 15 | 0.7511 | -0.4683 | 0.3002 |
| rsi_n | 1.2 | 17 | 0.7313 | -0.4604 | 0.2909 |
| rsi_lo | 0.8 | 40.0 | 0.7682 | -0.4874 | 0.3029 |
| rsi_lo | 0.9 | 45.0 | 0.7682 | -0.4874 | 0.3029 |
| rsi_lo | 1.1 | 55.00000000000001 | 0.7547 | -0.5469 | 0.2631 |
| rsi_lo | 1.2 | 60.0 | 0.6562 | -0.6449 | 0.1654 |
| rsi_hi | 0.8 | 56.0 | 0.9072 | -0.5426 | 0.3841 |
| rsi_hi | 0.9 | 63.0 | 0.7474 | -0.3279 | 0.3455 |
| rsi_hi | 1.1 | 77.0 | 0.7192 | -0.4979 | 0.2677 |
| rsi_hi | 1.2 | 84.0 | 0.7045 | -0.5173 | 0.2516 |
| rsi_exit | 0.8 | 32.0 | 0.7689 | -0.4908 | 0.3022 |
| rsi_exit | 0.9 | 36.0 | 0.7689 | -0.4908 | 0.3022 |
| rsi_exit | 1.1 | 44.0 | 0.7728 | -0.4738 | 0.311 |
| rsi_exit | 1.2 | 48.0 | 0.7963 | -0.5866 | 0.2766 |
| ema_fast | 0.8 | 16 | 0.7317 | -0.4578 | 0.2873 |
| ema_fast | 0.9 | 18 | 0.7426 | -0.509 | 0.2787 |
| ema_fast | 1.1 | 22 | 0.7997 | -0.4956 | 0.3222 |
| ema_fast | 1.2 | 24 | 0.8218 | -0.4914 | 0.3381 |
| ema_slow | 0.8 | 40 | 0.7315 | -0.4935 | 0.2737 |
| ema_slow | 0.9 | 45 | 0.823 | -0.473 | 0.3374 |
| ema_slow | 1.1 | 55 | 0.8278 | -0.4818 | 0.3434 |
| ema_slow | 1.2 | 60 | 0.8497 | -0.462 | 0.3622 |
| vwap_n | 0.8 | 16 | 0.5939 | -0.6655 | 0.1251 |
| vwap_n | 0.9 | 18 | 0.6918 | -0.6759 | 0.1876 |
| vwap_n | 1.1 | 22 | 0.7308 | -0.4122 | 0.3063 |
| vwap_n | 1.2 | 24 | 0.7842 | -0.4426 | 0.3265 |
| regime_ema | 0.8 | 160 | 0.4289 | -0.4283 | 0.113 |
| regime_ema | 0.9 | 180 | 0.4991 | -0.5462 | 0.1129 |
| regime_ema | 1.1 | 220 | 0.793 | -0.463 | 0.3329 |
| regime_ema | 1.2 | 240 | 0.551 | -0.2746 | 0.2474 |
| max_names | 0.8 | 16 | 0.6766 | -0.4337 | 0.2719 |
| max_names | 0.9 | 18 | 0.7441 | -0.4234 | 0.3172 |
| max_names | 1.1 | 22 | 0.7623 | -0.4485 | 0.3073 |
| max_names | 1.2 | 24 | 0.7486 | -0.4717 | 0.294 |
| gross | 0.8 | 0.8 | 0.7688 | -0.4898 | 0.3026 |
| gross | 0.9 | 0.9 | 0.7689 | -0.4903 | 0.3024 |

## Walk-forward (expanding window, yearly re-selection over the perturbation grid)

* concatenated test years: Sharpe -0.1654, CAGR 0.0264, maxDD -0.3155

| test year | chosen | Sharpe train | Sharpe test | return test |
|---|---|---|---|---|
| 2021 | ema_slowx1.2 | 1.803 | 0.928 | 0.2013 |
| 2022 | rsi_lox1.2 | 1.41 | -1.641 | -0.1595 |
| 2023 | rsi_hix0.8 | 0.844 | 1.246 | 0.2071 |
| 2024 | rsi_hix0.8 | 0.907 | -0.338 | 0.0019 |
| 2025 | rsi_hix0.8 | 0.646 | -0.749 | -0.0198 |
| 2026 | ema_slowx1.2 | 0.48 | -1.223 | -0.032 |

## Monte Carlo (stationary block bootstrap of daily net returns, full period)

* terminal wealth p5/p50/p95: 0.963/1.769/3.253, P(loss)=0.062, maxDD median -0.2271, 5th-pct worst -0.3842

## Regime split (Nifty vs its 200-day EMA)

* above: 1289 days, Sharpe 0.31 (Nifty 0.558); below: 385 days, Sharpe 0.604 (Nifty -0.071)
