"""Data adapters and infrastructure: timezones, logging hygiene, rate limiting, Yahoo/Kite/CSV sources."""
import asyncio
import importlib.util
import inspect
import io
import json
import subprocess
import sys
import time
import urllib.error
import zoneinfo
from itertools import pairwise
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import engine
from conftest import FIXTURE, ist


# ---------------------------------------------------------------- module-level hygiene
def test_ist_falls_back_to_fixed_offset_without_a_tz_database(monkeypatch):
    def missing(key):
        raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
    spec = importlib.util.spec_from_file_location("engine_without_tzdata", engine.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)                                       # v1.0 crashed here
    assert module.IST.utcoffset(None) == timedelta(hours=5, minutes=30)


def test_importing_the_engine_does_not_configure_logging():
    code = f"import sys, logging; sys.path.insert(0, {str(engine.__file__.rsplit('/', 1)[0])!r}); " \
           "import engine; print(len(logging.getLogger().handlers))"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "0"


def test_rate_limiter_admits_at_most_max_calls_per_window():
    async def scenario():
        limiter = engine.TokenBucketRateLimiter(max_calls=3, period=0.2)
        stamps = []

        async def call():
            await limiter.wait_for_capacity()
            stamps.append(time.monotonic())

        await asyncio.gather(*(call() for _ in range(10)))
        return sorted(stamps)

    stamps = asyncio.run(scenario())
    assert len(stamps) == 10
    assert all(stamps[i + 3] - stamps[i] >= 0.2 - 0.005 for i in range(len(stamps) - 3))


def test_harmonize_never_backfills_from_the_future():
    idx = pd.DatetimeIndex([ist(2026, 9, 28, 9, 15), ist(2026, 9, 28, 9, 20), ist(2026, 9, 28, 9, 25)])
    raw = pd.DataFrame({"Open": [None, 101, None], "High": [None, 102, 103], "Low": [None, 100, None],
                        "Close": [None, 101.5, 102.5], "Volume": [None, 500, None]}, index=idx)
    df = engine.harmonize_bars(raw)
    assert df.index.tolist() == [pd.Timestamp(ist(2026, 9, 28, 9, 20)), pd.Timestamp(ist(2026, 9, 28, 9, 25))]
    assert df.iloc[-1].tolist() == [102.5, 103.0, 102.5, 102.5, 0.0]


# ---------------------------------------------------------------- Yahoo adapter (offline, faked HTTP)
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def yahoo_payload(epochs, closes):
    n = len(epochs)
    return {"chart": {"error": None, "result": [{"timestamp": epochs, "indicators": {"quote": [{
        "open": list(closes), "high": [c and c + 1 for c in closes], "low": [c and c - 1 for c in closes],
        "close": list(closes), "volume": [1000 if c else None for c in closes][:n]}]}}]}}


def test_yahoo_keeps_only_complete_traded_session_bars(monkeypatch, fast_sleep):
    as_of = ist(2026, 9, 25, 11, 2)                                       # Friday, mid-session
    rows = [(ist(2026, 9, 25, 9, 8), 99.0),                              # pre-open print: not a bar
            (ist(2026, 9, 25, 10, 45), 100.0),
            (ist(2026, 9, 25, 10, 50), None),                            # no trades: dropped, not invented
            (ist(2026, 9, 25, 10, 55), 101.0),
            (ist(2026, 9, 25, 10, 57, 31), 101.2),                       # off-grid "live" row
            (ist(2026, 9, 25, 11, 0), 102.0)]                            # still forming at 11:02
    epochs = [int(t.timestamp()) for t, _ in rows]
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return FakeResponse(json.dumps(yahoo_payload(epochs, [c for _, c in rows])).encode())

    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    df = asyncio.run(engine.PublicExchangeAdapter().fetch_historical_bars("M&M", as_of - timedelta(days=1), as_of))
    assert "/M%26M.NS?" in seen["url"]
    assert df.index.tolist() == [pd.Timestamp(ist(2026, 9, 25, 10, 45)), pd.Timestamp(ist(2026, 9, 25, 10, 55))]
    assert str(df.index.tz) == str(engine.IST) and df["Close"].tolist() == [100.0, 101.0]


