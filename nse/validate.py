"""
Final validation of strategy.py (run after the research loop):
  * cost sensitivity (0/10/15/25/40 bps)
  * yearly breakdown vs NIFTY 50 / NIFTYBEES
  * stationary block-bootstrap confidence intervals for the Sharpe ratio
  * probabilistic and deflated Sharpe ratio (Bailey & Lopez de Prado, 2014) using the number of
    trials recorded in results.tsv
  * one-at-a-time parameter sensitivity (+-10%, +-20%)
  * walk-forward re-selection of parameters (expanding window, yearly) over the perturbation grid
  * Monte Carlo (block bootstrap) distribution of terminal wealth and max drawdown
  * charts under reports/
Usage: python validate.py [--boot 2000] [--out reports] [--strategy path]   (default: the frozen v1 final
v4/v1_final.py, which reports/validation.* describe; protocol v4 uses validate4.py)
"""
import argparse, copy, json, math, os, sys, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P

EULER = 0.5772156649


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    from scipy.stats import norm
    return float(norm.ppf(p))


def block_bootstrap(x: np.ndarray, n_boot: int, block: int, rng) -> np.ndarray:
    """Stationary block bootstrap (Politis & Romano): random block lengths ~ Geometric(1/block)."""
    T = len(x); out = np.empty((n_boot, T))
    for b in range(n_boot):
        idx = np.empty(T, dtype=int); pos = 0
        while pos < T:
            start = rng.integers(0, T); L = rng.geometric(1.0 / block)
            L = min(L, T - pos)
            idx[pos:pos + L] = (start + np.arange(L)) % T
            pos += L
        out[b] = x[idx]
    return out


def sharpe_ann(ex: np.ndarray) -> float:
    s = ex.std(ddof=1)
    return float(ex.mean() / s * math.sqrt(P.TRADING_DAYS)) if s > 0 else 0.0


def psr(ex: np.ndarray, sr0_period=0.0) -> float:
    """Probabilistic Sharpe ratio: P(true SR > sr0) given skew/kurtosis of per-period excess returns."""
    T = len(ex); sr = ex.mean() / ex.std(ddof=1)
    from scipy.stats import skew, kurtosis
    g3 = skew(ex); g4 = kurtosis(ex, fisher=False)
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    return norm_cdf((sr - sr0_period) * math.sqrt(T - 1) / denom)


def deflated_sharpe(ex: np.ndarray, trial_sharpes_ann) -> dict:
    """DSR = PSR(SR0) where SR0 accounts for the number and dispersion of trials."""
    trials = np.array([s for s in trial_sharpes_ann if np.isfinite(s)]) / math.sqrt(P.TRADING_DAYS)
    N = len(trials)
    if N < 2:
        return {"n_trials": N, "sr0_ann": 0.0, "dsr": psr(ex, 0.0)}
    v = trials.var(ddof=1)
    sr0 = math.sqrt(v) * ((1 - EULER) * norm_ppf(1 - 1.0 / N) + EULER * norm_ppf(1 - 1.0 / (N * math.e)))
    return {"n_trials": int(N), "sr0_ann": round(sr0 * math.sqrt(P.TRADING_DAYS), 6), "dsr": round(psr(ex, sr0), 6)}


def yearly_table(ret: pd.Series, rf: pd.Series, bench: pd.Series, bees: pd.Series, expo: pd.Series) -> pd.DataFrame:
    rows = []
    for y, r in ret.groupby(ret.index.year):
        rfy = rf.loc[r.index]; b = bench.loc[r.index]; e = bees.loc[r.index]
        rows.append(dict(year=y, strat_ret=round((1 + r).prod() - 1, 6), strat_sharpe=round(P.sharpe(r, rfy), 4),
                         strat_maxdd=round(P.max_drawdown(r), 6), exposure=round(float(expo.loc[r.index].mean()), 4),
                         nifty_ret=round((1 + b).prod() - 1, 6), nifty_sharpe=round(P.sharpe(b, rfy), 4),
                         bees_ret=round((1 + e).prod() - 1, 6), rf=round((1 + rfy).prod() - 1, 6)))
    return pd.DataFrame(rows)


