"""
Protocol-v4 validation of a strategy file on the 2016-2026 data (every window seen; objective = worst window):
  * dense lookahead check over 2017-2026
  * every window (2017-19, 2020-23, 2024-26), the 2017-26 span and 2020-26, against NIFTYBEES, the NIFTY 50 and the
    plain EMA timer R0
  * cost sensitivity (0/10/15/25/40 bps)
  * calendar years 2017-2026 against the NIFTY 50, NIFTYBEES and cash
  * stationary block-bootstrap Sharpe intervals and probabilistic Sharpe per window
  * deflated Sharpe of the span Sharpe against every protocol-v4 trial in results_v4.tsv
  * one-at-a-time parameter sensitivity (+-10 %, +-20 %)
  * walk-forward: each test year 2019-2026 uses the perturbation-grid point with the best Sharpe on 2017-04..the year
    before (expanding window)
  * Monte Carlo of the span and a regime split (NIFTY above / below its EMA200)
  * the protocol's robustness checks: plateau4 against the incumbent the strategy was merged over, the joint +-20 %
    perturbation (60 draws, numpy default_rng(20261010)) and each window with its own 5 / 10 best days removed,
    for the strategy and for R0
Writes reports/v4/validation.json, validation.md and equity_curves.csv (strategy, R0, the v1 final, NIFTYBEES,
NIFTY 50, exposure).

Usage: python validate4.py --strategy FILE [--boot 2000] [--out reports/v4]
"""
import argparse, copy, json, math, os, sys, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P
import validate as V
from run4 import load_strategy

R0_PATH = os.path.join(HERE, "v4", "r0_ema_timer.py")
V1_PATH = os.path.join(HERE, "v4", "v1_final.py")          # the v1 final, for the equity-curve comparison
WINDOWS = [(lab, a, b) for lab, a, b in P.V4_WINDOWS] + [("span", P.V4_START, P.END), ("full", P.START, P.END)]


