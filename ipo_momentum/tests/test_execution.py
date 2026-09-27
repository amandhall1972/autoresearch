"""ExecutionRouter sizing/risk and the Kite gateway, driven through the real kiteconnect SDK."""
import asyncio
import json
import logging
import re
import threading

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


# ---------------------------------------------------------------- Kite gateway on the real SDK
kiteconnect = pytest.importorskip("kiteconnect")
import requests  # noqa: E402 - installed with kiteconnect; without it the whole module is skipped


class StubKite(kiteconnect.KiteConnect):
    """The real SDK with only its HTTP transport replaced, so SDK-side validation still runs.

    Each scripted list is consumed in order (its last entry repeats); an Exception entry is raised.
    """

    def __init__(self, order_states, gtt_error=None, ltp=290.0, place_error=None, place_reached=False,
                 orders_result=None, cancel_results=None, gtt_results=None, gtt_created_before_error=False,
                 gtt_book=None, gtt_book_failures=0, orders_script=None, gtt_book_fail_reads=(),
                 gtt_hidden_reads=0, gtt_lands_on_next_place=False, gtt_created_status="active",
                 state_after_cancel=None):
        super().__init__(api_key="test_key", access_token="test_token")
        self.gtt_created_before_error = gtt_created_before_error
        self.gtt_book = gtt_book                  # GTTs that predate this entry, or an Exception: unreadable
        self.gtt_book_fail_reads = set(range(gtt_book_failures)) | set(gtt_book_fail_reads)   # read #s that fail
        self.gtt_reads = 0
        self.gtt_hidden_reads = gtt_hidden_reads      # a lost-reply GTT is booked only after this many reads
        self.gtt_lands_on_next_place = gtt_lands_on_next_place   # ... or only when the next request arrives
        self.gtt_created_status = gtt_created_status
        self.hidden_gtts = []
        self.state_after_cancel = state_after_cancel  # once a cancel arrives, order.info reports this
        self.cancelled = False
        self.created_gtts = []
        self.orders_script = list(orders_script) if orders_script else None
        self.order_states = list(order_states)
        self.last_price = ltp
        self.place_error, self.place_reached = place_error, place_reached
        self.orders_result = orders_result
        self.cancel_results = list(cancel_results or [{"order_id": "260928000000001"}])
        self.gtt_results = list(gtt_results or ([gtt_error] if gtt_error else [{"trigger_id": 777}]))
        self.requests = []
        self.placed_tag = None

    @staticmethod
    def _next(script):
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, Exception):
            raise item
        return item

    def _request(self, route, method, url_args=None, params=None, is_json=False, query_params=None):
        self.requests.append((route, method, url_args, params))
        if route == "market.quote.ltp":
            return {key: {"instrument_token": 1234, "last_price": self.last_price} for key in params["i"]}
        if route == "order.place":
            self.placed_tag = params.get("tag")
            if self.place_error:
                raise self.place_error
            return {"order_id": "260928000000001"}
        if route == "orders":
            if self.orders_script is not None:
                return [dict(o, tag=self.placed_tag) for o in self._next(self.orders_script)]
            if isinstance(self.orders_result, Exception):
                raise self.orders_result
            if self.orders_result is not None:
                return self.orders_result
            return [{"order_id": "260928000000001", "tag": self.placed_tag}] if self.place_reached else []
        if route == "order.info":
            if self.cancelled and self.state_after_cancel is not None:
                return [self.state_after_cancel]
            return [self._next(self.order_states)]
        if route == "order.cancel":
            self.cancelled = True
            return self._next(self.cancel_results)
        if route == "gtt.place":
            if self.gtt_lands_on_next_place:
                self.created_gtts.extend(self.hidden_gtts)
                self.hidden_gtts = []
            result = self.gtt_results.pop(0) if len(self.gtt_results) > 1 else self.gtt_results[0]
            if isinstance(result, Exception) and not self.gtt_created_before_error:
                raise result
            gid = 700 + len(self.created_gtts) + len(self.hidden_gtts)
            if not isinstance(result, Exception):
                gid = result.get("trigger_id", gid)
            condition, legs = json.loads(params["condition"]), json.loads(params["orders"])
            gtt = {"id": gid, "status": self.gtt_created_status, "condition": condition, "orders": legs}
            if isinstance(result, Exception):
                if self.gtt_lands_on_next_place or self.gtt_hidden_reads:
                    self.hidden_gtts.append(gtt)                          # booked later than the reply was lost
                else:
                    self.created_gtts.append(gtt)
                raise result                                              # created, but the reply was lost
            self.created_gtts.append(gtt)
            return dict(result, trigger_id=gid)
        if route == "gtt":
            read, self.gtt_reads = self.gtt_reads, self.gtt_reads + 1
            if isinstance(self.gtt_book, Exception):
                raise self.gtt_book
            if read in self.gtt_book_fail_reads:
                raise KE.NetworkException("Gateway timed out", code=504)
            if self.hidden_gtts and not self.gtt_lands_on_next_place:
                self.gtt_hidden_reads -= 1
                if self.gtt_hidden_reads <= 0:
                    self.created_gtts.extend(self.hidden_gtts)
                    self.hidden_gtts = []
            return list(self.gtt_book or []) + self.created_gtts
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
    # "gtt": the GTT book's ids before arming, so an ambiguous failure can never adopt an older GTT.
    assert kite.routes() == ["market.quote.ltp", "order.place", "order.info", "order.info", "gtt", "gtt.place"]
    _, _, url_args, entry = kite.requests[1]
    assert url_args == {"variety": "regular"}
    assert re.fullmatch(r"ipm[0-9a-f]{12}", entry.pop("tag"))            # unique, so a lost reply can be traced
    assert entry == {"variety": "regular", "exchange": "NSE", "tradingsymbol": "SWIGGY", "transaction_type": "BUY",
                     "quantity": 2941, "product": "CNC", "order_type": "LIMIT", "price": 291.30, "validity": "DAY"}
    gtt = kite.requests[-1][3]
    assert gtt["type"] == "two-leg"
    condition, legs = json.loads(gtt["condition"]), json.loads(gtt["orders"])
    assert condition["trigger_values"] == [286.20, 300.70] and condition["last_price"] == 290.1
    assert [(l["transaction_type"], l["order_type"], l["product"], l["quantity"]) for l in legs] == \
           [("SELL", "LIMIT", "CNC", 2941)] * 2
    assert legs[0]["price"] == 280.45                                     # stop limit: 286.20 * 0.98, down to tick
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


