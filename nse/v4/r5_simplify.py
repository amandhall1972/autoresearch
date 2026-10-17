"""
Pullback strategy with one parameter fewer: incumbent 1 (v4/inc1_pullback.py) with the time-stop parameter
max_hold deleted; a trade's holding limit is now its VWAP window, vwap_n sessions (protocol v4.2, round 5, lens:
SIMPLIFY). BEST ATTEMPT, NOT A KEEP: 6 parameters instead of 7 and an objective of 0.7915 (incumbent 0.7887), but
against incumbent 1 it fails sanity (invested on 28.0 % of days, floor 30 %), drop10, leave-one-year-out,
consistency, plateau4 and the event-day robustness bar. See RESULT and DELETION TABLE below.

The whole book is a dip-buying sleeve. Trades open only when the leaders of the market have pulled back together
inside a long uptrend, and close when the dip has been bought back. Every weight is decided with data up to the
close of day t; the harness trades it at the open of t+1. Capital not in a trade stays in cash and earns the repo
rate (credited by the harness).

RULES
1. Trigger (EMA + VWAP). Trades open only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad among the leaders: of the members in their own uptrend (close above their own
        EMA(100)), more than breadth_cut (60 %) close below their rolling vwap_n (10)-day VWAP.
2. Name filter (EMA + RSI). On such a day a member is eligible when it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max = 20.
3. Selection (VWAP). Free slots (slots = 12; one slot = 1/12 of the book) are filled with the eligible names that
   sit deepest below their 10-day VWAP. A name keeps the weight it was bought with until it is sold.
4. Exit (VWAP) - CHANGED. A held name is sold as soon as it closes back above its 10-day VWAP (the dip has been
   bought back), or once its entry day has left that VWAP window (vwap_n = 10 sessions after entry) without a
   reclaim. Names leaving the index are sold by the harness.

WHAT CHANGED VS INCUMBENT 1 AND WHY
* max_hold (20 sessions) is deleted. The holding limit uses the length the strategy already has, vwap_n: the
  setup is "the close is below the volume-weighted price of the last vwap_n sessions"; once the entry day has
  rolled out of that window, the VWAP no longer contains the dip the trade was opened on, so the original setup
  has expired. The limit becomes 10 sessions instead of 20 (at the point incumbent 1's time stop binds on 20 of
  729 trades). Every other rule and value is unchanged.
* Effect: objective 0.7887 -> 0.7915; windows pre 0.789 -> 0.856, is 1.121 -> 1.089, oos 0.847 -> 0.791 (now the
  binding window); span 0.956 -> 0.939; 2020-26 1.018 -> 0.978; drop10 0.559 -> 0.538; exposure 0.164 -> 0.148;
  invested days 34.8 % -> 28.0 %; names 5.65 -> 6.31; turnover 13.1x -> 13.2x; max drawdown -6.6 % -> -6.7 %.
  Years: 2018 0.00 -> -0.16 (negative), 2019 0.87 -> 1.12, 2020 2.47 -> 3.01, 2022 0.89 -> 0.74, 2023 1.56 -> 1.02,
  2024 1.13 -> 1.03; the others are within 0.03.

RESULT (honest; compare4.txt, plateau4 in plateau.txt, run4.txt; experiments.tsv)
* PASS: improves (+0.003), windows (is 1.089 against a floor of 1.021, oos 0.791 against 0.747), parsimony (one
  parameter fewer), compliance (RSI 0.27, EMA 0.64, VWAP 0.88), lookahead_ok, and the cost and perturbation bars:
  objective at 25 bps 0.567 (incumbent 0.557), at 40 bps 0.230 (0.209); joint +-20 % perturbation (60 draws, numpy
  default_rng(20261010), every numeric PARAMS entry times U[0.8, 1.2]) mean 0.684 (0.597), p10 0.450 (0.370),
  min 0.223 (0.189).
* FAIL sanity: invested on 28.0 % of days (floor 30 %). Shorter holds leave the book flat more often; nothing
  in the rules can raise it without a rule whose only job is the gate.
* FAIL drop10: 0.538 against 0.559.
* FAIL years: the candidate is below incumbent 1 without 2018 (-0.055), 2019 (-0.024), 2020 (-0.109) or 2025
  (-0.064); the +0.003 rests on 2017, 2024 and 2026.
* FAIL consistency: 8 positive years against 9 (2018 turns negative).
* FAIL event-day bar: with each window's own 5 best days removed, pre 0.103 / is 0.676 / oos 0.040, worst 0.040
  against incumbent 1's 0.062.
* FAIL plateau4 (every parameter is inherited, so the relative rule applies; vwap_n now also moves the holding
  limit): trend_n 80 0.835 (incumbent at 80: 0.772) and 120 0.791 (0.783), breadth_cut 0.48 0.586 (0.578) and
  rsi_n 4 0.878 (0.770) pass; breadth_cut 0.72 0.606 (0.684), rsi_n 2 0.578 (0.584), rsi_max 16 0.648 (0.753),
  rsi_max 24 0.838 (0.925), vwap_n 8 0.761 (0.770), vwap_n 12 0.586 (0.625), slots 10 0.813 (0.843) and
  slots 14 0.795 (0.807) fail.

DELETION TABLE (lens SIMPLIFY; each row changes incumbent 1 in one place unless stated; windows pre / is / oos ->
objective; incumbent 1 is 0.789 / 1.121 / 0.847 -> 0.789)
Time stop (max_hold):
* deleted: 0.809 / 1.087 / 0.593 -> 0.593 (dips that keep falling stay below their VWAP and are never sold).
* tied to the VWAP window, limit = vwap_n (THIS FILE): 0.856 / 1.089 / 0.791 -> 0.792 (invested 28.0 %).
* replaced by a sell when the name closes below its own EMA(100): 0.476 / 0.893 / 0.954 -> 0.476.
* replaced by a sell when the NIFTY closes below its EMA(100): 0.640 / 0.657 / 0.858 -> 0.640.
* replaced by a sell of the laggards once the market has recovered (share of uptrend members below their VWAP
  under 1 - breadth_cut): 1.098 / 1.047 / 0.948 -> 0.948, 10 positive years, drop10 0.602, event-day worst
  0.185, but invested on only 15.7 % of days, and a knife edge: breadth_cut 0.55 gives 0.616, 0.50 gives 0.609.
* replaced by a sell of the laggards once NIFTYBEES closes above its own 10-day VWAP: 0.531 / 0.916 / 0.633.
* replaced by a sell of the laggards once the trigger is off: 0.633 / 0.716 / 0.580.
RSI dials (rsi_n, rsi_max):
* rsi_max deleted, eligible names ranked by RSI(3) (no threshold): 0.682 / 0.664 / 0.926 -> 0.664.
* rsi_max replaced by the RSI midline, RSI(3) < 50: 0.621 / 0.714 / 1.050 -> 0.621; RSI(10) < 50: 0.754 / 0.792 /
  0.984 -> 0.754.
* rsi_max replaced by "stock RSI(3) below the NIFTY's RSI(3)": 0.714 / 1.161 / 0.885 -> 0.714 (8 positive years).
* both dials fixed at a textbook setting: Wilder RSI(14) < 30 never fires for an uptrend name (0.000); Connors
  RSI(2) < 10 0.831 / 1.035 / 0.998 -> 0.831, but drop10 0.552 and the constants would be hidden parameters.
* RSI filter deleted (non-compliant): 0.650 / 0.714 / 1.059 -> 0.650.
Slots and selection:
* slots deleted, every eligible name bought at 1/50 (no cap, no ranking): 0.838 / 0.974 / 0.734 -> 0.734
  (exposure 5 %).
* slots kept only as a cap, all eligible names bought at min(1/12, free capacity / n): 0.725 / 1.070 / 0.862
  -> 0.725.
* VWAP-depth ranking replaced by ticker order: 0.620 / 0.940 / 0.832 -> 0.620; replaced by RSI(3) (lowest first;
  same 7 parameters): 0.891 / 1.110 / 0.842 -> 0.842, but below incumbent 1 in leave-one-year-out without 2018,
  2020, 2024 and 2025 and in 7 of 14 relative-plateau neighbours (slots 10 and 14, rsi_n 2, rsi_max 24,
  breadth_cut 0.48 and 0.72, vwap_n 12). Which eligible names fill the slots is a lottery.
Trend and breadth:
* index filter deleted: 0.915 / 0.174 / 0.364 -> 0.174 (March 2020).
* index filter expressed through breadth alone (more than half of the live members above their own EMA(100)):
  0.785 / 1.079 / 0.785 -> 0.785, no fewer parameters, 8 positive years, drop10 0.520.
* own-uptrend requirement deleted from eligibility: 0.172 / 0.993 / 0.374 -> 0.172.
* breadth counted over all live members instead of uptrend members: 0.456 / 0.847 / 0.833 -> 0.456.
* breadth trigger deleted: -0.619 / 0.086 / 0.623 -> -0.619.
Two deletions together:
* RSI ranking + limit = vwap_n: 0.942 / 1.052 / 0.859 -> 0.859 (invested 28.1 %, 8 positive years, drop10 0.555).
* 1/50 sizing + limit = vwap_n (5 parameters): 0.842 / 0.925 / 0.797 -> 0.797 (invested 28.7 %, 8 positive years,
  drop10 0.395).
Conclusion: at its point incumbent 1 has no removable parameter. Every parameter deletion that keeps the objective
within 0.02 (or above it) shortens the laggards' holds and leaves the book invested on fewer than 30 % of days;
the three that tie the limit to vwap_n also turn 2018 negative (-0.16, -0.05, -0.29).

Long-only, gross at most 1 (12 slots of 1/12). Longest lookback 100 sessions (EMA100), inside the 200-session limit;
warm-up NaNs read as "no trend", "not oversold", "no breadth", "no reclaim".

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (index and stock trend filters
off, breadth over all live members); use_rsi=False drops the stock RSI filter; use_vwap=False drops the breadth
trigger (trades may open every day), the VWAP-depth ranking (ties keep the ticker order) and the VWAP-reclaim exit
(the vwap_n-session holding limit is then the only exit).
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
    vwap_n=10,           # rolling VWAP length: trigger breadth, ranking, reclaim exit AND the holding limit
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
    K, window = int(p["slots"]), int(p["vwap_n"])
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    out = np.zeros((T, N))
    for t in range(T):
        age[held] += 1
        sell = held & fresh[t] & (reclaimed[t] | (age >= window))
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
