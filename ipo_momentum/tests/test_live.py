"""LiveTickAdapter: tick -> bar synthesis, Kite payloads, the bar clock, and loop resilience."""
import asyncio
import sys
import threading
import time
import types
from datetime import datetime, timedelta

import pandas as pd
import pytest

import engine
from conftest import ist, make_bars

PRE_OPEN = ist(2026, 9, 28, 9, 0)


def adapter(history=None, started_at=PRE_OPEN, token_map=None, alpha=None):
    state = {} if history is None else {"SWIGGY": history}
    return engine.LiveTickAdapter(state, alpha or engine.AlphaEngine(), asyncio.Queue(), loop=None,
                                  token_map=token_map, started_at=started_at)


def tick(price, volume, ts, symbol="SWIGGY"):
    return engine.Tick(symbol, price, volume, ts)


def test_ticks_build_one_ohlcv_bar_that_closes_on_the_next_bucket():
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)))
    for p, v, s in [(100.0, 10, 10), (102.0, 5, 60), (99.0, 7, 180), (101.0, 3, 299)]:
        a.on_tick(tick(p, v, ist(2026, 9, 28, 9, 15) + timedelta(seconds=s)))
    assert len(a.market_state["SWIGGY"]) == 5                            # still forming
    a.on_tick(tick(101.5, 1, ist(2026, 9, 28, 9, 20, 1)))
    df = a.market_state["SWIGGY"]
    assert len(df) == 6
    assert df.index[-1] == pd.Timestamp(ist(2026, 9, 28, 9, 15))
    assert df.iloc[-1].tolist() == [100.0, 102.0, 99.0, 101.0, 25.0]
    assert df.index.is_monotonic_increasing and str(df.index.tz) == str(engine.IST)
    assert (df.dtypes == "float64").all()


def test_bar_clock_closes_a_bar_that_gets_no_further_ticks():
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 15, 25, 30)))            # the session's final bar
    a.flush_due_bars(ist(2026, 9, 28, 15, 30, 1))                         # inside the 2 s grace
    assert "SWIGGY" in a.current_bars
    a.flush_due_bars(ist(2026, 9, 28, 15, 30, 2))
    assert "SWIGGY" not in a.current_bars
    assert a.market_state["SWIGGY"].index[-1] == pd.Timestamp(ist(2026, 9, 28, 15, 25))


def test_first_bar_is_discarded_when_the_engine_started_mid_bar():
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)), started_at=ist(2026, 9, 28, 10, 2))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 10, 2, 30)))              # joins the 10:00 bar late
    a.on_tick(tick(101.0, 10, ist(2026, 9, 28, 10, 5, 0)))
    a.on_tick(tick(102.0, 10, ist(2026, 9, 28, 10, 10, 0)))
    assert list(a.market_state["SWIGGY"].index[-2:]) == [pd.Timestamp(ist(2026, 9, 25, 15, 20)),
                                                          pd.Timestamp(ist(2026, 9, 28, 10, 5))]


def test_first_bar_is_kept_when_the_engine_started_before_it():
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 9, 15, 0)))
    a.on_tick(tick(101.0, 10, ist(2026, 9, 28, 9, 20, 0)))
    assert a.market_state["SWIGGY"].index[-1] == pd.Timestamp(ist(2026, 9, 28, 9, 15))


def test_late_and_out_of_order_ticks_are_dropped_not_merged():
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 9, 15, 5)))
    a.on_tick(tick(101.0, 10, ist(2026, 9, 28, 9, 20, 5)))              # closes 09:15
    a.on_tick(tick(250.0, 999, ist(2026, 9, 28, 9, 19, 59)))            # late print for a closed bar
    a.on_tick(tick(101.0, 10, ist(2026, 9, 28, 9, 25, 5)))              # closes 09:20
    df = a.market_state["SWIGGY"]
    assert a.dropped_ticks == 1
    assert df.index[-2:].tolist() == [pd.Timestamp(ist(2026, 9, 28, 9, 15)), pd.Timestamp(ist(2026, 9, 28, 9, 20))]
    assert df["High"].max() < 250.0 and not df.index.duplicated().any()


def test_kite_full_mode_payload_uses_token_map_exchange_time_and_cumulative_volume():
    a = adapter(history=make_bars([280.0] * 5, start=ist(2026, 9, 25, 15, 0)), token_map={1234: "SWIGGY"})
    epoch = ist(2026, 9, 28, 9, 16, 5).timestamp()
    kite_tick = {"tradable": True, "mode": "full", "instrument_token": 1234, "last_price": 280.5,
                 "last_traded_quantity": 5, "volume_traded": 1000,
                 # KiteTicker builds naive host-local datetimes with datetime.fromtimestamp(), so the test must too.
                 "exchange_timestamp": datetime.fromtimestamp(epoch),  # noqa: DTZ006
                 "last_trade_time": datetime.fromtimestamp(epoch)}  # noqa: DTZ006
    t1 = a._normalize(kite_tick)
    assert t1.symbol == "SWIGGY" and t1.timestamp == ist(2026, 9, 28, 9, 16, 5)
    t2 = a._normalize({**kite_tick, "last_price": 281.0, "volume_traded": 1600,
                       "exchange_timestamp": datetime.fromtimestamp(epoch + 60)})  # noqa: DTZ006
    for t in (t1, t2):
        a.on_tick(t)
    a.flush_due_bars(ist(2026, 9, 28, 9, 21))
    bar = a.market_state["SWIGGY"].iloc[-1]
    assert bar["Volume"] == 1600                                          # 1000 + (1600 - 1000), not 5 + 5


def test_zeroed_exchange_time_falls_back_to_receive_time():
    a = adapter(token_map={1234: "SWIGGY"})
    t = a._normalize({"instrument_token": 1234, "last_price": 280.5, "volume_traded": 10,
                      "exchange_timestamp": datetime.fromtimestamp(0)})  # noqa: DTZ006 - what the SDK yields for 0
    assert abs((t.timestamp - datetime.now(engine.IST)).total_seconds()) < 5


def test_unknown_instrument_tokens_are_dropped_instead_of_crashing():
    async def scenario():
        a = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), asyncio.get_running_loop(),
                                   token_map={1234: "SWIGGY"})
        a.broker_on_ticks(None, [{"instrument_token": 999, "last_price": 10.0}])
        await asyncio.sleep(0)
        return a
    a = asyncio.run(scenario())
    assert a.dropped_ticks == 1 and a.tick_queue.empty()


def test_ltp_mode_ticks_carry_no_volume():
    a = adapter(token_map={1234: "SWIGGY"})
    t = a._normalize({"tradable": True, "mode": "ltp", "instrument_token": 1234, "last_price": 280.5})
    assert t.volume == 0 and t.cumulative_volume is None                  # v1.0 invented 100 shares


def test_cumulative_volume_counter_restarts_each_session():
    a = adapter(started_at=ist(2026, 9, 25, 8, 55))                        # running through both opens
    t = engine.Tick("SWIGGY", 100.0, 0, ist(2026, 9, 25, 15, 25), cumulative_volume=9_000_000)
    assert a._traded_quantity(t) == (9_000_000, False)
    t = engine.Tick("SWIGGY", 100.0, 0, ist(2026, 9, 28, 9, 15, 1), cumulative_volume=40_000)
    assert a._traded_quantity(t) == (40_000, False)                        # not negative, not ignored


