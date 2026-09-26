"""LiveTickAdapter: tick -> bar synthesis, Kite payloads, the bar clock, and loop resilience."""
import asyncio
import threading
from datetime import datetime, timedelta

import pandas as pd

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
    assert a._traded_quantity(t) == 9_000_000
    t = engine.Tick("SWIGGY", 100.0, 0, ist(2026, 9, 28, 9, 15, 1), cumulative_volume=40_000)
    assert a._traded_quantity(t) == 40_000                                 # not negative, not ignored


def test_joining_mid_session_does_not_dump_the_days_volume_into_one_bar():
    """Started 10:04:59; the first print (10:05:01) carries 5M shares traded since 09:15."""
    a = adapter(history=make_bars([100.0] * 5, start=ist(2026, 9, 25, 15, 0)), token_map={1234: "SWIGGY"},
                started_at=ist(2026, 9, 28, 10, 4, 59))
    for secs, cum in [(1, 5_000_000), (90, 5_004_000), (200, 5_010_000)]:
        a.on_tick(engine.Tick("SWIGGY", 280.0, 0, ist(2026, 9, 28, 10, 5) + timedelta(seconds=secs), cumulative_volume=cum))
    a.flush_due_bars(ist(2026, 9, 28, 10, 11))
    bar = a.market_state["SWIGGY"].iloc[-1]
    assert a.market_state["SWIGGY"].index[-1] == pd.Timestamp(ist(2026, 9, 28, 10, 5))   # complete bar, kept
    assert bar["Volume"] == 10_000                                         # only what traded while we watched


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
        history = {"X": make_bars([10.0] * 5, start=ist(2026, 9, 25, 15, 0))}
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
