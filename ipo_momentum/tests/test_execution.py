"""ExecutionRouter sizing/risk, staleness, paper exits and the halt report: no Kite dependency.

The Kite gateway tests are in test_kite_gateway.py, which is skipped without the kiteconnect extra.
"""
import asyncio
import logging

import pandas as pd
import pytest

import engine
from conftest import ist


def sig(entry=289.83, stop=286.2, target=300.72, symbol="SWIGGY"):
    return engine.Signal(symbol, entry, stop, target, "IPO_BASE_BREAKOUT", ist(2026, 9, 28, 9, 20))


MARKET_NOW = ist(2026, 9, 28, 9, 25, 30)                               # 30 s after sig()'s bar closed


def router(gateway=None, **kw):
    kw.setdefault("risk_per_trade", 15_000.0)
    kw.setdefault("clock", lambda: MARKET_NOW)                           # never the wall clock: no time bombs
    return engine.ExecutionRouter(asyncio.Queue(), gateway=gateway or engine.PaperGateway(latency=0), **kw)


# ---------------------------------------------------------------- sizing & rounding
def test_quantity_risks_the_budget_and_respects_the_notional_cap():
    r = router(max_position_value=None)
    assert r._calculate_qty(100.0, 98.0) == 7_500                        # 15,000 / 2.00
    r = router(max_position_value=500_000.0)
    assert r._calculate_qty(100.0, 99.9) == 5_000                        # 150,000 shares uncapped


@pytest.mark.parametrize("entry,stop", [(100.0, 100.0), (100.0, 101.0), (float("nan"), 99.0), (100.0, float("nan")), (0.0, -1.0)])
def test_degenerate_risk_is_never_traded(entry, stop):
    assert router()._calculate_qty(entry, stop) == 0


def test_plan_rounds_to_the_tick_grid_in_the_conservative_direction():
    r = router(max_position_value=1_000_000.0, max_entry_slippage=0.005)
    p = r.plan(sig(entry=289.83, stop=286.217, target=300.719))
    assert p.entry_limit == 291.30                                        # 289.83 * 1.005 = 291.279 -> up
    assert p.stop_loss == 286.20 and p.target == 300.70                   # both rounded down
    assert p.quantity == 2_941                                            # 15,000 / (291.30 - 286.20) = 2941.2
    assert p.quantity * (p.entry_limit - p.stop_loss) <= 15_000


def test_plan_rejects_a_target_that_the_limit_price_already_exceeds():
    assert router().plan(sig(entry=100.0, stop=99.0, target=100.2)) is None


# ---------------------------------------------------------------- OMS loop behaviour
def run_router(r, signals):
    async def scenario():
        worker = asyncio.create_task(r.process_orders())
        for s in signals:
            r.oms_queue.put_nowait(s)
        await asyncio.wait_for(r.oms_queue.join(), timeout=5)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())
    return r