def test_joining_mid_session_does_not_dump_the_days_volume_into_one_bar():
    """Started 10:04:59; the first print (10:05:01) carries 5M shares traded since 09:15."""
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)), token_map={1234: "SWIGGY"},
                started_at=ist(2026, 9, 28, 10, 4, 59))
    for secs, cum in [(1, 5_000_000), (90, 5_004_000), (200, 5_010_000), (330, 5_012_000), (420, 5_020_000)]:
        a.on_tick(engine.Tick("SWIGGY", 280.0, 0, ist(2026, 9, 28, 10, 5) + timedelta(seconds=secs), cumulative_volume=cum))
    a.flush_due_bars(ist(2026, 9, 28, 10, 16))
    today = a.market_state["SWIGGY"][a.market_state["SWIGGY"].index >= pd.Timestamp(ist(2026, 9, 28))]
    # The 10:05 bar's head (10:05:00-10:05:01) went into the baseline, so that bar is discarded (v1.4 kept it
    # short); the next bar gets only what traded while we watched.
    assert today.index.tolist() == [pd.Timestamp(ist(2026, 9, 28, 10, 10))] and today["Volume"].tolist() == [10_000]


def test_ticks_for_a_symbol_without_history_are_ignored_not_fatal(caplog):
    # v1.0 raised KeyError here and the shared aggregator died for every symbol. Accumulating bars
    # instead would later trade a base anchored at engine start-up, so the symbol is ignored.
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)))
    for m in (15, 20, 25):
        a.on_tick(tick(50.0, 10, ist(2026, 9, 28, 9, m, 1), symbol="NEWIPO"))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 9, 15, 1)))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 9, 20, 1)))
    assert "NEWIPO" not in a.market_state and a.dropped_ticks == 3
    assert a.market_state["SWIGGY"].index[-1] == pd.Timestamp(ist(2026, 9, 28, 9, 15))   # healthy symbol unaffected
    assert caplog.text.count("[NEWIPO] Ticks ignored: no established history") == 1


def test_pre_open_and_post_close_prints_are_not_bars_and_auction_volume_lands_at_the_open():
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)), started_at=ist(2026, 9, 28, 8, 55))
    pre_open = engine.Tick("SWIGGY", 101.0, 0, ist(2026, 9, 28, 9, 7, 30), cumulative_volume=250_000)
    first = engine.Tick("SWIGGY", 101.5, 0, ist(2026, 9, 28, 9, 15, 2), cumulative_volume=260_000)
    for t in (pre_open, first):
        a.on_tick(t)
    a.on_tick(tick(101.0, 10, ist(2026, 9, 28, 15, 31, 0)))              # closing-session print
    a.flush_due_bars(ist(2026, 9, 28, 9, 21))
    df = a.market_state["SWIGGY"]
    assert df.index[-1] == pd.Timestamp(ist(2026, 9, 28, 9, 15)) and a.dropped_ticks == 2
    assert df.iloc[-1]["Volume"] == 260_000 and df.iloc[-1]["Open"] == 101.5   # auction volume, first in-session price


def test_live_breakout_bar_close_emits_a_signal():
    closes = [100 + (i % 5) for i in range(150)] + [102.0] * 20
    a = adapter(history=make_bars(closes, start=ist(2026, 9, 1, 9, 15)), started_at=ist(2026, 9, 2, 8, 0))
    t0 = engine.next_session_open(a.market_state["SWIGGY"].index[-1])
    a.on_tick(tick(103.0, 200_000, t0))
    a.on_tick(tick(106.0, 400_000, t0 + timedelta(minutes=4)))
    a.on_tick(tick(106.0, 1_000, t0 + timedelta(minutes=5)))
    sig = a.oms_queue.get_nowait()
    assert sig.symbol == "SWIGGY" and sig.entry_price == 106.0 and sig.bar_time == t0


def test_process_ticks_survives_an_exception_and_keeps_counting_tasks():
    async def scenario():
        alpha = engine.AlphaEngine()
        calls = {"n": 0}

        def flaky(symbol, df):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return None

        alpha.evaluate = flaky
        history = {"X": make_bars([10.0] * 5, start=ist(2026, 9, 25, 15, 5))}      # through the 15:25 bar
        a = engine.LiveTickAdapter(history, alpha, asyncio.Queue(), asyncio.get_running_loop(), started_at=PRE_OPEN)
        worker = asyncio.create_task(a.process_ticks())
        a.broker_on_ticks(None, [{"symbol": "X", "price": 10.0, "volume": 1, "timestamp": ist(2026, 9, 28, 9, 15 + 5 * i)}
                                 for i in range(4)])
        await asyncio.wait_for(a.tick_queue.join(), timeout=2)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        return a, calls["n"]

    a, evaluations = asyncio.run(scenario())
    assert evaluations == 3                                               # bars 1-3 closed; the first raised
    # The raising evaluation still kept its bar and did not swallow the tick that closed it.
    assert a.market_state["X"].index[-3:].tolist() == [pd.Timestamp(ist(2026, 9, 28, 9, m)) for m in (15, 20, 25)]


def test_broker_callback_from_a_foreign_thread_is_delivered_to_the_loop():
    async def scenario():
        a = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), asyncio.get_running_loop())
        th = threading.Thread(target=a.broker_on_ticks,
                              args=(None, [{"symbol": "X", "price": 10.0, "volume": 1, "timestamp": ist(2026, 9, 28, 9, 15)}]))
        th.start()
        th.join()
        return await asyncio.wait_for(a.tick_queue.get(), timeout=2)

    t = asyncio.run(scenario())
    assert t.symbol == "X" and t.price == 10.0


def test_bar_clock_task_flushes_on_its_own():
    async def scenario():
        a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)))
        a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 9, 15, 1)))
        clock = asyncio.create_task(a.bar_clock(interval=0.01, clock=lambda: ist(2026, 9, 28, 9, 21)))
        await asyncio.sleep(0.1)
        clock.cancel()
        await asyncio.gather(clock, return_exceptions=True)
        return a

    a = asyncio.run(scenario())
    assert a.market_state["SWIGGY"].index[-1] == pd.Timestamp(ist(2026, 9, 28, 9, 15))


# ---------------------------------------------------------------- feed gaps, blind spots and back-fill
def cum_tick(price, cum, ts, symbol="SWIGGY"):
    return engine.Tick(symbol, price, 0, ts, cumulative_volume=cum)


def session_history(day=24, closes=None):
    """A full 09:15-15:25 session of 5m bars on 2026-09-<day> (75 bars) at 100k shares each."""
    closes = closes or [100.0] * 75
    df = make_bars(closes, start=ist(2026, 9, day, 9, 15))
    return df


def test_reconnect_rebaselines_volume_and_discards_the_blind_bar():
    """v1.1 credited all volume traded during a feed outage to the first bar after it: a fake RVOL spike."""
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(cum_tick(100.0, 1_000_000, ist(2026, 9, 25, 10, 0, 10)))
    a.on_tick(cum_tick(100.2, 1_100_000, ist(2026, 9, 25, 10, 2, 0)))
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 12, 0))                     # outage 10:02 -> 10:12
    a.on_tick(cum_tick(101.0, 2_500_000, ist(2026, 9, 25, 10, 12, 5)))     # 1.4M traded while blind
    a.on_tick(cum_tick(101.5, 2_600_000, ist(2026, 9, 25, 10, 15, 3)))
    a.flush_due_bars(ist(2026, 9, 25, 10, 21))
    df = a.market_state["SWIGGY"]
    today = df[df.index >= pd.Timestamp(ist(2026, 9, 25))]
    assert today.index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 15))]   # 10:00 and 10:10 spanned the gap
    assert today["Volume"].tolist() == [100_000.0]                              # not 1,500,000


