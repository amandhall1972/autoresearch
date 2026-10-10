"""
Trend timer with a breadth-washout early entry (protocol v4.1, round 2, lens: breadth and participation).

The strategy is the plain index trend timer R0 plus ONE breadth rule that only acts while R0 is in cash. Two
breadth gauges are computed every day from the point-in-time Nifty 50 members only (md.member, NIFTYBEES
excluded), each as the share of the members with a valid reading:

  long-term breadth  L_t = share of members whose own Wilder RSI(`long` = 100) is above 50, i.e. whose average
                           gain over roughly the last 100 sessions exceeds their average loss (a stock in a
                           long-term uptrend);
  short-term breadth S_t = share of members whose close is above their own rolling `fast`(=20)-session VWAP, i.e.
                           above the average price paid for them over the last month.

Rules (decided at the close of day t, executed by the harness at the open of t+1):

1. Trend (EMA) - unchanged R0. While the NIFTY 50's EMA(`fast` = 20) is above its EMA(`slow` = 50), hold
   NIFTYBEES with the whole book. Why: the index's own medium-term trend is the one effect that held in every
   research window (round 1); being in cash while it points down avoids most of the large drawdowns.

2. Washout early entry (RSI + VWAP breadth). While the trend of rule 1 is down, the book is in cash (repo rate)
   unless a breadth washout is followed by a breadth thrust:
     * washout (RSI): L_t < `wash` (0.4) - at least 60 % of the members are in long-term downtrends, so the
       selling has spread from the index leaders to the whole market;
     * thrust (VWAP): S_t >= `thrust` (0.5) - a majority of the members are back above the price their buyers paid
       on average over the last month.
   On the first day both hold (trend still down), the strategy buys the equal-weight basket of the current
   members (1/N each) and holds it until rule 1's trend turns up, when the basket is exchanged for NIFTYBEES (from
   then on it is plain R0 again). There is no other exit: the trade is an early entry into the next index uptrend.
   Why it should hold across regimes: a decline that has pushed most stocks into long-term downtrends is a mature,
   market-wide one (forced and broad selling, not a narrow rally rolling over), and a majority of stocks
   reclaiming their one-month VWAP is the first sign that buyers are absorbing it. The index EMA cross confirms
   such recoveries 3-12 weeks late; the breadth pair confirms them early. On 2017-2026 the rule traded seven
   times (Nov 2018, Feb 2019, Aug 2019, Apr 2020, Jun 2022, Jan 2025, Apr 2026: at least one in every research
   window) and each trade beat cash, by 0.9 % to 15.7 % before costs (about 0.45 % per trade incl. the handover
   switch). The equal-weight basket is used because the trade is a breadth trade: what washed out and is
   rebounding is the average member, not the cap-weighted leaders. (This is also what gives the book its >= 5
   names on average.)
   Caveats: the gain over R0 comes from seven bottoming trades, so it sits on rebound days (the 10 best
   difference days carry almost all of it); the 2017-19 gain does not survive removing that window's 5 best days
   (the Sep 2019 tax-cut rally), the 2020-23 and 2024-26 gains do. The trade has no stop: a failed thrust is held
   until the index trend turns up (adding a new-50-day-low stop cost 2017-19 more than 0.10 and was dropped).

What the breadth lens tried and dropped (all logged in experiments.tsv): requiring breadth to confirm R0's
entries, exiting R0 early in narrow rallies, re-entering on breadth recovery without a washout, dual-trend
hysteresis with an equal-weight index or the advance-decline line, contrarian EW/ETF tilts by breadth, and
overbought brakes: each was a trade-off between windows (mostly gaining 2017-19 or 2024-26 at the cost of
2020-23, or the reverse), not a Pareto improvement on R0.

Family switches (protocol v4.1 rule 2; not counted as parameters). Each switch replaces its family's conditions by
the neutral "always true":
  use_ema=False  : rule 1's trend is always up (the strategy holds NIFTYBEES throughout);
  use_rsi=False  : the washout condition always holds (any thrust while the trend is down starts a trade);
  use_vwap=False : the thrust condition always holds (the trade starts at the washout itself).

Causality and limits: EMA, Wilder RSI and rolling VWAP use data up to and including day t only; membership is the
point-in-time md.member of day t; members without a valid reading are left out of the share that day. The longest
lookback is the 100-session RSI (inside the 200-session limit); warm-up NaNs give no washout. Long-only, gross <= 1.
"""
import numpy as np
import pandas as pd

import indicators as I
from prepare import ETF_TICKER

PARAMS = dict(
    fast=20,      # rule 1: NIFTY EMA(fast) > EMA(slow); rule 2: short-term breadth = share of members above VWAP(fast)
    slow=50,      # rule 1
    long=100,     # rule 2: long-term breadth = share of members with Wilder RSI(long) > 50
    wash=0.4,     # rule 2: washout when the long-term breadth is below this share
    thrust=0.5,   # rule 2: thrust when the short-term breadth is at least this share
    use_rsi=True,
    use_ema=True,
    use_vwap=True,
)


def _share(cond: pd.DataFrame, valid: pd.DataFrame, mem: pd.DataFrame) -> pd.Series:
    """Share of the point-in-time members with a valid reading for which `cond` holds (NaN if none is valid)."""
    base = mem & valid
    n = base.sum(axis=1)
    return ((cond & base).sum(axis=1) / n.replace(0, np.nan)).astype(float)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    cal = md.calendar
    stocks = [t for t in md.tickers if t != ETF_TICKER]
    mem = md.member[stocks].astype(bool)
    c, h, l, v = md.close[stocks], md.high[stocks], md.low[stocks], md.volume[stocks]

    # rule 1: index trend (EMA)
    if p["use_ema"]:
        nifty = md.index_close[["NIFTY50"]]
        trend = (I.ema(nifty, p["fast"]) > I.ema(nifty, p["slow"]))["NIFTY50"].values
    else:
        trend = np.ones(len(cal), dtype=bool)

    # rule 2: washout (RSI long-term breadth) and thrust (VWAP short-term breadth)
    if p["use_rsi"]:
        r = I.rsi(c, p["long"])
        L = _share(r > 50, r.notna(), mem)
        washout = (L < p["wash"]).values            # NaN (warm-up) compares False: no washout
    else:
        washout = np.ones(len(cal), dtype=bool)
    if p["use_vwap"]:
        vw = I.rolling_vwap(h, l, c, v, p["fast"])
        S = _share(c > vw, vw.notna(), mem)
        thrust = (S >= p["thrust"]).values
    else:
        thrust = np.ones(len(cal), dtype=bool)

    early = np.zeros(len(cal), dtype=bool)
    state = False
    for k in range(len(cal)):
        if trend[k]:
            state = False                           # handover to rule 1
        elif not state and washout[k] and thrust[k]:
            state = True
        early[k] = state

    W = pd.DataFrame(0.0, index=cal, columns=md.tickers)
    W[ETF_TICKER] = trend.astype(float)
    memf = mem.astype(float)
    ew = memf.div(memf.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    W[stocks] = ew.mul(pd.Series(early.astype(float), index=cal), axis=0)
    return W
