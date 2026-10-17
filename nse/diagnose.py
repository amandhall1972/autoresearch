"""
Post-research diagnostics of the final strategy (lead/report use: reads the 2024-2026 window, so the research
loop never runs it). Writes reports/diagnostics.json and reports/diagnostics.md:

  * every invested spell (formation day, kind = fresh / whipsaw / stand-aside re-entry, length, names bought,
    net excess return of the spell, the NIFTY 50 move over the same days), for 2020-2023 and 2024-2026;
  * per-period summary: spells, hit rate, mean spell excess, share of days invested, costs;
  * the signal's own decay: average next-20-day return of names that pass the entry filters on switch-on days
    versus all members, by year.

Usage: python diagnose.py
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P


def spells_table(res, md, W):
    ret = res["ret"].loc[P.START:P.END]; rf = md.rf.loc[ret.index]
    expo = res["exposure"].loc[ret.index]
    stocks = W.drop(columns=[P.ETF_TICKER]).loc[ret.index]
    inv = (expo > 1e-9).values
    nifty = md.index_close["NIFTY50"].loc[ret.index]
    rows, start = [], None
    for i, f in enumerate(np.append(inv, False)):
        if f and start is None:
            start = i
        if not f and start is not None:
            a, b = start, i
            d0, d1 = ret.index[a], ret.index[b - 1]
            ex = float((1 + ret.iloc[a:b]).prod() - (1 + rf.iloc[a:b]).prod())
            prev = nifty.index.get_loc(d0)
            n_move = float(nifty.iloc[b - 1] / nifty.iloc[max(prev - 1, 0)] - 1)
            names = int((stocks.iloc[a] > 0).sum())
            rows.append(dict(start=str(d0.date()), end=str(d1.date()), days=b - a, names_at_start=names,
                             gross_start=round(float(expo.iloc[a]), 3), excess_return=round(ex, 4),
                             nifty_move=round(n_move, 4), period="2020-23" if d0 <= pd.Timestamp(P.IS_END) else "2024-26"))
            start = None
    return pd.DataFrame(rows)


def main():
    import argparse, importlib.util
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=os.path.join(HERE, "v4", "v1_final.py"),
                    help="strategy file (default: the frozen v1 final, which reports/diagnostics.* describe)")
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("strategy_diag", args.strategy)
    strategy = importlib.util.module_from_spec(spec); spec.loader.exec_module(strategy)
    md = P.load_data()
    W = strategy.compute_target_weights(md).reindex(index=md.calendar, columns=md.tickers).fillna(0.0)
    res = P.run_backtest(W, md)
    sp = spells_table(res, md, W)
    out = {"spells": sp.to_dict(orient="records"), "summary": {}}
    for per, g in sp.groupby("period"):
        out["summary"][per] = dict(spells=int(len(g)), hit_rate=round(float((g.excess_return > 0).mean()), 3),
                                   mean_spell_excess=round(float(g.excess_return.mean()), 4),
                                   median_spell_excess=round(float(g.excess_return.median()), 4),
                                   sum_spell_excess=round(float(g.excess_return.sum()), 4),
                                   mean_days=round(float(g.days.mean()), 1),
                                   spells_with_nifty_up=int((g.nifty_move > 0).sum()),
                                   mean_excess_when_nifty_up=round(float(g[g.nifty_move > 0].excess_return.mean()), 4) if (g.nifty_move > 0).any() else None,
                                   mean_excess_when_nifty_down=round(float(g[g.nifty_move <= 0].excess_return.mean()), 4) if (g.nifty_move <= 0).any() else None)
    for per, (a, b) in {"2020-23": (P.START, P.IS_END), "2024-26": (P.OOS_START, P.END)}.items():
        tc = res["ret_gross"].loc[a:b] - res["ret"].loc[a:b]
        out["summary"].setdefault(per, {})["cost_drag_per_yr"] = round(float(tc.sum() / (len(tc) / P.TRADING_DAYS)), 4)
    # signal decay: names bought at cohort formation vs all members, next 20 sessions
    c = md.close
    fwd = c.shift(-20) / c - 1.0
    stock = md.member.copy(); stock[P.ETF_TICKER] = False
    bought = (W.drop(columns=[P.ETF_TICKER]) > 0) & ~(W.drop(columns=[P.ETF_TICKER]).shift(1).fillna(0) > 0)
    rows = []
    for y in range(2020, 2027):
        days = [d for d in W.index if d.year == y and bought.loc[d].any()]
        if not days:
            continue
        sel = np.mean([fwd.loc[d, bought.columns[bought.loc[d]]].mean() for d in days])
        allm = np.mean([fwd.loc[d, stock.columns[stock.loc[d]]].mean() for d in days])
        rows.append(dict(year=y, formation_days=len(days), fwd20_bought=round(float(sel), 4), fwd20_all_members=round(float(allm), 4),
                         edge=round(float(sel - allm), 4)))
    out["selection_edge_by_year"] = rows
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "reports", "diagnostics.json"), "w"), indent=2)
    L = ["# Diagnostics of the final strategy", "", "## Invested spells by period", "",
         "| period | spells | hit rate | mean spell excess | median | sum | mean days | spells with NIFTY up | mean excess, NIFTY up | mean excess, NIFTY down | cost drag/yr |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for per, s in out["summary"].items():
        L.append(f"| {per} | {s.get('spells')} | {s.get('hit_rate')} | {s.get('mean_spell_excess')} | {s.get('median_spell_excess')} | {s.get('sum_spell_excess')} | {s.get('mean_days')} | {s.get('spells_with_nifty_up')} | {s.get('mean_excess_when_nifty_up')} | {s.get('mean_excess_when_nifty_down')} | {s.get('cost_drag_per_yr')} |")
    L += ["", "## Selection edge: next-20-session return of names bought at formation vs all members", "",
          "| year | formation days | names bought | all members | edge |", "|---|---|---|---|---|"]
    L += [f"| {r['year']} | {r['formation_days']} | {r['fwd20_bought']} | {r['fwd20_all_members']} | {r['edge']} |" for r in rows]
    L += ["", "## Every spell", "", "| period | start | end | days | names | gross | excess | NIFTY move |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['period']} | {r['start']} | {r['end']} | {r['days']} | {r['names_at_start']} | {r['gross_start']} | {r['excess_return']} | {r['nifty_move']} |" for r in out["spells"]]
    open(os.path.join(HERE, "reports", "diagnostics.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L[:20]))


if __name__ == "__main__":
    main()
