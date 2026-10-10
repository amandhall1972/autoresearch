"""
Relative-laggard rotation inside an index uptrend (lens: relative strength vs the NIFTY).

What the research found (2017-2026, point-in-time Nifty 50): among Nifty 50 members, relative strength vs the
index is NOT persistent at the 1-3 month horizon after 2019. The members with the strongest relative RSI / EMA /
VWAP readings lagged the member average over the next 1-3 months in 2020-2023 and 2024-2026 (they led only in
2017-2019), while the members with the weakest short-term relative strength beat the member average in all three
windows. So this strategy uses relative strength vs the index in the contrarian direction: while the market is in
an uptrend it owns the members that have lagged the index most over the last few weeks, for about two months.

Rules (decided at the close of day t, executed by the harness at the open of t+1):

1. Regime (EMA). Invest only while the NIFTY 50's EMA(reg_fast=20) is above its EMA(reg_slow=50); otherwise
   everything is in cash (which earns the repo rate). Short-term reversal is a liquidity-provision effect that
   pays when the broad trend is up; in a falling market the laggards keep lagging, so the book steps aside.

2. Relative-weakness score (RSI + VWAP vs the index). For every member on day t:
     a = RSI(n)(stock) - RSI(n)(NIFTY 50)                                (relative Wilder RSI, n = 14)
     b = log(close / VWAP(n))(stock) - log(close / VWAP(n))(NIFTYBEES)    (relative distance to the n-day VWAP)
   Each is turned into a cross-sectional percentile rank among the day's members and the two ranks are averaged.
   A low score = the stock has lagged the index both in momentum (RSI) and in price relative to where its recent
   volume traded (VWAP). The index terms do not change the ordering; they make the score read "vs the index".

3. Selection with hysteresis, in six weekly-staggered sleeves. The book is split into `tranches` (6) equal
   sleeves. When the regime turns on every sleeve buys at once; after that sleeve k is reviewed 7*k sessions
   after the turn-on and then every `rebal` (42) sessions, so one sleeve is reviewed each week. At a review a
   held name stays while its score rank is better than `exit_rank` (25 = the weaker half of the index) and the
   sleeve's free slots (`n_hold` = 10) are filled with the lowest-scoring members. Between reviews nothing is
   traded, except that names leaving the index are sold. Staggering removes the dependence of the result on
   the particular review dates (with a single book, moving the review cycle by a few sessions swung the
   2017-2019 Sharpe by more than 0.4).

4. Sizing. A newly bought name gets 1/n_hold of its sleeve times (median member volatility / its own
   volatility), clipped to 0.5x..2x, using 60-session realized volatility; incumbents keep their drifted weight.
   Volatility sizing keeps the most volatile laggards from dominating the risk of the book. Weight a sleeve does
   not use for stocks is held in NIFTYBEES while the regime is on.

Why it should hold across regimes: rule 1 is a slow trend filter (index exposure only in uptrends), rules 2-3
are a cross-sectional short-term reversal bet measured relative to the index, rule 4 is plain risk budgeting.
No rule depends on a calendar date or on a level fitted to one period. Known weakness: in a narrow market led by
a few mega-caps (2017-2018) an equal-risk basket of laggards trails the cap-weighted index.
All lookbacks are <= 60 sessions; everything is causal (rolling/EWM windows and per-day cross-sectional ranks).
"""
import numpy as np
import pandas as pd
import indicators as I
from prepare import ETF_TICKER

PARAMS = dict(
    n=14,            # RSI and VWAP window of the relative-weakness score
    n_hold=10,       # stock slots per sleeve
    exit_rank=25,    # at a review a held name is sold once its rank is >= exit_rank (0 = most lagging member)
    rebal=42,        # sessions between two reviews of the same sleeve while the regime stays on
    tranches=6,      # sleeves; their reviews are staggered by rebal / tranches = 7 sessions (one sleeve a week)
    vol_n=60,        # realized-volatility window for sizing
    reg_fast=20,     # NIFTY EMA fast
    reg_slow=50,     # NIFTY EMA slow
)


