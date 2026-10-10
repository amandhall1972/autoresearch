"""
Score the protocol-v4 pre-registered set on the deep hold-out (2011-04-01..2016-05-31) - once, at the end.

The set is fixed in program.md (Protocol v4.1 rule 4 and the round-3 merge note) before any look: the v1 baseline
and final, the plain EMA timer R0, the six round-1 candidates (information only), the round-3 with-core variant
(information only), every v4 incumbent and the final strategy. Each strategy is scored by deep_holdout.py, which
appends one row per look to reports/deep_holdout_log.tsv; this script refuses to run if any of them has been
looked at before (one look per strategy file content), so the log stays the complete record.

Writes reports/v4/deep_holdout.json and deep_holdout.md (and per-strategy daily returns under reports/v4/deep/).

Usage: python deep4.py --final FILE [--incumbents v4/inc1_pullback.py ...]
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pandas as pd
import prepare as P
import deep_holdout as D

INFORMATION = ["v4/v1_baseline.py", "v4/v1_final.py", "v4/r0_ema_timer.py", "v4/r1_timing.py", "v4/r1_trendbasket.py",
               "v4/r1_relstrength.py", "v4/r1_pullback.py", "v4/r1_repair.py", "v4/r1_overlay.py", "v4/r3_clean.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", required=True)
    ap.add_argument("--incumbents", nargs="*", default=["v4/inc1_pullback.py"])
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "v4"))
    args = ap.parse_args()
    roles = [(f, "information") for f in INFORMATION] + [(f, "incumbent") for f in args.incumbents] + [(args.final, "final")]
    seen, order = {}, []
    for f, role in roles:
        p = f if os.path.isabs(f) else os.path.join(HERE, f)
        sha = D.strategy_sha(p)
        if sha in seen:                                  # the final may be an incumbent's content: score once
            seen[sha]["roles"].append(role); continue
        seen[sha] = {"file": os.path.relpath(p, HERE), "path": p, "roles": [role]}
        order.append(sha)
    if os.path.exists(D.LOG):
        log = pd.read_csv(D.LOG, sep="\t")
        again = [seen[s]["file"] for s in order if s in set(log["strategy_sha256"].astype(str))]
        if again:
            raise SystemExit(f"already looked at on the deep hold-out: {again}; refusing a second look")
    md = P.load_data(deep=True)
    rep = {"window": [P.DEEP_START, P.DEEP_END], "data": D.data_report(md), "rows": []}
    os.makedirs(os.path.join(args.out, "deep"), exist_ok=True)
    for s in order:
        it = seen[s]
        label = "+".join(it["roles"]) + ":" + os.path.basename(it["file"])
        row = D.evaluate_and_log(it["path"], label, md=md,
                                 returns_out=os.path.join(args.out, "deep", os.path.basename(it["file"])[:-3] + ".csv"))
        rep["rows"].append({"file": it["file"], "roles": it["roles"], **{k: row.get(k) for k in D.COLS[3:]}})
        print(f"{it['file']:<28}{'/'.join(it['roles']):<24}sharpe {row['sharpe_deep']:>8}  nifty {row['bench_sharpe_deep']:>8}  "
              f"cagr {row['cagr_deep']:>8}  maxdd {row['maxdd_deep']:>8}  lookahead {row['lookahead_ok']}")
    with open(os.path.join(args.out, "deep_holdout.json"), "w") as f:
        json.dump(rep, f, indent=2, default=str)
    L = [f"# Deep hold-out {P.DEEP_START}..{P.DEEP_END} (scored once; pre-registered set)", "",
         "| strategy | role | Sharpe | CAGR | max DD | exposure | NIFTY Sharpe | " + " | ".join(str(y) for y in D.YEARS) + " |",
         "|---|---|---|---|---|---|---|" + "---|" * len(D.YEARS)]
    for r in rep["rows"]:
        L.append(f"| `{r['file']}` | {'/'.join(r['roles'])} | {r['sharpe_deep']} | {r['cagr_deep']} | {r['maxdd_deep']} | "
                 f"{r['exposure_deep']} | {r['bench_sharpe_deep']} | " + " | ".join(str(r[f'sharpe_{y}']) for y in D.YEARS) + " |")
    with open(os.path.join(args.out, "deep_holdout.md"), "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