# ---------------------------------------------------------------- live order safety (real SDK, scripted transport)
KE = kiteconnect.exceptions
OPEN = {"status": "OPEN", "filled_quantity": 0}


def fast_gateway(kite, **kw):
    kw.setdefault("poll_interval", 0)
    kw.setdefault("fill_timeout", 0.05)
    kw.setdefault("cancel_grace", 0.05)
    return engine.KiteOrderGateway(kite, **kw)


def test_transient_order_history_errors_keep_polling_instead_of_abandoning_the_order():
    kite = StubKite([KE.NetworkException("Gateway timed out", code=504), KE.NetworkException("Too many requests", code=429),
                     {"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])
    fill = asyncio.run(fast_gateway(kite, fill_timeout=5).execute(kite_plan()))
    assert fill.quantity == 2941 and fill.exit_order_id == "777"          # v1.1 raised and released the symbol


def test_a_cancel_that_never_lands_keeps_the_symbol_blocked(caplog):
    kite = StubKite([OPEN], cancel_results=[KE.NetworkException("Too many requests", code=429)])
    r = run_router(router(gateway=fast_gateway(kite)), [sig(), sig()])
    assert r.active_inventory == {"SWIGGY"} and r.fills == []
    assert len(r.unresolved) == 1 and "not terminal" in r.unresolved[0]
    assert kite.routes().count("order.place") == 1                         # the second signal did not re-enter
    assert "CHECK THE BROKER TERMINAL" in caplog.text


def test_the_gtt_covers_the_final_filled_quantity_after_a_cancel():
    kite = StubKite([{"status": "OPEN", "filled_quantity": 1000, "average_price": 290.0},
                     {"status": "OPEN", "filled_quantity": 1000, "average_price": 290.0},
                     {"status": "OPEN", "filled_quantity": 1000, "average_price": 290.0},
                     {"status": "CANCELLED", "filled_quantity": 1941, "average_price": 290.05}])
    fill = asyncio.run(fast_gateway(kite, fill_timeout=0, cancel_grace=5).execute(kite_plan()))
    assert fill.quantity == 1941                                          # v1.1 protected only the first 1000
    assert all(leg["quantity"] == 1941 for leg in json.loads(kite.requests[-1][3]["orders"]))


def test_a_lost_place_order_reply_is_found_by_its_tag_and_protected():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    place_error=KE.NetworkException("Read timed out", code=504), place_reached=True)
    fill = asyncio.run(fast_gateway(kite).execute(kite_plan()))
    assert fill.order_id == "260928000000001" and fill.exit_order_id == "777"
    assert kite.routes()[:3] == ["market.quote.ltp", "order.place", "orders"]


def test_a_request_that_never_left_the_machine_releases_the_symbol_without_a_lookup():
    kite = StubKite([OPEN], place_error=requests.exceptions.ConnectTimeout("connect timed out"))
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert r.active_inventory == set() and r.unresolved == [] and "orders" not in kite.routes()


def test_an_ambiguous_failure_whose_order_never_shows_keeps_the_symbol_blocked():
    # A 5xx or read timeout may still book the order moments later (v1.2 trusted one empty snapshot).
    kite = StubKite([OPEN], place_error=KE.NetworkException("Gateway timed out", code=504), place_reached=False)
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert r.active_inventory == {"SWIGGY"} and "never appeared in the order book" in r.unresolved[0]


def test_an_order_booked_after_the_lost_reply_is_found_by_polling_the_book():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    place_error=requests.exceptions.ReadTimeout("read timed out"),
                    orders_script=[[], [], [{"order_id": "260928000000001"}]])     # booked on the third look
    fill = asyncio.run(fast_gateway(kite, cancel_grace=5).execute(kite_plan()))
    assert fill.order_id == "260928000000001" and kite.routes().count("orders") == 3


def test_an_unverifiable_place_order_failure_keeps_the_symbol_blocked():
    kite = StubKite([OPEN], place_error=KE.NetworkException("Read timed out", code=504),
                    orders_result=KE.NetworkException("Gateway timed out", code=504))
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert r.active_inventory == {"SWIGGY"} and "the order book could not be read" in r.unresolved[0]


def test_an_api_refusal_releases_the_symbol_without_a_lookup():
    kite = StubKite([OPEN], place_error=KE.InputException("Insufficient funds"))
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert r.active_inventory == set() and "orders" not in kite.routes()


def test_an_entry_interrupted_by_shutdown_still_settles_and_its_fill_is_protected_once(caplog):
    polled = threading.Event()

    class SignallingKite(StubKite):
        def _request(self, route, *args, **kwargs):
            if route == "order.info":
                polled.set()                                              # the entry is now working
            return super()._request(route, *args, **kwargs)

    kite = SignallingKite([OPEN, OPEN, OPEN, {"status": "CANCELLED", "filled_quantity": 800, "average_price": 290.2}])

    async def scenario():
        gw = fast_gateway(kite, fill_timeout=30, poll_interval=0.01, cancel_grace=5)
        task = asyncio.create_task(gw.execute(kite_plan()))
        assert await asyncio.to_thread(polled.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await gw.wait_inflight()
        return gw

    gw = asyncio.run(scenario())
    assert kite.routes()[-1] == "gtt.place" and kite.routes().count("gtt.place") == 1   # v1.1: no GTT at all
    assert json.loads(kite.requests[-1][3]["orders"])[0]["quantity"] == 800
    assert [f.quantity for f in gw.late_fills] == [800]
    assert any("filled 800 while shutting down; exits armed (GTT 777)" in a for a in gw.alerts)


def test_a_gtt_that_was_never_created_is_retried():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 778}])
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "778" and kite.routes().count("gtt.place") == 2
    assert [g["id"] for g in kite.created_gtts] == [778] and gw.alerts == []


