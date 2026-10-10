"""
Index-trend-timed diversified basket: NIFTYBEES core + an equal-weight sleeve of Nifty 50 members that have
pulled back below their VWAP. Long-only, daily bars, target weights decided at the close of day t (the harness
trades them at the open of t+1).

RULES (in plain words)

1. REGIME (EMA, index level). The book is invested only while the NIFTY 50 is in an uptrend on two speeds at
   once: EMA(idx_fast=20) > EMA(idx_slow=50) (the short trend is up) AND EMA(idx_slow=50) > EMA(lt_n=100) (the
   medium trend is up). Otherwise everything is in cash and earns the repo rate. Why it should hold: index-level
   time-series momentum is the one effect that was positive in every window here (2017-19, 2020-23, 2024-26);
   requiring the medium trend as well stops the book from buying every bounce inside a falling market (the
   2024-25 and 2026 down-legs), which is where a single fast crossover lost money.

2. CORE. While the regime is on, `core` (half) of the book is NIFTYBEES. Why: the cap-weighted index and an
   equal-weight member basket lead in different eras (large caps led in 2017-19, the broad index in 2020-26);
   holding both halves keeps the book from depending on which of the two wins.

3. SLEEVE (VWAP + RSI, stock level). The other half is spread equally over the members that are pulled back:
   close < their rolling vwap_n(50)-day VWAP, and not in free fall: Wilder RSI(rsi_n=14) >= rsi_lo (30).
   Each name gets at most 1/k_min (1/5) of a daily basket. Why: inside the Nifty 50, stocks that ran above
   their 50-day VWAP (and above 50 on RSI) lagged the stocks that fell below it over the next month in all
   three windows (one-month reversal among liquid large caps); buying the laggards of an uptrending market is
   the robust way to own "the trend" here. An RSI below 30 marks a collapse (often a news-driven break) rather
   than a pullback, so those names are skipped.

4. OVERLAPPING TRANCHES. Each day a fresh sleeve basket is formed and held for `hold` (21) sessions; the sleeve is
   the average of the last 21 daily baskets. This is a monthly holding period without an arbitrary rebalance
   date (a fixed monthly date made the result depend on the calendar phase), and it spreads the sleeve over
   roughly 30 names.

The harness sells non-members at the next open, carries names without a fresh bar and credits idle cash with the
repo rate. All indicators are causal and use at most 100 sessions of history.
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    idx_fast=20,      # NIFTY short EMA
    idx_slow=50,      # NIFTY medium EMA
    lt_n=100,         # NIFTY long EMA (medium trend filter)
    core=0.5,         # NIFTYBEES share of the book while the regime is on
    vwap_n=50,        # stock VWAP window: a pullback is close < VWAP(vwap_n)
    rsi_n=14,         # stock RSI length
    rsi_lo=30.0,      # skip names with RSI below this (free fall)
    k_min=5,          # a daily sleeve basket spreads over at least k_min slots
    hold=21,          # sessions each daily sleeve basket is held (overlapping tranches)
)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    c, h, l, v = md.close, md.high, md.low, md.volume

    # 1. regime on the NIFTY 50 index (signal only)
    ix = md.index_close["NIFTY50"].to_frame()
    e_fast = ema(ix, p["idx_fast"]).iloc[:, 0]
    e_slow = ema(ix, p["idx_slow"]).iloc[:, 0]
    e_long = ema(ix, p["lt_n"]).iloc[:, 0]
    on = ((e_fast > e_slow) & (e_slow > e_long)).astype(float)

    # 3. pulled-back members: below their VWAP, RSI not in free fall
    stock = md.member.copy()
    stock[ETF_TICKER] = False
    eligible = stock & md.tradable
    vw = rolling_vwap(h, l, c, v, p["vwap_n"])
    r = rsi(c, p["rsi_n"])
    pick = eligible & (c < vw) & (r >= p["rsi_lo"])
    n = pick.sum(axis=1)
    daily = pick.astype(float).div(np.maximum(n, p["k_min"]), axis=0)

    # 4. overlapping tranches: average of the last `hold` daily baskets
    sleeve = daily.rolling(p["hold"], min_periods=1).mean().fillna(0.0)

    # 2. core + sleeve, all scaled by the regime
    w = sleeve * (1.0 - p["core"])
    w[ETF_TICKER] = w[ETF_TICKER] + p["core"]
    w = w.mul(on, axis=0)
    return w.fillna(0.0)
