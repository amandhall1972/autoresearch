"""
Fetcher: download the series from Financial Modeling Prep with your own API key (FMP_API_KEY env var) and
write data/raw/*.csv (and data/raw_presample/*.csv) in the harness format.

    FMP_API_KEY=... python fetch_fmp.py                 # rebuild data/raw from 2016-06-01 to today
    FMP_API_KEY=... python fetch_fmp.py --update        # append only bars newer than each file's last date
    FMP_API_KEY=... python fetch_fmp.py --presample     # rebuild the hold-out names in data/raw_presample

The repository's experiments used the same FMP data fetched through the FMP MCP connector and converted with
ingest_tool_results.py; this script reproduces or extends it (for example before running signals.py).
"""
import argparse, datetime, os, sys, time
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
UNI = os.path.join(HERE, "data", "universe")
BASE = "https://financialmodelingprep.com/stable/historical-price-eod/full"

CANON_TO_FMP = {"NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "BAJAJAUTO": "BAJAJ-AUTO.NS",
                "INFRATEL": "INDUSTOWER.NS", "M&M": "M&M.NS", "IBULHSGFIN": "SAMMAANCAP.NS"}
CONTINUATIONS = {"TATAMOTORS": ["TATAMOTORS.NS", "TMPV.NS"]}   # renamed after the 2025 demerger
PRESAMPLE_ONLY = ["ACC", "AMBUJACEM", "AUROPHARMA", "BANKBARODA", "BHEL", "BOSCHLTD", "HINDPETRO", "IBULHSGFIN",
                  "IDEA", "LUPIN", "TATAPOWER"]


def fetch(symbol, start, end, key):
    import requests
    r = requests.get(BASE, params={"symbol": symbol, "from": start, "to": end, "apikey": key}, timeout=60)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume"]]


def research_tickers():
    w = pd.read_csv(os.path.join(UNI, "nifty50_weights_2019_2025.csv"))
    active = [t for t in w.columns[1:] if (w[t] > 0).any()]
    changes = pd.read_csv(os.path.join(UNI, "nifty50_changes_post_dataset.csv"))["ticker"].unique()
    return sorted(set(active) | set(changes) | {"NIFTY50", "BANKNIFTY", "NIFTYBEES"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-06-01")
    ap.add_argument("--end", default=datetime.date.today().isoformat())
    ap.add_argument("--update", action="store_true", help="append bars after each file's last date")
    ap.add_argument("--presample", action="store_true", help="fetch the 11 hold-out-only names into data/raw_presample")
    args = ap.parse_args()
    key = os.environ.get("FMP_API_KEY")
    if not key:
        sys.exit("set FMP_API_KEY")
    out = os.path.join(HERE, "data", "raw_presample" if args.presample else "raw")
    os.makedirs(out, exist_ok=True)
    tickers = PRESAMPLE_ONLY if args.presample else research_tickers()
    end = "2020-03-31" if args.presample else args.end
    for t in tickers:
        path = os.path.join(out, t.replace("&", "_") + ".csv")
        start = args.start
        old = None
        if args.update and os.path.exists(path):
            old = pd.read_csv(path)
            start = (pd.Timestamp(old["date"].max()) + pd.Timedelta(days=1)).date().isoformat()
            if start > end:
                print(t, "up to date"); continue
        syms = CONTINUATIONS.get(t) or [CANON_TO_FMP.get(t, t + ".NS")]
        parts = []
        for s in syms:
            parts.append(fetch(s, start, end, key)); time.sleep(0.3)
        df = pd.concat(parts)
        if old is not None:
            df = pd.concat([old, df])
        df = df.drop_duplicates("date", keep="last").sort_values("date")
        df.to_csv(path, index=False)
        print(t, len(df), df["date"].iloc[-1] if len(df) else "")


if __name__ == "__main__":
    main()