def test_duplicate_signals_enter_once(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    r = run_router(router(), [sig(), sig(), sig()])
    assert r.active_inventory == {"SWIGGY"} and len(r.fills) == 1
    assert caplog.text.count("Signal ignored: position already pending/open") == 2   # v1.0 dropped these silently


def test_float_noise_in_the_stop_distance_does_not_cost_a_share():
    assert 15_000 / (103.0 - 100.0 + 4e-13) < 5_000                     # naive floor would give 4,999
    assert router()._calculate_qty(103.0 + 4e-13, 100.0) == 5_000


class ExplodingGateway(engine.OrderGateway):
    def __init__(self):
        self.calls = 0

    async def execute(self, plan):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("broker down")
        return engine.Fill(plan.signal.symbol, plan.quantity, plan.signal.entry_price, "OK-2")


def test_gateway_failure_releases_the_symbol_and_the_loop_keeps_running(caplog):
    gw = ExplodingGateway()
    r = run_router(router(gateway=gw), [sig(), sig()])
    assert gw.calls == 2                                                  # second signal retried after the failure
    assert [f.order_id for f in r.fills] == ["OK-2"]
    assert "Order routing failed" in caplog.text


def test_unfilled_order_releases_the_symbol():
    class NoFill(engine.OrderGateway):
        async def execute(self, plan):
            return None
    r = run_router(router(gateway=NoFill()), [sig()])
    assert r.active_inventory == set() and r.fills == []


def test_paper_fill_is_labelled_as_simulated(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    run_router(router(), [sig()])
    assert "PAPER FILL (simulated, no order sent)" in caplog.text
    assert "BROKER FILLED" not in caplog.text


# ---------------------------------------------------------------- staleness
def test_signal_is_stale_once_its_bar_is_long_closed(caplog):
    s = sig()                                                             # bar 09:20-09:25
    fresh = router(clock=lambda: ist(2026, 9, 28, 9, 25, 30))
    stale = router(clock=lambda: ist(2026, 9, 29, 9, 15, 0))              # the next morning
    assert not fresh.is_stale(s) and stale.is_stale(s)
    run_router(stale, [s])
    assert stale.fills == [] and "Signal not traded: it is stale" in caplog.text
    assert not router(clock=lambda: ist(2030, 1, 1), max_signal_age=None).is_stale(s)


def test_signal_for_a_bar_that_has_not_closed_is_refused(caplog):
    # Defence in depth: a synthetic or corrupt tape stamped in the future can never reach a gateway.
    early = router(clock=lambda: ist(2026, 9, 28, 9, 24, 0))                # the 09:20 bar closes at 09:25
    run_router(early, [sig()])
    assert early.fills == [] and "has not closed yet" in caplog.text


# ---------------------------------------------------------------- paper positions and simulated exits
def bar(o, h, l, c):
    return pd.Series({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1000.0})


def filled_router():
    r = run_router(router(max_position_value=1_000_000.0), [sig()])       # entry 289.83, stop 286.20, target 300.70
    assert set(r.positions) == {"SWIGGY"}
    return r


def test_paper_target_exit_realizes_profit_and_frees_the_symbol():
    r = filled_router()
    pos = r.positions["SWIGGY"]
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 20), bar(300, 310, 299, 305))   # the signal bar itself: ignored
    assert "SWIGGY" in r.positions
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(295, 301, 294, 300))
    assert r.positions == {} and r.active_inventory == set()
    closed = r.closed_positions[0]
    assert (closed.exit_reason, closed.exit_price) == ("TARGET", 300.70)
    assert closed.pnl == pytest.approx((300.70 - 289.83) * pos.quantity)


def test_paper_stop_gapped_through_fills_at_the_open_when_above_the_stop_limit():
    r = filled_router()                                                   # stop 286.20, stop limit 280.45
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(283.0, 284.0, 282.0, 283.5))
    closed = r.closed_positions[0]
    assert (closed.exit_reason, closed.exit_price) == ("STOP", 283.0)


def test_paper_gap_below_the_stop_limit_fills_only_if_price_recovers_to_it(caplog):
    r = filled_router()
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(279.0, 280.0, 278.0, 279.5))    # never reaches 280.45
    pos = r.positions["SWIGGY"]
    assert pos.stop_triggered and r.closed_positions == [] and "UNFILLED" in caplog.text
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 30), bar(279.5, 281.0, 279.0, 280.8))    # the resting limit fills
    closed = r.closed_positions[0]
    assert (closed.exit_reason, closed.exit_price) == ("STOP", 280.45)
    assert r.active_inventory == set()


def test_paper_open_beyond_the_target_books_the_target_at_the_open():
    r = filled_router()
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(305.0, 306.0, 285.0, 286.0))    # v1.1 booked this as a STOP
    closed = r.closed_positions[0]
    assert (closed.exit_reason, closed.exit_price) == ("TARGET", 305.0)


def test_paper_bar_touching_both_levels_assumes_the_stop_first():
    r = filled_router()
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(290.0, 302.0, 285.0, 295.0))
    assert r.closed_positions[0].exit_reason == "STOP"


def test_live_gateway_positions_are_not_simulated():
    class Live(engine.OrderGateway):
        async def execute(self, plan):
            return engine.Fill(plan.signal.symbol, plan.quantity, plan.signal.entry_price, "LIVE-1", "GTT-1")
    r = run_router(router(gateway=Live()), [sig()])
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(280.0, 281.0, 279.0, 280.5))
    assert "SWIGGY" in r.positions and r.closed_positions == []           # the broker's GTT owns the exit


def test_the_halt_report_lists_fills_that_completed_during_shutdown(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    gw = engine.PaperGateway()
    gw.late_fills.append(engine.Fill("SWIGGY", 800, 290.2, "260928000000001", "777"))
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    code = engine._halt_report(router(gateway=gw), gw, ticker, exit_code=0, stop_signal=None)
    assert "Open SWIGGY: 800 @ 290.20 | filled during shutdown | exits: 777" in caplog.text and code == 0
