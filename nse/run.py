"""
Run one experiment: load data, build target weights from strategy.py, verify no lookahead,
evaluate and print a summary block. Usage: python run.py [--cost 15] [--report] [--strategy strategy.py]   (the v1 final: --strategy v4/v1_final.py)

By default only in-sample (2020-2023) information is printed and written to runs/latest/: the
research loop must never see the 2024-2026 hold-out. `--report` (used by the lead/merge step and
the final report, never by researchers) also prints and writes the OOS and FULL metrics.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P


IS_KEYS = ["sharpe_is", "sharpe_is_h1", "sharpe_is_h2", "sharpe_is_drop5", "sharpe_is_gross", "cagr_is", "vol_is",
           "maxdd_is", "sortino_is", "hit_is", "beta_is", "alpha_ann_is", "tstat_is", "turnover_yr_is", "exposure_is",
           "n_names_avg_is", "pct_days_invested_is", "bench_sharpe_is", "cost_bps", "n_harness_fixes", "lookahead_ok",
           "lookahead_max_diff", "lookahead_checks", "lookahead_first_bad", "days_is", "runtime_s"]
REPORT_KEYS = ["sharpe_oos", "sharpe_full", "cagr_oos", "cagr_full", "vol_full", "maxdd_oos", "maxdd_full",
               "calmar_full", "tstat_oos", "turnover_yr_full", "exposure_full", "n_names_avg_full",
               "pct_days_invested_full", "bench_sharpe_oos", "bench_sharpe_full", "bench_cagr_full", "days_oos"]


def visible_keys(report: bool) -> list:
    """Keys printed/written by run.py. Without --report nothing after IS_END is exposed."""
    return IS_KEYS + (REPORT_KEYS if report else [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=P.COST_BPS_PER_SIDE)
    ap.add_argument("--out", default=os.path.join(HERE, "runs", "latest"))
    ap.add_argument("--no-lookahead-check", action="store_true")
    ap.add_argument("--report", action="store_true", help="also show OOS/FULL metrics (lead/report use only)")
    ap.add_argument("--strategy", default=os.path.join(HERE, "strategy.py"))
    args = ap.parse_args()
    t0 = time.time()
    src = open(args.strategy).read()
    banned = ["open(", "read_csv", "read_parquet", "read_pickle", "load_data", "os.", "glob", "pathlib", "subprocess",
              "pickle", "np.load", "__import__", "importlib", "urllib", "requests", "socket"]
    hits = [b for b in banned if b in src]
    if hits:
        print(f"{args.strategy} must not access files/network/the loader; found: {hits}", file=sys.stderr)
        sys.exit(3)
    import importlib.util
    spec = importlib.util.spec_from_file_location("strategy", args.strategy)
    strategy = importlib.util.module_from_spec(spec); spec.loader.exec_module(strategy)
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
    end = P.END if args.report else P.IS_END
    res["ret"].loc[P.START:end].to_csv(os.path.join(args.out, "daily_returns.csv"), header=True)
    res["turnover"].loc[P.START:end].to_csv(os.path.join(args.out, "turnover.csv"), header=True)
    Wx.loc[:end].to_csv(os.path.join(args.out, "weights.csv"))
    shown = visible_keys(args.report)
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump({k: ev[k] for k in shown + ["params"]}, f, indent=2, default=str)
    print("---")
    for k in shown:
        print(f"{k + ':':<22}{ev[k]}")
    if not ev["lookahead_ok"]:
        print("LOOKAHEAD DETECTED - result is invalid", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
