"""The whole pipeline through the real CLI entry points."""
import asyncio
import logging
import os
import queue
import signal
import subprocess
import sys
import textwrap
import threading
import time
import types
import urllib.error
from datetime import datetime
from pathlib import Path

import pandas as pd
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


@pytest.mark.parametrize("flag,value", [("--run-seconds", "nan"), ("--run-seconds", "inf"),
                                        ("--risk-per-trade", "-15000"), ("--max-position-value", "0"),
                                        ("--rvol-threshold", "nan"), ("--risk-reward", "-3")])
def test_numeric_arguments_must_be_finite_and_in_range(flag, value, capsys):
    # v1.2: `--run-seconds nan` ran forever; negative risk settings ran, rejected every signal, and exited 0.
    with pytest.raises(SystemExit) as stop:
        engine.build_arg_parser().parse_args([flag, value])
    assert stop.value.code == 2 and "error" in capsys.readouterr().err


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


class KiteDayClock(FarFuture):
    """Wall clock pinned within Kite's 180-day lookback of the 2026-09-08 listing (a Saturday)."""
    FIXED = datetime(2026, 9, 26, 12, 0, tzinfo=engine.IST)


def kite_env(monkeypatch, feed):
    monkeypatch.setattr(engine, "datetime", KiteDayClock)                # no time bomb once 2026-09-08 is > 180 days old
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
    """v1.1 cancelled the router 10 s into shutdown, stranding a live entry that can take 30 s to settle.

    The fill lands ~10.5 s into the shutdown, after v1.1's fixed 10 s drain.
    """
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")

    class SlowFill(engine.PaperGateway):
        def __init__(self):
            super().__init__(latency=11.0)                                # dispatched at ~3 s, fills at ~14 s

    monkeypatch.setattr(engine, "PaperGateway", SlowFill)
    assert asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "3.5"])) == 0
    assert "PAPER FILL" in caplog.text and "Fill SWIGGY: 2884 @ 289.83" in caplog.text


# ---------------------------------------------------------------- signals, in a real interpreter
class Child:
    """engine.run(args) in a fresh interpreter after ``prelude``; stderr is read line by line."""

    def __init__(self, prelude, *args):
        code = "\n".join(["import sys", f"sys.path.insert(0, {str(Path(engine.__file__).parent)!r})", "import engine",
                          textwrap.dedent(prelude), f"sys.exit(engine.run({list(args)!r}))"])
        self.proc = subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.PIPE, text=True)
        self.lines, self.log = queue.Queue(), []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stderr:
            self.lines.put(line)
        self.lines.put(None)

    def wait_for(self, needle, timeout=30.0):
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                break
            if line is None:
                break
            self.log.append(line)
            if needle in line:
                return
        self.proc.kill()
        raise AssertionError(f"{needle!r} was never logged:\n{''.join(self.log)}")

    def finish(self, timeout=60.0):
        code = self.proc.wait(timeout=timeout)
        while (line := self.lines.get(timeout=10)) is not None:
            self.log.append(line)
        return code, "".join(self.log)