def test_a_late_feed_connect_does_not_dump_the_days_volume_into_one_bar():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 5))
    a.mark_feed_reset(at=ist(2026, 9, 25, 11, 0, 0))                       # the websocket only got through at 11:00
    a.on_tick(cum_tick(100.0, 4_200_000, ist(2026, 9, 25, 11, 0, 5)))      # re-baselines: 11:00 is blind
    a.on_tick(cum_tick(100.1, 4_212_000, ist(2026, 9, 25, 11, 3, 0)))
    a.on_tick(cum_tick(100.2, 4_215_000, ist(2026, 9, 25, 11, 5, 10)))
    a.on_tick(cum_tick(100.3, 4_220_000, ist(2026, 9, 25, 11, 7, 0)))
    a.flush_due_bars(ist(2026, 9, 25, 11, 11))
    today = a.market_state["SWIGGY"][a.market_state["SWIGGY"].index >= pd.Timestamp(ist(2026, 9, 25))]
    assert today.index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 11, 5))] and today["Volume"].tolist() == [8_000]


def test_a_counter_going_backwards_is_rebaselined_not_reset_to_zero():
    a = adapter(started_at=ist(2026, 9, 25, 9, 0))
    assert a._traded_quantity(cum_tick(100.0, 1_000_000, ist(2026, 9, 25, 10, 0))) == (1_000_000, False)
    assert a._traded_quantity(cum_tick(100.0, 999_990, ist(2026, 9, 25, 10, 1))) == (0, True)   # v1.1: 999,990
    # v1.2 adopted the glitch value as the baseline and re-counted the dip (510); v1.3 dropped the 500.
    assert a._traded_quantity(cum_tick(100.0, 1_000_500, ist(2026, 9, 25, 10, 2))) == (500, False)
    assert a._traded_quantity(cum_tick(100.0, 1_000_800, ist(2026, 9, 25, 10, 3))) == (300, False)


def test_quote_updates_without_a_trade_do_not_create_bars():
    """In full mode Kite also sends depth changes; v1.1 turned those into flat zero-volume bars."""
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(cum_tick(100.0, 500_000, ist(2026, 9, 25, 10, 0, 5)))
    a.on_tick(cum_tick(100.0, 500_000, ist(2026, 9, 25, 10, 6, 0)))        # depth update, nothing traded
    a.on_tick(cum_tick(100.0, 500_000, ist(2026, 9, 25, 10, 12, 0)))
    a.flush_due_bars(ist(2026, 9, 25, 10, 30))
    df = a.market_state["SWIGGY"]
    assert df[df.index >= pd.Timestamp(ist(2026, 9, 25))].index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 0))]


def test_a_late_print_does_not_move_the_volume_baseline():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(cum_tick(100.0, 1_000_000, ist(2026, 9, 25, 10, 0, 10)))
    a.on_tick(cum_tick(100.0, 1_100_000, ist(2026, 9, 25, 10, 4, 50)))
    a.flush_due_bars(ist(2026, 9, 25, 10, 5, 2))                           # closes 10:00
    a.on_tick(cum_tick(100.0, 1_160_000, ist(2026, 9, 25, 10, 4, 59)))     # arrives late: dropped
    a.on_tick(cum_tick(100.0, 1_161_000, ist(2026, 9, 25, 10, 5, 4)))
    a.flush_due_bars(ist(2026, 9, 25, 10, 11))
    assert a.market_state["SWIGGY"]["Volume"].iloc[-2:].tolist() == [1_100_000.0, 61_000.0]   # v1.1: 1,000


def test_market_time_follows_accepted_ticks_only():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 25, 10, 0, 10)))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 25, 9, 7, 0)))                  # pre-open: ignored
    assert a.market_time == ist(2026, 9, 25, 10, 0, 10)


class RecordingAlpha(engine.AlphaEngine):
    def __init__(self):
        super().__init__()
        self.evaluated = []

    def evaluate(self, symbol, df):
        self.evaluated.append((df.index[-2], df.index[-1]))
        return None


def test_a_hole_is_backfilled_from_the_broker_before_the_next_bar_is_evaluated():
    history = session_history(24)
    history = pd.concat([history, make_bars([100.0] * 9, start=ist(2026, 9, 25, 9, 15))])   # today 09:15-09:55
    fetched_bar = make_bars([100.5], start=ist(2026, 9, 25, 10, 0))
    calls, seen = [], []

    async def backfill(sym, start, end):
        calls.append((sym, start, end))
        return fetched_bar

    async def scenario():
        alpha = RecordingAlpha()
        a = engine.LiveTickAdapter({"SWIGGY": history}, alpha, asyncio.Queue(), asyncio.get_running_loop(),
                                   started_at=ist(2026, 9, 25, 10, 2, 40), backfill=backfill,
                                   bar_listeners=[lambda s, ts, b: seen.append(ts)])
        a.on_tick(tick(100.4, 10, ist(2026, 9, 25, 10, 2, 45)))            # joins the 10:00 bar late: discarded
        a.on_tick(tick(100.6, 10, ist(2026, 9, 25, 10, 5, 1)))
        a.on_tick(tick(100.7, 10, ist(2026, 9, 25, 10, 10, 1)))            # closes 10:05 -> hole at 10:00
        await asyncio.sleep(0.05)
        return a, alpha

    a, alpha = asyncio.run(scenario())
    assert calls == [("SWIGGY", ist(2026, 9, 25, 10, 0), ist(2026, 9, 25, 10, 5))]
    idx = a.market_state["SWIGGY"].index
    assert idx[-2:].tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 0)), pd.Timestamp(ist(2026, 9, 25, 10, 5))]
    assert alpha.evaluated == [(pd.Timestamp(ist(2026, 9, 25, 10, 0)), pd.Timestamp(ist(2026, 9, 25, 10, 5)))]
    assert seen == [pd.Timestamp(ist(2026, 9, 25, 10, 0)), ist(2026, 9, 25, 10, 5)]   # listeners in time order


def test_without_a_backfill_source_a_bar_after_a_hole_is_not_evaluated(caplog):
    """Evaluating across a hole would report a 'first crossing' one bar late at a worse price."""
    history = pd.concat([session_history(24), make_bars([100.0] * 9, start=ist(2026, 9, 25, 9, 15))])
    alpha = RecordingAlpha()
    a = engine.LiveTickAdapter({"SWIGGY": history}, alpha, asyncio.Queue(), loop=None,
                               started_at=ist(2026, 9, 25, 10, 2, 40))
    a.on_tick(tick(100.4, 10, ist(2026, 9, 25, 10, 2, 45)))
    a.on_tick(tick(100.6, 10, ist(2026, 9, 25, 10, 5, 1)))
    a.on_tick(tick(100.7, 10, ist(2026, 9, 25, 10, 10, 1)))
    assert alpha.evaluated == [] and "Bars 10:00-10:05 are missing" in caplog.text
    a.on_tick(tick(100.8, 10, ist(2026, 9, 25, 10, 15, 1)))               # contiguous again: evaluated
    assert alpha.evaluated == [(pd.Timestamp(ist(2026, 9, 25, 10, 5)), pd.Timestamp(ist(2026, 9, 25, 10, 10)))]


def test_a_reconnect_marks_the_forming_bar_incomplete():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 25, 10, 0, 10)))
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 3))
    assert a.current_bars["SWIGGY"]["partial"] is True


def test_a_feed_drop_discards_the_bar_it_cut_short_and_every_bar_until_the_reconnect():
    # v1.2 only learned of an outage at the reconnect, so a bar cut short that closed before then was kept.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 25, 10, 0, 10)))
    a.mark_feed_down()                                                    # the websocket closed at ~10:01
    a.flush_due_bars(ist(2026, 9, 25, 10, 5, 3))
    a.on_tick(tick(100.2, 10, ist(2026, 9, 25, 10, 6, 0)))               # queued before the close was seen
    a.flush_due_bars(ist(2026, 9, 25, 10, 10, 3))
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 12))
    a.on_tick(tick(100.4, 10, ist(2026, 9, 25, 10, 15, 5)))
    a.flush_due_bars(ist(2026, 9, 25, 10, 20, 3))
    df = a.market_state["SWIGGY"]
    assert df[df.index >= pd.Timestamp(ist(2026, 9, 25))].index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 15))]


