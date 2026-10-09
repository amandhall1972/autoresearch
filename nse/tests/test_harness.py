import os, sys
import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import prepare as P
import indicators as I


# ----------------------------------------------------------------------------- indicators
def _ref_rsi(close, n):
    d = np.diff(close); up = np.clip(d, 0, None); dn = np.clip(-d, 0, None)
    au = np.full(len(close), np.nan); ad = np.full(len(close), np.nan)
    au[n] = up[:n].mean(); ad[n] = dn[:n].mean()
    for i in range(n + 1, len(close)):
        au[i] = (au[i - 1] * (n - 1) + up[i - 1]) / n
        ad[i] = (ad[i - 1] * (n - 1) + dn[i - 1]) / n
    return 100 - 100 / (1 + au / ad)


def test_rsi_matches_wilder_reference_in_steady_state():
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600)))
    df = pd.DataFrame({"A": c})
    ours = I.rsi(df, 14)["A"].values
    ref = _ref_rsi(c, 14)
    # ewm seeding differs from SMA seeding; after a long warm-up both converge
    assert np.nanmax(np.abs(ours[300:] - ref[300:])) < 0.05
    assert np.nanmin(ours) >= 0 and np.nanmax(ours) <= 100


def test_ema_reference():
    x = pd.DataFrame({"A": np.arange(1.0, 51.0)})
    e = I.ema(x, 10)["A"]
    a = 2 / 11; ref = x["A"].iloc[0]
    for v in x["A"].iloc[1:]:
        ref = a * v + (1 - a) * ref
    assert abs(e.iloc[-1] - ref) < 1e-9


def test_rolling_vwap_equals_sma_of_typical_price_with_constant_volume():
    idx = pd.date_range("2020-01-01", periods=30)
    h = pd.DataFrame({"A": np.linspace(10, 20, 30)}, index=idx); l = h - 1; c = h - 0.5
    v = pd.DataFrame({"A": np.full(30, 1000.0)}, index=idx)
    vw = I.rolling_vwap(h, l, c, v, 5)["A"]
    tp = ((h + l + c) / 3)["A"]
    assert np.allclose(vw.dropna().values, tp.rolling(5).mean().dropna().values)


def test_anchored_vwap_resets_at_anchor():
    idx = pd.date_range("2020-01-01", periods=6)
    h = pd.DataFrame({"A": [10, 20, 30, 40, 50, 60.0]}, index=idx); l = h.copy(); c = h.copy()
    v = pd.DataFrame({"A": [1, 1, 1, 1, 1, 1.0]}, index=idx)
    anchor = pd.DataFrame({"A": [True, False, False, True, False, False]}, index=idx)
    av = I.anchored_vwap(h, l, c, v, anchor)["A"].values
    assert np.allclose(av, [10, 15, 20, 40, 45, 50])


def test_indicators_are_causal():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2020-01-01", periods=300)
    c = pd.DataFrame({"A": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))}, index=idx)
    h = c * 1.01; l = c * 0.99; v = pd.DataFrame({"A": rng.integers(1000, 2000, 300)}, index=idx).astype(float)
    full = {"rsi": I.rsi(c, 14), "ema": I.ema(c, 20), "vwap": I.rolling_vwap(h, l, c, v, 20),
            "avwap": I.anchored_vwap(h, l, c, v, I.month_start_anchor(idx, c.columns)), "atr": I.atr(h, l, c, 14)}
    cut = 200
    part = {"rsi": I.rsi(c.iloc[:cut], 14), "ema": I.ema(c.iloc[:cut], 20),
            "vwap": I.rolling_vwap(h.iloc[:cut], l.iloc[:cut], c.iloc[:cut], v.iloc[:cut], 20),
            "avwap": I.anchored_vwap(h.iloc[:cut], l.iloc[:cut], c.iloc[:cut], v.iloc[:cut], I.month_start_anchor(idx[:cut], c.columns)),
            "atr": I.atr(h.iloc[:cut], l.iloc[:cut], c.iloc[:cut], 14)}
    for k in full:
        a = full[k].iloc[:cut].values; b = part[k].values
        assert np.allclose(np.nan_to_num(a), np.nan_to_num(b), atol=1e-9), k


