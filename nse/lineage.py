"""
Score the incumbent lineage of the research loop: every distinct strategy that was a project-level keep in
results.tsv (the baseline, each merged round winner, the final strategy), on the current harness.

  * research data: in-sample (the loop's objective), 2024-2026 (report-only) and full 2020-2026
  * --holdout: also the sealed 2017-2019 pre-sample hold-out (each look is logged by holdout.py). Per the
    pre-registration in program.md this is run once, at the end, and cannot change the final strategy.

Writes reports/lineage.json, reports/lineage.md and reports/lineage.png.
Usage: python lineage.py [--holdout]
"""
import argparse, json, os, subprocess, sys, tempfile
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P
import holdout as H


def lineage_rows(tsv_path):
    t = pd.read_csv(tsv_path, sep="\t", dtype={"commit": str})
    k = t[t.status == "keep"]
    k = k[~k.description.astype(str).str.startswith("[harness]")]       # re-measurements, not new strategies
    return k.reset_index(drop=True)


def strategy_at(commit, dest_dir):
    src = subprocess.run(["git", "show", f"{commit}:nse/strategy.py"], cwd=HERE, capture_output=True, text=True)
    if src.returncode != 0:
        raise RuntimeError(src.stderr)
    path = os.path.join(dest_dir, f"strategy_{commit}.py")
    open(path, "w").write(src.stdout)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "reports"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rows = lineage_rows(os.path.join(HERE, "results.tsv"))
    md = P.load_data()
    md_pre = P.load_data(presample=True) if args.holdout else None
    out, seen = [], set()
    with tempfile.TemporaryDirectory() as td:
        for i, r in rows.iterrows():
            path = strategy_at(r.commit, td)
            sha = H.strategy_sha(path)
            if sha in seen:
                continue
            seen.add(sha)
            mod = H.load_strategy(path)
            ev = P.evaluate(mod.compute_target_weights(md), md); ev.pop("_res")
            stage = "baseline" if not out else f"keep {len(out)}"
            rec = {"stage": stage, "commit": r.commit, "sha": sha, "description": str(r.description)[:160],
                   **{k: ev[k] for k in ["sharpe_is", "sharpe_is_h1", "sharpe_is_h2", "sharpe_is_drop5", "sharpe_oos",
                                         "sharpe_full", "cagr_full", "maxdd_full", "turnover_yr_is", "exposure_is"]}}
            if args.holdout:
                last = (i == len(rows) - 1)
                h = H.evaluate_and_log(path, f"lineage:{stage}:{r.commit}", md=md_pre, commit=r.commit,
                                       returns_out=os.path.join(args.out, "holdout_final_returns.csv") if last else None)
                rec.update({k: h[k] for k in ["sharpe_pre", "cagr_pre", "maxdd_pre", "bench_sharpe_pre",
                                              "sharpe_2017", "sharpe_2018", "sharpe_2019", "lookahead_ok"]})
            out.append(rec)
            print(rec)
    if out:
        out[-1]["stage"] = "final" if len(out) > 1 else out[-1]["stage"]
    json.dump(out, open(os.path.join(args.out, "lineage.json"), "w"), indent=2)
    cols = ["stage", "commit", "sharpe_is", "sharpe_is_h1", "sharpe_is_h2", "sharpe_oos", "sharpe_full"] + \
           (["sharpe_pre", "bench_sharpe_pre"] if args.holdout else []) + ["turnover_yr_is", "exposure_is", "description"]
    L = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    L += ["| " + " | ".join(str(o.get(c, "")) for c in cols) + " |" for o in out]
    open(os.path.join(args.out, "lineage.md"), "w").write("\n".join(L) + "\n")
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = np.arange(len(out)); series = [("sharpe_is", "in-sample 2020-23 (selection)"), ("sharpe_oos", "2024-26 (report-only)")]
    if args.holdout:
        series.append(("sharpe_pre", "sealed pre-sample 2017-19"))
    w = 0.8 / len(series)
    fig, ax = plt.subplots(figsize=(max(7, 1.6 * len(out)), 4.2))
    for j, (k, lab) in enumerate(series):
        ax.bar(x + (j - (len(series) - 1) / 2) * w, [o[k] for o in out], w, label=lab)
    ax.axhline(0, color="k", linewidth=0.6); ax.set_xticks(x); ax.set_xticklabels([f"{o['stage']}\n{o['commit']}" for o in out])
    ax.set_ylabel("annualised Sharpe (net, excess of repo)"); ax.set_title("Incumbent lineage of the research loop"); ax.legend(); ax.grid(alpha=0.3, axis="y")
    fig.tight_layout(); fig.savefig(os.path.join(args.out, "lineage.png"), dpi=130); plt.close(fig)
    print("\n".join(L))


if __name__ == "__main__":
    main()
