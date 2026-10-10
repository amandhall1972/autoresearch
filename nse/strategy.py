"""
RSI + EMA + VWAP strategy on the point-in-time Nifty 50 universe. Long-only, daily bars.
This is the ONLY file the research agent edits. The harness (prepare.py) executes the target
weights at the next day's open and charges costs; signals here must use data up to the close.

Rules (textbook confluence):
  trend    : EMA(fast) > EMA(slow) and close > EMA(fast)
  value    : close > rolling N-day VWAP (buyers in control)
  momentum : smoothed RSI(n) (EMA(rsi_smooth) of Wilder RSI, to damp one-day noise) between rsi_lo
             and rsi_hi (bullish, not yet overbought)
  regime   : the NIFTY 50's own RSI(n) must be in its bullish zone: the book is switched on when the
             index RSI crosses above regime_rsi_mid + regime_rsi_band and switched off (fully to cash)
             when it falls below regime_rsi_mid - regime_rsi_band (hysteresis avoids whipsaw); the book
             also stands aside while the index RSI is extremely overbought (above regime_rsi_max)
  sizing   : equal weight across the top `max_names` qualifying members (ranked by RSI),
             hold while a looser exit condition is not triggered (hysteresis).
Names with no fresh bar on a day stay held (they cannot be traded that day anyway).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    rsi_n=14, rsi_smooth=3, rsi_lo=50.0, rsi_hi=70.0, rsi_exit=40.0,
    ema_fast=20, ema_slow=50,
    vwap_n=20,
    regime_rsi_n=14, regime_rsi_mid=55.0, regime_rsi_band=2.0, regime_rsi_max=80.0,
    max_names=20,
    gross=1.0,
)


def hysteresis(x: pd.Series, on: float, off: float) -> np.ndarray:
    """Causal switch: True after x > on, False after x < off; NaN (warm-up) is always off."""
    out = np.zeros(len(x), dtype=bool)
    state = False
    for i, v in enumerate(x.values):
        if np.isnan(v) or v < off:
            state = False
        elif v > on:
            state = True
        out[i] = state
    return out


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    c, h, l, v = md.close, md.high, md.low, md.volume
    r = ema(rsi(c, p["rsi_n"]), p["rsi_smooth"])
    ef, es = ema(c, p["ema_fast"]), ema(c, p["ema_slow"])
    vw = rolling_vwap(h, l, c, v, p["vwap_n"])
    idx_rsi = rsi(md.index_close[["NIFTY50"]], p["regime_rsi_n"])["NIFTY50"]
    regime_v = hysteresis(idx_rsi, p["regime_rsi_mid"] + p["regime_rsi_band"], p["regime_rsi_mid"] - p["regime_rsi_band"])
    regime_v &= (idx_rsi <= p["regime_rsi_max"]).values               # NaN compares False -> off

    stock = md.member.copy(); stock[ETF_TICKER] = False          # the ETF is not part of the stock pool
    eligible = stock & md.tradable
    entry = eligible & (ef > es) & (c > ef) & (c > vw) & (r > p["rsi_lo"]) & (r < p["rsi_hi"])
    hold = (stock & ~md.tradable) | (eligible & (c > es) & (c > vw * 0.98) & (r > p["rsi_exit"]))
    score = r.where(entry)

    dates = md.calendar
    W = np.zeros((len(dates), len(md.tickers)))
    held = np.zeros(len(md.tickers), dtype=bool)
    entry_v, hold_v, score_v = entry.values, hold.values, score.values
    for t in range(len(dates)):
        if not regime_v[t]:
            held[:] = False
            continue
        held &= hold_v[t]                                   # drop names that violated the hold rule
        n_free = p["max_names"] - held.sum()
        if n_free > 0:
            cand = np.where(entry_v[t] & ~held)[0]
            if len(cand):
                cand = cand[np.argsort(-score_v[t][cand])][:n_free]
                held[cand] = True
        n = held.sum()
        if n:
            W[t, held] = p["gross"] / n
    return pd.DataFrame(W, index=dates, columns=md.tickers)