# ----------------------------------------------------------------------------- synthetic market
def _synthetic_md(T=6, tickers=("A", "B", P.ETF_TICKER), opens=None, closes=None, rf=0.0):
    cal = pd.date_range("2021-01-04", periods=T, freq="B")
    N = len(tickers)
    if closes is None:
        closes = np.full((T, N), 100.0)
    if opens is None:
        opens = closes.copy()
    c = pd.DataFrame(closes, index=cal, columns=tickers, dtype=float)
    o = pd.DataFrame(opens, index=cal, columns=tickers, dtype=float)
    h = pd.DataFrame(np.maximum(o.values, c.values), index=cal, columns=tickers); l = pd.DataFrame(np.minimum(o.values, c.values), index=cal, columns=tickers)
    v = pd.DataFrame(1000.0, index=cal, columns=tickers)
    tradable = pd.DataFrame(True, index=cal, columns=tickers)
    member = pd.DataFrame(True, index=cal, columns=tickers)
    ic = pd.DataFrame({"NIFTY50": 10000.0, "BANKNIFTY": 30000.0}, index=cal)
    rfs = pd.Series(rf, index=cal)
    return P.MarketData(o, h, l, c, v, tradable, member, ic, rfs, cal)


def test_engine_next_open_execution_hand_computed():
    # day0 close 100, day1 open 102 close 104, day2 open 103 close 101
    closes = np.array([[100.0], [104.0], [101.0]]); opens = np.array([[100.0], [102.0], [103.0]])
    md = _synthetic_md(T=3, tickers=("A",), opens=opens, closes=closes)
    w = pd.DataFrame([[1.0], [1.0], [1.0]], index=md.calendar, columns=["A"])  # decided at close of day0
    res = P.run_backtest(w, md, cost_bps=0)
    # day1: overnight leg with zero weight (nothing held yet), buy at open 102, earn 104/102-1
    assert abs(res["ret"].iloc[1] - (104 / 102 - 1)) < 1e-12
    # day2: held from day1 (weight 1): overnight 103/104-1 then intraday 101/103-1 => total 101/104-1
    assert abs(res["ret"].iloc[2] - (101 / 104 - 1)) < 1e-12


def test_engine_cost_is_charged_on_traded_value():
    md = _synthetic_md(T=4, tickers=("A",))
    w = pd.DataFrame([[1.0], [1.0], [0.0], [0.0]], index=md.calendar, columns=["A"])
    res = P.run_backtest(w, md, cost_bps=15)
    assert abs(res["ret"].iloc[1] - (-0.0015)) < 1e-12          # buy 100% at 15 bps
    assert abs(res["ret"].iloc[2]) < 1e-12                       # hold, flat prices, no trade
    assert abs(res["ret"].iloc[3] - (-0.0015)) < 1e-12          # sell 100%
    assert abs(res["turnover"].sum() - 2.0) < 1e-12


def test_cash_earns_risk_free():
    md = _synthetic_md(T=3, tickers=("A",), rf=0.0004)
    w = pd.DataFrame(0.0, index=md.calendar, columns=["A"])
    res = P.run_backtest(w, md, cost_bps=15)
    assert abs(res["ret"].iloc[1] - 0.0004) < 1e-9 or abs(res["ret"].iloc[1] - (1.0002 * 1.0002 - 1)) < 1e-9


def test_long_only_and_gross_cap_enforced():
    md = _synthetic_md(T=3, tickers=("A", "B"))
    with pytest.raises(ValueError):
        P.run_backtest(pd.DataFrame(-0.1, index=md.calendar, columns=["A", "B"]), md)
    with pytest.raises(ValueError):
        P.run_backtest(pd.DataFrame(0.6, index=md.calendar, columns=["A", "B"]), md)


def test_nonmember_weights_are_zeroed_and_delisted_names_liquidated():
    md = _synthetic_md(T=5, tickers=("A", "B"))
    md.member["B"] = False
    md.tradable.loc[md.calendar[3]:, "A"] = False
    md.close.loc[md.calendar[3]:, "A"] = np.nan; md.open.loc[md.calendar[3]:, "A"] = np.nan
    md.close["A"] = md.close["A"].ffill(); md.open["A"] = md.open["A"].ffill()
    w = pd.DataFrame(0.5, index=md.calendar, columns=["A", "B"])
    res = P.run_backtest(w, md, cost_bps=0)
    W = res["weights"]
    assert (W["B"] == 0).all()
    assert (W["A"].iloc[:3] == 0.5).all() and (W["A"].iloc[4:] == 0).all()


