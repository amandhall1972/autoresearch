"""
Protocol-v4 lineage: every strategy of the v4 research scored on every seen window (no deep hold-out), at 15 and
25 bps. Rows: the v1 baseline and final, the plain EMA timer R0, every round's candidates (v4/r<k>_*.py), the v4
incumbents and the final strategy (strategy.py once it is replaced). Writes reports/v4/lineage.json and lineage.md.

Usage: python lineage4.py [--out reports/v4]
"""
import argparse, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P
import v4lab

KEYS = ["objective", "sharpe_pre", "sharpe_is", "sharpe_oos", "sharpe_span", "sharpe_full", "sharpe_span_drop10",
        "years_positive", "worst_year_sharpe", "maxdd_span", "turnover_yr_span", "exposure_span", "cagr_span"]


def files():
    order = ["v4/v1_baseline.py", "v4/v1_final.py", "v4/r0_ema_timer.py"]
    order += sorted(glob.glob(os.path.join(HERE, "v4", "r[1-9]_*.py")))
    order += sorted(glob.glob(os.path.join(HERE, "v4", "inc*.py")))
    order += ["strategy.py"]
    out = []
    for f in order:
        p = f if os.path.isabs(f) else os.path.join(HERE, f)
        if os.path.exists(p) and p not in out:
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "v4"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    md = v4lab.data()
    rows = []
    for p in files():
        mod = v4lab.load(p)
        W = mod.compute_target_weights(md)
        e = P.evaluate_v4(W, md); e.pop("_res")
        e25 = P.evaluate_v4(W, md, cost_bps=25); e25.pop("_res")
        doc = (mod.__doc__ or "").strip().splitlines()
        rows.append({"file": os.path.relpath(p, HERE), "summary": doc[0] if doc else "",
                     **{k: e[k] for k in KEYS}, "objective_25bps": e25["objective"],
                     **{f"sharpe_{y}": e[f"sharpe_{y}"] for y in range(2017, 2027)}})
        print(f"{rows[-1]['file']:<28}{e['objective']:>9}{e['sharpe_pre']:>9}{e['sharpe_is']:>9}{e['sharpe_oos']:>9}{e['sharpe_span']:>9}")
    with open(os.path.join(args.out, "lineage.json"), "w") as f:
        json.dump(rows, f, indent=2)
    L = ["# Protocol-v4 lineage (seen windows only)", "",
         "| strategy | objective | 2017-19 | 2020-23 | 2024-26 | 2017-26 | 2020-26 | drop-10 | +years | turnover | exposure | objective at 25 bps |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| `{r['file']}` | {r['objective']} | {r['sharpe_pre']} | {r['sharpe_is']} | {r['sharpe_oos']} | {r['sharpe_span']} | "
                 f"{r['sharpe_full']} | {r['sharpe_span_drop10']} | {r['years_positive']} | {r['turnover_yr_span']} | {r['exposure_span']} | {r['objective_25bps']} |")
    with open(os.path.join(args.out, "lineage.md"), "w") as f:
        f.write(P.md_round("\n".join(L)) + "\n")


if __name__ == "__main__":
    main()
