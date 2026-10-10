"""
RSI + EMA + VWAP on the point-in-time Nifty 50, long-only, daily bars: a repaired version of the v1 timed-cohort
strategy (strategy.py, 40 parameters) reduced to 8 parameters. Target weights are decided with data up to the close
of day t; the harness trades them at the next open.

WHAT WAS KEPT FROM THE INCUMBENT, AND WHY
  * Timed exposure: the whole book is in the market only while an index regime is on, and in cash (earning the repo
    rate) otherwise. Index timing is the one part of the incumbent with an edge in every window.
  * The stock entry test: EMA(fast) > EMA(slow), close above the rolling VWAP, smoothed RSI below rsi_hi (not
    overbought). These are the incumbent's own RSI, EMA and VWAP rules, with the same lengths.
  * NIFTYBEES as the parking slot for capital the names do not use.

WHAT WAS REMOVED OR REPLACED, AND WHY (the diagnosis on 2017-19 / 2020-23 / 2024-26)
  * The regime. The incumbent switched on when the NIFTY 50's RSI(14) crossed 57 and off below 53 (plus a stand-aside
    above 80). Holding NIFTYBEES on that switch alone scores Sharpe -0.19 / 1.86 / -1.31: a short-swing timing that
    only worked in 2020-23. It is replaced by the slow index trend, NIFTY 50 EMA20 > EMA50 (0.75 / 1.12 / 0.20 for
    NIFTYBEES alone), which stays positive in every window.
  * The single cohort per switch-on, and everything built to manage it: Bank Nifty variance sizing, whipsaw half
    size, the model-vol budget and trims, the day-3 re-rank, the exhaustion sale, the breadth-dip top-up, the bottom
    rung tilt, the 40 % name cap, the re-entry cohort and its score. None of these helps once the regime is a trend
    filter, and the timed cohort itself made 84 % winning spells in 2020-23 but 39 % in 2024-26.
  * The single-index weighting (Blume beta / idiosyncratic variance) and the fast exits (close < 0.985 x VWAP20,
    VWAP ladder break, idiosyncratic shock, volume spike). The fast exits churn a continuous book (15-30x a year of
    turnover, 2-5 % a year of costs) and cost 0.3-0.4 of Sharpe in every window.
  * Rank-IC diagnostics showed no RSI / EMA / VWAP stock signal that ranks Nifty 50 members consistently in all
    three windows, and equal-weighted stock books lose to the cap-weighted index in 2017-19, when a handful of
    mega-caps carried the Nifty. So the stock book is kept as a half-sized satellite next to an index core.

RULES (all evaluated at the close of day t)
  1. Regime: on while the NIFTY 50's EMA(ema_fast) is above its EMA(ema_slow). Off: everything is sold, cash.
  2. Satellite: k slots of sleeve / k of capital each (10 x 5 %). A name is bought into a free slot when it is a
     current member with a fresh bar and
        EMA(ema_fast) > EMA(ema_slow)                     (its own uptrend)
        close > VWAP(vwap_n)                              (trading above its volume-weighted value)
        EMA(rsi_smooth) of Wilder RSI(rsi_n) < rsi_hi     (not overbought)
     When more names qualify than slots are free, the ones with the largest (close / VWAP(vwap_n) - 1) / vol(vwap_n)
     are bought: distance above VWAP measured in units of the name's own 20-day volatility, i.e. strength per unit of
     risk (raw distance favours volatile names; volatility alone favoured the 2017-19 winners only).
  3. A held name keeps its slot (at a constant sleeve / k weight) until its EMA(ema_fast) falls below its
     EMA(ema_slow), it leaves the index, or the regime switches off. A name without a fresh bar is kept (it cannot
     trade). No other exits: slow exits keep turnover near 6x a year.
  4. Core: whatever the satellite does not use (at least 1 - sleeve = 50 %) is held in NIFTYBEES while the regime is
     on.

WHY IT SHOULD HOLD ACROSS REGIMES
  The index trend filter is the classic time-series momentum effect (persistent trends, losses cut in drawn-out
  declines such as late 2024-early 2025 and 2026) and needs no stock-picking edge. The satellite only buys names that
  are trending up and trading above VWAP in a rising market, so it never holds a name against its own trend, and the
  50 % index core limits how far a few names can pull the book away from the index in markets where breadth is narrow.
  The binding windows are 2017-19 and 2024-26, and both stay positive. The parameters are the round values the
  incumbent already used (20 / 50 / 14 / 3 / 70) plus 10 slots and a half sleeve.
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap, realized_vol
from prepare import ETF_TICKER

PARAMS = dict(
    ema_fast=20, ema_slow=50,          # index regime and each name's trend
    vwap_n=20,                         # VWAP window, and the volatility window of the strength score
    rsi_n=14, rsi_smooth=3, rsi_hi=70.0,   # not-overbought entry cap
    k=10, sleeve=0.5,                  # satellite: k equal slots sharing `sleeve` of capital; the rest is NIFTYBEES
)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    c, h, l, v = md.close, md.high, md.low, md.volume
    nifty = md.index_close[["NIFTY50"]]
    regime = (ema(nifty, p["ema_fast"]) > ema(nifty, p["ema_slow"]))["NIFTY50"].values   # NaN (warm-up) -> off

    stock = md.member.copy()
    stock[ETF_TICKER] = False
    eligible = stock & md.tradable
    up = ema(c, p["ema_fast"]) > ema(c, p["ema_slow"])                   # NaN -> False
    vw = rolling_vwap(h, l, c, v, p["vwap_n"])
    r = ema(rsi(c, p["rsi_n"]), p["rsi_smooth"])
    entry = eligible & up & (c > vw) & (r < p["rsi_hi"])
    keep = (stock & ~md.tradable) | (eligible & up)                      # held without a fresh bar: cannot trade
    strength = ((c / vw - 1.0) / realized_vol(c, p["vwap_n"])).fillna(-np.inf)

    entry_v, keep_v, str_v = entry.values, keep.values, strength.values
    e = md.tickers.index(ETF_TICKER)
    N, k = len(md.tickers), p["k"]
    slot = p["sleeve"] / k
    W = np.zeros((len(md.calendar), N))
    held = np.zeros(N, dtype=bool)
    for t in range(len(md.calendar)):
        if not regime[t]:                                                # regime off: all cash
            held[:] = False
            continue
        held &= keep_v[t]                                                # trend-break / index-exit sales
        free = k - int(held.sum())
        if free > 0:
            cand = np.flatnonzero(entry_v[t] & ~held)
            if len(cand):
                cand = cand[np.argsort(-str_v[t, cand], kind="stable")][:free]
                held[cand] = True
        W[t, held] = slot
        W[t, e] = 1.0 - slot * held.sum()                                # the core: NIFTYBEES
    return pd.DataFrame(W, index=md.calendar, columns=md.tickers)
