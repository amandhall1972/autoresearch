"""
Pullback strategy with a market-confirmed breadth trigger (protocol v4.2, round 5, lens: a more robust trigger).
BEST ATTEMPT, NOT A KEEP: see RESULT. The lens found no trigger that is both higher and flatter than incumbent 1's.

The whole book is a dip-buying sleeve, as in incumbent 1 (v4/inc1_pullback.py). Every weight is decided with data
up to the close of day t; the harness trades it at the open of t+1. Capital not in a trade stays in cash and earns
the repo rate (credited by the harness).

RULES
1. Trigger (EMA + VWAP). Trades open only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend,
     b) the pullback is broad among the leaders: of the members in their own uptrend (close above their own
        EMA(100)), more than breadth_cut (60 %) close below their rolling vwap_n (10)-day VWAP, and
     c) NEW: the market itself is in the pullback: NIFTYBEES (the ETF holding all 50 members) closes below its own
        rolling vwap_n (10)-day VWAP.
2. Name filter (EMA + RSI). On such a day a member is eligible when it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max = 20.
3. Selection (VWAP). Free slots (slots = 12; one slot = 1/12 of the book) are filled with the eligible names that
   sit deepest below their 10-day VWAP. A name keeps the weight it was bought with until it is sold.
4. Exit (VWAP + time). A held name is sold as soon as it closes back above its 10-day VWAP, or after max_hold (20)
   sessions. Names leaving the index are sold by the harness.

WHAT CHANGED VS INCUMBENT 1 (one rule, no new parameter: 7 PARAMS as incumbent 1)
Rule 1c. A breadth reading above 60 % while the cap-weighted market holds above the volume-weighted price of its
last vwap_n sessions is rotation among the leaders, not a market-wide pullback; the rule skips those days. It reuses
vwap_n. In 2017-04..2026-10 it removes 6 of incumbent 1's 204 trigger days: 2017-05-19, 2017-05-22, 2017-08-17,
2018-06-05 (pre), 2021-10-26 (is), 2024-04-23 (oos). That is all it does, so its effect is an event effect, not a
structural one: the objective gain (0.789 -> 0.847, pre 0.789 -> 0.850) rests on four days in 2017-18.

WHAT THE LENS TRIED (experiments.tsv; incumbent 1 = 0.789; neighbours breadth_cut 0.48 / 0.72 = 0.578 / 0.684)
* The breadth-cut landscape is a noise-dominated peak, not a plateau: cut 0.45 / 0.5 / 0.55 / 0.6 / 0.65 / 0.7 /
  0.75 give 0.28 / 0.46 / 0.55 / 0.79 / 0.65 / 0.56 / 0.39. A looser cut hurts 2017-19 (2017 Sharpe 1.45 -> 0.64):
  the trigger fires one day earlier in each sell-off and fills the book with first-day names (slot capacity binds
  on 88 of 204 trigger days); a stricter cut hurts 2024-26, whose sell-offs were short. Averaging the book over
  cuts {0.5, 0.6, 0.7} or vwap_n {8, 10, 12} gives 0.754 / 0.756: the neighbourhood's level is about 0.70-0.75.
* Suggested structural alternatives, each with cut neighbours: breadth averaged over two or more VWAP lengths
  ({n/2, n}: 0.35; {n, 2n}: 0.67; n/2..2n: 0.59), breadth relative to its own trailing distribution (percentile
  rank, z-score, excess over trailing mean: best 0.76), breadth of RSI-oversold names (best 0.67), momentum
  breadth RSI(3) < 50 (best 0.66), smoothed breadth (best 0.57), change in breadth (best 0.64), turn of breadth
  (best 0.33), rising breadth (0.74; 0.56 / 0.70 at the neighbours), median VWAP depth (best 0.73), typical price or
  down-day breadth (best 0.74). None beats incumbent 1 at its point or at its neighbours.
* "Breadth above the cut OR a one-day breadth jump above 0.25-0.35" lifts 2020-23 and 2024-26 (span 1.08,
  drop10 0.70 at 0.25) but not the binding 2017-19, adds a parameter, and rests on about 7 shock days per window.

RESULT (15 bps; incumbent 1 in brackets; compare4.txt, plateau.txt, run4.txt, robust.txt)
objective 0.8467 (0.7887); windows pre 0.850 (0.789), is 1.121 (1.121), oos 0.847 (0.847); span 0.969 (0.956);
drop10 0.573 (0.559); 2017 1.67 (1.45), 2018 -0.05 (0.00), every other year identical. lookahead_ok True;
compliant (RSI 0.29, EMA 0.66, VWAP 0.84). Objective at 25 bps 0.620 (0.557), at 40 bps 0.276 (0.209). Each window
without its own 5 best days: worst 0.143 (0.062). Joint +-20 % perturbation (60 draws, seed 20261010): mean 0.612
(0.597), p10 0.375 (0.370), min 0.060 (0.189).
* PASS: sanity, improves, windows, drop10, parsimony (no new parameter), compliance, lookahead, the three
  robustness bars.
* FAIL years: the gain survives in 5 of 10 years only; without 2017 the candidate is BELOW incumbent 1
  (0.415 vs 0.438), and without 2018, 2020, 2024 or 2025 it only ties. The gain rests on the three removed
  2017 trigger days.
* FAIL consistency: 2018 turns from +0.003 to -0.045 (8 positive years against 9).
* FAIL plateau4: 9 of 14 neighbours fail the relative rule (most tie incumbent 1, because the rule removes no day
  there); breadth_cut 0.48 gives 0.563 (incumbent 0.578) and vwap_n 8 gives 0.674 (0.770). Minimum neighbour
  0.563, below incumbent 1's 0.578: the trigger is NOT more robust.

Long-only, gross at most 1 (12 slots of 1/12). Longest lookback 100 sessions (EMA100), inside the 200-session limit;
warm-up NaNs read as "no trend", "not oversold", "no breadth", "no market pullback".

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (index and stock trend filters
off, breadth over all live members); use_rsi=False drops the stock RSI filter; use_vwap=False drops the breadth
trigger and the NIFTYBEES confirmation (trades may open every day), the VWAP-depth ranking (ties keep the ticker
order) and the VWAP-reclaim exit (time stop only).
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
    vwap_n=10,           # rolling VWAP length: breadth, NIFTYBEES confirmation, ranking and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=12,            # each held name weighs 1 / slots of the book
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
        E = [ETF_TICKER]
        etf_vw = rolling_vwap(md.high[E], md.low[E], md.close[E], md.volume[E], p["vwap_n"])[ETF_TICKER]
        mkt_dip = (md.close[ETF_TICKER] < etf_vw).reindex(c.index).fillna(False)
        broad = ((breadth > p["breadth_cut"]) & mkt_dip).values
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
