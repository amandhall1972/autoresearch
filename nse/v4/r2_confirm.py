"""
EMA trend timer with an RSI overbought brake and a VWAP washout re-entry (protocol v4.1, round 2,
lens: index-level RSI and VWAP confirmation).

WHAT IT HOLDS
Cash (credited with the repo rate by the harness) or "the market": `etf_share` (0.9) of the invested amount in
NIFTYBEES and the rest spread equally over the point-in-time Nifty 50 members. The split never changes; only
the size of the market position does, and it is graded 0 / `half` / 1. The member sleeve is there because the
brief asks for at least 5 names on average; at 10 % it costs the least of the sleeves tried (an equal-weight
Nifty 50 lagged the cap-weighted index in 2018-19).

HOW MUCH OF THE MARKET IT HOLDS (decided at the close of day t, traded at the next open)
Every signal is read on the index itself (NIFTY 50 closes for EMA and RSI, NIFTYBEES bars for the VWAP).

1. Trend regime (EMA) - R0 unchanged. The NIFTY's EMA(fast = 20) above its EMA(slow = 50) is an uptrend.
2. Overbought brake (RSI). Inside the uptrend the position is cut to `half` while the NIFTY's Wilder
   RSI(rsi_n = 14) closes above 100 - rsi_band (70), and restored when it closes back at or below 70.
   Why: inside the uptrend, days with the index's RSI above 70 were followed by negative average next-day
   excess returns in 2017-19 and 2024-26 (positive in 2020-23, where RSI stayed above 70 for weeks in strong
   trends), so the brake halves rather than exits. This rule is the main lever on 2024-26.
3. Washout re-entry (VWAP + RSI). Outside the uptrend the strategy is normally in cash, as R0. It takes a `half`
   position when NIFTYBEES closes more than `wash_depth` (5 %) below its `wash_n` (100)-session rolling VWAP
   (the average buyer of the last five months is deep under water: a washout) while RSI(14) is above
   rsi_band (30), i.e. the selling momentum is no longer in free fall. The half position is kept until the EMA
   regime turns up again (rule 1 takes over) or the RSI closes back below 30 (the fall resumed: stop). After a
   stop it can re-enter on the same washout condition.
   Why: R0's slowest decisions are its re-entries after V-shaped recoveries (August-September 2019, April 2020,
   March-April 2025, April 2026): the EMA pair turns up only weeks after the low. A washout below the
   volume-weighted cost of the recent past, once the RSI has left its oversold extreme, is where forced selling
   has run out; the RSI condition keeps the rule out of crashes in progress (March 2020).

WHY IT SHOULD HOLD ACROSS REGIMES - AND WHERE IT IS FRAGILE
* The EMA regime is the one effect that held in every research window (round 1); it is not touched.
* The washout re-entry adds Sharpe in all three windows (2019, 2020, 2022, 2025-26) and lifts the span Sharpe
  without its 10 best days, so it is not one event day; but it acts in only a handful of episodes, and its
  2024-26 contribution depends on whether the March 2025 re-entry fires (a 1 % change in the depth or VWAP
  length switches it off). The 2024-26 Sharpe, the binding window, is therefore NOT on a plateau: see
  plateau.txt.
* The RSI brake is a trade-off on its own (it costs 2020-23 about 0.2 of Sharpe); the washout rule pays that
  back in 2020-23, which is why the pair, not either rule alone, passes the window gate.

Causality: EMA, RSI and VWAP use bars up to and including day t; the state machine only looks backwards.
Warm-up NaNs read as "no trend", "not overbought", "not a washout". The longest lookback is the 100-session
VWAP (limit 200).

Brief-compliance switches: use_ema=False makes the regime always true; use_rsi=False removes the brake, the
RSI condition of the washout entry and the RSI stop; use_vwap=False removes the washout re-entry rule.
"""
import numpy as np
import pandas as pd

import indicators as I
from prepare import ETF_TICKER

PARAMS = dict(
    fast=20,            # rule 1: EMA(fast) > EMA(slow) on the NIFTY 50
    slow=50,
    rsi_n=14,           # rules 2-3: Wilder RSI length on the NIFTY 50
    rsi_band=30,        # rule 3 floor / stop at rsi_band, rule 2 brake above 100 - rsi_band
    wash_n=100,         # rule 3: rolling VWAP length of NIFTYBEES (sessions)
    wash_depth=0.05,    # rule 3: washout = close more than this fraction below that VWAP
    half=0.5,           # rules 2-3: the intermediate exposure step (0 / half / 1)
    etf_share=0.9,      # share of the invested amount in NIFTYBEES (rest: equal-weight members)
    use_rsi=True, use_ema=True, use_vwap=True,
)


def exposure(md) -> pd.Series:
    """Target fraction of the portfolio in the market per day: 0, half or 1."""
    p = PARAMS
    cal = md.calendar
    n = len(cal)
    ix = md.index_close[["NIFTY50"]]
    etf = [ETF_TICKER]

    if p["use_ema"]:
        regime = (I.ema(ix, p["fast"]) > I.ema(ix, p["slow"])).iloc[:, 0].values
    else:
        regime = np.ones(n, bool)

    if p["use_rsi"]:
        rsi = I.rsi(ix, p["rsi_n"]).iloc[:, 0]
        overbought = (rsi > 100 - p["rsi_band"]).values
        alive = (rsi > p["rsi_band"]).values
        collapse = (rsi < p["rsi_band"]).values
    else:
        overbought = np.zeros(n, bool)
        alive = np.ones(n, bool)
        collapse = np.zeros(n, bool)

    if p["use_vwap"]:
        vw = I.rolling_vwap(md.high[etf], md.low[etf], md.close[etf], md.volume[etf], p["wash_n"]).iloc[:, 0]
        washout = (md.close[ETF_TICKER] < vw * (1.0 - p["wash_depth"])).values
    else:
        washout = np.zeros(n, bool)

    x = np.zeros(n)
    held = False                      # rule 3 position outside the regime
    for t in range(n):
        if regime[t]:
            held = False
            x[t] = p["half"] if overbought[t] else 1.0
        else:
            if held and collapse[t]:
                held = False
            elif (not held) and washout[t] and alive[t]:
                held = True
            x[t] = p["half"] if held else 0.0
    return pd.Series(x, index=cal)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    x = exposure(md)
    names = [t for t in md.tickers if t != ETF_TICKER]
    mem = md.member[names].astype(float)
    basket = mem.div(mem.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[names] = basket.mul(x * (1.0 - p["etf_share"]), axis=0)
    W[ETF_TICKER] = x * p["etf_share"]
    return W.fillna(0.0)
