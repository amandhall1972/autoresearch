"""
Technical indicators on wide DataFrames (index = dates, columns = tickers).
All functions are causal: the value at date t uses data up to and including t only.
This file is part of the fixed harness (do not modify); strategies may add their own helpers.
"""
import numpy as np
import pandas as pd


def ema(x: pd.DataFrame, span: int) -> pd.DataFrame:
    """Exponential moving average, alpha = 2/(span+1), seeded with the first observation."""
    return x.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder's RSI (smoothing alpha = 1/n). Range 0..100."""
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(ad > 0, 100.0)          # no down moves -> RSI 100
    out = out.where(~((au == 0) & (ad == 0)), 50.0)  # flat -> neutral
    return out


def typical_price(high, low, close) -> pd.DataFrame:
    return (high + low + close) / 3.0


def rolling_vwap(high, low, close, volume, n: int) -> pd.DataFrame:
    """Rolling n-day volume-weighted average price of the typical price."""
    tp = typical_price(high, low, close)
    pv = (tp * volume).rolling(n, min_periods=n).sum()
    v = volume.rolling(n, min_periods=n).sum()
    return pv / v.replace(0.0, np.nan)


def anchored_vwap(high, low, close, volume, anchor: pd.DataFrame) -> pd.DataFrame:
    """
    VWAP anchored at the most recent True in `anchor` (per column). The anchor bar itself is
    included. Resets whenever a new anchor occurs. Uses cumulative sums with a group id.
    """
    tp = typical_price(high, low, close)
    pv = (tp * volume).fillna(0.0)
    v = volume.fillna(0.0)
    gid = anchor.astype(int).cumsum()            # group id increments at each anchor
    out = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for c in close.columns:
        g = gid[c]
        cpv = pv[c].groupby(g).cumsum()
        cv = v[c].groupby(g).cumsum()
        out[c] = (cpv / cv.replace(0.0, np.nan)).where(g > 0)
    return out


def month_start_anchor(index: pd.DatetimeIndex, columns) -> pd.DataFrame:
    """True on the first trading day of each calendar month."""
    m = pd.Series(index.to_period("M"), index=index)
    first = (m != m.shift(1)).values
    return pd.DataFrame(np.repeat(first[:, None], len(columns), axis=1), index=index, columns=columns)


def atr(high, low, close, n: int = 14) -> pd.DataFrame:
    pc = close.shift(1)
    tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=0, keys=[0, 1, 2]).groupby(level=1).max()
    tr = tr.reindex(close.index)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def realized_vol(close: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Annualized close-to-close volatility over n days."""
    return close.pct_change().rolling(n, min_periods=n).std() * np.sqrt(252)


def cs_rank(x: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank in [0,1] per date (NaN-aware)."""
    return x.rank(axis=1, pct=True)