def test_a_lost_gtt_reply_adopts_the_existing_gtt_instead_of_arming_a_second():
    # v1.2 retried blindly: two OCO GTTs, each selling the whole position.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True)
    fill = asyncio.run(fast_gateway(kite).execute(kite_plan()))
    assert len(kite.created_gtts) == 1 and fill.exit_order_id == "700"


def test_an_unreadable_gtt_book_stops_the_retries_and_raises_an_alert():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_book=KE.NetworkException("Gateway timed out", code=504))
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and kite.routes().count("gtt.place") == 1
    assert "GTT STATE UNKNOWN for 2941 shares" in gw.alerts[0]


def test_an_identical_gtt_from_an_earlier_run_is_never_adopted():
    earlier = {"id": 650, "status": "active", "condition": {"tradingsymbol": "SWIGGY", "trigger_values": [286.2, 300.7]},
               "orders": [{"quantity": 2941}, {"quantity": 2941}]}
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_book=[earlier])
    fill = asyncio.run(fast_gateway(kite).execute(kite_plan()))
    assert fill.exit_order_id == "700" and len(kite.created_gtts) == 1    # this entry's GTT, not 650


def test_a_lost_gtt_reply_after_an_unreadable_snapshot_is_reported_not_guessed():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_book_failures=3)          # all 3 snapshot tries fail
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and len(kite.created_gtts) == 1     # no second GTT either
    assert "GTT 700 matches this position" in gw.alerts[0] and "CHECK THE GTT BOOK" in gw.alerts[0]


