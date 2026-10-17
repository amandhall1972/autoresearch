"""
Compare a candidate strategy file with the incumbent on IN-SAMPLE data only and print every protocol keep
gate that can be computed mechanically (program.md): sanity gates, gate 1 (both halves), gate 2 (drop5)
and gate 5 (leave-one-spell-out). Gate 3 (plateau) needs neighbour runs; gate 4 is a judgement on deletions.

Gate 5: an invested spell is a maximal run of consecutive days on which either book holds stocks. With
each spell removed in turn from both daily return series, the candidate's in-sample Sharpe must still be
above the incumbent's: an improvement that rests on a single episode is not a keep.

Usage: python compare.py candidate.py [--incumbent v4/v1_final.py]   (prints in-sample numbers only; the v1 protocol)
"""
import argparse, importlib.util, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def in_sample_data():
    """Research data cut at IS_END: nothing after the in-sample window is even loaded into the strategies."""
    return P.load_data().slice(pd.Timestamp(P.IS_END))


def is_stats(mod, md):
    res = P.run_backtest(mod.compute_target_weights(md), md)
    s = P.summarize(res, md, P.START, P.IS_END, "_is"); s.update(P.robustness_is(res, md))
    s["n_harness_fixes"] = res["n_harness_fixes"]
    s["_ret"] = res["ret"].loc[P.START:P.IS_END]; s["_expo"] = res["exposure"].loc[P.START:P.IS_END]
    return s


def spells(*exposures) -> list:
    inv = np.zeros(len(exposures[0]), dtype=bool)
    for e in exposures:
        inv |= e.values > 1e-9
    out, start = [], None
    for i, f in enumerate(inv):
        if f and start is None:
            start = i
        if not f and start is not None:
            out.append((start, i)); start = None
    if start is not None:
        out.append((start, len(inv)))
    return out


def leave_one_spell_out(ret_c, ret_i, rf, sp) -> pd.DataFrame:
    idx = ret_c.index
    rows = []
    for a, b in sp:
        keep = np.ones(len(idx), dtype=bool); keep[a:b] = False
        k = idx[keep]
        rows.append(dict(spell_start=idx[a].date(), spell_end=idx[b - 1].date(), days=b - a,
                         cand=P.sharpe(ret_c.loc[k], rf.loc[k]), inc=P.sharpe(ret_i.loc[k], rf.loc[k])))
    df = pd.DataFrame(rows)
    df["gain"] = df.cand - df.inc
    return df


def gates(c, i, lo) -> dict:
    g = {
        "sanity": bool(c["pct_days_invested_is"] >= 0.30 and c["n_names_avg_is"] >= 5 and c["turnover_yr_is"] <= 60
                       and c["maxdd_is"] > -0.45 and c["n_harness_fixes"] <= 3),
        "improves": bool(c["sharpe_is"] > i["sharpe_is"]),
        "gate1_halves": bool(c["sharpe_is_h1"] >= i["sharpe_is_h1"] - 0.10 and c["sharpe_is_h2"] >= i["sharpe_is_h2"] - 0.10),
        "gate2_drop5": bool(c["sharpe_is_drop5"] > i["sharpe_is_drop5"]),
        "gate5_spells": bool((lo.gain > 0).all()) if len(lo) else False,
    }
    g["all_mechanical_gates"] = all(g.values())
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--incumbent", default=os.path.join(HERE, "v4", "v1_final.py"))
    ap.add_argument("--spells", action="store_true", help="print the leave-one-spell-out table")
    args = ap.parse_args()
    md = in_sample_data()
    c = is_stats(_load(args.candidate, "cand"), md)
    i = is_stats(_load(args.incumbent, "inc"), md)
    rf = md.rf.loc[P.START:P.IS_END]
    lo = leave_one_spell_out(c["_ret"], i["_ret"], rf, spells(c["_expo"], i["_expo"]))
    keys = ["sharpe_is", "sharpe_is_h1", "sharpe_is_h2", "sharpe_is_drop5", "sharpe_is_gross", "maxdd_is", "turnover_yr_is",
            "exposure_is", "n_names_avg_is", "pct_days_invested_is", "n_harness_fixes"]
    print(f"{'':<22}{'candidate':>12}{'incumbent':>12}")
    for k in keys:
        print(f"{k:<22}{c[k]:>12}{i[k]:>12}")
    print(f"spells: {len(lo)}; leave-one-spell-out gain min {lo.gain.min():.4f}, median {lo.gain.median():.4f}, "
          f"spells where the gain survives {int((lo.gain > 0).sum())}/{len(lo)}")
    if args.spells:
        print(lo.round(4).to_string(index=False))
    for k, v in gates(c, i, lo).items():
        print(f"{k:<22}{v}")


if __name__ == "__main__":
    main()
