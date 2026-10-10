"""
Dev-only helper: convert FMP MCP tool-result dumps (JSON arrays saved to disk by the
agent harness) into clean per-ticker CSVs under data/raw/.

Canonical ticker names follow the Nifty 50 constituent dataset (e.g. BAJAJAUTO, M&M).
Files are named with '&' replaced by '_' (M&M -> M_M.csv).

Fetch M&M as "M%26M.NS": an unencoded "M&M.NS" is cut at the ampersand and returns the US ticker "M".

Usage: python ingest_tool_results.py [--out data/raw_presample] <dir-with-mcp-FMP-chart-*.txt> [more dirs...]
(default output: data/raw; the pre-sample hold-out names that left the Nifty 50 before 2020 go to
data/raw_presample so that they never enter the research loop's data; the 2007-2016 bars of the deep
hold-out go to data/raw_deep, which only deep_holdout.py reads.)
"""
import glob, json, os, sys
import pandas as pd

FMP_TO_CANON = {
    "^NSEI": "NIFTY50",
    "^NSEBANK": "BANKNIFTY",
    "BAJAJ-AUTO.NS": "BAJAJAUTO",
    "INDUSTOWER.NS": "INFRATEL",   # Bharti Infratel renamed Indus Towers (Dec 2020); same listed entity
    "TMPV.NS": "TATAMOTORS",       # Tata Motors renamed Tata Motors Passenger Vehicles after the Oct-2025 CV demerger
    "SAMMAANCAP.NS": "IBULHSGFIN", # Indiabulls Housing Finance renamed Sammaan Capital (2024); same listed entity
    "UNITDSPR.NS": "MCDOWELL-N",   # United Spirits traded as MCDOWELL-N until its 2024 symbol change; same entity
}

def canon(sym: str) -> str:
    if sym in FMP_TO_CANON:
        return FMP_TO_CANON[sym]
    return sym[:-3] if sym.endswith(".NS") else sym

def fname(ticker: str) -> str:
    return ticker.replace("&", "_") + ".csv"

def main(src_dirs, out_dir: str = os.path.join(os.path.dirname(__file__), "data", "raw")):
    os.makedirs(out_dir, exist_ok=True)
    frames = {}
    paths = sorted(p for d in src_dirs for p in glob.glob(os.path.join(d, "mcp-FMP-chart-*.txt")))
    for path in paths:
        raw = open(path).read()
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            print("skip (not json):", path); continue
        if not isinstance(rows, list) or not rows or "symbol" not in rows[0] or "open" not in rows[0]:
            print("skip (not eod-full):", path); continue
        sym = rows[0]["symbol"]
        df = pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume"]]
        frames.setdefault(canon(sym), []).append(df)
    report = []
    for tk, dfs in frames.items():
        df = pd.concat(dfs).drop_duplicates("date").sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        for c in ["open", "high", "low", "close"]:
            df[c] = df[c].astype(float)
        df["volume"] = df["volume"].astype("int64")
        df.to_csv(os.path.join(out_dir, fname(tk)), index=False)
        r = df["close"].pct_change()
        report.append(dict(ticker=tk, rows=len(df), first=df["date"].iloc[0], last=df["date"].iloc[-1],
                           max_ret=round(r.max(), 3), min_ret=round(r.min(), 3),
                           zero_vol=int((df["volume"] == 0).sum()),
                           ohlc_bad=int(((df["low"] > df[["open", "close"]].min(axis=1)) |
                                         (df["high"] < df[["open", "close"]].max(axis=1))).sum())))
    rep = pd.DataFrame(report).sort_values("ticker")
    pd.set_option("display.width", 200); pd.set_option("display.max_rows", 200)
    print(rep.to_string(index=False))
    print("tickers:", len(rep))

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--out":
        main(a[2:], out_dir=a[1])
    else:
        main(a)