def test_a_shutdown_during_a_slow_gtt_placement_arms_exactly_one_gtt():
    # v1.2's shutdown path re-ran _protect while the first place_gtt was still in flight: two GTTs.
    started, release = threading.Event(), threading.Event()

    class SlowGtt(StubKite):
        def _request(self, route, *args, **kwargs):
            if route == "gtt.place":
                started.set()
                release.wait(5)
            return super()._request(route, *args, **kwargs)

    kite = SlowGtt([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])

    async def scenario():
        gw = fast_gateway(kite)
        task = asyncio.create_task(gw.execute(kite_plan()))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await gw.wait_inflight()
        return gw

    gw = asyncio.run(scenario())
    assert kite.routes().count("gtt.place") == 1 and len(kite.created_gtts) == 1
    assert [f.exit_order_id for f in gw.late_fills] == ["777"]
    assert any("filled 2941 while shutting down; exits armed (GTT 777)" in a for a in gw.alerts)


def test_a_shutdown_during_place_order_still_settles_the_entry():
    # v1.2 raced the in-flight place_order and could report that the entry "never reached the exchange".
    started, release = threading.Event(), threading.Event()

    class SlowPlace(StubKite):
        def _request(self, route, *args, **kwargs):
            if route == "order.place":
                started.set()
                release.wait(5)
            return super()._request(route, *args, **kwargs)

    kite = SlowPlace([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])

    async def scenario():
        gw = fast_gateway(kite)
        task = asyncio.create_task(gw.execute(kite_plan()))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await gw.wait_inflight()
        return gw

    gw = asyncio.run(scenario())
    assert kite.routes()[1:] == ["order.place", "order.info", "gtt", "gtt.place"]
    assert [f.quantity for f in gw.late_fills] == [2941] and "exits armed (GTT 777)" in gw.alerts[0]


def test_a_partial_fill_whose_cancel_is_unconfirmed_is_still_protected_and_recorded(caplog):
    # v1.2 raised with no GTT, leaving 800 bought shares without exits (a v1.1 -> v1.2 regression).
    kite = StubKite([{"status": "OPEN", "filled_quantity": 800, "average_price": 290.2}])
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert json.loads(kite.requests[-1][3]["orders"])[0]["quantity"] == 800
    assert [(f.quantity, f.exit_order_id) for f in r.fills] == [(800, "777")] and r.positions["SWIGGY"].quantity == 800
    assert r.active_inventory == {"SWIGGY"}                               # the remainder may still be working
    assert "800 shares already bought are covered by GTT 777" in r.unresolved[0]


def test_a_gtt_booked_after_the_first_book_read_is_adopted():
    # v1.3 read the book once, straight after the lost reply, and then armed a second whole-position GTT.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_hidden_reads=3)
    gw = fast_gateway(kite, cancel_grace=5, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "700" and [g["id"] for g in kite.created_gtts] == [700] and gw.alerts == []


def test_a_gtt_booked_after_the_retry_is_reported_as_a_duplicate():
    # The broker books the lost request's GTT only after the engine stopped waiting and retried.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_lands_on_next_place=True)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "999" and sorted(g["id"] for g in kite.created_gtts) == [700, 999]
    assert gw.alerts == ["[SWIGGY] GTT DUPLICATE: 700 (active) also sell the 2941 shares of order 260928000000001 "
                         "that GTT 999 protects. DELETE ALL BUT GTT 999."]


def test_when_every_gtt_request_fails_ambiguously_the_alert_says_one_may_exist():
    # v1.3 said "no GTT could be placed" although each lost request may have created one.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504)])
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and kite.routes().count("gtt.place") == 3
    assert "GTT STATE UNKNOWN for 2941 shares" in gw.alerts[0] and "may still have been created" in gw.alerts[0]


def test_a_lost_reply_gtt_that_already_triggered_is_adopted_not_rearmed():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_created_status="triggered")
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "700" and kite.routes().count("gtt.place") == 1   # v1.3 armed a second GTT
    assert "GTT 700 for 2941 shares" in gw.alerts[0] and "already TRIGGERED" in gw.alerts[0]


