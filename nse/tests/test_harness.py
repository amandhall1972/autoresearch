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
    iohlc = {k: ic.copy() for k in ["open", "high", "low", "close"]}
    rfs = pd.Series(rf, index=cal)
    return P.MarketData(o, h, l, c, v, tradable, member, ic, iohlc, rfs, cal)


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
    assert abs(res["ret"].iloc[1] - 0.0004) < 1e-12
    assert abs(P.sharpe(res["ret"].iloc[1:], md.rf.iloc[1:])) < 1e-9   # all-cash: exactly zero excess return


def test_long_only_and_gross_cap_enforced():
    md = _synthetic_md(T=3, tickers=("A", "B"))
    with pytest.raises(ValueError):
        P.run_backtest(pd.DataFrame(-0.1, index=md.calendar, columns=["A", "B"]), md)
    with pytest.raises(ValueError):
        P.run_backtest(pd.DataFrame(0.6, index=md.calendar, columns=["A", "B"]), md)


def test_stale_held_name_is_carried_and_fresh_names_scaled_not_raised():
    md = _synthetic_md(T=5, tickers=("A", "B"))
    md.tradable.loc[md.calendar[2], "A"] = False
    # the strategy drops the stale name and re-spreads: (0.5,0.5) -> (0,1.0) on day 2 -> would exceed 1 with the carry
    w = pd.DataFrame(0.5, index=md.calendar, columns=["A", "B"])
    w.loc[md.calendar[2], "A"] = 0.0; w.loc[md.calendar[2], "B"] = 1.0
    res = P.run_backtest(w, md, cost_bps=0)
    W = res["weights"]
    assert abs(W.loc[md.calendar[2], "A"] - 0.5) < 1e-12 and abs(W.loc[md.calendar[2], "B"] - 0.5) < 1e-12
    assert res["n_harness_fixes"] == 1
    assert (W.sum(axis=1) <= P.MAX_GROSS + 1e-12).all()


def test_strategy_gross_violation_still_raises():
    md = _synthetic_md(T=3, tickers=("A", "B"))
    with pytest.raises(ValueError):
        P.run_backtest(pd.DataFrame(0.6, index=md.calendar, columns=["A", "B"]), md)


def test_engine_carries_drifted_state_overnight():
    # two assets, equal target every day; A rallies intraday on day1 so holdings drift to 0.55/0.45;
    # day2 flat -> the rebalance back to 0.5/0.5 at the open trades 0.10 and (at 0 bps) returns exactly 0
    closes = np.array([[100.0, 100.0], [110.0, 90.0], [110.0, 90.0]])
    opens = np.array([[100.0, 100.0], [100.0, 100.0], [110.0, 90.0]])
    md = _synthetic_md(T=3, tickers=("A", "B"), opens=opens, closes=closes)
    w = pd.DataFrame(0.5, index=md.calendar, columns=["A", "B"])
    res = P.run_backtest(w, md, cost_bps=0)
    assert abs(res["ret"].iloc[1]) < 1e-12 and abs(res["ret"].iloc[2]) < 1e-12
    assert abs(res["turnover"].iloc[2] - 0.10) < 1e-12
    res15 = P.run_backtest(w, md, cost_bps=15)
    assert abs(res15["ret"].iloc[2] - (-0.10 * 0.0015)) < 1e-12
    # overnight gap after the drift: A +10% overnight on day2 with drifted weight 0.55 -> +5.5% not +5%
    opens2 = np.array([[100.0, 100.0], [100.0, 100.0], [121.0, 90.0]]); closes2 = np.array([[100.0, 100.0], [110.0, 90.0], [121.0, 90.0]])
    md2 = _synthetic_md(T=3, tickers=("A", "B"), opens=opens2, closes=closes2)
    res2 = P.run_backtest(w, md2, cost_bps=0)
    assert abs(res2["ret"].iloc[2] - 0.055) < 1e-12


