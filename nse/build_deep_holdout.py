"""
Build the deep hold-out data (protocol v4): the point-in-time Nifty 50 of 2008-2016.

Inputs
  --src      per-ticker CSVs written by ingest_tool_results.py from FMP EOD bars of 2007-01..2016-06
             (every name that was a Nifty 50 member at any time in 2010-2016, plus NIFTY 50, BANK NIFTY and
             NIFTYBEES; fetch "M&M" as "M%26M.NS", United Spirits as "UNITDSPR.NS", Bharti Infratel as
             "INDUSTOWER.NS", Sesa Goa / Sesa Sterlite as "VEDL.NS")
  --weights  the first rows (2008-01-31 onward) of weights.csv from the "Historical Nifty 50 Constituent
             Weights (20Y)" dataset (Bera, 2025, CC BY-NC-SA 4.0; hf://datasets/AMP4010/
             Historical_Nifty_50_Constituent_Weights_20Y), the same source as data/universe/*.csv

Outputs
  data/raw_deep/*.csv                         bars up to DEEP_END (2016-05-31): nothing overlaps the research data
  data/universe/nifty50_weights_2008_2016.csv membership/weight rows 2008-01-31..2016-05-31

Corrections are NOT baked into the CSVs: prepare.py applies them when loading (DEEP_* constants), so the
files stay the vendor's bars. The membership file merges SESAGOA into VEDL (one listed entity, renamed in
2013) and keeps the three members the vendor no longer carries (CAIRN, RANBAXY, STER: delisted by merger)
so that their absence is visible; prepare.py drops names without bars.

Usage: python build_deep_holdout.py --src DIR --weights FILE
"""
import argparse, glob, os, shutil, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P

OUT_RAW = os.path.join(P.DATA_DIR, "raw_deep")
OUT_UNI = os.path.join(P.DATA_DIR, "universe", "nifty50_weights_2008_2016.csv")
RENAMES = {"SESAGOA": "VEDL"}          # same listed entity (Sesa Goa -> Sesa Sterlite 2013 -> Vedanta 2015)


def build_universe(weights_path: str) -> pd.DataFrame:
    w = pd.read_csv(weights_path, parse_dates=["DATE"]).set_index("DATE").sort_index()
    w = w.loc[:P.DEEP_END]
    for old, new in RENAMES.items():
        if old in w.columns:
            w[new] = w[[old, new]].max(axis=1) if new in w.columns else w[old]
            w = w.drop(columns=old)
    w = w.loc[:, (w > 0).any()]                      # names that were members at some point in 2008-2016
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--weights", required=True)
    args = ap.parse_args()
    if os.path.exists(OUT_RAW):
        shutil.rmtree(OUT_RAW)
    os.makedirs(OUT_RAW)
    n = 0
    for p in sorted(glob.glob(os.path.join(args.src, "*.csv"))):
        df = pd.read_csv(p)
        df = df[df["date"] <= P.DEEP_END]
        df.to_csv(os.path.join(OUT_RAW, os.path.basename(p)), index=False)
        n += 1
    w = build_universe(args.weights)
    w.index.name = "DATE"
    w.to_csv(OUT_UNI, date_format="%Y-%m-%d")
    have = {os.path.basename(p)[:-4].replace("_", "&") for p in glob.glob(os.path.join(OUT_RAW, "*.csv"))}
    members = [t for t in w.columns if (w.loc[P.DEEP_START:, t] > 0).any()]
    missing = sorted(set(members) - have)
    print(f"wrote {n} series to {OUT_RAW}; universe {w.shape[0]} rows x {w.shape[1]} names -> {OUT_UNI}")
    print(f"members during {P.DEEP_START}..{P.DEEP_END}: {len(members)}; without vendor bars: {missing}")


if __name__ == "__main__":
    main()
