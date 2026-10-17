"""
Trend core plus a pullback sleeve, cleaned (protocol v4.2, round 3, lens: a robust trend core).

The book is two fixed parts that never compete for the same capital:
  * the TREND CORE (core_share = 20 % of the book): NIFTYBEES while the NIFTY 50 is in a medium-term uptrend;
  * the PULLBACK SLEEVE (the other 80 %): short dip-buying trades in uptrending members, opened only when the
    leaders of the market have pulled back together inside a long uptrend.
Every weight is decided with data up to the close of day t; the harness trades it at the open of t+1.
Capital that neither part uses stays in cash and earns the repo rate (credited by the harness).

RULES
1. Trend core (EMA). While the NIFTY 50's EMA(core_fast = 20) is above its EMA(core_slow = 50), the core holds
   its 20 % of the book in NIFTYBEES (one unit of all 50 members); otherwise that 20 % is in cash. The
   lengths are R0's; they are named core_fast / core_slow so that the plateau gate tests them with the
   ABSOLUTE rule (every +-20 % neighbour must beat R0 and keep 60 % of the margin), not the relative one.
2. Pullback trigger (EMA + VWAP). The sleeve opens trades only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad among the leaders: of the members in their own uptrend (close above their own
        EMA(100)), the share that closes below its rolling vwap_n (10)-day VWAP is above breadth_cut (60 %).
   (Round 2 sized trades on a ramp from 50 % to 70 %; the ramp is replaced by a single cut at its midpoint, a
   simplification that costs 0.011 of objective and removes one parameter; see WHY below.)
3. Name filter (EMA + RSI). On such a day a member is eligible when it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max = 20.
4. Selection (VWAP). Free slots (slots = 12; one slot = 1/12 of the sleeve = 6.7 % of the book) are filled
   with the eligible names that sit deepest below their 10-day VWAP (lowest close / VWAP10 - 1). A name keeps
   the weight it was bought with until it is sold.
5. Exit (VWAP + time). A held name is sold as soon as it closes back above its 10-day VWAP (the dip has been
   bought back), or after max_hold (20) sessions if that never happens; a name that leaves the index is sold.

WHAT THE LENS FOUND ABOUT THE CORE (scratch experiments.tsv, about 150 rows)
* No EMA trend core tried is on an absolute plateau at R0's level, and none keeps all of R0's windows while
  being less fragile. On the NIFTY, 2024-26 trend timing is near zero Sharpe, and the result depends on the
  exact cross dates of 2-3 episodes (Dec 2024, Apr 2025, Aug 2025): R0 20/50 earns about 3 % more than 16/50 or
  24/50 in single months, nothing else; buy-and-hold NIFTYBEES has a 2024-26 Sharpe of -0.28. Grid of EMA crosses (fast 10..30 x slow 35..100): 2024-26 Sharpe from
  -0.45 to +0.22 with no broad positive region; local ensembles (9 pairs 16-24 x 40-60, graded 0.11, at
  +-20 % scale -0.00 / 0.10), speed ensembles (0.04), consensus hysteresis (0.19; 0.05 / 0.13), spread
  hysteresis bands (-0.13..0.19), a ramp on the spread (in the book), EMA slope (0.17; neighbours -0.28..0.05), price vs EMA with or without RSI and
  VWAP agreement (all negative, 7-18x turnover), member-breadth of stock trends (smooth but -0.34) and a long
  trend filter (EMA50 > EMA100: 2024-26 up to 0.33, but 2020-23 falls from 1.12 to 0.77-0.96 because it
  misses the V-shaped recoveries of 2020 and 2022) were all either trade-offs between windows or flatter but
  lower. The averaging cores do not even buy robustness inside the combined book (9-pair core: combined
  objective 0.73, neighbours 0.68-0.72, against 0.69-0.74 for the plain cross, both with round 2's sleeve).
* Inside the book the core's fragility does not carry over: moving core_fast or core_slow by +-20 % leaves the
  combined objective at 0.69-0.73 (base 0.745, absolute floor 0.53), because the sleeve, not the core, carries 2024-26.
* What the core does buy is robustness to event days. A dip buyer earns much of its return on a few rebound
  days. Without the core (sleeve only) the objective is higher (0.79), but 2017-19 loses almost all its Sharpe
  when that window's 5 best days are removed (0.79 -> 0.06; R0 0.75 -> 0.35); the core's steady trend returns
  (2017, 2021, 2023) cut that dependence (0.22, and 2020-23 0.85 vs R0 0.86). The 80/20 split is the round-2
  equal-exposure rule (the core is on ~71 % of days at 20 % of the book, the sleeve averages ~16 % gross of its
  80 %), not tuned here: a 10 % core gives a higher objective (0.80) and a weaker event-day profile, a 30 %
  core 0.68 and a stronger one.

WHY THE SLEEVE SHOULD HOLD ACROSS REGIMES (round 2, re-checked)
* It harvests short-term reversal after market-wide selling: forced and impatient sellers push uptrending
  names below the volume-weighted price their recent buyers paid, and the bounce back to that price pays
  whoever supplies liquidity. Names are chosen by how far they fell, not by momentum or sector, so a change of
  market leadership does not remove the edge.
* Breadth is counted over the uptrend names only, so the sleeve fires when the leaders themselves pull back,
  not in narrow tapes where most members are already falling.
* The index filter keeps the sleeve out of crashes in progress (March 2020); the time stop cuts dips that are
  not bought back within a month.
* The breadth cut at 0.6 is the midpoint of round 2's 0.5-0.7 size ramp. The ramp bought 0.011 of objective
  for an extra parameter; the cut has a higher span Sharpe (1.01 vs 0.94), a higher Sharpe without the 10 best
  days (0.63 vs 0.56) and a 2020-23 Sharpe without that window's 5 best days of 0.85 (ramp 0.71). Its
  neighbours (0.48, 0.72) stay above the absolute floor (plateau.txt).

KNOWN WEAK POINTS
* 2026 is negative (-1.19), as for every strategy tried; the core makes it worse than the sleeve alone (-0.53).
* 2017-19 without its 10 best days is negative (R0 +0.07); the 2024-26 gain over R0 survives event-day removal.
* Costs: turnover about 11x a year, mostly the sleeve; the objective is 0.56 at 25 bps and 0.28 at 40 bps
  (R0 0.17 and 0.12).
* A joint random +-20 % move of all ten parameters (12 draws) averages 0.63 (min 0.49).

Long-only, gross at most 1 (core 0.2 + sleeve at most 0.8). Longest lookback 100 sessions (EMA100), inside the
200-session limit; warm-up NaNs read as "no trend", "not oversold", "no breadth".

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (core always on, index and
stock trend filters off, breadth over all live members); use_rsi=False drops the stock RSI filter;
use_vwap=False drops the breadth trigger (trades may open every day), the VWAP-depth ranking (ties keep the
ticker order) and the VWAP-reclaim exit (time stop only).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    core_fast=20,        # core: NIFTY EMA(core_fast) > EMA(core_slow) -> NIFTYBEES (R0's lengths)
    core_slow=50,
    core_share=0.2,      # share of the book in the trend core (the sleeve gets the rest)
    trend_n=100,         # EMA length of the uptrend filters (index and stocks)
    breadth_cut=0.6,     # trigger: share of uptrend members below their VWAP must exceed this
    rsi_n=3,             # stock Wilder RSI length
    rsi_max=20.0,        # stock is oversold when RSI(rsi_n) < rsi_max
    vwap_n=10,           # rolling VWAP length: trigger breadth, ranking and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=12,            # sleeve slots; each held name weighs (1 - core_share) / slots
    use_rsi=True, use_ema=True, use_vwap=True,
)


def _pullback_sleeve(md, p, tick):
    """Weights of the pullback sleeve as a fraction of the sleeve's own capital (each held name 1/slots)."""
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
        broad = (breadth > p["breadth_cut"]).values
        reclaimed = (c > vw).values
        gap = (c / vw - 1.0).values
    else:
        broad = np.ones(len(c.index), bool)
        reclaimed = np.zeros(c.shape, bool)
        gap = np.zeros(c.shape)
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
                cand = cand[np.argsort(gap[t, cand], kind="stable")[:free]]
            held[cand] = True
            age[cand] = 0
        out[t, held] = 1.0 / K
    return pd.DataFrame(out, index=c.index, columns=tick)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    tick = [t for t in md.tickers if t != ETF_TICKER]
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        on = (ema(ix, p["core_fast"]) > ema(ix, p["core_slow"]))["NIFTY50"].astype(float)
    else:
        on = pd.Series(1.0, index=ix.index)
    on = on.reindex(md.calendar).fillna(0.0)

    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[ETF_TICKER] = p["core_share"] * on
    S = _pullback_sleeve(md, p, tick).reindex(index=md.calendar).fillna(0.0)
    W[tick] = S * (1.0 - p["core_share"])
    return W
