"""KiteOrderGateway driven through the real kiteconnect SDK, with only its HTTP transport scripted."""
import asyncio
import json
import logging
import re
import threading

import pytest

import engine
from test_execution import router, run_router, sig  # helpers only: nothing is collected twice

kiteconnect = pytest.importorskip("kiteconnect")
import requests  # noqa: E402 - installed with kiteconnect


class StubKite(kiteconnect.KiteConnect):
    """The real SDK with only its HTTP transport replaced, so SDK-side validation still runs.

    Each scripted list is consumed in order (its last entry repeats); an Exception entry is raised.
    """

    def __init__(self, order_states, gtt_error=None, ltp=290.0, place_error=None, place_reached=False,
                 orders_result=None, cancel_results=None, gtt_results=None, gtt_created_before_error=False,
                 gtt_book=None, gtt_book_failures=0, orders_script=None, gtt_book_fail_reads=(),
                 gtt_hidden_reads=0, gtt_lands_on_next_place=False, gtt_created_status="active",
                 state_after_cancel=None, gtt_book_fail=None, gtt_booked=None):
        super().__init__(api_key="test_key", access_token="test_token")
        self.gtt_created_before_error = gtt_created_before_error
        self.gtt_booked = gtt_booked                  # per request: did a failed one still book its GTT?
        self.gtt_book = gtt_book                  # GTTs that predate this entry, or an Exception: unreadable
        self.gtt_book_fail_reads = set(range(gtt_book_failures)) | set(gtt_book_fail_reads)   # read #s that fail
        self.gtt_reads = 0
        self.gtt_book_fail = gtt_book_fail            # (reads since the last GTT request, requests so far) -> fail?
        self.gtt_places, self.reads_since_place = 0, 0
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
            self.gtt_places, self.reads_since_place = self.gtt_places + 1, 0
            if self.gtt_lands_on_next_place:
                self.created_gtts.extend(self.hidden_gtts)
                self.hidden_gtts = []
            result = self.gtt_results.pop(0) if len(self.gtt_results) > 1 else self.gtt_results[0]
            booked = self.gtt_created_before_error if self.gtt_booked is None else self.gtt_booked[self.gtt_places - 1]
            if isinstance(result, Exception) and not booked:
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
            since, self.reads_since_place = self.reads_since_place, self.reads_since_place + 1
            if isinstance(self.gtt_book, Exception):
                raise self.gtt_book
            if read in self.gtt_book_fail_reads or (self.gtt_book_fail and self.gtt_book_fail(since, self.gtt_places)):
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
    # "gtt" first: the GTT book's ids, read before anything is bought (so its latency never delays the
    # exits) and before the LTP check (which stays back to back with place_order).
    assert kite.routes() == ["gtt", "market.quote.ltp", "order.place", "order.info", "order.info", "gtt.place"]
    _, _, url_args, entry = kite.requests[2]
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
    assert kite.routes() == ["gtt", "market.quote.ltp"] and "Entry skipped" in caplog.text


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
    assert kite.routes()[1:4] == ["market.quote.ltp", "order.place", "orders"]


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
    assert fill.exits_unknown is True                                     # GTT 700 exists: never "exits: NONE"
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
    assert kite.routes()[2:] == ["order.place", "order.info", "gtt.place"]
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
    assert fill.exit_order_id is None and kite.routes().count("gtt.place") == 3 and fill.exits_unknown is True
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
    assert asyncio.run(gw.execute(kite_plan())) is None and kite.routes() == ["gtt", "market.quote.ltp"]


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


# ---------------------------------------------------------------- round 5: windows judged on their latest read
def refused_connection():
    """The exception requests really raises when a connection cannot be opened (nothing was sent)."""
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]                                   # closed again: connections are refused
    session = requests.Session()
    session.trust_env = False                                           # no proxy in between
    try:
        session.get(f"http://127.0.0.1:{port}/", timeout=2)
    except requests.exceptions.ConnectionError as e:
        return e
    raise AssertionError("the connection was not refused")


def test_a_gtt_booked_while_the_book_is_unreadable_is_still_adopted():
    # v1.4 counted the window as read if its first read succeeded: a GTT booked just after that read,
    # while every later read failed, was never seen and a second one was armed.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_hidden_reads=2,
                    gtt_book_fail=lambda since, places: places == 1 and 1 <= since < 30)
    gw = fast_gateway(kite, cancel_grace=1.0, poll_interval=0.05)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "700" and [g["id"] for g in kite.created_gtts] == [700] and gw.alerts == []


