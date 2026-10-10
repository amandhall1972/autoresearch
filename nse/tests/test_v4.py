"""Tests for protocol v4: the windowed objective, evaluate_v4, the keep gates, v4lab, run4's file guard, the
extended repo-rate schedule and the deep hold-out loader (skipped while the deep data are sealed)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NSE = os.path.dirname(HERE)
sys.path.insert(0, NSE)
import prepare as P  # noqa: E402

HAVE_DEEP = os.path.isdir(os.path.join(P.DATA_DIR, "raw_deep")) and os.path.exists(
    os.path.join(P.DATA_DIR, "universe", "nifty50_weights_2008_2016.csv"))
needs_deep = pytest.mark.skipif(not HAVE_DEEP, reason="deep hold-out data sealed (not in data/raw_deep)")


@pytest.fixture(scope="module")
def md():
    return P.load_data(presample=True, end=P.END)


def _hold_etf(md):
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[P.ETF_TICKER] = 1.0
    return W


# ----------------------------------------------------------------------------- the objective
def test_v4_objective_is_the_worst_window():
    idx = pd.bdate_range("2017-04-03", "2026-10-09")
    rng = np.random.default_rng(0)
    ret = pd.Series(rng.normal(0.0004, 0.01, len(idx)), index=idx)
    ret.loc["2024":] -= 0.002                                   # make 2024-26 the worst window
    rf = pd.Series(0.0001, index=idx)
    vals = [P.sharpe(ret.loc[a:b], rf.loc[a:b]) for _, a, b in P.V4_WINDOWS]
    assert P.v4_objective(ret, rf) == pytest.approx(min(vals))
    assert P.v4_objective(ret, rf) == pytest.approx(vals[2])
    # leave-one-year-out drops the year from every window it touches
    r25 = ret[ret.index.year != 2025]
    assert P.v4_objective(ret, rf, skip_year=2025) == pytest.approx(
        min(P.sharpe(r25.loc[a:b], rf.loc[r25.loc[a:b].index]) for _, a, b in P.V4_WINDOWS))


def test_v4_windows_cover_the_span_without_gaps():
    (n0, a0, b0), (n1, a1, b1), (n2, a2, b2) = P.V4_WINDOWS
    assert (n0, n1, n2) == ("pre", "is", "oos")
    assert a0 == P.V4_START == P.PRESAMPLE_START and b2 == P.END
    assert pd.Timestamp(b0) + pd.Timedelta(days=1) == pd.Timestamp(a1)
    assert pd.Timestamp(b1) + pd.Timedelta(days=1) == pd.Timestamp(a2)


def test_evaluate_v4_matches_its_parts(md):
    ev = P.evaluate_v4(_hold_etf(md), md)
    res = ev.pop("_res")
    assert ev["objective"] == pytest.approx(min(ev["sharpe_pre"], ev["sharpe_is"], ev["sharpe_oos"]), abs=1e-4)
    r = res["ret"].loc[P.V4_START:P.END]; rf = md.rf.loc[P.V4_START:P.END]
    assert ev["sharpe_span"] == pytest.approx(P.sharpe(r, rf), abs=1e-4)
    assert ev["years"] == 10 and 0 <= ev["years_positive"] <= 10
    assert ev["worst_year_sharpe"] == pytest.approx(min(ev[f"sharpe_{y}"] for y in range(2017, 2027)), abs=1e-4)
    assert ev["sharpe_span_drop10"] < ev["sharpe_span"]          # removing the best days can only lower it
    assert ev["exposure_span"] == pytest.approx(1.0, abs=1e-3)


def test_v4_data_universe_spans_2017_to_2026(md):
    pre = md.member.loc["2018-06-01"]; post = md.member.loc["2025-06-02"]
    assert 49 <= pre.drop(P.ETF_TICKER).sum() <= 51
    assert 49 <= post.drop(P.ETF_TICKER).sum() <= 51
    assert pre["HINDPETRO"] and not post["HINDPETRO"]           # left the index before 2020 (pre-sample-only data)


# ----------------------------------------------------------------------------- keep gates
def _row(**kw):
    base = dict(pct_days_invested_span=0.6, n_names_avg_span=10, turnover_yr_span=10, maxdd_span=-0.1,
                n_harness_fixes=0, objective=0.3, sharpe_pre=0.4, sharpe_is=0.8, sharpe_oos=0.3,
                sharpe_span_drop10=0.3, years_positive=7, n_params=6)
    base.update(kw)
    return base


def test_compare4_gates_accept_a_clean_improvement():
    import compare4
    c, i = _row(objective=0.5, sharpe_oos=0.5, sharpe_span_drop10=0.4), _row()
    g = compare4.gates(c, i, [(2020, 0.5, 0.3), (2021, 0.45, 0.3)])
    assert g["all_mechanical_gates"]


@pytest.mark.parametrize("change,gate", [
    (dict(sharpe_is=0.6), "windows"),                 # a window falls more than 0.10
    (dict(sharpe_span_drop10=0.2), "drop10"),
    (dict(years_positive=6), "consistency"),
    (dict(n_params=12), "parsimony"),                 # +6 parameters for +0.2 of objective
    (dict(turnover_yr_span=45), "sanity"),
])
def test_compare4_gates_reject(change, gate):
    import compare4
    c = _row(**{**dict(objective=0.5, sharpe_oos=0.5, sharpe_span_drop10=0.4), **change})
    g = compare4.gates(c, _row(), [(2020, 0.5, 0.3)])
    assert not g[gate] and not g["all_mechanical_gates"]


def test_compare4_years_gate_needs_every_year():
    import compare4
    c = _row(objective=0.5, sharpe_oos=0.5, sharpe_span_drop10=0.4)
    g = compare4.gates(c, _row(), [(2020, 0.5, 0.3), (2024, 0.25, 0.3)])
    assert not g["years"]


# ----------------------------------------------------------------------------- tools
def test_v4lab_score_restores_params(md, tmp_path):
    import v4lab
    src = ("import pandas as pd\nfrom prepare import ETF_TICKER\nPARAMS = dict(w=1.0)\n"
           "def compute_target_weights(md):\n    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)\n"
           "    W[ETF_TICKER] = PARAMS['w']\n    return W\n")
    f = tmp_path / "s.py"; f.write_text(src)
    mod = v4lab.load(str(f))
    full = v4lab.score(mod, md)
    half = v4lab.score(mod, md, w=0.5)
    assert mod.PARAMS == {"w": 1.0}
    assert half["exposure_span"] == pytest.approx(0.5, abs=1e-3) and full["exposure_span"] == pytest.approx(1.0, abs=1e-3)
    assert half["label"] == "w=0.5"


def test_run4_refuses_file_access(tmp_path):
    import run4
    f = tmp_path / "bad.py"
    f.write_text("PARAMS = {}\nX = open('x')\ndef compute_target_weights(md):\n    return None\n")
    with pytest.raises(SystemExit):
        run4.load_strategy(str(f))


def test_repo_rate_covers_the_deep_window():
    cal = pd.bdate_range("2007-01-01", "2016-05-31")
    rf = P.load_rf(cal)
    assert not rf.isna().any()
    daily = lambda d: rf.loc[d] * 365.0                     # noqa: E731  (rate x accrual days / 365, 1 day apart)
    assert daily(pd.Timestamp("2012-06-05")) == pytest.approx(0.08)       # 8.00 % from 2012-04-17
    assert daily(pd.Timestamp("2009-06-02")) == pytest.approx(0.0475)     # 4.75 % from 2009-04-21
    assert daily(pd.Timestamp("2015-10-06")) == pytest.approx(0.0675)     # 6.75 % from 2015-09-29


def test_research_loaders_never_read_the_deep_data():
    assert pd.Timestamp(P.DEEP_END) < pd.Timestamp(P.WARMUP_START)
    names = set(P.available_tickers()) | set(P.available_tickers(presample=True))
    assert not names & {"RCOM", "UNITECH", "JPASSOCIAT", "SUZLON", "RPOWER"}   # deep-only members


# ----------------------------------------------------------------------------- deep hold-out data
@pytest.fixture(scope="module")
def deep():
    return P.load_data(deep=True)


@needs_deep
def test_deep_calendar_and_universe(deep):
    cal = deep.calendar
    assert cal[0] == pd.Timestamp("2007-09-17") and cal[-1] == pd.Timestamp("2016-05-31")
    for d in P.DEEP_MUHURAT_SESSIONS + P.DEEP_CLOSED_SESSIONS:
        assert pd.Timestamp(d) not in cal
    win = cal[cal >= pd.Timestamp(P.DEEP_START)]
    n = deep.member.loc[win].drop(columns=P.ETF_TICKER).sum(axis=1)
    assert n.min() >= 46 and n.max() <= 50
    assert not deep.member.loc["2014-01-01":, "HDFC"].any()          # vendor gap: leaves the universe
    assert deep.member.loc["2012-01-02", "HDFC"]


@needs_deep
def test_deep_corrections(deep):
    raw = P._read_raw("BAJAJAUTO", deep=True)
    assert raw.index.min() >= pd.Timestamp("2008-05-27")
    g = deep.close["GRASIM"].pct_change()["2010-05-26"]
    m = deep.index_close["NIFTY50"].pct_change()["2010-05-26"]
    assert abs(g - m) < 0.005                                         # ex-day move set to the market's
    bn = deep.index_close["BANKNIFTY"].loc["2015-11-01":"2015-12-31"]
    assert (bn.diff().iloc[1:] != 0).mean() > 0.95                    # rebuilt, not carried flat


@needs_deep
def test_deep_index_rebuild_is_close_to_vendor(deep):
    import deep_holdout
    rep = deep_holdout.data_report(deep)
    assert rep["banknifty_proxy_rms_bps"] < 30 and rep["banknifty_proxy_corr"] > 0.99
    assert rep["sessions_scored"] > 1200


def test_compliance_counts_sessions_moved_by_each_family(md, tmp_path):
    import compare4, v4lab
    src = ("import pandas as pd\nfrom indicators import ema, rsi\nfrom prepare import ETF_TICKER\n"
           "PARAMS = dict(use_rsi=True, use_ema=True, use_vwap=True, n=20)\n"
           "def compute_target_weights(md):\n    idx = md.index_close[['NIFTY50']]\n"
           "    on = (ema(idx, 20) > ema(idx, 50))['NIFTY50'] if PARAMS['use_ema'] else idx['NIFTY50'] > 0\n"
           "    if PARAMS['use_rsi']:\n        on = on & (rsi(idx, 14)['NIFTY50'] < 70)\n"
           "    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)\n    W[ETF_TICKER] = on.astype(float)\n"
           "    return W\n")
    f = tmp_path / "c.py"; f.write_text(src)
    mod = v4lab.load(str(f))
    comp = compare4.compliance(mod, md)
    assert comp["use_ema"] > 0.05 and comp["use_rsi"] > 0.05
    assert comp["use_vwap"] == 0.0 and not comp["compliant"]          # the switch exists but binds nowhere
    assert compare4.n_params(mod) == 1                                  # switches are not parameters
    assert mod.PARAMS["use_rsi"] is True                                # restored after the ablation


def test_plateau_neighbours():
    import plateau4
    assert plateau4.neighbours(20) == [16, 24]
    assert plateau4.neighbours(3) == [2, 4]                    # integers move at least one step
    assert plateau4.neighbours(1) == [1, 2]
    assert plateau4.neighbours(0.5) == [0.4, 0.6]
    assert plateau4.neighbours(0.985) == [0.988, 0.982]        # a stop near 1 moves through its distance from 1
    assert plateau4.neighbours(True) == [] and plateau4.neighbours("BANKNIFTY") == []


def test_compare4_sanity_accepts_an_etf_core_book():
    import compare4
    etf = _row(n_names_avg_span=1.0, etf_share_span=1.0)
    assert compare4.gates(etf, _row(), [(2020, 0.4, 0.3)])["sanity"]
    thin = _row(n_names_avg_span=3.0, etf_share_span=0.2)
    assert not compare4.gates(thin, _row(), [(2020, 0.4, 0.3)])["sanity"]


def test_evaluate_v4_reports_the_etf_share(md):
    ev = P.evaluate_v4(_hold_etf(md), md); ev.pop("_res")
    assert ev["etf_share_span"] == pytest.approx(1.0)


def test_live_signal_loader_gives_the_research_weights(md):
    """signals.py trades from load_data() (the live universe); the v2 strategy must hold exactly the weights the
    protocol-v4 research data give it over 2024-2026, and nothing outside the live universe."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("final_v4", os.path.join(NSE, "strategy.py"))
    S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
    live = P.load_data(end=P.END)
    Wl, Wr = S.compute_target_weights(live), S.compute_target_weights(md)
    idx = live.calendar[live.calendar >= pd.Timestamp(P.OOS_START)]
    cols = [c for c in Wl.columns if c in Wr.columns]
    assert np.allclose(Wl.loc[idx, cols].values, Wr.reindex(index=idx, columns=cols).fillna(0.0).values)
    assert Wr.reindex(index=idx).drop(columns=cols).abs().to_numpy().sum() == 0.0
    assert (Wr.loc[idx].sum(axis=1) > 0).any()                 # the check is not vacuous: the book trades