def test_lookahead_detector_catches_a_leak():
    md = _synthetic_md(T=40, tickers=("A", "B"), closes=100 + np.cumsum(np.random.default_rng(3).normal(0, 1, (40, 2)), axis=0))

    def leaky(m):
        up = m.close.shift(-1) > m.close
        return up.astype(float) * 0.5

    def clean(m):
        up = m.close > m.close.shift(1)
        return up.astype(float) * 0.5

    assert not P.check_no_lookahead(leaky, md, check_dates=(md.calendar[20], md.calendar[30]))["ok"]
    assert P.check_no_lookahead(clean, md, check_dates=(md.calendar[20], md.calendar[30]))["ok"]


def test_metrics_basic():
    idx = pd.date_range("2020-01-01", periods=504, freq="B")
    r = pd.Series(np.tile([0.002, -0.001], 252), index=idx); rf = pd.Series(0.0, index=idx)
    assert abs(P.sharpe(r, rf) - r.mean() / r.std(ddof=1) * np.sqrt(252)) < 1e-12
    r2 = pd.Series([0.01, -0.02, 0.03, -0.01], index=idx[:4])
    assert abs(P.max_drawdown(r2) - (-0.02)) < 1e-12


# ----------------------------------------------------------------------------- real data
@pytest.fixture(scope="module")
def md():
    return P.load_data()


def test_calendar_and_coverage(md):
    cal = md.calendar
    assert cal[0] == pd.Timestamp("2019-06-03") and cal[-1] == pd.Timestamp(P.END)
    for d in ["2020-02-01", "2024-01-20", "2024-03-02", "2024-05-18", "2023-11-12", "2023-10-24", "2024-11-01"]:
        assert pd.Timestamp(d) not in cal          # special sessions / holiday artifacts excluded
    assert pd.Timestamp("2025-02-01") in cal        # Budget Saturday 2025: a full session with broad data
    assert md.tradable.loc["2020":].values.mean() > 0.95
    c = md.close.loc["2020":]
    for t in c.columns:                              # no gaps after a name's first bar (leading NaN = not yet listed)
        first = c[t].first_valid_index()
        assert first is not None and not c[t].loc[first:].isna().any(), t
    assert md.close.loc["2020":, ["ETERNAL", "JIOFIN"]].isna().any().any()   # listed in 2021 / 2023


def test_universe_point_in_time(md):
    m = md.member
    assert m.loc["2020-03-18", "YESBANK"] and not m.loc["2020-03-19", "YESBANK"]
    assert not m.loc["2020-03-18", "SHREECEM"] and m.loc["2020-03-19", "SHREECEM"]
    assert m.loc["2023-06-30", "HDFC"] and not m.loc["2023-07-03", "HDFC"]
    assert m.loc["2025-09-29", "HEROMOTOCO"] and not m.loc["2025-09-30", "HEROMOTOCO"]
    assert not m.loc["2025-09-29", "INDIGO"] and m.loc["2025-09-30", "INDIGO"]
    assert m.loc["2026-09-29", "WIPRO"] and not m.loc["2026-09-30", "WIPRO"] and m.loc["2026-09-30", "BSE"]
    assert (m.loc["2020":].sum(axis=1) == 51).all()   # 50 constituents + the ETF, every day
    assert m[P.ETF_TICKER].all()


def test_risk_free_schedule(md):
    rf = md.rf
    assert abs(rf.loc["2023-06-01"] * 365 - 0.065) < 1e-9
    assert abs(rf.loc["2021-06-01"] * 365 - 0.04) < 1e-9
    assert rf.notna().all()


def test_buy_and_hold_etf_reproduces_asset_return(md):
    w = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers); w[P.ETF_TICKER] = 1.0
    res = P.run_backtest(w, md, cost_bps=0)
    r = res["ret"].loc[P.START:P.END]; a = md.close[P.ETF_TICKER].pct_change().loc[P.START:P.END]
    assert abs((1 + r).prod() - (1 + a).prod()) < 1e-9
