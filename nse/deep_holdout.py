"""
Deep hold-out (protocol v4, program.md): April 2011 to May 2016 on the point-in-time Nifty 50 of 2008-2016.

Protocol v4 researched on every window seen so far (2017-04..2026-10). Once those windows were spent, this
window became the only historical test left that no strategy in this repository had ever been run on.
No research decision may use it: the loop never runs this script. The lead runs it once, at the end, on the
set of strategies pre-registered in program.md (protocol v4). Every look is appended to
reports/deep_holdout_log.tsv (UTC time, git commit, sha256 of the strategy file, results).

The data are built by build_deep_holdout.py (data/raw_deep, data/universe/nifty50_weights_2008_2016.csv);
prepare.load_data(deep=True) applies the documented corrections (DEEP_* constants). Known limits: the vendor
has no bars for three members delisted by merger (Cairn India for the whole window, Ranbaxy to 2014-03,
Sterlite Industries to 2012-10) and none for HDFC from 2013-12-12, so 46 to 49 of the 50 members are
tradable on any day.

Usage:
  python deep_holdout.py --data-report                # data checks only: runs no strategy, logs nothing
  python deep_holdout.py --strategy FILE --label NAME  # one logged look (lead only, pre-registered set only)
"""
import argparse, datetime, hashlib, importlib.util, os, subprocess, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P

LOG = os.path.join(HERE, "reports", "deep_holdout_log.tsv")
YEARS = (2011, 2012, 2013, 2014, 2015, 2016)
COLS = (["utc", "git_commit", "strategy_sha256", "label", "cost_bps", "sharpe_deep", "cagr_deep", "maxdd_deep",
         "vol_deep", "turnover_yr_deep", "exposure_deep", "n_names_avg_deep", "pct_days_invested_deep",
         "bench_sharpe_deep", "bench_cagr_deep"] + [f"sharpe_{y}" for y in YEARS] + ["lookahead_ok", "days_deep"])


def load_strategy(path):
    spec = importlib.util.spec_from_file_location("deep_strategy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score(strategy_mod, md, cost_bps=P.COST_BPS_PER_SIDE, check=True) -> dict:
    W = strategy_mod.compute_target_weights(md)
    res = P.run_backtest(W, md, cost_bps=cost_bps)
    out = P.summarize(res, md, P.DEEP_START, P.DEEP_END, "_deep")
    r = res["ret"].loc[P.DEEP_START:P.DEEP_END]
    for y in YEARS:
        ry = r.loc[str(y)]
        out[f"sharpe_{y}"] = round(P.sharpe(ry, md.rf.loc[ry.index]), 4) if len(ry) > 20 else None
    out["lookahead_ok"] = True
    if check:
        la = P.check_no_lookahead(strategy_mod.compute_target_weights, md, n_trunc=20, n_corrupt=10,
                                  lo=P.DEEP_START, hi=P.DEEP_END)
        out["lookahead_ok"] = bool(la["ok"])
    out["_res"] = res
    return out


def strategy_sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def evaluate_and_log(path, label, md=None, cost_bps=P.COST_BPS_PER_SIDE, commit=None, returns_out=None) -> dict:
    """Score one strategy file on the deep hold-out and append the look to reports/deep_holdout_log.tsv."""
    if commit is None:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True,
                                    text=True).stdout.strip()
        except Exception:
            commit = ""
    md = md if md is not None else P.load_data(deep=True)
    ev = score(load_strategy(path), md, cost_bps=cost_bps)
    res = ev.pop("_res")
    if returns_out:
        a, b = P.DEEP_START, P.DEEP_END
        pd.DataFrame({"strategy": res["ret"].loc[a:b], "niftybees": md.close[P.ETF_TICKER].pct_change().loc[a:b],
                      "nifty": md.index_close["NIFTY50"].pct_change().loc[a:b], "cash": md.rf.loc[a:b],
                      "exposure": res["exposure"].loc[a:b]}).to_csv(returns_out)
    row = {"utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "git_commit": commit,
           "strategy_sha256": strategy_sha(path), "label": label, "cost_bps": cost_bps, **ev}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, "a") as f:
        if new:
            f.write("\t".join(COLS) + "\n")
        f.write("\t".join(str(row.get(c, "")) for c in COLS) + "\n")
    return row


