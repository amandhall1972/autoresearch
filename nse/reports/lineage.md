| stage | commit | sharpe_is | sharpe_is_h1 | sharpe_is_h2 | sharpe_oos | sharpe_full | turnover_yr_is | exposure_is | description |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 3659aab | 0.7689 | 1.2075 | 0.2065 | -0.4908 | 0.3022 | 37.38 | 0.821 | baseline: EMA20>EMA50 & close>EMA20 & close>VWAP20 & 50<RSI14<70, hold while close>EMA50 & RSI>40, Nifty>EMA200 regime, top20 equal weight (post-review harness) |
| final | a1edb7e | 1.8761 | 1.9678 | 1.8888 | -1.6006 | 0.4247 | 25.43 | 0.484 | [r1:rsi] (lens best) KEEP v3 (run.py, lookahead ok): also stand aside while Nifty RSI14 > 80, on v2. E43 robustness: the cutoff improves all 10 regime settings  |
