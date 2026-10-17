"""
R0 trend core plus a pullback sleeve (protocol v4.1, round 2, lens: R0 plus a pullback sleeve).

The book is two fixed parts that never compete for the same capital:
  * the TREND CORE (1 - sleeve_share = 20 % of the book): the R0 timer;
  * the PULLBACK SLEEVE (sleeve_share = 80 % of the book): short dip-buying trades in uptrending members,
    opened only when the market as a whole has pulled back inside its uptrend.
Every weight is decided with data up to the close of day t; the harness trades it at the open of t+1.
Capital that neither part uses stays in cash and earns the repo rate (credited by the harness).

RULES
1. Trend core (EMA). While the NIFTY 50's EMA(fast = 20) is above its EMA(slow = 50), the core holds its 20 %:
   etf_share (75 %) of it in NIFTYBEES and the other quarter split equally over the point-in-time Nifty 50
   members. Otherwise the core is in cash. This is R0, the one effect that held in every research window; the
   equal-weight quarter only spreads the core over the member list (the book then holds many names).
2. Pullback trigger and trade size (EMA + VWAP). The sleeve opens trades only on a day when
     a) the NIFTY 50 closes above its EMA(trend_n = 100): the market is in a long uptrend, and
     b) the pullback is broad: among the members that are in their own uptrend (close above their EMA(100)),
        the share that closes below its rolling vwap_n-day (10) VWAP is above breadth_from (50 %).
   The size of each new trade grows linearly with that share, from zero at 50 % to full size at
   breadth_full (70 %) and above: a pullback that only half the leaders join buys small, one that most of
   them join buys full.
3. Name filter (EMA + RSI + VWAP). A member is eligible on such a day when it is in its own uptrend (close
   above EMA(100)), was sold hard (Wilder RSI(rsi_n = 3) below rsi_max = 20) and still closes below its
   10-day VWAP.
4. Selection (VWAP). Free slots (slots = 12; a full-size slot is sleeve_share / 12 = 6.7 % of the book) are
   filled with the eligible names that sit deepest below their 10-day VWAP (lowest close / VWAP10 - 1).
   A name keeps the weight it was bought with until it is sold.
5. Exit (VWAP + time). A held name is sold as soon as it closes back above its 10-day VWAP (the dip has been
   bought back), or after max_hold (20) sessions if that never happens; non-members are sold.

WHY IT SHOULD HOLD ACROSS REGIMES
* The trend core is R0 unchanged: medium-term index trend persistence, positive in 2017-19, 2020-23 and
  2024-26, and it keeps the book out of the long falls (2018 H2, March 2020, 2022, early 2026).
* The sleeve harvests short-term reversal after market-wide selling: forced and impatient sellers push
  uptrending names below the volume-weighted price their recent buyers paid, and the bounce back to that
  price is paid to whoever supplies liquidity. The trade-level edge is present in every window: mean trade
  net of the 30 bp round trip +0.5 % (2017-19), +0.9 % (2020-23), +1.0 % (2024-26), 69-72 % winners,
  about 7 sessions held. Names are chosen by how far they fell, not by momentum or sector, so a change of
  market leadership (the v1 failure after 2023) does not remove the edge.
* The breadth is counted over the uptrend names only. In a narrow market (2018-19) most members sit below
  their VWAP while a few leaders carry the index; counting all members fires in those weak tapes and buys
  falling knives. Counting the uptrend names fires only when the leaders themselves pull back.
* The size ramp replaces a hard breadth cut-off. With a cut-off the result hinged on which handful of days
  crossed it (cut-offs 0.55 / 0.60 / 0.65 / 0.70 gave objectives 0.62 / 0.83 / 0.67 / 0.52); with the ramp,
  moving either end by 0.1 changes the objective by at most 0.16 and every neighbour stays above 0.59.
* The index filter (NIFTY above EMA100) keeps the sleeve out of crashes (without it 2020 turns negative);
  the time stop matters too (without it the objective falls by about 0.2): a dip that has not been bought
  back within a month is not a liquidity dip.
* Sizing: the sleeve is active on about 46 % of the days with an average gross of 0.14 of its capital, the
  core is on about 71 % of the days, so 80 / 20 gives the two parts similar average exposure (about 0.11 and
  0.14 of the book). Their daily excess returns correlate about 0.55, so the mix is smoother than either.

KNOWN WEAK POINTS
* 2017 and 2021 (strong, steady trends) are weaker than R0 because 80 % of the capital waits for dips.
* The sleeve has a fat right tail (rebound days), so its Sharpe after removing its own best days falls more
  than R0's; after removing the NIFTY's 5 or 10 best days of each window (the market-wide event days) the
  2024-26 gain survives (0.43 vs -0.12 for R0) while 2017-19 and 2020-23 stay within 0.05 of R0.
* 2026 is negative, as for every strategy tried.

Long-only, gross at most 1; longest lookback 100 sessions (EMA100), inside the 200-session limit.

Brief-compliance switches: use_ema=False makes every EMA rule pass (core always on, index and stock trend
filters off, breadth over all live members); use_rsi=False drops the stock RSI filter; use_vwap=False drops
the breadth trigger and size ramp (full size every day), the below-VWAP entry filter, the VWAP-depth
ranking and the VWAP-reclaim exit (time stop only).
"""
import numpy as np
import pandas as pd
from indicators import ema, rsi, rolling_vwap
from prepare import ETF_TICKER

