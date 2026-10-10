"""
Protocol v4 keep gates (program.md): compare a candidate strategy file with the incumbent on the 2016-2026 data
and print every gate that can be computed mechanically.

  sanity       invested on >= 30 % of days, diversified (>= 5 names on average, or NIFTYBEES - one unit of all
               50 members - at least half of the gross on invested days: protocol v4.2), turnover <= 40x a year,
               span max drawdown above -30 %, at most 3 harness fixes
  improves     the objective (lowest of the three window Sharpe ratios) rises
  windows      no window (2017-19, 2020-23, 2024-26) falls more than 0.10 below the incumbent's
  drop10       the 2017-04..2026-10 Sharpe without its 10 best days rises
  years        leave-one-year-out: with any single calendar year removed, the candidate's objective is still
               above the incumbent's (an improvement that rests on one year is not a keep)
  consistency  no fewer calendar years with a positive excess return
  parsimony    each extra PARAMS entry must buy at least 0.05 of objective; fewer parameters at no worse
               objective passes (a simplification); the use_rsi / use_ema / use_vwap switches do not count
Brief compliance (protocol v4.1, printed, required of the final strategy): a candidate exposes boolean PARAMS
use_rsi, use_ema and use_vwap that switch each indicator family's rules to their neutral setting; it is compliant
when switching off any one family changes the target weights on at least 5 % of the 2017-04..2026-10 sessions.
Gate 3 (plateau: the neighbouring values of every moved parameter also beat the incumbent) needs neighbour runs.

Usage: python compare4.py candidate.py [--incumbent strategy.py] [--years]
"""
import argparse, os, sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P
from run4 import load_strategy

PARSIMONY = 0.05
COMPLIANCE_MIN = 0.05
FAMILIES = ("use_rsi", "use_ema", "use_vwap")


def n_params(mod) -> int:
    return len([k for k in getattr(mod, "PARAMS", {}) if k not in FAMILIES])


def compliance(mod, md) -> dict:
    """Share of 2017-04..2026-10 sessions whose target weights change when one indicator family is switched off.
    None for a family the strategy does not expose a switch for (non-compliant)."""
    params = getattr(mod, "PARAMS", {})
    base = mod.compute_target_weights(md).reindex(index=md.calendar, columns=md.tickers).fillna(0.0)
    span = base.index[(base.index >= pd.Timestamp(P.V4_START)) & (base.index <= pd.Timestamp(P.END))]
    out = {}
    for fam in FAMILIES:
        if fam not in params:
            out[fam] = None
            continue
        saved = params[fam]
        try:
            params[fam] = False
            alt = mod.compute_target_weights(md).reindex(index=md.calendar, columns=md.tickers).fillna(0.0)
        finally:
            params[fam] = saved
        diff = (alt.loc[span] - base.loc[span]).abs().sum(axis=1) > 1e-9
        out[fam] = round(float(diff.mean()), 4)
    out["compliant"] = all(v is not None and v >= COMPLIANCE_MIN for v in (out[f] for f in FAMILIES))
    return out


def stats(path, md):
    mod = load_strategy(path)
    ev = P.evaluate_v4(mod.compute_target_weights(md), md)
    res = ev.pop("_res")
    ev["_ret"] = res["ret"].loc[P.V4_START:P.END]
    ev["n_params"] = n_params(mod)
    ev["_mod"] = mod
    return ev


def year_table(c, i, rf):
    years = sorted(set(c["_ret"].index.year))
    rows = []
    for y in years:
        rows.append((y, P.v4_objective(c["_ret"], rf, skip_year=y), P.v4_objective(i["_ret"], rf, skip_year=y)))
    return rows


def gates(c, i, ytab) -> dict:
    g = {
        "sanity": bool(c["pct_days_invested_span"] >= 0.30
                       and (c["n_names_avg_span"] >= 5 or c.get("etf_share_span", 0.0) >= 0.5)
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
            + ["maxdd_span", "turnover_yr_span", "exposure_span", "n_names_avg_span", "etf_share_span", "pct_days_invested_span",
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
    comp = compliance(c["_mod"], md)
    print("brief compliance (share of sessions moved when a family is switched off): "
          + ", ".join(f"{k} {v}" for k, v in comp.items()))
    for k, v in gates(c, i, ytab).items():
        print(f"{k:<24}{v}")


if __name__ == "__main__":
    main()