def test_a_duplicate_watch_whose_last_read_failed_says_so():
    # v1.4 stayed silent if the watch's first read succeeded, whatever happened afterwards.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_hidden_reads=10 ** 6,
                    gtt_book_fail=lambda since, places: places >= 2 and since >= 1)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "999"
    assert gw.alerts == ["[SWIGGY] GTT 999 armed after an ambiguous failure, but the GTT book could not be read "
                         "at the end of the watch to rule out a duplicate. CHECK THE GTT BOOK."]


def test_a_duplicate_that_already_triggered_is_reported_as_a_fired_exit():
    # v1.4 said "DELETE ALL BUT GTT 999", i.e. keep the new GTT over shares the fired one is already selling.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_lands_on_next_place=True, gtt_created_status="triggered")
    gw = fast_gateway(kite)
    asyncio.run(gw.execute(kite_plan()))
    assert "GTT 700 has already TRIGGERED: the exit has fired. DELETE GTT 999" in gw.alerts[0]
    assert "DELETE ALL BUT" not in gw.alerts[0]


def test_a_refused_connection_releases_the_symbol_without_a_lookup():
    # A refused connection never sent the order; v1.4 treated it as a lost reply and blocked the symbol.
    kite = StubKite([OPEN], place_error=refused_connection())
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert r.active_inventory == set() and r.unresolved == [] and "orders" not in kite.routes()


def test_a_gtt_is_retried_through_a_short_outage_that_refuses_connections():
    # v1.4 treated a refused GTT request as ambiguous, polled an unreachable book and gave up.
    refused = refused_connection()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused, refused, {"trigger_id": 778}],
                    gtt_book=KE.NetworkException("Gateway timed out", code=504))
    gw = fast_gateway(kite, cancel_grace=5, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "778" and gw.alerts == []


def test_a_refused_gtt_request_that_outlasts_the_window_says_no_gtt_exists():
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_connection()])
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and kite.created_gtts == []
    assert "POSITION OPEN WITHOUT EXITS" in gw.alerts[0] and "GTT STATE UNKNOWN" not in gw.alerts[0]


@pytest.mark.parametrize("script", ["refused, 504, refused", "504, refused, 504, refused", "refused, read timeout, refused"])
def test_a_second_outage_after_an_ambiguous_attempt_gets_its_own_window(script):
    # v1.5 opened one never-sent window for the whole GTT loop: an ambiguous attempt's book poll used it
    # up, so a second short outage broke out at once and left the position without a GTT.
    make = {"refused": refused_connection, "504": lambda: KE.NetworkException("Gateway timed out", code=504),
            "read timeout": lambda: requests.exceptions.ReadTimeout("read timed out")}
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[make[step]() for step in script.split(", ")] + [{"trigger_id": 778}])
    gw = fast_gateway(kite, cancel_grace=0.3, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "778" and [g["id"] for g in kite.created_gtts] == [778] and gw.alerts == []


def test_the_shutdown_estimate_counts_a_never_sent_window_before_each_gtt_attempt():
    gw = engine.KiteOrderGateway(StubKite([OPEN]), fill_timeout=30, poll_interval=1, cancel_grace=15)
    assert gw.settle_timeout == 30 + 12 * 15 + 20 * (7 + 1)


def closed_port():
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]                                   # closed again: connections are refused


def proxied_session(proxy_port):
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": f"http://127.0.0.1:{proxy_port}", "https": f"http://127.0.0.1:{proxy_port}"}
    return session


def unreachable_proxy():
    """What requests raises when the proxy itself refuses the connection: nothing reached Kite."""
    try:
        proxied_session(closed_port()).get("https://api.kite.trade/orders", timeout=2)
    except requests.exceptions.ProxyError as e:
        return e
    raise AssertionError("the proxy connection was not refused")


def refused_tunnel():
    """What requests raises when the proxy was reached but refused the CONNECT (403): the proxy got the
    request line, so this is no proof that nothing was sent."""
    import socket
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer():
        conn, _ = server.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        conn.close()
    threading.Thread(target=answer, daemon=True).start()
    try:
        proxied_session(server.getsockname()[1]).get("https://api.kite.trade/orders", timeout=2)
    except requests.exceptions.ProxyError as e:
        return e
    finally:
        server.close()
    raise AssertionError("the tunnel was not refused")