def test_nonmember_weights_are_zeroed_and_delisted_names_liquidated():
    md = _synthetic_md(T=5, tickers=("A", "B"))
    md.member["B"] = False
    md.tradable.loc[md.calendar[3]:, "A"] = False
    w = pd.DataFrame(0.5, index=md.calendar, columns=["A", "B"])
    res = P.run_backtest(w, md, cost_bps=0)
    W = res["weights"]
    assert (W["B"] == 0).all()
    assert (W["A"].iloc[:3] == 0.5).all() and (W["A"].iloc[3:] == 0).all()   # liquidated at its last fresh close


@pytest.fixture(scope="module")
def md():
    return P.load_data()


def test_lookahead_detector_catches_leaks_on_real_data(md):
    from indicators import ema

    def clean(m):
        up = (m.close > ema(m.close, 20)) & m.member & m.tradable
        w = up.astype(float); return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    def leaky_exit(m):          # the review's example: exit before a >3% drop tomorrow, neutral at the boundary
        up = (m.close > ema(m.close, 20)) & m.member & m.tradable
        up &= ~((m.close.shift(-1) / m.close - 1) < -0.03)
        w = up.astype(float); return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    def leaky_rank(m):          # rank candidates by tomorrow's return (fillna neutral)
        up = (m.close > ema(m.close, 20)) & m.member & m.tradable
        sc = (m.close.shift(-1) / m.close).fillna(1.0).where(up)
        top = sc.rank(axis=1, ascending=False) <= 10
        w = top.astype(float); return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    assert P.check_no_lookahead(clean, md, n_trunc=8, n_corrupt=4)["ok"]
    assert not P.check_no_lookahead(leaky_exit, md, n_trunc=8, n_corrupt=4)["ok"]
    assert not P.check_no_lookahead(leaky_rank, md, n_trunc=8, n_corrupt=4)["ok"]


def test_metrics_basic():
    idx = pd.date_range("2020-01-01", periods=504, freq="B")
    r = pd.Series(np.tile([0.002, -0.001], 252), index=idx); rf = pd.Series(0.0, index=idx)
    assert abs(P.sharpe(r, rf) - r.mean() / r.std(ddof=1) * np.sqrt(252)) < 1e-12
    r2 = pd.Series([0.01, -0.02, 0.03, -0.01], index=idx[:4])
    assert abs(P.max_drawdown(r2) - (-0.02)) < 1e-12


# ----------------------------------------------------------------------------- real data
def test_calendar_and_coverage(md):
    cal = md.calendar
    assert cal[0] == pd.Timestamp("2016-06-01") and cal[-1] == pd.Timestamp(P.END)
    assert int(cal.searchsorted(pd.Timestamp(P.START))) >= 850    # indicator warm-up before the backtest starts
    for d in ["2024-03-02", "2024-05-18", "2023-11-12", "2023-10-24", "2024-11-01", "2025-10-21"]:
        assert pd.Timestamp(d) not in cal          # short DR sessions / holiday artifacts / Muhurat excluded
    for d in ["2020-02-01", "2024-01-20", "2019-03-29", "2019-01-01"]:
        assert pd.Timestamp(d) in cal              # full sessions the vendor's index series lacks are kept
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
    assert m.loc["2023-07-12", "HDFC"] and not m.loc["2023-07-13", "HDFC"] and m.loc["2023-07-13", "LTIM"]
    assert not m.loc["2024-09-27", "BEL"] and m.loc["2024-09-30", "BEL"] and not m.loc["2024-09-30", "DIVISLAB"]
    assert m.loc["2025-03-27", "BPCL"] and not m.loc["2025-03-28", "BPCL"] and m.loc["2025-03-28", "ETERNAL"]
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


def test_membership_change_rows_fall_on_trading_days(md):
    w = pd.read_csv(os.path.join(os.path.dirname(HERE), "data", "universe", "nifty50_weights_2019_2025.csv"), parse_dates=["DATE"]).set_index("DATE")
    mem = (w > 0)
    changed = mem.ne(mem.shift(1)).any(axis=1)
    for d in mem.index[1:][changed.values[1:]]:
        assert d in md.calendar, d


