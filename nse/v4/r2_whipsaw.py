"""
EMA trend timer with a climax-then-distribution exit (protocol v4.1, round 2, lens: cut R0's whipsaw and drift
losses).

What it holds
-------------
Cash (credited with the repo rate by the harness) or "the market": 90 % NIFTYBEES and 10 % spread equally over
the point-in-time Nifty 50 members (about 50 names, so the book holds more than 5 names whenever it is invested).
The split is fixed (`etf_share` = 0.9); it is not a signal. Only the size of the market position changes: 1 or 0.

One horizon
-----------
The fast EMA, the VWAP and the RSI all use the same window, `fast` = 20 sessions (about one trading month).
They are three readings of one month of trading: its trend, its volume-weighted cost and its momentum. Tying
them is a design choice that removes two free parameters. The RSI midline 50 is used as a structural constant,
not a tuned level: RSI > 50 means the Wilder-smoothed up-moves exceed the down-moves.

Rules (decided at the close of day t, traded by the harness at the open of t+1)
-------------------------------------------------------------------------------
1. Trend (EMA), unchanged from R0. Invested only while the NIFTY 50's EMA(20) is above its EMA(50).

2. Distribution exit (RSI + VWAP). Inside an uptrend the position goes to cash on a day when all four of these
   hold:
   a. Climax (RSI). Since the uptrend began (the last EMA(20)-over-EMA(50) cross), the NIFTY's Wilder RSI(20)
      has closed above `rsi_climax` = 65 at least once: the trend has had a buying climax.
   b. Volume at the top (VWAP). NIFTYBEES' 20-session VWAP of the typical price (H+L+C)/3 is above the plain
      20-session average of the same typical prices. VWAP minus the plain average is positive when the window's
      heavy-volume sessions traded at its higher prices, so a lot of money was committed near the top.
   c. Those buyers are under water (VWAP). NIFTYBEES closes below that 20-session VWAP.
   d. Momentum has turned (RSI). The NIFTY's RSI(20) is below 50.

3. Re-entry (RSI). After a distribution exit the strategy stays in cash until the RSI(20) closes back above 50
   (momentum restored) or a fresh EMA(20)-over-EMA(50) cross starts a new trend. Rule 1 still applies: no
   position without the EMA uptrend.

Why it should hold across regimes
---------------------------------
* The trend rule is R0's, the one effect the round-1 audits found in all three windows.
* Lens diagnosis: each of R0's 16 trend spells since 2017 gave back about 3-8 % between its peak and the EMA
  exit (Feb 2018 after the Budget, Sep 2018, Feb 2020, Jan 2022, Oct 2024, Jan 2026...). Entries were not the
  problem: at every R0 entry NIFTYBEES was already above its VWAP and RSI above 50, so entry filters do not bind.
* Exits on plain weakness (close below VWAP and RSI below 50, with or without a k-day persistence, at
  14/20/25/50 sessions; RSI range-shift exits at 40/60; an anchored VWAP since the cross) cut the giveback but
  sold ordinary pullbacks. They lost mostly in 2017-19, where an uptrend usually recovers from a pullback.
  The volume condition (b) is what separates a distribution top from a pullback. Within R0's uptrend days,
  the sessions with the window's volume concentrated at its higher prices had lower next-session returns than
  the others in each window (diagnostic, gross annualised Sharpe -0.05 vs 1.35 in 2017-19, 1.01 vs 1.41 in
  2020-23, -2.09 vs 1.47 in 2024-26). The subset that also has RSI below 50 was negative in all three windows.
  The climax condition (a) keeps the exit off in spells that never got overbought (choppy 2025-26 trends),
  where it would only add commissions. Re-entering only once momentum is back (rule 3) avoids the in-and-out
  round trips that cost the free version of the exit in 2025-26.
* The exit acts on few sessions (71 sessions, 3 % of 2017-26, in 16 episodes), so it cannot keep the strategy
  out of a recovering trend for long, and R0's entry is kept.

Honest caveats (numbers in the research log and plateau.txt):
* It is not a strict Pareto gain. 2017-19 (0.71 vs 0.75) and 2020-23 (1.09 vs 1.12) are slightly below R0,
  inside the 0.10 tolerance. The whole gain is in 2024-26 (0.45 vs 0.20), and it rests on a few episodes:
  Oct 2024, Jul 2025 and Jan 2026, plus Sep 2018 and Jan 2020 outside the binding window. If the 5 (10)
  2024-26 sessions with the largest gain over R0 are given R0's return, the objective drops to 0.23 (0.11).
* Plateau. Climax 60 gives 0.45, but 70 gives 0.32, which beats R0 but loses more than 40 % of the margin.
  The untied RSI or VWAP window at 14 or 25 gives 0.42-0.46. With EMA fast 16 or 25 the objective falls to
  R0's level (R0 itself falls to 0.02-0.06 there; the paired gain stays +0.13 to +0.23). slow = 60 gives
  0.30. An RSI midline of 45 instead of 50 gives 0.15.
* The RSI length (20, tied to the fast EMA) and the climax level (65) were picked after a scan around the
  textbook values (RSI 14, 70). About 150 variants were tried for this lens. The RSI and VWAP compliance
  shares are close to the 5 % floor (0.054 and 0.062).

Every indicator uses data up to the close of day t only. The longest lookback is 50 sessions, inside the
200-session limit. use_rsi / use_ema / use_vwap = False set each family's conditions to "always true" (protocol
v4.1, rule 2): without RSI the exit is (b) and (c) alone, re-entering as soon as they stop holding; without
VWAP it is (a) and (d) with the RSI re-entry; without EMA the trend filter is always on and there are no crosses.
"""
import numpy as np
import pandas as pd

