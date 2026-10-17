"""
Index timing with RSI + EMA + VWAP (protocol v4, lens: index timing).

What it holds
-------------
Either cash (credited with the repo rate by the harness) or "the market", held as two equal halves:
  * NIFTYBEES (the Nifty 50 ETF), and
  * an equal-weight basket of the point-in-time Nifty 50 members (1/N of that half each).
The two halves always move together; only the size of the market position changes. The split is a fixed
structural choice (`etf_share` = 0.5), not a signal: the ETF is the cheapest faithful index exposure, the
equal-weight half spreads the position over ~50 names.

How much of the market it holds (decided at the close of day t, traded at the next open)
------------------------------------------------------------------------------------------
All three readings are taken on the NIFTY 50 index itself, so no single stock and no ETF price artefact can
drive the decision.

1. Trend vote 1 - EMA cross: the 20-session EMA of the NIFTY is above its 50-session EMA.
2. Trend vote 2 - EMA over VWAP: the 20-session EMA of the NIFTY is above the 50-session VWAP of the NIFTY
   (typical price (H+L+C)/3 of the index, weighted by the daily traded volume of NIFTYBEES, the market's
   index-tracking flow). The VWAP is the average price paid over the window; an EMA above it means recent
   prices sit above where the volume actually traded.
   Each vote is worth half of the position, so the exposure is graded 0 / 0.5 / 1: both trend readings
   agree -> fully invested, they disagree -> half, both down -> cash. The two readings measure the same
   medium-term trend with different weights (time-decay vs volume), so a whipsaw in one only moves half the
   position.
3. Overbought brake - RSI: while the NIFTY's 14-session Wilder RSI is above 70, the exposure from (1)-(2) is
   halved (`ob_keep` = 0.5). It is restored as soon as the RSI is back at or below 70.

Why it should hold across regimes
---------------------------------
* Trend: the medium-term trend of a broad equity index persists more often than not (time-series momentum).
  On every seen window the index earned more after days with EMA20 > EMA50 than after days without
  (2017-19, 2020-23 and 2024-26 alike), and being in cash during downtrends avoids most of the large
  drawdowns (2018 H2, March 2020, late 2024, early 2026). Long-only timing cannot make money in a market that
  only goes sideways, but it limits the damage there.
* Overbought brake: after the index has run up far enough to push RSI(14) above 70, the following days have
  on average paid little or less than cash in choppy markets (2017-19, 2024-26). Halving rather than exiting
  keeps half the position in strong trends (2020-21, 2023), where RSI can stay above 70 for weeks, so the cost
  of the brake in trending regimes is bounded while it trims the reversals after euphoric spikes.
* Few, round parameters (20/50, 14/70, one half), every one on a plateau of its neighbours; no stock selection,
  so the result does not rest on a cross-sectional edge that can vanish (the v1 incumbent's failure mode).

Causality: EMA, VWAP and RSI use data up to and including day t only; warm-up NaNs read as "no trend" (cash).
The longest lookback is 50 sessions.
"""
import numpy as np
import pandas as pd

import indicators as I
from prepare import ETF_TICKER

PARAMS = {
    "fast": 20,        # fast EMA of the NIFTY (sessions)
    "slow": 50,        # slow EMA of the NIFTY and the VWAP window (sessions)
    "rsi_n": 14,       # Wilder RSI length on the NIFTY
    "rsi_ob": 70,      # overbought level: above it the exposure is cut
    "ob_keep": 0.5,    # fraction of the exposure kept while overbought
    "etf_share": 0.5,  # share of the market position held in NIFTYBEES (rest: equal-weight members)
}


def market_exposure(md) -> pd.Series:
    """Target fraction of the portfolio in the market, per day (0, 0.25, 0.5 or 1)."""
    p = PARAMS
    ix = md.index_close[["NIFTY50"]]
    ohlc = md.index_ohlc
    flow = md.volume[[ETF_TICKER]].rename(columns={ETF_TICKER: "NIFTY50"})
    ema_fast = I.ema(ix, p["fast"]).iloc[:, 0]
    ema_slow = I.ema(ix, p["slow"]).iloc[:, 0]
    vwap = I.rolling_vwap(ohlc["high"][["NIFTY50"]], ohlc["low"][["NIFTY50"]], ohlc["close"][["NIFTY50"]],
                          flow, p["slow"]).iloc[:, 0]
    rsi = I.rsi(ix, p["rsi_n"]).iloc[:, 0]
    trend = 0.5 * (ema_fast > ema_slow).astype(float) + 0.5 * (ema_fast > vwap).astype(float)
    brake = pd.Series(np.where(rsi > p["rsi_ob"], p["ob_keep"], 1.0), index=rsi.index)
    return (trend * brake).reindex(md.calendar).fillna(0.0)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    expo = market_exposure(md)
    names = [t for t in md.tickers if t != ETF_TICKER]
    mem = md.member[names].astype(float)
    basket = mem.div(mem.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[ETF_TICKER] = expo * p["etf_share"]
    W[names] = basket.mul(expo * (1.0 - p["etf_share"]), axis=0)
    return W.fillna(0.0)
