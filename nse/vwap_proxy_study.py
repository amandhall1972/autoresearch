"""
How well does the daily-bar VWAP proxy track a session VWAP built from intraday bars?
Uses FMP 1-hour bars (Jan-Mar 2025, five liquid names) saved under data/intraday_sample/.
Writes reports/vwap_proxy_study.md. Usage: python vwap_proxy_study.py
"""
import glob, os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    rows = []; lines = ["# VWAP proxy study (daily typical price vs hourly-bar session VWAP)", "",
                        "Sample: five liquid Nifty 50 names, 1-hour bars, 2025-01-01..2025-03-28 (the vendor's intraday history for NSE starts in 2023, so this cannot cover 2020-2026).", "",
                        "| name | days | median |sessionVWAP - HLC3| (bps) | 90th pct (bps) | median |rolling20 VWAP diff| (bps) | sign agreement close>VWAP20 |", "|---|---|---|---|---|---|"]
    for f in sorted(glob.glob(os.path.join(HERE, "data", "intraday_sample", "*_1h_2025Q1.csv"))):
        name = os.path.basename(f).split("_")[0]
        h = pd.read_csv(f, parse_dates=["date"])
        h["day"] = h["date"].dt.normalize()
        h["tp"] = (h.high + h.low + h.close) / 3
        g = h.groupby("day")
        sess = (g.apply(lambda x: (x.tp * x.volume).sum() / x.volume.sum())).rename("vwap_session")
        dv = g["volume"].sum().rename("vol_h")
        d = pd.read_csv(os.path.join(HERE, "data", "raw", name + ".csv"), parse_dates=["date"]).set_index("date")
        d = d.loc[sess.index.min():sess.index.max()]
        d["hlc3"] = (d.high + d.low + d.close) / 3
        j = d.join(sess, how="inner").join(dv)
        diff1 = ((j.vwap_session - j.hlc3) / j.hlc3 * 1e4).abs()
        # rolling 20-day VWAP from each construction (same daily volumes from the daily file)
        v20_proxy = (j.hlc3 * j.volume).rolling(20).sum() / j.volume.rolling(20).sum()
        v20_true = (j.vwap_session * j.volume).rolling(20).sum() / j.volume.rolling(20).sum()
        diff20 = ((v20_true - v20_proxy) / v20_proxy * 1e4).abs().dropna()
        agree = ((j.close > v20_proxy) == (j.close > v20_true)).loc[diff20.index].mean()
        rows.append(dict(name=name, days=len(j), med1=diff1.median(), p90=diff1.quantile(0.9), med20=diff20.median(), agree=agree))
        lines.append(f"| {name} | {len(j)} | {diff1.median():.1f} | {diff1.quantile(0.9):.1f} | {diff20.median():.1f} | {agree:.1%} |")
    r = pd.DataFrame(rows)
    lines += ["", f"Across the sample the daily typical price sits within a median {r.med1.median():.0f} bps of the hourly-bar session VWAP, the 20-day rolling VWAPs built from the two differ by a median {r.med20.median():.0f} bps, and the close-above-VWAP20 signal agrees on {r.agree.mean():.1%} of days.",
              "", "Interpretation: for the rolling/anchored VWAP levels used by this strategy (multi-day windows), the daily-bar proxy is a faithful substitute; it is not a substitute for an intraday session-VWAP strategy, which this project does not attempt."]
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    open(os.path.join(HERE, "reports", "vwap_proxy_study.md"), "w").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
