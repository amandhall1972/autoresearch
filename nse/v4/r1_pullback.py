"""
Pullback in an uptrend: buy the deepest short-term dips of uptrending Nifty 50 names, but only when the whole
market has pulled back inside its own uptrend; sell each name as soon as it reclaims its short VWAP.
Long-only, daily bars; target weights use data up to the close of day t and are executed at the next open.

RULES
1. Market trigger (RSI + EMA). Entries are allowed only on days when the NIFTY 50 is in an uptrend, close above
   its EMA(trend_n = 100), AND is short-term oversold, Wilder RSI(idx_rsi_n = 3) of the index below
   idx_rsi_entry (20). That combination is a market-wide pullback inside a persistent uptrend.
2. Name filter (EMA + VWAP). A member stock is eligible when it is in its own uptrend, close above its
   EMA(trend_n = 100), and is trading at or below its rolling vwap_n-day (10) VWAP (it has pulled back too).
3. Selection (VWAP). On a trigger day the free slots (slots = 12, each 1/12 of the book) are filled with the
   eligible names that sit deepest below their 10-day VWAP (lowest close / VWAP10 - 1).
4. Exit (VWAP + time). A held name is sold when it closes back above its 10-day VWAP (the dip has been bought
   back: the average buyer of the last two weeks is in profit again), or after max_hold (20) sessions if that
   never happens; non-members are sold. Weights are fixed at 1/12 per held name; unused capital stays in
   cash and earns the repo rate (the harness credits it).

WHY IT SHOULD HOLD ACROSS REGIMES
* Short-term reversal after market-wide selling is a liquidity-provision effect: forced and impatient sellers
  push prices below what the recent volume-weighted buyers paid, and the bounce is paid to whoever supplies
  liquidity. It is strongest when the dip is market-wide (index RSI) rather than stock-specific: single-name
  dips in a calm market did not cover the 30 bp round-trip cost in any window.
* The two trend filters (index and stock above EMA100) keep the strategy out of falling markets (2020 crash,
  2022 drawdowns), where oversold readings keep getting more oversold; without the index filter the
  2020-2023 Sharpe turns negative.
* The VWAP-reclaim exit takes the bounce and frees the capital within about six sessions on average, so the
  book is in cash most of the time, carries little market beta and pays costs only on high-edge trades.
* Nothing depends on which stocks lead a given era: the names are chosen by how far they fell, not by a
  momentum or size ranking, so a change in market leadership (the failure of the v1 strategy after 2023)
  does not remove the edge.
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    trend_n=100,          # EMA length of the uptrend filter (index and stocks)
    idx_rsi_n=3,          # Wilder RSI length of the NIFTY 50 pullback trigger
    idx_rsi_entry=20.0,   # index RSI below this = market-wide pullback (entries allowed)
    vwap_n=10,            # rolling VWAP length: entry depth, ranking and the reclaim exit
    max_hold=20,          # time stop in sessions
    slots=12,             # number of position slots; each held name weighs 1/slots
)


def compute_target_weights(md):
    p = PARAMS
    tick = [t for t in md.tickers if t != ETF_TICKER]
    c = md.close[tick]
    vw = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["vwap_n"])
    trend = c > ema(c, p["trend_n"])
    live = md.member[tick] & md.tradable[tick]

    ix = md.index_close[["NIFTY50"]]
    ix_up = ix["NIFTY50"] > ema(ix, p["trend_n"])["NIFTY50"]
    ix_dip = rsi(ix, p["idx_rsi_n"])["NIFTY50"] < p["idx_rsi_entry"]
    trigger = (ix_up & ix_dip).values

    reclaimed = (c > vw).values                       # exit signal; also blocks entries above VWAP
    eligible = (live & trend).values
    gap = (c / vw - 1.0).values                       # depth below the 10-day VWAP (more negative = deeper)
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
            cand = np.where(eligible[t] & ~held & ~reclaimed[t])[0]
            if len(cand) > free:
                cand = cand[np.argsort(gap[t, cand], kind="stable")[:free]]
            held[cand] = True
            age[cand] = 0
        out[t, held] = 1.0 / K
    W = pd.DataFrame(out, index=c.index, columns=tick)
    return W.reindex(columns=md.tickers).fillna(0.0)