def test_an_unreachable_proxy_counts_as_never_sent_but_a_refused_tunnel_does_not():
    import urllib3
    timed_out = requests.exceptions.ProxyError(urllib3.exceptions.MaxRetryError(
        None, "https://api.kite.trade/orders",
        reason=urllib3.exceptions.ProxyError("Unable to connect to proxy",
                                             urllib3.exceptions.ConnectTimeoutError(None, "timed out"))))
    assert engine.KiteOrderGateway._never_sent(unreachable_proxy())
    assert engine.KiteOrderGateway._never_sent(timed_out)
    assert not engine.KiteOrderGateway._never_sent(refused_tunnel())


def test_an_unreachable_proxy_releases_the_symbol_without_a_lookup():
    # v1.5 recognised only direct connect failures: behind a dead proxy the entry looked its tag up
    # through the same proxy and blocked the symbol for the session.
    kite = StubKite([OPEN], place_error=unreachable_proxy())
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert r.active_inventory == set() and r.unresolved == [] and "orders" not in kite.routes()


def test_an_entry_refused_at_the_proxys_tunnel_releases_the_symbol():
    # A proxy that refuses the CONNECT received only the CONNECT line: the order never left. v1.6 looked the
    # tag up through the same failing proxy and blocked the symbol for the session.
    kite = StubKite([OPEN], place_error=refused_tunnel(), orders_result=[])
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert "orders" not in kite.routes() and r.active_inventory == set() and r.unresolved == []


def test_a_gtt_refused_at_the_proxys_tunnel_stays_on_the_ambiguous_path():
    # Attempts plus book polls ride out a longer proxy outage than a single never-sent window.
    tunnel = refused_tunnel()
    assert not engine.KiteOrderGateway._never_sent(tunnel)
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[tunnel, {"trigger_id": 777}])
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "777" and "gtt" in kite.routes()[kite.routes().index("gtt.place"):]


@pytest.mark.parametrize("status", ["active", "triggered"])
def test_a_gtt_booked_during_a_later_outage_is_adopted_not_armed_again(status):
    # The 504's GTT is booked only after the 15 s poll, while the next request is refused. v1.6 retried
    # through that outage without reading the book again and armed a second whole-position GTT.
    refused = refused_connection()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused, KE.NetworkException("Gateway timed out", code=504), refused,
                                 {"trigger_id": 902}],
                    gtt_booked=[False, True, False, False], gtt_lands_on_next_place=True, gtt_created_status=status)
    gw = fast_gateway(kite, cancel_grace=0.3, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "700" and [g["id"] for g in kite.created_gtts] == [700]
    if status == "active":
        assert gw.alerts == []
    else:
        assert len(gw.alerts) == 1 and "had already TRIGGERED" in gw.alerts[0]


def test_after_an_ambiguous_attempt_nothing_is_placed_while_the_book_is_unreadable():
    refused = refused_connection()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused, KE.NetworkException("Gateway timed out", code=504), refused,
                                 {"trigger_id": 902}],
                    gtt_booked=[False, True, False, False], gtt_lands_on_next_place=True,
                    gtt_book_fail=lambda since, places: places == 3 and since < 3)    # the outage hides the book too
    gw = fast_gateway(kite, cancel_grace=0.3, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "700" and kite.gtt_places == 3 and gw.alerts == []


def test_an_outage_after_an_ambiguous_attempt_whose_gtt_never_lands_still_arms_one():
    refused = refused_connection()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused, KE.NetworkException("Gateway timed out", code=504), refused,
                                 {"trigger_id": 902}])
    gw = fast_gateway(kite, cancel_grace=0.3, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "902" and [g["id"] for g in kite.created_gtts] == [902] and gw.alerts == []


def test_a_permanent_error_while_rechecking_the_book_ends_the_loop_and_is_named():
    # v1.7 kept reading a book that answered 403 (an expired session) until the window ended, then blamed
    # the earlier refused connection in the alert.
    refused = refused_connection()
    token = KE.TokenException("Incorrect `api_key` or `access_token`.", code=403)
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), refused, token],
                    gtt_book_fail=None)
    reads = {"n": 0}
    original = kite._request

    def request(route, method, *args, **kwargs):
        if route == "gtt" and kite.gtt_places >= 2:                       # the session died after the blip
            reads["n"] += 1
            raise token
        return original(route, method, *args, **kwargs)

    kite._request = request
    gw = fast_gateway(kite, cancel_grace=1.0, poll_interval=0.05)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and reads["n"] == 1 and kite.gtt_places == 2 and fill.exits_unknown is True
    assert "GTT STATE UNKNOWN" in gw.alerts[-1] and "TokenException" in gw.alerts[-1]


