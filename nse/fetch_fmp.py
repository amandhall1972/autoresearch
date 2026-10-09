"""
Reproduction fetcher: download the same series from Financial Modeling Prep with your own API key
(FMP_API_KEY env var) and write data/raw/*.csv in the harness format.

    FMP_API_KEY=... python fetch_fmp.py

Not used in this repository's experiments (the data was fetched through the FMP MCP connector and
converted with ingest_tool_results.py); provided so the dataset can be rebuilt or extended.
"""
import os, sys, time
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "raw")
BASE = "https://financialmodelingprep.com/stable/historical-price-eod/full"

CANON_TO_FMP = {"NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "BAJAJAUTO": "BAJAJ-AUTO.NS",
                "INFRATEL": "INDUSTOWER.NS", "M&M": "M%26M.NS"}
CONTINUATIONS = {"TATAMOTORS": ["TATAMOTORS.NS", "TMPV.NS"]}   # renamed after the 2025 demerger


def fetch(symbol, start="2019-06-01", end="2026-10-09", key=None):
    r = requests.get(BASE, params={"symbol": symbol, "from": start, "to": end, "apikey": key}, timeout=60)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume"]]
    return df


def main():
    key = os.environ.get("FMP_API_KEY")
    if not key:
        sys.exit("set FMP_API_KEY")
    os.makedirs(OUT, exist_ok=True)
    universe = pd.read_csv(os.path.join(HERE, "data", "universe", "nifty50_weights_2019_2025.csv")).columns[1:]
    changes = pd.read_csv(os.path.join(HERE, "data", "universe", "nifty50_changes_post_dataset.csv"))["ticker"].unique()
    active = [t for t in universe if (pd.read_csv(os.path.join(HERE, "data", "universe", "nifty50_weights_2019_2025.csv"))[t] > 0).any()]
    tickers = sorted(set(active) | set(changes) | {"NIFTY50", "BANKNIFTY", "NIFTYBEES"})
    for t in tickers:
        syms = CONTINUATIONS.get(t) or [CANON_TO_FMP.get(t, t + ".NS")]
        parts = []
        for s in syms:
            parts.append(fetch(s, key=key)); time.sleep(0.3)
        df = pd.concat(parts).drop_duplicates("date").sort_values("date")
        df.to_csv(os.path.join(OUT, t.replace("&", "_") + ".csv"), index=False)
        print(t, len(df))


if __name__ == "__main__":
    main()
