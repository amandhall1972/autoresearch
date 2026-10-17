"""
Pullback strategy with fewer, larger slots (protocol v4.2, round 4, CAPITAL-EFFICIENCY lens; best attempt, NOT a keep).

Identical to incumbent 1 (v4/inc1_pullback.py) except for one sizing change: the book has 10 slots of 1/10 instead of
12 slots of 1/12, so each dip that is bought carries 10 % of the book instead of 8.3 %. Every weight is decided with
data up to the close of day t; the harness trades it at the open of t+1. Capital not in a trade stays in cash and
earns the repo rate (credited by the harness).

RULES (unchanged from incumbent 1 except rule 3's slot count)
1. Trigger (EMA + VWAP). Trades open only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad among the leaders: of the members in their own uptrend (close above their own
        EMA(100)), more than breadth_cut (60 %) close below their rolling vwap_n (10)-day VWAP.
2. Name filter (EMA + RSI). On such a day a member is eligible when it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max = 20.
3. Selection and size (VWAP). Free slots (slots = 10; one slot = 1/10 of the book; incumbent 1 used 12) are filled
   with the eligible names that sit deepest below their 10-day VWAP. A name keeps the weight it was bought with until
   it is sold (no daily re-scaling of open positions).
4. Exit (VWAP + time). A held name is sold as soon as it closes back above its 10-day VWAP (the dip has been
   bought back), or after max_hold (20) sessions if that never happens. Names leaving the index are sold by the
   harness.

WHAT CHANGED VS INCUMBENT 1 AND WHY
* slots 12 -> 10 (the lens's "fewer, larger slots"). Trigger days fill on average 10 of 12 slots in incumbent 1,
  and the book's edge sits in the broad episodes where it is already near full, so a larger slot puts more of the
  book into the same trades. Exposure 0.164 -> 0.173, CAGR 2017-04..2026-10 12.3 % -> 13.1 %, max drawdown
  -6.6 % -> -7.0 %. No parameter is added (7 PARAMS, as incumbent 1).

RESULT (v4lab / compare4 / plateau4 against incumbent 1; full outputs in compare4.txt, plateau.txt, run4.txt)
* objective 0.8429 (incumbent 0.7887); windows pre 0.934 / is 1.142 / oos 0.843 (binding oos);
  span 0.997, 2020-26 1.030, drop10 0.598 (0.559); each window without its own 5 best days: pre 0.274,
  is 0.731, oos 0.139 (worst 0.139 vs 0.062); objective at 25 bps 0.638 (0.557), at 40 bps 0.329 (0.209).
* lookahead_ok True; compliant (RSI 0.28, EMA 0.67, VWAP 0.84); sanity passes narrowly (5.14 names on average
  on invested days against the floor of 5; 8 or 6 slots fall below it).
* FAILS consistency: 2018 turns from 0.00 to -0.13 (8 positive years against 9).
* FAILS years (leave-one-year-out): the gain survives in 6 of 10 years only; without 2019 (-0.09), 2024 (-0.11),
  2020 (-0.03) or 2018 (-0.004) the candidate is below incumbent 1. Most of the gain is 2019 (Sharpe 0.87 -> 1.53).
* FAILS plateau4: slots is a moved parameter, and both neighbours (8: 0.801, 12: 0.789 = incumbent 1) are below
  the absolute floor (incumbent + 60 % of the margin, 0.821); at 8 of the 12 neighbouring settings of the inherited
  parameters 10 slots are worse than 12. Joint +-20 % perturbation (60 draws, seed 20261010): mean 0.607 (incumbent
  0.597), p10 0.334 (0.370), min 0.107 (0.189). The gain is a single-point bump on the slot grid, not a structural
  improvement; this file is reported as the lens's best attempt, not as a keep.

WHAT ELSE THE LENS TRIED (experiments.tsv; all below incumbent 1)
* Daily exposure targeting (all open positions re-scaled to min(size, 1/n_held)): objective 0.37 / 0.13. Days with
  only 1-2 names held are mostly stragglers that have not reclaimed their VWAP, and their excess return is negative,
  so scaling them up adds risk without return.
* Frozen entry size min(size, free capacity / new names) (narrow dips get bigger slots): 0.54-0.69; 2017-19
  falls because narrow episodes pre-empt capacity that the broad rebound episodes need.
* NIFTYBEES for the unused capacity on trigger days (exit on its own VWAP reclaim or the time stop): 0.38 / 0.52.
* Inverse-volatility entry sizing (rv20 or rv60 relative to the member median, clipped 0.5-2): 0.75 / 0.66.

WHY IT SHOULD HOLD ACROSS REGIMES (as incumbent 1)
* It harvests short-term reversal after market-wide selling: forced and impatient sellers push uptrending names
  below the volume-weighted price their recent buyers paid, and the bounce back to that price pays whoever supplies
  liquidity. The index filter keeps it out of crashes in progress; the time stop cuts dips that are not bought back.

Long-only, gross at most 1 (10 slots of 1/10). Longest lookback 100 sessions (EMA100), inside the 200-session limit;
warm-up NaNs read as "no trend", "not oversold", "no breadth".

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (index and stock trend filters
off, breadth over all live members); use_rsi=False drops the stock RSI filter; use_vwap=False drops the breadth
trigger (trades may open every day), the VWAP-depth ranking (ties keep the ticker order) and the VWAP-reclaim exit
(time stop only).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    trend_n=100,         # EMA length of the uptrend filters (index and stocks)
    breadth_cut=0.6,     # trigger: share of uptrend members below their VWAP must exceed this
    rsi_n=3,             # stock Wilder RSI length
    rsi_max=20.0,        # stock is oversold when RSI(rsi_n) < rsi_max
    vwap_n=10,           # rolling VWAP length: trigger breadth, ranking and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=10,            # each held name weighs 1 / slots of the book (incumbent 1: 12)
    use_rsi=True, use_ema=True, use_vwap=True,
)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    tick = [t for t in md.tickers if t != ETF_TICKER]
    c = md.close[tick]
    live = md.member[tick] & md.tradable[tick]
    vw = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["vwap_n"])
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        uptrend = c > ema(c, p["trend_n"])
        ix_up = (ix > ema(ix, p["trend_n"]))["NIFTY50"]
    else:
        uptrend = pd.DataFrame(True, index=c.index, columns=tick)
        ix_up = pd.Series(True, index=ix.index)
    base = live & uptrend
    if p["use_vwap"]:
        below = (c < vw) & base
        breadth = below.sum(axis=1) / base.sum(axis=1).replace(0, np.nan)
        broad = (breadth > p["breadth_cut"]).values
        reclaimed = (c > vw).values
        depth = (c / vw - 1.0).values
    else:
        broad = np.ones(len(c), bool)
        reclaimed = np.zeros(c.shape, bool)
        depth = np.zeros(c.shape)
    if p["use_rsi"]:
        oversold = rsi(c, p["rsi_n"]) < p["rsi_max"]
    else:
        oversold = pd.DataFrame(True, index=c.index, columns=tick)
    trigger = ix_up.reindex(c.index).fillna(False).values & broad
    eligible = (base & oversold).values
    fresh = md.tradable[tick].values

    T, N = c.shape
    K, mh = int(p["slots"]), int(p["max_hold"])
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    out = np.zeros((T, N))
    for t in range(T):
        age[held] += 1
        sell = held & fresh[t] & (reclaimed[t] | (age >= mh))
        held &= ~sell
        age[~held] = 0
        free = K - int(held.sum())
        if trigger[t] and free > 0:
            cand = np.where(eligible[t] & ~held)[0]
            if len(cand) > free:
                cand = cand[np.argsort(depth[t, cand], kind="stable")[:free]]
            held[cand] = True
            age[cand] = 0
        out[t, held] = 1.0 / K
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[tick] = pd.DataFrame(out, index=c.index, columns=tick).reindex(index=md.calendar).fillna(0.0)
    return W
