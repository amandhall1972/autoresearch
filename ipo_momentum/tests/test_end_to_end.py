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
from datetime import datetime, timedelta
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
    child = Child("", "--source", "csv", "--csv", str(FIXTURE), "--run-seconds", "0")
    child.wait_for("PAPER FILL")                                          # a fixed sleep races start-up under load
    child.proc.send_signal(signal.SIGINT)
    code, err = child.finish()
    assert code == 130, err
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
        assert asyncio.run(engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "0.2"])) == 0
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
            assert await engine.main(["--source", "csv", "--csv", str(FIXTURE), "--no-simulate", "--run-seconds", "0.2"]) == 0
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
        history_cutoff = engine.BrokerAdapter.history_cutoff              # a vendor's rolling window, not a file's

    ad = ShortVendor({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": datetime(2025, 12, 1, tzinfo=engine.IST)}, ad,
                                         as_of=datetime(2026, 9, 26, tzinfo=engine.IST))
    assert asyncio.run(orch.build_the_ground()) is False
    assert "History starts 2026-09-08" in caplog.text and "--max-lookback-days" not in caplog.text


# ---------------------------------------------------------------- round 6: signals shared by engines and hosts
HOST = """
import asyncio, os, signal, sys
sys.path.insert(0, {engine_dir!r})
import engine
ARGS = ["--source", "csv", "--csv", {csv!r}, "--no-simulate", "--run-seconds"]
got = []


def loop_callback(loop, signum):
    handle = getattr(loop, "_signal_handlers", {{}}).get(signum)
    return getattr(getattr(handle, "_callback", None), "__qualname__", None)


async def hooked(n, run, signum=signal.SIGTERM):     # until n engines take signum (a fixed sleep races start-up)
    while len(engine._STOP_ROUTES.routes.get(signum, {{}}).get("callbacks", ())) < n:
        if run.done():
            raise SystemExit(f"the engine exited before hooking {{signum}}: {{run.result()!r}}")
        await asyncio.sleep(0.01)


async def delivered(signum, timeout=3.0):
    before = len(got)
    os.kill(os.getpid(), signum)
    deadline = asyncio.get_running_loop().time() + timeout
    while len(got) == before and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    return got[before:]


async def host():
    loop = asyncio.get_running_loop()
{body}


{runner}
"""


def run_host(body, uvloop=False):
    """``body`` runs as a host coroutine that embeds engine.main() in a fresh interpreter."""
    runner = "import uvloop\nuvloop.install()\nasyncio.run(host())" if uvloop else "asyncio.run(host())"
    code = HOST.format(engine_dir=str(Path(engine.__file__).parent), csv=str(FIXTURE),
                       body=textwrap.indent(textwrap.dedent(body), "    "), runner=runner)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_on_uvloop_the_engine_owns_the_signal_and_never_leaves_it_swallowed():
    # v1.5 read the host's callback from asyncio's private table, which uvloop does not expose: it then
    # re-installed uvloop's dispatcher with no entry behind it, and every later SIGTERM was swallowed.
    # uvloop's callbacks cannot be read back, so the signal returns to its default, with a warning.
    pytest.importorskip("uvloop")
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        engine_run = asyncio.create_task(engine.main(ARGS + ["30"]))
        await hooked(1, engine_run)
        os.kill(os.getpid(), signal.SIGTERM)                          # stops the engine in order
        print("main", await engine_run, "host got", got, flush=True)
        print("after", signal.getsignal(signal.SIGTERM) == signal.SIG_DFL, flush=True)
    """, uvloop=True)
    assert code == 0, err
    assert "main 143 host got []" in out and "after True" in out, out + err
    assert "Signal 15 received: shutting down in order." in err
    assert "cannot be read back, so the signal is back at its default" in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
def test_a_host_that_stops_its_loop_on_sigterm_cannot_cut_the_shutdown_short(loop_kind):
    # The engine owns the signal while it runs: chaining to the host's loop.stop would stop the loop in
    # the middle of settling an entry.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    code = textwrap.dedent(f"""
        import asyncio, os, signal, sys
        sys.path.insert(0, {str(Path(engine.__file__).parent)!r})
        import engine
        engine.configure_logging()
        if {loop_kind!r} == "uvloop":
            import uvloop
            loop = uvloop.new_event_loop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.add_signal_handler(signal.SIGTERM, loop.stop)
        task = loop.create_task(engine.main(["--source", "csv", "--csv", {str(FIXTURE)!r}, "--no-simulate",
                                             "--run-seconds", "30"]))

        async def stop_once_hooked():                                 # a fixed delay races start-up
            while not engine._STOP_ROUTES.routes.get(signal.SIGTERM, {{}}).get("callbacks") and not task.done():
                await asyncio.sleep(0.01)
            os.kill(os.getpid(), signal.SIGTERM)

        stopper = loop.create_task(stop_once_hooked())
        task.add_done_callback(lambda _: loop.stop())
        loop.call_later(20.0, loop.stop)                              # a stuck engine still ends the test
        loop.run_forever()
        print("done", task.done() and task.result(), flush=True)
    """)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert "done 143" in proc.stdout, proc.stdout + proc.stderr
    assert "=== SYSTEM HALT ===" in proc.stderr


@pytest.mark.skipif(not (sys.platform.startswith("linux") and os.uname().machine == "x86_64"),
                    reason="reads glibc's x86_64 struct sigaction")
def test_handing_a_signal_back_keeps_its_sa_restart_flag():
    # asyncio's loop.add_signal_handler sets SA_RESTART; re-installing the same handler with signal.signal
    # would clear it, and interrupted system calls would start failing with EINTR after main().
    code, out, err = run_host("""
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)

        class SigAction(ctypes.Structure):
            _fields_ = [("handler", ctypes.c_void_p), ("mask", ctypes.c_ulong * 16), ("flags", ctypes.c_int),
                        ("restorer", ctypes.c_void_p)]

        def restart():
            action = SigAction()
            assert libc.sigaction(signal.SIGTERM, None, ctypes.byref(action)) == 0
            return bool(action.flags & 0x10000000)

        loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        before = restart()
        print("main", await engine.main(ARGS + ["0.2"]), "SA_RESTART", before, restart(), flush=True)
    """)
    assert code == 0, err
    assert "main 0 SA_RESTART True True" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_one_stop_signal_stops_every_engine_in_the_loop_in_order():
    # v1.5: the engine that started last took the signal alone, and whichever finished first handed the
    # signal back, so a supervisor's stop killed the other engine outright.
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        a = asyncio.create_task(engine.main(ARGS + ["0.5"]))
        await hooked(1, a)
        b = asyncio.create_task(engine.main(ARGS + ["30"]))
        print("a", await a, flush=True)
        c = asyncio.create_task(engine.main(ARGS + ["30"]))
        await hooked(2, c)
        os.kill(os.getpid(), signal.SIGTERM)                          # b and c are running; a has finished
        print("b c", await b, await c, flush=True)
        during = list(got)                                            # the engines had the signal
        print("host got", during, "after", await delivered(signal.SIGTERM), flush=True)
    """)
    assert code == 0, err
    assert "a 0" in out and "b c 143 143" in out, out + err
    assert "host got [] after ['host']" in out, out + err
    assert err.count("Signal 15 received: shutting down in order.") == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_finished_engine_never_keeps_the_signal():
    # v1.5 re-armed the finished engine's handler (which ignores signals during its shutdown) when the
    # engine that started after it ended: SIGINT and SIGTERM were then ignored for good.
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        a = asyncio.create_task(engine.main(ARGS + ["0.5"]))
        await hooked(1, a)
        b = asyncio.create_task(engine.main(ARGS + ["1.5"]))
        print("engines", await a, await b, flush=True)
        print("after", loop_callback(loop, signal.SIGTERM), await delivered(signal.SIGTERM), flush=True)
    """)
    assert code == 0, err
    assert "engines 0 0" in out and "after list.append ['host']" in out, out + err
    assert "signal ignored" not in err, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_handler_the_host_replaces_during_a_run_is_kept():
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "old")
        run = asyncio.create_task(engine.main(ARGS + ["0.6"]))
        await hooked(1, run)
        loop.add_signal_handler(signal.SIGTERM, got.append, "new")
        print("main", await run, "after", await delivered(signal.SIGTERM), flush=True)
    """)
    assert code == 0, err
    assert "main 0 after ['new']" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_handler_the_host_removes_during_a_run_stays_removed():
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        run = asyncio.create_task(engine.main(ARGS + ["0.6"]))
        await hooked(1, run)
        loop.remove_signal_handler(signal.SIGTERM)
        print("main", await run, "after", loop_callback(loop, signal.SIGTERM),
              signal.getsignal(signal.SIGTERM) == signal.SIG_DFL, flush=True)
    """)
    assert code == 0, err
    assert "main 0 after None True" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_live_plain_handler_beside_a_stale_loop_entry_comes_back_too():
    # v1.5 restored only the loop entry, dropping the host's plain handler that was live beside it.
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "loop entry")
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("plain handler"))
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.5)
        before = sorted(got)
        got.clear()
        main = await engine.main(ARGS + ["0.3"])
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.5)
        print("main", main, "before", before, "after", sorted(got), flush=True)
    """)
    assert code == 0, err
    assert "main 0 before ['loop entry', 'plain handler'] after ['loop entry', 'plain handler']" in out, out + err


