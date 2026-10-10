"""
Pullback sleeve plus EMA trend core, cleaned (protocol v4.2, round 3, lens: combine orthogonal parts).

Started from v4/r2_pullbackcombo.py and deleted two things the r2 audit flagged: the 25 % equal-weight "sliver"
inside the core (it only existed to pass the old >= 5 names rule; protocol v4.2 accepts NIFTYBEES >= half of
the gross, here 69 %) and the entry condition "not back above its VWAP" (dead code: it never removed a
candidate). 11 parameters (r2: 12). Objective 0.7556 vs r2's 0.7528. No rule was added: every R0-side addition
this lens tested (below) raised the objective only through one or two episodes.

The book is two fixed parts that never compete for the same capital. Every weight is decided with data up to the
close of day t; the harness trades it at the open of t+1. Capital that neither part uses stays in cash and earns
the repo rate (credited by the harness).

RULES
1. Trend core (EMA), 1 - sleeve_share = 20 % of the book. While the NIFTY 50's EMA(fast = 20) is above its
   EMA(slow = 50), the core holds NIFTYBEES; otherwise it is in cash. This is R0, the one effect that held in
   every research window.
2. Pullback trigger and trade size (EMA + VWAP). The sleeve (sleeve_share = 80 % of the book) opens trades only
   on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad: among the members in their own uptrend (close above their own EMA(100)), the
        share that closes below its rolling vwap_n (= 10) session VWAP is above breadth_from (= 0.5).
   The size of each new trade grows linearly with that share, from zero at 0.5 to full at breadth_full (0.7)
   and above: a pullback that half of the leaders join buys small, one that most of them join buys full.
3. Name filter (EMA + RSI). A member is eligible on a trigger day when it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max = 20.
4. Selection (VWAP). Free slots (slots = 12; a full-size slot is sleeve_share / slots = 6.7 % of the book) are
   filled with the eligible names that sit deepest below their 10-session VWAP (lowest close / VWAP10 - 1).
   A name keeps the weight it was bought with until it is sold; held names are not topped up.
5. Exit (VWAP + time). A held name is sold when it closes back above its 10-session VWAP (the dip has been
   bought back) or after max_hold = 20 sessions; a name that leaves the index is sold.

WHY IT SHOULD HOLD ACROSS REGIMES
* The core is R0 unchanged: index trend persistence, positive in 2017-19, 2020-23 and 2024-26, out of the long
  falls (2018 H2, March 2020, 2022, early 2026).
* The sleeve supplies liquidity after market-wide selling: uptrending names pushed below the volume-weighted price
  of their recent buyers tend to return to it. The trade-level edge is present in every window (r2: mean trade
  net of costs +0.5 % / +0.9 % / +1.0 % in 2017-19 / 2020-23 / 2024-26, about 70 % winners, ~7 sessions held).
  Names are chosen by how far they fell, not by momentum, so a change of leadership does not remove it.
* Breadth is counted over uptrend names only, so the sleeve fires when the leaders pull back, not in narrow tapes
  where most members are already falling; the index filter keeps it out of crashes; the time stop drops dips that
  are not bought back within a month.
* The ramp is kept. Collapsing it to a hard cut at its midpoint 0.6 costs only 0.010 (0.7452, one parameter
  fewer) but the 2017-19 window then rests on 2017: without 2017 the objective falls to 0.51 (ramp 0.76), so the
  deletion fails the leave-one-year-out gate and is not a simplification keep.

NUMBERS (15 bps; outputs in this folder)
objective 0.7556: pre 0.8659, is 1.0928, oos 0.7556; span 0.9364; drop10 0.5627; worst year 2026 -1.6368;
max drawdown -6.4 %; turnover 9.6x a year; average gross 0.26; NIFTYBEES 69 % of the gross on invested days.
At 25 bps 0.5695, at 40 bps 0.2903 (R0: 0.1675 / 0.1197).

KNOWN WEAK POINTS
* Event days. With each window's own 5 best days removed: pre 0.23 / is 0.71 / oos 0.07 (R0 0.35 / 0.86 /
  -0.39); with 10: -0.24 / 0.37 / -0.52 (R0 0.07 / 0.65 / -0.80). The dip buyer leans on rebound days: pre and
  is fall below R0's once they are removed (the r2 audit finding, unchanged by the deletions).
* 2026 is negative (-1.64), as for every strategy tried; 2021 (0.68) and 2022 (0.25) are weak.
* R0's own fast/slow are a narrow peak (R0 at fast 16/24: 0.017/0.028); here fast 16/24 gives 0.69/0.70.

WHAT THIS LENS TESTED AND DID NOT ADD (experiments.tsv; all on this base)
* r2_whipsaw's distribution-day exit on the core: +0.095 with the RSI climax at 65 (0.8508), but the climax
  parameter itself buys only 0.04 and its neighbours 52 / 78 lose it; without the climax the exit adds 0.054 at
  RSI midline 50, -0.01 at 45 and +0.01 at 55. Its 2024-26 gain is two exits (Oct 2024, Jan 2026): skip them and
  the objective is 0.748, below this base.
* r2_breadth's washout-thrust early entry on the core: +0.067 (two parameters, does not pay 0.10). With
  "short-term breadth above long-term breadth" as the thrust (one parameter) +0.106, but its 2024-26 gain is one
  trade (Apr-Jul 2026): skip it and the gain is 0.009.
* Both together reach 0.92-0.96, with better event-day numbers and worst year, but skipping two episodes
  (the Oct 2024 exit and the Apr 2026 entry) returns the objective to 0.757: a combination that rests on two
  episodes is not progress.
* Split: the objective rises with the sleeve share (0.9: 0.8267) while drop10 falls to R0's level (0.52) and the
  +20 % neighbour leaves no core; 0.8 is kept. Blocking sleeve entries during a distribution exit: +0.03, but RSI
  midline 55 gives 0.58. Core trend from NIFTY > EMA100 instead of the EMA cross: 0.64; both: 0.73.

Long-only, gross at most 1; longest lookback 100 sessions (EMA100), inside the 200-session limit.

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (core always on, index and
stock trend filters off); use_rsi=False drops the stock RSI filter; use_vwap=False drops the breadth trigger and
size ramp (full size every day), the VWAP-depth ranking and the VWAP-reclaim exit (time stop only).
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
    breadth_from=0.5,    # trigger: share of uptrend members below their VWAP above which the sleeve buys
    breadth_full=0.7,    # ... and at which it buys at full size (linear in between)
    rsi_n=3,             # stock Wilder RSI length
    rsi_max=20.0,        # stock is oversold when RSI(rsi_n) < rsi_max
    vwap_n=10,           # rolling VWAP length: trigger breadth, ranking depth and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=12,            # sleeve slots; a full-size held name weighs sleeve_share / slots
    use_rsi=True, use_ema=True, use_vwap=True,
)


def _pullback_sleeve(md, p, tick):
    """Weights of the pullback sleeve as a fraction of the sleeve's own capital (each held name size / slots)."""
    c = md.close[tick]
    live = md.member[tick] & md.tradable[tick]
    vw = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["vwap_n"])
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        trend = c > ema(c, p["trend_n"])
        ix_up = (ix > ema(ix, p["trend_n"]))["NIFTY50"]
    else:
        trend = pd.DataFrame(True, index=c.index, columns=tick)
        ix_up = pd.Series(True, index=ix.index)
    base = live & trend
    if p["use_vwap"]:
        below = (c < vw) & base
        breadth = below.sum(axis=1) / base.sum(axis=1).replace(0, np.nan)
        size = ((breadth - p["breadth_from"]) / (p["breadth_full"] - p["breadth_from"])).clip(0.0, 1.0).fillna(0.0)
        reclaimed = (c > vw).values
        gap = (c / vw - 1.0).values
    else:
        size = pd.Series(1.0, index=c.index)
        reclaimed = np.zeros(c.shape, bool)
        gap = np.zeros(c.shape)
    if p["use_rsi"]:
        oversold = rsi(c, p["rsi_n"]) < p["rsi_max"]
    else:
        oversold = pd.DataFrame(True, index=c.index, columns=tick)
    size = size.values
    trigger = ix_up.reindex(c.index).fillna(False).values & (size > 0)
    eligible = (base & oversold).values
    fresh = md.tradable[tick].values
    member = md.member[tick].values

    T, N = c.shape
    K, mh = int(p["slots"]), int(p["max_hold"])
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    wgt = np.zeros(N)
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
                cand = cand[np.argsort(gap[t, cand], kind="stable")[:free]]
            held[cand] = True
            age[cand] = 0
            wgt[cand] = size[t] / K
        out[t, held] = wgt[held]
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
