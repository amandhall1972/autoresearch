"""
Protocol v4 keep gates (program.md): compare a candidate strategy file with the incumbent on the 2016-2026 data
and print every gate that can be computed mechanically.

  sanity       invested on >= 30 % of days, >= 5 names on average, turnover <= 40x a year, span max drawdown above
               -30 %, at most 3 harness fixes
  improves     the objective (lowest of the three window Sharpe ratios) rises
  windows      no window (2017-19, 2020-23, 2024-26) falls more than 0.10 below the incumbent's
  drop10       the 2017-04..2026-10 Sharpe without its 10 best days rises
  years        leave-one-year-out: with any single calendar year removed, the candidate's objective is still
               above the incumbent's (an improvement that rests on one year is not a keep)
  consistency  no fewer calendar years with a positive excess return
  parsimony    each extra PARAMS entry must buy at least 0.05 of objective; fewer parameters at no worse
               objective passes (a simplification)
Gate 3 (plateau: the neighbouring values of every moved parameter also beat the incumbent) needs neighbour runs.

Usage: python compare4.py candidate.py [--incumbent strategy.py] [--years]
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P
from run4 import load_strategy

PARSIMONY = 0.05


def stats(path, md):
    mod = load_strategy(path)
    ev = P.evaluate_v4(mod.compute_target_weights(md), md)
    res = ev.pop("_res")
    ev["_ret"] = res["ret"].loc[P.V4_START:P.END]
    ev["n_params"] = len(getattr(mod, "PARAMS", {}))
    return ev


def year_table(c, i, rf):
    years = sorted(set(c["_ret"].index.year))
    rows = []
    for y in years:
        rows.append((y, P.v4_objective(c["_ret"], rf, skip_year=y), P.v4_objective(i["_ret"], rf, skip_year=y)))
    return rows


def gates(c, i, ytab) -> dict:
    g = {
        "sanity": bool(c["pct_days_invested_span"] >= 0.30 and c["n_names_avg_span"] >= 5
                       and c["turnover_yr_span"] <= 40 and c["maxdd_span"] > -0.30 and c["n_harness_fixes"] <= 3),
        "improves": bool(c["objective"] > i["objective"]),
        "windows": bool(all(c[f"sharpe_{w}"] >= i[f"sharpe_{w}"] - 0.10 for w, _, _ in P.V4_WINDOWS)),
        "drop10": bool(c["sharpe_span_drop10"] > i["sharpe_span_drop10"]),
        "years": bool(all(a > b for _, a, b in ytab)),
        "consistency": bool(c["years_positive"] >= i["years_positive"]),
    }
    extra = c["n_params"] - i["n_params"]
    gain = c["objective"] - i["objective"]
    g["parsimony"] = bool(gain >= PARSIMONY * extra) if extra > 0 else True
    g["all_mechanical_gates"] = all(g.values())
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--incumbent", default=os.path.join(HERE, "strategy.py"))
    ap.add_argument("--years", action="store_true", help="print the leave-one-year-out table")
    args = ap.parse_args()
    md = P.load_data(presample=True, end=P.END)
    c = stats(args.candidate, md)
    i = stats(args.incumbent, md)
    rf = md.rf.loc[P.V4_START:P.END]
    ytab = year_table(c, i, rf)
    keys = (["objective", "sharpe_pre", "sharpe_is", "sharpe_oos", "sharpe_span", "sharpe_full", "sharpe_span_drop10",
             "sharpe_span_gross", "years_positive", "worst_year_sharpe"] + [f"sharpe_{y}" for y in range(2017, 2027)]
            + ["maxdd_span", "turnover_yr_span", "exposure_span", "n_names_avg_span", "pct_days_invested_span",
               "n_harness_fixes", "n_params"])
    print(f"{'':<24}{'candidate':>12}{'incumbent':>12}")
    for k in keys:
        print(f"{k:<24}{c.get(k)!s:>12}{i.get(k)!s:>12}")
    worst = min(a - b for _, a, b in ytab)
    print(f"leave-one-year-out: objective gain min {worst:.4f}; years where the gain survives "
          f"{sum(1 for _, a, b in ytab if a > b)}/{len(ytab)}")
    if args.years:
        for y, a, b in ytab:
            print(f"  without {y}: candidate {a:.4f}  incumbent {b:.4f}  gain {a - b:+.4f}")
    for k, v in gates(c, i, ytab).items():
        print(f"{k:<24}{v}")


if __name__ == "__main__":
    main()
