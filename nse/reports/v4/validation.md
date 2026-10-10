# Protocol-v4 validation

Strategy: `strategy.py`; params `{"trend_n": 100, "breadth_cut": 0.6, "rsi_n": 3, "rsi_max": 20.0, "vwap_n": 10, "max_hold": 20, "slots": 12, "use_rsi": true, "use_ema": true, "use_vwap": true}`

Lookahead check: ok=True, max diff 0.0, 33/91 informative

## Windows (Sharpe of daily excess over the repo rate, net of 15 bps)

| | 2017-19 | 2020-23 | 2024-26 | 2017-26 | 2020-26 |
|---|---|---|---|---|---|
| strategy | 0.7887 | 1.1212 | 0.8467 | 0.9564 | 1.0179 |
| niftybees | 0.5484 | 0.6892 | -0.2191 | 0.4247 | 0.3922 |
| nifty50 | 0.4154 | 0.5849 | -0.2810 | 0.3256 | 0.3054 |
| r0_ema_timer | 0.7467 | 1.1242 | 0.1995 | 0.7906 | 0.8077 |

Objective (worst window) 0.7887; span CAGR 0.1226, max DD -0.0662, vol 0.0641, turnover 13.07x/yr, exposure 0.1639, names 5.65; span Sharpe without the 10 best days 0.5590; 9/10 years with a positive sum of daily excess returns (the consistency gate's count; compounded, 8 beat cash, 1 were level with it).

## Costs

| bps | objective | 2017-19 | 2020-23 | 2024-26 | 2017-26 | 2020-26 | CAGR 2017-26 | max DD |
|---|---|---|---|---|---|---|---|---|
| 0 | 1.1328 | 1.1328 | 1.4112 | 1.1661 | 1.2589 | 1.3142 | 0.1448 | -0.0640 |
| 10 | 0.9040 | 0.9040 | 1.2184 | 0.9536 | 1.0578 | 1.1172 | 0.1299 | -0.0655 |
| 15 | 0.7887 | 0.7887 | 1.1212 | 0.8467 | 0.9564 | 1.0179 | 0.1226 | -0.0662 |
| 25 | 0.5571 | 0.5571 | 0.9253 | 0.6320 | 0.7525 | 0.8181 | 0.1080 | -0.0678 |
| 40 | 0.2093 | 0.2093 | 0.6292 | 0.3097 | 0.4450 | 0.5165 | 0.0865 | -0.0701 |

## Calendar years

| year | return | Sharpe | max DD | exposure | NIFTY ret | NIFTY Sharpe | NIFTYBEES ret | cash |
|---|---|---|---|---|---|---|---|---|
| 2017 | 0.1150 | 1.4476 | -0.0236 | 0.2459 | 0.1479 | 1.4518 | 0.1558 | 0.0468 |
| 2018 | 0.0641 | 0.0026 | -0.0416 | 0.068 | 0.0315 | -0.1896 | 0.0482 | 0.0648 |
| 2019 | 0.0952 | 0.8717 | -0.0248 | 0.1424 | 0.1202 | 0.4862 | 0.1361 | 0.0591 |
| 2020 | 0.2180 | 2.4716 | -0.0358 | 0.1175 | 0.1490 | 0.4649 | 0.1542 | 0.0444 |
| 2021 | 0.1084 | 0.6016 | -0.0662 | 0.3327 | 0.2412 | 1.2223 | 0.2597 | 0.0408 |
| 2022 | 0.1124 | 0.8908 | -0.0331 | 0.1292 | 0.0433 | 0.0483 | 0.0546 | 0.0501 |
| 2023 | 0.1275 | 1.5559 | -0.0145 | 0.1633 | 0.2003 | 1.2955 | 0.2101 | 0.0667 |
| 2024 | 0.1708 | 1.1283 | -0.0570 | 0.2629 | 0.0880 | 0.2081 | 0.1036 | 0.0677 |
| 2025 | 0.1049 | 1.5015 | -0.0143 | 0.1005 | 0.1051 | 0.4217 | 0.1166 | 0.0596 |
| 2026 | 0.0319 | -0.5297 | -0.0108 | 0.0772 | -0.1381 | -1.593 | -0.1285 | 0.0414 |

## Sampling error

* pre: Sharpe 0.7887 (t 1.29), 95 % block-bootstrap CI [-0.1722, 1.6604], share of resamples with Sharpe > 0 0.9445 (not the probability that the true Sharpe is positive), PSR(0) 0.9104
* is: Sharpe 1.1212 (t 2.22), 95 % block-bootstrap CI [0.4572, 1.9053], share of resamples with Sharpe > 0 0.998 (not the probability that the true Sharpe is positive), PSR(0) 0.9878
* oos: Sharpe 0.8467 (t 1.39), 95 % block-bootstrap CI [-0.2394, 1.7626], share of resamples with Sharpe > 0 0.937 (not the probability that the true Sharpe is positive), PSR(0) 0.9269
* span: Sharpe 0.9564 (t 2.92), 95 % block-bootstrap CI [0.4744, 1.4432], share of resamples with Sharpe > 0 1.0 (not the probability that the true Sharpe is positive), PSR(0) 0.9986; deflated Sharpe over N=3490 trials (SR0 0.7225): DSR 0.7676
* full: Sharpe 1.0179 (t 2.62), 95 % block-bootstrap CI [0.4555, 1.5963], share of resamples with Sharpe > 0 1.0 (not the probability that the true Sharpe is positive), PSR(0) 0.9963

## Parameter sensitivity (one at a time, +-10 / 20 %)

* 26 perturbations: objective median 0.754 (base 0.7887), min 0.5647, 1.0 positive, 0.654 within 0.1 of base; base ranks #8

| param | factor | value | objective | 2017-19 | 2020-23 | 2024-26 | 2017-26 |
|---|---|---|---|---|---|---|---|
| base | 1.0 | None | 0.7887 | 0.7887 | 1.1212 | 0.8467 | 0.9564 |
| trend_n | 0.8 | 80 | 0.7720 | 0.7846 | 1.1044 | 0.7720 | 0.9282 |
| trend_n | 0.9 | 90 | 0.7309 | 0.8960 | 1.1294 | 0.7309 | 0.9549 |
| trend_n | 1.1 | 110 | 0.8070 | 0.9672 | 1.0991 | 0.8070 | 0.9723 |
| trend_n | 1.2 | 120 | 0.7830 | 0.7830 | 1.0976 | 0.8021 | 0.9318 |
| breadth_cut | 0.8 | 0.48 | 0.5783 | 0.5783 | 0.9018 | 1.0565 | 0.8572 |
| breadth_cut | 0.9 | 0.54 | 0.5647 | 0.5647 | 0.8654 | 0.917 | 0.7974 |
| breadth_cut | 1.1 | 0.66 | 0.6532 | 0.8289 | 0.9742 | 0.6532 | 0.8411 |
| breadth_cut | 1.2 | 0.72 | 0.6839 | 1.0840 | 0.6839 | 0.6928 | 0.7518 |
| rsi_n | 0.8 | 2 | 0.5836 | 0.5836 | 0.8222 | 1.1510 | 0.8473 |
| rsi_n | 1.2 | 4 | 0.7702 | 1.3957 | 1.2542 | 0.7702 | 1.1322 |
| rsi_max | 0.8 | 16.0 | 0.7531 | 1.0880 | 1.1373 | 0.7531 | 1.0023 |
| rsi_max | 0.9 | 18.0 | 0.8228 | 1.1141 | 1.1112 | 0.8228 | 1.0119 |
| rsi_max | 1.1 | 22.0 | 0.9277 | 0.9277 | 1.0465 | 1.0094 | 0.9895 |
| rsi_max | 1.2 | 24.0 | 0.9253 | 0.9253 | 1.0320 | 0.9391 | 0.9662 |
| vwap_n | 0.8 | 8 | 0.7702 | 0.7702 | 0.9659 | 0.8277 | 0.8635 |
| vwap_n | 0.9 | 9 | 0.6770 | 0.6770 | 1.0723 | 1.0291 | 0.9546 |
| vwap_n | 1.1 | 11 | 0.7190 | 0.8481 | 0.8901 | 0.7190 | 0.8198 |
| vwap_n | 1.2 | 12 | 0.6254 | 0.6254 | 0.8239 | 0.6624 | 0.7207 |
| max_hold | 0.8 | 16 | 0.7548 | 0.7548 | 1.1494 | 0.8572 | 0.9664 |
| max_hold | 0.9 | 18 | 0.7490 | 0.7490 | 1.1002 | 0.8277 | 0.9326 |
| max_hold | 1.1 | 22 | 0.7795 | 0.7898 | 1.1194 | 0.7795 | 0.9387 |
| max_hold | 1.2 | 24 | 0.7403 | 0.7725 | 1.1059 | 0.7403 | 0.9182 |
| slots | 0.8 | 10 | 0.8429 | 0.9341 | 1.1419 | 0.8429 | 0.9972 |
| slots | 0.9 | 11 | 0.8074 | 0.9168 | 1.1136 | 0.8074 | 0.9693 |
| slots | 1.1 | 13 | 0.7401 | 0.7401 | 1.1379 | 0.7995 | 0.9427 |
| slots | 1.2 | 14 | 0.8066 | 0.8066 | 1.0945 | 0.8347 | 0.9431 |

## Walk-forward (expanding window from 2017-04, yearly re-selection over the perturbation grid)

* concatenated 2019-2026: Sharpe 1.0132 (2024-26: 0.7702), CAGR 0.1099, max DD -0.0520

| test year | chosen | Sharpe train | Sharpe test | return test |
|---|---|---|---|---|
| 2019 | rsi_maxx0.8 | 1.455 | 0.475 | 0.0788 |
| 2020 | rsi_nx1.2 | 1.396 | 2.956 | 0.1794 |
| 2021 | rsi_nx1.2 | 1.872 | 1.175 | 0.1545 |
| 2022 | rsi_nx1.2 | 1.432 | 0.561 | 0.0723 |
| 2023 | rsi_nx1.2 | 1.308 | 1.013 | 0.095 |
| 2024 | rsi_nx1.2 | 1.263 | 0.698 | 0.1148 |
| 2025 | rsi_nx1.2 | 1.16 | 1.76 | 0.0917 |
| 2026 | rsi_nx1.2 | 1.156 | 1.5 | 0.0535 |

## Monte Carlo and regimes

* terminal wealth over 2017-26 p5/p50/p95 2.2830/2.9385/3.8040, P(loss) 0.0, max DD median -0.0662, 5th-percentile worst -0.0878
* NIFTY above its EMA200: 1871 days, Sharpe 1.0595 (NIFTY 0.4719); below: 477 days, Sharpe 0.3285 (NIFTY 0.0932)

## Protocol robustness checks

* plateau4 against `v4/r0_ema_timer.py`: ok=True, minimum neighbour objective 0.5783 (strategy 0.7887, incumbent 0.1995)
* joint +-20 % perturbation of every parameter (60 draws): mean 0.5969, median 0.6123, p10 0.3698, min 0.1894; 98% of draws above R0's objective
* each window with its own best days removed (worst window):
  * strategy: none 0.7887, 5 days 0.0616 (0.0616 / 0.7107 / 0.1425), 10 days -0.5512
  * r0_ema_timer: none 0.1995, 5 days -0.3914 (0.3538 / 0.8597 / -0.3914), 10 days -0.7973
  * niftybees: none -0.2191, 5 days -0.7248 (0.0390 / 0.3692 / -0.7248), 10 days -1.0734
* the 10 best days carry 48% of the 2017-26 excess return