SLOW_PAPER = """
class SlowPaper(engine.PaperGateway):
    def __init__(self):
        super().__init__(latency={latency})
engine.PaperGateway = SlowPaper
"""
DEMO = ["--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "0"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_kill_and_hangup_settle_the_entry_in_flight_like_ctrl_c(signame):
    # v1.2 handled only SIGINT: `kill`, `docker stop` or a closed terminal ended it mid-entry, unreported.
    child = Child(SLOW_PAPER.format(latency=3.0), *DEMO)
    child.wait_for("[OMS DISPATCH]")
    child.proc.send_signal(getattr(signal, signame))
    code, err = child.finish()
    assert code == 128 + getattr(signal, signame), err
    assert "shutting down in order" in err and "PAPER FILL" in err
    assert "=== SYSTEM HALT ===" in err and "Open SWIGGY: 2884 @ 289.83" in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_without_loop_signal_handlers_ctrl_c_still_settles_and_a_second_one_is_ignored():
    # As on Windows: the loop cannot own signals. v1.2 relied on asyncio.run's handling, which on 3.10
    # cancelled the entry in flight and on 3.11 lost the halt report to a second Ctrl-C.
    prelude = SLOW_PAPER.format(latency=3.0) + """
import asyncio
def refuse(self, *args, **kwargs):
    raise NotImplementedError
asyncio.SelectorEventLoop.add_signal_handler = refuse
"""
    child = Child(prelude, *DEMO)
    child.wait_for("[OMS DISPATCH]")
    child.proc.send_signal(signal.SIGINT)
    child.wait_for("shutting down in order")
    child.proc.send_signal(signal.SIGINT)
    code, err = child.finish()
    assert code == 130, err
    assert "shutdown already in progress" in err and "PAPER FILL" in err
    assert "=== SYSTEM HALT ===" in err and "Open SWIGGY: 2884 @ 289.83" in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_a_failed_shutdown_exits_1_even_when_a_signal_started_it():
    # v1.2 returned 130 whenever Ctrl-C was pressed, hiding the failure from a supervisor keyed on exit 1.
    prelude = SLOW_PAPER.format(latency=30.0) + """
engine.ExecutionRouter.settle_timeout = property(lambda self: 0.5)
"""
    child = Child(prelude, *DEMO)
    child.wait_for("[OMS DISPATCH]")
    child.proc.send_signal(signal.SIGINT)
    code, err = child.finish()
    assert code == 1, err
    assert "did not settle within" in err and "=== SYSTEM HALT ===" in err


# ---------------------------------------------------------------- round 4: shutdown timing and signal ownership
def test_shutdown_tells_the_gateway_to_stop_waiting_for_fills(monkeypatch, caplog):
    # v1.3 let a live entry wait out its 30 s fill window after a stop request, longer than
    # `docker stop` allows (SIGKILL after 10 s); the gateway now cancels the remainder at once.
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")

    class RestingOrder(engine.PaperGateway):
        async def execute(self, plan):
            for _ in range(600):                                          # up to 30 s, like the fill window
                if self.stopping:
                    return engine.Fill(plan.signal.symbol, 500, plan.signal.entry_price, "LIVE-1", "777")
                await asyncio.sleep(0.05)
            return None

    monkeypatch.setattr(engine, "PaperGateway", RestingOrder)
    started = time.monotonic()
    code = asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "3.5"]))
    assert code == 0 and time.monotonic() - started < 8
    assert "Fill SWIGGY: 500 @ 289.83 (order LIVE-1, exits: 777)" in caplog.text


def test_shutdown_stops_taking_signals_before_anything_can_yield(monkeypatch):
    # v1.3 cleared `accepting` only after two awaits, so a back-fill landing then could still enter.
    seen = {}

    def feed(api_key, access_token, tokens, tick_adapter, feed_dead):
        oms = tick_adapter.bar_listeners[0].__self__
        tick_adapter.loop.call_later(0.3, feed_dead.set)
        return types.SimpleNamespace(close=lambda: seen.update(accepting=oms.accepting))   # shutdown's first step

    kite_env(monkeypatch, feed)
    assert asyncio.run(engine.main(["--source", "kite", "--listing-date", "2026-09-08", "--live-feed",
                                    "--run-seconds", "30"])) == 1
    assert seen == {"accepting": False}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_handlers_the_engine_replaced_are_restored_afterwards():
    # v1.3's loop.remove_signal_handler left SIG_DFL behind, dropping an embedding host's handlers.
    def host_handler(signum, frame):
        pass

    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        signal.signal(signal.SIGTERM, host_handler)
        signal.signal(signal.SIGHUP, host_handler)
        asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "0.2"]))
        assert signal.getsignal(signal.SIGTERM) is host_handler and signal.getsignal(signal.SIGHUP) is host_handler
    finally:
        for s, previous in saved.items():
            signal.signal(s, previous)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_nohup_is_honoured():
    # nohup starts the engine with SIGHUP ignored so a dropped SSH session cannot stop it; v1.3 overrode that.
    child = Child(SLOW_PAPER.format(latency=3.0) + "import signal\nsignal.signal(signal.SIGHUP, signal.SIG_IGN)\n", *DEMO)
    child.wait_for("[OMS DISPATCH]")
    child.proc.send_signal(signal.SIGHUP)
    child.wait_for("PAPER FILL")                                         # still running ~3 s later
    child.proc.send_signal(signal.SIGTERM)
    code, err = child.finish()
    assert code == 143, err
    assert "Signal 1 received" not in err and "Signal 15 received: shutting down in order." in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_a_signal_during_teardown_cannot_replace_the_result(signame):
    # asyncio.run waits for worker threads after main() returns; v1.3 had already released the signals,
    # so a signal then turned the result into 130 or a kill.
    prelude = """
import asyncio, time
_cancel_backfills = engine.LiveTickAdapter.cancel_backfills
async def cancel_backfills(self):
    asyncio.get_running_loop().run_in_executor(None, time.sleep, 3)   # a worker thread still running
    await _cancel_backfills(self)
engine.LiveTickAdapter.cancel_backfills = cancel_backfills
"""
    child = Child(prelude, "--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "4")
    child.wait_for("=== SYSTEM HALT ===")
    time.sleep(0.5)
    child.proc.send_signal(getattr(signal, signame))
    code, err = child.finish()
    assert code == 0, err
    assert "shutdown already in progress, signal ignored" in err


def listing_csv(tmp_path):
    """The bundled bars with a first session re-dated to 2026-03-02, 207 days before the data ends."""
    bars = pd.read_csv(FIXTURE)
    first = bars[bars["datetime_ist"].str.startswith("2026-09-08")].copy()
    first["datetime_ist"] = first["datetime_ist"].str.replace("2026-09-08", "2026-03-02")
    path = tmp_path / "SWIGGY_5m_2026-03-02_2026-09-25.csv"
    pd.concat([first, bars]).to_csv(path, index=False)
    return path


def test_the_lookback_can_reach_an_older_listing(tmp_path, caplog):
    csv = str(listing_csv(tmp_path))
    args = ["--source", "csv", "--csv", csv, "--listing-date", "2026-03-02", "--no-simulate", "--run-seconds", "0.1"]
    assert asyncio.run(engine.main(args)) == 1
    assert "The 180-day lookback starts" in caplog.text and "--max-lookback-days to reach the listing" in caplog.text
    assert asyncio.run(engine.main(args + ["--max-lookback-days", "400"])) == 0


@pytest.mark.parametrize("value", ["0", "-5", "1.5", "nan", "36501", "1000000"])
def test_the_lookback_must_be_a_positive_whole_number_of_days(value, capsys):
    assert engine.build_arg_parser().parse_args(["--max-lookback-days", "400"]).max_lookback_days == 400
    with pytest.raises(SystemExit) as stop:
        engine.build_arg_parser().parse_args(["--max-lookback-days", value])
    assert stop.value.code == 2 and "argument --max-lookback-days" in capsys.readouterr().err


# ---------------------------------------------------------------- round 5: embedding hosts and lookback edges
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_hosts_own_loop_signal_handlers_survive_the_engine():
    # v1.4 re-installed asyncio's no-op C handler after removing its own loop handler, so a host that had
    # registered SIGTERM with loop.add_signal_handler could no longer be stopped by anything but SIGKILL.
    async def host():
        loop = asyncio.get_running_loop()
        stopped = asyncio.Event()
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
        try:
            await engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "0.2"])
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(stopped.wait(), 3)
            return True
        finally:
            loop.remove_signal_handler(signal.SIGTERM)

    saved = signal.getsignal(signal.SIGTERM)
    try:
        assert asyncio.run(host())
    finally:
        signal.signal(signal.SIGTERM, saved)


