"""
Fixed harness for the NSE RSI+EMA+VWAP autoresearch loop. DO NOT MODIFY.

Provides: constants, data loading/cleaning, point-in-time Nifty 50 universe, risk-free rate,
the execution/cost engine, performance metrics and a dense lookahead detector.

Data: FMP EOD (split/bonus/demerger-adjusted) for every stock that was a Nifty 50 member at
any time in 2020-2026, plus the NIFTY 50 index, BANK NIFTY index and the NIFTYBEES ETF.
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

# ---------------------------------------------------------------------------
# Constants (fixed, do not modify)
# ---------------------------------------------------------------------------
WARMUP_START = "2016-06-01"     # price history available from here (>= 880 bars of indicator warm-up)
START = "2020-01-01"            # backtest start
IS_END = "2023-12-31"           # in-sample / development period end (inclusive)
OOS_START = "2024-01-01"        # out-of-sample period start
END = "2026-10-09"              # backtest end (last available bar)
TRADING_DAYS = 252
COST_BPS_PER_SIDE = 15.0        # one-way cost in bps of traded value: STT 10 + exch/SEBI/stamp/GST ~1 + slippage ~4
MIN_NAMES_FOR_SESSION = 30      # a date is a trading day only if >= this many stocks have a bar
INDEX_TICKERS = ("NIFTY50", "BANKNIFTY")   # signal-only, never tradable
ETF_TICKER = "NIFTYBEES"                   # tradable proxy for the index (always "member")
MAX_GROSS = 1.0                 # no leverage; long-only cash equities
# One-hour Muhurat sessions (Diwali evening) are not regular trading days: excluded from the calendar.
MUHURAT_SESSIONS = ("2016-10-30", "2017-10-19", "2018-11-07", "2019-10-27", "2020-11-14", "2021-11-04",
                    "2022-10-24", "2023-11-12", "2024-11-01", "2025-10-21")
# Vendor demerger adjustments that differ from the NSE special-session ex-prices: multiply all bars
# strictly before `date` by `factor` so that open/prev_close on the ex-date is the true market gap.
CORPORATE_ACTION_FIXES = (
    ("RELIANCE", "2023-07-20", 0.9079 / 0.9230),     # Jio Financial: NSE ex-factor 2580/2841.85 vs vendor 0.9230
    ("ITC", "2025-01-06", 0.9437 / 0.9416),          # ITC Hotels: 455.60/482.60 vs vendor 0.9416
    ("TATAMOTORS", "2025-10-14", 400.0 / 398.91),    # TMCV: discovered PV price 400 vs vendor 398.91
)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _fname(ticker: str) -> str:
    return ticker.replace("&", "_") + ".csv"


def _read_raw(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(DATA_DIR, "raw", _fname(ticker)), parse_dates=["date"]).set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # stale rows: zero volume and a flat bar -> vendor gap (price carried forward); treat as missing
    stale = (df["volume"] == 0) & (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    df.loc[stale, ["open", "high", "low", "close", "volume"]] = np.nan
    for t, d, f in CORPORATE_ACTION_FIXES:
        if t == ticker:
            m = df.index < pd.Timestamp(d)
            df.loc[m, ["open", "high", "low", "close"]] *= f
    return df


def available_tickers():
    return sorted(f[:-4].replace("_", "&") for f in os.listdir(os.path.join(DATA_DIR, "raw")) if f.endswith(".csv"))


class MarketData:
    """Wide frames aligned on the master trading calendar (index=dates, columns=tickers).

    open/high/low/close : prices; on a stale/missing day the previous close is carried into all four
    volume              : shares; 0 on stale days
    tradable            : bool, a fresh bar exists for (date, ticker) - orders can only be filled then
    member              : bool, point-in-time Nifty 50 membership (the ETF is always True)
    index_close         : DataFrame with NIFTY50 and BANKNIFTY closes (signal only)
    index_ohlc          : dict of DataFrames open/high/low/close for the two indices
    rf                  : Series, daily risk-free return on the calendar
    """

    def __init__(self, open_, high, low, close, volume, tradable, member, index_close, index_ohlc, rf, calendar):
        self.open, self.high, self.low, self.close, self.volume = open_, high, low, close, volume
        self.tradable, self.member = tradable, member
        self.index_close, self.index_ohlc, self.rf, self.calendar = index_close, index_ohlc, rf, calendar
        self.tickers = list(close.columns)

    def _rows(self, cal):
        return MarketData(self.open.loc[cal], self.high.loc[cal], self.low.loc[cal], self.close.loc[cal],
                          self.volume.loc[cal], self.tradable.loc[cal], self.member.loc[cal], self.index_close.loc[cal],
                          {k: v.loc[cal] for k, v in self.index_ohlc.items()}, self.rf.loc[cal], cal)

    def slice(self, end_date) -> "MarketData":
        """Copy truncated at end_date (inclusive)."""
        return self._rows(self.calendar[self.calendar <= pd.Timestamp(end_date)])

    def corrupt_after(self, end_date, rng) -> "MarketData":
        """Copy where every bar strictly after end_date is replaced by a random permutation of the
        later bars (all frames permuted together, calendar unchanged). Used by the lookahead
        detector: a causal strategy gives identical weights up to end_date."""
        cal = self.calendar
        after = np.where(cal > pd.Timestamp(end_date))[0]
        if len(after) < 2:
            return self
        perm = rng.permutation(after)
        order = np.arange(len(cal)); order[after] = perm

        def P(df):
            out = df.iloc[order].copy(); out.index = cal; return out

        return MarketData(P(self.open), P(self.high), P(self.low), P(self.close), P(self.volume), P(self.tradable),
                          P(self.member), P(self.index_close), {k: P(v) for k, v in self.index_ohlc.items()}, P(self.rf), cal)


def load_rf(calendar: pd.DatetimeIndex) -> pd.Series:
    """Daily risk-free return from the RBI repo-rate schedule (ACT/365 accrual between trading days)."""
    sched = pd.read_csv(os.path.join(DATA_DIR, "rf_repo_rate.csv"), parse_dates=["effective_date"]).sort_values("effective_date")
    rate = pd.Series(sched["repo_rate_pct"].values / 100.0, index=sched["effective_date"])
    rate = rate.reindex(calendar.union(rate.index)).ffill().reindex(calendar)
    days = pd.Series(calendar, index=calendar).diff().dt.days.fillna(1).clip(lower=1)
    return (rate * days / 365.0).rename("rf")


def load_universe(calendar: pd.DatetimeIndex, tickers) -> pd.DataFrame:
    """Point-in-time membership: a stock is a member on date t if it was in the index per the most
    recent membership row on or before t (rows are month-ends and NSE effective dates of changes).
    Changes after the matrix ends come from data/universe/nifty50_changes_post_dataset.csv."""
    w = pd.read_csv(os.path.join(DATA_DIR, "universe", "nifty50_weights_2019_2025.csv"), parse_dates=["DATE"]).set_index("DATE")
    mem = (w > 0)
    ch = pd.read_csv(os.path.join(DATA_DIR, "universe", "nifty50_changes_post_dataset.csv"), parse_dates=["effective_date"])
    new_cols = [t for t in ch["ticker"].unique() if t not in mem.columns]
    mem = pd.concat([mem, pd.DataFrame(False, index=mem.index, columns=new_cols)], axis=1)
    for d, g in ch.groupby("effective_date"):
        row = mem.loc[mem.index[mem.index <= d].max()].copy()
        for _, r in g.iterrows():
            row[r["ticker"]] = (r["action"] == "add")
        mem.loc[d] = row
    mem = mem.sort_index().astype(bool)
    full = mem.reindex(calendar.union(mem.index)).ffill().reindex(calendar).fillna(False).astype(bool)
    full = full.reindex(columns=tickers, fill_value=False)
    full[ETF_TICKER] = True
    return full


def load_data(start=WARMUP_START, end=END) -> MarketData:
    tickers = available_tickers()
    raws = {t: _read_raw(t) for t in tickers}
    stocks = [t for t in tickers if t not in INDEX_TICKERS]
    # master calendar: NIFTY50 bars that are not flat, intersected with days where enough stocks traded,
    # minus Muhurat sessions
    nifty = raws["NIFTY50"]
    nifty = nifty[(nifty["high"] != nifty["low"]) & nifty["close"].notna()]
    closes = pd.concat([raws[t]["close"].rename(t) for t in stocks], axis=1, sort=True)
    enough = closes.notna().sum(axis=1) >= MIN_NAMES_FOR_SESSION
    calendar = nifty.index.intersection(closes.index[enough])
    calendar = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    calendar = calendar.difference(pd.to_datetime(list(MUHURAT_SESSIONS)))

    def wide(col):
        return pd.concat([raws[t][col].rename(t) for t in stocks], axis=1, sort=True).reindex(calendar)

    o, h, l, c, v = (wide(x) for x in ["open", "high", "low", "close", "volume"])
    fresh = c.notna() & o.notna() & h.notna() & l.notna() & (v > 0)
    listed = c.ffill().notna()                       # after the first bar
    c_ff = c.ffill()
    o = o.where(fresh, c_ff); h = h.where(fresh, c_ff); l = l.where(fresh, c_ff)
    v = v.where(fresh, 0.0).where(listed)
    idx_ohlc = {col: pd.concat([raws[t][col].rename(t) for t in INDEX_TICKERS], axis=1, sort=True).reindex(calendar).ffill()
                for col in ["open", "high", "low", "close"]}
    # The ETF's vendor opens are first-print artifacts (open == low or high on half the bars). Replace the
    # ETF's open/high/low by the index's open/high/low gaps applied to the ETF's previous close.
    e = ETF_TICKER
    ic = idx_ohlc["close"]["NIFTY50"]; ipc = ic.shift(1)
    pc = c_ff[e].shift(1)
    o_s = pc * idx_ohlc["open"]["NIFTY50"] / ipc
    h_s = pc * idx_ohlc["high"]["NIFTY50"] / ipc
    l_s = pc * idx_ohlc["low"]["NIFTY50"] / ipc
    ok = o_s.notna() & fresh[e]
    o[e] = o_s.where(ok, o[e])
    h[e] = np.maximum(np.maximum(h_s.where(ok, h[e]), o[e]), c_ff[e])
    l[e] = np.minimum(np.minimum(l_s.where(ok, l[e]), o[e]), c_ff[e])
    rf = load_rf(calendar)
    if rf.isna().any():
        raise ValueError("risk-free schedule does not cover the calendar")
    member = load_universe(calendar, stocks)
    return MarketData(o, h, l, c_ff, v, fresh, member, idx_ohlc["close"], idx_ohlc, rf, calendar)


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------

def apply_harness_rules(weights: pd.DataFrame, md: MarketData) -> tuple:
    """Turn the strategy's target weights into executable targets.

    * raises if any weight is negative or any row's gross exceeds MAX_GROSS (strategy violation)
    * a name with no fresh bar keeps its previous executable target (it cannot be traded that day)
    * a non-member is sold at the next open (weight forced to 0)
    * a name that never trades again is liquidated at its last fresh close
    * if the stale carry pushes a row above MAX_GROSS, the fresh names are scaled down to fit
    Returns (executable weights, number of rows that needed the stale-carry rescale).
    """
    W = weights.reindex(index=md.calendar, columns=md.tickers).fillna(0.0).astype(float)
    if (W < -1e-12).any().any():
        raise ValueError("long-only: negative weights found")
    gross = W.sum(axis=1)
    if (gross > MAX_GROSS + 1e-9).any():
        raise ValueError(f"gross exposure exceeds {MAX_GROSS}: max {gross.max():.4f}")
    W = W.where(md.tradable, np.nan).ffill().fillna(0.0)     # stale day: carry the previous target
    W = W.where(md.member, 0.0)                                # non-members cannot be held
    last_fresh = md.tradable.apply(lambda s: s[s].index.max() if s.any() else pd.NaT)
    for t in md.tickers:
        lf = last_fresh[t]
        if pd.notna(lf) and lf < md.calendar[-1]:
            W.loc[W.index > lf, t] = 0.0
    gross = W.sum(axis=1)
    over = gross > MAX_GROSS + 1e-12
    n_fix = int(over.sum())
    if n_fix:
        stale_w = W.where(~md.tradable, 0.0)
        fresh_w = W.where(md.tradable, 0.0)
        room = (MAX_GROSS - stale_w.sum(axis=1)).clip(lower=0.0)
        scale = (room / fresh_w.sum(axis=1).replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
        W = stale_w.add(fresh_w.mul(scale.where(over, 1.0), axis=0))
    return W, n_fix


def run_backtest(weights: pd.DataFrame, md: MarketData, cost_bps=COST_BPS_PER_SIDE) -> dict:
    """
    weights.loc[t, i]: target weight decided with information up to the close of t,
    executed at the OPEN of t+1. Long-only, sum(weights) <= MAX_GROSS.

    Exact portfolio accounting in weight space. State at the close of each day = holdings weights h
    and cash weight (fractions of portfolio value). For day k:
      overnight  : holdings earn open_k/close_{k-1}-1, cash earns half a day of rf
      rebalance  : at open_k trade from the drifted weights to the target w_{k-1}; cost_bps on |trade|
      intraday   : the new holdings earn close_k/open_k-1, cash earns the other half day of rf
      carry      : the end-of-day drifted state (not the target) is the start state of day k+1
    Returns dict: 'ret' (net), 'ret_gross' (no costs), 'turnover' (fraction of value traded),
    'exposure' (gross weight held during the day), 'weights' (executable targets), 'n_harness_fixes'.
    """
    W, n_fix = apply_harness_rules(weights, md)
    c = md.close.values; o = md.open.values
    T, N = c.shape
    r_on = np.zeros((T, N)); r_id = np.zeros((T, N))
    r_on[1:] = o[1:] / c[:-1] - 1.0
    r_id[:] = c / o - 1.0
    r_on = np.nan_to_num(r_on); r_id = np.nan_to_num(r_id)
    w = W.values
    cf = np.sqrt(1.0 + md.rf.values)                 # half-day cash factor; (cf*cf = 1 + rf)
    cr = cost_bps / 1e4
    ret = np.zeros(T); ret_g = np.zeros(T); turn = np.zeros(T); expo = np.zeros(T)
    h = np.zeros(N); cash = 1.0                       # net path state
    hg = np.zeros(N); cashg = 1.0                     # gross (cost-free) path state
    for k in range(1, T):
        wt = w[k - 1]; wsum = wt.sum(); g_on = 1.0 + r_on[k]; g_id = 1.0 + r_id[k]
        # --- net path
        v_open = (h * g_on).sum() + cash * cf[k]
        drift = h * g_on / v_open
        trade = np.abs(wt - drift).sum()              # fraction of v_open
        v_open2 = v_open * (1.0 - trade * cr)
        v_close = v_open2 * ((wt * g_id).sum() + (1.0 - wsum) * cf[k])
        h = wt * g_id * v_open2 / v_close
        cash = (1.0 - wsum) * cf[k] * v_open2 / v_close
        ret[k] = v_close - 1.0; turn[k] = trade * v_open; expo[k] = wsum
        # --- gross path (identical but without the cost deduction)
        v_open_g = (hg * g_on).sum() + cashg * cf[k]
        v_close_g = v_open_g * ((wt * g_id).sum() + (1.0 - wsum) * cf[k])
        hg = wt * g_id * v_open_g / v_close_g
        cashg = (1.0 - wsum) * cf[k] * v_open_g / v_close_g
        ret_g[k] = v_close_g - 1.0
    return dict(
        ret=pd.Series(ret, index=md.calendar, name="ret"),
        ret_gross=pd.Series(ret_g, index=md.calendar, name="ret_gross"),
        turnover=pd.Series(turn, index=md.calendar, name="turnover"),
        exposure=pd.Series(expo, index=md.calendar, name="exposure"),
        weights=W, n_harness_fixes=n_fix,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def sharpe(ret: pd.Series, rf: pd.Series) -> float:
    ex = (ret - rf.reindex(ret.index)).dropna()
    s = ex.std(ddof=1)
    return float(ex.mean() / s * np.sqrt(TRADING_DAYS)) if s > 1e-12 else 0.0


def sortino(ret: pd.Series, rf: pd.Series) -> float:
    ex = (ret - rf.reindex(ret.index)).dropna()
    dd = np.sqrt((np.minimum(ex, 0.0) ** 2).mean())
    return float(ex.mean() / dd * np.sqrt(TRADING_DAYS)) if dd > 1e-12 else 0.0


def max_drawdown(ret: pd.Series) -> float:
    eq = (1.0 + ret).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def cagr(ret: pd.Series) -> float:
    n = len(ret)
    if n == 0:
        return 0.0
    return float((1.0 + ret).prod() ** (TRADING_DAYS / n) - 1.0)


def summarize(res: dict, md: MarketData, start, end, label="") -> dict:
    r = res["ret"].loc[start:end]
    rf = md.rf.loc[start:end]
    ex = r - rf
    bench = md.index_close["NIFTY50"].pct_change().loc[start:end]
    bx = bench - rf
    beta = float(np.cov(ex, bx)[0, 1] / bx.var()) if bx.var() > 0 else 0.0
    alpha_ann = float((ex.mean() - beta * bx.mean()) * TRADING_DAYS)
    yrs = len(r) / TRADING_DAYS
    W = res["weights"].loc[start:end]
    n_names = (W > 0).sum(axis=1)
    mdd = max_drawdown(r)
    return {
        f"sharpe{label}": round(sharpe(r, rf), 4),
        f"cagr{label}": round(cagr(r), 4),
        f"vol{label}": round(float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)), 4),
        f"maxdd{label}": round(mdd, 4),
        f"sortino{label}": round(sortino(r, rf), 4),
        f"calmar{label}": round(cagr(r) / abs(mdd), 4) if mdd < 0 else 0.0,
        f"hit{label}": round(float((ex > 0).mean()), 4),
        f"beta{label}": round(beta, 3),
        f"alpha_ann{label}": round(alpha_ann, 4),
        f"turnover_yr{label}": round(float(res["turnover"].loc[start:end].sum() / yrs), 2),
        f"exposure{label}": round(float(res["exposure"].loc[start:end].mean()), 3),
        f"n_names_avg{label}": round(float(n_names[n_names > 0].mean()) if (n_names > 0).any() else 0.0, 2),
        f"pct_days_invested{label}": round(float((n_names > 0).mean()), 3),
        f"bench_sharpe{label}": round(sharpe(bench, rf), 4),
        f"bench_cagr{label}": round(cagr(bench.dropna()), 4),
        f"tstat{label}": round(float(ex.mean() / ex.std(ddof=1) * np.sqrt(len(ex))), 2),
        f"days{label}": int(len(r)),
    }


def evaluate(weights: pd.DataFrame, md: MarketData, cost_bps=COST_BPS_PER_SIDE) -> dict:
    """Full evaluation: IS (START..IS_END), OOS (OOS_START..END) and FULL. The loop's objective is sharpe_is."""
    res = run_backtest(weights, md, cost_bps=cost_bps)
    out = {}
    out.update(summarize(res, md, START, IS_END, "_is"))
    out.update(summarize(res, md, OOS_START, END, "_oos"))
    out.update(summarize(res, md, START, END, "_full"))
    out["cost_bps"] = cost_bps
    out["n_harness_fixes"] = res["n_harness_fixes"]
    out["_res"] = res
    return out