def perturbations(params: dict):
    """One-at-a-time +-10% / +-20% perturbations of numeric parameters (ints stay ints, min 2). A band just below 1
    (0.9 < v < 1, e.g. a 0.985 x VWAP stop) is perturbed through its distance from 1 (0.985 -> 0.982 / 0.988), since
    1.2 x 0.985 would be a meaningless stop above the price."""
    out = []
    for k, v in params.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        for f in (0.8, 0.9, 1.1, 1.2):
            nv = 1.0 - (1.0 - v) * f if (isinstance(v, float) and 0.9 < v < 1.0) else v * f
            if isinstance(v, int):
                nv = max(2, int(round(nv)))
                if nv == v:
                    continue
            p = copy.deepcopy(params); p[k] = nv
            out.append((k, f, p))
    return out


def run_with_params(strategy, params, md, cost=P.COST_BPS_PER_SIDE):
    old = copy.deepcopy(strategy.PARAMS)
    strategy.PARAMS.clear(); strategy.PARAMS.update(params)
    try:
        W = strategy.compute_target_weights(md)
        res = P.run_backtest(W, md, cost_bps=cost)
    finally:
        strategy.PARAMS.clear(); strategy.PARAMS.update(old)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--out", default=os.path.join(HERE, "reports"))
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--strategy", default=os.path.join(HERE, "v4", "v1_final.py"),
                    help="strategy file (default: the frozen v1 final)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time(); rng = np.random.default_rng(42)
    if args.strategy:
        import importlib.util
        spec = importlib.util.spec_from_file_location("strategy_under_validation", args.strategy)
        strategy = importlib.util.module_from_spec(spec); spec.loader.exec_module(strategy)
    else:
        import strategy
    md = P.load_data()
    W = strategy.compute_target_weights(md)
    la = P.check_no_lookahead(strategy.compute_target_weights, md)
    report = {"lookahead": la, "params": copy.deepcopy(strategy.PARAMS)}

    # ---- cost sensitivity
    cs = []
    for c in (0, 10, 15, 25, 40):
        ev = P.evaluate(W, md, cost_bps=c); ev.pop("_res")
        cs.append(dict(cost_bps=c, sharpe_is=ev["sharpe_is"], sharpe_oos=ev["sharpe_oos"], sharpe_full=ev["sharpe_full"],
                       cagr_full=ev["cagr_full"], maxdd_full=ev["maxdd_full"]))
    report["cost_sensitivity"] = cs
    base = P.evaluate(W, md); res = base.pop("_res"); report["metrics"] = base

    # ---- yearly
    ret = res["ret"].loc[P.START:P.END]; rf = md.rf.loc[P.START:P.END]
    bench = md.index_close["NIFTY50"].pct_change().loc[P.START:P.END].fillna(0.0)
    bees = md.close[P.ETF_TICKER].pct_change().loc[P.START:P.END].fillna(0.0)
    yt = yearly_table(ret, rf, bench, bees, res["exposure"].loc[P.START:P.END])
    report["yearly"] = yt.to_dict(orient="records")

    # ---- bootstrap CIs, PSR, DSR
    trials = []
    tsv = os.path.join(HERE, "results.tsv")
    if os.path.exists(tsv):
        t = pd.read_csv(tsv, sep="\t")
        trials = [float(x) for x in t["sharpe_is"].values if float(x) != 0.0]
    boot = {}
    for label, (a, b) in {"is": (P.START, P.IS_END), "oos": (P.OOS_START, P.END), "full": (P.START, P.END)}.items():
        ex = (res["ret"].loc[a:b] - md.rf.loc[a:b]).values
        bs = block_bootstrap(ex, args.boot, 20, rng)
        srs = np.array([sharpe_ann(x) for x in bs])
        d = {"sharpe": round(sharpe_ann(ex), 4), "ci95": [round(float(np.percentile(srs, 2.5)), 4), round(float(np.percentile(srs, 97.5)), 4)],
             "p_sharpe_gt_0": round(float((srs > 0).mean()), 4), "psr_vs_0": round(psr(ex, 0.0), 4)}
        if label == "is":
            d["deflated"] = deflated_sharpe(ex, trials)
        boot[label] = d
    report["bootstrap"] = boot

    # ---- parameter sensitivity + walk-forward over the perturbation grid
    grid = [("base", 1.0, copy.deepcopy(strategy.PARAMS))] + perturbations(strategy.PARAMS)
    sens = []; rets = {}
    skipped = []
    for k, f, p in grid:
        try:
            r = run_with_params(strategy, p, md)["ret"]
        except ValueError as e:                      # perturbation violates a harness constraint (e.g. gross > 1)
            skipped.append(f"{k}x{f}: {e}"); continue
        rets[(k, f)] = r
        sens.append(dict(param=k, factor=f, value=p.get(k), sharpe_is=round(P.sharpe(r.loc[P.START:P.IS_END], md.rf.loc[P.START:P.IS_END]), 4),
                         sharpe_oos=round(P.sharpe(r.loc[P.OOS_START:P.END], md.rf.loc[P.OOS_START:P.END]), 4),
                         sharpe_full=round(P.sharpe(r.loc[P.START:P.END], md.rf.loc[P.START:P.END]), 4)))
    sdf = pd.DataFrame(sens)
    base_is = float(sdf.loc[sdf.param == "base", "sharpe_is"].iloc[0])
    report["param_sensitivity"] = sens
    report["param_sensitivity_skipped"] = skipped
    report["param_sensitivity_summary"] = {
        "n_perturbations": int(len(sdf) - 1),
        "median_sharpe_is": round(float(sdf.sharpe_is.iloc[1:].median()), 4),
        "min_sharpe_is": round(float(sdf.sharpe_is.iloc[1:].min()), 4),
        "frac_within_0.2_of_base_is": round(float((abs(sdf.sharpe_is.iloc[1:] - base_is) <= 0.2).mean()), 3),
        "median_sharpe_oos": round(float(sdf.sharpe_oos.iloc[1:].median()), 4),
        "base_rank_is": int((sdf.sharpe_is > base_is).sum() + 1),
    }
    # walk-forward: for each test year, pick the grid point with the best Sharpe on all prior years (from 2020)
    wf = []; wf_ret = []
    years = sorted(set(ret.index.year))
    for y in years[1:]:
        tr_a, tr_b = P.START, f"{y - 1}-12-31"; te_a, te_b = f"{y}-01-01", f"{y}-12-31"
        best = max(rets.items(), key=lambda kv: P.sharpe(kv[1].loc[tr_a:tr_b], md.rf.loc[tr_a:tr_b]))
        r_te = best[1].loc[te_a:te_b]
        wf.append(dict(test_year=y, chosen=f"{best[0][0]}x{best[0][1]}", sharpe_train=round(P.sharpe(best[1].loc[tr_a:tr_b], md.rf.loc[tr_a:tr_b]), 3),
                       sharpe_test=round(P.sharpe(r_te, md.rf.loc[te_a:te_b]), 3), ret_test=round((1 + r_te).prod() - 1, 4)))
        wf_ret.append(r_te)
    wf_all = pd.concat(wf_ret)
    report["walk_forward"] = {"years": wf, "sharpe_concat": round(P.sharpe(wf_all, md.rf.loc[wf_all.index]), 4),
                             "cagr_concat": round(P.cagr(wf_all), 4), "maxdd_concat": round(P.max_drawdown(wf_all), 4)}

    # ---- Monte Carlo on full-period net returns
    x = ret.values; bs = block_bootstrap(x, min(args.boot, 2000), 20, rng)
    term = (1 + bs).prod(axis=1); mdd = np.array([P.max_drawdown(pd.Series(b)) for b in bs[:500]])
    report["monte_carlo"] = {"terminal_wealth_p5": round(float(np.percentile(term, 5)), 3), "terminal_wealth_p50": round(float(np.percentile(term, 50)), 3),
                             "terminal_wealth_p95": round(float(np.percentile(term, 95)), 3), "p_loss": round(float((term < 1).mean()), 4),
                             "maxdd_p50": round(float(np.percentile(mdd, 50)), 4), "maxdd_p95_worst": round(float(np.percentile(mdd, 5)), 4)}

    # ---- regime split (Nifty above / below its 200-day EMA at the previous close)
    nifty = md.index_close["NIFTY50"]; above = (nifty > nifty.ewm(span=200, adjust=False).mean()).shift(1).reindex(ret.index).fillna(False).astype(bool)
    report["regime"] = {"days_above": int(above.sum()), "sharpe_above": round(P.sharpe(ret[above], rf[above]), 3),
                        "days_below": int((~above).sum()), "sharpe_below": round(P.sharpe(ret[~above], rf[~above]), 3),
                        "nifty_sharpe_above": round(P.sharpe(bench[above], rf[above]), 3), "nifty_sharpe_below": round(P.sharpe(bench[~above], rf[~above]), 3)}
    report["runtime_s"] = round(time.time() - t0, 1)

    with open(os.path.join(args.out, "validation.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    (1 + ret).cumprod().rename("strategy").to_frame().join((1 + bench).cumprod().rename("nifty50")).join((1 + bees).cumprod().rename("niftybees")).to_csv(os.path.join(args.out, "equity_curves.csv"))

    # ---- markdown
    m = base
    lines = ["# Validation report", "", f"Params: `{json.dumps(strategy.PARAMS)}`", "", f"Lookahead check: {la}", "",
             "## Headline", "", "| period | Sharpe | CAGR | vol | maxDD | Sortino | turnover/yr | exposure | Nifty Sharpe | t-stat |", "|---|---|---|---|---|---|---|---|---|---|"]
    for lab in ("is", "oos", "full"):
        lines.append(f"| {lab.upper()} | {m['sharpe_'+lab]} | {m['cagr_'+lab]} | {m['vol_'+lab]} | {m['maxdd_'+lab]} | {m['sortino_'+lab]} | {m['turnover_yr_'+lab]} | {m['exposure_'+lab]} | {m['bench_sharpe_'+lab]} | {m['tstat_'+lab]} |")
    lines += ["", f"In-sample robustness (protocol v2 gates): Sharpe 2020-21 {m['sharpe_is_h1']}, 2022-23 {m['sharpe_is_h2']}, "
                  f"with the 5 best days removed {m['sharpe_is_drop5']}, before costs {m['sharpe_is_gross']}."]
    lines += ["", "## Cost sensitivity", "", "| cost bps | Sharpe IS | Sharpe OOS | Sharpe FULL | CAGR FULL | maxDD FULL |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['cost_bps']} | {r['sharpe_is']} | {r['sharpe_oos']} | {r['sharpe_full']} | {r['cagr_full']} | {r['maxdd_full']} |" for r in cs]
    lines += ["", "## Yearly", "", "| year | strat ret | strat Sharpe | strat maxDD | exposure | Nifty ret | Nifty Sharpe | NIFTYBEES ret | cash |", "|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['year']} | {r['strat_ret']} | {r['strat_sharpe']} | {r['strat_maxdd']} | {r['exposure']} | {r['nifty_ret']} | {r['nifty_sharpe']} | {r['bees_ret']} | {r['rf']} |" for r in report["yearly"]]
    lines += ["", "## Bootstrap / PSR / DSR", ""]
    for lab, d in boot.items():
        extra = f", deflated Sharpe (N={d['deflated']['n_trials']} trials, SR0={d['deflated']['sr0_ann']}): DSR={d['deflated']['dsr']}" if "deflated" in d else ""
        lines.append(f"* {lab.upper()}: Sharpe {d['sharpe']}, 95% block-bootstrap CI {d['ci95']}, P(Sharpe>0)={d['p_sharpe_gt_0']}, PSR(0)={d['psr_vs_0']}{extra}")
    s = report["param_sensitivity_summary"]
    lines += ["", "## Parameter sensitivity (one-at-a-time +-10/20%)", "", f"* {s['n_perturbations']} perturbations; median Sharpe IS {s['median_sharpe_is']} (base {base_is}), min {s['min_sharpe_is']}, {s['frac_within_0.2_of_base_is']:.0%} within 0.2 of base; base ranks #{s['base_rank_is']}; median Sharpe OOS {s['median_sharpe_oos']}", "",
              "| param | factor | value | Sharpe IS | Sharpe OOS | Sharpe FULL |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['param']} | {r['factor']} | {r['value']} | {r['sharpe_is']} | {r['sharpe_oos']} | {r['sharpe_full']} |" for r in sens]
    w = report["walk_forward"]
    lines += ["", "## Walk-forward (expanding window, yearly re-selection over the perturbation grid)", "", f"* concatenated test years: Sharpe {w['sharpe_concat']}, CAGR {w['cagr_concat']}, maxDD {w['maxdd_concat']}", "",
              "| test year | chosen | Sharpe train | Sharpe test | return test |", "|---|---|---|---|---|"]
    lines += [f"| {r['test_year']} | {r['chosen']} | {r['sharpe_train']} | {r['sharpe_test']} | {r['ret_test']} |" for r in w["years"]]
    mc = report["monte_carlo"]; rg = report["regime"]
    lines += ["", "## Monte Carlo (stationary block bootstrap of daily net returns, full period)", "", f"* terminal wealth p5/p50/p95: {mc['terminal_wealth_p5']}/{mc['terminal_wealth_p50']}/{mc['terminal_wealth_p95']}, P(loss)={mc['p_loss']}, maxDD median {mc['maxdd_p50']}, 5th-pct worst {mc['maxdd_p95_worst']}", "",
              "## Regime split (Nifty vs its 200-day EMA)", "", f"* above: {rg['days_above']} days, Sharpe {rg['sharpe_above']} (Nifty {rg['nifty_sharpe_above']}); below: {rg['days_below']} days, Sharpe {rg['sharpe_below']} (Nifty {rg['nifty_sharpe_below']})", ""]
    with open(os.path.join(args.out, "validation.md"), "w") as f:
        f.write("\n".join(lines))

    if not args.no_charts:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = pd.read_csv(os.path.join(args.out, "equity_curves.csv"), index_col=0, parse_dates=True)
        fig, ax = plt.subplots(3, 1, figsize=(11, 11), sharex=True, gridspec_kw={"height_ratios": [3, 1.2, 1]})
        for col, lab in [("strategy", "RSI+EMA+VWAP strategy (net)"), ("niftybees", "NIFTYBEES (buy & hold)"), ("nifty50", "NIFTY 50 price index")]:
            ax[0].plot(eq.index, eq[col], label=lab, linewidth=1.6 if col == "strategy" else 1.0)
        ax[0].axvline(pd.Timestamp(P.OOS_START), color="grey", linestyle="--", linewidth=1); ax[0].text(pd.Timestamp(P.OOS_START), ax[0].get_ylim()[1] * 0.95, "  out-of-sample", color="grey")
        ax[0].set_yscale("log"); ax[0].set_title("Growth of 1 (log scale)"); ax[0].legend(loc="upper left"); ax[0].grid(alpha=0.3)
        dd = eq["strategy"] / eq["strategy"].cummax() - 1; ddb = eq["niftybees"] / eq["niftybees"].cummax() - 1
        ax[1].fill_between(dd.index, dd, 0, alpha=0.5, label="strategy drawdown"); ax[1].plot(ddb.index, ddb, linewidth=0.8, label="NIFTYBEES drawdown"); ax[1].legend(loc="lower left"); ax[1].grid(alpha=0.3)
        ax[2].plot(res["exposure"].loc[P.START:P.END].index, res["exposure"].loc[P.START:P.END], linewidth=0.8); ax[2].set_ylim(0, 1.05); ax[2].set_title("Gross exposure"); ax[2].grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(os.path.join(args.out, "equity.png"), dpi=130); plt.close(fig)
        fig, ax = plt.subplots(figsize=(10, 4))
        ex = (ret - rf); roll = ex.rolling(252).apply(lambda z: z.mean() / z.std(ddof=1) * math.sqrt(252) if z.std(ddof=1) > 0 else 0.0, raw=False)
        bx = (bench - rf); rollb = bx.rolling(252).apply(lambda z: z.mean() / z.std(ddof=1) * math.sqrt(252) if z.std(ddof=1) > 0 else 0.0, raw=False)
        ax.plot(roll.index, roll, label="strategy"); ax.plot(rollb.index, rollb, label="NIFTY 50"); ax.axhline(0, color="k", linewidth=0.6); ax.set_title("Rolling 1-year Sharpe"); ax.legend(); ax.grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(os.path.join(args.out, "rolling_sharpe.png"), dpi=130); plt.close(fig)
        fig, ax = plt.subplots(figsize=(10, 4)); x = np.arange(len(yt)); wdt = 0.27
        ax.bar(x - wdt, yt.strat_ret, wdt, label="strategy"); ax.bar(x, yt.bees_ret, wdt, label="NIFTYBEES"); ax.bar(x + wdt, yt.rf, wdt, label="cash (repo)")
        ax.set_xticks(x); ax.set_xticklabels(yt.year); ax.set_title("Calendar-year returns"); ax.legend(); ax.grid(alpha=0.3, axis="y")
        fig.tight_layout(); fig.savefig(os.path.join(args.out, "yearly.png"), dpi=130); plt.close(fig)
    print("\n".join(lines))
    print(f"\nwritten to {args.out} in {report['runtime_s']}s")


if __name__ == "__main__":
    main()
