"""
Staggered pullback strategy: incumbent 1's RSI + EMA + VWAP dip buying, with at most max_new new names per day
(protocol v4.2, round 4, lens: event-day robustness). BEST ATTEMPT, NOT A KEEP: against incumbent 1 it fails
sanity (4.91 names on average, below 5), leave-one-year-out (without 2020 or 2024) and plateau4 (see RESULT below).

The whole book is a dip-buying sleeve. Trades open only when the leaders of the market have pulled back together
inside a long uptrend, and close when the dip has been bought back. Every weight is decided with data up to the
close of day t; the harness trades it at the open of t+1. Capital not in a trade stays in cash at the repo rate.

RULES
1. Trigger (EMA + VWAP). Trades open only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100), and
     b) of the members in their own uptrend (close above their own EMA(100)), more than breadth_cut (60 %)
        close below their rolling vwap_n (10)-day VWAP.
2. Name filter (EMA + RSI). A member is eligible when it is in its own uptrend and Wilder RSI(rsi_n = 3) is below
   rsi_max = 20.
3. Selection (VWAP) - CHANGED. On a trigger day the free slots (slots = 12, one slot = 1/12 of the book) are
   filled with the eligible names that sit deepest below their 10-day VWAP, but AT MOST max_new = 5 NEW NAMES
   PER DAY. Slots left free stay available to later trigger days of the same sell-off, so a broad sell-off is
   bought in stages over consecutive trigger days instead of all at the first close. A name keeps the weight it
   was bought with until it is sold.
4. Exit (VWAP + time). A held name is sold when it closes back above its 10-day VWAP, or after max_hold (20)
   sessions. Names leaving the index are sold by the harness.

WHAT CHANGED VS INCUMBENT 1 (v4/inc1_pullback.py) AND WHY
Incumbent 1 fills every free slot on the first trigger day; on a broad sell-off that is the whole book at one
close, so its window Sharpe rests on the next market-wide rebound day (2017-19: the 5 best days carry 94 % of the
window's excess return; with them removed the Sharpe is 0.06). Capping the new names per day spreads the entry of a
broad sell-off over its consecutive trigger days and lowers the weight of the few episodes in which 7 to 12 names
were bought at once (the biggest winners and the biggest losers: 2018-09-11, 2019-07-08, 2024-10-03).
At the point: objective 0.7887 -> 0.9101 (pre 1.163, is 1.039, oos 0.910), drop10 0.559 -> 0.642, each window
without its own 5 best days 0.06 / 0.71 / 0.14 -> 0.53 / 0.62 / 0.25.

RESULT (honest). Passes: improves, windows, drop10 (0.642), consistency (9 positive years), parsimony (+0.121 for
one extra parameter), compliance (RSI 0.26, EMA 0.67, VWAP 0.84), lookahead_ok, and the robustness bars: joint
+-20 % perturbation (60 draws, seed 20261010) mean 0.667 vs 0.597 (but p10 0.335 vs 0.370, only 27 % of draws
above incumbent 1's point), worst window without its own 5 best days 0.248 vs 0.062, objective at 25 bps 0.686
vs 0.557 (40 bps 0.347 vs 0.209), turnover 11.3x vs 13.1x.
Fails: sanity (4.91 names on average; the cap leaves slots empty), leave-one-year-out (without 2020: 0.734 vs
0.774; without 2024: 0.703 vs 0.733), and plateau4. The gain is not a plateau. Over max_new = 2..8 the objective
is 0.51, 0.63, 0.83, 0.91, 0.77, 0.79, 0.83 (12 = incumbent 1: 0.79). 2017-19 improves for every cap from 3 to 8
(0.97 to 1.16 against 0.79), which is the robust part of the effect, but 2020-23 and 2024-26 move
non-monotonically with the cap, because the cap decides which of the eligible names are bought and the VWAP-depth
ranking that picks them is no better than random (shallow, trend-strength and low-volatility rankings under the
same cap are worse). The inherited dials also lose their relative plateau (breadth_cut 0.72, rsi_n 4, rsi_max 24,
vwap_n 8 and 12 score below incumbent 1 at the same values). Smooth versions (a daily budget split equally over all
eligible names, top-ups of held names, half-slot entries) remove the jaggedness but cost 2020-23 more than the
windows gate allows and lower drop10 below 0.559.

Long-only, gross at most 1. Longest lookback 100 sessions (EMA100). Causal: EMA, RSI and rolling VWAP use bars up
to day t; the slot book only looks back.

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass; use_rsi=False drops the stock
RSI filter; use_vwap=False drops the breadth trigger, the VWAP-depth ranking (ticker order) and the VWAP-reclaim
exit (time stop only).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    trend_n=100,         # EMA length of the uptrend filters (index and stocks)
    breadth_cut=0.6,     # trigger: share of uptrend members below their VWAP must exceed this
    rsi_n=3,             # stock Wilder RSI length
    rsi_max=20.0,        # stock is oversold when RSI(rsi_n) < rsi_max
    vwap_n=10,           # rolling VWAP length: trigger breadth, ranking and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=12,            # each held name weighs 1 / slots of the book
    max_new=5,           # at most this many new names per day (staggered entry)
    use_rsi=True, use_ema=True, use_vwap=True,
)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    tick = [t for t in md.tickers if t != ETF_TICKER]
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

    T, N = c.shape
    K, mh, M = int(p["slots"]), int(p["max_hold"]), int(p["max_new"])
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    out = np.zeros((T, N))
    for t in range(T):
        age[held] += 1
        sell = held & fresh[t] & (reclaimed[t] | (age >= mh))
        held &= ~sell
        age[~held] = 0
        lim = min(K - int(held.sum()), M)
        if trigger[t] and lim > 0:
            cand = np.where(eligible[t] & ~held)[0]
            if len(cand) > lim:
                cand = cand[np.argsort(depth[t, cand], kind="stable")[:lim]]
            held[cand] = True
            age[cand] = 0
        out[t, held] = 1.0 / K
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[tick] = pd.DataFrame(out, index=c.index, columns=tick).reindex(index=md.calendar).fillna(0.0)
    return W