def test_a_gtt_request_that_never_left_is_retried_without_the_book():
    # v1.3 needed the book after a connect timeout, and an outage that also hid the book ended protection.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ConnectTimeout("connect timed out"), {"trigger_id": 778}],
                    gtt_book=KE.NetworkException("Gateway timed out", code=504))
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "778" and gw.alerts == []


def test_one_failed_book_read_after_an_ambiguous_failure_does_not_end_protection():
    # v1.3 gave up for good when the single read after a 429 failed; the book is now polled.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Too many requests", code=429), {"trigger_id": 778}],
                    gtt_book_fail_reads={1})                              # read 0 is the snapshot
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "778" and gw.alerts == [] and [g["id"] for g in kite.created_gtts] == [778]


def test_a_5xx_order_exception_is_looked_up_not_taken_as_a_refusal():
    # The SDK sets the class from the body and the code from the HTTP status: an OrderException can be a 503.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    place_error=KE.OrderException("Order request timed out", code=503), place_reached=True)
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert "orders" in kite.routes() and [f.quantity for f in r.fills] == [2941]   # v1.3 released the symbol


def test_shutdown_stops_waiting_for_the_fill_and_cancels_the_remainder_at_once():
    # v1.3 kept waiting the full 30 s fill window, longer than `docker stop` allows before SIGKILL.
    polled = threading.Event()

    class SignallingKite(StubKite):
        def _request(self, route, *args, **kwargs):
            if route == "order.info":
                polled.set()
            return super()._request(route, *args, **kwargs)

    kite = SignallingKite([{"status": "OPEN", "filled_quantity": 300, "average_price": 290.2}],
                          state_after_cancel={"status": "CANCELLED", "filled_quantity": 300, "average_price": 290.2})

    async def scenario():
        gw = fast_gateway(kite, fill_timeout=30, poll_interval=0.01, cancel_grace=5)
        task = asyncio.create_task(gw.execute(kite_plan()))
        assert await asyncio.to_thread(polled.wait, 5)
        gw.stopping = True
        return await asyncio.wait_for(task, 5)

    fill = asyncio.run(scenario())
    assert "order.cancel" in kite.routes() and fill.quantity == 300 and fill.exit_order_id == "777"


def test_no_entry_is_sent_once_shutdown_began():
    kite = StubKite([OPEN])
    gw = fast_gateway(kite)
    gw.stopping = True
    assert asyncio.run(gw.execute(kite_plan())) is None and kite.routes() == ["market.quote.ltp"]


def test_the_halt_report_lists_fills_that_completed_during_shutdown(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    gw = engine.PaperGateway()
    gw.late_fills.append(engine.Fill("SWIGGY", 800, 290.2, "260928000000001", "777"))
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    code = engine._halt_report(router(gateway=gw), gw, ticker, exit_code=0, stop_signal=None)
    assert "Open SWIGGY: 800 @ 290.20 | filled during shutdown | exits: 777" in caplog.text and code == 0


@pytest.mark.parametrize("error,permanent", [
    (KE.GeneralException("Insufficient funds", code=400), True),          # MarginException arrives like this
    (KE.GeneralException("Holdings not found", code=400), True),          # HoldingException, UserException, ...
    (KE.OrderException("Order rejected", code=400), True),
    (KE.InputException("Invalid quantity", code=400), True),
    (KE.TokenException("Token expired", code=403), True),
    (KE.NetworkException("Too many requests", code=429), False),
    (KE.NetworkException("Gateway timed out", code=504), False),
    (KE.DataException("Unparsable response", code=502), False),
    (KE.GeneralException("Internal error", code=500), False),
    (KE.OrderException("Order request timed out", code=503), False),       # v1.3: permanent by name
    (KE.OrderException("Too many requests", code=429), False),
    (requests.exceptions.ReadTimeout("read timed out"), False),
])
def test_broker_refusals_are_permanent_and_outages_are_not(error, permanent):
    assert engine.is_permanent_error(error) is permanent                  # v1.2: every GeneralException transient
