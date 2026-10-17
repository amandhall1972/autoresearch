"""
Pullback strategy with a short-VWAP exit (round 4, lens: entries, exits and costs). Best attempt, NOT a keep:
it fails the sanity gate (invested on 28.1 % of days, floor 30 %), the leave-one-year-out gate (without 2020) and
the plateau gate (exit_n = 8). See "GATES" below.

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
4. Exit (VWAP + time). A held name is sold as soon as it closes above its SHORT exit_n (7)-day VWAP, or after
   max_hold (20) sessions if that never happens. Names leaving the index are sold by the harness.

WHAT CHANGED VS INCUMBENT 1 (one rule, one new parameter: 8 parameters against 7)
* The exit VWAP is decoupled from the setup VWAP: the trigger and the ranking still measure the pullback against
  the 10-day VWAP, but the trade is closed when the close is back above the 7-day VWAP instead of the 10-day one.
  During a pullback the short VWAP already contains more of the dip's own (lower) prices, so it is reached sooner:
  quick rebounds are banked one or two days earlier, and a dip that keeps falling is closed on its first bounce
  above the short VWAP instead of being carried to the 10-day reclaim or to the 20-session time stop.
* Trade-level effect (2017-04..2026-10): 745 trades instead of 705, the same summed trade P&L (9.75 against 9.85
  in units of one slot), average holding 5.0 sessions instead of 6.5, time-stop exits 3 instead of 20. The same
  P&L with about a fifth less time at risk is where the Sharpe gain comes from (exposure 0.132 against 0.164).
  Diagnostics on incumbent 1's own trades motivated it: after the 10-day reclaim the drift is only the index's
  (excess about 0), and trades still open after 8-15 sessions have a remaining expected return near zero in every
  window, so holding them only adds variance and costs.
* The 20-session time stop is kept although it binds on only 3 trades at exit_n = 7: without it the neighbouring
  exit length 8 falls from 0.885 to 0.729 (it is not dead in the neighbourhood), and with use_vwap = False it is the
  only exit.

RESULTS (15 bps; incumbent 1 in brackets)
objective 0.9872 (0.7887); windows pre 1.190 (0.789), is 1.066 (1.121), oos 0.987 (0.847); span 1.040 (0.956);
drop-10 0.641 (0.559); 9 positive years (9); 2018 1.18 (0.00), 2022 0.59 (0.89), 2026 -0.49 (-0.53).
Objective at 25 bps 0.728 (0.557), at 40 bps 0.340 (0.209). Joint +-20 % perturbation (60 draws, seed 20261010):
mean 0.670 (0.597), p10 0.415 (0.370). Each window without its own 5 best days: pre 0.511, is 0.643, oos 0.226,
worst 0.226 (0.062).

GATES AGAINST INCUMBENT 1 (compare4.py, plateau4.py)
* PASS: improves, windows (is 1.066 against a floor of 1.021), drop10, consistency, parsimony (+0.198 for one
  parameter), compliance, lookahead.
* FAIL sanity: invested on 28.1 % of days (floor 30 %); shorter holds leave the book flat more often. Loosening the
  trigger to raise it (breadth_cut 0.55 / 0.50) breaks the is window.
* FAIL years: without 2020 the is window (2021-23) is 0.686 against the incumbent's 0.774: the short exit costs 2022
  (0.59 against 0.89), where dips recovered slowly to the 10-day VWAP.
* FAIL plateau: exit_n neighbours 6 -> 1.040 (pass), 8 -> 0.885 (floor 0.908). Exit lengths 6-9 all beat the
  incumbent (1.040 / 0.987 / 0.885 / 0.889) but 5 does not (0.725), and an exponentially weighted VWAP of span 7
  does not (0.742).
* Event dependence: most of the pre-window gain is 2018, and most of 2018's gain is three trades of the
  2018-09-11 entry (BAJFINANCE, ITC, SBIN banked on the first bounce instead of being held into the IL&FS sell-off).

Long-only, gross at most 1 (12 slots of 1/12). Longest lookback 100 sessions (EMA100), inside the 200-session limit;
warm-up NaNs read as "no trend", "not oversold", "no breadth", "no reclaim".

Brief-compliance switches (not parameters): use_ema=False makes every EMA rule pass (index and stock trend filters
off, breadth over all live members); use_rsi=False drops the stock RSI filter; use_vwap=False drops the breadth
trigger (trades may open every day), the VWAP-depth ranking (ties keep the ticker order) and the short-VWAP exit
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
    vwap_n=10,           # setup VWAP length: trigger breadth and depth ranking
    exit_n=7,            # exit VWAP length: a held name is sold when it closes above its exit_n-day VWAP
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
        vw_exit = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["exit_n"])
        reclaimed = (c > vw_exit).values
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
