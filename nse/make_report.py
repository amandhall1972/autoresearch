"""
Build reports/REPORT.md. When the protocol-v4 reports exist (reports/v4: validation4.py, lineage4.py, deep4.py)
the report opens with the v2 strategy; the v1 record follows, built from results.tsv, reports/validation.json
(the v1 final) and, if present, reports/baseline/validation.json (the v1 baseline). Usage: python make_report.py
"""
import json, os, sys, re
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "reports")
if "--dir" in sys.argv:                       # alternative report directory (dry runs)
    R = sys.argv[sys.argv.index("--dir") + 1]


def load(p):
    return json.load(open(p)) if os.path.exists(p) else None


def md_body(path, demote=True):
    """A generated markdown report without its title line, headings one level down."""
    if not os.path.exists(path):
        return ""
    body = open(path).read().split("\n", 1)[1].strip()
    return body.replace("\n## ", "\n### ") if demote else body


def v4_section():
    """The protocol-v4 (v2 strategy) part of the report, or [] when its reports have not been built."""
    R4 = os.path.join(R, "v4")
    v = load(os.path.join(R4, "validation.json"))
    lin = load(os.path.join(R4, "lineage.json"))
    if v is None or lin is None:
        return []
    deep = load(os.path.join(R4, "deep_holdout.json"))
    m, bm = v["metrics"], v["benchmarks"]
    by = {r["file"]: r for r in lin}
    v1, r0 = by["v4/v1_final.py"], by["v4/r0_ema_timer.py"]
    drow = {r["file"]: r for r in deep["rows"]} if deep else {}
    dfin = next((r for r in deep["rows"] if "final" in r["roles"]), None) if deep else None
    tsv4 = pd.read_csv(os.path.join(HERE, "results_v4.tsv"), sep="\t")
    src = open(os.path.join(HERE, v["strategy"])).read()
    doc = re.search(r'"""(.*?)"""', src, re.S).group(1).strip()
    dv = lambda f: drow[f]["sharpe_deep"] if f in drow else "-"          # noqa: E731
    L = ["# RSI + EMA + VWAP on the Nifty 50: the v2 strategy (protocol v4)", "",
         "The question: which changes make 2024-2026 positive as well, and the strategy effective over the whole of "
         "2020-2026? Protocol v4 researched on every window seen so far at once (2017-04..2019-12, 2020-2023, "
         "2024-01..2026-10) and maximised the worst of the three window Sharpes, with keep gates against regime "
         "trading, best-day dependence, single years and parameter peaks, and a brief-compliance requirement for the "
         "final strategy. A new hold-out, "
         f"{deep['window'][0] if deep else '2011-04-01'}..{deep['window'][1] if deep else '2016-05-31'}, was built before "
         f"round 1 and scored once at the end. {len(tsv4):,} rows are logged in `results_v4.tsv`; the protocol, every "
         "amendment and every merge are in `program.md`; the research note is RCG-RN-REV-002 (`make_pdf_v4.py`).", "",
         "## Headline", "",
         "Sharpe ratio of daily returns over the RBI repo rate, net of 15 bps one-way costs, next-open execution.", "",
         "| | 2017-19 | 2020-23 | 2024-26 | 2017-26 | 2020-26 | worst window | deep hold-out 2011-16 |",
         "|---|---|---|---|---|---|---|---|",
         f"| **v2 strategy** (`strategy.py`) | {m['sharpe_pre']} | {m['sharpe_is']} | {m['sharpe_oos']} | {m['sharpe_span']} | "
         f"{m['sharpe_full']} | {m['objective']} | {dfin['sharpe_deep'] if dfin else '-'} |",
         f"| v1 strategy (`v4/v1_final.py`) | {v1['sharpe_pre']} | {v1['sharpe_is']} | {v1['sharpe_oos']} | {v1['sharpe_span']} | "
         f"{v1['sharpe_full']} | {v1['objective']} | {dv('v4/v1_final.py')} |",
         f"| EMA timer R0 (`v4/r0_ema_timer.py`) | {r0['sharpe_pre']} | {r0['sharpe_is']} | {r0['sharpe_oos']} | {r0['sharpe_span']} | "
         f"{r0['sharpe_full']} | {r0['objective']} | {dv('v4/r0_ema_timer.py')} |"]
    for k, lab in [("niftybees", "NIFTYBEES buy and hold"), ("nifty50", "NIFTY 50 price index")]:
        b = bm[k]
        L.append(f"| {lab} | {b['pre']} | {b['is']} | {b['oos']} | {b['span']} | {b['full']} | "
                 f"{min(b['pre'], b['is'], b['oos'])} | {dfin['bench_sharpe_deep'] if (dfin and k == 'nifty50') else '-'} |")
    L += ["", f"The v2 strategy: CAGR {m['cagr_span']:.1%} over 2017-2026 (repo rate included), maximum drawdown "
          f"{m['maxdd_span']:.1%}, average exposure {m['exposure_span']:.0%}, turnover {m['turnover_yr_span']:.1f}x a year; "
          f"it beat cash in {sum(1 for r in v['yearly'] if r['strat_ret'] - r['rf'] > 0.0025)} of 10 calendar years"
          + "".join(f", was level with it in {r['year']}" for r in v["yearly"] if abs(r["strat_ret"] - r["rf"]) <= 0.0025)
          + "".join(f" and trailed it in {r['year']}" for r in v["yearly"] if r["strat_ret"] - r["rf"] < -0.0025)
          + " (2017 from April, 2026 to October).", "",
          "## The v2 strategy", "", "```", doc, "```", "", f"Parameters: `{json.dumps(v['params'])}`", "",
          "## Every protocol-v4 strategy on every seen window", "", md_body(os.path.join(R4, "lineage.md")), ""]
    if deep:
        L += ["## Deep hold-out, scored once", "", md_body(os.path.join(R4, "deep_holdout.md")), "",
              f"Data checks: {deep['data']['sessions_scored']:,} sessions scored, {deep['data']['members_with_bars_min']} to "
              f"{deep['data']['members_with_bars_max']} members with bars on any day; the look log is `reports/deep_holdout_log.tsv`.", ""]
        smp = deep.get("sampling")
        if smp and dfin:
            fs = smp["strategies"][dfin["file"]]
            d_r0 = smp["differences"][f"{dfin['file']} - v4/r0_ema_timer.py"]
            d_nx = smp["differences"][f"{dfin['file']} - NIFTY50"]
            L += [f"Sampling error (stationary bootstrap, mean block {smp['mean_block']} sessions, {smp['paths']} paired paths): the "
                  f"final's Sharpe {fs['sharpe']:.2f} has a 95 % interval of {fs['ci95'][0]:.2f} to {fs['ci95'][1]:.2f}; its lead over R0 "
                  f"{d_r0['ci95'][0]:.2f} to {d_r0['ci95'][1]:.2f}; over the NIFTY 50 {d_nx['ci95'][0]:.2f} to {d_nx['ci95'][1]:.2f} (not "
                  f"established). Daily t-statistic {fs['tstat_daily']:.2f}, monthly {fs['tstat_monthly']:.2f}: the strategy's days are "
                  f"negatively autocorrelated (variance ratio of 20-day sums {fs['variance_ratio_20d']:.2f}).", ""]
    L += ["## Validation of the v2 strategy", "", md_body(os.path.join(R4, "validation.md")), "",
          "## How to read the v2 numbers", "",
          "* All three seen windows were used to choose the rules, so none of them is out of sample any more; the "
          "worst-window objective is a selection statistic. The deflated Sharpe ratio is a probability, not a Sharpe: the "
          "chance that the true 2017-26 Sharpe beats what the best of all logged tries (every row of results_v4.tsv with a "
          "2017-26 Sharpe) would reach by luck. Below 0.95 it means the 2017-26 Sharpe is not significant once the search "
          "is charged for. Many of those rows re-evaluate existing candidates, which narrows their spread and lowers the "
          "hurdle, and the v1 trials on the same years are not charged, so the figure is if anything generous.",
          "* The deep hold-out (2011-2016) is the only test no rule was chosen on; it was scored once, after the final "
          "was fixed, on a set of strategies registered in `program.md` before the look.",
          "* The joint perturbation (every parameter moved at random by up to 20 % at once) and the walk-forward "
          "re-selection run on the same years the rules were chosen on: they measure how fragile the fit is, not how it "
          "will do on new data; the optimised worst-window Sharpe is the top of that in-sample range.",
          "* The Monte Carlo resamples the strategy's own realised 2017-26 returns, which were used to select it, with "
          "about 84 % of capital at the repo rate: its probability of loss holds almost by construction and describes "
          "the backtest, not the risk of trading it.",
          "* Stock returns are price returns (no dividends); cash earns the RBI repo rate.", "", "---", ""]
    return L


