"""The whole pipeline through the real CLI entry points."""
import asyncio
import logging
import subprocess
import sys
import urllib.error

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
