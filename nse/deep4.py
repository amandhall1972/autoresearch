"""
Score the protocol-v4 pre-registered set on the deep hold-out (2011-04-01..2016-05-31) - once, at the end.

The set is fixed in program.md (Protocol v4.1 rule 4 and the round-3 merge note) before any look: the v1 baseline
and final, the plain EMA timer R0, the six round-1 candidates (information only), the round-3 with-core variant
(information only), every v4 incumbent and the final strategy. Each strategy is scored by deep_holdout.py, which
appends one row per look to reports/deep_holdout_log.tsv; this script refuses to run if any of them has been
looked at before (one look per strategy file content), so the log stays the complete record.

Writes reports/v4/deep_holdout.json and deep_holdout.md (and per-strategy daily returns under reports/v4/deep/).

Usage: python deep4.py --final FILE [--incumbents v4/inc1_pullback.py ...]
       python deep4.py --rebuild-report     # rewrite deep_holdout.json/.md from the saved daily returns (no look)
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import pandas as pd
import prepare as P
import deep_holdout as D
import validate as V

INFORMATION = ["v4/v1_baseline.py", "v4/v1_final.py", "v4/r0_ema_timer.py", "v4/r1_timing.py", "v4/r1_trendbasket.py",
               "v4/r1_relstrength.py", "v4/r1_pullback.py", "v4/r1_repair.py", "v4/r1_overlay.py", "v4/r3_clean.py"]


PRECISE = ("sharpe_deep", "cagr_deep", "maxdd_deep", "vol_deep", "exposure_deep", "bench_sharpe_deep", "bench_cagr_deep")


def metrics_from_saved(path):
    """The deep metrics recomputed at full precision from a look's saved daily returns (reports/v4/deep/*.csv:
    strategy, nifty, cash, exposure over the window). Runs no strategy and reads no deep market data."""
    d = pd.read_csv(path, index_col=0, parse_dates=True)
    r, rf, bench = d["strategy"], d["cash"], d["nifty"]
    out = {"sharpe_deep": P.sharpe(r, rf), "cagr_deep": P.cagr(r), "maxdd_deep": P.max_drawdown(r),
           "vol_deep": float(r.std(ddof=1) * (P.TRADING_DAYS ** 0.5)), "exposure_deep": float(d["exposure"].mean()),
           "bench_sharpe_deep": P.sharpe(bench, rf), "bench_cagr_deep": P.cagr(bench.dropna())}
    for y in D.YEARS:
        ry = r.loc[str(y)]
        out[f"sharpe_{y}"] = P.sharpe(ry, rf.loc[ry.index]) if len(ry) > 20 else None
    return out


SAMPLING_SEED, SAMPLING_PATHS, SAMPLING_BLOCK = 20261011, 2000, 20


def sampling_error(out_dir, rows):
    """Sampling error of each look's deep Sharpe from its saved daily excess returns: the daily t-statistic, the
    t-statistic of monthly sums, the variance ratio of 20-day sums (below 1 when days are negatively autocorrelated)
    and a 95 % stationary-bootstrap interval (mean block 20 sessions). All strategies share the same bootstrap paths,
    so the differences against R0 and the NIFTY 50 are paired."""
    ex = {}
    for row in rows:
        d = pd.read_csv(os.path.join(out_dir, "deep", os.path.basename(row["file"])[:-3] + ".csv"), index_col=0, parse_dates=True)
        ex[row["file"]] = d["strategy"] - d["cash"]
        ex.setdefault("NIFTY50", d["nifty"].fillna(0.0) - d["cash"])
    T = len(next(iter(ex.values())))
    idx = V.block_bootstrap(np.arange(T, dtype=float), SAMPLING_PATHS, SAMPLING_BLOCK,
                            np.random.default_rng(SAMPLING_SEED)).astype(int)
    sr = lambda v: float(v.mean() / v.std(ddof=1) * np.sqrt(P.TRADING_DAYS))      # noqa: E731
    boots = {k: np.array([sr(x.values[i]) for i in idx]) for k, x in ex.items()}
    out = {"paths": SAMPLING_PATHS, "mean_block": SAMPLING_BLOCK, "seed": SAMPLING_SEED, "strategies": {}, "differences": {}}
    for k, x in ex.items():
        agg = x.rolling(20).sum().dropna().values[::20]
        mon = x.resample("ME").sum()
        out["strategies"][k] = {"sharpe": round(sr(x.values), 6),
                                "tstat_daily": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 4),
                                "tstat_monthly": round(float(mon.mean() / mon.std(ddof=1) * np.sqrt(len(mon))), 4),
                                "variance_ratio_20d": round(float(agg.var(ddof=1) / (20 * x.var(ddof=1))), 4),
                                "ci95": [round(float(np.percentile(boots[k], 2.5)), 6), round(float(np.percentile(boots[k], 97.5)), 6)]}
    final = next(r["file"] for r in rows if "final" in r["roles"])
    for other in ("v4/r0_ema_timer.py", "NIFTY50"):
        dlt = boots[final] - boots[other]
        out["differences"][f"{final} - {other}"] = {
            "value": round(sr(ex[final].values) - sr(ex[other].values), 6),
            "ci95": [round(float(np.percentile(dlt, 2.5)), 6), round(float(np.percentile(dlt, 97.5)), 6)]}
    return out


def rebuild_report(out_dir):
    """Rewrite deep_holdout.json and .md at full precision from the saved returns of the looks already taken. Each
    recomputed value must round to the value logged at the look (reports/deep_holdout_log.tsv), or nothing is
    written; fields the saved returns cannot give (turnover, names, invested days, lookahead) keep the logged value."""
    rep = json.load(open(os.path.join(out_dir, "deep_holdout.json")))
    log = pd.read_csv(D.LOG, sep="\t")
    log["base"] = log["label"].str.split(":").str[-1]
    for row in rep["rows"]:
        base = os.path.basename(row["file"])
        logged = log[log["base"] == base]
        if len(logged) != 1:
            raise SystemExit(f"{base}: expected exactly one logged look, found {len(logged)}")
        logged = logged.iloc[0]
        new = metrics_from_saved(os.path.join(out_dir, "deep", base[:-3] + ".csv"))
        for k, v in new.items():
            ref = logged[k]
            if v is None or pd.isna(ref):
                if not (v is None and (pd.isna(ref) or str(ref) == "None")):
                    raise SystemExit(f"{base} {k}: saved returns give {v}, the log has {ref}")
                continue
            digits = 3 if k == "exposure_deep" else 4
            if abs(round(v, digits) - float(ref)) > 10 ** -digits / 2 + 1e-12:
                raise SystemExit(f"{base} {k}: saved returns give {v:.6f}, the log has {ref}; not rewriting")
            row[k] = round(v, 6)
    rep["sampling"] = sampling_error(out_dir, rep["rows"])
    with open(os.path.join(out_dir, "deep_holdout.json"), "w") as f:
        json.dump(rep, f, indent=2, default=str)
    write_md(rep, out_dir)
    print(f"rebuilt {os.path.join(out_dir, 'deep_holdout.json')} from {len(rep['rows'])} saved looks; every value matches the log")


def write_md(rep, out_dir):
    L = [f"# Deep hold-out {P.DEEP_START}..{P.DEEP_END} (scored once; pre-registered set)", "",
         "| strategy | role | Sharpe | CAGR | max DD | exposure | NIFTY Sharpe | " + " | ".join(str(y) for y in D.YEARS) + " |",
         "|---|---|---|---|---|---|---|" + "---|" * len(D.YEARS)]
    r4 = lambda x: "-" if x is None or (isinstance(x, float) and pd.isna(x)) else (round(x, 4) if isinstance(x, float) else x)  # noqa: E731
    for r in rep["rows"]:
        L.append(f"| `{r['file']}` | {'/'.join(r['roles'])} | {r4(r['sharpe_deep'])} | {r4(r['cagr_deep'])} | {r4(r['maxdd_deep'])} | "
                 f"{r4(r['exposure_deep'])} | {r4(r['bench_sharpe_deep'])} | " + " | ".join(str(r4(r[f'sharpe_{y}'])) for y in D.YEARS) + " |")
    with open(os.path.join(out_dir, "deep_holdout.md"), "w") as f:
        f.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    if "--rebuild-report" in sys.argv:
        ap.add_argument("--rebuild-report", action="store_true")
        ap.add_argument("--out", default=os.path.join(HERE, "reports", "v4"))
        rebuild_report(ap.parse_args().out)
        return
    ap.add_argument("--final", required=True)
    ap.add_argument("--incumbents", nargs="*", default=["v4/inc1_pullback.py"])
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "v4"))
    args = ap.parse_args()
    roles = [(f, "information") for f in INFORMATION] + [(f, "incumbent") for f in args.incumbents] + [(args.final, "final")]
    seen, order = {}, []
    for f, role in roles:
        p = f if os.path.isabs(f) else os.path.join(HERE, f)
        sha = D.code_fingerprint(p)                      # same code, one look (a docstring edit does not count)
        if sha in seen:                                  # the final may be an incumbent's content: score once
            seen[sha]["roles"].append(role); continue
        seen[sha] = {"file": os.path.relpath(p, HERE), "path": p, "roles": [role]}
        order.append(sha)
    if os.path.exists(D.LOG):
        again = [seen[s]["file"] for s in order if D.looked_at(seen[s]["path"])]
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
    write_md(rep, args.out)


if __name__ == "__main__":
    main()
