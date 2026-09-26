"""ExecutionRouter sizing/risk and the Kite gateway, driven through the real kiteconnect SDK."""
import asyncio
import json
import logging

import pandas as pd
import pytest

import engine
from conftest import ist


def sig(entry=289.83, stop=286.2, target=300.72, symbol="SWIGGY"):
    return engine.Signal(symbol, entry, stop, target, "IPO_BASE_BREAKOUT", ist(2026, 9, 28, 9, 20))


def router(gateway=None, **kw):
    kw.setdefault("risk_per_trade", 15_000.0)
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


def test_duplicate_signals_enter_once():
    r = run_router(router(), [sig(), sig(), sig()])
    assert r.active_inventory == {"SWIGGY"} and len(r.fills) == 1


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


# ---------------------------------------------------------------- Kite gateway on the real SDK
kiteconnect = pytest.importorskip("kiteconnect")


class StubKite(kiteconnect.KiteConnect):
    """The real SDK with only its HTTP transport replaced, so SDK-side validation still runs."""

    def __init__(self, order_states, gtt_error=None, ltp=290.0):
        super().__init__(api_key="test_key", access_token="test_token")
        self.order_states = list(order_states)
        self.gtt_error = gtt_error
        self.last_price = ltp
        self.requests = []

    def _request(self, route, method, url_args=None, params=None, is_json=False, query_params=None):
        self.requests.append((route, method, url_args, params))
        if route == "market.quote.ltp":
            return {key: {"instrument_token": 1234, "last_price": self.last_price} for key in params["i"]}
        if route == "order.place":
            return {"order_id": "260928000000001"}
        if route == "order.info":
            return [self.order_states.pop(0) if len(self.order_states) > 1 else self.order_states[0]]
        if route == "order.cancel":
            return {"order_id": url_args["order_id"]}
        if route == "gtt.place":
            if self.gtt_error:
                raise self.gtt_error
            return {"trigger_id": 777}
        raise AssertionError(f"unexpected route {route}")

    def routes(self):
        return [r[0] for r in self.requests]


def kite_plan():
    return engine.OrderPlan(sig(), quantity=2_941, entry_limit=291.30, stop_loss=286.20, target=300.70)


def test_sdk_has_no_bracket_order_variety():
    assert not hasattr(kiteconnect.KiteConnect, "VARIETY_BO")           # why v1.0's commented BO call cannot work


def test_kite_entry_then_gtt_oco_on_the_filled_quantity():
    kite = StubKite([{"status": "OPEN", "filled_quantity": 0},
                     {"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])
    gw = engine.KiteOrderGateway(kite, poll_interval=0, fill_timeout=5)
    fill = asyncio.run(gw.execute(kite_plan()))

    assert fill == engine.Fill("SWIGGY", 2941, 290.1, "260928000000001", "777")
    assert kite.routes() == ["market.quote.ltp", "order.place", "order.info", "order.info", "gtt.place"]
    _, _, url_args, entry = kite.requests[1]
    assert url_args == {"variety": "regular"}
    assert entry == {"variety": "regular", "exchange": "NSE", "tradingsymbol": "SWIGGY", "transaction_type": "BUY",
                     "quantity": 2941, "product": "CNC", "order_type": "LIMIT", "price": 291.30, "validity": "DAY",
                     "tag": "ipomomentum"}
    gtt = kite.requests[-1][3]
    assert gtt["type"] == "two-leg"
    condition, legs = json.loads(gtt["condition"]), json.loads(gtt["orders"])
    assert condition["trigger_values"] == [286.20, 300.70] and condition["last_price"] == 290.1
    assert [(l["transaction_type"], l["order_type"], l["product"], l["quantity"]) for l in legs] == \
           [("SELL", "LIMIT", "CNC", 2941)] * 2
    assert legs[0]["price"] == 284.75                                     # stop limit: 286.20 * 0.995, down to tick
    assert legs[1]["price"] == 300.70


def test_kite_partial_fill_times_out_cancels_rest_and_protects_what_filled():
    kite = StubKite([{"status": "OPEN", "filled_quantity": 1000, "average_price": 290.0},
                     {"status": "OPEN", "filled_quantity": 1000, "average_price": 290.0},
                     {"status": "CANCELLED", "filled_quantity": 1000, "average_price": 290.0}])
    gw = engine.KiteOrderGateway(kite, poll_interval=0, fill_timeout=0)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert "order.cancel" in kite.routes()
    assert fill.quantity == 1000
    assert all(l["quantity"] == 1000 for l in json.loads(kite.requests[-1][3]["orders"]))


def test_kite_rejected_entry_places_no_gtt():
    kite = StubKite([{"status": "REJECTED", "filled_quantity": 0, "status_message": "Insufficient funds"}])
    fill = asyncio.run(engine.KiteOrderGateway(kite, poll_interval=0).execute(kite_plan()))
    assert fill is None and "gtt.place" not in kite.routes()


def test_kite_gtt_failure_is_reported_loudly_and_the_position_is_kept(caplog):
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_error=kiteconnect.exceptions.InputException("Trigger too close to LTP"))
    fill = asyncio.run(engine.KiteOrderGateway(kite, poll_interval=0).execute(kite_plan()))
    assert fill.quantity == 2941 and fill.exit_order_id is None
    assert "POSITION OPEN WITHOUT EXITS" in caplog.text


@pytest.mark.parametrize("ltp", [286.20, 280.0, 291.35])
def test_kite_entry_is_skipped_when_ltp_left_the_band(ltp, caplog):
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}], ltp=ltp)
    assert asyncio.run(engine.KiteOrderGateway(kite, poll_interval=0).execute(kite_plan())) is None
    assert kite.routes() == ["market.quote.ltp"] and "Entry skipped" in caplog.text


# ---------------------------------------------------------------- staleness
def test_signal_is_stale_once_its_bar_is_long_closed(caplog):
    s = sig()                                                             # bar 09:20-09:25
    fresh = router(clock=lambda: ist(2026, 9, 28, 9, 25, 30))
    stale = router(clock=lambda: ist(2026, 9, 29, 9, 15, 0))              # the next morning
    assert not fresh.is_stale(s) and stale.is_stale(s)
    run_router(stale, [s])
    assert stale.fills == [] and "stale; not trading it" in caplog.text
    assert not router(clock=lambda: ist(2030, 1, 1), max_signal_age=None).is_stale(s)


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


def test_paper_stop_gapped_through_fills_at_the_open():
    r = filled_router()
    r.on_bar("SWIGGY", ist(2026, 9, 28, 9, 25), bar(280.0, 281.0, 279.0, 280.5))
    closed = r.closed_positions[0]
    assert (closed.exit_reason, closed.exit_price) == ("STOP", 280.0)


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