def test_when_both_the_lookback_and_the_vendor_cut_the_listing_the_message_names_both(caplog):
    # v1.5 blamed only the vendor and printed the lookback's start as where the vendor's data begins.
    class ShortVendor(engine.CsvReplayAdapter):
        def history_cutoff(self, symbol=None):
            return datetime(2026, 9, 10, tzinfo=engine.IST)

    ad = ShortVendor({"SWIGGY": FIXTURE})
    orch = engine.ProductionOrchestrator({"SWIGGY": datetime(2026, 6, 19, tzinfo=engine.IST)}, ad,
                                         as_of=datetime(2026, 9, 26, tzinfo=engine.IST), max_lookback_days=10)
    assert asyncio.run(orch.build_the_ground()) is False
    assert ("The 10-day lookback starts 2026-09-17 and the source's history 2026-09-10, both after the "
            "2026-06-19 listing") in caplog.text
    assert "--max-lookback-days 17 starts at the source's earliest day" in caplog.text
    orch17 = engine.ProductionOrchestrator({"SWIGGY": datetime(2026, 6, 19, tzinfo=engine.IST)}, ad,
                                           as_of=datetime(2026, 9, 26, tzinfo=engine.IST), max_lookback_days=17)
    caplog.clear()
    assert asyncio.run(orch17.build_the_ground()) is False
    assert "History starts 2026-09-10" in caplog.text and "lookback" not in caplog.text