def window_sharpes(ret, rf):
    return {lab: round(P.sharpe(ret.loc[a:b], rf.loc[a:b]), 6) for lab, a, b in WINDOWS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "v4"))
    ap.add_argument("--incumbent", default="v4/r0_ema_timer.py",
                    help="the incumbent the strategy was merged over (plateau4 reference)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time(); rng = np.random.default_rng(42)
    strategy = load_strategy(args.strategy)
    md = P.load_data(presample=True, end=P.END)
    W = strategy.compute_target_weights(md)
    la = P.check_no_lookahead(strategy.compute_target_weights, md, lo=P.V4_START, hi=P.OOS_START)
    rep = {"strategy": os.path.relpath(args.strategy, HERE), "lookahead": la, "params": copy.deepcopy(strategy.PARAMS)}

    ev = P.evaluate_v4(W, md); res = ev.pop("_res"); rep["metrics"] = ev
    rf = md.rf
    ret = res["ret"]
    bees = md.close[P.ETF_TICKER].pct_change().fillna(0.0)
    nifty = md.index_close["NIFTY50"].pct_change().fillna(0.0)
    r0 = load_strategy(R0_PATH)
    r0_res = P.run_backtest(r0.compute_target_weights(md), md)
    rep["benchmarks"] = {"strategy": window_sharpes(ret, rf), "niftybees": window_sharpes(bees, rf),
                         "nifty50": window_sharpes(nifty, rf), "r0_ema_timer": window_sharpes(r0_res["ret"], rf)}

    # ---- costs
    cs = []
    for c in (0, 10, 15, 25, 40):
        e = P.evaluate_v4(W, md, cost_bps=c); e.pop("_res")
        cs.append({"cost_bps": c, "objective": e["objective"], **{f"sharpe_{k}": e[f"sharpe_{k}"] for k in ("pre", "is", "oos", "span", "full")},
                   "cagr_span": e["cagr_span"], "maxdd_span": e["maxdd_span"]})
    rep["cost_sensitivity"] = cs

    # ---- calendar years
    span = ret.loc[P.V4_START:P.END]
    yt = V.yearly_table(span, rf.loc[span.index], nifty.loc[span.index], bees.loc[span.index], res["exposure"].loc[span.index])
    rep["yearly"] = yt.to_dict(orient="records")

    # ---- bootstrap / PSR / DSR
    trials = []
    tsv = os.path.join(HERE, "results_v4.tsv")
    if os.path.exists(tsv):
        t = pd.read_csv(tsv, sep="\t")
        trials = [float(x) for x in pd.to_numeric(t["sharpe_span"], errors="coerce").dropna() if float(x) != 0.0]
    boot = {}
    for lab, a, b in WINDOWS:
        ex = (ret.loc[a:b] - rf.loc[a:b]).values
        bs = V.block_bootstrap(ex, args.boot, 20, rng)
        srs = np.array([V.sharpe_ann(x) for x in bs])
        d = {"sharpe": round(V.sharpe_ann(ex), 6), "ci95": [round(float(np.percentile(srs, 2.5)), 6), round(float(np.percentile(srs, 97.5)), 6)],
             "p_sharpe_gt_0": round(float((srs > 0).mean()), 6), "psr_vs_0": round(V.psr(ex, 0.0), 6),
             "tstat": round(float(ex.mean() / ex.std(ddof=1) * math.sqrt(len(ex))), 2)}
        if lab == "span":
            d["deflated"] = V.deflated_sharpe(ex, trials)
        boot[lab] = d
    rep["bootstrap"] = boot

    # ---- parameter sensitivity and walk-forward over the perturbation grid
    grid = [("base", 1.0, copy.deepcopy(strategy.PARAMS))] + V.perturbations(
        {k: v for k, v in strategy.PARAMS.items() if not k.startswith("use_")})
    for _, _, p in grid:
        for k, v in strategy.PARAMS.items():
            p.setdefault(k, v)
    sens, rets, skipped = [], {}, []
    for k, f, p in grid:
        try:
            r = V.run_with_params(strategy, p, md)["ret"]
        except ValueError as e:
            skipped.append(f"{k}x{f}: {e}"); continue
        rets[(k, f)] = r
        w = window_sharpes(r, rf)
        sens.append({"param": k, "factor": f, "value": p.get(k), "objective": round(min(w["pre"], w["is"], w["oos"]), 6), **w})
    sdf = pd.DataFrame(sens)
    base_obj = float(sdf.loc[sdf.param == "base", "objective"].iloc[0])
    pert = sdf.iloc[1:]
    rep["param_sensitivity"] = sens
    rep["param_sensitivity_skipped"] = skipped
    rep["param_sensitivity_summary"] = {
        "n_perturbations": int(len(pert)), "median_objective": round(float(pert.objective.median()), 4) if len(pert) else None,
        "min_objective": round(float(pert.objective.min()), 4) if len(pert) else None,
        "frac_objective_positive": round(float((pert.objective > 0).mean()), 3) if len(pert) else None,
        "frac_within_0.1_of_base": round(float((abs(pert.objective - base_obj) <= 0.1).mean()), 3) if len(pert) else None,
        "base_rank": int((sdf.objective > base_obj).sum() + 1)}
    wf, wf_ret = [], []
    for y in range(2019, 2027):
        tr_a, tr_b = P.V4_START, f"{y - 1}-12-31"; te_a, te_b = f"{y}-01-01", f"{y}-12-31"
        best = max(rets.items(), key=lambda kv: P.sharpe(kv[1].loc[tr_a:tr_b], rf.loc[tr_a:tr_b]))
        r_te = best[1].loc[te_a:min(te_b, P.END)]
        wf.append({"test_year": y, "chosen": f"{best[0][0]}x{best[0][1]}",
                   "sharpe_train": round(P.sharpe(best[1].loc[tr_a:tr_b], rf.loc[tr_a:tr_b]), 3),
                   "sharpe_test": round(P.sharpe(r_te, rf.loc[r_te.index]), 3), "ret_test": round(float((1 + r_te).prod() - 1), 4)})
        wf_ret.append(r_te)
    wf_all = pd.concat(wf_ret)
    rep["walk_forward"] = {"years": wf, "sharpe_concat": round(P.sharpe(wf_all, rf.loc[wf_all.index]), 6),
                           "sharpe_concat_2024_26": round(P.sharpe(wf_all.loc[P.OOS_START:], rf.loc[wf_all.loc[P.OOS_START:].index]), 6),
                           "cagr_concat": round(P.cagr(wf_all), 6), "maxdd_concat": round(P.max_drawdown(wf_all), 6)}

    # ---- Monte Carlo and regime split on the span
    x = span.values; bs = V.block_bootstrap(x, min(args.boot, 2000), 20, rng)
    term = (1 + bs).prod(axis=1); mdd = np.array([P.max_drawdown(pd.Series(b)) for b in bs[:500]])
    rep["monte_carlo"] = {"terminal_wealth_p5": round(float(np.percentile(term, 5)), 6), "terminal_wealth_p50": round(float(np.percentile(term, 50)), 6),
                          "terminal_wealth_p95": round(float(np.percentile(term, 95)), 6), "p_loss": round(float((term < 1).mean()), 6),
                          "maxdd_p50": round(float(np.percentile(mdd, 50)), 6), "maxdd_p95_worst": round(float(np.percentile(mdd, 5)), 6)}
    nx = md.index_close["NIFTY50"]
    above = (nx > nx.ewm(span=200, adjust=False).mean()).shift(1).reindex(span.index).fillna(False).astype(bool)
    rfs = rf.loc[span.index]; ns = nifty.loc[span.index]
    rep["regime"] = {"days_above": int(above.sum()), "sharpe_above": round(P.sharpe(span[above], rfs[above]), 6),
                     "days_below": int((~above).sum()), "sharpe_below": round(P.sharpe(span[~above], rfs[~above]), 6),
                     "nifty_sharpe_above": round(P.sharpe(ns[above], rfs[above]), 6), "nifty_sharpe_below": round(P.sharpe(ns[~above], rfs[~above]), 6)}
    # ---- protocol robustness checks
    import plateau4
    inc = os.path.join(HERE, args.incumbent) if not os.path.isabs(args.incumbent) else args.incumbent
    pb, pi, prow = plateau4.plateau(args.strategy, inc)
    rep["plateau"] = {"incumbent": os.path.relpath(inc, HERE), "objective": pb, "incumbent_objective": pi,
                      "rows": prow, "ok": all(r["ok"] for r in prow), "min": min((r["objective"] for r in prow), default=None)}
    jr = np.random.default_rng(20261010)
    num = {k: v for k, v in strategy.PARAMS.items()
           if not k.startswith("use_") and isinstance(v, (int, float)) and not isinstance(v, bool)}
    jo = []
    for _ in range(60):
        ov = {}
        for k, v in num.items():
            f = jr.uniform(0.8, 1.2)
            nv = 1.0 - (1.0 - v) * f if (isinstance(v, float) and 0.9 < v < 1.0) else v * f
            ov[k] = max(1, int(round(nv))) if isinstance(v, int) else nv
        p2 = copy.deepcopy(strategy.PARAMS); p2.update(ov)
        r2 = V.run_with_params(strategy, p2, md)["ret"]
        jo.append(P.v4_objective(r2.loc[P.V4_START:P.END], rf.loc[P.V4_START:P.END]))
    jo = np.array(jo)
    r0_obj = rep["benchmarks"]["r0_ema_timer"]
    rep["joint_perturbation"] = {"draws": 60, "seed": 20261010, "mean": round(float(jo.mean()), 6),
                                 "median": round(float(np.median(jo)), 6), "p10": round(float(np.percentile(jo, 10)), 6),
                                 "min": round(float(jo.min()), 6), "max": round(float(jo.max()), 6),
                                 "share_above_r0": round(float((jo > min(r0_obj["pre"], r0_obj["is"], r0_obj["oos"])).mean()), 6),
                                 "values": [round(float(x), 6) for x in jo]}

    def own_best_removed(ret, k):
        out = {}
        for lab, a, b in P.V4_WINDOWS:
            r = ret.loc[a:b]; ex = r - rf.loc[r.index]
            keep = ex.drop(ex.nlargest(k).index).index
            out[lab] = round(P.sharpe(r.loc[keep], rf.loc[keep]), 6)
        out["worst"] = min(out[w] for w, _, _ in P.V4_WINDOWS)
        return out
    rep["event_days"] = {name: {str(k): own_best_removed(r, k) for k in (0, 5, 10)}
                         for name, r in (("strategy", ret), ("r0_ema_timer", r0_res["ret"]), ("niftybees", bees))}
    spn = (ret.loc[P.V4_START:P.END] - rf.loc[P.V4_START:P.END])
    rep["event_days"]["concentration"] = {"top10_share_of_span_excess": round(float(spn.nlargest(10).sum() / spn.sum()), 6),
                                          "top10_days": [str(d.date()) for d in spn.nlargest(10).index]}
    rep["runtime_s"] = round(time.time() - t0, 1)

    with open(os.path.join(args.out, "validation.json"), "w") as f:
        json.dump(rep, f, indent=2, default=str)
    v1_res = P.run_backtest(load_strategy(V1_PATH).compute_target_weights(md), md)
    pd.DataFrame({"strategy": (1 + span).cumprod(), "r0_ema_timer": (1 + r0_res["ret"].loc[span.index]).cumprod(),
                  "v1_final": (1 + v1_res["ret"].loc[span.index]).cumprod(),
                  "niftybees": (1 + bees.loc[span.index]).cumprod(), "nifty50": (1 + nifty.loc[span.index]).cumprod(),
                  "exposure": res["exposure"].loc[span.index]}).to_csv(os.path.join(args.out, "equity_curves.csv"))

    m = ev
    L = ["# Protocol-v4 validation", "", f"Strategy: `{rep['strategy']}`; params `{json.dumps(rep['params'])}`", "",
         f"Lookahead check: ok={la['ok']}, max diff {la['max_abs_diff']}, {la['n_checks_with_holdings']}/{la['n_checks']} informative", "",
         "## Windows (Sharpe of daily excess over the repo rate, net of 15 bps)", "",
         "| | 2017-19 | 2020-23 | 2024-26 | 2017-26 | 2020-26 |", "|---|---|---|---|---|---|"]
    for name, d in rep["benchmarks"].items():
        L.append(f"| {name} | {d['pre']} | {d['is']} | {d['oos']} | {d['span']} | {d['full']} |")
    L += ["", f"Objective (worst window) {m['objective']}; span CAGR {m['cagr_span']}, max DD {m['maxdd_span']}, vol {m['vol_span']}, "
              f"turnover {m['turnover_yr_span']}x/yr, exposure {m['exposure_span']}, names {m['n_names_avg_span']}; span Sharpe without "
              f"the 10 best days {m['sharpe_span_drop10']}; {m['years_positive']}/{m['years']} years with a positive sum of daily "
              f"excess returns (the consistency gate's count; compounded, "
              f"{sum(1 for r in rep['yearly'] if r['strat_ret'] - r['rf'] > 0.0025)} beat cash, "
              f"{sum(1 for r in rep['yearly'] if abs(r['strat_ret'] - r['rf']) <= 0.0025)} were level with it).",
          "", "## Costs", "", "| bps | objective | 2017-19 | 2020-23 | 2024-26 | 2017-26 | 2020-26 | CAGR 2017-26 | max DD |", "|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['cost_bps']} | {r['objective']} | {r['sharpe_pre']} | {r['sharpe_is']} | {r['sharpe_oos']} | {r['sharpe_span']} | {r['sharpe_full']} | {r['cagr_span']} | {r['maxdd_span']} |" for r in cs]
    L += ["", "## Calendar years", "", "| year | return | Sharpe | max DD | exposure | NIFTY ret | NIFTY Sharpe | NIFTYBEES ret | cash |", "|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['year']} | {r['strat_ret']} | {r['strat_sharpe']} | {r['strat_maxdd']} | {r['exposure']} | {r['nifty_ret']} | {r['nifty_sharpe']} | {r['bees_ret']} | {r['rf']} |" for r in rep["yearly"]]
    L += ["", "## Sampling error", ""]
    for lab, d in boot.items():
        extra = f"; deflated Sharpe over N={d['deflated']['n_trials']} trials (SR0 {d['deflated']['sr0_ann']}): DSR {d['deflated']['dsr']}" if "deflated" in d else ""
        L.append(f"* {lab}: Sharpe {d['sharpe']} (t {d['tstat']}), 95 % block-bootstrap CI {d['ci95']}, share of resamples with Sharpe > 0 {d['p_sharpe_gt_0']} (not the probability that the true Sharpe is positive), PSR(0) {d['psr_vs_0']}{extra}")
    s = rep["param_sensitivity_summary"]
    L += ["", "## Parameter sensitivity (one at a time, +-10 / 20 %)", "",
          f"* {s['n_perturbations']} perturbations: objective median {s['median_objective']} (base {base_obj}), min {s['min_objective']}, "
          f"{s['frac_objective_positive']} positive, {s['frac_within_0.1_of_base']} within 0.1 of base; base ranks #{s['base_rank']}", "",
          "| param | factor | value | objective | 2017-19 | 2020-23 | 2024-26 | 2017-26 |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['param']} | {r['factor']} | {r['value']} | {r['objective']} | {r['pre']} | {r['is']} | {r['oos']} | {r['span']} |" for r in sens]
    w = rep["walk_forward"]
    L += ["", "## Walk-forward (expanding window from 2017-04, yearly re-selection over the perturbation grid)", "",
          f"* concatenated 2019-2026: Sharpe {w['sharpe_concat']} (2024-26: {w['sharpe_concat_2024_26']}), CAGR {w['cagr_concat']}, max DD {w['maxdd_concat']}", "",
          "| test year | chosen | Sharpe train | Sharpe test | return test |", "|---|---|---|---|---|"]
    L += [f"| {r['test_year']} | {r['chosen']} | {r['sharpe_train']} | {r['sharpe_test']} | {r['ret_test']} |" for r in w["years"]]
    mc, rg = rep["monte_carlo"], rep["regime"]
    L += ["", "## Monte Carlo and regimes", "",
          f"* terminal wealth over 2017-26 p5/p50/p95 {mc['terminal_wealth_p5']}/{mc['terminal_wealth_p50']}/{mc['terminal_wealth_p95']}, P(loss) {mc['p_loss']}, "
          f"max DD median {mc['maxdd_p50']}, 5th-percentile worst {mc['maxdd_p95_worst']}",
          f"* NIFTY above its EMA200: {rg['days_above']} days, Sharpe {rg['sharpe_above']} (NIFTY {rg['nifty_sharpe_above']}); "
          f"below: {rg['days_below']} days, Sharpe {rg['sharpe_below']} (NIFTY {rg['nifty_sharpe_below']})", ""]
    pl, jp, evd = rep["plateau"], rep["joint_perturbation"], rep["event_days"]
    L += ["## Protocol robustness checks", "",
          f"* plateau4 against `{pl['incumbent']}`: ok={pl['ok']}, minimum neighbour objective {pl['min']} "
          f"(strategy {pl['objective']}, incumbent {pl['incumbent_objective']})",
          f"* joint +-20 % perturbation of every parameter (60 draws): mean {jp['mean']}, median {jp['median']}, p10 {jp['p10']}, "
          f"min {jp['min']}; {jp['share_above_r0']:.0%} of draws above R0's objective",
          "* each window with its own best days removed (worst window):"]
    for name in ("strategy", "r0_ema_timer", "niftybees"):
        d = evd[name]
        L.append(f"  * {name}: none {d['0']['worst']}, 5 days {d['5']['worst']} ({d['5']['pre']} / {d['5']['is']} / {d['5']['oos']}), "
                 f"10 days {d['10']['worst']}")
    L.append(f"* the 10 best days carry {evd['concentration']['top10_share_of_span_excess']:.0%} of the 2017-26 excess return")
    L.append("")
    with open(os.path.join(args.out, "validation.md"), "w") as f:
        f.write(P.md_round("\n".join(L)))
    print("\n".join(L))
    print(f"\nwritten to {args.out} in {rep['runtime_s']} s")


if __name__ == "__main__":
    main()
