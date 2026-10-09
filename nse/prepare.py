"""
Fixed harness for the NSE RSI+EMA+VWAP autoresearch loop. DO NOT MODIFY.

Provides: constants, data loading/cleaning, point-in-time Nifty 50 universe, risk-free rate,
the execution/cost engine, performance metrics and a lookahead detector.

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
WARMUP_START = "2019-06-03"     # price history available from here (indicator warm-up)
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
    return df


def available_tickers():
    return sorted(f[:-4].replace("_", "&") for f in os.listdir(os.path.join(DATA_DIR, "raw")) if f.endswith(".csv"))


class MarketData:
    """Wide frames aligned on the master trading calendar (index=dates, columns=tickers)."""

    def __init__(self, open_, high, low, close, volume, tradable, member, index_close, rf, calendar):
        self.open, self.high, self.low, self.close, self.volume = open_, high, low, close, volume
        self.tradable = tradable          # bool: fresh bar exists for (date, ticker)
        self.member = member              # bool: point-in-time Nifty 50 membership (ETF always True)
        self.index_close = index_close    # DataFrame with NIFTY50, BANKNIFTY closes
        self.rf = rf                      # Series: daily risk-free return on the calendar
        self.calendar = calendar
        self.tickers = list(close.columns)

    def slice(self, end_date) -> "MarketData":
        """Return a copy truncated at end_date (inclusive) - used by the lookahead detector."""
        m = self.calendar <= pd.Timestamp(end_date)
        cal = self.calendar[m]
        return MarketData(self.open.loc[cal], self.high.loc[cal], self.low.loc[cal], self.close.loc[cal],
                          self.volume.loc[cal], self.tradable.loc[cal], self.member.loc[cal],
                          self.index_close.loc[cal], self.rf.loc[cal], cal)


def load_rf(calendar: pd.DatetimeIndex) -> pd.Series:
    """Daily risk-free return from the RBI repo-rate schedule (ACT/365 accrual between trading days)."""
    sched = pd.read_csv(os.path.join(DATA_DIR, "rf_repo_rate.csv"), parse_dates=["effective_date"]).sort_values("effective_date")
    rate = pd.Series(sched["repo_rate_pct"].values / 100.0, index=sched["effective_date"])
    rate = rate.reindex(calendar.union(rate.index)).ffill().reindex(calendar)
    days = pd.Series(calendar, index=calendar).diff().dt.days.fillna(1).clip(lower=1)
    return (rate * days / 365.0).rename("rf")


def load_universe(calendar: pd.DatetimeIndex, tickers) -> pd.DataFrame:
    """Point-in-time membership: a stock is a member on date t if it was in the index per the most
    recent membership row on or before t (matrix rows are month-ends and rebalance dates). Index
    changes after the matrix ends are applied from data/universe/nifty50_changes_post_dataset.csv."""
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
    # master calendar: NIFTY50 bars that are not flat, intersected with days where enough stocks traded
    nifty = raws["NIFTY50"]
    nifty = nifty[(nifty["high"] != nifty["low"]) & nifty["close"].notna()]
    closes = pd.concat([raws[t]["close"].rename(t) for t in stocks], axis=1, sort=True)
    enough = closes.notna().sum(axis=1) >= MIN_NAMES_FOR_SESSION
    calendar = nifty.index.intersection(closes.index[enough])
    calendar = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]

    def wide(col):
        return pd.concat([raws[t][col].rename(t) for t in stocks], axis=1, sort=True).reindex(calendar)

    o, h, l, c, v = (wide(x) for x in ["open", "high", "low", "close", "volume"])
    tradable = c.notna() & o.notna() & (v > 0)
    # carry last close forward for valuation on missing days (zero return), keep OHLC NaN
    c_ff = c.ffill()
    o_ff = o.where(o.notna(), c_ff)
    index_close = pd.concat([raws[t]["close"].rename(t) for t in INDEX_TICKERS], axis=1, sort=True).reindex(calendar).ffill()
    rf = load_rf(calendar)
    member = load_universe(calendar, stocks)
    return MarketData(o_ff, h, l, c_ff, v, tradable, member, index_close, rf, calendar)


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------

def run_backtest(weights: pd.DataFrame, md: MarketData, cost_bps=COST_BPS_PER_SIDE, enforce=True) -> dict:
    """
    weights.loc[t, i]: target weight decided with information up to the close of t,
    executed at the OPEN of the next trading day t+1. Long-only, sum(weights) <= MAX_GROSS.

    Daily accounting for day t+1 (close_t -> close_{t+1}):
      overnight leg  : positions decided at t-1 (held through day t) earn open_{t+1}/close_t - 1
      rebalance      : at open_{t+1} move from drifted weights to w_t, paying cost_bps on |trade|
      intraday leg   : w_t earns close_{t+1}/open_{t+1} - 1
      cash           : (1 - sum w) earns the risk-free rate
    Returns dict with 'ret' (net daily returns), 'ret_gross', 'turnover', 'exposure', 'weights_exec'.
    """
    W = weights.reindex(index=md.calendar, columns=md.tickers).fillna(0.0).astype(float)
    if enforce:
        if (W < -1e-12).any().any():
            raise ValueError("long-only: negative weights found")
        W = W.where(md.member, 0.0)                      # non-members cannot be held
        W = W.where(md.tradable, np.nan)                 # cannot trade a name with no fresh bar...
        W = W.ffill().fillna(0.0)                        # ...keep previous target on stale days
        gross = W.sum(axis=1)
        if (gross > MAX_GROSS + 1e-9).any():
            raise ValueError(f"gross exposure exceeds {MAX_GROSS}: max {gross.max():.4f}")
    # a name that disappears for good (delisting/merger) is liquidated at its last fresh close
    last_fresh = md.tradable.apply(lambda s: s[s].index.max() if s.any() else pd.NaT)
    for t in md.tickers:
        lf = last_fresh[t]
        if pd.notna(lf) and lf < md.calendar[-1]:
            W.loc[W.index > lf, t] = 0.0

    c = md.close.values; o = md.open.values
    r_on = np.zeros_like(c); r_id = np.zeros_like(c)
    r_on[1:] = o[1:] / c[:-1] - 1.0                      # close_t -> open_{t+1}
    r_id[:] = c / o - 1.0                                # open_t -> close_t
    r_on = np.nan_to_num(r_on); r_id = np.nan_to_num(r_id)
    w = W.values
    w_prev = np.zeros_like(w); w_prev[1:] = w[:-1]       # decided at t-1, held through day t
    rf = md.rf.values
    T = len(md.calendar)
    ret = np.zeros(T); ret_g = np.zeros(T); turn = np.zeros(T)
    for k in range(1, T):
        wp = w[k - 2] if k >= 2 else np.zeros(w.shape[1])        # held during day k-1 (decided at k-2)
        wt = w[k - 1]                                            # decided at close of k-1, executed at open of k
        cash_p = 1.0 - wp.sum()
        v_open = 1.0 + (wp * r_on[k]).sum() + cash_p * rf[k] * 0.5
        drift = wp * (1.0 + r_on[k]) / v_open
        trade = np.abs(wt - drift).sum() * v_open
        cost = trade * cost_bps / 1e4
        cash_t = 1.0 - wt.sum()
        v_close_g = v_open * (1.0 + (wt * r_id[k]).sum() + cash_t * rf[k] * 0.5)
        v_close = (v_open - cost) * (1.0 + (wt * r_id[k]).sum() + cash_t * rf[k] * 0.5)
        ret[k] = v_close - 1.0; ret_g[k] = v_close_g - 1.0; turn[k] = trade
    out = dict(
        ret=pd.Series(ret, index=md.calendar, name="ret"),
        ret_gross=pd.Series(ret_g, index=md.calendar, name="ret_gross"),
        turnover=pd.Series(turn, index=md.calendar, name="turnover"),
        exposure=pd.Series(w_prev.sum(axis=1), index=md.calendar, name="exposure"),  # weights held during each day
        weights=W,
    )
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def sharpe(ret: pd.Series, rf: pd.Series) -> float:
    ex = (ret - rf.reindex(ret.index)).dropna()
    s = ex.std(ddof=1)
    return float(ex.mean() / s * np.sqrt(TRADING_DAYS)) if s > 0 else 0.0


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
    return {
        f"sharpe{label}": round(sharpe(r, rf), 4),
        f"cagr{label}": round(cagr(r), 4),
        f"vol{label}": round(float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)), 4),
        f"maxdd{label}": round(max_drawdown(r), 4),
        f"sortino{label}": round(float(ex.mean() / ex[ex < 0].std(ddof=1) * np.sqrt(TRADING_DAYS)) if (ex < 0).any() else 0.0, 4),
        f"calmar{label}": round(cagr(r) / abs(max_drawdown(r)), 4) if max_drawdown(r) < 0 else 0.0,
        f"hit{label}": round(float((r > 0).mean()), 4),
        f"beta{label}": round(beta, 3),
        f"alpha_ann{label}": round(alpha_ann, 4),
        f"turnover_yr{label}": round(float(res["turnover"].loc[start:end].sum() / yrs), 2),
        f"exposure{label}": round(float(res["exposure"].loc[start:end].mean()), 3),
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
    out["_res"] = res
    return out


# ---------------------------------------------------------------------------
# Lookahead detector
# ---------------------------------------------------------------------------

def check_no_lookahead(strategy_fn, md: MarketData, check_dates=("2021-06-30", "2023-03-31", "2025-06-30"), atol=1e-9) -> dict:
    """
    Runs the strategy on data truncated at several dates and verifies that the weights up to each
    truncation date are identical to the full-sample run. Any dependence on future bars shows up as
    a mismatch. Returns {'ok': bool, 'max_abs_diff': float, 'first_bad_date': str|None}.
    """
    full = strategy_fn(md).reindex(index=md.calendar, columns=md.tickers).fillna(0.0)
    worst = 0.0; bad = None
    for d in check_dates:
        sub = md.slice(d)
        w = strategy_fn(sub).reindex(index=sub.calendar, columns=md.tickers).fillna(0.0)
        diff = (w - full.loc[sub.calendar]).abs()
        m = float(diff.values.max()) if diff.size else 0.0
        if m > worst:
            worst = m
        if m > atol and bad is None:
            bad = str(diff.max(axis=1).idxmax().date())
    return {"ok": worst <= atol, "max_abs_diff": worst, "first_bad_date": bad}