def test_a_live_bar_closed_by_the_clock_needs_the_feed_alive_to_its_end():
    """No tick in a bucket's tail is normal for an illiquid name; no message at all (heartbeats
    included) means the feed may have missed trades, so that bar cannot be trusted."""
    a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(), loop=None,
                               started_at=ist(2026, 9, 25, 9, 0), require_feed_liveness=True)
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 59))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 25, 10, 0, 10)))
    a.note_feed_alive(at=ist(2026, 9, 25, 10, 3))                         # then silence
    a.flush_due_bars(ist(2026, 9, 25, 10, 5, 3))
    a.on_tick(tick(100.2, 10, ist(2026, 9, 25, 10, 5, 10)))
    a.note_feed_alive(at=ist(2026, 9, 25, 10, 10, 1))                     # heartbeats past the bucket's end
    a.flush_due_bars(ist(2026, 9, 25, 10, 10, 3))
    df = a.market_state["SWIGGY"]
    assert df[df.index >= pd.Timestamp(ist(2026, 9, 25))].index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 5))]


def test_a_failed_kite_backfill_leaves_the_bar_unevaluated(fast_sleep, caplog):
    class FailingKite:
        def instruments(self, exchange=None):
            return [{"tradingsymbol": "SWIGGY", "instrument_token": 1234, "tick_size": 0.05}]

        def historical_data(self, **kwargs):
            raise ConnectionError("gateway timeout")

    kite = engine.ZerodhaKiteAdapter("k", "t", kite=FailingKite())
    history = pd.concat([session_history(24), make_bars([100.0] * 9, start=ist(2026, 9, 25, 9, 15))])

    async def scenario():
        await kite.boot()
        alpha = RecordingAlpha()
        a = engine.LiveTickAdapter({"SWIGGY": history}, alpha, asyncio.Queue(), asyncio.get_running_loop(),
                                   started_at=ist(2026, 9, 25, 10, 2, 40), backfill=kite.backfill)   # as main() wires it
        a.on_tick(tick(100.4, 10, ist(2026, 9, 25, 10, 2, 45)))
        a.on_tick(tick(100.6, 10, ist(2026, 9, 25, 10, 5, 1)))
        a.on_tick(tick(100.7, 10, ist(2026, 9, 25, 10, 10, 1)))            # closes 10:05 -> hole at 10:00
        await asyncio.gather(*a._backfills)
        return alpha

    alpha = asyncio.run(scenario())
    assert alpha.evaluated == [] and "Back-fill of 10:00-10:05 failed" in caplog.text   # v1.2 evaluated it


def test_a_rebaselining_print_after_a_reconnect_opens_no_bar():
    # v1.2 opened a flat zero-volume bar on it, and depth updates kept that bar alive.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 0))
    a.on_tick(cum_tick(100.0, 500_000, ist(2026, 9, 25, 10, 0, 5)))        # re-baselines only
    a.on_tick(cum_tick(100.0, 500_000, ist(2026, 9, 25, 10, 6, 0)))        # depth update
    a.on_tick(cum_tick(100.3, 500_100, ist(2026, 9, 25, 10, 7, 0)))        # a trade
    a.flush_due_bars(ist(2026, 9, 25, 10, 20))
    df = a.market_state["SWIGGY"]
    today = df[df.index >= pd.Timestamp(ist(2026, 9, 25))]
    assert today.index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 5))] and today["Volume"].tolist() == [100.0]


class SessionClock(datetime):
    """Wall clock inside Monday's session."""
    FIXED = datetime(2026, 9, 28, 11, 0, tzinfo=engine.IST)

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED.astimezone(tz) if tz else cls.FIXED.replace(tzinfo=None)


class EveningClock(SessionClock):
    FIXED = datetime(2026, 9, 28, 18, 0, tzinfo=engine.IST)


def test_a_silent_websocket_during_the_session_stops_the_engine(monkeypatch):
    # KiteTicker's ping loop never sends a ping, so a half-open socket stays "connected" forever.
    monkeypatch.setattr(engine, "datetime", SessionClock)
    a = adapter(history=session_history(24))
    a.on_tick(tick(100.0, 10, ist(2026, 9, 28, 10, 59, 30)))
    a.note_feed_alive(at=SessionClock.FIXED - timedelta(seconds=20))
    with pytest.raises(RuntimeError, match="no market data"):
        asyncio.run(engine._watch_feed(asyncio.Event(), a, stall_after=15, check_every=0.01))
    assert a.current_bars["SWIGGY"]["partial"] is True


def test_the_feed_watchdog_tolerates_quiet_evenings_and_fresh_heartbeats(monkeypatch):
    async def watch_briefly(adapter_):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(engine._watch_feed(asyncio.Event(), adapter_, stall_after=15, check_every=0.01), 0.1)

    monkeypatch.setattr(engine, "datetime", EveningClock)
    evening = adapter(history=session_history(24))
    evening.note_feed_alive(at=EveningClock.FIXED - timedelta(hours=2))
    asyncio.run(watch_briefly(evening))                                   # the exchange is closed: silence is fine
    monkeypatch.setattr(engine, "datetime", SessionClock)
    busy = adapter(history=session_history(24))
    busy.note_feed_alive(at=SessionClock.FIXED - timedelta(seconds=1))
    asyncio.run(watch_briefly(busy))


def test_consecutive_zeroed_packets_never_become_the_baseline():
    # v1.3 invalidated the baseline on a dip but adopted the next print unchecked: a second zeroed packet
    # became the baseline and the next good print was credited with the whole day's volume.
    a = adapter(started_at=ist(2026, 9, 25, 10, 58))
    q = [a._traded_quantity(cum_tick(100.0, c, ist(2026, 9, 25, 11, 0, s)))
         for s, c in [(1, 4_000_000), (2, 4_001_000), (3, 0), (4, 0), (5, 4_002_000), (6, 4_003_000)]]
    assert q == [(0, True), (1_000, False), (0, True), (0, True), (1_000, False), (1_000, False)]


def test_a_zeroed_first_packet_after_a_reconnect_is_not_the_baseline():
    a = adapter(started_at=ist(2026, 9, 25, 9, 0))
    a._traded_quantity(cum_tick(100.0, 4_000_000, ist(2026, 9, 25, 10, 0)))
    a.mark_feed_down()
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 2))
    assert a._traded_quantity(cum_tick(100.0, 0, ist(2026, 9, 25, 10, 2, 1))) == (0, True)
    assert a._traded_quantity(cum_tick(100.0, 4_011_000, ist(2026, 9, 25, 10, 2, 2))) == (0, True)   # v1.3: 4,011,000
    assert a._traded_quantity(cum_tick(100.0, 4_012_000, ist(2026, 9, 25, 10, 2, 3))) == (1_000, False)


def test_the_print_after_a_glitch_keeps_its_trade_and_price():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 58))   # joined mid-session
    a.on_tick(cum_tick(100.0, 990_000, ist(2026, 9, 25, 9, 59, 0)))            # re-baselines (09:55 is blind)
    a.on_tick(cum_tick(100.0, 1_000_000, ist(2026, 9, 25, 10, 0, 5)))
    a.on_tick(cum_tick(100.0, 1_010_000, ist(2026, 9, 25, 10, 1, 0)))
    a.on_tick(cum_tick(100.0, 0, ist(2026, 9, 25, 10, 2, 0)))                  # a zeroed packet
    a.on_tick(cum_tick(97.0, 1_060_000, ist(2026, 9, 25, 10, 3, 0)))           # a real 50k trade at 97
    a.flush_due_bars(ist(2026, 9, 25, 10, 5, 3))
    bar = a.market_state["SWIGGY"].iloc[-1]
    assert (bar["Low"], bar["Close"], bar["Volume"]) == (97.0, 97.0, 70_000.0)   # v1.3 lost the trade