def test_the_live_router_judges_signal_ages_on_the_feeds_clock(monkeypatch):
    # The router must get exchange_clock: with the plain wall clock, a host 20 s behind the exchange
    # refuses every tick-closed signal as "not closed yet".
    seen = []

    def feed(api_key, access_token, tokens, tick_adapter, feed_dead):
        oms = tick_adapter.bar_listeners[0].__self__
        now = engine.datetime.now(engine.IST)
        tick_adapter.note_feed_lag(now, -20.0)
        seen.append((oms.clock() - now).total_seconds())
        tick_adapter.loop.call_later(0.1, feed_dead.set)
        return types.SimpleNamespace(close=lambda: None)

    kite_env(monkeypatch, feed)
    asyncio.run(engine.main(["--source", "kite", "--listing-date", "2026-09-08", "--live-feed", "--run-seconds", "30"]))
    assert len(seen) == 1 and abs(seen[0] - 20.0) < 1.0


def test_the_live_routers_clock_does_not_step_with_the_host_clock(monkeypatch):
    # Signal ages are judged on the feed's steady clock: an NTP step of the host clock must not age or rejuvenate
    # every pending signal at once.
    moves = []

    def feed(api_key, access_token, tokens, tick_adapter, feed_dead):
        oms = tick_adapter.bar_listeners[0].__self__
        before = oms.clock()
        monkeypatch.setattr(KiteDayClock, "FIXED", KiteDayClock.FIXED + timedelta(seconds=20))   # the step
        moves.append((oms.clock() - before).total_seconds())
        tick_adapter.loop.call_later(0.1, feed_dead.set)
        return types.SimpleNamespace(close=lambda: None)

    kite_env(monkeypatch, feed)
    asyncio.run(engine.main(["--source", "kite", "--listing-date", "2026-09-08", "--live-feed", "--run-seconds", "30"]))
    assert len(moves) == 1 and abs(moves[0]) < 1.0


# ---------------------------------------------------------------- round 7: a host that changes its handling mid-run
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
@pytest.mark.parametrize("change", ["re-register", "remove"])
def test_an_engine_that_starts_after_the_host_took_the_signal_back_owns_it(loop_kind, change):
    # v1.6 appended a later engine to the existing route without checking that the route still received the
    # signal: after the host re-registered (the README's own advice) or removed SIGTERM, a supervisor's stop
    # went to the host's callback, or killed the process, with the later engine's entry in flight.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    take_back = ('loop.add_signal_handler(signal.SIGTERM, got.append, "host")' if change == "re-register"
                 else "loop.remove_signal_handler(signal.SIGTERM)")
    code, out, err = run_host(f"""
        a = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(1, a)
        {take_back}
        b = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(2, b)
        os.kill(os.getpid(), signal.SIGTERM)
        print("engines", await a, await b, "host got", got, flush=True)
    """, uvloop=loop_kind == "uvloop")
    assert code == 0, out + err
    assert "engines 143 143 host got []" in out, out + err
    assert err.count("Signal 15 received: shutting down in order.") == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