# ---------------------------------------------------------------- round 9: every GTT-book loop stops on a permanent error
def expiring_book(kite, after_places, token):
    """From the ``after_places``-th GTT request on, every GTT book read answers ``token``; returns the count."""
    reads = {"n": 0}
    original = kite._request

    def request(route, method, *args, **kwargs):
        if route == "gtt" and kite.gtt_places >= after_places:
            reads["n"] += 1
            raise token
        return original(route, method, *args, **kwargs)

    kite._request = request
    return reads


def test_a_permanent_error_while_polling_for_a_lost_reply_gtt_ends_the_poll_and_is_named():
    # v1.8 polled an expired session's book for two windows after the 504, then blamed only the 504.
    token = KE.TokenException("Incorrect `api_key` or `access_token`.", code=403)
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 778}])
    reads = expiring_book(kite, 1, token)
    gw = fast_gateway(kite, cancel_grace=1.0, poll_interval=0.05)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and reads["n"] == 1 and kite.gtt_places == 1
    assert gw.alerts == ["[SWIGGY] GTT STATE UNKNOWN for 2941 shares (order 260928000000001): placing failed "
                         "(NetworkException('Gateway timed out')) and the GTT book could not be read "
                         "(TokenException('Incorrect `api_key` or `access_token`.')). CHECK THE GTT BOOK."]


def test_a_permanent_error_during_the_duplicate_watch_ends_the_watch_and_is_named():
    # v1.8 kept reading for the whole watch and said only that the book "could not be read at the end".
    token = KE.TokenException("Incorrect `api_key` or `access_token`.", code=403)
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 778}])
    reads = expiring_book(kite, 2, token)                                  # the session dies once 778 is armed
    gw = fast_gateway(kite, cancel_grace=1.0, poll_interval=0.05)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "778" and reads["n"] == 1 and kite.gtt_places == 2
    assert gw.alerts == ["[SWIGGY] GTT 778 armed after an ambiguous failure, but the GTT book could not be read "
                         "(TokenException('Incorrect `api_key` or `access_token`.')) to rule out a duplicate. "
                         "CHECK THE GTT BOOK."]


def test_a_transient_error_while_polling_for_a_lost_reply_gtt_still_extends_the_poll():
    # Control: only a permanent error ends the poll early; a 504 book keeps it going a second window.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 778}])
    reads = expiring_book(kite, 1, KE.NetworkException("Gateway timed out", code=504))
    gw = fast_gateway(kite, cancel_grace=0.2, poll_interval=0.02)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and reads["n"] >= 15 and kite.gtt_places == 1
    assert len(gw.alerts) == 1 and "GTT STATE UNKNOWN for 2941 shares" in gw.alerts[0]


# ---------------------------------------------------------------- round 10: an unknown GTT state is reported as unknown
TOKEN = KE.TokenException("Incorrect `api_key` or `access_token`.", code=403)


def lost_reply_then_expired(order_states):
    """place_gtt answers 504 but the broker books the GTT anyway; every later book read answers 403."""
    kite = StubKite(order_states, gtt_results=[KE.NetworkException("Gateway timed out", code=504)],
                    gtt_created_before_error=True)
    expiring_book(kite, 1, TOKEN)
    return kite


def test_a_fill_whose_gtt_state_is_unknown_is_reported_as_unknown_not_as_unprotected(caplog):
    # v1.9 returned the same Fill for "a GTT may exist" as for "no GTT": the halt report said "exits: NONE" next to
    # the alert telling the operator a GTT may exist (here one does), inviting a manual second exit.
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    kite = lost_reply_then_expired([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])
    gw = fast_gateway(kite)
    r = run_router(router(gateway=gw), [sig()])
    assert [(f.exit_order_id, f.exits_unknown) for f in r.fills] == [(None, True)] and len(kite.created_gtts) == 1
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    assert engine._halt_report(r, gw, ticker, exit_code=0, stop_signal=None) == 1
    assert "exits: UNKNOWN)" in caplog.text and "exits: NONE" not in caplog.text


