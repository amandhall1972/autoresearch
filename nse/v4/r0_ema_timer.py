"""
Reset reference R0 (protocol v4.1): hold NIFTYBEES while the NIFTY 50's EMA(20) is above its EMA(50), otherwise
cash. The calibration named in the v4 round-1 brief as the bar to beat. Uses EMA only (not brief-compliant on its
own): it is the reference the protocol-v4.1 gates are measured against, not a candidate.
"""
import pandas as pd
from indicators import ema
from prepare import ETF_TICKER

PARAMS = dict(fast=20, slow=50)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    idx = md.index_close[["NIFTY50"]]
    on = (ema(idx, p["fast"]) > ema(idx, p["slow"]))["NIFTY50"]
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[ETF_TICKER] = on.astype(float)
    return W
