import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import engine

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "SWIGGY_5m_2026-09-08_2026-09-25.csv"


def ist(*args) -> datetime:
    return datetime(*args, tzinfo=engine.IST)


def make_bars(closes, volumes=None, start=None, spread=0.5) -> pd.DataFrame:
    """Deterministic 5-minute bars: Open = previous close, High/Low = max/min(O, C) +/- spread."""
    start = start or ist(2026, 9, 1, 9, 15)
    volumes = volumes if volumes is not None else [100_000] * len(closes)
    rows, prev = [], closes[0]
    for c in closes:
        o = prev
        rows.append([o, max(o, c) + spread, min(o, c) - spread, c, float(volumes[len(rows)])])
        prev = c
    idx = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(len(closes))], name="datetime")
    return pd.DataFrame(rows, columns=engine.OHLCV, index=idx).astype("float64")


@pytest.fixture(scope="session")
def real_bars() -> pd.DataFrame:
    return engine.CsvReplayAdapter.load(FIXTURE)


@pytest.fixture
def fast_sleep(monkeypatch):
    """Make retry back-offs instant without breaking the event loop's own scheduling."""
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        return await real_sleep(0)

    monkeypatch.setattr(engine.asyncio, "sleep", _sleep)
    return _sleep