def test_run_from_a_worker_thread_returns_the_exit_code():
    # v1.4's run() restored signal handlers unconditionally, which raises outside the main thread.
    code = textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {str(Path(engine.__file__).parent)!r})
        import engine
        result = []
        worker = threading.Thread(target=lambda: result.append(engine.run(
            ["--source", "csv", "--csv", {str(FIXTURE)!r}, "--no-simulate", "--run-seconds", "0.2"])))
        worker.start()
        worker.join()
        print("RESULT", result)
    """)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert "RESULT [0]" in proc.stdout, proc.stdout + proc.stderr


def test_a_huge_lookback_is_capped_not_a_crash(caplog):
    # Library callers skip the CLI's bound; v1.4 overflowed the calendar (OverflowError, exit 1).
    ad = engine.CsvReplayAdapter({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": datetime(2026, 9, 8, tzinfo=engine.IST)}, ad,
                                         as_of=datetime(2026, 9, 26, tzinfo=engine.IST), max_lookback_days=10 ** 9)
    assert asyncio.run(orch.build_the_ground())


def test_a_demo_honours_a_shorter_lookback(caplog):
    # v1.4 always fetched the demo's 20 days, whatever --max-lookback-days said.
    caplog.set_level(logging.INFO, logger="QUANT_ENGINE")
    assert asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "0.1",
                                    "--max-lookback-days", "3"])) == 0
    assert "treating the first bar (2026-09-23 09:15) as the listing" in caplog.text


def test_when_the_vendor_cuts_the_listing_the_message_does_not_blame_the_lookback(caplog):
    # With Yahoo's ~60-day window, v1.4 said the 180-day lookback cut an older listing and prescribed
    # --max-lookback-days, which cannot reach it.
    class ShortVendor(engine.CsvReplayAdapter):
        history_limit_days = 59

    ad = ShortVendor({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": datetime(2025, 12, 1, tzinfo=engine.IST)}, ad,
                                         as_of=datetime(2026, 9, 26, tzinfo=engine.IST))
    assert asyncio.run(orch.build_the_ground()) is False
    assert "History starts 2026-09-08" in caplog.text and "--max-lookback-days" not in caplog.text