def test_an_unconfirmed_cancel_says_the_bought_shares_gtt_state_is_unknown():
    kite = lost_reply_then_expired([{"status": "OPEN", "filled_quantity": 800, "average_price": 290.2}])
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert "800 shares already bought are of UNKNOWN GTT state (a GTT may exist: CHECK THE GTT BOOK)" in r.unresolved[0]
    assert "NOT covered" not in r.unresolved[0]


def test_a_fill_during_shutdown_whose_gtt_state_is_unknown_says_so(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    gw = fast_gateway(StubKite([OPEN]))

    async def settled():
        return engine.Fill("SWIGGY", 2941, 290.1, "260928000000001", None, exits_unknown=True)

    async def scenario():
        task = asyncio.ensure_future(settled())
        await task
        gw._report_late("SWIGGY", task)

    asyncio.run(scenario())
    assert gw.alerts == ["[SWIGGY] Entry 260928000000001 filled 2941 while shutting down; exits UNKNOWN (a GTT may "
                         "exist: CHECK THE GTT BOOK)."]
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    engine._halt_report(router(gateway=gw), gw, ticker, exit_code=0, stop_signal=None)
    assert "Open SWIGGY: 2941 @ 290.10 | filled during shutdown | exits: UNKNOWN" in caplog.text


def test_a_position_that_surely_has_no_gtt_still_reports_none(caplog):
    # Control: a refusal (no request can have created a GTT) is still "NONE", not "UNKNOWN".
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_error=KE.InputException("Trigger too close to LTP", code=400))
    gw = fast_gateway(kite)
    r = run_router(router(gateway=gw), [sig()])
    assert [f.exit_order_id for f in r.fills] == [None]
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    engine._halt_report(r, gw, ticker, exit_code=0, stop_signal=None)
    assert "exits: NONE)" in caplog.text and "UNKNOWN" not in caplog.text


# ---------------------------------------------------------------- round 11: what the reports say a GTT is
def test_a_never_sent_window_that_expires_after_an_ambiguous_attempt_reports_the_gtt_state_unknown():
    # The 504 may have created a GTT that the refused retries never saw: its state is unknown, not "NONE".
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), refused_connection()])
    gw = fast_gateway(kite, cancel_grace=0.2, poll_interval=0.02)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and fill.exits_unknown is True
    assert "GTT STATE UNKNOWN" in gw.alerts[-1]


@pytest.mark.parametrize("book_readable", [True, False])
def test_gtt_requests_refused_only_at_the_proxys_tunnel_leave_a_position_known_to_have_no_gtt(book_readable, caplog):
    # A refused tunnel is retried as ambiguous (a longer proxy outage is ridden out), but it carried nothing to Kite.
    # v1.10 reported "GTT STATE UNKNOWN" and "exits: UNKNOWN" although no GTT can exist.
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    tunnel = refused_tunnel()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[tunnel], gtt_book=None if book_readable else tunnel)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    r = run_router(router(gateway=gw), [sig()])
    assert [(f.exit_order_id, f.exits_unknown) for f in r.fills] == [(None, False)] and kite.created_gtts == []
    assert kite.gtt_places == 3                                            # nothing can exist: every attempt is used
    assert "POSITION OPEN WITHOUT EXITS" in gw.alerts[-1] and not any("GTT STATE UNKNOWN" in a for a in gw.alerts)
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    engine._halt_report(r, gw, ticker, exit_code=0, stop_signal=None)
    assert "exits: NONE)" in caplog.text


def test_a_504_before_a_refused_tunnel_still_leaves_the_gtt_state_unknown():
    # Control: the 504 may have reached Kite.
    tunnel = refused_tunnel()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), tunnel], gtt_book=tunnel)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id is None and fill.exits_unknown is True and "GTT STATE UNKNOWN" in gw.alerts[-1]


def triggered_lost_reply(order_states, **kw):
    return StubKite(order_states, gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_created_status="triggered", **kw)


def test_a_gtt_that_had_already_fired_is_not_reported_as_the_positions_armed_exit(caplog):
    # v1.10 said "exits: 700" next to the alert that GTT 700 had already TRIGGERED.
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    kite = triggered_lost_reply([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])
    gw = fast_gateway(kite)
    r = run_router(router(gateway=gw), [sig()])
    assert [(f.exit_order_id, f.exits_fired) for f in r.fills] == [("700", True)]
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    engine._halt_report(r, gw, ticker, exit_code=0, stop_signal=None)
    assert f"exits: 700 ({engine.FIRED_NOTE}))" in caplog.text