# ---------------------------------------------------------------------------
# Lookahead detector
# ---------------------------------------------------------------------------

def check_no_lookahead(strategy_fn, md: MarketData, n_trunc=60, n_corrupt=30, seed=0, atol=1e-9) -> dict:
    """
    Dense causality test. (1) Truncation: the strategy is run on data cut at n_trunc dates spread
    over START..END (plus the last in-sample day and the penultimate day); weights up to each cut
    must equal the full-sample weights. (2) Corruption: at n_corrupt further dates every later bar
    is replaced by a random permutation of the later bars (prices, volumes, membership, index, rf);
    weights up to the date must still be identical. A strategy that reads any future bar, or that
    treats 'missing future' as neutral, fails at least one of these. Reports how many check dates
    had non-zero holdings in the full run so an inert check set is visible.
    """
    cal = md.calendar
    full = strategy_fn(md).reindex(index=cal, columns=md.tickers).fillna(0.0)
    i0 = int(cal.searchsorted(pd.Timestamp(START))); i1 = len(cal) - 2
    pos = np.unique(np.concatenate([np.linspace(i0, i1, n_trunc).astype(int),
                                    [int(cal.searchsorted(pd.Timestamp(IS_END))), i1]]))
    rng = np.random.default_rng(seed)
    cpos = np.sort(rng.choice(np.arange(i0, i1), size=min(n_corrupt, i1 - i0), replace=False))
    worst = 0.0; bad = None; informative = 0; n_checks = 0

    def compare(w, d):
        nonlocal worst, bad, informative, n_checks
        sub_cal = cal[cal <= d]
        w = w.reindex(index=sub_cal, columns=md.tickers).fillna(0.0)
        diff = (w - full.loc[sub_cal]).abs()
        m = float(diff.values.max()) if diff.size else 0.0
        n_checks += 1
        if (full.loc[d] > 0).any():
            informative += 1
        if m > worst:
            worst = m
        if m > atol and bad is None:
            bad = str(pd.Timestamp(d).date())

    for i in pos:
        d = cal[i]
        compare(strategy_fn(md.slice(d)), d)
    for i in cpos:
        d = cal[i]
        compare(strategy_fn(md.corrupt_after(d, rng)), d)
    return {"ok": worst <= atol, "max_abs_diff": worst, "first_bad_date": bad, "n_checks": n_checks,
            "n_checks_with_holdings": informative}
