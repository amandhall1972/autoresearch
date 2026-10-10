"""
Timed NIFTYBEES core + a leadership-gated laggard sleeve (protocol v4.1, round 2, lens: a stock sleeve that beats the
timed ETF). Long-only, daily bars, point-in-time Nifty 50. Target weights are decided with data up to the close of
day t; the harness trades them at the open of t+1.

THE RULES (all evaluated at the close of day t)

1. Market regime (EMA). The book is invested only while the NIFTY 50's EMA(fast=20) is above its EMA(slow=50); when
   it is not, everything is sold and the cash earns the repo rate. This is R0, unchanged: the one effect that held in
   all three windows in round 1 (time-series momentum of the index; it steps aside in drawn-out declines).

2. Leadership gate (EMA). An equal-weighted index of the current members is built from their daily close-to-close
   returns (the members of t-1 with a fresh bar). While the NIFTY 50 is in its uptrend, the stock sleeve is used only
   while the equal-weighted members are LEADING the cap-weighted NIFTY 50: EMA(fast) of log(NIFTY 50 / equal-weight
   index) below its EMA(slow) - the regime's own trend test, applied to the cap-weighted-vs-equal-weighted ratio.
   When the ratio's trend is up (a narrow market carried by a few heavyweights, as in 2018), the sleeve's weight is
   held in NIFTYBEES instead, because an equal-weighted basket of members is structurally short the heavyweights
   that are doing the lifting. This is the rule that keeps the sleeve from losing in 2017-19.

3. Laggard score (RSI + VWAP). Each eligible member (a current member with a fresh bar) gets the average of two
   cross-sectional percentile ranks: its Wilder RSI(fast) (one month of momentum) and its log distance from the
   rolling VWAP(slow) (where the last ~2.5 months of volume traded). A low score is a member that has fallen behind
   both in momentum and against the price at which its recent volume changed hands.

4. Sleeve (selection, holding, hysteresis). The book is split into `fast` = 20 equal tranches; tranche j reviews
   its holdings every 20 sessions, on the sessions whose position in the calendar is j modulo 20, so one tranche
   is reviewed each day and every name is held for about a month. At a review, while the leadership gate is open,
   the tranche keeps each held name whose score is still in the lower `keep` = half of the members, and fills its
   free slots up to `k` = 10 with the lowest-scoring members; a newly bought name gets `sleeve` / k = 5 % of the
   tranche, and kept names keep their drifted weight. While the gate is closed, the tranche sells its names into
   NIFTYBEES at the review. Everything a tranche does not hold in stocks is in NIFTYBEES while the regime is on.
   Between reviews nothing is traded except that names leaving the index are sold into NIFTYBEES. When the regime
   turns on, every tranche buys at once; when it turns off, every tranche goes to cash at once.

WHY IT SHOULD HOLD ACROSS REGIMES
   The diagnostics on 2017-2026 (scratch diag*.py) found that among Nifty 50 members the short-to-medium-term losers
   (low RSI, below VWAP) beat the member average in all three windows (rank IC of RSI vs the next month's relative
   return -0.05 / -0.02 / -0.01), whereas the winners (the same scores, top of the ranking) lagged NIFTYBEES in every
   window: a liquidity-provision/overreaction effect, not momentum. What hid it against NIFTYBEES was the size
   effect: any equal-weighted basket of members trails the cap-weighted index while a few heavyweights carry the
   market (the gated equal-weight of all members scores IR -1.26 vs NIFTYBEES in 2017-19). Rule 2 measures that
   condition directly and stands the sleeve down while it lasts, so the sleeve competes with NIFTYBEES only when the
   size headwind is absent. Selection is slow (monthly, hysteresis, 20 staggered tranches: about 6.7x turnover a
   year in all) so costs stay near 1 % a year. The momentum direction of the same score was tested and loses in every
   window.

PARAMETERS: fast and slow are R0's; the sleeve re-uses them as its horizons (RSI length and holding period = fast,
VWAP window and the leadership trend = fast/slow); k, sleeve and keep are new. use_rsi / use_ema / use_vwap switch
each indicator family to its neutral setting (rank on the other family only; regime and gate always on).
All lookbacks are <= 50 sessions; everything is causal (EMA/rolling windows, per-day cross-sectional ranks).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(use_rsi=True, use_ema=True, use_vwap=True,
              fast=20, slow=50,          # NIFTY regime; also RSI length / holding period (fast), VWAP window (slow)
              k=10,                      # names per tranche
              sleeve=0.5,                # share of each tranche in stocks while the gate is open
              keep=0.5)                  # hysteresis: a held name stays while its score is in this lower share


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    fast, slow, k, sl, keep = int(p["fast"]), int(p["slow"]), int(p["k"]), float(p["sleeve"]), float(p["keep"])
    C = md.close
    T, N = C.shape
    je = list(md.tickers).index(ETF_TICKER)
    mem = md.member.copy()
    mem[ETF_TICKER] = False
    elig = mem & md.tradable
    nifty = md.index_close[["NIFTY50"]]

    # rules 1-2: regime and leadership gate (EMA family)
    if p["use_ema"]:
        reg = (ema(nifty, fast) > ema(nifty, slow))["NIFTY50"].values
        cc = C.pct_change().where(elig.shift(1, fill_value=False))
        ew_index = (1.0 + cc.mean(axis=1).fillna(0.0)).cumprod()
        ratio = np.log(nifty["NIFTY50"] / ew_index).to_frame("r")
        broad = (ema(ratio, fast) < ema(ratio, slow))["r"].values
    else:
        reg = np.ones(T, bool)
        broad = np.ones(T, bool)

    # rule 3: laggard score (RSI and VWAP families)
    parts = []
    if p["use_rsi"]:
        parts.append(rsi(C, fast).where(elig).rank(axis=1, pct=True))
    if p["use_vwap"]:
        dist = np.log(C / rolling_vwap(md.high, md.low, C, md.volume, slow))
        parts.append(dist.where(elig).rank(axis=1, pct=True))
    score = sum(parts) / len(parts) if parts else elig.astype(float).where(elig)
    S = score.values
    OK = elig.values & np.isfinite(S)
    warm = OK.sum(axis=1) >= 30
    M = md.member.values
    Cv = C.values
    G = Cv / np.vstack([Cv[:1], Cv[:-1]])
    G = np.where(np.isfinite(G), G, 1.0)

    # rule 4: one tranche
    def tranche(offset):
        out = np.zeros((T, N))
        w = np.zeros(N)
        prev_on = False
        for t in range(T):
            if w.sum() > 0:                                    # drift with prices between reviews
                w = w * G[t] / ((w * G[t]).sum() + (1.0 - w.sum()))
            if not (reg[t] and warm[t]):                       # regime off: cash
                w = np.zeros(N)
                prev_on = False
                continue
            gone = (w > 0) & ~M[t]
            gone[je] = False
            if gone.any():                                     # index leavers are sold into NIFTYBEES
                w[je] += w[gone].sum()
                w[gone] = 0.0
            if (not prev_on) or (t % fast == offset):          # review
                nw = np.zeros(N)
                if broad[t]:
                    order = np.argsort(np.where(OK[t], S[t], np.inf), kind="stable")
                    rank = np.empty(N, int)
                    rank[order] = np.arange(N)
                    held = [j for j in np.flatnonzero(w > 0)
                            if j != je and OK[t, j] and rank[j] < keep * OK[t].sum()]
                    sel = list(held)
                    for j in order:
                        if len(sel) >= k or not OK[t, j]:
                            break
                        if j not in sel:
                            sel.append(j)
                    for j in sel:
                        nw[j] = w[j] if j in held else sl / k
                    if nw.sum() > 1.0:
                        nw /= nw.sum()
                nw[je] = 1.0 - nw.sum()
                w = nw
            prev_on = True
            out[t] = w
        return out

    W = sum(tranche(o) for o in range(fast)) / fast
    W = np.where(W > 1e-9, W, 0.0)
    gross = W.sum(axis=1, keepdims=True)
    W = np.where(gross > 0.999, W / np.maximum(gross, 1e-12) * 0.999, W)
    return pd.DataFrame(W, index=md.calendar, columns=md.tickers)