def test_a_plain_handler_or_ignore_the_host_sets_during_a_run_is_kept(loop_kind):
    # v1.6 judged only asyncio's loop table: a SIG_IGN or plain handler set with signal.signal mid-run was
    # replaced by the default afterwards, and the next SIGHUP killed a host that had chosen to ignore it.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    code, out, err = run_host(f"""
        run = asyncio.create_task(engine.main(ARGS + ["0.6"]))
        await hooked(1, run)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("plain"))
        print("main", await run, flush=True)
        if {loop_kind!r} == "asyncio":                                # uvloop keeps its own wakeup fd
            # The hand-back emptied the loop table itself, so it must drop the wakeup fd too: left behind, it
            # would point at the closed self-pipe, and a later signal would write into whatever file reuses it.
            print("wakeup fd", signal.set_wakeup_fd(-1), flush=True)
        print("after", signal.getsignal(signal.SIGHUP) == signal.SIG_IGN, await delivered(signal.SIGTERM), flush=True)
    """, uvloop=loop_kind == "uvloop")
    assert code == 0, out + err
    assert "main 0" in out and "after True ['plain']" in out, out + err
    assert loop_kind == "uvloop" or "wakeup fd -1" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_on_uvloop_a_callback_the_host_registers_during_a_run_is_kept():
    pytest.importorskip("uvloop")
    code, out, err = run_host("""
        run = asyncio.create_task(engine.main(ARGS + ["0.6"]))
        await hooked(1, run)
        loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        print("main", await run, "after", await delivered(signal.SIGTERM), flush=True)
    """, uvloop=True)
    assert code == 0, out + err
    assert "main 0 after ['host']" in out, out + err


# ---------------------------------------------------------------- round 8: re-takes and hand-backs
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
def test_an_engine_that_starts_while_the_host_ignores_the_signal_still_gets_it_later(loop_kind):
    # v1.7 skipped an engine that hooked while the host had SIGTERM at SIG_IGN (a common guard around spawning
    # workers): once the host put its handler back, a supervisor's stop reached only the other engines.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    code, out, err = run_host("""
        a = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(1, a)
        old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        b = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(2, b)
        signal.signal(signal.SIGTERM, old)
        os.kill(os.getpid(), signal.SIGTERM)
        print("engines", await a, await b, flush=True)
    """, uvloop=loop_kind == "uvloop")
    assert code == 0, out + err
    assert "engines 143 143" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_held_engine_is_not_carried_into_a_later_run_once_the_host_restores_its_handler():
    # v1.7 carried main(hold_signals=True)'s finished callback into the next engine's re-take, so the host's
    # restored handler was never handed back and every later stop was "ignored" for good.
    code, out, err = run_host("""
        held = await engine.main(ARGS + ["0.3"], hold_signals=True)
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("host"))
        later = await engine.main(ARGS + ["0.3"])
        print("mains", held, later, "after", await delivered(signal.SIGTERM), await delivered(signal.SIGTERM), flush=True)
    """)
    assert code == 0, out + err
    assert "mains 0 0 after ['host'] ['host']" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_re_take_keeps_the_hosts_pre_run_loop_callback():
    # v1.7's re-take recorded the engine's own dispatcher as the host's loop callback: the host's callback X was
    # lost, and the engine's stale entry stayed in the host's loop table.
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "X loop cb")
        a = asyncio.create_task(engine.main(ARGS + ["3"]))
        await hooked(1, a)
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("P plain"))
        b = asyncio.create_task(engine.main(ARGS + ["0.3"]))
        await hooked(2, b)                                               # b re-took the signal while a runs
        print("engines", await a, await b, "table", loop_callback(loop, signal.SIGTERM), flush=True)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.5)
        print("delivered", sorted(got), flush=True)
    """)
    assert code == 0, out + err
    assert "engines 0 0 table list.append" in out, out + err
    assert "delivered ['P plain', 'X loop cb']" in out, out + err


@pytest.mark.skipif(not (sys.platform.startswith("linux") and os.uname().machine == "x86_64"),
                    reason="reads glibc's x86_64 struct sigaction")
def test_a_plain_handler_set_mid_run_keeps_its_flags():
    # v1.7 re-installed it with signal.signal on hand-back, which clears the SA_RESTART the host had asked for.
    # (On uvloop, whose table cannot be read, it is still re-installed: a documented limitation.)
    code, out, err = run_host("""
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)

        class SigAction(ctypes.Structure):
            _fields_ = [("handler", ctypes.c_void_p), ("mask", ctypes.c_ulong * 16), ("flags", ctypes.c_int),
                        ("restorer", ctypes.c_void_p)]

        def restart():
            action = SigAction()
            assert libc.sigaction(signal.SIGTERM, None, ctypes.byref(action)) == 0
            return bool(action.flags & 0x10000000)

        run = asyncio.create_task(engine.main(ARGS + ["0.6"]))
        await hooked(1, run)
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("P plain"))
        signal.siginterrupt(signal.SIGTERM, False)
        mid = restart()
        print("main", await run, "SA_RESTART", mid, restart(), flush=True)
    """)
    assert code == 0, out + err
    assert "main 0 SA_RESTART True True" in out, out + err


