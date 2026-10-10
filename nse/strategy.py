"""
Pullback strategy: RSI + EMA + VWAP dip buying inside uptrends (protocol v4.2, incumbent 1, merged after round 3).
The final strategy of protocol v4: rounds 4 and 5 merged nothing, so under the stopping rule incumbent 1 is final.
Its code is identical to v4/inc1_pullback.py, the file scored on the deep hold-out; this copy's docstring was
corrected after that look (program.md, review of the v2 deliverables).

The whole book is a dip-buying sleeve. Its trades open only when the leaders of the market have pulled back together
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
   sit deepest below their 10-day VWAP. A held name's target stays 1/12 until it is sold; the harness trades the
   drifted position back to 1/12 at every open (costs included), so positions do not drift.
4. Exit (VWAP + time). A held name is sold as soon as it closes back above its 10-day VWAP (the dip has been
   bought back), or after max_hold (20) sessions if that never happens. Names leaving the index are sold by the
   harness.

WHY IT SHOULD HOLD ACROSS REGIMES
* It harvests short-term reversal after market-wide selling: forced and impatient sellers push uptrending names
  below the volume-weighted price their recent buyers paid, and the bounce back to that price pays whoever supplies
  liquidity. Any member in its own uptrend can qualify, whatever its sector, so a change of market leadership
  changes the names rather than the trade (the depth ranking only decides which names fill the slots when more
  qualify than are free; the audits found it no better than random).
* Breadth is counted over the uptrend names only, so it fires when the leaders themselves pull back, not in narrow
  tapes where most members are already falling. The index filter keeps it out of crashes in progress (March 2020);
  the time stop cuts dips that are not bought back within a month.

PROVENANCE (results_v4.tsv, program.md protocol v4.2)
Round 1 (pullback lens) found the dip-buying effect with an index RSI(3) trigger (objective 0.69, fragile). Round 2
moved the oversold test to the stocks and the trigger to leader breadth, inside a 20 % EMA-timer core (0.75).
Round 3 removed a dead entry rule, a token equal-weight sliver and the breadth size ramp (0.745). The round-3 audits
then showed that the 20 % EMA-timer core LOWERS the objective (removing it: 0.79) while every gate against R0 still passes, so
under the simplification rule the lead deleted it (and one dead sell rule duplicated by the harness) for this
incumbent.

DISCLOSED WEAK POINTS (round-3 audits; figures re-measured on this book after the deep look, program.md)
* Low exposure: 16 % of the book on average over 2017-26, stocks held on about a third of sessions. The CAGR
  includes the repo rate earned on idle capital; the Sharpe, measured over the repo rate, does not.
* Event days: a dip buyer earns much of its return on a few market-wide rebound days. With each window's own 5 best
  days removed, 2017-19 and 2020-23 fall below the plain EMA timer R0; the 2024-26 advantage survives.
* The 2024-26 result is concentrated in 2024 (the June 2024 exit-poll rebound among its best days); 2026 is negative.
* The VWAP-depth ranking is no better than random choice among eligible names (it is kept because some rule must
  choose and it is the structural one). Switching the RSI(3) filter off raises 2024-26 (0.85 to 1.06)
  but lowers 2017-19 (0.79 to 0.65) and 2020-23 (1.12 to 0.71): removing it costs 0.14 of objective. It also
  carries the brief's RSI.
* vwap_n is one length shared by the trigger, the ranking and the exit; untied, the trigger's own length is a
  narrow peak (8 or 12 sessions keeps only 45 % or 57 % of the margin over R0, below the plateau gate's 60 %).

Long-only, gross at most 1 (12 slots of 1/12). Longest lookback 100 sessions (EMA100), inside the 200-session limit;
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
