"""
Live signal generator for strategy.py: the target portfolio for the NEXT session's open, computed from
data through the latest available close, exactly as the backtest would trade it (same harness rules).

    python fetch_fmp.py --update                       # bring data/raw up to the latest close (needs FMP_API_KEY)
    python signals.py --capital 1000000                # target weights and rupee amounts
    python signals.py --capital 1000000 --holdings my_holdings.csv   # + orders vs current holdings (ticker,shares)

Workflow: after the NSE close, update the data, run this, and place the orders as market-on-open (or in the
first minutes) of the next session. Uninvested cash should sit in a liquid fund / overnight instrument.
The point-in-time universe beyond the last entry in data/universe/ must be kept current by hand
(nifty50_changes_post_dataset.csv) when NSE announces index changes.
"""
import argparse, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prepare as P


def next_session_targets(md, strategy_mod) -> pd.DataFrame:
    """Target weights for the open after the last bar in md, after the harness rules (members only,
    long-only, stale names carried), plus the previous session's target for comparison."""
    W = strategy_mod.compute_target_weights(md).reindex(index=md.calendar, columns=md.tickers).fillna(0.0)
    Wh, _ = P.apply_harness_rules(W, md)
    last, prev = Wh.iloc[-1], Wh.iloc[-2]
    df = pd.DataFrame({"weight": last, "prev_weight": prev, "last_close": md.close.iloc[-1]})
    df = df[(df.weight > 0) | (df.prev_weight > 0)].sort_values("weight", ascending=False)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--holdings", help="CSV with columns ticker,shares (current positions)")
    ap.add_argument("--end", default=None, help="evaluate as of this date (default: latest data)")
    args = ap.parse_args()
    import strategy
    raw_last = max(pd.read_csv(os.path.join(P.DATA_DIR, "raw", f), usecols=["date"])["date"].max()
                   for f in os.listdir(os.path.join(P.DATA_DIR, "raw")) if f.endswith(".csv"))
    md = P.load_data(end=args.end or raw_last)
    asof = md.calendar[-1].date()
    t = next_session_targets(md, strategy)
    t["target_value"] = (t.weight * args.capital).round(0)
    t["target_shares"] = np.floor(t.target_value / t.last_close).astype(int)
    print(f"Signals as of the close of {asof} -> trade at the next session's open")
    print(f"gross target {t.weight.sum():.1%} in {(t.weight > 0).sum()} names; cash {1 - t.weight.sum():.1%}")
    print(t.to_string(float_format=lambda x: f"{x:,.4f}"))
    if args.holdings:
        h = pd.read_csv(args.holdings).set_index("ticker")["shares"]
        names = sorted(set(h.index) | set(t.index))
        cur = h.reindex(names).fillna(0).astype(int)
        tgt = t["target_shares"].reindex(names).fillna(0).astype(int)
        orders = (tgt - cur).rename("order_shares")
        orders = orders[orders != 0]
        print("\nOrders for the next open (positive = buy):")
        print(orders.to_string() if len(orders) else "  none")


if __name__ == "__main__":
    main()