def test_a_stall_across_a_bucket_end_does_not_credit_its_tail_twice():
    # The clock discards the stalled bar and the back-fill restores it; v1.3 also credited the stalled
    # tail to the next live bar, so the tail was in history twice. v1.4 re-baselined the counter but
    # kept the next bar with its head swallowed by the new baseline; that bar is now discarded and
    # back-filled too.
    history = pd.concat([session_history(24), make_bars([100.0] * 10, start=ist(2026, 9, 25, 9, 15))])
    candles = make_bars([99.0, 100.9], start=ist(2026, 9, 25, 10, 5))
    candles["Volume"] = [280_000.0, 20_000.0]                            # the exchange's 10:05 and 10:10 candles

    async def backfill(sym, start, end):
        return candles

    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": history}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), started_at=ist(2026, 9, 25, 9, 0),
                                   backfill=backfill, require_feed_liveness=True)
        a.mark_feed_reset(at=ist(2026, 9, 25, 10, 4, 30))
        a.on_tick(cum_tick(100.0, 1_000_000, ist(2026, 9, 25, 10, 4, 40)))  # re-baselines
        a.on_tick(cum_tick(99.5, 1_010_000, ist(2026, 9, 25, 10, 5, 10)))
        a.on_tick(cum_tick(99.0, 1_030_000, ist(2026, 9, 25, 10, 7, 0)))
        a.note_feed_alive(at=ist(2026, 9, 25, 10, 9, 56))                   # then the feed stalls
        a.flush_due_bars(ist(2026, 9, 25, 10, 10, 2))                        # 10:05 discarded
        a.on_tick(cum_tick(99.2, 1_280_000, ist(2026, 9, 25, 10, 9, 58)))   # the stalled 250k burst: late
        a.note_feed_alive(at=ist(2026, 9, 25, 10, 10, 3))
        a.on_tick(cum_tick(100.8, 1_285_000, ist(2026, 9, 25, 10, 10, 5)))  # re-baselines: 10:10 is blind
        a.on_tick(cum_tick(101.0, 1_300_000, ist(2026, 9, 25, 10, 11, 0)))
        a.on_tick(cum_tick(101.0, 1_301_000, ist(2026, 9, 25, 10, 15, 1)))  # closes (discards) 10:10
        a.on_tick(cum_tick(101.2, 1_321_000, ist(2026, 9, 25, 10, 17, 0)))
        a.on_tick(cum_tick(101.3, 1_322_000, ist(2026, 9, 25, 10, 20, 1)))  # closes 10:15: hole 10:05-10:15
        await asyncio.gather(*a._backfills)
        return a.market_state["SWIGGY"]

    df = asyncio.run(scenario())
    today = df[df.index >= pd.Timestamp(ist(2026, 9, 25, 10, 5))]
    assert today["Volume"].tolist() == [280_000.0, 20_000.0, 21_000.0]      # v1.3: 280,000 then 270,000


def test_bars_close_in_feed_time_when_the_feed_lags():
    # With the feed 3 s behind the host clock, a heartbeat received at 11:05:01 was sent at 11:04:58:
    # v1.3 took it as proof that the 11:00 bar was complete and dropped its last print as late.
    a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(), loop=None,
                               started_at=ist(2026, 9, 25, 9, 0), require_feed_liveness=True)
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 59))
    a.note_feed_lag(ist(2026, 9, 25, 11, 0, 3), 3.0)
    a.on_tick(tick(101.0, 100, ist(2026, 9, 25, 11, 0, 0)))
    a.note_feed_alive(at=ist(2026, 9, 25, 11, 5, 1))
    a.flush_due_bars(ist(2026, 9, 25, 11, 5, 2, 500000))
    assert "SWIGGY" in a.current_bars                                     # not yet due in feed time
    a.on_tick(tick(100.3, 100, ist(2026, 9, 25, 11, 4, 59, 500000)))      # the tail, received 3 s late
    a.note_feed_alive(at=ist(2026, 9, 25, 11, 5, 4))
    a.flush_due_bars(ist(2026, 9, 25, 11, 5, 5, 500000))
    assert a.market_state["SWIGGY"].iloc[-1]["Close"] == 100.3 and a.dropped_ticks == 0


def test_the_feed_lag_is_measured_on_trades_not_on_snapshots(monkeypatch):
    monkeypatch.setattr(engine, "datetime", SessionClock)
    now = SessionClock.FIXED

    def kite_tick(cum, sent):
        naive_local = sent.astimezone().replace(tzinfo=None)             # KiteTicker's naive host-local time
        return {"instrument_token": 1234, "last_price": 100.0, "volume_traded": cum, "exchange_timestamp": naive_local}

    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), token_map={1234: "SWIGGY"},
                                   started_at=ist(2026, 9, 28, 10, 50))
        a.broker_on_ticks(None, [kite_tick(1_000, now - timedelta(minutes=4))])   # subscribe snapshot: stale
        a.broker_on_ticks(None, [kite_tick(1_000, now - timedelta(seconds=30))])  # no trade
        a.broker_on_ticks(None, [kite_tick(1_200, now - timedelta(seconds=3))])   # a trade, sent 3 s ago
        for _ in range(3):
            a.on_tick(await a.tick_queue.get())
        return a.feed_lag

    assert asyncio.run(scenario()) == 3.0


def test_the_bar_clock_waits_for_ticks_already_received():
    # A tick still queued may be the tail of a due bar; closing first would drop it as late.
    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), started_at=ist(2026, 9, 25, 9, 0))
        a.on_tick(tick(101.0, 100, ist(2026, 9, 25, 11, 0, 0)))
        a.tick_queue.put_nowait(tick(100.3, 100, ist(2026, 9, 25, 11, 4, 59)))
        clock = asyncio.create_task(a.bar_clock(interval=0.01, clock=lambda: ist(2026, 9, 25, 11, 5, 3)))
        await asyncio.sleep(0.05)
        still_open = "SWIGGY" in a.current_bars                           # v1.3 closed it without the tail
        a.on_tick(a.tick_queue.get_nowait())
        await asyncio.sleep(0.05)
        clock.cancel()
        await asyncio.gather(clock, return_exceptions=True)
        return still_open, a.market_state["SWIGGY"].iloc[-1]["Close"]

    assert asyncio.run(scenario()) == (True, 100.3)


# ---------------------------------------------------------------- round 5: feed time in both directions
def kite_packet(cum, sent, price=100.0):
    naive_local = sent.astimezone().replace(tzinfo=None)                   # KiteTicker's naive host-local time
    return {"instrument_token": 1234, "last_price": price, "volume_traded": cum, "exchange_timestamp": naive_local}


def test_a_lag_rise_seen_on_depth_updates_holds_the_bar_open(monkeypatch):
    # v1.4 measured lag on trades only, so late depth updates kept proving liveness at the old lag and
    # the bar closed before its tail arrived.
    class SteppedClock(datetime):
        FIXED = None

        @classmethod
        def now(cls, tz=None):
            return cls.FIXED.astimezone(tz) if tz else cls.FIXED.replace(tzinfo=None)

    monkeypatch.setattr(engine, "datetime", SteppedClock)

    def receive(a, cum, sent, delay):
        SteppedClock.FIXED = sent + timedelta(seconds=delay)
        a.broker_on_ticks(None, [kite_packet(cum, sent)])

    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), token_map={1234: "SWIGGY"},
                                   started_at=ist(2026, 9, 28, 10, 50))
        receive(a, 1_000, ist(2026, 9, 28, 10, 59, 40), 0.3)              # re-baselines
        receive(a, 1_200, ist(2026, 9, 28, 10, 59, 50), 0.3)              # a trade, 0.3 s late
        receive(a, 1_200, ist(2026, 9, 28, 11, 0, 0), 4.0)                # a depth update, 4 s late
        await asyncio.sleep(0)
        while not a.tick_queue.empty():
            a.on_tick(a.tick_queue.get_nowait())
        return a.feed_lag

    assert asyncio.run(scenario()) == 4.0                                 # v1.4: 0.3