@pytest.mark.parametrize("code,reason,attempts", [(403, "Forbidden", 1), (404, "Not Found", 1),
                                                  (429, "Too Many Requests", 3), (503, "Service Unavailable", 3)])
def test_yahoo_failures_are_logged_and_only_transient_ones_retried(monkeypatch, fast_sleep, caplog, code, reason, attempts):
    def failing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, reason, {}, None)

    monkeypatch.setattr(engine.urllib.request, "urlopen", failing)
    now = datetime.now(timezone.utc)
    df = asyncio.run(engine.PublicExchangeAdapter().fetch_historical_bars("SWIGGY", now - timedelta(days=5), now))
    assert df.empty and list(df.columns) == engine.OHLCV
    assert caplog.text.count(f"<HTTPError {code}: '{reason}'>") == attempts   # every attempt visible (v1.0: silent)
    assert "--source csv" in caplog.text


# ---------------------------------------------------------------- Kite adapter (fake client, real SDK signatures)
class FakeKite:
    def __init__(self, fail_after_first_chunk=False):
        self.calls = []
        self.fail_after_first_chunk = fail_after_first_chunk

    def instruments(self, exchange=None):
        return [{"tradingsymbol": "SWIGGY", "instrument_token": 1234, "tick_size": 0.05},
                {"tradingsymbol": "PENNY", "instrument_token": 99, "tick_size": 0.01}]

    def historical_data(self, instrument_token, from_date, to_date, interval, continuous=False, oi=False):
        self.calls.append(dict(instrument_token=instrument_token, from_date=from_date, to_date=to_date, interval=interval))
        if self.fail_after_first_chunk and from_date != self.calls[0]["from_date"]:
            raise ConnectionError("gateway timeout")
        start = datetime.strptime(from_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return [{"date": start + timedelta(minutes=5 * i), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}
                for i in range(3)]


def test_kite_history_is_chunked_contiguously_with_sdk_compatible_arguments():
    kiteconnect = pytest.importorskip("kiteconnect")
    fake = FakeKite()
    ad = engine.ZerodhaKiteAdapter("k", "t", kite=fake)
    asyncio.run(ad.boot())
    end = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    df = asyncio.run(ad.fetch_historical_bars("SWIGGY", end - timedelta(days=200), end))
    sig = inspect.signature(kiteconnect.KiteConnect.historical_data)
    for call in fake.calls:
        sig.bind(None, **call)                                            # raises if an argument name is wrong
        assert call["interval"] == "5minute"
    assert len(fake.calls) == 3
    spans = [(datetime.fromisoformat(c["from_date"]), datetime.fromisoformat(c["to_date"])) for c in fake.calls]
    assert all(b[0] - a[1] == timedelta(seconds=1) for a, b in pairwise(spans))
    assert all((t - f) <= timedelta(days=90) for f, t in spans)
    assert len(df) == 9 and str(df.index.tz) == str(engine.IST)
    assert ad.symbol_for_token(1234) == "SWIGGY" and ad.token_map() == {1234: "SWIGGY", 99: "PENNY"}
    assert ad.tick_size("PENNY") == 0.01 and ad.tick_size("UNKNOWN") == 0.05


def test_kite_partial_history_failure_fails_closed_and_says_so(fast_sleep, caplog):
    fake = FakeKite(fail_after_first_chunk=True)                          # chunk 2 fails on every retry
    ad = engine.ZerodhaKiteAdapter("k", "t", kite=fake)
    asyncio.run(ad.boot())
    end = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    df = asyncio.run(ad.fetch_historical_bars("SWIGGY", end - timedelta(days=200), end))
    assert df.empty
    assert "attempt 3/3 failed" in caplog.text and "discarding 3 partial bars" in caplog.text


def test_kite_unknown_symbol_is_reported(caplog):
    ad = engine.ZerodhaKiteAdapter("k", "t", kite=FakeKite())
    asyncio.run(ad.boot())
    now = datetime.now(timezone.utc)
    assert asyncio.run(ad.fetch_historical_bars("NOPE", now - timedelta(days=1), now)).empty
    assert "Unknown NSE tradingsymbol" in caplog.text


# ---------------------------------------------------------------- CSV replay + orchestrator
def test_csv_replay_serves_the_real_fixture(real_bars):
    assert len(real_bars) == 944
    assert real_bars.index[0] == pd.Timestamp(ist(2026, 9, 8, 9, 15))
    assert real_bars.index[-1] == pd.Timestamp(ist(2026, 9, 25, 15, 15))
    assert real_bars.index.is_monotonic_increasing and (real_bars.dtypes == "float64").all()
    ad = engine.CsvReplayAdapter({"SWIGGY": FIXTURE})
    window = asyncio.run(ad.fetch_historical_bars("SWIGGY", ist(2026, 9, 15, 0, 0), ist(2026, 9, 16, 0, 0)))
    assert len(window) == 73 and window.index.normalize().unique().tolist() == [pd.Timestamp(ist(2026, 9, 15))]


def test_csv_missing_file_is_an_error_not_a_crash(tmp_path, caplog):
    ad = engine.CsvReplayAdapter({"SWIGGY": tmp_path / "nope.csv"})
    assert asyncio.run(ad.fetch_historical_bars("SWIGGY", ist(2026, 9, 1), ist(2026, 9, 30))).empty
    assert "No CSV file" in caplog.text


def test_orchestrator_excludes_symbols_whose_history_misses_the_listing(caplog):
    # SWIGGY actually listed on 2024-11-13; the fixture starts 664 days later, so no IPO base exists.
    ad = engine.CsvReplayAdapter({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": ist(2024, 11, 13)}, ad, as_of=ist(2026, 9, 26))
    assert asyncio.run(orch.build_the_ground()) is False and orch.market_state == {}
    assert "664 days after the 2024-11-13 listing" in caplog.text and "symbol excluded" in caplog.text

    orch = engine.ProductionOrchestrator({"SWIGGY": ist(2024, 11, 13)}, ad, as_of=ist(2026, 9, 26),
                                         allow_partial_history=True)
    assert asyncio.run(orch.build_the_ground()) and len(orch.market_state["SWIGGY"]) == 944


@pytest.mark.parametrize("listing,accepted", [(ist(2026, 9, 8), True),                   # the first bar's session
                                              (ist(2026, 9, 7), False),                  # one session missing
                                              (ist(2026, 9, 4), False)])                 # Friday: 1 session + weekend
def test_orchestrator_needs_the_listing_session_itself(listing, accepted, caplog):
    # v1.1 allowed 5 days of slack, which let Yahoo's 59-day clamp drop listing sessions unnoticed.
    ad = engine.CsvReplayAdapter({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": listing}, ad, as_of=ist(2026, 9, 26))
    assert asyncio.run(orch.build_the_ground()) is accepted
    assert ("symbol excluded" in caplog.text) is not accepted


def test_orchestrator_without_a_listing_date_anchors_at_the_first_bar_and_says_so(caplog):
    ad = engine.CsvReplayAdapter({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": None}, ad, as_of=ist(2026, 9, 25, 15, 20))
    assert asyncio.run(orch.build_the_ground())
    assert "No listing date given: treating the first bar (2026-09-08 09:15) as the listing" in caplog.text


def test_orchestrator_reports_failure_when_nothing_loads(tmp_path):
    ad = engine.CsvReplayAdapter({"SWIGGY": tmp_path / "missing.csv"})
    orch = engine.ProductionOrchestrator({"SWIGGY": ist(2026, 9, 1)}, ad, as_of=ist(2026, 9, 26))
    assert asyncio.run(orch.build_the_ground()) is False and orch.market_state == {}


def fake_ip_echo(monkeypatch, answers):
    """Serve each provider URL its scripted body (bytes) or raise (an exception instance)."""
    asked = []

    def urlopen(req, timeout):
        asked.append(req.full_url)
        answer = answers[req.full_url]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    monkeypatch.setattr(engine.urllib.request, "urlopen", urlopen)
    return asked


V4 = engine.IP_ECHO_PROVIDERS[4]


def test_hardware_ip_mismatch_is_fatal(monkeypatch):
    fake_ip_echo(monkeypatch, {p: b"203.0.113.9\n" for p in V4})
    assert asyncio.run(engine.verify_hardware_ip("198.51.100.1")) is False
    assert asyncio.run(engine.verify_hardware_ip("203.0.113.9")) is True


def test_ip_check_survives_one_dead_provider_and_asks_only_single_family_hosts(monkeypatch):
    asked = fake_ip_echo(monkeypatch, {V4[0]: TimeoutError("timed out"), V4[1]: b"203.0.113.9", V4[2]: b"203.0.113.9\n"})
    assert asyncio.run(engine.verify_hardware_ip("203.0.113.9")) is True   # v1.0: FATAL via a dual-stack fallback
    assert sorted(asked) == sorted(V4) and "ifconfig.me" not in " ".join(asked)


def test_ip_check_ignores_junk_and_wrong_family_answers_but_needs_two_confirmations(monkeypatch, caplog):
    fake_ip_echo(monkeypatch, {V4[0]: b"<html>captive portal</html>", V4[1]: b"2406:da1a:6c3:c700::10", V4[2]: b"203.0.113.9"})
    assert asyncio.run(engine.verify_hardware_ip("203.0.113.9")) is False
    assert "answered with IPv6" in caplog.text and "only 1 of 3 IP verifiers answered" in caplog.text


def test_any_disagreeing_provider_fails_the_ip_check(monkeypatch, caplog):
    fake_ip_echo(monkeypatch, {V4[0]: b"203.0.113.9", V4[1]: b"203.0.113.9", V4[2]: b"198.51.100.77"})
    assert asyncio.run(engine.verify_hardware_ip("203.0.113.9")) is False
    assert "observed: 198.51.100.77 via https://ipv4.icanhazip.com" in caplog.text


def test_ip_check_rejects_a_malformed_expected_address():
    assert asyncio.run(engine.verify_hardware_ip("not-an-ip")) is False


def test_limiter_serves_waiters_in_arrival_order():
    async def scenario():
        limiter = engine.TokenBucketRateLimiter(max_calls=2, period=0.05)
        order = []

        async def call(i):
            await limiter.wait_for_capacity()
            order.append(i)

        tasks = []
        for i in range(8):
            tasks.append(asyncio.create_task(call(i)))
            await asyncio.sleep(0)                                        # arrive strictly in sequence
        await asyncio.gather(*tasks)
        return order

    assert asyncio.run(scenario()) == list(range(8))


def test_kite_permanent_errors_are_not_retried(fast_sleep, caplog):
    kiteconnect = pytest.importorskip("kiteconnect")

    class Expired(FakeKite):
        def historical_data(self, **kw):
            self.calls.append(kw)
            raise kiteconnect.exceptions.TokenException("Incorrect `api_key` or `access_token`.")

    fake = Expired()
    ad = engine.ZerodhaKiteAdapter("k", "t", kite=fake)
    asyncio.run(ad.boot())
    end = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert asyncio.run(ad.fetch_historical_bars("SWIGGY", end - timedelta(days=10), end)).empty
    assert len(fake.calls) == 1 and "TokenException" in caplog.text


def test_kite_interval_table_bounds_each_request():
    fake = FakeKite()
    ad = engine.ZerodhaKiteAdapter("k", "t", kite=fake)
    asyncio.run(ad.boot())
    end = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    asyncio.run(ad.fetch_historical_bars("SWIGGY", end - timedelta(days=120), end, interval="1m"))
    assert {c["interval"] for c in fake.calls} == {"minute"} and len(fake.calls) == 3   # 55-day chunks
    with pytest.raises(ValueError):
        asyncio.run(ad.fetch_historical_bars("SWIGGY", end - timedelta(days=1), end, interval="7m"))


def test_orchestrator_accepts_naive_listing_dates():
    ad = engine.CsvReplayAdapter({"SWIGGY": FIXTURE})
    naive = datetime(2026, 9, 8)  # noqa: DTZ001 - the naive input is the point of this test
    orch = engine.ProductionOrchestrator({"SWIGGY": naive}, ad, as_of=ist(2026, 9, 26))
    assert asyncio.run(orch.build_the_ground())                            # v1.0: TypeError naive vs aware


# ---------------------------------------------------------------- malformed inputs are errors, not crashes
def test_csv_timestamps_with_utc_offsets_load(tmp_path):
    src = pd.read_csv(FIXTURE).head(5)
    utc = pd.to_datetime(src["datetime_ist"]).dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC")
    src["datetime_ist"] = utc.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")        # e.g. an export in UTC with an offset
    path = tmp_path / "SWIGGY_utc.csv"
    src.to_csv(path, index=False)
    df = engine.CsvReplayAdapter.load(path)                                 # v1.1: TypeError, a raw traceback
    assert df.index[0] == pd.Timestamp(ist(2026, 9, 8, 9, 15))


def test_non_numeric_csv_values_are_reported(tmp_path, caplog):
    src = pd.read_csv(FIXTURE).head(5)
    src["volume"] = [f"{v:,}" for v in src["volume"]]                      # thousands separators
    path = tmp_path / "SWIGGY_commas.csv"
    src.to_csv(path, index=False)
    engine.CsvReplayAdapter.load(path)
    assert "were not numbers" in caplog.text                                # v1.1: silently every volume 0


def test_a_csv_that_looks_like_another_symbol_is_flagged(caplog):
    ad = engine.CsvReplayAdapter({"ZOMATO": FIXTURE})
    asyncio.run(ad.fetch_historical_bars("ZOMATO", ist(2026, 9, 1), ist(2026, 9, 30)))
    assert "does not look like ZOMATO data" in caplog.text


@pytest.mark.parametrize("payload", [
    [], {"chart": None}, {"chart": {"result": [None]}}, {"chart": {"result": [{"timestamp": [1], "indicators": {"quote": []}}]}},
    {"chart": {"result": [{"timestamp": [1, 2, 3], "indicators": {"quote": [{"close": [1.0, 2.0]}]}}]}},
])
def test_malformed_yahoo_payloads_give_an_empty_frame(monkeypatch, fast_sleep, payload, caplog):
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda req, timeout: FakeResponse(json.dumps(payload).encode()))
    now = datetime.now(timezone.utc)
    df = asyncio.run(engine.PublicExchangeAdapter().fetch_historical_bars("SWIGGY", now - timedelta(days=1), now))
    assert df.empty and "Yahoo" in caplog.text


def test_broker_start_up_failure_aborts_cleanly(caplog):
    class ExpiredToken(engine.BrokerAdapter):
        async def boot(self):
            raise RuntimeError("TokenException: Incorrect `api_key` or `access_token`.")

        async def fetch_historical_bars(self, *a, **k):
            raise AssertionError("not reached")

    orch = engine.ProductionOrchestrator({"SWIGGY": ist(2026, 9, 8)}, ExpiredToken(), as_of=ist(2026, 9, 26))
    assert asyncio.run(orch.build_the_ground()) is False and "Broker start-up failed" in caplog.text


def test_one_symbol_failing_to_fetch_does_not_stop_the_others(caplog):
    class Flaky(engine.CsvReplayAdapter):
        async def fetch_historical_bars(self, symbol, start_date, end_date, interval="5m"):
            if symbol == "BROKEN":
                raise KeyError("indicators")
            return await super().fetch_historical_bars(symbol, start_date, end_date, interval)

    orch = engine.ProductionOrchestrator({"BROKEN": ist(2026, 9, 8), "SWIGGY": ist(2026, 9, 8)},
                                         Flaky({"SWIGGY": FIXTURE}), as_of=ist(2026, 9, 26))
    assert asyncio.run(orch.build_the_ground()) and list(orch.market_state) == ["SWIGGY"]
    assert "[BROKEN] History fetch failed" in caplog.text


def test_thirty_minute_bars_are_anchored_at_the_open(monkeypatch, fast_sleep):
    rows = [ist(2026, 9, 25, 9, 15), ist(2026, 9, 25, 9, 45), ist(2026, 9, 25, 10, 15)]
    payload = yahoo_payload([int(t.timestamp()) for t in rows], [100.0, 101.0, 102.0])
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda req, timeout: FakeResponse(json.dumps(payload).encode()))
    df = asyncio.run(engine.PublicExchangeAdapter().fetch_historical_bars(
        "SWIGGY", ist(2026, 9, 24), ist(2026, 9, 25, 11, 0), interval="30m"))
    assert len(df) == 3                                                     # v1.1 kept none (hour-aligned grid)
    assert engine.bar_floor(ist(2026, 9, 25, 9, 50), 30) == ist(2026, 9, 25, 9, 45)
    assert engine.bar_floor(ist(2026, 9, 25, 9, 50), 5) == ist(2026, 9, 25, 9, 50)
