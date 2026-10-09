"""
RSI + EMA + VWAP strategy on the point-in-time Nifty 50 universe. Long-only, daily bars.
This is the ONLY file the research agent edits. The harness (prepare.py) executes the target
weights at the next day's open and charges costs; signals here must use data up to the close.

Baseline rules (textbook confluence):
  trend    : EMA(fast) > EMA(slow) and close > EMA(fast)
  value    : close > rolling N-day VWAP (buyers in control)
  momentum : RSI(n) between rsi_lo and rsi_hi (bullish, not yet overbought)
  regime   : NIFTY 50 above its EMA(regime_ema); otherwise fully in cash
  sizing   : equal weight across the top `max_names` qualifying members (ranked by RSI),
             hold while a looser exit condition is not triggered (hysteresis).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap

PARAMS = dict(
    rsi_n=14, rsi_lo=50.0, rsi_hi=70.0, rsi_exit=40.0,
    ema_fast=20, ema_slow=50,
    vwap_n=20,
    regime_ema=200,
    max_names=20,
    gross=1.0,
)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    c, h, l, v = md.close, md.high, md.low, md.volume
    r = rsi(c, p["rsi_n"])
    ef, es = ema(c, p["ema_fast"]), ema(c, p["ema_slow"])
    vw = rolling_vwap(h, l, c, v, p["vwap_n"])
    nifty = md.index_close["NIFTY50"]
    regime = (nifty > nifty.ewm(span=p["regime_ema"], adjust=False, min_periods=p["regime_ema"]).mean())

    eligible = md.member & md.tradable
    entry = eligible & (ef > es) & (c > ef) & (c > vw) & (r > p["rsi_lo"]) & (r < p["rsi_hi"])
    hold = eligible & (c > es) & (c > vw * 0.98) & (r > p["rsi_exit"])
    score = r.where(entry)

    dates = md.calendar
    W = np.zeros((len(dates), len(md.tickers)))
    held = np.zeros(len(md.tickers), dtype=bool)
    entry_v, hold_v, score_v, regime_v = entry.values, hold.values, score.values, regime.values
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