# ---------------------------------------------------------------- round 9: joins under SIG_IGN, held runs, hand-backs
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
@pytest.mark.parametrize("change", ["re-register", "remove"])
def test_an_engine_that_joins_under_sig_ign_a_route_the_host_took_back_still_gets_the_signal(loop_kind, change):
    # v1.8 joined the route without checking that it still received the signal. After the host had re-registered
    # or removed SIGTERM mid-run, a supervisor's stop, sent once the guard was lifted, went to the host's callback
    # (or killed the process) while both engines ran.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    take_back = ('loop.add_signal_handler(signal.SIGTERM, got.append, "host")' if change == "re-register"
                 else "loop.remove_signal_handler(signal.SIGTERM)")
    code, out, err = run_host(f"""
        a = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(1, a)
        {take_back}
        old = signal.signal(signal.SIGTERM, signal.SIG_IGN)            # a guard around spawning a worker
        b = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(2, b)
        signal.signal(signal.SIGTERM, old)
        await asyncio.sleep(0.3)                                       # taken back within RECLAIM_POLL
        os.kill(os.getpid(), signal.SIGTERM)
        print("engines", await a, await b, "host got", got, flush=True)
    """, uvloop=loop_kind == "uvloop")
    assert code == 0, out + err
    assert "engines 143 143 host got []" in out, out + err
    assert err.count("Signal 15 received: shutting down in order.") == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_route_left_to_held_runs_is_not_taken_back_from_the_host_after_its_guard():
    # Control for the reclaim: once every engine on the route has finished (one of them held), the host's own
    # handler set after the guard keeps the signal; taking it back would swallow every later stop.
    code, out, err = run_host("""
        a = asyncio.create_task(engine.main(ARGS + ["1.0"]))
        await hooked(1, a)
        old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        b = asyncio.create_task(engine.main(ARGS + ["1.5"], hold_signals=True))
        await hooked(2, b)
        print("engines", await a, await b, flush=True)
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("P plain"))
        await asyncio.sleep(0.3)
        print("after", await delivered(signal.SIGTERM), flush=True)
    """)
    assert code == 0, out + err
    assert "engines 0 0" in out and "after ['P plain']" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_release_signals_hands_back_what_a_held_run_kept():
    # v1.8 told the caller to "restore" the handler it saved; on asyncio that handler is one shared function, so the
    # restore changed nothing: the held run kept the signal and every later stop was "ignored" for good.
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "X host")
        held = await engine.main(ARGS + ["0.3"], hold_signals=True)
        engine.release_signals()
        print("held", held, "after", await delivered(signal.SIGTERM), loop_callback(loop, signal.SIGTERM), flush=True)
        a = asyncio.create_task(engine.main(ARGS + ["5"]))
        await hooked(1, a)
        print("held beside a", await engine.main(ARGS + ["0.3"], hold_signals=True), flush=True)
        engine.release_signals()                                      # a still runs: it keeps the signal
        os.kill(os.getpid(), signal.SIGTERM)
        print("a", await a, "then", await delivered(signal.SIGTERM), flush=True)
    """)
    assert code == 0, out + err
    assert "held 0 after ['X host'] list.append" in out, out + err
    assert "held beside a 0" in out and "a 143 then ['X host']" in out, out + err
    assert "signal ignored" not in err, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
def test_an_engine_that_ends_inside_the_hosts_ignore_says_what_to_restore(loop_kind):
    # The handler a host saves when it sets SIG_IGN mid-run is the engine's. If the last engine ends inside that
    # window, restoring it swallows every later SIGTERM (and Ctrl-C), silently. v1.8 said nothing.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    code, out, err = run_host("""
        a = asyncio.create_task(engine.main(ARGS + ["0.3"]))
        await hooked(1, a)
        old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("a", await a, "ignored", signal.getsignal(signal.SIGTERM) == signal.SIG_IGN, flush=True)
    """, uvloop=loop_kind == "uvloop")
    assert code == 0, out + err
    assert "a 0 ignored True" in out, out + err
    assert ("Signal 15 is handed back ignored, as the host set it while the engine ran. The handler saved then was the "
            "engine's and would now swallow the signal: to lift the ignore, restore what was there before the engine "
            "started (<Handlers.SIG_DFL: 0>).") in err, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_on_uvloop_a_re_take_keeps_the_warning_that_the_hosts_callback_was_lost():
    # The re-take must carry the "lost" flag: without it, the host's pre-run uvloop callback was dropped silently.
    pytest.importorskip("uvloop")
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "X loop cb")
        a = asyncio.create_task(engine.main(ARGS + ["1.5"]))
        await hooked(1, a)
        signal.signal(signal.SIGTERM, lambda signum, frame: got.append("P plain"))
        b = asyncio.create_task(engine.main(ARGS + ["0.3"]))
        await hooked(2, b)                                               # b re-took the signal while a runs
        print("engines", await a, await b, "after", await delivered(signal.SIGTERM), flush=True)
    """, uvloop=True)
    assert code == 0, out + err
    assert "engines 0 0 after ['P plain']" in out, out + err
    assert "cannot be read back, so the signal is left as the host set it mid-run" in err, out + err


