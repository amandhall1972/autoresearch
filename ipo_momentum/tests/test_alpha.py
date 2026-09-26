"""AlphaEngine: breakout logic, indicator definitions, and freedom from look-ahead."""
import pandas as pd
import pytest

import engine
from conftest import ist, make_bars

BASE = [100 + (i % 5) for i in range(150)]          # closes 100..104 -> base high 104.5


def breakout_frame(breakout_close=106.0, breakout_volume=500_000, pre=20):
    closes = BASE + [102.0] * pre + [breakout_close]
    volumes = [100_000] * (len(closes) - 1) + [breakout_volume]
    return make_bars(closes, volumes)


def test_breakout_fires_with_documented_stop_and_target():
    df = breakout_frame()
    alpha = engine.AlphaEngine()
    sig = alpha.evaluate("IPO", df)
    assert sig is not None and sig.reason == "IPO_BASE_BREAKOUT"
    assert sig.entry_price == 106.0
    assert sig.bar_time == df.index[-1]
    # True range: 13 quiet bars of 1.0 plus the breakout bar's 5.0 -> ATR = 18 / 14.
    atr = 18 / 14
    assert sig.stop_loss == pytest.approx(106.0 - 1.5 * atr)      # the ATR stop is closer than AVWAP here
    assert sig.target == pytest.approx(106.0 + 3 * 1.5 * atr)


def test_no_breakout_until_the_base_is_complete():
    # 150 bars that end in a monster up-bar: the base (first 150 bars) is not finished yet.
    closes = [100.0] * 149 + [150.0]
    df = make_bars(closes, [100_000] * 149 + [10_000_000])
    assert engine.AlphaEngine().evaluate("IPO", df) is None
    assert engine.AlphaEngine().scan("IPO", df) == []


def test_base_is_the_first_150_bars_only_and_excludes_the_latest_bar():
    df = breakout_frame()
    ind = engine.AlphaEngine().indicators(df)
    assert ind["Base_High"].iloc[-1] == pytest.approx(104.5)
    assert df["High"].iloc[-1] > 104.5                               # latest bar is above, yet not in the base


def test_rvol_baseline_excludes_the_current_bar():
    df = breakout_frame(breakout_volume=500_000)
    ind = engine.AlphaEngine().indicators(df)
    # 500k against the *previous* 20 bars of 100k is exactly 5x (the v1.0 formula gave 4.17x).
    assert ind["RVOL"].iloc[-1] == pytest.approx(5.0)


def test_weak_volume_blocks_the_breakout():
    df = breakout_frame(breakout_volume=150_000)                      # 1.5x < 2.0 threshold
    assert engine.AlphaEngine().evaluate("IPO", df) is None


def test_atr_uses_true_range_so_gaps_count():
    df = make_bars([100.0, 100.0])
    df.iloc[1] = [110.0, 110.0, 110.0, 110.0, 1.0]                     # flat bar that gapped up 10
    ind = engine.AlphaEngine(atr_period=1).indicators(df)
    assert ind["ATR"].iloc[1] == pytest.approx(10.0)                   # High-Low alone would say 0


def test_stop_uses_avwap_when_it_is_the_closer_floor():
    # Wide-ranging bars (ATR ~8.4) around a base high of 114; breakout close 116, AVWAP ~106.
    closes = [100.0 if i % 2 else 110.0 for i in range(150)] + [110.0] * 20 + [116.0]
    df = make_bars(closes, [100_000] * 170 + [600_000], spread=4.0)
    alpha = engine.AlphaEngine()
    ind = alpha.indicators(df).iloc[-1]
    assert ind["Base_High"] == pytest.approx(114.0)
    assert ind["AVWAP"] > ind["Close"] - 1.5 * ind["ATR"]              # precondition: AVWAP is closer
    sig = alpha.evaluate("IPO", df)
    assert sig is not None
    assert sig.stop_loss == pytest.approx(ind["AVWAP"])
    assert sig.target == pytest.approx(116.0 + 3 * (116.0 - ind["AVWAP"]))


def test_only_the_first_close_above_the_base_is_a_breakout():
    closes = BASE + [102.0] * 20 + [106.0, 107.0]
    df = make_bars(closes, [100_000] * 170 + [500_000, 900_000])
    alpha = engine.AlphaEngine()
    assert [s.entry_price for s in alpha.scan("IPO", df)] == [106.0]
    assert alpha.evaluate("IPO", df) is None                           # prev close already above base


def test_price_below_avwap_blocks_the_breakout():
    # After the base, 20 heavy bars print long upper wicks to 125 but close at 104 (inside the base),
    # which lifts AVWAP above the breakout close of 106.
    closes = BASE + [104.0] * 20 + [106.0]
    df = make_bars(closes, [100_000] * 150 + [5_000_000] * 20 + [12_000_000])
    df.iloc[150:170, df.columns.get_loc("High")] = 125.0
    ind = engine.AlphaEngine().indicators(df).iloc[-1]
    assert ind["Close"] > ind["Base_High"] and ind["RVOL"] > 2.0      # every other condition holds
    assert ind["AVWAP"] > ind["Close"]
    assert engine.AlphaEngine().evaluate("IPO", df) is None


