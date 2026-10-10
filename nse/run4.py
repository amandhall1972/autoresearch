"""
Protocol v4 experiment (program.md): load the 2016-2026 point-in-time data, build target weights from a strategy
file, verify no lookahead over 2017-2026, evaluate every window and print the summary block.

Every window the earlier protocols kept apart (2017-19 pre-sample, 2020-23 selection, 2024-26 validation) has
been seen, so protocol v4 researches on all of them at once: the objective is the LOWEST of the three window
Sharpe ratios. The only data never shown to the loop is the deep hold-out (2011-2016), which this script cannot
load (see deep_holdout.py).

Usage: python run4.py [--strategy strategy.py] [--cost 15] [--no-lookahead-check] [--out runs/v4_latest]
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P

KEYS = (["objective", "sharpe_pre", "sharpe_is", "sharpe_oos", "sharpe_span", "sharpe_full", "sharpe_span_drop10",
         "sharpe_span_gross", "years_positive", "years", "worst_year_sharpe"]
        + [f"sharpe_{y}" for y in range(2017, 2027)]
        + ["cagr_pre", "cagr_is", "cagr_oos", "cagr_span", "cagr_full", "maxdd_pre", "maxdd_is", "maxdd_oos", "maxdd_span",
           "vol_span", "turnover_yr_span", "exposure_span", "n_names_avg_span", "pct_days_invested_span", "beta_span",
           "bench_sharpe_pre", "bench_sharpe_is", "bench_sharpe_oos", "bench_sharpe_span", "bench_sharpe_full",
           "cost_bps", "n_harness_fixes", "lookahead_ok", "lookahead_max_diff", "lookahead_checks", "runtime_s"])
BANNED = ["open(", "read_csv", "read_parquet", "read_pickle", "load_data", "os.", "glob", "pathlib", "subprocess",
          "pickle", "np.load", "__import__", "importlib", "urllib", "requests", "socket"]


def load_strategy(path):
    import importlib.util
    src = open(path).read()
    hits = [b for b in BANNED if b in src]
    if hits:
        raise SystemExit(f"{path} must not access files/network/the loader; found: {hits}")
    spec = importlib.util.spec_from_file_location("v4_strategy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(path, cost=P.COST_BPS_PER_SIDE, lookahead=True, md=None) -> dict:
    t0 = time.time()
    mod = load_strategy(path)
    md = md if md is not None else P.load_data(presample=True, end=P.END)
    W = mod.compute_target_weights(md)
    la = {"ok": True, "max_abs_diff": 0.0, "n_checks": 0, "n_checks_with_holdings": 0, "first_bad_date": None}
    if lookahead:
        la = P.check_no_lookahead(mod.compute_target_weights, md, lo=P.V4_START, hi=P.OOS_START)
    ev = P.evaluate_v4(W, md, cost_bps=cost)
    ev["lookahead_ok"] = bool(la["ok"]); ev["lookahead_max_diff"] = float(la["max_abs_diff"])
    ev["lookahead_checks"] = f'{la["n_checks_with_holdings"]}/{la["n_checks"]} informative'
    ev["lookahead_first_bad"] = la["first_bad_date"]
    ev["runtime_s"] = round(time.time() - t0, 1)
    ev["params"] = getattr(mod, "PARAMS", {})
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=os.path.join(HERE, "strategy.py"))
    ap.add_argument("--cost", type=float, default=P.COST_BPS_PER_SIDE)
    ap.add_argument("--out", default=os.path.join(HERE, "runs", "v4_latest"))
    ap.add_argument("--no-lookahead-check", action="store_true")
    args = ap.parse_args()
    ev = run(args.strategy, args.cost, not args.no_lookahead_check)
    res = ev.pop("_res")
    os.makedirs(args.out, exist_ok=True)
    res["ret"].loc[P.V4_START:P.END].to_csv(os.path.join(args.out, "daily_returns.csv"), header=True)
    res["weights"].loc[P.V4_START:P.END].to_csv(os.path.join(args.out, "weights.csv"))
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump({k: ev.get(k) for k in KEYS + ["params"]}, f, indent=2, default=str)
    print("---")
    for k in KEYS:
        print(f"{k + ':':<24}{ev.get(k)}")
    if not ev["lookahead_ok"]:
        print(f"LOOKAHEAD DETECTED (first bad date {ev['lookahead_first_bad']}) - result is invalid", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