# ---------------------------------------------------------------- round 10: hosts that drive their own loop, fork, or thread
SCRIPT = """
import asyncio, gc, os, signal, sys
sys.path.insert(0, {engine_dir!r})
import engine
ARGS = ["--source", "csv", "--csv", {csv!r}, "--no-simulate", "--run-seconds"]
engine.configure_logging()


async def until_hooked(n, signum=signal.SIGTERM):
    while len(engine._STOP_ROUTES.routes.get(signum, {{}}).get("callbacks", ())) < n:
        await asyncio.sleep(0.01)


{body}
"""


def run_script(body):
    """``body`` runs as a module-level script (its own loop handling) in a fresh interpreter."""
    code = SCRIPT.format(engine_dir=str(Path(engine.__file__).parent), csv=str(FIXTURE), body=textwrap.dedent(body))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_on_uvloop_release_signals_outside_the_loop_really_restores_the_default():
    # uvloop's remove_signal_handler does nothing while the loop is not running. v1.9 relied on it, so a held run
    # released after run_until_complete left SIGTERM on uvloop's dispatcher with nothing behind it (swallowed for good),
    # while the log said the signal was "back at its default".
    pytest.importorskip("uvloop")
    code, out, err = run_script("""
        import uvloop
        loop = uvloop.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.add_signal_handler(signal.SIGTERM, print, "host")
        print("main", loop.run_until_complete(engine.main(ARGS + ["0.3"], hold_signals=True)), flush=True)
        engine.release_signals(loop)
        print("default", signal.getsignal(signal.SIGTERM) == signal.SIG_DFL, flush=True)
    """)
    assert code == 0, out + err
    assert "main 0" in out and "default True" in out, out + err
    assert "cannot be read back, so the signal is back at its default" in err


