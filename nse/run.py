"""
Run one experiment: load data, build target weights from strategy.py, verify no lookahead,
evaluate IS/OOS/FULL and print a summary block. Usage: python run.py [--cost 15]
"""
import argparse, json, os, sys, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=P.COST_BPS_PER_SIDE)
    ap.add_argument("--out", default=os.path.join(HERE, "runs", "latest"))
    ap.add_argument("--no-lookahead-check", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    src = open(os.path.join(HERE, "strategy.py")).read()
    banned = ["open(", "read_csv", "read_parquet", "read_pickle", "load_data", "os.", "glob", "pathlib", "subprocess",
              "pickle", "np.load", "__import__", "importlib", "urllib", "requests", "socket"]
    hits = [b for b in banned if b in src]
    if hits:
        print(f"strategy.py must not access files/network/the loader; found: {hits}", file=sys.stderr)
        sys.exit(3)
    import strategy
    md = P.load_data()
    W = strategy.compute_target_weights(md)
    la = {"ok": True, "max_abs_diff": 0.0, "first_bad_date": None, "n_checks": 0, "n_checks_with_holdings": 0}
    if not args.no_lookahead_check:
        la = P.check_no_lookahead(strategy.compute_target_weights, md)
    ev = P.evaluate(W, md, cost_bps=args.cost)
    res = ev.pop("_res")
    Wx = res["weights"].loc[P.START:P.END]
    ev["lookahead_ok"] = bool(la["ok"]); ev["lookahead_max_diff"] = float(la["max_abs_diff"])
    ev["lookahead_checks"] = f'{la["n_checks_with_holdings"]}/{la["n_checks"]} informative'
    ev["lookahead_first_bad"] = la["first_bad_date"]
    ev["runtime_s"] = round(time.time() - t0, 1)
    ev["params"] = getattr(strategy, "PARAMS", {})
    os.makedirs(args.out, exist_ok=True)
    res["ret"].loc[P.START:P.END].to_csv(os.path.join(args.out, "daily_returns.csv"), header=True)
    res["turnover"].loc[P.START:P.END].to_csv(os.path.join(args.out, "turnover.csv"), header=True)
    Wx.to_csv(os.path.join(args.out, "weights.csv"))
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(ev, f, indent=2, default=str)
    print("---")
    order = ["sharpe_is", "sharpe_oos", "sharpe_full", "cagr_is", "cagr_oos", "cagr_full", "vol_is", "vol_full",
             "maxdd_is", "maxdd_oos", "maxdd_full", "sortino_is", "calmar_full", "hit_is", "beta_is", "alpha_ann_is",
             "tstat_is", "tstat_oos", "turnover_yr_is", "turnover_yr_full", "exposure_is", "exposure_full",
             "n_names_avg_is", "n_names_avg_full", "pct_days_invested_is", "pct_days_invested_full",
             "bench_sharpe_is", "bench_sharpe_oos", "bench_sharpe_full", "bench_cagr_full", "cost_bps",
             "n_harness_fixes", "lookahead_ok", "lookahead_max_diff", "lookahead_checks", "lookahead_first_bad",
             "days_is", "days_oos", "runtime_s"]
    for k in order:
        print(f"{k + ':':<22}{ev[k]}")
    if not ev["lookahead_ok"]:
        print("LOOKAHEAD DETECTED - result is invalid", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