def test_an_unconfirmed_cancel_does_not_call_a_fired_gtt_cover():
    kite = triggered_lost_reply([{"status": "OPEN", "filled_quantity": 800, "average_price": 290.2}])
    r = run_router(router(gateway=fast_gateway(kite)), [sig()])
    assert f"800 shares already bought are NOT surely covered (GTT 700; {engine.FIRED_NOTE})" in r.unresolved[0]
    assert "covered by GTT" not in r.unresolved[0]


def test_a_fill_during_shutdown_whose_gtt_had_fired_is_not_reported_as_exits_armed(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    gw = fast_gateway(StubKite([OPEN]))

    async def settled():
        return engine.Fill("SWIGGY", 2941, 290.1, "260928000000001", "700", exits_fired=True)

    async def scenario():
        task = asyncio.ensure_future(settled())
        await task
        gw._report_late("SWIGGY", task)

    asyncio.run(scenario())
    assert gw.alerts == [f"[SWIGGY] Entry 260928000000001 filled 2941 while shutting down; exits: GTT 700 "
                         f"({engine.FIRED_NOTE})."]
    ticker = engine.LiveTickAdapter({}, engine.AlphaEngine(), asyncio.Queue(), loop=None)
    engine._halt_report(router(gateway=gw), gw, ticker, exit_code=0, stop_signal=None)
    assert f"filled during shutdown | exits: 700 ({engine.FIRED_NOTE})" in caplog.text     # the halt report's line too


@pytest.mark.parametrize("status", ["triggered", "active"])
def test_a_duplicate_that_had_fired_marks_the_fill(status):
    # The duplicate watch's TRIGGERED duplicate (whose alert says DELETE the armed GTT) marks the fill; an active one
    # does not.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_created_before_error=True, gtt_lands_on_next_place=True, gtt_created_status=status)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, getattr(fill, "exits_fired", False)) == ("999", status == "triggered")


# ---------------------------------------------------------------- round 12: refused tunnels carry nothing to Kite
def proxy_outage(kite, error, until_places):
    """Every GTT book read answers ``error`` until the ``until_places``-th GTT request (the proxy is back)."""
    original = kite._request

    def request(route, method, *args, **kwargs):
        if route == "gtt" and kite.gtt_places < until_places:
            raise error
        return original(route, method, *args, **kwargs)

    kite._request = request


def test_a_proxy_outage_that_also_hides_the_gtt_book_is_ridden_out_with_every_attempt():
    # The GTT book goes through the same proxy, so it is refused too. v1.11 stopped after the first refused tunnel
    # ("no GTT can exist") with two attempts left, and left the filled position without exits.
    tunnel = refused_tunnel()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[tunnel, tunnel, {"trigger_id": 777}])
    proxy_outage(kite, tunnel, until_places=3)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == ("777", False) and kite.gtt_places == 3 and gw.alerts == []


def test_a_refused_tunnel_then_an_expired_session_names_the_session():
    # v1.11 gave up after the refused tunnel and blamed the proxy; the next attempt is the one that says why.
    tunnel = refused_tunnel()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[tunnel, TOKEN], gtt_book=tunnel)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == (None, False) and kite.gtt_places == 2
    assert "POSITION OPEN WITHOUT EXITS" in gw.alerts[-1] and "TokenException" in gw.alerts[-1]


def test_a_gtt_armed_after_refused_tunnels_alone_starts_no_duplicate_watch():
    # Only a request that may have reached Kite can have booked a second GTT. v1.11 watched anyway, and a book that
    # failed at the end of the watch raised "could not rule out a duplicate" (and exit 1) for a clean run.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), {"trigger_id": 777}],
                    gtt_book_fail=lambda since, places: places >= 2)       # the book fails once 777 is armed
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    routes = kite.routes()
    assert fill.exit_order_id == "777" and gw.alerts == []
    assert "gtt" not in routes[len(routes) - routes[::-1].index("gtt.place"):]   # no read after the last request


def test_a_gtt_armed_after_a_504_is_still_watched_for_a_duplicate():
    # Control: a 504 may have reached Kite, so the watch runs, and a book that fails at its end is still reported.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 777}],
                    gtt_book_fail=lambda since, places: places >= 2)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "777" and len(gw.alerts) == 1 and "to rule out a duplicate" in gw.alerts[0]