def compute_target_weights(md):
    p = PARAMS
    C = md.close
    tick = list(md.tickers)
    j_etf = tick.index(ETF_TICKER)
    n = int(p["n"])
    idx = md.index_close["NIFTY50"].to_frame()
    mem = md.member.copy()
    mem[ETF_TICKER] = False

    # --- relative-weakness score (rule 2)
    rel_rsi = I.rsi(C, n).sub(I.rsi(idx, n).iloc[:, 0], axis=0)
    dist = np.log(C / I.rolling_vwap(md.high, md.low, C, md.volume, n))
    rel_vwap = dist.sub(dist[ETF_TICKER], axis=0)
    score = (rel_rsi.where(mem).rank(axis=1, pct=True) + rel_vwap.where(mem).rank(axis=1, pct=True)) / 2.0
    score = score + 1e-9 * rel_rsi            # deterministic tie-break
    elig = (mem & md.tradable & score.notna()).values
    warm = (score.notna().sum(axis=1) >= 30).values

    # --- regime (rule 1)
    f = I.ema(idx, int(p["reg_fast"])).iloc[:, 0]
    s = I.ema(idx, int(p["reg_slow"])).iloc[:, 0]
    reg = (f > s).values
    reg_ok = (f.notna() & s.notna()).values

    # --- volatility for sizing (rule 4)
    vol = I.realized_vol(C, int(p["vol_n"])).where(mem)
    VOL = vol.values
    VMED = vol.median(axis=1).values

    S = score.values
    Cv = C.values
    M = md.member.values
    T, N = Cv.shape
    n_hold = int(p["n_hold"]); exit_rank = int(p["exit_rank"]); rebal = int(p["rebal"])
    K = int(p["tranches"])

    def book(offset):
        out = np.zeros((T, N))
        w = np.zeros(N)
        last_review = -10 ** 9
        prev_on = None
        for t in range(T):
            if t > 0 and w.sum() > 0:                   # let the book drift with prices between reviews
                g = Cv[t] / Cv[t - 1]
                g = np.where(np.isfinite(g), g, 1.0)
                w = w * g / ((w * g).sum() + (1.0 - w.sum()))
            gone = (w > 0) & ~M[t]                      # names that left the index are sold
            gone[j_etf] = False
            if gone.any():
                freed = w[gone].sum()
                w[gone] = 0.0
                if reg[t]:
                    w[j_etf] += freed
            if not (reg_ok[t] and warm[t]):
                out[t] = w
                continue
            on = bool(reg[t])
            turn = prev_on is None or on != prev_on
            if (t - last_review >= rebal) or turn:
                # a tranche with offset o reviews o sessions after the regime turns on, then every `rebal`
                last_review = t - ((rebal - offset) % rebal) if turn else t
                if not on:
                    w = np.zeros(N)
                else:
                    sc = np.where(elig[t], S[t], np.inf)
                    order = np.argsort(sc, kind="stable")    # most lagging first
                    rank = np.empty(N, int)
                    rank[order] = np.arange(N)
                    keep = [j for j in np.flatnonzero(w > 0) if j != j_etf and elig[t, j] and rank[j] < exit_rank]
                    sel = list(keep)
                    for j in order:
                        if len(sel) >= n_hold or not elig[t, j]:
                            break
                        if j not in sel:
                            sel.append(j)
                    nw = np.zeros(N)
                    for j in sel:
                        if j in keep:
                            nw[j] = w[j]
                        else:
                            v = VOL[t, j]
                            k = float(np.clip(VMED[t] / v, 0.5, 2.0)) if (np.isfinite(v) and v > 0) else 1.0
                            nw[j] = k / n_hold
                    if nw.sum() > 1.0:
                        nw /= nw.sum()
                    nw[j_etf] = max(0.0, 1.0 - nw.sum())
                    w = nw
            prev_on = on
            out[t] = w
        return out

    out = sum(book(int(round(k * rebal / K))) for k in range(K)) / K
    out = np.where(out > 1e-9, out, 0.0)
    gs = out.sum(axis=1, keepdims=True)
    out = np.where(gs > 0.999, out / np.maximum(gs, 1e-12) * 0.999, out)
    return pd.DataFrame(out, index=C.index, columns=C.columns)
