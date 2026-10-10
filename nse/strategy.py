"""
RSI + EMA + VWAP strategy on the point-in-time Nifty 50 universe. Long-only, daily bars.
This is the ONLY file the research agent edits. The harness (prepare.py) executes the target
weights at the next day's open and charges costs; signals here must use data up to the close.

Rules (textbook confluence):
  trend    : EMA(fast) > EMA(slow) and close > EMA(fast)
  value    : close > rolling N-day VWAP (buyers in control)
  momentum : smoothed RSI(n) (EMA(rsi_smooth) of Wilder RSI, to damp one-day noise) below rsi_hi
             (not yet overbought; a lower bound such as RSI > 50 practically never binds once the trend
             and value filters hold, so there is none)
  regime   : the NIFTY 50's own RSI(n) must be in its bullish zone: the book is switched on when the
             index RSI crosses above regime_rsi_mid + regime_rsi_band and switched off (fully to cash)
             when it falls below regime_rsi_mid - regime_rsi_band (hysteresis avoids whipsaw); the book
             also stands aside while the index RSI is extremely overbought (above regime_rsi_max)
  cohort   : the book is bought once, on the day the regime switches on: every member in confluence,
             weighted by inverse vol_n-day realised variance (vol ** -vol_power, vol_power = 2: minimum
             variance if names were uncorrelated). Each name is then held while a looser exit condition is not triggered
             (hysteresis: close above vwap_exit * VWAP) and its weight rides with its own price (no
             rebalancing trades); a name that exits leaves its weight in cash. There are no
             replacement entries until the regime switches off and on again, so exposure decays as
             the move matures and the only trades are the cohort buy, single-name exits and the
             regime-off liquidation.
Names with no fresh bar on a day stay held (they cannot be traded that day anyway).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap, realized_vol
from prepare import ETF_TICKER

PARAMS = dict(
    rsi_n=14, rsi_smooth=3, rsi_hi=70.0,
    ema_fast=20, ema_slow=50,
    vwap_n=20, vwap_exit=0.985,
    vol_n=20, vol_power=2.0,
    regime_rsi_n=14, regime_rsi_mid=55.0, regime_rsi_band=2.0, regime_rsi_max=80.0,
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
    entry = eligible & (ef > es) & (c > ef) & (c > vw) & (r < p["rsi_hi"])
    hold = (stock & ~md.tradable) | (eligible & (c > vw * p["vwap_exit"]))

    dates = md.calendar
    N = len(md.tickers)
    W = np.zeros((len(dates), N))
    held = np.zeros(N, dtype=bool)
    w = np.zeros(N)                                         # yesterday's target weights
    entry_v, hold_v = entry.values, hold.values
    g_v = (c / c.shift(1)).fillna(1.0).values               # close-to-close growth (1 when unknown)
    vol = realized_vol(c, p["vol_n"])
    iv_v = (1.0 / vol.where(vol > 0) ** p["vol_power"]).fillna(0.0).values   # inverse variance (0 when unknown)
    for t in range(len(dates)):
        if not regime_v[t]:                                 # regime off: fully in cash
            held[:] = False
            w = np.zeros(N)
        elif t == 0 or not regime_v[t - 1]:                 # regime switches on: buy the cohort
            held = entry_v[t] & (iv_v[t] > 0)
            w = np.where(held, iv_v[t], 0.0)
            w = p["gross"] * w / w.sum() if held.any() else np.zeros(N)
        else:                                               # hold the cohort, weights ride, no top-ups
            held &= hold_v[t]
            wg = w * g_v[t]
            w = np.where(held, wg, 0.0) / (wg.sum() + 1.0 - w.sum())
        W[t] = w
    return pd.DataFrame(W, index=dates, columns=md.tickers)