# ---------------------------------------------------------------- round 13: only a GTT that may exist gates a retry
def test_a_proxy_restart_inside_a_tunnel_outage_still_uses_every_attempt():
    # Refused tunnels, then a moment of refused connections (the proxy restarting), then refused tunnels again, with the
    # book behind the same proxy. v1.12 took the refused tunnel for a request that may have booked a GTT, so after the
    # never-sent error it only read the (refused) book, and gave up with attempts unused: POSITION OPEN WITHOUT EXITS.
    tunnel = refused_tunnel()
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[tunnel, unreachable_proxy(), tunnel, {"trigger_id": 900}])
    proxy_outage(kite, tunnel, until_places=4)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == ("900", False) and kite.gtt_places == 4 and gw.alerts == []


def test_refused_tunnels_name_the_expired_session_the_gtt_book_answered():
    # Every GTT request is refused at the tunnel while the book answers 403 (an expired session, which a restart does
    # not fix either). v1.12's alert named only the proxy.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}], gtt_results=[refused_tunnel()])
    expiring_book(kite, 1, TOKEN)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == (None, False) and kite.gtt_places == 3
    assert "POSITION OPEN WITHOUT EXITS" in gw.alerts[-1] and "the GTT book answered TokenException" in gw.alerts[-1]


def test_a_refused_tunnel_then_a_dead_proxy_still_names_the_expired_session():
    # Control: v1.12 named the session here through the book recheck that v1.13 no longer runs after a refused tunnel.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), unreachable_proxy()])
    expiring_book(kite, 1, TOKEN)
    gw = fast_gateway(kite, cancel_grace=0.1, poll_interval=0.01)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == (None, False)
    assert "POSITION OPEN WITHOUT EXITS" in gw.alerts[-1] and "TokenException" in gw.alerts[-1]


def test_a_stop_handled_after_the_entry_was_scheduled_but_before_it_ran_sends_nothing(monkeypatch):
    # The stop can be handled between execute()'s check and the entry task's first step (a loop callback queued behind
    # the LTP reply). v1.12 still sent place_order, although "once shutdown has begun, no new entry is sent".
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}])
    gw = fast_gateway(kite)
    real_uuid4 = engine.uuid.uuid4

    def uuid4():                                        # called just before the entry task is scheduled
        asyncio.get_running_loop().call_soon(setattr, gw, "stopping", True)
        return real_uuid4()

    monkeypatch.setattr(engine.uuid, "uuid4", uuid4)
    assert asyncio.run(gw.execute(kite_plan())) is None
    assert "order.place" not in kite.routes()


# ---------------------------------------------------------------- round 14: refused tunnels adopt nothing
EARLIER_GTT = {"id": 650, "status": "active", "condition": {"tradingsymbol": "SWIGGY", "trigger_values": [286.2, 300.7]},
               "orders": [{"quantity": 2941}, {"quantity": 2941}]}


def test_after_refused_tunnels_alone_an_identical_earlier_gtt_is_neither_adopted_nor_reported():
    # A refused tunnel carried nothing to Kite, so no GTT of this entry can exist. With the snapshot unreadable, v1.13
    # took an earlier run's identical GTT for a possible one of its own: GTT STATE UNKNOWN with two attempts unused,
    # and the new shares were left without exits.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), {"trigger_id": 999}], gtt_book=[EARLIER_GTT], gtt_book_failures=3)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == ("999", False) and gw.alerts == []
    assert kite.gtt_places == 2


def test_after_refused_tunnels_alone_a_foreign_gtt_booked_since_the_snapshot_is_not_adopted():
    # Someone else's identical GTT booked after the snapshot: v1.13 adopted it as this entry's exit.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), {"trigger_id": 999}])
    original = kite._request

    def request(route, method, *args, **kwargs):
        if route == "gtt" and kite.gtt_places and EARLIER_GTT not in kite.created_gtts:
            kite.created_gtts.append(EARLIER_GTT)
        return original(route, method, *args, **kwargs)

    kite._request = request
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == ("999", False) and gw.alerts == []


