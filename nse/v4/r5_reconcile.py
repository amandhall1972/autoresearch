"""
Pullback strategy with a market-conditional short exit (protocol v4.2, round 5, lens: reconcile the round-4 levers
with every gate). BEST ATTEMPT, NOT A KEEP: against incumbent 1 it passes sanity, improves, windows, drop10,
consistency, parsimony, compliance and lookahead, but FAILS leave-one-year-out (without 2020) and plateau4
(breadth_cut 0.72, rsi_max 16). See RESULT below.

The whole book is a dip-buying sleeve, as in incumbent 1 (v4/inc1_pullback.py). Every weight is decided with data up
to the close of day t; the harness trades it at the open of t+1. Capital not in a trade stays in cash and earns the
repo rate (credited by the harness).

RULES
1. Trigger (EMA + VWAP). Trades open only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad among the leaders: of the members in their own uptrend (close above their own
        EMA(100)), more than breadth_cut (60 %) close below their rolling vwap_n (10)-day VWAP.
2. Name filter (EMA + RSI). On such a day a member is eligible when it is in its own uptrend (close above its
   EMA(100)) and was sold hard: Wilder RSI(rsi_n = 3) below rsi_max = 20.
3. Selection (VWAP). Free slots (slots = 12; one slot = 1/12 of the book) are filled with the eligible names that
   sit deepest below their 10-day VWAP. A name keeps the weight it was bought with until it is sold.
4. Exit (VWAP + time) - CHANGED. A held name is sold when
     a) it closes back above its 10-day VWAP (the dip has been bought back; incumbent 1's exit), or
     b) it closes above its SHORT exit_n (7)-day VWAP while the market itself has NOT been bought back yet:
        NIFTYBEES (one unit of all 50 members) still closes below its own 10-day VWAP, or
     c) after max_hold (20) sessions if neither happens.
   Names leaving the index are sold by the harness.

WHAT CHANGED VS INCUMBENT 1 AND WHY (one rule, one new parameter: 8 parameters against 7)
* Round 4 found that exiting every trade at the 7-day VWAP instead of the 10-day one lifts the objective
  (0.789 -> 0.987) but fails sanity (28.1 % of days invested) and leave-one-year-out (without 2020). Diagnosis on
  incumbent 1's 705 trades (2017-04..2026-10): 362 trades close above their 7-day VWAP before they reclaim the
  10-day one, and the remaining "continuation" from that first bounce to the incumbent's exit is worth +0.56 slot
  units in total (+0.15 % a trade over about 3 sessions). It depends on the market: when NIFTYBEES is still below
  its own 10-day VWAP at the name's first bounce, the continuation is nil (167 trades, +0.02 % a trade, sum +0.04;
  2018 -0.37 from the IL&FS sell-off); when the market has already reclaimed its VWAP it is positive (195 trades,
  +0.27 % a trade, sum +0.52). Rule 4b therefore banks the first bounce only while the market-wide pullback has
  not been bought back, and keeps incumbent 1's full-reclaim exit once it has. The trade is a bet on the reversal
  of a market-wide pullback: a name that bounces while the market is still below its VWAP has given what the
  pullback can be expected to give; once the market has reversed, lagging names drift back to their own VWAP.
* Compared with the unconditional 7-day exit, the conditional one keeps most of the gain (objective 0.934 against
  0.987) with longer holds where they pay: 33.9 % of days invested (unconditional 28.1 %, floor 30 %) and 5.35
  names on average (floor 5), so the sanity gate passes; the exit_n neighbours 6 and 8 now both clear the absolute
  plateau floor (1.063 / 0.919 against 0.876; unconditional 8 gave 0.885 against 0.908).

WHY IT STILL FAILS (honest)
* Leave-one-year-out without 2020: 2021-23 Sharpe about 0.74 against incumbent 1's 0.774. Every way of shortening
  the trade tested this round (conditional on the market's VWAP, on the market being below its entry-day level, on
  pullback breadth, on the name lagging the market, a shrinking exit window, a relative VWAP-gap exit) and every way
  of staggering entries (per-day cap, share-of-eligible cap, half-of-free-slots pyramid) lowers 2022 (0.89 ->
  0.51-0.78) and usually 2021: the 2021-22 rebounds were V-shaped and the continuation after the first bounce was
  positive in every split (entry depth, entry breadth, market state, age at the bounce). The levers' gain rests on
  failing-rebound episodes (2018-09 IL&FS: BAJFINANCE, ITC, SBIN; 2024-10: BAJAJ-AUTO, HEROMOTOCO; 2023-09), which
  is what the leave-one-year-out gate is designed to catch; in the full is window 2020's gain hides the 2021-22 loss.
* Plateau4 (relative rule for inherited parameters): at breadth_cut 0.72 (only the broadest sell-offs) the
  conditional exit gives 0.598 against incumbent 1's 0.684 at the same setting (broad sell-offs rebound V-shaped,
  continuation +0.66 % a trade for entry breadth 0.7-0.8); at rsi_max 16 0.742 against 0.753.

RESULTS (15 bps; incumbent 1 in brackets): objective 0.9339 (0.7887); windows pre 1.175 (0.789), is 1.098 (1.121),
oos 0.934 (0.847); span 1.038 (0.956); 2020-26 1.028 (1.018); drop10 0.647 (0.559); 9 positive years (9); 2018 1.18
(0.00), 2022 0.69 (0.89), 2023 2.09 (1.56), 2026 -0.28 (-0.53). Exposure 0.151 (0.164), invested on 33.9 % of days
(34.8 %), 5.35 names (5.65), turnover 13.6x (13.1x), span max drawdown -6.5 % (-6.6 %), CAGR 12.5 %.
Robustness (all at least as good as incumbent 1): objective at 25 bps 0.689 (0.557), at 40 bps 0.322 (0.209); each
window without its own 5 best days pre 0.490 / is 0.691 / oos 0.192, worst 0.192 (0.062); joint +-20 % perturbation
(60 draws, numpy default_rng(20261010)) mean 0.657 (0.597), p10 0.426 (0.370), min 0.171 (0.189).
Gates (compare4.py / plateau4.py against incumbent 1): sanity, improves, windows, drop10, consistency, parsimony
(+0.145 for one parameter), compliance (RSI 0.29, EMA 0.69, VWAP 0.84) and lookahead pass; years FAILS (without
2020: 0.739 against 0.774; the gain survives in the other 9 years, +0.09 to +0.50); plateau4 FAILS at breadth_cut
0.72 (0.598 vs 0.684) and rsi_max 16 (0.742 vs 0.753); exit_n 6 / 8 give 1.063 / 0.919 (floor 0.876).

Long-only, gross at most 1 (12 slots of 1/12). Longest lookback 100 sessions (EMA100), inside the 200-session limit;
warm-up NaNs read as "no trend", "not oversold", "no breadth", "no reclaim", "market not below its VWAP".

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (index and stock trend filters
off, breadth over all live members); use_rsi=False drops the stock RSI filter; use_vwap=False drops the breadth
trigger (trades may open every day), the VWAP-depth ranking (ties keep the ticker order) and both VWAP exits
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
    vwap_n=10,           # rolling VWAP length: trigger breadth, ranking, full-reclaim exit, market VWAP
    exit_n=7,            # short VWAP length: first-bounce exit while the market is still below its VWAP
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
        broad = (breadth > p["breadth_cut"]).values
        E = [ETF_TICKER]
        mvw = rolling_vwap(md.high[E], md.low[E], md.close[E], md.volume[E], p["vwap_n"])[ETF_TICKER]
        mkt_down = (md.close[ETF_TICKER] < mvw).reindex(c.index).fillna(False).values
        vs = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["exit_n"])
        reclaimed = (c > vw).values | ((c > vs).values & mkt_down[:, None])
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
