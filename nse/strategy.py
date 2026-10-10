"""
RSI + EMA + VWAP strategy on the point-in-time Nifty 50 universe. Long-only, daily bars.
This is the ONLY file the research agent edits. The harness (prepare.py) executes the target
weights at the next day's open and charges costs; signals here must use data up to the close.

Rules (textbook confluence):
  trend    : EMA(fast) > EMA(slow) (a separate close > EMA(fast) check adds nothing once close > VWAP),
             but EMA(fast) no more than ema_spread above EMA(slow): a trend that is already stretched
             tends to stall over the short life of a cohort
  value    : close > rolling N-day VWAP (buyers in control)
  momentum : smoothed RSI(n) (EMA(rsi_smooth) of Wilder RSI, to damp one-day noise) below rsi_hi
             (not yet overbought; a lower bound such as RSI > 50 practically never binds once the trend
             and value filters hold, so there is none), and it has not been extremely overbought
             (above rsi_peak) at any close of the last rsi_peak_n days: a name that has just had its
             blow-off is skipped rather than bought as it cools back under rsi_hi; and it does not lag
             the market's own momentum (the same smoothed RSI computed on the NIFTY 50) by more than
             rsi_lag points. On a normal switch-on day the index RSI is in the mid 50s and this floor
             hardly binds; on the re-entry after an index RSI>80 stand-aside the market is hot and only
             names keeping pace with it are bought
  calm     : the name's vol_n-day realised volatility relative to its own vol_long_n-day volatility
             must not be in the top third of the members (vol_calm_q = 2/3): a name whose volatility has
             just expanded is moving on news, and calm, orderly trends carry the cohort better
  regime   : the NIFTY 50's own RSI(n) must be in its bullish zone: the book is switched on when the
             index RSI crosses above regime_rsi_mid + regime_rsi_band and switched off (fully to cash)
             when it falls below regime_rsi_mid - regime_rsi_band (hysteresis avoids whipsaw); the book
             also stands aside while the index RSI is extremely overbought (above regime_rsi_max)
  cohort   : the book is bought once, on the day the regime switches on: every member in confluence,
             weighted by beta / idiosyncratic variance. The idiosyncratic vol is the vol_n-day
             realised vol with the part explained by the NIFTY 50 removed (vol * sqrt(1 - corr^2), corr over
             w_corr_n days), raised to vol_power (2: variance); beta is the same window's market beta
             (corr * vol / NIFTY vol), Blume-adjusted toward 1 (beta_shrink = 1/3) and floored at 0. The
             book is a timed bet on the market's up-leg: in a single-index model where each name earns its
             beta times that move, the mean-variance weights are exactly beta / idiosyncratic variance (a
             square-root beta tilt was used before the vol budget below existed; with the riding book's
             risk budgeted, the full single-index weights are better). No name may hold more than w_cap
             (40 %) of the cohort: the share above the cap is parked in NIFTYBEES (index exposure, held
             until the regime switches off), so a one- or two-name cohort or a near-1 correlation cannot put the book into a single stock.
             Each name is then held while a looser exit condition is not triggered
             (hysteresis: close above vwap_exit * VWAP) and its weight rides with its own price (no
             rebalancing trades); a name that exits leaves its weight in cash. There are no
             replacement entries until the regime switches off and on again, so exposure decays as
             the move matures and the only trades are the cohort buy, single-name exits and the
             regime-off liquidation.
  exits    : besides the VWAP stop, a held name is sold when it stops being a calm trend: (a) an
             idiosyncratic shock, i.e. its return net of beta x the NIFTY 50 return falls more than
             shock_k residual sigmas (beta and residual vol from the previous day's vol_n/corr_n windows);
             (b) a volume spike, i.e. the day's volume exceeds vspike_k times its average over the previous
             vwap_n days (up or down day alike: a news or block-trade day ends the orderly trend); (c) a
             VWAP-ladder break: close below the vwap_short_n-day VWAP while that VWAP is
             below the vwap_long_n-day VWAP (the short-term volume-weighted price has slipped under the
             longer one and the stock trades below both: the trend has broken; no tuned band). A name the
             exit rules would sell today is not bought into a new cohort either. (A top-5 % vol-expansion
             exit was deleted once the vol budget below was in place: it no longer added anything.)
  vol budget: the riding book is trimmed (never topped up) so that its single-index model vol,
             sqrt((sum w*beta)^2 x NIFTY vol_n-day variance + sum w^2 x idiosyncratic variance), with the
             same Blume betas and idiosyncratic vols as the weights (NIFTYBEES: beta 1, no idio risk), stays
             at or below vol_cap (10 % a year). The first vol_cap_delay days after a cohort is bought are
             left untrimmed: the switch-on thrust is the bet itself, and the NIFTY vol at a switch-on still
             carries the sell-off that preceded it. As names exit, drift or market vol rises, the trims
             keep the remaining book inside the budget; when vol falls the book is not re-levered.
             A trim is not pro rata: it keeps what the cohort's own weighting likes best. Name i keeps
             min(1, lam x score_i) of its weight, lam set so the model vol lands on the cap; the score is
             today's beta / idiosyncratic variance for a fresh cohort (its mean-variance weight, so the trim
             cuts the names with the most idiosyncratic risk per unit of market exposure first) and today's
             ladder height x RSI lead for a stand-aside re-entry cohort (the score its weights were bought
             on). NIFTYBEES carries no idiosyncratic risk and is not trimmed. A held name whose EMA20 has run
             more than ema_spread above its EMA50 (stretched past the band it was bought in) counts at
             stretch_score (half) of its score, so the trims cut it first.
  re-rank  : on the first day the budget may trim (day vol_cap_delay + 1 of a cohort, when the switch-on
             thrust is over), the riding book is re-ranked by that same score of today and the bottom
             rerank_drop (a third, rounded down) of its held stock names are sold before the budget is
             applied: the cohort was bought on formation-day estimates, and three days later the names its
             own weighting likes least are the ones least worth carrying (a ranking by the formation-day
             score, or by the score's change since formation, does not do this). Done once per cohort; the
             proceeds stay in cash.
  exhaust  : once the names sold by the exit rules and the re-rank make up more than 1 - exhaust (60 %) of
             the cohort's formation stock weight, the stock remainder is sold (the NIFTYBEES slot rides on):
             the cohort is spent. Budget trims do not count (they would fire it far too often).
  sizing   : a cohort bought at a FRESH regime start (not the re-entry after an RSI>80 stand-aside) is
             scaled by the BANK NIFTY's variance ratio, min(1, (vol_long_n-day vol / vol_n-day vol)^size_power):
             when the market's most liquid, most leveraged sector has just become more volatile than its
             norm, the new up-leg is less trustworthy and the book is smaller; the rest stays in cash. A
             re-cross after only whipsaw_days or fewer days off (a whipsaw) is bought at whipsaw_size of that.
             A re-entry cohort after the RSI>80 stand-aside is weighted by ladder height
             (VWAP20/VWAP50 - 1)+ x rung instead: on those hot days the idiosyncratic-variance estimates are
             least reliable, and medium-term volume-weighted momentum ranks the names.
  weights  : cohort weight = Blume-adjusted beta / idiosyncratic variance (w_corr_n-day correlation),
             times min(1, days since the name's last shock or volume-spike day / vwap_n): names that were
             in the news recently are bought small and earn their full weight back over one VWAP window.
             No single name may exceed w_cap of the cohort; the excess is parked in NIFTYBEES.
Names with no fresh bar on a day stay held (they cannot be traded that day anyway).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap, realized_vol
from prepare import ETF_TICKER

PARAMS = dict(
    rsi_n=14, rsi_smooth=3, rsi_hi=70.0, rsi_peak=80.0, rsi_peak_n=20, rsi_lag=16.0, re_k=5,
    ema_fast=20, ema_slow=50, ema_spread=0.06,
    vwap_n=20, vwap_exit=0.985, shock_k=2.5, vspike_k=4.0, vwap_long_n=50, vwap_short_n=3,
    vol_n=20, vol_power=2.0, vol_long_n=120, vol_calm_q=2.0 / 3.0, corr_n=20,
    w_corr_n=15, beta_shrink=1.0 / 3.0, w_cap=0.4, rung_power=20.0,
    regime_rsi_n=14, regime_rsi_mid=55.0, regime_rsi_band=2.0, regime_rsi_max=80.0,
    size_index="BANKNIFTY", size_power=2.0, whipsaw_days=3, whipsaw_size=0.5, vol_cap=0.10, vol_cap_delay=2,
    rerank_drop=1.0 / 3.0, stretch_score=0.5, exhaust=0.4, dip_k=0.15, gross=1.0,
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


def budget_trim(w, b, s2, nv, cap, score):
    """Trim (never add) a book to the model-vol budget, keeping what the cohort's own weighting likes best: name i
    keeps min(1, lam x score_i) of its weight, lam (bisection) puts the model vol on the cap. NIFTYBEES scores
    infinite (no idiosyncratic risk) and is cut only if it alone is over the budget."""
    mvol = lambda x: np.sqrt((x @ b) ** 2 * nv ** 2 + x ** 2 @ s2)
    fin = np.isfinite(score); fs = np.where(fin, score, 0.0)
    keep = lambda lam: np.where(fin, np.minimum(1.0, lam * fs), 1.0)
    fin = fin & (w > 0)
    lo, hi = 0.0, (1.0 / max(score[fin].min(), 1e-12) if fin.any() else 0.0)   # at hi every name keeps its weight
    for _ in range(60):
        lam = 0.5 * (lo + hi)
        if mvol(w * keep(lam)) > cap:
            hi = lam
        else:
            lo = lam
    x = w * keep(lo)
    return x * min(1.0, cap / max(mvol(x), 1e-12))


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    c, h, l, v = md.close, md.high, md.low, md.volume
    r = ema(rsi(c, p["rsi_n"]), p["rsi_smooth"])
    ef, es = ema(c, p["ema_fast"]), ema(c, p["ema_slow"])
    vw = rolling_vwap(h, l, c, v, p["vwap_n"])
    idx_rsi = rsi(md.index_close[["NIFTY50"]], p["regime_rsi_n"])["NIFTY50"]
    hyst = hysteresis(idx_rsi, p["regime_rsi_mid"] + p["regime_rsi_band"], p["regime_rsi_mid"] - p["regime_rsi_band"])
    regime_v = hyst & (idx_rsi <= p["regime_rsi_max"]).values         # NaN compares False -> off
    ix = md.index_close[[p["size_index"]]]
    idx_vr = (realized_vol(ix, p["vol_long_n"]) / realized_vol(ix, p["vol_n"]))[p["size_index"]]
    size_v = (idx_vr ** p["size_power"]).clip(upper=1.0).fillna(0.0).values   # index variance ratio (NaN -> 0)

    stock = md.member.copy(); stock[ETF_TICKER] = False          # the ETF is not part of the stock pool
    eligible = stock & md.tradable
    r_peak = r.rolling(p["rsi_peak_n"], min_periods=p["rsi_peak_n"]).max()   # NaN (warm-up) compares False
    r_idx = ema(rsi(md.index_close[["NIFTY50"]], p["rsi_n"]), p["rsi_smooth"])["NIFTY50"]   # same RSI, on the index
    trend = (ef > es) & (ef < es * (1.0 + p["ema_spread"]))         # up, but not stretched
    stretched_v = (ef > es * (1.0 + p["ema_spread"])).values          # a held name past the cap (NaN -> False)
    vol = realized_vol(c, p["vol_n"])
    vol_rank = (vol / realized_vol(c, p["vol_long_n"])).where(stock).rank(axis=1, pct=True)   # NaN -> no entry
    entry = (eligible & trend & (c > vw) & (r < p["rsi_hi"]) & (r_peak < p["rsi_peak"]) & (vol_rank < p["vol_calm_q"]))
    lead_v = r.sub(r_idx - p["rsi_lag"], axis=0).clip(lower=0.0).fillna(0.0).values   # RSI lead over (index RSI - lag)
    ret, idx_ret = c.pct_change(), md.index_close["NIFTY50"].pct_change()
    corr = ret.rolling(p["corr_n"], min_periods=p["corr_n"]).corr(idx_ret)
    ivol = vol * np.sqrt((1.0 - corr ** 2).clip(lower=0.0))          # residual (idiosyncratic) vol vs the NIFTY 50
    nvol = realized_vol(md.index_close[["NIFTY50"]], p["vol_n"])["NIFTY50"]
    vol_ratio = vol.div(nvol, axis=0)
    beta = corr * vol_ratio
    shock = (ret - beta.shift(1).mul(idx_ret, axis=0)) < -p["shock_k"] * ivol.shift(1) / np.sqrt(252)   # NaN -> no shock
    spike = v > p["vspike_k"] * v.rolling(p["vwap_n"], min_periods=p["vwap_n"]).mean().shift(1)   # NaN -> no spike
    vw_short = rolling_vwap(h, l, c, v, p["vwap_short_n"])
    vw_long = rolling_vwap(h, l, c, v, p["vwap_long_n"])
    ladder_break = (c < vw_short) & (vw_short < vw_long)            # NaN -> no break
    rung_v = ((c / vw_short) ** p["rung_power"]).fillna(0.0).values     # bottom-rung tilt (close/VWAP3)^k (NaN -> 0)
    height_v = (vw / vw_long - 1.0).clip(lower=0.0).fillna(0.0).values * lead_v   # re-entry: ladder height x RSI lead
    hold = (stock & ~md.tradable) | (eligible & (c > vw * p["vwap_exit"]) & ~shock & ~spike
                                     & ~ladder_break)
    hold[ETF_TICKER] = True                                 # a parked index slot rides until the regime switches off
    e = md.tickers.index(ETF_TICKER)
    nontrad_v = (stock & ~md.tradable).values               # held members without a fresh bar (cannot be traded)
    stock_col = np.arange(len(md.tickers)) != e

    dates = md.calendar
    N = len(md.tickers)
    W = np.zeros((len(dates), N))
    held = np.zeros(N, dtype=bool)
    w = np.zeros(N)                                         # yesterday's target weights
    entry_v, hold_v = entry.values, hold.values
    g_v = (c / c.shift(1)).fillna(1.0).values               # close-to-close growth (1 when unknown)
    corr_w = ret.rolling(p["w_corr_n"], min_periods=p["w_corr_n"]).corr(idx_ret)   # faster correlation for the weights
    ivol_w = vol * np.sqrt((1.0 - corr_w ** 2).clip(lower=0.0))
    beta_w = ((1.0 - p["beta_shrink"]) * corr_w * vol_ratio + p["beta_shrink"]).clip(lower=0.0)   # Blume-adjusted beta
    iv_v = (beta_w / ivol_w.where(ivol_w > 0) ** p["vol_power"]).fillna(0.0).values   # 0 when unknown
    i_v = np.arange(len(dates))[:, None]                    # days since the name's last abnormal day (shock or spike)
    age = i_v - pd.DataFrame(np.where((shock | spike).values, i_v, np.nan)).ffill().values   # NaN: none on record
    iv_v = iv_v * np.nan_to_num(np.minimum(1.0, age / p["vwap_n"]), nan=1.0) * rung_v   # ramp back over vwap_n days
    b_v = beta_w.fillna(0.0).to_numpy(copy=True); b_v[:, e] = 1.0          # single-index model of the riding book: betas (ETF 1)
    s2_v = (ivol_w ** 2).fillna(0.0).to_numpy(copy=True); s2_v[:, e] = 0.0  # ... idiosyncratic variances (ETF 0)
    nv_v = nvol.values                                       # ... and the NIFTY 50 vol (NaN -> no trim)
    mvs_v = np.where(s2_v > 0, b_v / np.where(s2_v > 0, s2_v, 1.0), np.inf)   # trim scores: fresh cohorts beta / idio var,
    hs_v = height_v.copy(); hs_v[:, e] = np.inf                                # re-entry cohorts their ladder height
    score_v = mvs_v
    breadth = ((c > vw) & eligible).sum(axis=1) / eligible.sum(axis=1).replace(0, np.nan)   # share above VWAP20
    dip = breadth < breadth.rolling(p["vwap_n"], min_periods=p["vwap_n"]).mean() - p["dip_k"]   # NaN -> no dip
    dip_up_v = (dip.shift(1, fill_value=False) & (breadth > breadth.shift(1))).values   # yesterday a dip, today up
    last_on, t_form = -10 ** 6, -10 ** 6                    # last day the regime was on; the cohort's formation day
    fresh = True                                            # the riding cohort is a fresh (not a re-entry) cohort
    w0 = np.zeros(N)                                        # the cohort's formation stock weights
    for t in range(len(dates)):
        if not regime_v[t]:                                 # regime off: fully in cash
            held[:] = False
            w = np.zeros(N)
        elif t == 0 or not regime_v[t - 1]:                 # regime switches on: buy the cohort
            t_form, score_v, fresh = t, mvs_v, True
            held = entry_v[t] & hold_v[t] & (iv_v[t] > 0)     # never buy a name the exit rules would sell today
            w = np.where(held, iv_v[t], 0.0)
            g = p["gross"]
            if t == 0 or not hyst[t - 1]:                   # fresh regime start (not a stand-aside re-entry)
                g *= size_v[t]                              # index variance-ratio sizing
                if t - last_on - 1 <= p["whipsaw_days"]:
                    g *= p["whipsaw_size"]                  # re-cross after only a few days off: whipsaw
            else:                                           # re-entry after the RSI>80 stand-aside: height x lead weights
                w = np.where(held, height_v[t], 0.0)
                if (w > 0).sum() > p["re_k"]:
                    w = np.where(w >= np.sort(w)[-p["re_k"]], w, 0.0)   # only the re_k strongest names
                held &= w > 0                               # no ladder height or no RSI lead: not bought
                score_v, fresh = hs_v, False
            if held.any():
                w = w / w.sum()                             # cohort shares
                excess = np.clip(w - p["w_cap"], 0.0, None)   # share above the single-name cap ...
                w = g * (w - excess)
                w[e] = g * excess.sum(); held[e] = w[e] > 0   # ... is parked in NIFTYBEES (index exposure)
            else:
                w = np.zeros(N)
            w0 = w.copy(); w0[e] = 0.0
        else:                                               # hold the cohort, weights ride, no top-ups
            held &= hold_v[t]
            if w0.sum() > 0 and (w0 * held).sum() < p["exhaust"] * w0.sum():   # the exits sold most of the cohort:
                held[w0 > 0] = False                        # it is spent, sell the stock remainder (NIFTYBEES rides on)
            wg = w * g_v[t]
            w = np.where(held, wg, 0.0) / (wg.sum() + 1.0 - w.sum())
            sc = np.where(stretched_v[t], score_v[t] * p["stretch_score"], score_v[t])   # past the EMA band cap: cut first
            if t - t_form == p["vol_cap_delay"] + 1:        # the first day the budget may trim: re-rank the book
                ix_h = np.flatnonzero(held & (w > 0) & stock_col)
                k = int(np.floor(p["rerank_drop"] * len(ix_h)))
                if len(ix_h) >= 3 and k > 0:                # sell the bottom third by today's own score
                    drop = ix_h[np.argsort(sc[ix_h], kind="stable")[:k]]
                    drop = drop[~nontrad_v[t, drop]]        # a name with no fresh bar cannot be sold today
                    held[drop] = False; w[drop] = 0.0
            mv = np.sqrt((w @ b_v[t]) ** 2 * nv_v[t] ** 2 + w ** 2 @ s2_v[t])   # the riding book's model vol
            if mv > p["vol_cap"] and t - t_form > p["vol_cap_delay"]:   # the first days of the up-leg run untrimmed
                w = budget_trim(w, b_v[t], s2_v[t], nv_v[t], p["vol_cap"], sc)   # trim (never add) to budget
            x = np.where(held, w, 0.0); x[e] = 0.0             # breadth-dip recovery: top up the riding stock names
            if dip_up_v[t] and fresh and x.sum() > 0:       # pro rata, with idle cash, up to the vol budget
                x = x / x.sum()
                mvol = lambda a: np.sqrt(((w + a * x) @ b_v[t]) ** 2 * nv_v[t] ** 2 + (w + a * x) ** 2 @ s2_v[t])
                lo, hi = 0.0, max(0.0, 1.0 - w.sum())
                if mvol(hi) > p["vol_cap"]:
                    for _ in range(40):
                        mid = 0.5 * (lo + hi)
                        lo, hi = (lo, mid) if mvol(mid) > p["vol_cap"] else (mid, hi)
                else:
                    lo = hi
                w = w + lo * x
        if regime_v[t]:
            last_on = t
        W[t] = w
    return pd.DataFrame(W, index=dates, columns=md.tickers)