PARAMS = dict(
    fast=20,             # core: NIFTY EMA(fast) > EMA(slow) (R0)
    slow=50,
    etf_share=0.75,      # share of the core in NIFTYBEES; the rest equal-weight over the members
    sleeve_share=0.8,    # share of the book given to the pullback sleeve (the core gets the rest)
    trend_n=100,         # EMA length of the uptrend filters (index and stocks)
    breadth_from=0.5,    # trigger: share of uptrend members below their VWAP above which the sleeve buys
    breadth_full=0.7,    # ... and at which it buys at full size (linear in between)
    rsi_n=3,             # stock Wilder RSI length
    rsi_max=20.0,        # stock is oversold when RSI(rsi_n) < rsi_max
    vwap_n=10,           # rolling VWAP length: trigger breadth, entry depth, ranking and the reclaim exit
    max_hold=20,         # time stop in sessions
    slots=12,            # sleeve slots; each held name weighs sleeve_share / slots
    use_rsi=True, use_ema=True, use_vwap=True,
)


def _pullback_sleeve(md, p, tick):
    """Weights of the pullback sleeve as a fraction of the sleeve's own capital (each held name 1/slots)."""
    c = md.close[tick]
    live = md.member[tick] & md.tradable[tick]
    vw = rolling_vwap(md.high[tick], md.low[tick], c, md.volume[tick], p["vwap_n"])
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        trend = c > ema(c, p["trend_n"])
        ix_up = (ix > ema(ix, p["trend_n"]))["NIFTY50"]
    else:
        trend = pd.DataFrame(True, index=c.index, columns=tick)
        ix_up = pd.Series(True, index=ix.index)
    base = live & trend
    if p["use_vwap"]:
        below = (c < vw) & base
        breadth = below.sum(axis=1) / base.sum(axis=1).replace(0, np.nan)
        size = ((breadth - p["breadth_from"]) / (p["breadth_full"] - p["breadth_from"])).clip(0.0, 1.0)
        size = size.fillna(0.0)
        reclaimed = (c > vw).values
        gap = (c / vw - 1.0).values
    else:
        size = pd.Series(1.0, index=c.index)
        reclaimed = np.zeros(c.shape, bool)
        gap = np.zeros(c.shape)
    if p["use_rsi"]:
        oversold = rsi(c, p["rsi_n"]) < p["rsi_max"]
    else:
        oversold = pd.DataFrame(True, index=c.index, columns=tick)
    size = size.values
    trigger = ix_up.reindex(c.index).fillna(False).values & (size > 0)
    eligible = (base & oversold).values
    fresh = md.tradable[tick].values
    member = md.member[tick].values

    T, N = c.shape
    K, mh = int(p["slots"]), int(p["max_hold"])
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    wgt = np.zeros(N)
    out = np.zeros((T, N))
    for t in range(T):
        age[held] += 1
        sell = held & fresh[t] & (reclaimed[t] | (age >= mh))
        sell |= held & ~member[t]
        held &= ~sell
        age[~held] = 0
        free = K - int(held.sum())
        if trigger[t] and free > 0:
            cand = np.where(eligible[t] & ~held & ~reclaimed[t])[0]
            if len(cand) > free:
                cand = cand[np.argsort(gap[t, cand], kind="stable")[:free]]
            held[cand] = True
            age[cand] = 0
            wgt[cand] = size[t] / K
        out[t, held] = wgt[held]
    return pd.DataFrame(out, index=c.index, columns=tick)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    tick = [t for t in md.tickers if t != ETF_TICKER]
    ix = md.index_close[["NIFTY50"]]
    if p["use_ema"]:
        on = (ema(ix, p["fast"]) > ema(ix, p["slow"]))["NIFTY50"].astype(float)
    else:
        on = pd.Series(1.0, index=ix.index)
    on = on.reindex(md.calendar).fillna(0.0)
    core_w = (1.0 - p["sleeve_share"]) * on

    mem = md.member[tick].astype(float)          # a member without a fresh bar keeps its slot (harness carry)
    ew = mem.div(mem.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)

    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[tick] = ew.mul(core_w * (1.0 - p["etf_share"]), axis=0)
    W[ETF_TICKER] = core_w * p["etf_share"]
    S = _pullback_sleeve(md, p, tick).reindex(index=md.calendar).fillna(0.0)
    W[tick] = W[tick] + S * p["sleeve_share"]
    return W