def test_etf_open_tracks_index_open(md):
    e = P.ETF_TICKER
    g_etf = md.open[e] / md.close[e].shift(1) - 1
    g_idx = md.index_ohlc["open"]["NIFTY50"] / md.index_close["NIFTY50"].shift(1) - 1
    d = (g_etf - g_idx).loc["2020":][md.tradable[e].loc["2020":]]
    assert d.abs().max() < 0.01
    ok = md.close.loc["2020":].notna()
    assert ((md.high.loc["2020":] >= md.close.loc["2020":] - 1e-9) | ~ok).all().all()
    assert ((md.low.loc["2020":] <= md.close.loc["2020":] + 1e-9) | ~ok).all().all()


def test_demerger_ex_date_gaps_are_market_gaps(md):
    for t, d in [("RELIANCE", "2023-07-20"), ("ITC", "2025-01-06"), ("TATAMOTORS", "2025-10-14")]:
        i = md.calendar.get_loc(pd.Timestamp(d))
        gap = md.open[t].iloc[i] / md.close[t].iloc[i - 1] - 1
        assert abs(gap) < 0.003, (t, gap)


def test_stale_bars_are_carried_with_zero_volume(md):
    assert md.volume.loc["2025-03-18", "RELIANCE"] == 0 and not md.tradable.loc["2025-03-18", "RELIANCE"]
    assert md.open.loc["2025-03-18", "RELIANCE"] == md.close.loc["2025-03-17", "RELIANCE"]


# ----------------------------------------------------------------------------- robustness gates / holdout hygiene
def test_robustness_is_halves_drop5_and_gross_use_only_in_sample(md):
    cal = md.calendar
    rng = np.random.default_rng(3)
    ret = pd.Series(rng.normal(0.0008, 0.01, len(cal)), index=cal)
    gross = ret + 0.0002
    # poison everything after IS_END: the statistics must not move
    ret2 = ret.copy(); ret2.loc[P.OOS_START:] = 9.9
    rf = md.rf
    for r in (ret, ret2):
        res = {"ret": r, "ret_gross": gross if r is ret else gross.where(gross.index <= pd.Timestamp(P.IS_END), -5.0)}
        out = P.robustness_is(res, md)
        h1 = P.sharpe(ret.loc[P.START:P.IS_H1_END], rf.loc[P.START:P.IS_H1_END])
        h2 = P.sharpe(ret.loc[P.IS_H2_START:P.IS_END], rf.loc[P.IS_H2_START:P.IS_END])
        assert abs(out["sharpe_is_h1"] - round(h1, 4)) < 1e-9
        assert abs(out["sharpe_is_h2"] - round(h2, 4)) < 1e-9
        ex = (ret - rf).loc[P.START:P.IS_END]
        kept = ex.sort_values().iloc[:-P.DROP_BEST_DAYS]
        ref = kept.mean() / kept.std(ddof=1) * np.sqrt(P.TRADING_DAYS)
        assert abs(out["sharpe_is_drop5"] - round(ref, 4)) < 1e-9
        assert out["sharpe_is_drop5"] < P.sharpe(ret.loc[P.START:P.IS_END], rf.loc[P.START:P.IS_END])
        assert abs(out["sharpe_is_gross"] - round(P.sharpe(gross.loc[P.START:P.IS_END], rf.loc[P.START:P.IS_END]), 4)) < 1e-9


def test_run_py_hides_holdout_by_default():
    import run
    hidden = run.visible_keys(False)
    assert "sharpe_is" in hidden and "sharpe_is_drop5" in hidden
    assert not any(k.endswith("_oos") or k.endswith("_full") for k in hidden)
    shown = run.visible_keys(True)
    assert "sharpe_oos" in shown and "sharpe_full" in shown


# ----------------------------------------------------------------------------- calendar repair / index rebuild
def test_missing_index_sessions_use_published_closes_and_consistent_bars(md):
    ic = md.index_ohlc
    for d, official in P.INDEX_CLOSE_PATCHES:
        t = pd.Timestamp(d)
        if t not in md.calendar:
            continue
        o, h, l, c = (float(ic[k].loc[t, "NIFTY50"]) for k in ["open", "high", "low", "close"])
        assert abs(c - official) < 1e-6
        assert h >= max(o, c) - 1e-9 and l <= min(o, c) + 1e-9
        assert abs(o / float(md.index_close["NIFTY50"].shift(1).loc[t]) - 1) < 0.05
    assert not md.index_close.isna().loc["2016-07-01":].any().any()