def test_a_tick_payload_proves_liveness_only_after_its_ticks_are_queued():
    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), token_map={1234: "SWIGGY"})
        order = []
        a.tick_queue.put_nowait = lambda t: order.append("tick")
        a.note_feed_alive = lambda at=None: order.append("alive")
        a.broker_on_ticks(None, [{"instrument_token": 1234, "last_price": 100.0, "volume_traded": 10}])
        await asyncio.sleep(0)
        return order

    assert asyncio.run(scenario()) == ["tick", "alive"]                    # v1.4 stamped nothing here


def test_only_heartbeats_and_text_stamp_liveness_from_on_message(monkeypatch):
    # KiteTicker calls on_message before on_ticks for the same payload, so a tick payload stamped there
    # could vouch for its own tail.
    class FakeTicker:
        MODE_FULL = "full"

        def __init__(self, api_key, access_token):
            pass

        def connect(self, threaded=False):
            pass

    monkeypatch.setitem(sys.modules, "kiteconnect", types.SimpleNamespace(KiteTicker=FakeTicker))  # no extra needed

    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop())
        stamps = []
        a.note_feed_alive = lambda at=None: stamps.append(at)
        kws = engine.start_kite_feed("key", "token", [1234], a, asyncio.Event())
        kws.on_message(kws, b"\x00" * 184, True)                         # a tick payload: stamped by on_ticks
        kws.on_message(kws, b"\x00", True)                               # a heartbeat
        kws.on_message(kws, '{"type": "order"}', False)                  # a text message
        await asyncio.sleep(0)
        return len(stamps)

    assert asyncio.run(scenario()) == 2


def test_a_host_clock_behind_the_exchange_is_corrected_not_ignored(caplog):
    # v1.4 clamped negative lag to 0: bars closed S s late and the router misjudged signal ages by S.
    a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(), loop=None,
                               started_at=ist(2026, 9, 25, 9, 0), require_feed_liveness=True)
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 59))
    a.note_feed_lag(ist(2026, 9, 25, 10, 0, 0), -19.7)                   # host clock 19.7 s slow
    a.on_tick(tick(101.0, 100, ist(2026, 9, 25, 10, 0, 10)))
    a.note_feed_alive(at=ist(2026, 9, 25, 10, 4, 44))                    # true 10:05:03.7
    a.flush_due_bars(ist(2026, 9, 25, 10, 4, 45))                        # true 10:05:04.7: due
    assert "SWIGGY" not in a.current_bars and a.market_state["SWIGGY"].index[-1] == pd.Timestamp(ist(2026, 9, 25, 10, 0))
    assert "Host clock runs 19.7s behind the exchange" in caplog.text
    clock = engine.exchange_clock(lambda: a)
    assert abs((clock() - datetime.now(engine.IST)).total_seconds() - 19.7) < 1.0
    a._lag_samples.clear()
    a.note_feed_lag(ist(2026, 9, 25, 10, 5), 3.0)                        # real latency is not removed
    assert abs((clock() - datetime.now(engine.IST)).total_seconds()) < 1.0


# ---------------------------------------------------------------- round 6: a host clock that is wrong, or fixed
class WallClock(datetime):
    """The host's wall clock, set by the test: it can step, as when NTP corrects it."""
    FIXED = None

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED.astimezone(tz) if tz else cls.FIXED.replace(tzinfo=None)


@pytest.mark.parametrize("skew", [20, -20])
def test_a_host_clock_step_mid_session_does_not_cut_a_bar(monkeypatch, skew):
    # The host runs 20 s behind (or ahead); at 10:04:00 NTP steps it right (the fix v1.5's warning asks for).
    # v1.5 kept the stale lag in the stepped frame, closed the 10:00 bar early and dropped its tail. The
    # production bar clock runs here, as main() starts it for a live feed.
    monkeypatch.setattr(engine, "datetime", WallClock)
    start, step, offset = ist(2026, 9, 25, 9, 59, 20), ist(2026, 9, 25, 10, 4, 0), timedelta(seconds=skew)
    mono = [0.0]

    def at(true):                                     # move both clocks to true exchange time `true`
        mono[0] = (true - start).total_seconds()
        WallClock.FIXED = true - offset if true < step else true

    async def scenario():
        at(start)
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), token_map={1234: "SWIGGY"}, require_feed_liveness=True,
                                   clock=engine.steady_clock(monotonic=lambda: mono[0]))
        a.mark_feed_reset()
        bar_clock = asyncio.create_task(a.bar_clock(interval=0))
        trades = {ist(2026, 9, 25, 9, 59, 50): (1_000, 100.0), ist(2026, 9, 25, 10, 0, 10): (21_000, 100.0),
                  ist(2026, 9, 25, 10, 1, 0): (21_000, 101.0), ist(2026, 9, 25, 10, 4, 50): (41_000, 97.0),
                  ist(2026, 9, 25, 10, 4, 58): (41_500, 96.5), ist(2026, 9, 25, 10, 5, 20): (42_000, 97.5)}
        true = start
        while true < ist(2026, 9, 25, 10, 5, 30):
            true += timedelta(seconds=0.1)
            sent = true - timedelta(seconds=0.3)                      # 0.3 s of latency
            at(true)
            if sent in trades:
                cum, price = trades[sent]
                a.broker_on_ticks(None, [kite_packet(cum, sent, price)])
                await asyncio.sleep(0)
                while not a.tick_queue.empty():
                    a.on_tick(a.tick_queue.get_nowait())
            if true.microsecond == 0:
                a.note_feed_alive(a.clock())                          # a heartbeat
            await asyncio.sleep(0)                                    # the bar clock's turn
            await asyncio.sleep(0)
        bar_clock.cancel()
        await asyncio.gather(bar_clock, return_exceptions=True)
        return a.market_state["SWIGGY"], a.dropped_ticks

    bars, dropped = asyncio.run(scenario())
    bar = bars.loc[pd.Timestamp(ist(2026, 9, 25, 10, 0))]
    assert (bar["Low"], bar["Close"], bar["Volume"], dropped) == (96.5, 96.5, 40_500.0, 0)


def test_a_host_clock_step_does_not_trip_the_feed_watchdog(monkeypatch):
    # v1.5's watchdog read the wall clock: NTP stepping a slow host 20 s forward looked like 20 s of silence.
    monkeypatch.setattr(engine, "datetime", WallClock)
    WallClock.FIXED = SessionClock.FIXED
    a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(), loop=None,
                               require_feed_liveness=True, clock=engine.steady_clock())
    a.note_feed_alive()
    WallClock.FIXED = SessionClock.FIXED + timedelta(seconds=20)         # the step

    async def watch_briefly():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(engine._watch_feed(asyncio.Event(), a, stall_after=15, check_every=0.01), 0.1)

    asyncio.run(watch_briefly())