@pytest.mark.skipif(sys.platform != "linux", reason="multiprocessing's fork start method")
def test_a_forked_worker_takes_its_own_stop_signals():
    # The child inherited the parent's routes, on a loop that is not closed, so its engine never hooked a signal: a
    # SIGTERM sent to the worker reached the parent's self-pipe and stopped the PARENT's engine, and the worker's engine
    # traded on.
    code, out, err = run_host("""
        import multiprocessing

        def worker(ready):
            async def run():
                t = asyncio.create_task(engine.main(ARGS + ["5"]))
                while not ((r := engine._STOP_ROUTES.routes.get(signal.SIGTERM)) and r["loop"] is asyncio.get_running_loop()):
                    if t.done():
                        break
                    await asyncio.sleep(0.01)
                os.write(ready, b"x")
                return await t
            sys.exit(asyncio.run(run()))

        a = asyncio.create_task(engine.main(ARGS + ["8"]))
        await hooked(1, a)
        r, w = os.pipe()
        p = multiprocessing.get_context("fork").Process(target=worker, args=(w,))
        p.start()
        await loop.run_in_executor(None, os.read, r, 1)
        os.kill(p.pid, signal.SIGTERM)                               # stop the worker only
        done, _ = await asyncio.wait({a}, timeout=1.5)
        print("parent", "running" if not done else a.result(), flush=True)
        await loop.run_in_executor(None, p.join, 30)
        print("worker", p.exitcode, "a", await a, flush=True)
    """)
    assert code == 0, out + err
    assert "parent running" in out and "worker 143 a 0" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_release_signals_from_another_thread_refuses_before_changing_anything():
    # Only the main thread can hand a signal back. v1.9 dropped SIGINT's route, then failed in the hand-back: Ctrl-C
    # was swallowed for the rest of the process, and a later release from the main thread could not repair it.
    code, out, err = run_host("""
        loop.add_signal_handler(signal.SIGTERM, got.append, "X host")
        held = await engine.main(ARGS + ["0.3"], hold_signals=True)
        errors = []

        def off_main():
            try:
                engine.release_signals(loop)
            except ValueError as e:
                errors.append(type(e).__name__ + (" (between runs)" if "between runs" in str(e) else ""))

        await loop.run_in_executor(None, off_main)
        print("held", held, "errors", errors, "routes", sorted(map(int, engine._STOP_ROUTES.routes)), flush=True)
        engine.release_signals()
        sigint = getattr(signal.getsignal(signal.SIGINT), "__name__", None)
        print("after", await delivered(signal.SIGTERM), "routes", sorted(map(int, engine._STOP_ROUTES.routes)),
              "sigint", loop_callback(loop, signal.SIGINT), sigint != "_sighandler_noop", flush=True)
    """)
    assert code == 0, out + err
    assert "held 0 errors ['ValueError (between runs)'] routes [1, 2, 15]" in out, out + err
    assert "after ['X host'] routes [] sigint None True" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_without_loop_signal_handlers_an_engine_that_ends_inside_the_ignore_says_what_to_restore():
    # The plain-handler path (a loop that cannot own signals, as on Windows) skipped v1.9's warning.
    code, out, err = run_host("""
        def refuse(*args, **kwargs):
            raise NotImplementedError

        loop.add_signal_handler = refuse
        a = asyncio.create_task(engine.main(ARGS + ["0.3"]))
        await hooked(1, a)
        old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("a", await a, "ignored", signal.getsignal(signal.SIGTERM) == signal.SIG_IGN, flush=True)
    """)
    assert code == 0, out + err
    assert "a 0 ignored True" in out, out + err
    assert ("Signal 15 is handed back ignored, as the host set it while the engine ran. The handler saved then was the "
            "engine's and would now swallow the signal: to lift the ignore, restore what was there before the engine "
            "started (<Handlers.SIG_DFL: 0>).") in err, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_reclaim_left_pending_by_a_loop_that_stopped_inside_the_guard_is_dropped_silently():
    # v1.9's reclaim was a task: released (or abandoned) after run_until_complete, it was never cancelled for real
    # and asyncio logged "Task was destroyed but it is pending!" when the closed loop let it go.
    code, out, err = run_script("""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def host():
            a = asyncio.create_task(engine.main(ARGS + ["0.5"], hold_signals=True))
            await until_hooked(1)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)                 # a guard the host never lifts
            b = asyncio.create_task(engine.main(ARGS + ["0.5"], hold_signals=True))
            await until_hooked(2)
            return await a, await b

        print("engines", loop.run_until_complete(host()), flush=True)
        engine.release_signals(loop)
        loop.close()
        gc.collect()
        print("closed", flush=True)
    """)
    assert code == 0, out + err
    assert "engines (0, 0)" in out and "closed" in out, out + err
    assert "Task was destroyed but it is pending" not in err, err


# ---------------------------------------------------------------- round 11: a forked worker, before and after its engine
@pytest.mark.skipif(sys.platform != "linux", reason="multiprocessing's fork start method")
@pytest.mark.parametrize("loop_kind", ["asyncio", "uvloop"])
@pytest.mark.parametrize("window", ["before its engine hooks", "after its engine ended"])
def test_a_forked_worker_takes_its_own_stop_signals_before_and_after_its_engine(loop_kind, window):
    # v1.10 dropped the parent's routes only when the worker's own engine hooked. Before that (a worker's kite
    # start-up takes seconds) and after its engine ended, the worker kept the parent engine's dispatcher and wakeup
    # fd: a stop sent to the worker stopped the PARENT's engines (or was swallowed), and the worker traded on.
    if loop_kind == "uvloop":
        pytest.importorskip("uvloop")
    code, out, err = run_host(f"""
        import multiprocessing, time

        def worker(ready):
            if {window!r} == "after its engine ended":
                asyncio.run(engine.main(ARGS + ["0.3"]))
            os.write(ready, b"x")
            time.sleep(5)                                            # start-up (a network fetch), or life after
            sys.exit(7)

        a = asyncio.create_task(engine.main(ARGS + ["8"]))
        await hooked(1, a)
        r, w = os.pipe()
        p = multiprocessing.get_context("fork").Process(target=worker, args=(w,))
        p.start()
        await loop.run_in_executor(None, os.read, r, 1)
        os.kill(p.pid, signal.SIGTERM)                               # stop the worker only
        done, _ = await asyncio.wait({{a}}, timeout=1.5)
        print("parent", "running" if not done else a.result(), flush=True)
        await loop.run_in_executor(None, p.join, 30)
        os.kill(os.getpid(), signal.SIGTERM)
        print("worker", p.exitcode, "a", await a, flush=True)
    """, uvloop=(loop_kind == "uvloop"))
    assert code == 0, out + err
    assert "parent running" in out and "worker -15 a 143" in out, out + err


