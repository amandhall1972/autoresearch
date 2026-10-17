"""
Fast protocol-v4 experiments for researchers: the data are loaded once (and cached as a pickle in the system temp
directory, keyed by the data files' modification times) and a strategy module is scored with any PARAMS
overrides in about half a second, without the lookahead check. A candidate that looks good must still pass
`python run4.py --strategy FILE` (dense lookahead check) and `python compare4.py FILE` (keep gates).

    import v4lab
    md = v4lab.data()
    base = v4lab.score("strategy.py", md)
    rows = [v4lab.score("strategy.py", md, regime_rsi_band=b) for b in (1.0, 2.0, 3.0)]
    v4lab.show([base] + rows)
"""
import importlib.util, os, pickle, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P

SHOW = ["objective", "sharpe_pre", "sharpe_is", "sharpe_oos", "sharpe_span", "sharpe_full", "sharpe_span_drop10",
        "years_positive", "worst_year_sharpe", "maxdd_span", "turnover_yr_span", "exposure_span", "n_names_avg_span"]


def _stamp():
    newest = 0.0
    for d in ("raw", "raw_presample", "universe"):
        for f in os.listdir(os.path.join(P.DATA_DIR, d)):
            newest = max(newest, os.path.getmtime(os.path.join(P.DATA_DIR, d, f)))
    newest = max(newest, os.path.getmtime(os.path.join(P.DATA_DIR, "rf_repo_rate.csv")),
                 os.path.getmtime(os.path.join(HERE, "prepare.py")))
    return int(newest)


def data():
    """The protocol-v4 MarketData (2016-06..2026-10 on the 2016-2026 universe), cached between processes."""
    path = os.path.join(tempfile.gettempdir(), f"nse_v4_md_{_stamp()}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    md = P.load_data(presample=True, end=P.END)
    with open(path, "wb") as f:
        pickle.dump(md, f)
    return md


def load(path):
    spec = importlib.util.spec_from_file_location(f"lab_{abs(hash(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score(strategy, md=None, label=None, **overrides) -> dict:
    """Score a strategy (path or module) with PARAMS overrides; returns the protocol-v4 metrics (no lookahead check)."""
    mod = load(strategy) if isinstance(strategy, str) else strategy
    md = md if md is not None else data()
    saved = dict(getattr(mod, "PARAMS", {}))
    try:
        if overrides:
            mod.PARAMS.update(overrides)
        ev = P.evaluate_v4(mod.compute_target_weights(md), md)
    finally:
        if hasattr(mod, "PARAMS"):
            mod.PARAMS.clear(); mod.PARAMS.update(saved)
    ev.pop("_res")
    ev["label"] = label or (", ".join(f"{k}={v}" for k, v in overrides.items()) or "base")
    return ev


def show(rows, keys=SHOW):
    w = max(len(r["label"]) for r in rows)
    print(f"{'':<{w}}" + "".join(f"{k[:11]:>12}" for k in keys))
    for r in rows:
        print(f"{r['label']:<{w}}" + "".join(f"{r[k]!s:>12}" for k in keys))