def test_a_foreign_match_does_not_cut_short_the_poll_after_a_refused_tunnel():
    # The poll after a refused tunnel spaces the attempts to ride out a proxy outage; an identical earlier GTT in the
    # book must not end it at the first read, or three refused tunnels would burn every attempt at once.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), {"trigger_id": 999}], gtt_book=[EARLIER_GTT], gtt_book_failures=3)
    gw = fast_gateway(kite, cancel_grace=0.2, poll_interval=0.01)
    places = []
    original = kite._request

    def request(route, method, *args, **kwargs):
        if route == "gtt.place":
            places.append(engine.time.monotonic())
        return original(route, method, *args, **kwargs)

    kite._request = request
    fill = asyncio.run(gw.execute(kite_plan()))
    assert fill.exit_order_id == "999" and places[1] - places[0] >= 0.2


def test_a_429_on_place_order_is_looked_up_by_its_tag_and_keeps_the_symbol_blocked():
    # A rate limit is not a refusal: the entry is looked up like a lost reply (README: "any 4xx other than 429").
    kite = StubKite([OPEN], place_error=KE.NetworkException("Too many requests", code=429))
    r = run_router(router(gateway=fast_gateway(kite)), [sig(), sig()])
    assert r.active_inventory == {"SWIGGY"} and "orders" in kite.routes()
    assert kite.routes().count("order.place") == 1 and "never appeared in the order book" in r.unresolved[0]


# ---------------------------------------------------------------- round 15: a book read after refused tunnels alone
def test_an_identical_gtt_read_after_a_refused_tunnel_is_not_taken_for_a_later_504s():
    # The snapshot is unreadable and an earlier run's identical GTT is in the book. The poll after the refused tunnel
    # reads the book and shows it before any request that could book a GTT is sent. v1.14 forgot those reads, so the
    # 504's poll took the same GTT for a possible one of its own: GTT STATE UNKNOWN with an attempt unused, no exits.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 999}],
                    gtt_book=[EARLIER_GTT], gtt_book_failures=3)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == ("999", False) and gw.alerts == [] and kite.gtt_places == 3


def test_a_foreign_gtt_read_after_a_refused_tunnel_is_not_adopted_after_a_later_504():
    # Someone else's identical GTT, booked after the snapshot but before this entry's first GTT request. The poll after
    # the refused tunnel shows it; v1.14 still adopted it silently after the 504 as this position's exit.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 999}])
    original = kite._request

    def request(route, method, *args, **kwargs):
        if route == "gtt.place" and EARLIER_GTT not in kite.created_gtts:
            kite.created_gtts.append(EARLIER_GTT)          # booked just before this entry's first GTT request
        return original(route, method, *args, **kwargs)

    kite._request = request
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == ("999", False) and gw.alerts == [] and kite.gtt_places == 3


@pytest.mark.parametrize("status", ["active", "triggered"])
def test_a_gtt_booked_by_a_read_timeout_after_a_refused_tunnel_is_adopted(status):
    # Control: only reads taken before any request that may have reached Kite are set aside, so this entry's own GTT
    # is adopted (a fired one marked so). With the earlier GTT read after the tunnel, v1.14 matched that one first and
    # raised GTT STATE UNKNOWN although the entry's own GTT was in the book.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), requests.exceptions.ReadTimeout("read timed out"), {"trigger_id": 999}],
                    gtt_booked=[False, True, False], gtt_created_status=status, gtt_book=[EARLIER_GTT],
                    gtt_book_failures=3)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown, fill.exits_fired) == ("700", False, status == "triggered")
    assert kite.gtt_places == 2 and [g["id"] for g in kite.created_gtts] == [700]
    assert gw.alerts == [] if status == "active" else len(gw.alerts) == 1 and "already TRIGGERED" in gw.alerts[0]


def test_an_identical_gtt_is_still_reported_when_no_read_after_the_refused_tunnel_succeeded():
    # Control: the book stays behind the proxy until the 504, so nothing proves the match predates it: still UNKNOWN.
    kite = StubKite([{"status": "COMPLETE", "filled_quantity": 2941, "average_price": 290.1}],
                    gtt_results=[refused_tunnel(), KE.NetworkException("Gateway timed out", code=504), {"trigger_id": 999}],
                    gtt_book=[EARLIER_GTT], gtt_book_failures=3, gtt_book_fail=lambda since, places: places <= 1)
    gw = fast_gateway(kite)
    fill = asyncio.run(gw.execute(kite_plan()))
    assert (fill.exit_order_id, fill.exits_unknown) == (None, True) and kite.gtt_places == 2
    assert "GTT 650 matches this position" in gw.alerts[0]
