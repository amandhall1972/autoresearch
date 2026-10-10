"""
Sealed pre-sample hold-out (protocol v2, program.md).

Scores a strategy file on 2017-04-01..2019-12-31 using the 2016-2019 point-in-time Nifty 50 (including
the names that left the index before 2020, data/raw_presample). No research decision may use this
number: the research loop never runs this script. It is run by the lead, once, at the end, on the
pre-registered lineage of incumbents (the baseline, every merged round winner, the final strategy).

Every invocation is appended to reports/holdout_log.tsv (UTC time, git commit, sha256 of the strategy
file, results) so the number of looks at the hold-out is auditable.

Usage: python holdout.py [--strategy strategy.py] [--label final] [--cost 15]
"""
import argparse, datetime, hashlib, importlib.util, json, os, subprocess, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P

LOG = os.path.join(HERE, "reports", "holdout_log.tsv")
COLS = ["utc", "git_commit", "strategy_sha256", "label", "cost_bps", "sharpe_pre", "cagr_pre", "maxdd_pre",
        "vol_pre", "turnover_yr_pre", "exposure_pre", "n_names_avg_pre", "bench_sharpe_pre", "bench_cagr_pre",
        "sharpe_2017", "sharpe_2018", "sharpe_2019", "lookahead_ok", "days_pre"]


def load_strategy(path):
    spec = importlib.util.spec_from_file_location("holdout_strategy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score(strategy_mod, md, cost_bps=P.COST_BPS_PER_SIDE, check=True) -> dict:
    W = strategy_mod.compute_target_weights(md)
    res = P.run_backtest(W, md, cost_bps=cost_bps)
    out = P.summarize(res, md, P.PRESAMPLE_START, P.PRESAMPLE_END, "_pre")
    r = res["ret"].loc[P.PRESAMPLE_START:P.PRESAMPLE_END]
    for y in (2017, 2018, 2019):
        ry = r.loc[str(y)]
        out[f"sharpe_{y}"] = round(P.sharpe(ry, md.rf.loc[ry.index]), 4)
    out["lookahead_ok"] = True
    if check:
        la = P.check_no_lookahead(strategy_mod.compute_target_weights, md, n_trunc=20, n_corrupt=10,
                                  lo=P.PRESAMPLE_START, hi=P.PRESAMPLE_END)
        out["lookahead_ok"] = bool(la["ok"])
    out["_res"] = res
    return out


def strategy_sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def evaluate_and_log(path, label, md=None, cost_bps=P.COST_BPS_PER_SIDE, commit=None) -> dict:
    """Score one strategy file on the hold-out and append the look to reports/holdout_log.tsv."""
    if commit is None:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
        except Exception:
            commit = ""
    md = md if md is not None else P.load_data(presample=True)
    ev = score(load_strategy(path), md, cost_bps=cost_bps)
    ev.pop("_res")
    row = {"utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "git_commit": commit,
           "strategy_sha256": strategy_sha(path), "label": label, "cost_bps": cost_bps, **ev}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, "a") as f:
        if new:
            f.write("\t".join(COLS) + "\n")
        f.write("\t".join(str(row.get(c, "")) for c in COLS) + "\n")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=os.path.join(HERE, "strategy.py"))
    ap.add_argument("--label", default="final")
    ap.add_argument("--cost", type=float, default=P.COST_BPS_PER_SIDE)
    args = ap.parse_args()
    row = evaluate_and_log(args.strategy, args.label, cost_bps=args.cost)
    print("--- SEALED PRE-SAMPLE HOLD-OUT 2017-04-01..2019-12-31 (logged to reports/holdout_log.tsv)")
    for c in COLS[3:]:
        print(f"{c + ':':<20}{row.get(c)}")


if __name__ == "__main__":
    main()
