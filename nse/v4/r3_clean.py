"""
R0 trend core plus a pullback sleeve, cleaned (protocol v4.2, round 3, lens: clean and harden the pullback
sleeve of v4/r2_pullbackcombo.py).

The book has two fixed parts that never compete for the same capital. Every weight is decided with data up
to the close of day t and traded by the harness at the open of t+1; capital neither part uses stays in cash
and earns the repo rate.

RULES
1. Trend core (EMA), 1 - sleeve_share = 20 % of the book. While the NIFTY 50's EMA(fast = 20) is above its
   EMA(slow = 50), the core holds its 20 % in NIFTYBEES; otherwise it is in cash. This is R0 unchanged.
2. Pullback trigger (EMA + VWAP). The sleeve (sleeve_share = 80 % of the book) may buy only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad: among the members in their own uptrend (close above their EMA(100)), more
        than breadth_cut (60 %) close below their rolling vwap_n-day (10) VWAP.
3. Name filter (EMA + RSI). On such a day a member is eligible if it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max (20).
4. Selection and size (VWAP). Free slots (slots = 12; one slot = sleeve_share / 12 = 6.7 % of the book) are
   filled with the eligible names that sit deepest below their 10-day VWAP (lowest close / VWAP10 - 1).
   A name keeps its slot weight until it is sold.
5. Exit (VWAP + time). A held name is sold when it closes back above its 10-day VWAP (the dip has been bought
   back) or after max_hold (20) sessions; names that leave the index are sold.

WHAT THE CLEAN-UP CHANGED (from v4/r2_pullbackcombo.py; objective 0.7528 -> 0.7452)
* Deleted the entry test "not already above VWAP10": it never binds (RSI(3) < 20 implies a close below the
  10-day VWAP on every eligible day; identical weights with and without it).
* Deleted the equal-weight sliver (25 % of the core spread over the members). The core is now NIFTYBEES
  only. Effect: pre +0.09, is -0.01, oos +0.003 (objective 0.7528 -> 0.7556). Diversification is met
  honestly by protocol v4.2's second test: NIFTYBEES (one unit of all 50 members) is 71 % of the gross on
  invested days on average (etf_share_span 0.7085 >= 0.5); the book holds 3.6 names on average.
* Replaced the breadth size ramp (from zero at 50 % to full at 70 %) with a hard cut at 60 %, which removes
  one parameter. The ramp bought 0.0104 of objective (0.7556 vs 0.7452), less than the 0.05 parsimony bar
  and less than the 0.02 simplification bar. Both pass plateau4 (min neighbour 0.6077 vs 0.6075). Under
  the joint +-20 % perturbation of every parameter (60 draws, seed 1) the hard cut is better: mean 0.617 vs
  0.595, p10 0.444 vs 0.378, min 0.224 vs 0.003. The cut also lifts is (1.233 vs 1.093), the span Sharpe
  without its 10 best days (0.630 vs 0.563) and is with its own 5 best days removed (0.846 vs 0.706).
  Honest caveat: the breadth cut is the most sensitive dial. Cuts 0.48 / 0.5 / 0.55 / 0.65 / 0.7 / 0.72 give
  objectives 0.697 / 0.614 / 0.691 / 0.676 / 0.519 / 0.623. A higher cut helps pre and hurts oos (about
  +0.28 and -0.15 per +20 %).

WHAT EACH RULE CONTRIBUTES (window Sharpe of the book minus the book without the rule: pre / is / oos;
the book scores 0.894 / 1.233 / 0.745)
* trend core (R0, 20 %):            +0.10 / +0.11 / -0.10  (objective -0.046; kept: see robustness below)
* pullback sleeve as a whole:       +0.15 / +0.11 / +0.55
* index filter NIFTY > EMA100:      -0.09 / +0.84 / +0.38
* stock uptrend filter > EMA100:    +0.88 / +0.97 / +0.11
* breadth trigger:                  +1.24 / +0.91 / +0.15
* breadth counted over uptrend
  names, not all members:           +0.30 / +0.24 / +0.01
* RSI(3) < 20 filter:               +0.13 / +0.34 / -0.20  (objective -0.019; kept: see below)
* VWAP-reclaim exit:                +0.26 / +0.02 / +0.70
* time stop:                        -0.02 / +0.03 / +0.21
* VWAP-depth ranking vs equal split
  of the sleeve over all eligible
  names (no ranking):               +0.05 / +0.04 / -0.005 (vs random ranking, mean of 10 seeds:
                                    +0.03 / +0.05 / +0.01)
Two rules lower the binding oos window and are kept on purpose. The core and the RSI filter each move
Sharpe from 2024-26 into 2017-23, and they are what keeps the book from leaning on a few rebound days:
* without the core, each window's Sharpe with its own 5 best days removed falls to 0.06 (pre) and 0.71
  (is), against 0.22 and 0.85 with it; drop10 falls from 0.630 to 0.560;
* without the RSI filter, is with its 5 best days removed falls from 0.85 to 0.49 and drop10 from 0.630 to
  0.472; use_rsi also needs it to bind.
The ranking adds little at the point (it stays inside the random-ranking range of 0.63-0.80). It is kept
because, over 50 paired joint +-20 % draws, it beats the equal split by 0.037 +- 0.011 of objective.

ROBUSTNESS (the two weaknesses the round-2 audit named)
* Event days. With each window's own 5 best days removed: pre 0.222 (R0 0.354), is 0.846 (R0 0.860),
  oos 0.055 (R0 -0.391). With its own 10 best days removed: pre -0.289 (R0 0.072), is 0.514 (R0 0.648),
  oos -0.537 (R0 -0.797). With the NIFTY's 5 best days of each window removed, the book beats R0 in every
  window: 0.706 / 1.134 / 0.425 vs 0.619 / 1.048 / -0.121. The is gap on 5 days shrank from 0.15 under the
  ramp to 0.014. The pre gap remains: 75 % of
  the sleeve's 2017-19 P&L comes from 5 of its 43 entry episodes, all of them market-wide rebound days
  with the sleeve loaded. That concentration is the effect itself, not an artefact. Buying one close later,
  after the first up close, wipes out the edge (objective -0.07). Rules that fixed pre on event days
  (stricter RSI: rsi_max 12-14, or rsi_n 4) sit on a cliff in 2024-26 and fail plateau4 (rsi_n 4 -> 0.13,
  rsi_max 11.2 -> 0.47). A larger core fixes pre only near sleeve_share 0.5, which gives up most of the oos
  gain. Low-volatility ranking, RSI ranking, RSI-weighted size, a trend-break exit and an index-break exit
  did not help.
* Joint perturbation: every numeric parameter, R0's fast/slow included, gets an independent uniform +-20 %
  move, rounded for integers. On this file with 60 draws (seed 11) the objective has mean 0.5775, p10
  0.4205 and min 0.1659; 98 % of draws beat R0's 0.1995. Pooled over the 170 draws of this
  configuration: mean 0.598, p10 0.411, min 0.166. Holding fast/slow fixed (50 draws, seed 12) gives mean
  0.6016, p10 0.4129 and min 0.1241. Against the ramp version on the same 60 draws (seed 1) the hard cut
  scores mean 0.617 vs 0.595, p10 0.444 vs 0.378 and min 0.224 vs 0.003, so the tail improved. The mean
  premium (about 0.15 below the point) did not shrink. It is structural: the point sits on the pre = oos
  ridge of a max-min objective. Stricter entry (breadth_cut up, rsi_max down, rsi_n up) starves 2024-26
  of trades and oos binds in 118 of 170 draws. Looser entry lets 2017-19 buy falling knives and pre binds
  in 47 of 170.
* Costs: objective 0.5593 at 25 bps one-way and 0.2805 at 40 bps (R0: 0.1675 and 0.1197); turnover 11.3x a
  year.

KNOWN WEAK POINTS
* 2018 (Sharpe 0.05) and 2026 (-1.19) are weak. 2026 is negative for every strategy tried, and the core
  is most of that loss (core-free book: -0.53).
* The sleeve's edge is the immediate rebound after market-wide selling. A stretch with few sharp
  rebounds would leave the book close to R0 at 20 % plus cash.

Long-only, gross at most 1. Longest lookback is 100 sessions (EMA100), inside the 200-session limit.
Everything is causal: EMA, RSI and rolling VWAP use bars up to day t, and the slot book only looks back.

Brief-compliance switches. use_ema=False switches every EMA rule off: the core is always on, there are no
index or stock trend filters, and breadth is counted over all live members. use_rsi=False drops the RSI(3)
filter. use_vwap=False drops the breadth trigger (the sleeve may buy every day), the VWAP-depth ranking
(names are then taken in ticker order) and the VWAP-reclaim exit (only the time stop is left).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    fast=20,             # core: NIFTY EMA(fast) > EMA(slow) (R0)
    slow=50,
    sleeve_share=0.8,    # share of the book given to the pullback sleeve (the core gets the rest)
    trend_n=100,         # EMA length of the uptrend filters (index and stocks)
    breadth_cut=0.6,     # trigger: share of uptrend members below their VWAP must exceed this
    rsi_n=3,             # stock Wilder RSI length
    rsi_max=20.0,        # stock is oversold when RSI(rsi_n) < rsi_max
    vwap_n=10,           # rolling VWAP length: breadth, ranking depth and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=12,            # sleeve slots; each held name weighs sleeve_share / slots
    use_rsi=True, use_ema=True, use_vwap=True,
)


def _pullback_sleeve(md, p, tick):
    """Weights of the pullback sleeve as a fraction of the sleeve's own capital (each held name 1/slots)."""
    c = md.close[tick]
    live = md.member[tick] & md.tradable[tick]
    vw = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["vwap_n"])
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        uptrend = c > ema(c, p["trend_n"])
        ix_up = (ix > ema(ix, p["trend_n"]))["NIFTY50"]
    else:
        uptrend = pd.DataFrame(True, index=c.index, columns=tick)
        ix_up = pd.Series(True, index=ix.index)
    base = live & uptrend
    if p["use_vwap"]:
        below = (c < vw) & base
        breadth = below.sum(axis=1) / base.sum(axis=1).replace(0, np.nan)
        broad = (breadth > p["breadth_cut"]).values
        reclaimed = (c > vw).values
        depth = (c / vw - 1.0).values
    else:
        broad = np.ones(len(c), bool)
        reclaimed = np.zeros(c.shape, bool)
        depth = np.zeros(c.shape)
    if p["use_rsi"]:
        oversold = rsi(c, p["rsi_n"]) < p["rsi_max"]
    else:
        oversold = pd.DataFrame(True, index=c.index, columns=tick)
    trigger = ix_up.reindex(c.index).fillna(False).values & broad
    eligible = (base & oversold).values
    fresh = md.tradable[tick].values
    member = md.member[tick].values

    T, N = c.shape
    K, mh = int(p["slots"]), int(p["max_hold"])
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    out = np.zeros((T, N))
    for t in range(T):
        age[held] += 1
        sell = held & fresh[t] & (reclaimed[t] | (age >= mh))
        sell |= held & ~member[t]
        held &= ~sell
        age[~held] = 0
        free = K - int(held.sum())
        if trigger[t] and free > 0:
            cand = np.where(eligible[t] & ~held)[0]
            if len(cand) > free:
                cand = cand[np.argsort(depth[t, cand], kind="stable")[:free]]
            held[cand] = True
            age[cand] = 0
        out[t, held] = 1.0 / K
    return pd.DataFrame(out, index=c.index, columns=tick)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    tick = [t for t in md.tickers if t != ETF_TICKER]
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        on = (ema(ix, p["fast"]) > ema(ix, p["slow"]))["NIFTY50"].astype(float)
    else:
        on = pd.Series(1.0, index=ix.index)
    on = on.reindex(md.calendar).fillna(0.0)
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[ETF_TICKER] = (1.0 - p["sleeve_share"]) * on
    S = _pullback_sleeve(md, p, tick).reindex(index=md.calendar).fillna(0.0)
    W[tick] = S * p["sleeve_share"]
    return W