def test_zero_volume_history_never_signals_or_divides_by_zero():
    df = breakout_frame()
    df["Volume"] = 0.0
    ind = engine.AlphaEngine().indicators(df)
    assert ind["AVWAP"].isna().all() and ind["RVOL"].isna().all()
    assert engine.AlphaEngine().evaluate("IPO", df) is None


def test_real_swiggy_fixture_has_exactly_one_breakout(real_bars):
    """Pinned regression on real prints: 2026-09-23 09:15 IST opened above the 285.55 base."""
    sigs = engine.AlphaEngine().scan("SWIGGY", real_bars)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.bar_time == pd.Timestamp(ist(2026, 9, 23, 9, 15))
    assert s.entry_price == pytest.approx(286.65)
    assert s.stop_loss == pytest.approx(284.9625)
    assert s.target == pytest.approx(291.7125)


def test_walk_forward_equals_vectorized_scan_on_real_data(real_bars):
    """No look-ahead: evaluating each prefix bar-by-bar reproduces the vectorized scan exactly."""
    alpha = engine.AlphaEngine()
    walk = [s for i in range(alpha.base_bars, len(real_bars)) if (s := alpha.evaluate("SWIGGY", real_bars.iloc[: i + 1]))]
    scan = alpha.scan("SWIGGY", real_bars)
    assert [(s.bar_time, s.entry_price, s.stop_loss, s.target) for s in walk] == \
           [(s.bar_time, s.entry_price, s.stop_loss, s.target) for s in scan]


def test_future_bars_do_not_change_past_indicators(real_bars):
    alpha = engine.AlphaEngine()
    full = alpha.indicators(real_bars)
    part = alpha.indicators(real_bars.iloc[:400])
    pd.testing.assert_frame_equal(full.iloc[:400], part)


def session_bars(days, open_volume=500_000, other_volume=100_000):
    """Full NSE sessions (09:15-15:25) whose opening bar is always 5x heavier, like real tape."""
    idx, vols = [], []
    for d in days:
        for k in range(75):
            idx.append(ist(2026, 9, d, 9, 15) + pd.Timedelta(minutes=5 * k))
            vols.append(open_volume if k == 0 else other_volume)
    df = make_bars([100.0] * len(idx), vols)
    df.index = pd.DatetimeIndex(idx, name="datetime")
    return df


def test_time_of_day_rvol_removes_the_opening_bar_bias():
    df = session_bars([21, 22, 23])
    open_bar = pd.Timestamp(ist(2026, 9, 23, 9, 15))
    trailing = engine.AlphaEngine(rvol_mode="trailing").indicators(df)["RVOL"]
    tod = engine.AlphaEngine(rvol_mode="time_of_day").indicators(df)["RVOL"]
    assert trailing[open_bar] == pytest.approx(5.0)                        # every open looks like a volume surge
    assert tod[open_bar] == pytest.approx(1.0)                             # ...but it is a normal open
    assert tod[pd.Timestamp(ist(2026, 9, 23, 11, 0))] == pytest.approx(1.0)


def test_time_of_day_rvol_falls_back_to_trailing_for_young_listings():
    df = session_bars([21])
    tod = engine.AlphaEngine(rvol_mode="time_of_day").indicators(df)["RVOL"]
    trailing = engine.AlphaEngine(rvol_mode="trailing").indicators(df)["RVOL"]
    pd.testing.assert_series_equal(tod, trailing)                          # no prior sessions yet


def test_time_of_day_rvol_is_causal_and_keeps_the_real_breakout(real_bars):
    alpha = engine.AlphaEngine(rvol_mode="time_of_day")
    pd.testing.assert_frame_equal(alpha.indicators(real_bars).iloc[:500], alpha.indicators(real_bars.iloc[:500]))
    ind = alpha.indicators(real_bars)
    assert ind.loc[pd.Timestamp(ist(2026, 9, 23, 9, 15)), "RVOL"] == pytest.approx(2.394, abs=1e-3)
    assert [s.bar_time for s in alpha.scan("SWIGGY", real_bars)] == [pd.Timestamp(ist(2026, 9, 23, 9, 15))]


def test_unknown_rvol_mode_is_rejected():
    with pytest.raises(ValueError):
        engine.AlphaEngine(rvol_mode="vibes")


def test_vendor_gap_bars_are_left_out_of_the_volume_baseline():
    df = breakout_frame(breakout_volume=500_000)
    gap = df.index[-5]
    df.loc[gap, "Volume"] = 0.0                                            # price moved, no volume: a vendor gap
    assert df.loc[gap, "High"] > df.loc[gap, "Low"]
    ind = engine.AlphaEngine().indicators(df)
    assert ind["RVOL"].iloc[-1] == pytest.approx(5.0)                      # 500k / mean of the 19 real bars
