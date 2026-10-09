"""
Build reports/REPORT.md from results.tsv, reports/validation.json (final strategy) and, if present,
reports/baseline/validation.json (baseline). Usage: python make_report.py
"""
import json, os, sys, re
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "reports")


def load(p):
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    final = load(os.path.join(R, "validation.json")); base = load(os.path.join(R, "baseline", "validation.json"))
    if final is None:
        sys.exit("run validate.py first")
    tsv = pd.read_csv(os.path.join(HERE, "results.tsv"), sep="\t")
    keeps = tsv[tsv.status == "keep"].reset_index(drop=True)
    m = final["metrics"]; b = base["metrics"] if base else None
    src = open(os.path.join(HERE, "strategy.py")).read()
    doc = re.search(r'"""(.*?)"""', src, re.S).group(1).strip()
    L = []
    L += ["# RSI + EMA + VWAP on the Nifty 50 universe, 2020-2026: final report", "",
          f"Branch `autoresearch/oct9-nse`. {len(tsv)} experiments logged, {len(keeps)} kept. "
          f"Objective: in-sample Sharpe (2020-01-01..2023-12-31, net of 15 bps one-way costs, next-open execution). "
          f"Out-of-sample (2024-01-01..2026-10-09) was never used for any decision.", ""]
    L += ["## Headline", "", "| | Sharpe IS | Sharpe OOS | Sharpe FULL | CAGR FULL | maxDD FULL | turnover/yr | exposure |", "|---|---|---|---|---|---|---|---|"]
    L.append(f"| **final strategy** | {m['sharpe_is']} | {m['sharpe_oos']} | {m['sharpe_full']} | {m['cagr_full']} | {m['maxdd_full']} | {m['turnover_yr_full']} | {m['exposure_full']} |")
    if b:
        L.append(f"| baseline (first run) | {b['sharpe_is']} | {b['sharpe_oos']} | {b['sharpe_full']} | {b['cagr_full']} | {b['maxdd_full']} | {b['turnover_yr_full']} | {b['exposure_full']} |")
    L.append(f"| NIFTY 50 price index | {m['bench_sharpe_is']} | {m['bench_sharpe_oos']} | {m['bench_sharpe_full']} | {m['bench_cagr_full']} | - | - | 1.0 |")
    yt = pd.DataFrame(final["yearly"])
    L += ["", f"Cash (RBI repo) returned {', '.join(f'{r.year}: {r.rf:.1%}' for r in yt.itertuples())}.", ""]
    L += ["![equity](equity.png)", "", "![rolling](rolling_sharpe.png)", "", "![yearly](yearly.png)", ""]
    L += ["## The strategy", "", "```", doc, "```", "", f"Parameters: `{json.dumps(final['params'])}`", ""]
    L += ["## Research log (kept experiments)", "", "| # | commit | Sharpe IS | Sharpe OOS | Sharpe FULL | maxDD IS | turnover/yr | description |", "|---|---|---|---|---|---|---|---|"]
    for i, r in keeps.iterrows():
        L.append(f"| {i} | {r.commit} | {r.sharpe_is} | {r.sharpe_oos} | {r.sharpe_full} | {r.maxdd_is} | {r.turnover_yr} | {r.description} |")
    disc = tsv[tsv.status != "keep"]
    L += ["", f"{len(disc)} experiments were discarded or crashed; see `results.tsv` for every row.", ""]
    L += ["## Validation", ""]
    vmd = open(os.path.join(R, "validation.md")).read()
    vmd = vmd.split("## Headline", 1)[1]            # drop the duplicated header block
    L += ["### Headline (full metrics)" + vmd, ""]
    L += ["## How to read this", "",
          "* `sharpe_is` is the number the loop optimised; it is subject to selection bias across all logged trials. The deflated Sharpe ratio above accounts for the number and dispersion of those trials.",
          "* `sharpe_oos` is a true hold-out: nothing in 2024-2026 was used to choose rules or parameters.",
          "* The walk-forward row re-selects parameters each year from a perturbation grid using only prior years; it tests the parameter choice, not the rule structure (which was chosen in-sample).",
          "* Costs: 15 bps one-way (STT 10 + fees ~1 + slippage ~4). The cost table shows how quickly the edge decays if execution is worse.",
          "* Stock returns are price returns (no dividends), which understates long-only returns by roughly 1-1.5 %/yr versus a total-return benchmark; cash earns the repo rate.", ""]
    with open(os.path.join(R, "REPORT.md"), "w") as f:
        f.write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