def test_index_rebuild_matches_vendor_index_on_normal_days():
    """Drop real vendor index bars, rebuild them from constituents and compare (the method used for gaps)."""
    md = P.load_data()
    cal = md.calendar
    days = cal[(cal >= pd.Timestamp("2021-03-01")) & (cal <= pd.Timestamp("2021-03-31"))]
    raw = {k: md.index_ohlc[k].copy() for k in md.index_ohlc}
    holed = {k: v.copy() for k, v in raw.items()}
    for k in holed:
        holed[k].loc[days, "NIFTY50"] = np.nan
    rebuilt = P._fill_index_bars(holed, cal)["close"]["NIFTY50"]
    # chained rebuild over a whole month: daily returns must track the real index closely
    err = (rebuilt.loc[days].pct_change() - raw["close"]["NIFTY50"].loc[days].pct_change()).dropna()
    assert float(np.sqrt((err ** 2).mean())) < 0.0010


# ----------------------------------------------------------------------------- sealed pre-sample hold-out data
@pytest.fixture(scope="module")
def md_pre():
    return P.load_data(presample=True)


def test_presample_universe_is_point_in_time_and_complete(md_pre):
    m = md_pre.member.drop(columns=[P.ETF_TICKER]).loc[P.PRESAMPLE_START:P.PRESAMPLE_END]
    assert (m.sum(axis=1) == 50).all()
    c = md_pre.close.loc[m.index, m.columns]
    assert not (m & c.isna()).values.any()                      # every member has a price
    assert md_pre.calendar[-1] <= pd.Timestamp(P.PRESAMPLE_END)
    checks = [("2017-03-30", {"IBULHSGFIN": False, "BHEL": True}), ("2017-03-31", {"IBULHSGFIN": True, "BHEL": False}),
              ("2017-05-25", {"VEDL": False, "GRASIM": True}), ("2017-05-26", {"VEDL": True, "GRASIM": False}),
              ("2017-09-28", {"BAJFINANCE": False, "ACC": True}), ("2017-09-29", {"BAJFINANCE": True, "ACC": False}),
              ("2018-03-28", {"TITAN": False, "BOSCHLTD": True}), ("2018-04-02", {"TITAN": True, "BOSCHLTD": False}),
              ("2018-09-27", {"JSWSTEEL": False, "LUPIN": True}), ("2018-09-28", {"JSWSTEEL": True, "LUPIN": False}),
              ("2019-03-28", {"BRITANNIA": False, "HINDPETRO": True}), ("2019-03-29", {"BRITANNIA": True, "HINDPETRO": False}),
              ("2019-09-26", {"NESTLEIND": False, "IBULHSGFIN": True}), ("2019-09-27", {"NESTLEIND": True, "IBULHSGFIN": False})]
    for d, exp in checks:
        assert {k: bool(md_pre.member.loc[d, k]) for k in exp} == exp, d


def test_research_data_never_contains_presample_names_or_members(md):
    pre_only = set(P.available_tickers(True)) - set(P.available_tickers(False))
    assert pre_only and not (pre_only & set(md.tickers))
    assert not md.member.drop(columns=[P.ETF_TICKER]).loc[:"2019-12-30"].values.any()


def test_holdout_score_reports_presample_window(md_pre):
    import holdout

    class BuyHoldETF:
        @staticmethod
        def compute_target_weights(m):
            W = pd.DataFrame(0.0, index=m.calendar, columns=m.tickers); W[P.ETF_TICKER] = 1.0
            return W

    ev = holdout.score(BuyHoldETF, md_pre, check=False)
    assert ev["days_pre"] == len(md_pre.calendar[md_pre.calendar >= pd.Timestamp(P.PRESAMPLE_START)])
    assert {"sharpe_pre", "sharpe_2017", "sharpe_2018", "sharpe_2019", "turnover_yr_pre"} <= set(ev)
    assert ev["exposure_pre"] > 0.99 and ev["turnover_yr_pre"] < 1.0