import indicators as I
from prepare import ETF_TICKER

PARAMS = dict(
    fast=20,           # fast EMA of the NIFTY 50 and the window of the VWAP and RSI readings (sessions)
    slow=50,           # slow EMA of the NIFTY 50 (sessions)
    rsi_climax=65.0,   # rule 2a: RSI(fast) close that marks the trend's buying climax
    etf_share=0.9,     # holding: share of the invested capital in NIFTYBEES (rest equal-weight members)
    use_rsi=True,
    use_ema=True,
    use_vwap=True,
)

MIDLINE = 50.0         # RSI neutral point (structural constant, not tuned)


def exposure(md) -> pd.Series:
    """Target fraction of the portfolio in the market per day: 1.0 or 0.0."""
    p = PARAMS
    cal = md.calendar
    n = p["fast"]
    always = pd.Series(True, index=cal)
    nifty = md.index_close[["NIFTY50"]]
    etf = [ETF_TICKER]

    # rule 1: trend
    if p["use_ema"]:
        trend = (I.ema(nifty, p["fast"]) > I.ema(nifty, p["slow"]))["NIFTY50"].reindex(cal).fillna(False)
    else:
        trend = always
    new_trend = trend & ~trend.shift(1, fill_value=False)
    spell = new_trend.cumsum()                                   # id of the current trend (since its last cross)

    # rule 2a/2d and rule 3: RSI readings
    rsi = I.rsi(nifty, n)["NIFTY50"].reindex(cal)
    if p["use_rsi"]:
        overbought = (rsi > p["rsi_climax"]).fillna(False)
        climax = overbought.astype(int).groupby(spell).cummax().astype(bool)
        momentum_down = (rsi < MIDLINE).fillna(False)
        momentum_back = (rsi > MIDLINE).fillna(False)
    else:
        climax, momentum_down, momentum_back = always, always, None

    # rule 2b/2c: VWAP readings on NIFTYBEES
    if p["use_vwap"]:
        h, l, c, v = md.high[etf], md.low[etf], md.close[etf], md.volume[etf]
        typical = I.typical_price(h, l, c)[ETF_TICKER]
        vwap = I.rolling_vwap(h, l, c, v, n)[ETF_TICKER]
        plain = typical.rolling(n, min_periods=n).mean()
        volume_at_top = (vwap > plain).fillna(False)
        under_water = (c[ETF_TICKER] < vwap).fillna(False)
    else:
        volume_at_top, under_water = always, always

    distribution = trend & climax & volume_at_top & under_water & momentum_down

    # rule 3: out from a distribution day until momentum is back (or a new trend starts)
    if momentum_back is None:                                    # RSI switched off: re-enter when the pattern ends
        out = distribution
    else:
        state = pd.Series(np.nan, index=cal)
        state[momentum_back | new_trend] = 1.0
        state[distribution] = 0.0
        out = ~(state.ffill().fillna(1.0) > 0.5)

    return (trend & ~out).astype(float)


def compute_target_weights(md) -> pd.DataFrame:
    p = PARAMS
    invested = exposure(md)
    stocks = [t for t in md.tickers if t != ETF_TICKER]
    members = md.member[stocks].astype(float)
    sleeve = members.div(members.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    W = pd.DataFrame(0.0, index=md.calendar, columns=md.tickers)
    W[stocks] = sleeve.mul(invested * (1.0 - p["etf_share"]), axis=0)
    W[ETF_TICKER] = invested - W[stocks].sum(axis=1)
    return W
