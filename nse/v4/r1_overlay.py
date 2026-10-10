"""
Trend regime with an overbought pause: an exposure overlay on a near-index core (protocol v4, lens: exposure and
risk overlay).

The strategy holds a near-index core (NIFTYBEES and an equal-weight basket of all Nifty 50 members) when the
index is in a confirmed uptrend. It holds cash, which earns the repo rate, when the uptrend is missing and also
for a while after the index gets overbought. Every rule is on the index or on NIFTYBEES. There is no
stock-picking signal.

Rules, decided at the close of day t and executed by the harness at the open of t+1:

1. Trend regime (EMA). The NIFTY 50 is in a confirmed uptrend when EMA(20) > EMA(50) > EMA(100), i.e. the
   short, medium and long trends all point up. Outside this regime the strategy is in cash.
   Why it should hold across regimes: the aligned state is the only one whose next-day returns (net of
   the repo rate) were positive in all three research windows: annualised Sharpe +0.43 in 2017-19, +1.15
   in 2020-23 and +0.52 in 2024-26, on a 50/50 NIFTYBEES/equal-weight blend. The states that were not aligned were
   negative on balance in 2017-19 and 2024-26. They were positive in 2020-23 only because of the V-shaped
   2020 rebound. Most of the damage in every window came from falling or trendless stretches (2018, 2022,
   2026), and sitting in cash removes it at the cost of late entries. The long leg (EMA50 > EMA100) adds
   about 0.12 of objective over EMA20 > EMA50 alone, because it keeps out short rallies inside a falling
   market.

2. Overbought pause (RSI). When the index's Wilder RSI(14) closes above 70, the market is stretched.
   The strategy then goes to cash, even inside the regime.
   Why: inside the aligned regime, an index RSI above 70 was followed on average by poor next-day
   returns (same 50/50 blend) in 2017-19 (Sharpe -2.3 over 66 days) and 2024-26 (-4.1 over 32 days), as the stretch
   tends to unwind. In 2020-23, where momentum kept running (+1.6 over 140 days), the pause costs some
   return. The samples are small, so this is the most fragile rule: a faster trigger (RSI 65, or
   RSI length 11) still beats the incumbent but loses about 0.3 of objective. The rule takes profit into
   strength instead of holding through the giveback.

3. Re-entry at volume-weighted cost (VWAP). The pause lasts until NIFTYBEES closes below its 20-day
   rolling VWAP: the price has come back to what the last month's buyers paid on average, so the stretch is
   worked off. The strategy then rejoins the trend if rule 1 still holds. If RSI(14) is above 70 on that
   same day, the pause continues.
   Why: VWAP is the recent average cost of the people who actually traded. A pullback to it is a natural
   "fair value" level in an uptrend, and it gives the pause a hysteresis: the strategy does not flip in and
   out around RSI 70 every day (span turnover about 8.5x a year). Ending the pause when the RSI falls back
   below 50 instead lowered the objective from 0.58 to 0.30.

4. Core (when invested). 75 % of the capital goes to NIFTYBEES. The other 25 % is split equally across
   every point-in-time Nifty 50 member. A member with no fresh bar that day keeps its slot, which the harness
   carries, so the book never needs the harness's over-gross rescale.
   Why: NIFTYBEES carries the cap-weighted index, which held up best when the market was narrow (2018-19).
   The equal-weight sleeve spreads the book over about 50 names at a low cost and takes part in broad
   rallies (2021, 2023-24). Filtering the stocks by their own EMA, VWAP or RSI was tested and never helped:
   it lowered the objective every time, and lowered most windows: the edge is in the timing, not in the
   selection. Volatility targeting and an index-drawdown brake on top of these rules, and breadth gates on
   earlier versions, also lowered the objective.

There is no leverage and no shorting. The gross weight is 1 when invested and 0 otherwise. Every indicator
uses data up to the close of day t only. The longest lookback is the 100-session EMA, inside the
200-session limit.
"""
import numpy as np
import pandas as pd

import indicators as I
from prepare import ETF_TICKER

PARAMS = dict(
    ema_fast=20,      # rule 1: EMA(fast) > EMA(mid) > EMA(slow) on the NIFTY 50
    ema_mid=50,
    ema_slow=100,
    rsi_n=14,         # rule 2: Wilder RSI length on the NIFTY 50
    rsi_ob=70,        # rule 2: overbought level that starts the pause
    vwap_n=20,        # rule 3: NIFTYBEES rolling VWAP length that ends the pause
    etf_share=0.75,   # rule 4: share of the invested capital in NIFTYBEES (the rest: equal-weight members)
)


def compute_target_weights(md):
    p = PARAMS
    cal, tick = md.calendar, md.tickers
    stocks = [t for t in tick if t != ETF_TICKER]
    nifty = md.index_close[["NIFTY50"]]

    # 1. trend regime: EMA(fast) > EMA(mid) > EMA(slow)
    e_f = I.ema(nifty, p["ema_fast"]).iloc[:, 0]
    e_m = I.ema(nifty, p["ema_mid"]).iloc[:, 0]
    e_s = I.ema(nifty, p["ema_slow"]).iloc[:, 0]
    regime = ((e_f > e_m) & (e_m > e_s)).fillna(False)

    # 2-3. overbought pause: starts when RSI > rsi_ob, ends when NIFTYBEES closes below its rolling VWAP
    rsi = I.rsi(nifty, p["rsi_n"]).iloc[:, 0]
    etf = [ETF_TICKER]
    vwap = I.rolling_vwap(md.high[etf], md.low[etf], md.close[etf], md.volume[etf], p["vwap_n"]).iloc[:, 0]
    state = pd.Series(np.nan, index=cal)
    state[md.close[ETF_TICKER] < vwap] = 0.0     # back at volume-weighted cost: pause over
    state[rsi > p["rsi_ob"]] = 1.0               # overbought: pause (wins if both happen on one day)
    paused = state.ffill().fillna(0.0) > 0.5

    invested = (regime & ~paused).astype(float)

    # 4. core: NIFTYBEES plus an equal-weight sleeve of all point-in-time members
    members = md.member[stocks].astype(float)    # a member without a fresh bar keeps its slot (the harness carries it)
    sleeve = members.div(members.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)

    W = pd.DataFrame(0.0, index=cal, columns=tick)
    W[stocks] = sleeve.mul(invested * (1.0 - p["etf_share"]), axis=0)
    W[ETF_TICKER] = invested - W[stocks].sum(axis=1)
    return W