def data_report(md=None) -> dict:
    """Checks on the deep data that run no strategy: calendar, membership coverage, rebuilt index sessions and
    how well the rebuilt BANK NIFTY tracks the vendor's on the sessions where both exist."""
    md = md if md is not None else P.load_data(deep=True)
    raws = {t: P._read_raw(t, deep=True) for t in P.INDEX_TICKERS}
    cal = md.calendar
    win = cal[(cal >= pd.Timestamp(P.DEEP_START)) & (cal <= pd.Timestamp(P.DEEP_END))]
    stocks = [t for t in md.tickers if t != P.ETF_TICKER]
    members = md.member.loc[win, stocks]
    tradable_members = (members & md.tradable.loc[win, stocks]).sum(axis=1)
    out = {"sessions": len(cal), "sessions_scored": len(win), "first": str(cal[0].date()), "last": str(cal[-1].date()),
           "members_with_bars_min": int(members.sum(axis=1).min()), "members_with_bars_max": int(members.sum(axis=1).max()),
           "tradable_members_median": float(tradable_members.median())}
    for ix in P.INDEX_TICKERS:
        vendor = raws[ix]["close"].reindex(cal)
        out[f"{ix}_rebuilt_sessions"] = int(vendor.loc[win].isna().sum())
    # BANK NIFTY proxy tracking: rebuild every session from DEEP_BANKS and compare daily returns
    w = P._index_weight_rows(deep=True)
    px = pd.concat([P._read_raw(t, deep=True)["close"].rename(t) for t in P.DEEP_BANKS], axis=1, sort=True).reindex(cal).ffill()
    rows = w.reindex(cal.union(w.index)).ffill().reindex(cal)[list(P.DEEP_BANKS)].fillna(0.0)
    r = px.pct_change(fill_method=None)
    wd = rows.shift(1) * (px.shift(1) / px.shift(1))       # weights of the previous session (drift ignored)
    proxy = (wd * r).sum(axis=1) / wd.where(r.notna()).sum(axis=1)
    actual = raws["BANKNIFTY"]["close"].reindex(cal).pct_change(fill_method=None)
    both = proxy.notna() & actual.notna() & (cal >= pd.Timestamp("2010-01-01"))
    err = (proxy - actual)[both]
    out["banknifty_proxy_days"] = int(both.sum())
    out["banknifty_proxy_rms_bps"] = round(float(np.sqrt((err ** 2).mean()) * 1e4), 1)
    out["banknifty_proxy_corr"] = round(float(np.corrcoef(proxy[both], actual[both])[0, 1]), 4)
    nb = md.close[P.ETF_TICKER].pct_change().loc[win]; nx = md.index_close["NIFTY50"].pct_change().loc[win]
    out["niftybees_vs_nifty_corr"] = round(float(np.corrcoef(nb.fillna(0), nx.fillna(0))[0, 1]), 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy")
    ap.add_argument("--label", default="final")
    ap.add_argument("--cost", type=float, default=P.COST_BPS_PER_SIDE)
    ap.add_argument("--data-report", action="store_true")
    args = ap.parse_args()
    if args.data_report:
        for k, v in data_report().items():
            print(f"{k + ':':<32}{v}")
        return
    if not args.strategy:
        ap.error("--strategy is required (or --data-report)")
    row = evaluate_and_log(args.strategy, args.label, cost_bps=args.cost)
    print(f"--- DEEP HOLD-OUT {P.DEEP_START}..{P.DEEP_END} (logged to reports/deep_holdout_log.tsv)")
    for c in COLS[3:]:
        print(f"{c + ':':<24}{row.get(c)}")


if __name__ == "__main__":
    main()
