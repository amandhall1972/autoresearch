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
             weighted by beta^beta_power / idiosyncratic variance. The idiosyncratic vol is the vol_n-day
             realised vol with the part explained by the NIFTY 50 removed (vol * sqrt(1 - corr^2), corr over
             w_corr_n days), raised to vol_power (2: variance); beta is the same window's market beta
             (corr * vol / NIFTY vol), Blume-adjusted toward 1 (beta_shrink = 1/3) and floored at 0. The
             book is a timed bet on the market's up-leg: in a single-index model where each name earns its
             beta times that move, the mean-variance weights are beta / idiosyncratic variance; the square
             root (beta_power 0.5) hedges between that and equal expected returns (plain inverse
             idiosyncratic variance). No name may hold more than w_cap (40 %) of the cohort: the share above
             the cap is parked in NIFTYBEES (index exposure, held until the regime switches off), so a
             one- or two-name cohort or a near-1 correlation cannot put the book into a single stock.
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
             vwap_n days (up or down day alike: a news or block-trade day ends the orderly trend); (c) its
             vol_n/vol_long_n vol expansion ranks in the top 5 % of members (vol_exit_q, the exit-side
             twin of the calm entry filter). A name the exit rules would sell today is not bought into a
             new cohort either. (d) A trend break: close below vwap_long_exit x the rolling vwap_long_n-day
             VWAP (the slower VWAP catches names that drift down without breaching the 20-day stop).
  sizing   : a cohort bought at a FRESH regime start (not the re-entry after an RSI>80 stand-aside) is
             scaled by the BANK NIFTY's variance ratio, min(1, (vol_long_n-day vol / vol_n-day vol)^size_power):
             when the market's most liquid, most leveraged sector has just become more volatile than its
             norm, the new up-leg is less trustworthy and the book is smaller; the rest stays in cash. A
             re-cross after only whipsaw_days or fewer days off (a whipsaw) is bought at whipsaw_size of that.
Names with no fresh bar on a day stay held (they cannot be traded that day anyway).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap, realized_vol
from prepare import ETF_TICKER

PARAMS = dict(
    rsi_n=14, rsi_smooth=3, rsi_hi=70.0, rsi_peak=80.0, rsi_peak_n=20, rsi_lag=12.0,
    ema_fast=20, ema_slow=50, ema_spread=0.06,
    vwap_n=20, vwap_exit=0.985, shock_k=2.5, vspike_k=4.0, vwap_long_n=50, vwap_long_exit=0.975,
    vol_n=20, vol_power=2.0, vol_long_n=120, vol_calm_q=2.0 / 3.0, vol_exit_q=0.95, corr_n=20,
    w_corr_n=15, beta_power=0.5, beta_shrink=1.0 / 3.0, w_cap=0.4,
    regime_rsi_n=14, regime_rsi_mid=55.0, regime_rsi_band=2.0, regime_rsi_max=80.0,
    size_index="BANKNIFTY", size_power=2.0, whipsaw_days=3, whipsaw_size=0.5,
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
    vol = realized_vol(c, p["vol_n"])
    vol_rank = (vol / realized_vol(c, p["vol_long_n"])).where(stock).rank(axis=1, pct=True)   # NaN -> no entry
    entry = (eligible & trend & (c > vw) & (r < p["rsi_hi"]) & (r_peak < p["rsi_peak"])
             & (r.sub(r_idx, axis=0) > -p["rsi_lag"]) & (vol_rank < p["vol_calm_q"]))
    ret, idx_ret = c.pct_change(), md.index_close["NIFTY50"].pct_change()
    corr = ret.rolling(p["corr_n"], min_periods=p["corr_n"]).corr(idx_ret)
    ivol = vol * np.sqrt((1.0 - corr ** 2).clip(lower=0.0))          # residual (idiosyncratic) vol vs the NIFTY 50
    vol_ratio = vol.div(realized_vol(md.index_close[["NIFTY50"]], p["vol_n"])["NIFTY50"], axis=0)
    beta = corr * vol_ratio
    shock = (ret - beta.shift(1).mul(idx_ret, axis=0)) < -p["shock_k"] * ivol.shift(1) / np.sqrt(252)   # NaN -> no shock
    spike = v > p["vspike_k"] * v.rolling(p["vwap_n"], min_periods=p["vwap_n"]).mean().shift(1)   # NaN -> no spike
    vw_long = rolling_vwap(h, l, c, v, p["vwap_long_n"])
    hold = (stock & ~md.tradable) | (eligible & (c > vw * p["vwap_exit"]) & ~shock & ~(vol_rank >= p["vol_exit_q"]) & ~spike
                                     & (c > vw_long * p["vwap_long_exit"]))
    hold[ETF_TICKER] = True                                 # a parked index slot rides until the regime switches off
    e = md.tickers.index(ETF_TICKER)

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
    iv_v = (beta_w ** p["beta_power"] / ivol_w.where(ivol_w > 0) ** p["vol_power"]).fillna(0.0).values   # 0 when unknown
    last_on = -10 ** 6                                      # last day the regime was on
    for t in range(len(dates)):
        if not regime_v[t]:                                 # regime off: fully in cash
            held[:] = False
            w = np.zeros(N)
        elif t == 0 or not regime_v[t - 1]:                 # regime switches on: buy the cohort
            held = entry_v[t] & hold_v[t] & (iv_v[t] > 0)     # never buy a name the exit rules would sell today
            w = np.where(held, iv_v[t], 0.0)
            g = p["gross"]
            if t == 0 or not hyst[t - 1]:                   # fresh regime start (not a stand-aside re-entry)
                g *= size_v[t]                              # index variance-ratio sizing
                if t - last_on - 1 <= p["whipsaw_days"]:
                    g *= p["whipsaw_size"]                  # re-cross after only a few days off: whipsaw
            if held.any():
                w = w / w.sum()                             # cohort shares
                excess = np.clip(w - p["w_cap"], 0.0, None)   # share above the single-name cap ...
                w = g * (w - excess)
                w[e] = g * excess.sum(); held[e] = w[e] > 0   # ... is parked in NIFTYBEES (index exposure)
            else:
                w = np.zeros(N)
        else:                                               # hold the cohort, weights ride, no top-ups
            held &= hold_v[t]
            wg = w * g_v[t]
            w = np.where(held, wg, 0.0) / (wg.sum() + 1.0 - w.sum())
        if regime_v[t]:
            last_on = t
        W[t] = w
    return pd.DataFrame(W, index=dates, columns=md.tickers)
