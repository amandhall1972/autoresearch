"""The whole pipeline through the real CLI entry points."""
import asyncio
import logging
import signal
import subprocess
import sys
import time
import types
import urllib.error
from datetime import datetime

import pytest

import engine
from conftest import FIXTURE


def cli(*args, timeout=60):
    return subprocess.run([sys.executable, engine.__file__, *args], capture_output=True, text=True, timeout=timeout)


def test_offline_run_on_real_bars_trades_the_simulated_breakout():
    proc = cli("--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "4.5")
    log = proc.stderr
    assert proc.returncode == 0, log
    assert "944 concrete 5m bars acquired" in log
    assert "Real Historical Base High to Beat: 285.55" in log
    assert "5m Bar Closed 2026-09-28 09:20 | O: 286.12 H: 290.40 L: 286.12 C: 289.83 | V: 850,000" in log
    assert "ALPHA TRIGGER: Base Breakout @ 289.83" in log
    assert "[OMS DISPATCH] BUY 2884x SWIGGY LIMIT 291.30" in log
    assert "PAPER FILL (simulated, no order sent): 2884x @ 289.83" in log
    assert "Final Inventory State: {'SWIGGY'}" in log
    assert "Traceback" not in log and "Task was destroyed" not in log


def test_unreachable_history_aborts_with_a_nonzero_exit(monkeypatch, fast_sleep, caplog):
    def forbidden(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(engine.urllib.request, "urlopen", forbidden)
    assert asyncio.run(engine.main(["--source", "yahoo", "--run-seconds", "0.1"])) == 1
    assert "Failed to build historical context. Aborting." in caplog.text
    assert "403" in caplog.text


def test_live_flags_are_refused_without_the_kite_source():
    proc = cli("--source", "csv", "--live-orders")
    assert proc.returncode == 2 and "require --source kite" in proc.stderr


def test_real_orders_require_a_live_feed_so_the_synthetic_tape_can_never_trade():
    proc = cli("--source", "kite", "--listing-date", "2026-09-08", "--live-orders", "--expect-ip", "203.0.113.9")
    assert proc.returncode == 2 and "--live-orders requires --live-feed" in proc.stderr


def test_real_orders_require_a_verified_static_ip():
    proc = cli("--source", "kite", "--listing-date", "2026-09-08", "--live-feed", "--live-orders")
    assert proc.returncode == 2 and "--live-orders requires --expect-ip" in proc.stderr   # before any network call


def test_kite_requires_the_real_listing_date():
    proc = cli("--source", "kite")
    assert proc.returncode == 2 and "--source kite requires --listing-date" in proc.stderr


@pytest.mark.parametrize("flag,value", [("--expect-ip", "203.0.113.300"), ("--run-seconds", "-1")])
def test_malformed_arguments_exit_2(flag, value):
    proc = cli("--source", "csv", flag, value)
    assert proc.returncode == 2 and "error" in proc.stderr


def test_kite_source_requires_credentials(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    assert asyncio.run(engine.main(["--source", "kite"])) == 2


def test_unreadable_csv_exits_with_an_error(tmp_path):
    proc = cli("--source", "csv", "--csv", str(tmp_path / "missing.csv"))
    assert proc.returncode == 1 and "Cannot read bars" in proc.stderr


def test_no_simulation_means_no_trades(caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    assert asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "0.2"])) == 0
    assert "Final Inventory State: {}" in caplog.text


def test_a_crashed_worker_stops_the_engine_with_a_failure_code(monkeypatch, caplog):
    async def broken_clock(self, *args, **kwargs):
        raise RuntimeError("clock hardware fault")

    monkeypatch.setattr(engine.LiveTickAdapter, "bar_clock", broken_clock)
    code = asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "30"]))
    assert code == 1
    assert "Worker 'bar-clock' stopped unexpectedly: RuntimeError('clock hardware fault')" in caplog.text


# ---------------------------------------------------------------- time, signals and live-mode safety
class FarFuture(datetime):
    """Wall clock pinned to a date long after the bundled data (the v1.1 demo broke after 2026-09-28 09:26)."""
    FIXED = datetime(2027, 3, 15, 11, 0, tzinfo=engine.IST)

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED.astimezone(tz) if tz else cls.FIXED.replace(tzinfo=None)


def test_the_offline_demo_does_not_depend_on_the_wall_clock(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    monkeypatch.setattr(engine, "datetime", FarFuture)
    assert asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "4.5"])) == 0
    assert "[OMS DISPATCH] BUY 2884x SWIGGY LIMIT 291.30" in caplog.text
    assert "Final Inventory State: {'SWIGGY'}" in caplog.text and "stale" not in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_ctrl_c_runs_the_orderly_shutdown_and_prints_the_halt_report():
    proc = subprocess.Popen([sys.executable, engine.__file__, "--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "0"],
                            stderr=subprocess.PIPE, text=True)
    time.sleep(5)                                                         # the paper fill lands at ~3.3 s
    proc.send_signal(signal.SIGINT)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, err
    assert "PAPER FILL" in err and "=== SYSTEM HALT ===" in err and "Open SWIGGY: 2884 @ 289.83" in err


class FixtureAsKite(engine.CsvReplayAdapter):
    """Stands in for ZerodhaKiteAdapter: serves the bundled bars whatever the requested window."""

    def __init__(self, *args):
        super().__init__({"SWIGGY": FIXTURE})

    def token_map(self):
        return {1234: "SWIGGY"}

    async def fetch_historical_bars(self, symbol, start_date, end_date, interval="5m"):
        return self.load(FIXTURE)


def kite_env(monkeypatch, feed):
    monkeypatch.setenv("KITE_API_KEY", "key")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "token")
    monkeypatch.setattr(engine, "ZerodhaKiteAdapter", FixtureAsKite)
    monkeypatch.setattr(engine, "start_kite_feed", feed)


def test_a_dead_websocket_stops_the_engine_and_a_live_feed_never_runs_the_synthetic_tape(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")

    def feed_that_dies(api_key, access_token, tokens, tick_adapter, feed_dead):
        assert tokens == [1234]
        tick_adapter.loop.call_later(0.3, feed_dead.set)                  # KiteTicker gave up reconnecting
        return types.SimpleNamespace(close=lambda: None)

    kite_env(monkeypatch, feed_that_dies)
    started = time.monotonic()
    code = asyncio.run(engine.main(["--source", "kite", "--listing-date", "2026-09-08", "--live-feed", "--run-seconds", "30"]))
    assert code == 1 and time.monotonic() - started < 10                  # v1.1 ran blind and exited 0
    assert "Worker 'kite-feed' stopped unexpectedly" in caplog.text
    assert "synthetic breakout tape is disabled" in caplog.text and "Initiating simulated stream" not in caplog.text


def test_shutdown_waits_for_the_order_in_flight(monkeypatch, caplog):
    """v1.1 cancelled the router 10 s into shutdown, stranding a live entry that can take 30 s to settle."""
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")

    class SlowFill(engine.PaperGateway):
        def __init__(self):
            super().__init__(latency=3.0)                                 # dispatched at ~3.0 s, fills at ~6.0 s

    monkeypatch.setattr(engine, "PaperGateway", SlowFill)
    assert asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "3.5"])) == 0
    assert "PAPER FILL" in caplog.text and "Fill SWIGGY: 2884 @ 289.83" in caplog.text