def test_a_live_feed_runs_on_a_steady_clock(monkeypatch):
    clocks = []

    def feed(api_key, access_token, tokens, tick_adapter, feed_dead):
        clocks.append(tick_adapter.clock.__qualname__)
        tick_adapter.loop.call_later(0.2, feed_dead.set)
        return types.SimpleNamespace(close=lambda: None)

    from test_end_to_end import kite_env
    kite_env(monkeypatch, feed)
    asyncio.run(engine.main(["--source", "kite", "--listing-date", "2026-09-08", "--live-feed", "--run-seconds", "30"]))
    assert clocks == ["steady_clock.<locals>.<lambda>"]


def test_one_late_packet_does_not_move_the_router_clock():
    # v1.5 used the largest lag sample as the skew estimate: one trade 8 s late cut the 19.7 s correction to
    # 12 s, so a signal closed by the next bucket's first print was refused as "not closed yet".
    a = adapter(history=session_history(24))
    for second in range(10):
        a.note_feed_lag(ist(2026, 9, 25, 10, 0, second), -19.7)
    a.note_feed_lag(ist(2026, 9, 25, 10, 0, 10), -11.7)                  # 8 s of latency on one packet
    assert a.feed_lag == -11.7                                            # bars still wait for it
    clock = engine.exchange_clock(lambda: a)
    assert abs((clock() - datetime.now(engine.IST)).total_seconds() - 19.7) < 1.0


def test_a_zeroed_exchange_time_is_not_a_lag_sample():
    # v1.5 checked the fallback (receive time) instead of the raw field, so a zeroed stamp became a 0 s
    # sample and cancelled the host's -19.7 s correction for a minute.
    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), token_map={1234: "SWIGGY"})
        a.broker_on_ticks(None, [{"instrument_token": 1234, "last_price": 100.0, "volume_traded": 10,
                                  "exchange_timestamp": datetime.fromtimestamp(0)}])  # noqa: DTZ006 (as KiteTicker)
        await asyncio.sleep(0)
        return a.tick_queue.get_nowait()

    assert asyncio.run(scenario()).received is None


def test_a_tick_stamped_far_in_the_future_is_dropped_before_it_moves_anything(caplog):
    # v1.5 took one forward-stamped packet as a -600 s lag: a future bar closed at once and the router's
    # clock moved 600 s with it.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.on_tick(tick(100.0, 100, ist(2026, 9, 25, 10, 1)))
    ahead = tick(101.0, 100, ist(2026, 9, 25, 10, 12))
    ahead.received = ist(2026, 9, 25, 10, 2)
    a.on_tick(ahead)
    assert a.dropped_ticks == 1 and a.feed_lag == 0.0 and a.market_time == ist(2026, 9, 25, 10, 1)
    assert a.current_bars["SWIGGY"]["timestamp"] == ist(2026, 9, 25, 10, 0)
    assert "stamped 600s ahead of the host clock dropped" in caplog.text
    within = tick(101.0, 100, ist(2026, 9, 25, 10, 2, 20))              # a host 20 s behind is still believed
    within.received = ist(2026, 9, 25, 10, 2)
    a.on_tick(within)
    assert a.dropped_ticks == 1 and a.feed_lag == -20.0


def test_with_the_host_behind_a_connect_after_the_open_still_blinds_the_first_bar():
    # The host runs 20 s behind and the feed connects at 09:15:10 (host 09:14:50). v1.5 judged "watching
    # since before the open" in host time, counted the day's volume from zero and kept a 09:15 bar that
    # missed the opening seconds.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 14, 50))
    first = cum_tick(104.0, 420_000, ist(2026, 9, 25, 9, 15, 11))
    first.received = ist(2026, 9, 25, 9, 14, 51, 300000)
    a.on_tick(first)
    later = cum_tick(103.0, 430_000, ist(2026, 9, 25, 9, 16))
    later.received = ist(2026, 9, 25, 9, 15, 40, 300000)
    a.on_tick(later)
    assert a.current_bars["SWIGGY"]["partial"] is True
    nxt = cum_tick(103.5, 431_000, ist(2026, 9, 25, 9, 20, 1))
    nxt.received = ist(2026, 9, 25, 9, 19, 41, 300000)
    a.on_tick(nxt)
    assert pd.Timestamp(ist(2026, 9, 25, 9, 15)) not in a.market_state["SWIGGY"].index


def test_with_a_synced_host_a_connect_before_the_open_counts_the_first_print():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 14, 50))
    first = cum_tick(104.0, 420_000, ist(2026, 9, 25, 9, 15, 1))
    first.received = ist(2026, 9, 25, 9, 15, 1, 300000)
    a.on_tick(first)
    assert a.current_bars["SWIGGY"]["partial"] is False and a.current_bars["SWIGGY"]["Volume"] == 420_000


def test_an_update_that_is_not_newer_is_not_lag(monkeypatch):
    # Only a newer exchange time dates the feed: the same stamp re-sent late says nothing about the feed.
    monkeypatch.setattr(engine, "datetime", WallClock)

    def receive(a, cum, sent, delay):
        WallClock.FIXED = sent + timedelta(seconds=delay)
        a.broker_on_ticks(None, [kite_packet(cum, sent)])

    async def scenario():
        a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(),
                                   asyncio.get_running_loop(), token_map={1234: "SWIGGY"},
                                   started_at=ist(2026, 9, 28, 10, 50))
        receive(a, 1_000, ist(2026, 9, 28, 10, 59, 40), 0.3)              # re-baselines
        receive(a, 1_200, ist(2026, 9, 28, 10, 59, 50), 0.3)              # a trade, 0.3 s late
        receive(a, 1_200, ist(2026, 9, 28, 10, 59, 50), 9.0)              # the same update again, 9 s later
        await asyncio.sleep(0)
        while not a.tick_queue.empty():
            a.on_tick(a.tick_queue.get_nowait())
        return a.feed_lag

    assert asyncio.run(scenario()) == 0.3


def test_a_sessions_lost_last_bar_is_backfilled_before_the_next_session_trades():
    # A reconnect at 15:26 blinds the 15:25 bar, which is discarded. v1.5 back-filled only holes inside a
    # session, so it stayed missing and the next 09:15 bar was evaluated across it.
    history = make_bars([100.0] * 74, start=ist(2026, 9, 24, 9, 15))    # 09:15-15:20: the 15:25 bar is lost
    lost = make_bars([100.2], start=ist(2026, 9, 24, 15, 25))
    calls, seen = [], []

    async def backfill(sym, start, end):
        calls.append((start, end))
        return lost

    async def scenario():
        alpha = RecordingAlpha()
        a = engine.LiveTickAdapter({"SWIGGY": history}, alpha, asyncio.Queue(), asyncio.get_running_loop(),
                                   started_at=ist(2026, 9, 25, 9, 0), backfill=backfill,
                                   bar_listeners=[lambda s, ts, b: seen.append(pd.Timestamp(ts))])
        a.on_tick(tick(100.4, 10, ist(2026, 9, 25, 9, 15, 5)))
        a.on_tick(tick(100.6, 10, ist(2026, 9, 25, 9, 20, 1)))              # closes 09:15
        await asyncio.sleep(0.05)
        return a, alpha

    a, alpha = asyncio.run(scenario())
    assert calls == [(ist(2026, 9, 24, 15, 25), ist(2026, 9, 25, 9, 15))]
    assert pd.Timestamp(ist(2026, 9, 24, 15, 25)) in a.market_state["SWIGGY"].index
    assert alpha.evaluated == [(pd.Timestamp(ist(2026, 9, 24, 15, 25)), pd.Timestamp(ist(2026, 9, 25, 9, 15)))]
    assert seen == [pd.Timestamp(ist(2026, 9, 24, 15, 25)), pd.Timestamp(ist(2026, 9, 25, 9, 15))]