@pytest.mark.skipif(sys.platform != "linux", reason="os.fork")
@pytest.mark.parametrize("host_callback", [False, True])
def test_a_forked_helper_that_runs_no_engine_is_stopped_by_its_own_stop(host_callback):
    # A plain os.fork() helper forwarded any stop it received to the parent's engines and ignored it itself. With a
    # host loop callback before the engine, the saved handler is asyncio's shared no-op: the child gets the default.
    code, out, err = run_host(f"""
        import time
        if {host_callback!r}:
            loop.add_signal_handler(signal.SIGTERM, got.append, "host")
        a = asyncio.create_task(engine.main(ARGS + ["8"]))
        await hooked(1, a)
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.write(w, b"x")
            time.sleep(5)
            os._exit(7)
        await loop.run_in_executor(None, os.read, r, 1)
        os.kill(pid, signal.SIGTERM)
        done, _ = await asyncio.wait({{a}}, timeout=1.5)
        _, status = await loop.run_in_executor(None, os.waitpid, pid, 0)
        print("parent", "running" if not done else a.result(), "helper", os.waitstatus_to_exitcode(status), flush=True)
        os.kill(os.getpid(), signal.SIGTERM)
        print("a", await a, flush=True)
    """)
    assert code == 0, out + err
    assert "parent running helper -15" in out and "a 143" in out, out + err


@pytest.mark.skipif(sys.platform != "linux", reason="multiprocessing's fork start method")
def test_a_host_guard_is_left_alone_in_a_forked_worker():
    # Control: a SIG_IGN the host set around the fork is the host's choice, in the child too.
    code, out, err = run_host("""
        import multiprocessing

        def worker(conn):
            conn.send(signal.getsignal(signal.SIGTERM) == signal.SIG_IGN)

        a = asyncio.create_task(engine.main(ARGS + ["2"]))
        await hooked(1, a)
        old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        parent_end, child_end = multiprocessing.Pipe()
        p = multiprocessing.get_context("fork").Process(target=worker, args=(child_end,))
        p.start()
        ignored = await loop.run_in_executor(None, parent_end.recv)
        signal.signal(signal.SIGTERM, old)
        await loop.run_in_executor(None, p.join, 30)
        print("ignored in the child", ignored, "a", await a, flush=True)
    """)
    assert code == 0, out + err
    assert "ignored in the child True a 0" in out, out + err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_held_run_whose_loop_closed_unreleased_is_reported_when_the_next_engine_starts():
    # A release lost with a closed loop (the host's release never ran) was silent.
    code, out, err = run_script("""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print("held", loop.run_until_complete(engine.main(ARGS + ["0.3"], hold_signals=True)), flush=True)
        loop.close()
        print("next", asyncio.run(engine.main(ARGS + ["0.3"])), flush=True)
    """)
    assert code == 0, out + err
    assert "held 0" in out and "next 0" in out, out + err
    assert "was still held by a finished main(hold_signals=True) when its loop closed" in err, out + err


@pytest.mark.skipif(sys.platform != "linux", reason="os.fork")
def test_a_forked_helper_does_not_inherit_a_stale_loop_dispatcher_the_host_had():
    # The host left a closed uvloop loop's dispatcher installed before the engine ran. Restored in a forked child, it
    # would swallow every stop sent to the child: a dispatcher bound to any loop goes back to the default there.
    pytest.importorskip("uvloop")
    code, out, err = run_script("""
        import time, uvloop
        other = uvloop.new_event_loop()
        other.run_until_complete(asyncio.sleep(0))
        other.add_signal_handler(signal.SIGTERM, print, "other")
        other.close()

        async def host():
            loop = asyncio.get_running_loop()
            a = asyncio.create_task(engine.main(ARGS + ["6"]))
            await until_hooked(1)
            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.write(w, b"x")
                time.sleep(4)
                os._exit(7)
            await loop.run_in_executor(None, os.read, r, 1)
            os.kill(pid, signal.SIGTERM)
            _, status = await loop.run_in_executor(None, os.waitpid, pid, 0)
            print("helper", os.waitstatus_to_exitcode(status), flush=True)
            os.kill(os.getpid(), signal.SIGTERM)
            print("a", await a, flush=True)

        asyncio.run(host())
    """)
    assert code == 0, out + err
    assert "helper -15" in out and "a 143" in out, out + err