def main():
    final = load(os.path.join(R, "validation.json")); base = load(os.path.join(R, "baseline", "validation.json"))
    if final is None:
        sys.exit("run validate.py first")
    tsv = pd.read_csv(os.path.join(HERE, "results.tsv"), sep="\t")
    keeps = tsv[tsv.status == "keep"].reset_index(drop=True)
    m = final["metrics"]; b = base["metrics"] if base else None
    src = open(os.path.join(HERE, "v4", "v1_final.py")).read()
    doc = re.search(r'"""(.*?)"""', src, re.S).group(1).strip()
    L = v4_section()
    L += ["# The v1 record (RCG-RN-REV-001): RSI + EMA + VWAP on the Nifty 50 universe, 2020-2026" if L else
          "# RSI + EMA + VWAP on the Nifty 50 universe, 2020-2026: final report", "",
          f"Branch `autoresearch/oct9-nse`. {len(tsv)} experiments logged, {len(keeps)} kept "
          f"(including {(keeps.description.str.startswith('[harness]')).sum()} harness re-measurement of an incumbent). "
          f"Objective: in-sample Sharpe (2020-01-01..2023-12-31, net of 15 bps one-way costs, next-open execution). "
          f"The 2024-01-01..2026-10-09 window was never used by the keep rule (validation window, see below); the 2017-2019 "
          f"pre-sample hold-out was sealed until the end and scored once for the pre-registered incumbent lineage.", ""]
    lin = load(os.path.join(R, "lineage.json"))
    pre_f = lin[-1].get("sharpe_pre") if lin else None
    pre_b = lin[0].get("sharpe_pre") if lin else None
    pre_n = lin[-1].get("bench_sharpe_pre") if lin else None
    pv = lambda x: "-" if x is None else x
    L += ["## Headline", "", "| | Sharpe IS 2020-23 | Sharpe 2024-26 | Sharpe hold-out 2017-19 | Sharpe FULL 2020-26 | CAGR FULL | maxDD FULL | turnover/yr | exposure |",
          "|---|---|---|---|---|---|---|---|---|"]
    L.append(f"| **final strategy** | {m['sharpe_is']} | {m['sharpe_oos']} | {pv(pre_f)} | {m['sharpe_full']} | {m['cagr_full']} | {m['maxdd_full']} | {m['turnover_yr_full']} | {m['exposure_full']} |")
    if b:
        L.append(f"| baseline (first run) | {b['sharpe_is']} | {b['sharpe_oos']} | {pv(pre_b)} | {b['sharpe_full']} | {b['cagr_full']} | {b['maxdd_full']} | {b['turnover_yr_full']} | {b['exposure_full']} |")
    L.append(f"| NIFTY 50 price index | {m['bench_sharpe_is']} | {m['bench_sharpe_oos']} | {pv(pre_n)} | {m['bench_sharpe_full']} | {m['bench_cagr_full']} | - | - | 1.0 |")
    if lin and pre_f is not None:
        neg_oos = sum(o["sharpe_oos"] < 0 for o in lin); neg_pre = sum(o.get("sharpe_pre", 0) < 0 for o in lin)
        best = max(lin, key=lambda o: o.get("sharpe_pre", -9))
        L += ["", f"**Verdict.** The loop raised the in-sample Sharpe from {pv(b and b['sharpe_is'])} to {m['sharpe_is']}; the gain did not carry over. "
              f"On 2024-26 the final strategy scores {m['sharpe_oos']} (baseline {pv(b and b['sharpe_oos'])}, Nifty {m['bench_sharpe_oos']}); "
              f"{neg_oos} of {len(lin)} kept strategies are negative there and {neg_pre} of {len(lin)} on the sealed 2017-19 hold-out "
              f"(final {pre_f}, baseline {pv(pre_b)}, best {best['stage']} `{best['commit']}` at {best['sharpe_pre']}; buy-and-hold Nifty {pv(pre_n)}). "
              "The in-sample figure measures fit to 2020-2023, not expected return; see the lineage and diagnostics sections below."]
    yt = pd.DataFrame(final["yearly"])
    L += ["", f"Cash (RBI repo) returned {', '.join(f'{r.year}: {r.rf:.1%}' for r in yt.itertuples())}.", ""]
    L += ["![equity](equity.png)", "", "![rolling](rolling_sharpe.png)", "", "![yearly](yearly.png)", ""]
    L += ["## The strategy", "", "```", doc, "```", "", f"Parameters: `{json.dumps(final['params'])}`", ""]
    if lin:
        has_pre = "sharpe_pre" in lin[0]
        L += ["## Incumbent lineage on every window", "",
              "Each row is a strategy the loop kept (baseline, every merged round winner, final), re-scored on the final harness."
              + (" The sealed pre-sample column is the first and only look at 2017-2019 for these strategies (log: `reports/holdout_log.tsv`)." if has_pre else ""), "",
              "| stage | commit | Sharpe IS 2020-23 | IS 2020-21 | IS 2022-23 | Sharpe 2024-26 | Sharpe FULL 2020-26 |" + (" Sharpe pre-sample 2017-19 | Nifty 2017-19 |" if has_pre else ""),
              "|---|---|---|---|---|---|---|" + ("---|---|" if has_pre else "")]
        for o in lin:
            L.append(f"| {o['stage']} | {o['commit']} | {o['sharpe_is']} | {o['sharpe_is_h1']} | {o['sharpe_is_h2']} | {o['sharpe_oos']} | {o['sharpe_full']} |"
                     + (f" {o['sharpe_pre']} | {o['bench_sharpe_pre']} |" if has_pre else ""))
        L += ["", "![lineage](lineage.png)", ""]
    dpath = os.path.join(R, "diagnostics.md")
    if os.path.exists(dpath):
        dmd = open(dpath).read().split("## Every spell", 1)[0]
        dmd = dmd.split("\n", 1)[1].replace("\n## ", "\n### ")   # drop the file title, demote headings
        L += ["## Diagnostics: why it fails after 2023", "",
              "An invested spell is a maximal run of invested sessions (from a switch-on or re-entry to the switch-off, a stand-aside above RSI 80, or until the exits empty the book); excess is its return over cash. "
              "The selection edge compares the next-20-session return of the names a cohort bought with the average member "
              "on the same days (`python diagnose.py`; every spell is listed in `reports/diagnostics.md`).", "", dmd.strip(), ""]
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
          "* `sharpe_is` is the number the loop optimised; it is subject to selection bias across all logged trials. The deflated Sharpe ratio above adjusts for the number and dispersion of those trials, but treats them as independent draws; a sequential search that builds on its own best results on the same years is not that, so the DSR is not evidence of an edge.",
          "* `sharpe_oos` (2024-2026) was never used by the loop to choose rules or parameters, and since protocol v2 researchers cannot see it. The lead researcher did inspect its yearly values after round 1, which is why it is called a validation window here and why a second, sealed hold-out (2017-2019) was created before round 2.",
          "* The sealed pre-sample hold-out (2017-2019) uses its own point-in-time Nifty 50 including 11 names that left the index before 2020; it was scored once at the end for the pre-registered lineage and could not change the final choice.",
          "* The walk-forward row re-selects parameters each year from a perturbation grid using only prior years; it tests the parameter choice, not the rule structure (which was chosen in-sample).",
          "* Costs: 15 bps one-way (STT 10 + fees ~1 + slippage ~4). The cost table shows how quickly the edge decays if execution is worse.",
          "* Stock returns are price returns (no dividends), which understates long-only returns by roughly 1-1.5 %/yr versus a total-return benchmark; cash earns the repo rate.", ""]
    with open(os.path.join(R, "REPORT.md"), "w") as f:
        f.write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