def test_a_breakout_in_a_lost_last_bar_does_not_fire_again_at_the_next_open():
    # The base was crossed in the 15:25 bar the engine lost; evaluated across that hole, the 09:15 bar
    # looked like a fresh crossing, a day late.
    closes = [100.0] * 20 + [99.0] * 54                                   # base high ~100.5; 15:20 closes below
    history = make_bars(closes, volumes=[100_000] * 74, start=ist(2026, 9, 24, 9, 15))
    alpha = engine.AlphaEngine(base_bars=20)

    async def run(backfill):
        a = engine.LiveTickAdapter({"SWIGGY": history.copy()}, alpha, asyncio.Queue(), asyncio.get_running_loop(),
                                   started_at=ist(2026, 9, 25, 9, 0), backfill=backfill)
        a.on_tick(tick(103.0, 900_000, ist(2026, 9, 25, 9, 15, 5)))
        a.on_tick(tick(103.1, 10, ist(2026, 9, 25, 9, 20, 1)))
        await asyncio.sleep(0.05)
        return a.oms_queue.qsize()

    async def crossed_at_1525(sym, start, end):
        return make_bars([103.0], volumes=[900_000], start=ist(2026, 9, 24, 15, 25))

    assert asyncio.run(run(crossed_at_1525)) == 0


# ---------------------------------------------------------------- round 7: the 30 s rule, suspends, zeroed stamps
def stamped(price, cum, stamp, ahead):
    """A Kite print whose exchange stamp leads its receive time by ``ahead`` seconds."""
    t = cum_tick(price, cum, stamp)
    t.received = stamp - timedelta(seconds=ahead)
    return t


def test_a_print_dropped_as_far_ahead_blinds_its_bar_and_the_counter():
    # A host ~30.4 s behind: whole-second stamps put some prints just over the 30 s limit and the rest under
    # it. v1.6 kept the 10:00 bar built from the prints that got through, and credited the dropped print's
    # shares to the 10:05 bar.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 0))
    a.on_tick(stamped(100.0, 1_000_000, ist(2026, 9, 25, 10, 0, 5), 29.8))
    a.on_tick(stamped(101.0, 1_100_000, ist(2026, 9, 25, 10, 2), 29.9))
    a.on_tick(stamped(100.2, 1_400_000, ist(2026, 9, 25, 10, 4, 58), 30.2))  # just over the limit: dropped
    assert a.dropped_ticks == 1 and a.current_bars["SWIGGY"]["partial"] is True
    a.on_tick(stamped(100.3, 1_410_000, ist(2026, 9, 25, 10, 5, 1), 29.7))
    a.on_tick(stamped(100.4, 1_420_000, ist(2026, 9, 25, 10, 6), 29.7))
    assert pd.Timestamp(ist(2026, 9, 25, 10, 0)) not in a.market_state["SWIGGY"].index
    assert a.current_bars["SWIGGY"]["Volume"] == 10_000 and a.current_bars["SWIGGY"]["partial"] is True


def test_a_late_print_that_slips_past_the_rule_is_not_credited_with_the_day():
    # Host 35 s behind: every print is dropped until one arrives 6 s late and gets through. Nothing had been
    # accepted today, so v1.6 counted the whole day's counter into that one bar: a fake 70x RVOL.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 0))
    for second, cum in [(5, 7_000_000), (30, 7_020_000), (55, 7_040_000)]:
        a.on_tick(stamped(100.5, cum, ist(2026, 9, 25, 10, 0, second), 35.0))
    a.on_tick(stamped(100.9, 7_055_000, ist(2026, 9, 25, 10, 1), 29.0))
    assert a.dropped_ticks == 3 and "SWIGGY" not in a.current_bars


@pytest.mark.skipif(not hasattr(time, "CLOCK_BOOTTIME"), reason="Linux only")
def test_the_steady_clock_keeps_counting_through_a_suspend():
    # CLOCK_MONOTONIC stops while the host is suspended: after a laptop sleep, v1.6's live clock stayed
    # behind by the sleep, every tick was dropped as far ahead and the run went on trading nothing.
    assert engine.steady_clock.__defaults__[1] is engine._uptime
    assert abs(engine._uptime() - time.clock_gettime(time.CLOCK_BOOTTIME)) < 1.0


def test_a_run_whose_every_tick_is_far_ahead_stops_instead_of_running_blind():
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    for second in range(0, 21, 2):                                        # 20 s of prints, all 40 s ahead
        a.on_tick(stamped(100.0, 1_000_000 + second, ist(2026, 9, 25, 10, 1, second), 40.0))
    assert a.blind_for == 20.0
    with pytest.raises(RuntimeError, match="stamped more than 30s ahead of this run's clock"):
        asyncio.run(engine._watch_feed(asyncio.Event(), a, stall_after=15, check_every=0.01))


def test_one_corrupt_stamp_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(engine, "datetime", SessionClock)
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 28, 9, 0))
    a.on_tick(stamped(100.0, 1_000, ist(2026, 9, 28, 10, 50), 600.0))     # corrupt: dropped
    a.on_tick(stamped(100.0, 1_100, ist(2026, 9, 28, 10, 50, 30), 0.3))   # believable again
    a.on_tick(stamped(100.0, 1_200, ist(2026, 9, 28, 11, 0, 50), 600.0))  # another corrupt one, 20 s later
    a.note_feed_alive(at=SessionClock.FIXED)

    async def watch_briefly():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(engine._watch_feed(asyncio.Event(), a, stall_after=15, check_every=0.01), 0.1)

    asyncio.run(watch_briefly())


def test_a_zeroed_stamp_is_bucketed_in_feed_time(monkeypatch):
    # The host runs 20 s fast. v1.6 bucketed a zeroed-stamp print by the raw host clock, so a print sent at
    # 10:04:40 closed the 10:00 bar 20 s early and its last prints were dropped as late.
    monkeypatch.setattr(engine, "datetime", WallClock)
    WallClock.FIXED = ist(2026, 9, 25, 10, 5, 0, 300000)                 # true 10:04:40
    a = engine.LiveTickAdapter({"SWIGGY": session_history(24)}, engine.AlphaEngine(), asyncio.Queue(), loop=None,
                               token_map={1234: "SWIGGY"})
    a.note_feed_lag(WallClock.FIXED, 20.3)
    t = a._normalize({"instrument_token": 1234, "last_price": 100.0, "volume_traded": 10,
                      "exchange_timestamp": datetime.fromtimestamp(0)})  # noqa: DTZ006 (as KiteTicker)
    assert engine.bar_floor(t.timestamp) == ist(2026, 9, 25, 10, 0)


def test_with_the_host_behind_a_first_print_after_the_first_bucket_is_blind():
    # Pins the first-print rule itself (in exchange time): a connect at exchange 09:15:10, stamped 09:14:50 by
    # a host 20 s behind, did not watch the open, so the day's counter cannot all belong to the 09:20 bar.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 9, 14, 50))
    a.on_tick(stamped(104.0, 420_000, ist(2026, 9, 25, 9, 22), 19.7))
    a.on_tick(stamped(104.5, 421_000, ist(2026, 9, 25, 9, 25, 1), 19.7))
    assert pd.Timestamp(ist(2026, 9, 25, 9, 20)) not in a.market_state["SWIGGY"].index


def test_a_tick_handled_after_the_feed_went_down_is_not_a_crash():
    # on_close's mark_feed_down is queued behind ticks already received; judging such a tick's watch start
    # from "down" (datetime.max) must not overflow.
    a = adapter(history=session_history(24), started_at=ist(2026, 9, 25, 9, 0))
    a.mark_feed_reset(at=ist(2026, 9, 25, 10, 0))
    a.mark_feed_down()
    a.on_tick(stamped(101.0, 5_000, ist(2026, 9, 25, 10, 0, 30), 19.7))
    assert "SWIGGY" not in a.current_bars or a.current_bars["SWIGGY"]["partial"] is True
