"""
====================================================================================
INSTITUTIONAL QUANTITATIVE ENGINE - IPO MOMENTUM & LIVE EXECUTION (V1.8)
====================================================================================
Architecture:
1. Data Harmonization (Historical Reality Sync via REST, or offline CSV replay)
2. Live Tick Ingestion (Thread-Safe In-Memory Synthesizer + session bar clock)
3. Vectorized Alpha Engine (AVWAP, RVOL, Base Breakouts)
4. Execution Router (Fixed-Risk Sizing, Notional Cap, Paper or Zerodha Execution)

Quick start (see README.md):
    python engine.py                                        # Yahoo history + simulated breakout, paper fills
    python engine.py --source csv                           # bundled real SWIGGY 5m bars, fully offline
    python engine.py --source kite --listing-date YYYY-MM-DD   # Zerodha history (KITE_API_KEY, KITE_ACCESS_TOKEN)

Paper execution is the default. Real orders require
    --source kite --listing-date YYYY-MM-DD --live-feed --live-orders --expect-ip <static IP>
and the synthetic tape never runs alongside a live feed. Nothing here is investment advice.
====================================================================================
"""

import argparse
import asyncio
import collections
import functools
import ipaddress
import json
import logging
import math
import os
import signal
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import pandas as pd

# Standard Timezone Handling. ZoneInfo raises ZoneInfoNotFoundError (not
# ImportError) when the host has no IANA database, e.g. Windows without the
# `tzdata` package; IST has had no DST since 1945, so a fixed offset is exact.
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

logger = logging.getLogger("QUANT_ENGINE")

OHLCV = ["Open", "High", "Low", "Close", "Volume"]
BAR_MINUTES = 5
BAR_CLOSE_GRACE = timedelta(seconds=2)          # the bar clock closes a bar this long after its bucket ends
SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 30)
DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "SWIGGY_5m_2026-09-08_2026-09-25.csv"
MAX_LOOKBACK_DAYS = 36_500           # 100 years: longer than any listing can matter, and calendar-safe
INTERVAL_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60}
# Kite historical API: interval name and the days one request may span (kept under the published caps
# of 60 / 100 / 100 / 100 / 200 / 200 / 400 days).
KITE_INTERVALS = {"1m": ("minute", 55), "3m": ("3minute", 90), "5m": ("5minute", 90), "10m": ("10minute", 90),
                  "15m": ("15minute", 180), "30m": ("30minute", 180), "60m": ("60minute", 360)}


def configure_logging(level: int = logging.INFO) -> None:
    """CLI-only logging setup; importing this module never touches the root logger."""
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")  # emoji-safe on legacy Windows code pages
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d | %(levelname)-8s | [%(name)s] | %(message)s",
        datefmt="%H:%M:%S",
    )


# ==============================================================================
# 1. EVENT DATA STRUCTURES
# ==============================================================================
@dataclass
class Tick:
    symbol: str
    price: float
    volume: int                                 # quantity traded since the previous tick
    timestamp: datetime                         # tz-aware IST
    cumulative_volume: Optional[int] = None     # day volume (Kite `volume_traded`); wins over `volume`
    received: Optional[datetime] = None         # receive time, when `timestamp` is the exchange's own

@dataclass
class Signal:
    symbol: str
    entry_price: float
    stop_loss: float
    target: float
    reason: str
    bar_time: Optional[datetime] = None         # start of the bar that triggered the signal

@dataclass
class OrderPlan:
    signal: Signal
    quantity: int
    entry_limit: float                          # marketable LIMIT: entry + slippage, tick-rounded
    stop_loss: float
    target: float

@dataclass
class Fill:
    symbol: str
    quantity: int
    average_price: float
    order_id: str
    exit_order_id: Optional[str] = None         # Kite GTT trigger id protecting the position

@dataclass
class Position:
    symbol: str
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    opened_at: datetime                         # first bar that can hit the stop or target
    stop_limit: Optional[float] = None          # SELL LIMIT placed when the stop triggers (as in the live GTT)
    stop_triggered: bool = False                # triggered, but the limit has not filled yet
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None           # "STOP" or "TARGET"
    closed_at: Optional[datetime] = None

    @property
    def pnl(self) -> Optional[float]:
        return None if self.exit_price is None else (self.exit_price - self.entry_price) * self.quantity


# ==============================================================================
# TIME, PRICE AND FRAME HELPERS
# ==============================================================================
def to_ist(ts: datetime) -> datetime:
    """Normalize any timestamp to tz-aware IST.

    Naive values are read as host-local time, which is what KiteTicker produces
    (it builds tick times with ``datetime.fromtimestamp``).
    """
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    return ts.astimezone(IST)


def bar_floor(ts: datetime, minutes: int = BAR_MINUTES) -> datetime:
    """Start of the ``minutes``-wide bar containing ``ts``. NSE bars are aligned to the 09:15 open,
    which matters for 30- and 60-minute bars (09:15, 09:45, ...)."""
    anchor = ts.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)
    width = timedelta(minutes=minutes)
    return anchor + ((ts - anchor) // width) * width


def next_ist_midnight(ts: datetime) -> datetime:
    """00:00 IST on the day after ``ts``: a lookback clamp snapped here can never start mid-session."""
    return datetime.combine(to_ist(ts).date() + timedelta(days=1), dtime(0), tzinfo=IST)


try:                                    # Linux: monotonic time that keeps counting while the host is suspended
    time.clock_gettime(time.CLOCK_BOOTTIME)

    def _uptime() -> float:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
except (AttributeError, OSError):
    _uptime = time.monotonic            # elsewhere a suspend is caught by the watchdog (every tick far ahead)


def steady_clock(wall: Optional[Callable[[], datetime]] = None, monotonic: Callable[[], float] = _uptime,
                 counts_suspend: Optional[bool] = None) -> Callable[[], datetime]:
    """The wall clock as read now, advanced by a monotonic clock from then on, so it never steps. A live
    feed stamps and judges everything with it: an NTP correction mid-session (a step of the host clock)
    then cannot shift feed time, liveness or signal ages. Any offset it started with is measured as
    clock skew, like any other. On Linux it keeps counting through a suspend (CLOCK_BOOTTIME)."""
    read_wall = wall or (lambda: datetime.now(IST))
    start, ticks = to_ist(read_wall()), monotonic()

    def now() -> datetime:
        return start + timedelta(seconds=monotonic() - ticks)

    # Where the monotonic clock stops during a suspend (not Linux), the wall clock's lead over this clock is
    # time the run did not see. The router adds it to signal ages, which can only refuse more signals.
    if counts_suspend is None:
        counts_suspend = monotonic is not time.monotonic
    now.wall_gain = (lambda: 0.0) if counts_suspend else \
        (lambda: max(0.0, (to_ist(read_wall()) - now()).total_seconds()))
    return now


def next_session_open(ts: datetime) -> datetime:
    """09:15 IST on the next weekday after ``ts`` (exchange holidays are not modelled)."""
    day = to_ist(ts).date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, SESSION_OPEN, tzinfo=IST)


def round_to_tick(price: float, tick: float, mode: str = "nearest") -> float:
    """Round ``price`` onto the exchange tick grid ('down', 'up' or 'nearest')."""
    steps = price / tick
    if mode == "down":
        n = math.floor(steps + 1e-9)
    elif mode == "up":
        n = math.ceil(steps - 1e-9)
    else:
        n = round(steps)
    return round(n * tick, 6)


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV},
                        index=pd.DatetimeIndex([], tz=IST, name="datetime"))


def harmonize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Shared cleaning for every data source.

    A bar exists only if it traded: rows without a Close are dropped rather than
    back-filled (a back-filled first bar would carry a *future* price). Partially
    missing Open/High/Low fall back to the Close; missing Volume is 0.
    """
    if df.empty:
        return empty_bars()
    raw = df[OHLCV]
    df = raw.apply(pd.to_numeric, errors="coerce").astype("float64")
    coerced = int((df.isna() & raw.notna()).sum().sum())
    if coerced:
        logger.warning(f"{coerced} OHLCV values were not numbers (e.g. '1,234,567') and were treated as missing.")
    df = df[df["Close"].notna()].copy()
    for col in ["Open", "High", "Low"]:
        df[col] = df[col].fillna(df["Close"])
    df["Volume"] = df["Volume"].fillna(0.0)
    df.index = df.index.tz_localize(IST) if df.index.tz is None else df.index.tz_convert(IST)
    df.index.name = "datetime"
    return df[~df.index.duplicated(keep="last")].sort_index()


def drop_incomplete_bars(df: pd.DataFrame, as_of: datetime, minutes: int) -> pd.DataFrame:
    """Drop bars still forming at ``as_of``; the live synthesizer owns those buckets.

    A bar ends ``minutes`` after it starts, or at the 15:30 close if that comes first: the
    375-minute session is not a multiple of 30 or 60, so the 15:15 bar of a 09:15-anchored
    30m/60m series is complete at 15:30, not at 15:45/16:15.
    """
    close = df.index.normalize() + pd.Timedelta(hours=SESSION_CLOSE.hour, minutes=SESSION_CLOSE.minute)
    end = df.index + pd.Timedelta(minutes=minutes)
    end = end.where((end <= close) | (df.index >= close), close)   # a special session's bars keep their width
    return df[end <= pd.Timestamp(as_of)]


# ==============================================================================
# 2. DATA HARMONIZATION & BROKER ADAPTERS
# ==============================================================================
def is_permanent_error(e: BaseException) -> bool:
    """Errors a retry cannot fix: HTTP 4xx other than 429, and Kite auth errors.

    The SDK builds every API error as ``ErrorType(message, code=<HTTP status>)``, and the type
    (from the body) and the status are independent: Kite reports MarginException,
    HoldingException and the like as a GeneralException with a 400, and an OrderException can
    carry a 5xx. So kiteconnect errors are classified by their status, not their name.
    """
    if isinstance(e, urllib.error.HTTPError):
        return 400 <= e.code < 500 and e.code != 429
    if type(e).__name__ in ("TokenException", "PermissionException"):
        return True
    code = getattr(e, "code", None)
    if type(e).__module__.startswith("kiteconnect") and isinstance(code, int):
        return 400 <= code < 500 and code != 429
    return False

def _read_url(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()

# Single-family echo services: a dual-stack one would report the IPv6 address of an IPv4-whitelisted host.
IP_ECHO_PROVIDERS = {
    4: ("https://api.ipify.org", "https://v4.ident.me", "https://ipv4.icanhazip.com"),
    6: ("https://api6.ipify.org", "https://v6.ident.me", "https://ipv6.icanhazip.com"),
}

async def verify_hardware_ip(expected_static_ip: str, min_agreeing: int = 2) -> bool:
    """True only if the public egress IP is the address whitelisted with the broker.

    All providers of the expected address family are asked concurrently. Any valid answer that
    differs fails the check; otherwise at least ``min_agreeing`` providers must confirm it.
    Unreachable providers and non-IP bodies (captive portals, error pages) are ignored.
    """
    logger.info("Executing hardware IP verification...")
    try:
        expected = ipaddress.ip_address(expected_static_ip.strip())
    except ValueError:
        logger.critical(f"FATAL: {expected_static_ip!r} is not an IP address.")
        return False
    providers = IP_ECHO_PROVIDERS[expected.version]

    async def ask(provider: str):
        req = urllib.request.Request(provider, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            answer = ipaddress.ip_address((await asyncio.to_thread(_read_url, req, 5)).decode('utf-8', 'replace').strip())
        except Exception as e:
            logger.warning(f"IP provider {provider} gave no usable answer: {e!r}")
            return None
        if answer.version != expected.version:
            logger.warning(f"IP provider {provider} answered with IPv{answer.version} {answer}; ignored.")
            return None
        return answer

    answers = await asyncio.gather(*(ask(p) for p in providers))
    valid = {p: a for p, a in zip(providers, answers, strict=True) if a is not None}
    mismatched = {p: a for p, a in valid.items() if a != expected}
    if mismatched:
        seen = ", ".join(f"{a} via {p}" for p, a in mismatched.items())
        logger.critical(f"FATAL: IP Mismatch. Authorized: {expected}, observed: {seen}.")
        return False
    if len(valid) < min_agreeing:
        logger.critical(f"FATAL: only {len(valid)} of {len(providers)} IP verifiers answered; {min_agreeing} must agree.")
        return False
    logger.info(f"IP Verification Confirmed by {len(valid)} providers. IP: {expected}")
    return True

class TokenBucketRateLimiter:
    """Sliding-window limiter: at most ``max_calls`` acquisitions in any ``period`` seconds.

    Callers are served in arrival order: asyncio.Lock is FIFO, and each caller waits for its
    reserved start while still holding it, so a later arrival can neither take an earlier
    slot nor wake first. Reserved starts never decrease, so this costs no throughput.
    """
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls: List[float] = []    # reserved start times, non-decreasing
        self._lock = asyncio.Lock()

    async def wait_for_capacity(self):
        async with self._lock:
            now = time.monotonic()
            self.calls = [t for t in self.calls if t > now - self.period]
            start = max([now] + self.calls[-1:])
            if len(self.calls) >= self.max_calls:
                start = max(start, self.calls[-self.max_calls] + self.period)
            self.calls.append(start)
            if start > now:
                await asyncio.sleep(start - now)

class BrokerAdapter(ABC):
    history_limit_days: Optional[int] = None    # how many days back the source serves 5m bars (None: no limit)

    def history_cutoff(self, symbol: Optional[str] = None) -> Optional[datetime]:
        """00:00 IST of the oldest day this source can serve 5m bars for, or None if it is not limited."""
        if self.history_limit_days is None:
            return None
        return next_ist_midnight(datetime.now(IST) - timedelta(days=self.history_limit_days))

    async def boot(self) -> None:
        """Optional start-up hook (instrument downloads, logins); the default does nothing."""
        return None

    def _empty_map(self) -> pd.DataFrame:
        return empty_bars()

    def symbol_for_token(self, token: int) -> Optional[str]:
        """Reverse instrument lookup for websocket ticks (brokers that use numeric tokens)."""
        return None

    def token_map(self) -> Dict[int, str]:
        return {}

    def tick_size(self, symbol: str) -> float:
        return 0.05

    @abstractmethod
    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str) -> pd.DataFrame:
        pass

    async def backfill(self, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """5m bars for a hole in a live feed. Must raise when the fetch fails: an empty frame means
        nothing traded in the hole, and the bar after it is then evaluated."""
        raise NotImplementedError(f"{type(self).__name__} cannot back-fill a live feed")

class ZerodhaKiteAdapter(BrokerAdapter):
    """Real Money Zerodha Setup (Asyncio-safe)"""
    def __init__(self, api_key: str, access_token: str, exchange: str = "NSE", kite=None):
        if kite is None:
            try:
                from kiteconnect import KiteConnect
            except ImportError as err:
                raise ImportError("FATAL: kiteconnect SDK not installed. Run 'pip install kiteconnect'.") from err
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
        self.kite = kite
        self.exchange = exchange
        self.limiter = TokenBucketRateLimiter(max_calls=3, period=1.1)  # Kite historical API: 3 req/s
        self._instrument_cache: Dict[str, int] = {}
        self._token_to_symbol: Dict[int, str] = {}
        self._tick_sizes: Dict[str, float] = {}

    async def boot(self):
        logger.info(f"Downloading master instrument mapping from {self.exchange}...")
        instruments = await asyncio.to_thread(self.kite.instruments, self.exchange)
        for inst in instruments:
            sym, token = inst['tradingsymbol'], int(inst['instrument_token'])
            self._instrument_cache[sym] = token
            self._token_to_symbol[token] = sym
            if inst.get('tick_size'):
                self._tick_sizes[sym] = float(inst['tick_size'])
        logger.info(f"Instrument mapping sealed. {len(self._instrument_cache)} {self.exchange} instruments registered.")

    def symbol_for_token(self, token: int) -> Optional[str]:
        return self._token_to_symbol.get(int(token))

    def token_map(self) -> Dict[int, str]:
        return dict(self._token_to_symbol)

    def tick_size(self, symbol: str) -> float:
        return self._tick_sizes.get(symbol, 0.05)

    async def backfill(self, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        return await self.fetch_historical_bars(symbol, start_date, end_date, "5m", strict=True)

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m",
                                    strict: bool = False) -> pd.DataFrame:
        """Bars in [start_date, end_date). With ``strict`` a failure raises instead of returning an
        empty frame, so a back-fill can tell 'no trades in the hole' from 'the request failed'."""
        token = self._instrument_cache.get(symbol)
        if not token:
            logger.error(f"[{symbol}] Unknown {self.exchange} tradingsymbol (did boot() run?).")
            if strict:
                raise LookupError(f"unknown {self.exchange} tradingsymbol {symbol}")
            return self._empty_map()

        if interval not in KITE_INTERVALS:
            raise ValueError(f"Unsupported interval {interval!r}; expected one of {sorted(KITE_INTERVALS)}")
        kite_interval, max_days = KITE_INTERVALS[interval]
        current_start, chunks = start_date, []

        while current_start < end_date:
            chunk_end = min(current_start + timedelta(days=max_days), end_date)
            from_ist = current_start.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')
            to_ist_str = chunk_end.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')

            for attempt in range(3):
                await self.limiter.wait_for_capacity()
                try:
                    records = await asyncio.to_thread(
                        self.kite.historical_data, instrument_token=token, from_date=from_ist, to_date=to_ist_str, interval=kite_interval
                    )
                    chunks.extend(records or [])
                    break
                except Exception as e:
                    logger.warning(f"[{symbol}] Kite history {from_ist}->{to_ist_str} attempt {attempt + 1}/3 failed: {e!r}")
                    if attempt == 2 or is_permanent_error(e):
                        # Fail closed: a hole in history would silently shift the base and AVWAP.
                        logger.error(f"[{symbol}] Giving up on Kite history; discarding {len(chunks)} partial bars.")
                        if strict:
                            raise
                        return self._empty_map()
                    await asyncio.sleep(2 ** attempt)
            current_start = chunk_end + timedelta(seconds=1)

        if not chunks: return self._empty_map()
        df = pd.DataFrame(chunks)
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop('date')))
        df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
        return drop_incomplete_bars(harmonize_bars(df), end_date, INTERVAL_MINUTES.get(interval, BAR_MINUTES))

class PublicExchangeAdapter(BrokerAdapter):
    """Fetches real market prints via Yahoo Finance to bypass broker SDK limits for testing."""
    history_limit_days = 59            # Yahoo serves 5m bars for ~60 days

    def __init__(self, suffix: str = ".NS"):
        self.suffix = suffix
        self.limiter = TokenBucketRateLimiter(max_calls=3, period=1.1)

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        logger.info(f"[{symbol}] Fetching public exchange prints via direct HTTP...")
        # Snapped to midnight so the window never starts mid-session.
        cutoff = self.history_cutoff(symbol)
        if start_date < cutoff:
            logger.info(f"[{symbol}] Yahoo intraday history is limited; clamping start to {cutoff:%Y-%m-%d}.")
            start_date = cutoff

        query = urllib.parse.urlencode({"interval": interval, "period1": int(start_date.timestamp()),
                                        "period2": int(end_date.timestamp()), "includePrePost": "false"})
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol + self.suffix)}?{query}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        req = urllib.request.Request(url, headers=headers)

        data = None
        for attempt in range(3):
            await self.limiter.wait_for_capacity()
            try:
                data = json.loads(await asyncio.to_thread(_read_url, req, 10))
                break
            except Exception as e:
                logger.warning(f"[{symbol}] Yahoo request attempt {attempt + 1}/3 failed: {e!r}")
                if attempt == 2 or is_permanent_error(e):
                    logger.error(f"[{symbol}] Yahoo Finance unreachable; no history acquired. "
                                 f"Try '--source csv' for the bundled offline data.")
                    return self._empty_map()
                await asyncio.sleep(2 ** attempt)

        chart = data.get('chart') if isinstance(data, dict) else None
        results = chart.get('result') if isinstance(chart, dict) else None
        res = results[0] if isinstance(results, list) and results else None
        if not isinstance(res, dict):
            logger.error(f"[{symbol}] Yahoo returned no chart data: {chart.get('error') if isinstance(chart, dict) else data!r:.200}")
            return self._empty_map()
        stamps = res.get('timestamp')
        indicators = res.get('indicators')
        quotes = indicators.get('quote') if isinstance(indicators, dict) else None
        quote = quotes[0] if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict) else {}
        if not isinstance(stamps, list) or not stamps or not quote:
            logger.error(f"[{symbol}] Yahoo chart has no bars in the requested window.")
            return self._empty_map()
        # A stamp that is not a plausible epoch second cannot place its bar; its row is dropped below.
        epoch = pd.to_numeric(pd.Series(stamps, dtype="object"), errors="coerce")
        epoch = epoch.where((epoch > 0) & (epoch < 2 ** 32))
        columns = {}
        for col in OHLCV:
            values = quote.get(col.lower())
            if not isinstance(values, list) or len(values) != len(stamps):
                logger.warning(f"[{symbol}] Yahoo '{col.lower()}' series is missing or misaligned; treated as empty.")
                values = [None] * len(stamps)
            columns[col] = values
        df = pd.DataFrame(columns, index=pd.DatetimeIndex(pd.to_datetime(epoch, unit='s', utc=True)))
        if df.index.hasnans:
            logger.warning(f"[{symbol}] {int(df.index.isna().sum())} Yahoo timestamps were not epoch seconds; those rows were dropped.")
            df = df[df.index.notna()]
        minutes = INTERVAL_MINUTES.get(interval, BAR_MINUTES)
        df = harmonize_bars(df)
        # Yahoo can append an off-grid "live" row (e.g. 15:29:59) and pre-open prints; neither is a bar.
        # The grid is anchored at the 09:15 open (30-minute bars start 09:15, 09:45, ...).
        t = df.index
        since_open = (t.hour * 60 + t.minute) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)
        on_grid = (t.second == 0) & (since_open % minutes == 0) & (t.time >= SESSION_OPEN) & (t.time < SESSION_CLOSE)
        return drop_incomplete_bars(df[on_grid], end_date, minutes)

class CsvReplayAdapter(BrokerAdapter):
    """Offline source: real bars from CSV files (datetime_ist, open, high, low, close, volume).

    ``datetime_ist`` is the bar start as naive IST wall-clock time (see data/README.md).
    """
    def __init__(self, files: Dict[str, Path]):
        self.files = {sym: Path(p) for sym, p in files.items()}
        self.first_bar: Dict[str, datetime] = {}

    def history_cutoff(self, symbol: Optional[str] = None) -> Optional[datetime]:
        """A file is the source's whole history: nothing exists before the day of its first bar."""
        first = self.first_bar.get(symbol)
        return None if first is None else datetime.combine(to_ist(first).date(), dtime(0), tzinfo=IST)

    @staticmethod
    def load(path: Path) -> pd.DataFrame:
        raw = pd.read_csv(path)
        df = raw.rename(columns={c: c.capitalize() for c in ["open", "high", "low", "close", "volume"]})
        idx = pd.DatetimeIndex(pd.to_datetime(raw["datetime_ist"]))
        df.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)   # offsets allowed
        return harmonize_bars(df)

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        path = self.files.get(symbol)
        if path is None or not path.exists():
            logger.error(f"[{symbol}] No CSV file for symbol (looked for {path}).")
            return self._empty_map()
        if path.stem.split("_")[0].upper() != symbol.upper():             # files are named SYMBOL_interval_from_to
            logger.warning(f"[{symbol}] {path.name} does not look like {symbol} data; replaying it as {symbol} anyway.")
        df = await asyncio.to_thread(self.load, path)
        if len(df):
            self.first_bar[symbol] = df.index[0]
        window = df[(df.index >= pd.Timestamp(start_date)) & (df.index < pd.Timestamp(end_date))]
        logger.info(f"[{symbol}] Replaying {len(window)} bars from {path.name}.")
        return window

class ProductionOrchestrator:
    """Loads each IPO's history from its listing date.

    The base and AVWAP are anchored to the first bar, so the first bar must come from the
    listing session itself. A symbol whose history starts later (vendor limits such as
    Yahoo's ~60 days, or a listing older than ``max_lookback_days``) has no IPO base and
    is excluded unless ``allow_partial_history`` is set. A listing date of ``None`` means
    demo semantics: the first bar fetched is treated as the listing.
    """
    def __init__(self, target_ipos: Dict[str, Optional[datetime]], broker: BrokerAdapter,
                 as_of: Optional[datetime] = None, max_lookback_days: int = 180, allow_partial_history: bool = False,
                 demo_lookback_days: int = 20):
        # A listing is a calendar date: 00:00 IST on that date, whatever time or zone it came with.
        self.watchlist = {s: None if d is None else datetime.combine(d.date(), dtime(0), tzinfo=IST)
                          for s, d in target_ipos.items()}
        self.broker = broker
        self.as_of = as_of
        self.max_lookback_days = max_lookback_days
        self.allow_partial_history = allow_partial_history
        self.demo_lookback_days = demo_lookback_days
        self.market_state: Dict[str, pd.DataFrame] = {}

    async def build_the_ground(self) -> bool:
        try:
            await self.broker.boot()
        except Exception as e:
            logger.critical(f"Broker start-up failed: {e!r}")
            return False
        end_date = self.as_of or datetime.now(timezone.utc)

        # A lookback longer than any listing can matter would overflow the calendar; cap it at 100 years.
        lookback = timedelta(days=min(self.max_lookback_days, MAX_LOOKBACK_DAYS))
        history_cutoff = getattr(self.broker, "history_cutoff", None)
        for symbol, listing_date in self.watchlist.items():
            if listing_date is None:
                start_date = next_ist_midnight(end_date - min(timedelta(days=self.demo_lookback_days), lookback))
            else:
                start_date = max(listing_date, next_ist_midnight(end_date - lookback))
            try:
                df = await self.broker.fetch_historical_bars(symbol, start_date, end_date, "5m")
            except Exception:
                logger.exception(f"[{symbol}] History fetch failed; symbol excluded.")
                continue
            if df.empty:
                logger.error(f"[{symbol}] No historical bars acquired; symbol excluded.")
                continue
            if listing_date is None:
                logger.warning(f"[{symbol}] No listing date given: treating the first bar ({df.index[0]:%Y-%m-%d %H:%M}) "
                               f"as the listing, so the base and AVWAP are anchored there (demo semantics).")
            elif start_date > listing_date or df.index[0].date() != listing_date.date():
                cutoff = history_cutoff(symbol) if callable(history_cutoff) else None
                vendor_cut = cutoff is not None and cutoff > listing_date
                if start_date > listing_date and not vendor_cut:
                    msg = (f"[{symbol}] The {self.max_lookback_days}-day lookback starts {start_date:%Y-%m-%d}, after "
                           f"the {listing_date:%Y-%m-%d} listing, so the IPO base and AVWAP anchor are unknown")
                    remedy = "--max-lookback-days to reach the listing, or --allow-partial-history to anchor at the first bar"
                elif start_date > listing_date and start_date > cutoff:
                    # Both cut it, the lookback later: more lookback helps, but only up to the source's window.
                    reach = (to_ist(end_date).date() - cutoff.date()).days + 1
                    msg = (f"[{symbol}] The {self.max_lookback_days}-day lookback starts {start_date:%Y-%m-%d} and the "
                           f"source's history {cutoff:%Y-%m-%d}, both after the {listing_date:%Y-%m-%d} listing, so the "
                           f"IPO base and AVWAP anchor are unknown")
                    remedy = (f"--allow-partial-history to anchor at the first bar; --max-lookback-days {reach} "
                              f"starts at the source's earliest day")
                else:
                    gap = (df.index[0].date() - listing_date.date()).days
                    msg = (f"[{symbol}] History starts {df.index[0]:%Y-%m-%d}, {gap} days after the "
                           f"{listing_date:%Y-%m-%d} listing, so the IPO base and AVWAP anchor are unknown")
                    remedy = "--allow-partial-history to anchor at the first bar"
                if not self.allow_partial_history:
                    logger.error(f"{msg}; symbol excluded ({remedy}).")
                    continue
                logger.warning(f"{msg}; anchoring at the first available bar as requested.")
            self.market_state[symbol] = df
            logger.info(f"[{symbol}] Map established. {len(df)} concrete 5m bars acquired "
                        f"({df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M} IST).")
        return bool(self.market_state)


# ==============================================================================
# 3. ALPHA ENGINE & IN-MEMORY SYNTHESIZER
# ==============================================================================
class AlphaEngine:
    """IPO base breakout: a close above the IPO base high after a close at or below it
    (a crossing; a later re-cross counts again), above the listing-anchored VWAP, on
    high relative volume.

    The base is the first ``base_bars`` bars since listing (v1.0's definition), or, with
    ``base_sessions``, every bar of the first N sessions. Every indicator is causal: the
    value on bar *t* uses bars <= *t* only, and no breakout counts until the base is done.
    """
    RVOL_MODES = ("trailing", "time_of_day")

    def __init__(self, rvol_threshold: float = 2.0, risk_reward_ratio: float = 3.0, base_bars: int = 150,
                 rvol_lookback: int = 20, atr_period: int = 14, atr_stop_multiple: float = 1.5,
                 rvol_mode: str = "trailing", rvol_sessions: int = 10, base_sessions: Optional[int] = None):
        if rvol_mode not in self.RVOL_MODES:
            raise ValueError(f"rvol_mode must be one of {self.RVOL_MODES}")
        self.rvol_threshold = rvol_threshold
        self.rr_ratio = risk_reward_ratio
        self.base_bars = base_bars
        self.base_sessions = base_sessions      # listing day opens at 10:00, so 150 bars reach into session 3
        self.rvol_lookback = rvol_lookback
        self.rvol_mode = rvol_mode              # time_of_day: vs the same 5-minute slot of prior sessions
        self.rvol_sessions = rvol_sessions
        self.atr_period = atr_period
        self.atr_stop_multiple = atr_stop_multiple

    def base_length(self, df: pd.DataFrame) -> int:
        """Number of leading rows that form the IPO base."""
        if self.base_sessions:
            days = df.index.normalize()
            return int(days.isin(days.unique()[:self.base_sessions]).sum())
        return self.base_bars

    def base_high(self, df: pd.DataFrame) -> float:
        return float(df['High'].iloc[:self.base_length(df)].max())

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df[OHLCV].copy()
        typical = (df['High'] + df['Low'] + df['Close']) / 3.0
        cum_vol = df['Volume'].cumsum()
        df['AVWAP'] = (typical * df['Volume']).cumsum() / cum_vol.where(cum_vol > 0)
        # Relative volume against *preceding* bars only; the current bar is not in its own baseline.
        # A bar exists only if it traded, so a zero-volume bar is a vendor gap or a no-trade bucket,
        # never a quiet bar: it is left out of the baseline.
        vol = df['Volume'].where(df['Volume'] > 0)
        min_obs = max(1, math.ceil(0.75 * self.rvol_lookback))
        vol_base = vol.rolling(self.rvol_lookback, min_periods=min_obs).mean().shift(1)
        if self.rvol_mode == "time_of_day":
            # Opening and closing bars are structurally heavy; compare each bar with its own slot.
            slot = (df.index.hour * 60 + df.index.minute).to_numpy()   # integer key: ~6x faster than strftime
            slot_base = vol.groupby(slot).transform(
                lambda s: s.shift(1).rolling(self.rvol_sessions, min_periods=2).mean())
            vol_base = slot_base.fillna(vol_base)   # young listings: trailing until slot history exists
        df['RVOL'] = df['Volume'] / vol_base.where(vol_base > 0)
        prev_close = df['Close'].shift(1)
        true_range = pd.concat([df['High'] - df['Low'], (df['High'] - prev_close).abs(),
                                (df['Low'] - prev_close).abs()], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(self.atr_period, min_periods=1).mean()
        # The base high exists only once the base is complete; rows inside the base get NaN, so no
        # row ever sees a High from its future.
        base_len = self.base_length(df)
        df['Base_High'] = float('nan')
        if len(df) > base_len:
            df.iloc[base_len:, df.columns.get_loc('Base_High')] = df['High'].iloc[:base_len].max()
        breakout = ((prev_close <= df['Base_High']) & (df['Close'] > df['Base_High'])
                    & (df['Close'] > df['AVWAP']) & (df['RVOL'] > self.rvol_threshold))
        breakout.iloc[:max(base_len, self.rvol_lookback)] = False   # same warm-up as evaluate()
        df['Breakout'] = breakout
        return df

    def _signal(self, symbol: str, bar: pd.Series, bar_time: datetime) -> Signal:
        close = float(bar['Close'])
        # Risk is bounded by 1.5 ATR, or the AVWAP structural floor, whichever is closer.
        stop_loss = max(close - self.atr_stop_multiple * float(bar['ATR']), float(bar['AVWAP']))
        target = close + (close - stop_loss) * self.rr_ratio
        return Signal(symbol, close, stop_loss, target, "IPO_BASE_BREAKOUT", bar_time)

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) <= max(self.base_length(df), self.rvol_lookback): return None

        ind = self.indicators(df)
        latest = ind.iloc[-1]
        if not bool(latest['Breakout']):
            return None
        logger.info(f"[{symbol}] 🟢 ALPHA TRIGGER: Base Breakout @ {latest['Close']:.2f} "
                    f"(base {latest['Base_High']:.2f}) | RVOL: {latest['RVOL']:.2f}x | AVWAP: {latest['AVWAP']:.2f}")
        return self._signal(symbol, latest, ind.index[-1])

    def scan(self, symbol: str, df: pd.DataFrame) -> List[Signal]:
        """Every bar on which evaluate() would have fired, in one vectorized pass (walk-forward)."""
        if len(df) <= max(self.base_length(df), self.rvol_lookback): return []
        ind = self.indicators(df)
        return [self._signal(symbol, row, ts) for ts, row in ind[ind['Breakout']].iterrows()]

class LiveTickAdapter:
    """Turns broker ticks into completed 5-minute bars and evaluates each one.

    Threading: ``broker_on_ticks`` runs on the broker's websocket thread and only
    enqueues; all bar state is owned by the event loop (``process_ticks``, ``bar_clock``
    and back-fill tasks), so no locks are needed.

    Volume integrity: Kite reports cumulative day volume, and a bar gets the difference
    between prints. Whenever the feed was not watching (start-up, a reconnect), the counter
    holds trades we never saw, so it is re-baselined and the bar that spans the blind spot
    is discarded. The hole it leaves is back-filled from the broker's history before the
    next bar is evaluated.
    """
    max_stamp_ahead = 30.0          # seconds an exchange stamp may lead the host clock before it is not believed

    def __init__(self, market_state: Dict[str, pd.DataFrame], alpha_engine: AlphaEngine, oms_queue: asyncio.Queue,
                 loop: asyncio.AbstractEventLoop, token_map: Optional[Dict[int, str]] = None,
                 bar_minutes: int = BAR_MINUTES, started_at: Optional[datetime] = None,
                 bar_listeners: Optional[List[Callable[[str, datetime, pd.Series], None]]] = None,
                 backfill: Optional[Callable[[str, datetime, datetime], Awaitable[pd.DataFrame]]] = None,
                 require_feed_liveness: bool = False, clock: Optional[Callable[[], datetime]] = None):
        self.market_state = market_state
        # The host clock for receive stamps and bar closing (main() gives a live feed a steady_clock).
        self.clock = clock or (lambda: datetime.now(IST))
        self.alpha = alpha_engine
        self.oms_queue = oms_queue
        self.loop = loop
        self.token_map = dict(token_map or {})
        self.bar_minutes = bar_minutes
        self.started_at = to_ist(started_at or self.clock())
        self.tick_queue = asyncio.Queue()
        self.current_bars: Dict[str, dict] = {}
        self.last_closed: Dict[str, datetime] = {}
        self._cum_volume: Dict[str, Tuple[object, int, int]] = {}   # symbol -> (day, counter, feed epoch)
        self._feed_epoch = 0
        self._feed_since = self.started_at                           # watching continuously since
        self.dropped_ticks = 0
        self.bar_listeners = list(bar_listeners or [])
        self.backfill = backfill
        self._backfills: set = set()
        self._unanchored: set = set()
        self.market_time: Optional[datetime] = None                  # latest accepted tick time
        # Live feeds only: a bar is complete only if the feed provably delivered something (Kite sends a
        # heartbeat every second) after the bar's bucket ended; otherwise its tail may be missing.
        self.require_feed_liveness = require_feed_liveness
        self.last_alive: Optional[datetime] = None
        # Receive time minus exchange time, the largest seen in the last minute: bars are closed and
        # liveness judged in feed time, so feed latency or a host clock running fast cannot cut a bar's
        # tail. Negative when the host clock runs behind the exchange (then no sample is ever positive).
        # clock_skew is the robust estimate of that host offset alone (see there).
        self.feed_lag = 0.0
        self._lag_samples: collections.deque = collections.deque()
        self._lag_warned: Optional[datetime] = None
        self._ahead_warned: Optional[datetime] = None
        self._ahead_run: Optional[Tuple[datetime, datetime]] = None  # first and last far-ahead drop in a row
        self._ahead_last: Optional[datetime] = None                  # the latest drop just over the limit
        self._last_update: Dict[str, Tuple[int, datetime]] = {}      # symbol -> (feed epoch, newest exchange time)
        self._glitch_warned: set = set()                             # (symbol, day) already reported
        # A bucket in which the volume counter was re-baselined lost its head to the baseline.
        self._blind_bucket: Dict[str, datetime] = {}

    def _normalize(self, t: dict) -> Optional[Tick]:
        if 'instrument_token' in t:
            symbol = self.token_map.get(t['instrument_token'])
        else:
            symbol = t.get('symbol', t.get('tradingsymbol'))
        price = t.get('last_price', t.get('price'))
        if symbol is None or price is None or price <= 0:
            return None
        # Bucket by exchange time. last_trade_time is not used: for a quiet name it can be minutes old.
        # A zeroed exchange field parses as 1970; fall back to receive time rather than trust it.
        ts = t.get('exchange_timestamp') or t.get('timestamp')
        ts = to_ist(ts) if ts is not None else None
        if ts is None or ts.year < 2000:
            # Feed time, as bars are closed, with the bar clock's own 2 s grace: such a print cannot close a bar
            # before the bar clock would, even if latency has just risen (it errs early: a late one is dropped and
            # its shares carry forward). A float read is thread-safe.
            ts = self.clock() - timedelta(seconds=self.feed_lag) - BAR_CLOSE_GRACE
        cumulative = t.get('volume_traded')
        volume = t.get('last_traded_quantity', t.get('volume', 0)) if cumulative is None else 0
        return Tick(symbol=symbol, price=float(price), volume=int(volume or 0), timestamp=ts,
                    cumulative_volume=None if cumulative is None else int(cumulative))

    def broker_on_ticks(self, ws, ticks: list):
        """Websocket Callback. Accepts Kite payloads (instrument_token, last_price, volume_traded,
        exchange_timestamp) and simple dicts (symbol, price, volume, timestamp)."""
        received = self.clock()
        try:
            for t in ticks:
                tick = self._normalize(t)
                if tick is None:
                    self.dropped_ticks += 1
                    continue
                raw = t.get('exchange_timestamp')
                if getattr(raw, "year", 0) >= 2000:
                    tick.received = received      # the exchange's own time: lets on_tick measure the lag
                # Thread-safe dispatch from the broker's C-Thread to our Async Event Loop
                self.loop.call_soon_threadsafe(self.tick_queue.put_nowait, tick)
        finally:
            # Proof of life only after the payload's own ticks are queued, so it cannot vouch for its tail.
            self.loop.call_soon_threadsafe(self.note_feed_alive, received)

    def note_feed_alive(self, at: Optional[datetime] = None) -> None:
        """Any message from the broker, heartbeats included, stamped with its receive time.
        Must run on the event loop."""
        self.last_alive = to_ist(at or self.clock())

    @property
    def blind_for(self) -> float:
        """Seconds for which every exchange-stamped tick has been dropped as far ahead (0 if none is)."""
        return (self._ahead_run[1] - self._ahead_run[0]).total_seconds() if self._ahead_run else 0.0

    @property
    def clock_skew(self) -> float:
        """How far the host clock runs behind the exchange, in seconds (<= 0): the upper median of the
        last minute's lag samples. Each sample is latency minus that offset. One late packet cannot move
        it once the minute holds at least two other samples (with fewer, it follows the later one), and
        being the upper median, a forward-stamped packet never can. A host that does not run behind reads
        0. Latency is not part of it: a late signal really is older."""
        samples = sorted(sample for _, sample in self._lag_samples)
        return min(0.0, samples[len(samples) // 2]) if samples else 0.0

    def note_feed_lag(self, at: datetime, lag: float, window: timedelta = timedelta(seconds=60),
                      grace: timedelta = timedelta(seconds=2)) -> None:
        """Record how far receive time ran ahead of exchange time. Must run on the event loop."""
        at = to_ist(at)
        self._lag_samples.append((at, lag))
        while self._lag_samples and self._lag_samples[0][0] < at - window:
            self._lag_samples.popleft()
        self.feed_lag = max(sample for _, sample in self._lag_samples)
        if self._lag_warned is not None and at - self._lag_warned < window:
            return
        lowest = min(sample for _, sample in self._lag_samples)
        skew = self.clock_skew if lowest < -grace.total_seconds() else 0.0     # the median is never below it
        if skew < -grace.total_seconds():
            self._lag_warned = at
            logger.warning(f"Host clock runs {-skew:.1f}s behind the exchange; bars and signal ages are corrected "
                           f"for it. Check the host's time sync.")
        elif self.feed_lag > grace.total_seconds():
            self._lag_warned = at
            logger.warning(f"Feed runs {self.feed_lag:.1f}s behind the host clock (latency or clock skew); "
                           f"bars are closed that much later.")

    def mark_feed_down(self) -> None:
        """The broker feed dropped. Every forming bar misses its tail, and nothing opened before the
        next (re)connect can be complete. Must run on the event loop."""
        self._feed_since = datetime.max.replace(tzinfo=IST)
        for bar in self.current_bars.values():
            bar['partial'] = True

    def mark_feed_reset(self, at: Optional[datetime] = None) -> None:
        """The broker feed (re)connected. Every counter must be re-baselined, and bars that were
        forming while the feed was down are incomplete. Must run on the event loop."""
        self._feed_epoch += 1
        self._feed_since = to_ist(at or self.clock())
        self.last_alive = self._feed_since
        for bar in self.current_bars.values():
            bar['partial'] = True

    def _traded_quantity(self, tick: Tick) -> Tuple[int, bool]:
        """(quantity to add to the bar, whether the cumulative counter was re-baselined).

        The day's counter never legitimately goes down, so the highest value seen today is a
        high-water mark: a print below it (a zeroed or stale packet) is a glitch that is never
        adopted as the baseline, in any feed epoch, however many arrive in a row.
        """
        if tick.cumulative_volume is None:
            return max(0, tick.volume), False
        day, cum = tick.timestamp.date(), tick.cumulative_volume
        prev = self._cum_volume.get(tick.symbol)
        same_day = prev is not None and prev[0] == day
        if same_day and cum < prev[1]:
            if (tick.symbol, day) not in self._glitch_warned:
                self._glitch_warned.add((tick.symbol, day))
                logger.warning(f"[{tick.symbol}] Volume counter went backwards ({cum:,} < {prev[1]:,}): treated as a "
                               f"glitch. Prints stay uncounted until the counter passes {prev[1]:,}.")
            return 0, True
        self._cum_volume[tick.symbol] = (day, cum, self._feed_epoch)
        if same_day and prev[2] == self._feed_epoch:
            return cum - prev[1], False
        # First print of the session, or the first since the feed (re)connected or stalled. The counter then
        # holds trades we did not see; they belong to this bar only if we have been watching since the open.
        if not same_day and self._watching_since(tick) <= datetime.combine(day, SESSION_OPEN, tzinfo=IST):
            return cum, False
        # The new baseline also swallowed whatever traded earlier in this print's bucket: that bar is blind.
        self._blind_bucket[tick.symbol] = bar_floor(tick.timestamp, self.bar_minutes)
        if cum == 0:
            # A zeroed packet is no baseline (the day has traded): the next print re-baselines again.
            self._cum_volume[tick.symbol] = (day, 0, -1)
        return 0, True

    def _watching_since(self, tick: Optional[Tick] = None) -> datetime:
        """When the feed started watching, in exchange time: the host stamp moved forward by how far the
        host runs behind (the tick's own lag counts too: the day's first print has no samples before it).
        A host running ahead is not corrected, so the answer errs late, which errs blind."""
        if self._feed_since == datetime.max.replace(tzinfo=IST):
            return self._feed_since                                  # the feed is down
        skew = self.clock_skew
        if tick is not None and tick.received is not None:
            skew = min(skew, (tick.received - tick.timestamp).total_seconds())
        return self._feed_since - timedelta(seconds=skew)

    def _open_bar(self, sym: str, bucket: datetime, price: float, volume: int, tick: Optional[Tick] = None) -> None:
        # A bucket that began before we were watching, or whose head went into a re-baselined counter,
        # has an unknown open and volume.
        partial = self._watching_since(tick) > bucket or self._blind_bucket.get(sym) == bucket
        self.current_bars[sym] = {'timestamp': bucket, 'Open': price, 'High': price, 'Low': price,
                                  'Close': price, 'Volume': volume, 'partial': partial}

    def on_tick(self, tick: Tick) -> None:
        sym = tick.symbol
        if sym not in self.market_state:
            # No history means no IPO base or AVWAP anchor: trading its live bars later would be
            # trading a made-up base, so the symbol is ignored (fail closed, like the orchestrator).
            self.dropped_ticks += 1
            if sym not in self._unanchored:
                self._unanchored.add(sym)
                logger.warning(f"[{sym}] Ticks ignored: no established history for this symbol.")
            return
        if tick.received is not None and (ahead := (tick.timestamp - tick.received).total_seconds()) > self.max_stamp_ahead:
            # The exchange cannot stamp a print in our future: a corrupt stamp, or a host clock so far behind
            # that nothing measured against it can be trusted. The print is dropped before it moves the lag
            # or the market time, and like a stall it blinds the symbol: the bar it belonged to is discarded
            # (and back-filled) and the counter re-baselined, so no bar is built from the prints that got
            # through and no later bar is credited with the dropped shares.
            self.dropped_ticks += 1
            self._ahead_run = (self._ahead_run[0] if self._ahead_run else tick.received, tick.received)
            if ahead < self.max_stamp_ahead + 2:                     # just over: whole-second stamps straddle it
                self._ahead_last = tick.received
            if (bar := self.current_bars.get(sym)) is not None:
                bar['partial'] = True
            counter, day = self._cum_volume.get(sym), tick.received.date()    # the stamp's own date is not trusted
            # Only a drop that could hold session shares re-baselines (pre-open prints never count). The margin is
            # twice the limit: a host just past it receives the opening print well before 09:15 by its own clock.
            open_at = datetime.combine(day, SESSION_OPEN, tzinfo=IST) - timedelta(seconds=2 * self.max_stamp_ahead)
            if tick.received >= open_at:
                self._cum_volume[sym] = (counter[0], counter[1], -1) if counter is not None and counter[0] == day \
                    else (day, 0, -1)
            if self._ahead_warned is None or abs(tick.received - self._ahead_warned) >= timedelta(seconds=60):
                self._ahead_warned = tick.received
                logger.critical(f"[{sym}] Tick stamped {ahead:.0f}s ahead of the host clock dropped: a corrupt "
                                f"exchange time, or the host clock runs far behind. Fix the host's time sync and restart.")
            return
        if tick.received is not None:
            self._ahead_run = None                                   # stamps are believable again
        # Floor the timestamp to the current 5-minute block
        boundary = bar_floor(tick.timestamp, self.bar_minutes)
        if not SESSION_OPEN <= boundary.time() < SESSION_CLOSE:
            # Pre-open auction and post-close prints are not continuous-session bars. Dropping them
            # before the volume baseline moves puts auction volume in the 09:15 bar, as brokers do.
            self.dropped_ticks += 1
            return
        active = self.current_bars.get(sym)
        if (sym in self.last_closed and boundary <= self.last_closed[sym]) or \
                (active is not None and boundary < active['timestamp']):
            # A late print for a closed bar, or out of order: dropped before it can move the volume
            # baseline, so the next accepted print still carries its shares into the current bar.
            self.dropped_ticks += 1
            return
        if self.market_time is None or tick.timestamp > self.market_time:
            self.market_time = tick.timestamp
        volume, rebaselined = self._traded_quantity(tick)
        if tick.received is not None:
            # A trade dates the feed, and so does any newer exchange update once the symbol has traded in
            # this connection. A (re)subscribe snapshot of a quiet name carries its last exchange time,
            # which can be minutes old without the feed being late, so it never counts.
            last = self._last_update.get(sym)
            tracking = last is not None and last[0] == self._feed_epoch
            fresh = tracking and not rebaselined and tick.timestamp > last[1]
            if volume > 0 or fresh:
                self.note_feed_lag(tick.received, (tick.received - tick.timestamp).total_seconds())
            if volume > 0 or tracking:
                newest = max(last[1], tick.timestamp) if tracking else tick.timestamp
                self._last_update[sym] = (self._feed_epoch, newest)
        if volume <= 0:
            # No verified trade: a quote or depth update, or a print that only (re)sets the volume
            # baseline. A bucket in which nothing verifiably traded has no bar (a later positive delta
            # opens it, or the back-fill covers it). The print still proves the previous bucket is over.
            if active is not None and boundary > active['timestamp']:
                self.close_bar(sym)
            return
        if active is None:
            self._open_bar(sym, boundary, tick.price, volume, tick)
        elif boundary > active['timestamp']:
            self.close_bar(sym)
            self._open_bar(sym, boundary, tick.price, volume, tick)
        else:
            active['High'] = max(active['High'], tick.price)
            active['Low'] = min(active['Low'], tick.price)
            active['Close'] = tick.price
            active['Volume'] += volume

    def _hole_before(self, prev_idx: datetime, idx: datetime) -> Optional[datetime]:
        """Start of the bars missing between history's last bar and ``idx``: the rest of prev_idx's session
        (a late join, a reconnect or a stall near the close can cost it its last bars), then idx's session
        up to idx."""
        width = timedelta(minutes=self.bar_minutes)
        if prev_idx >= idx - width:
            return None
        if prev_idx + width < datetime.combine(prev_idx.date(), SESSION_CLOSE, tzinfo=IST):
            return prev_idx + width
        session_open = datetime.combine(idx.date(), SESSION_OPEN, tzinfo=IST)
        if idx <= session_open:
            return None
        return max(prev_idx + width, session_open)

    @staticmethod
    def _span(start: datetime, end: datetime) -> str:
        return f"{start:%H:%M}-{end:%H:%M}" if start.date() == end.date() else f"{start:%Y-%m-%d %H:%M}-{end:%Y-%m-%d %H:%M}"

    def _notify(self, sym: str, ts: datetime, bar: pd.Series) -> None:
        for listener in self.bar_listeners:
            try:
                listener(sym, ts, bar)
            except Exception:
                logger.exception(f"[{sym}] Bar listener failed on the {ts:%Y-%m-%d %H:%M} bar.")

    def _evaluate(self, sym: str, history: pd.DataFrame, idx: datetime) -> None:
        # A failing evaluation must not abort bar bookkeeping, or the tick that closed the bar is lost too.
        try:
            signal = self.alpha.evaluate(sym, history)
        except Exception:
            logger.exception(f"[{sym}] Alpha evaluation failed on the {idx:%Y-%m-%d %H:%M} bar.")
            return
        if signal:
            self.oms_queue.put_nowait(signal)

    def close_bar(self, sym: str) -> None:
        bar = self.current_bars.pop(sym)
        idx = bar['timestamp']
        self.last_closed[sym] = idx
        if bar['partial']:
            logger.info(f"[{sym}] Discarding the {idx:%H:%M} bar: it began before the feed was watching.")
            return

        history = self.market_state.get(sym)
        if history is None:
            history = empty_bars()
        if len(history) and idx <= history.index[-1]:
            logger.warning(f"[{sym}] Live bar {idx} overlaps history (last {history.index[-1]}); keeping history.")
            return
        row = pd.DataFrame([[float(bar[c]) for c in OHLCV]], columns=OHLCV,
                           index=pd.DatetimeIndex([idx], name=history.index.name))
        hole_start = self._hole_before(history.index[-1], idx) if len(history) else None
        self.market_state[sym] = history = pd.concat([history, row]) if len(history) else row
        logger.info(f"📊 [{sym}] 5m Bar Closed {idx:%Y-%m-%d %H:%M} | O: {bar['Open']:.2f} H: {bar['High']:.2f} "
                    f"L: {bar['Low']:.2f} C: {bar['Close']:.2f} | V: {bar['Volume']:,}")

        if hole_start is not None:
            # Evaluating across a hole could report a stale "first crossing" one bar late at a worse price.
            if self.backfill is None:
                logger.warning(f"[{sym}] Bars {self._span(hole_start, idx)} are missing; the {idx:%H:%M} bar is not evaluated.")
                self._notify(sym, idx, row.iloc[0])
                return
            task = asyncio.get_running_loop().create_task(self._backfill_then_evaluate(sym, hole_start, idx, row.iloc[0]))
            self._backfills.add(task)
            task.add_done_callback(self._backfills.discard)
            return
        self._notify(sym, idx, row.iloc[0])
        self._evaluate(sym, history, idx)

    async def _backfill_then_evaluate(self, sym: str, start: datetime, idx: datetime, live_bar: pd.Series) -> None:
        try:
            fetched = await self.backfill(sym, start, idx)
            fetched = fetched[(fetched.index >= pd.Timestamp(start)) & (fetched.index < pd.Timestamp(idx))]
        except Exception as e:
            logger.warning(f"[{sym}] Back-fill of {self._span(start, idx)} failed ({e!r}); the {idx:%H:%M} bar is not evaluated.")
            self._notify(sym, idx, live_bar)
            return
        history = self.market_state[sym]
        if len(fetched):
            history = pd.concat([history, fetched])
            history = history[~history.index.duplicated(keep='first')].sort_index()
            self.market_state[sym] = history
            logger.info(f"[{sym}] Back-filled {len(fetched)} missing bar(s) {self._span(start, idx)} from the broker.")
        for ts, bar in fetched.iterrows():
            self._notify(sym, ts, bar)
        self._notify(sym, idx, live_bar)
        self._evaluate(sym, history.loc[:idx], idx)

    async def cancel_backfills(self) -> None:
        for task in list(self._backfills):
            task.cancel()
        await asyncio.gather(*self._backfills, return_exceptions=True)

    def flush_due_bars(self, now: datetime, grace: timedelta = BAR_CLOSE_GRACE) -> None:
        """Close bars whose bucket has ended; without this, a bar waits for the *next* tick,
        which never comes for an illiquid name or the session's final bar. Both the deadline and
        the liveness proof are judged in feed time (receive time minus the measured feed lag)."""
        lag = timedelta(seconds=self.feed_lag)
        now = to_ist(now) - lag
        width = timedelta(minutes=self.bar_minutes)
        for sym in [s for s, b in self.current_bars.items() if b['timestamp'] + width + grace <= now]:
            bar_end = self.current_bars[sym]['timestamp'] + width
            if self.require_feed_liveness and (self.last_alive is None or self.last_alive - lag < bar_end):
                # The feed went quiet before the bucket ended: the bar's tail may be missing, and the
                # counter already holds those trades. Discard the bar (the back-fill replaces it) and
                # re-baseline the counter, or the next bar would be credited with the tail as well.
                self.current_bars[sym]['partial'] = True
                counter = self._cum_volume.get(sym)
                if counter is not None:
                    self._cum_volume[sym] = (counter[0], counter[1], -1)
            self.close_bar(sym)

    async def process_ticks(self):
        logger.info("Tick Aggregator Online. Awaiting live websocket events...")
        while True:
            tick: Tick = await self.tick_queue.get()
            try:
                self.on_tick(tick)
            except Exception:
                logger.exception(f"[{tick.symbol}] Tick processing failed; tick skipped.")
            finally:
                self.tick_queue.task_done()

    async def bar_clock(self, interval: float = 1.0, clock: Optional[Callable[[], datetime]] = None):
        clock = clock or self.clock
        while True:
            await asyncio.sleep(interval)
            if not self.tick_queue.empty():
                continue                  # ticks received earlier may still belong to a due bar's tail
            try:
                self.flush_due_bars(clock())
            except Exception:
                logger.exception("Bar clock flush failed.")


# ==============================================================================
# 4. EXECUTION ROUTER (OMS)
# ==============================================================================
class OrderStateUnknown(Exception):
    """A live order may be working at the exchange, but its state could not be confirmed.
    ``fill`` carries any quantity already known to be bought (and protected, if possible)."""
    def __init__(self, symbol: str, ref: str, detail: str, fill: Optional["Fill"] = None):
        super().__init__(f"[{symbol}] order {ref}: {detail}")
        self.symbol, self.ref, self.fill = symbol, ref, fill

class OrderGateway(ABC):
    simulates_exits = False                     # True: the engine itself must play the stop/target
    settle_timeout = 5.0                        # budget for one execute() call, used by shutdown

    def __init__(self):
        self.alerts: List[str] = []             # conditions an operator must act on
        self.late_fills: List[Fill] = []        # fills that completed after their caller was cancelled
        self.stopping = False                   # shutdown began: send nothing new, stop waiting for fills
        self._inflight: set = set()

    @abstractmethod
    async def execute(self, plan: OrderPlan) -> Optional[Fill]:
        """Enter the position and attach its stop/target exits; None if nothing was bought."""

    async def wait_inflight(self) -> None:
        """Wait for entries still settling after their caller was cancelled (shutdown)."""
        await asyncio.gather(*self._inflight, return_exceptions=True)

class PaperGateway(OrderGateway):
    """Simulated execution: fills at the signal price, sends nothing anywhere.
    Its stop/target OCO is played by ExecutionRouter.on_bar against closed bars."""
    simulates_exits = True

    def __init__(self, latency: float = 0.3):
        super().__init__()
        self.latency = latency
        self.settle_timeout = latency + 5.0
        self._orders = 0

    async def execute(self, plan: OrderPlan) -> Optional[Fill]:
        await asyncio.sleep(self.latency) # Simulate Broker HTTP Order Placement Latency
        self._orders += 1
        sym = plan.signal.symbol
        logger.info(f"✅ [{sym}] PAPER FILL (simulated, no order sent): {plan.quantity}x @ {plan.signal.entry_price:.2f}.")
        return Fill(sym, plan.quantity, plan.signal.entry_price, f"PAPER-{self._orders}")

class KiteOrderGateway(OrderGateway):
    """Zerodha execution: a marketable LIMIT buy, then a GTT OCO (stop + target) on the filled quantity.

    Zerodha disabled bracket orders (variety ``bo``) in March 2020 and the SDK no longer
    defines ``VARIETY_BO``; GTT supports CNC/NRML/MTF, hence the CNC default.

    Nothing that may exist at the broker is ever abandoned or duplicated:
    * Everything from ``place_order`` to the GTT runs as one shielded task. Cancelling
      ``execute()`` (shutdown) never interrupts it; it finishes within its own deadlines and
      its outcome is reported as an alert.
    * A reply lost after the request may have been sent is resolved through the order's
      unique tag, polled for ``cancel_grace`` seconds; if the order cannot be found or ruled
      out, OrderStateUnknown keeps the symbol blocked.
    * A timed-out entry is cancelled and polled until the exchange confirms a terminal state.
      Once shutdown begins (``stopping``), the fill wait ends at once and the remainder is
      cancelled. Whatever filled gets a GTT, including a partial fill whose cancel cannot be
      confirmed.
    * GTT placement is idempotent. The GTT book's ids are read before arming. After an ambiguous
      failure the book is polled for ``cancel_grace`` seconds (the broker can still be creating
      the GTT), and a matching GTT created since arming began is adopted, even one that has
      already triggered, instead of creating a second one that would sell twice. After any
      ambiguous attempt the book is watched once more for a late duplicate, which raises an
      alert. GTTs already in the book beforehand (an earlier run's) are never adopted.
    """
    TERMINAL = ("COMPLETE", "REJECTED", "CANCELLED")

    @staticmethod
    def _never_sent(e: BaseException) -> bool:
        """True if the request provably never left this machine: a connect timeout, or a connection
        that could not be opened (refused, unreachable, DNS failure). requests reports the latter as
        a plain ConnectionError whose MaxRetryError reason is urllib3's NewConnectionError. Behind a
        proxy, a proxy that could not be reached raises ProxyError around the same connect failure
        (urllib3's own test for "the server never received it"). A proxy that refused the tunnel is
        handled by _tunnel_refused, for the entry only."""
        name = type(e).__name__
        if name == "ConnectTimeout":
            return True
        reason = getattr(e.args[0], "reason", None) if name in ("ConnectionError", "ProxyError") and e.args else None
        if name == "ProxyError":
            reason = getattr(reason, "original_error", None)
            return any(c.__name__ == "ConnectTimeoutError" for c in type(reason).__mro__)  # NewConnectionError too
        return any(c.__name__ == "NewConnectionError" for c in type(reason).__mro__)

    @staticmethod
    def _tunnel_refused(e: BaseException) -> bool:
        """True if a proxy refused the CONNECT for Kite (403, 407, 502, 503...). urllib3 raises this only
        from its tunnel set-up and writes a request only through an open tunnel, so the proxy received
        the CONNECT line and nothing else: the order never left. The GTT path keeps treating it as
        ambiguous on purpose: attempts plus book polls ride out a longer proxy outage than one never-sent
        window would."""
        reason = getattr(e.args[0], "reason", None) if type(e).__name__ == "ProxyError" and e.args else None
        reason = getattr(reason, "original_error", None)
        return isinstance(reason, OSError) and str(reason).startswith("Tunnel connection failed:")

    def __init__(self, kite, exchange: str = "NSE", product: str = "CNC", fill_timeout: float = 30.0,
                 poll_interval: float = 1.0, cancel_grace: float = 15.0, stop_limit_buffer: float = 0.02,
                 tick_size: float = 0.05):
        super().__init__()
        self.kite = kite
        self.exchange = exchange
        self.product = product
        self.fill_timeout = fill_timeout
        self.poll_interval = poll_interval
        self.cancel_grace = cancel_grace
        self.stop_limit_buffer = stop_limit_buffer
        self.tick_size = tick_size
        # An estimate of the worst case, used only to decide when shutdown reports "did not settle": the
        # fill wait, plus 12 windows of cancel_grace (tag lookup, cancel wait, 3 GTT-book polls that may
        # each run one more window until a read succeeds, up to 3 never-sent GTT retry windows (one
        # before each attempt), the duplicate watch), plus 20 SDK calls at the client timeout (ltp,
        # place, 3 cancels, 3 snapshot tries, 3 GTT attempts, one overrun call per window). requests
        # bounds each connect and each socket read by that timeout, not a whole call, so a trickling
        # reply can take longer; the entry is then still awaited, never interrupted.
        sdk_timeout = float(getattr(kite, "timeout", None) or 7.0)
        self.settle_timeout = fill_timeout + 12 * cancel_grace + 20 * (sdk_timeout + poll_interval)

    async def _poll_state(self, order_id: str) -> Optional[dict]:
        try:
            history = await asyncio.to_thread(self.kite.order_history, order_id)
        except Exception as e:
            logger.warning(f"order_history({order_id}) failed: {e!r}; retrying.")
            return None
        return history[-1] if history else None

    async def _wait_terminal(self, order_id: str, timeout: float,
                             interruptible: bool = False) -> Tuple[Optional[dict], Optional[dict]]:
        """(terminal state or None, last state seen) after polling for up to ``timeout`` seconds,
        or, if ``interruptible``, until shutdown begins."""
        deadline, last = time.monotonic() + timeout, None
        while True:
            state = await self._poll_state(order_id)
            if state is not None:
                last = state
                if state.get('status') in self.TERMINAL:
                    return state, last
            if time.monotonic() >= deadline or (interruptible and self.stopping):
                return None, last
            await asyncio.sleep(self.poll_interval)

    async def _cancel_and_settle(self, sym: str, order_id: str, last: Optional[dict]) -> Tuple[Optional[dict], Optional[dict]]:
        for attempt in range(3):
            try:
                await asyncio.to_thread(self.kite.cancel_order, self.kite.VARIETY_REGULAR, order_id)
                break
            except Exception as e:
                # It may already be complete or cancelled; polling below decides either way.
                logger.error(f"[{sym}] Cancel of {order_id} failed (attempt {attempt + 1}/3): {e!r}")
                await asyncio.sleep(self.poll_interval)
        terminal, seen = await self._wait_terminal(order_id, self.cancel_grace)
        return terminal, seen or last

    async def _find_by_tag(self, sym: str, tag: str, cause: Exception) -> str:
        """The order id for ``tag``, polling the order book for ``cancel_grace`` seconds (the broker
        may book an order moments after the reply was lost); OrderStateUnknown if it never shows."""
        deadline, read_error = time.monotonic() + self.cancel_grace, None
        while True:
            try:
                orders = await asyncio.to_thread(self.kite.orders)
                match = next((str(o['order_id']) for o in orders if o.get('tag') == tag), None)
                if match is not None:
                    return match
                read_error = None
            except Exception as e:
                read_error = e
                logger.warning(f"[{sym}] Order book read failed: {e!r}; retrying.")
            if time.monotonic() >= deadline:
                why = (f"the order book could not be read ({read_error!r})" if read_error
                       else f"tag {tag} never appeared in the order book")
                raise OrderStateUnknown(sym, tag, f"entry outcome unknown after {cause!r}; {why}") from cause
            await asyncio.sleep(self.poll_interval)

    async def _gtt_ids(self, sym: str) -> Optional[set]:
        """Ids of every GTT already in the book before arming (any status), or None if the book
        cannot be read in 3 tries."""
        for attempt in range(3):
            try:
                return {str(g.get('id')) for g in await asyncio.to_thread(self.kite.get_gtts) or []}
            except Exception as e:
                logger.warning(f"[{sym}] GTT book read failed before arming (attempt {attempt + 1}/3): {e!r}.")
                if attempt < 2:
                    await asyncio.sleep(self.poll_interval)
        return None

    @staticmethod
    def _matching_gtts(gtts: Optional[list], sym: str, plan: OrderPlan, quantity: int,
                       exclude: set) -> List[Tuple[str, str]]:
        """(id, status) of the GTTs not in ``exclude`` that protect exactly this position and
        exist or have already acted ('active' or 'triggered')."""
        want = [round(plan.stop_loss, 2), round(plan.target, 2)]
        found = []
        for g in gtts or []:
            cond = g.get('condition') or {}
            if str(g.get('id')) in exclude or g.get('status') not in ('active', 'triggered') \
                    or cond.get('tradingsymbol') != sym:
                continue
            if [round(float(v), 2) for v in cond.get('trigger_values') or []] != want:
                continue
            legs = g.get('orders') or []
            if legs and all(int(leg.get('quantity') or 0) == quantity for leg in legs):
                found.append((str(g['id']), g.get('status')))
        return found

    async def _await_gtt(self, sym: str, plan: OrderPlan, quantity: int,
                         exclude: set) -> Tuple[Optional[Tuple[str, str]], bool]:
        """Poll the GTT book for ``cancel_grace`` seconds for a GTT that a request with a lost reply
        may still create. (first match or None, whether the book was read at the end of the window).

        A booked GTT stays in the book (active or triggered), so one successful read at or after the
        deadline covers the whole window; if the latest read failed, polling continues for up to one
        more window until a read succeeds."""
        deadline, readable = time.monotonic() + self.cancel_grace, False
        while True:
            try:
                found = self._matching_gtts(await asyncio.to_thread(self.kite.get_gtts), sym, plan, quantity, exclude)
                readable = True
                if found:
                    return found[0], True
            except Exception as e:
                readable = False
                logger.warning(f"[{sym}] GTT book read failed: {e!r}; retrying.")
            now = time.monotonic()
            if now >= deadline and (readable or now >= deadline + self.cancel_grace):
                return None, readable
            await asyncio.sleep(self.poll_interval)

    async def _watch_duplicates(self, sym: str, plan: OrderPlan, quantity: int, order_id: str,
                                trigger_id: str, known: set) -> None:
        """After an ambiguous attempt, an earlier request's GTT can still land. Watch the book for
        ``cancel_grace`` seconds and alert on any second GTT that sells the same shares."""
        deadline, readable = time.monotonic() + self.cancel_grace, False
        while True:
            try:
                dupes = self._matching_gtts(await asyncio.to_thread(self.kite.get_gtts), sym, plan, quantity,
                                            known | {trigger_id})
                readable = True
                if dupes:
                    ids = ", ".join(f"{i} ({status})" for i, status in dupes)
                    fired = [i for i, status in dupes if status == 'triggered']
                    if fired:
                        self._alert(f"[{sym}] GTT DUPLICATE: {ids} also sell the {quantity} shares of order {order_id}; "
                                    f"GTT {', '.join(fired)} has already TRIGGERED: the exit has fired. DELETE GTT "
                                    f"{trigger_id} and any other active duplicate, and CHECK ORDERS AND HOLDINGS.")
                    else:
                        self._alert(f"[{sym}] GTT DUPLICATE: {ids} also sell the {quantity} shares of order {order_id} "
                                    f"that GTT {trigger_id} protects. DELETE ALL BUT GTT {trigger_id}.")
                    return
            except Exception as e:
                readable = False
                logger.warning(f"[{sym}] GTT book read failed: {e!r}; retrying.")
            if time.monotonic() >= deadline:
                if not readable:
                    self._alert(f"[{sym}] GTT {trigger_id} armed after an ambiguous failure, but the GTT book could "
                                f"not be read at the end of the watch to rule out a duplicate. CHECK THE GTT BOOK.")
                return
            await asyncio.sleep(self.poll_interval)

    def _alert(self, msg: str) -> None:
        logger.critical(msg)
        self.alerts.append(msg)

    async def _protect(self, plan: OrderPlan, order_id: str, state: dict, known: Optional[set]) -> Optional[Fill]:
        k, sym = self.kite, plan.signal.symbol
        filled = int(state.get('filled_quantity') or 0)
        if filled <= 0:
            logger.warning(f"[{sym}] Entry {order_id} ended {state.get('status')} with no fill: {state.get('status_message')}")
            return None
        avg = float(state.get('average_price') or plan.signal.entry_price)
        stop_limit = round_to_tick(plan.stop_loss * (1 - self.stop_limit_buffer), self.tick_size, "down")
        legs = [
            {"transaction_type": k.TRANSACTION_TYPE_SELL, "quantity": filled, "order_type": k.ORDER_TYPE_LIMIT,
             "product": self.product, "price": stop_limit},
            {"transaction_type": k.TRANSACTION_TYPE_SELL, "quantity": filled, "order_type": k.ORDER_TYPE_LIMIT,
             "product": self.product, "price": plan.target},
        ]
        # ``known``: the GTT book's ids before this entry began (a match listed there belongs to someone else).
        trigger_id, ambiguous, error = None, False, None
        attempt, never_sent_until, recheck = 0, None, False
        while attempt < 3:
            found = None
            if recheck:
                # A retry after an ambiguous attempt must follow a read of the book: the ambiguous request's
                # GTT may have been booked during the outage. An unreadable book is part of the outage.
                try:
                    gtts = await asyncio.to_thread(k.get_gtts)
                except Exception as read_error:
                    logger.warning(f"[{sym}] GTT book read failed ({read_error!r}); not placing until it can be read.")
                    if is_permanent_error(read_error) or time.monotonic() >= never_sent_until:
                        error = read_error                     # the alert names what ended the loop
                        break
                    await asyncio.sleep(self.poll_interval)
                    continue
                recheck = False
                found = next(iter(self._matching_gtts(gtts, sym, plan, filled, known or set())), None)
            if found is None:
                try:
                    gtt = await asyncio.to_thread(
                        k.place_gtt, trigger_type=k.GTT_TYPE_OCO, tradingsymbol=sym, exchange=self.exchange,
                        trigger_values=[plan.stop_loss, plan.target], last_price=avg, orders=legs)
                    trigger_id = str(gtt['trigger_id'])
                    logger.info(f"🛡️ [{sym}] GTT OCO {trigger_id} armed: stop {plan.stop_loss:.2f} "
                                f"(limit {stop_limit:.2f}) / target {plan.target:.2f}")
                    break
                except Exception as e:
                    error = e
                    logger.error(f"[{sym}] GTT attempt {attempt + 1}/3 failed: {e!r}")
                    if is_permanent_error(e):
                        break
                    if self._never_sent(e):
                        # Nothing left this machine, so nothing can exist from this request: retry through the
                        # outage for up to cancel_grace without using up an attempt.
                        recheck = ambiguous
                        never_sent_until = never_sent_until or time.monotonic() + self.cancel_grace
                        if time.monotonic() >= never_sent_until:
                            break
                        await asyncio.sleep(self.poll_interval)
                        continue
                    attempt += 1
                    never_sent_until = None                    # a later outage gets its own window
                    # The request may have reached the broker, which can still be creating the GTT: a blind
                    # retry would arm a second one that sells the whole position again.
                    ambiguous = True
                    found, readable = await self._await_gtt(sym, plan, filled, exclude=known or set())
                    if found is None and readable:
                        continue                               # no GTT appeared in cancel_grace: retry
                    if found is None:
                        self._alert(f"[{sym}] GTT STATE UNKNOWN for {filled} shares (order {order_id}): placing "
                                    f"failed ({e!r}) and the GTT book could not be read at the end of the window. "
                                    f"CHECK THE GTT BOOK.")
                        return Fill(sym, filled, avg, order_id, None)
            # A GTT created by an ambiguous request: adopt it rather than arm a second one.
            if known is None:
                self._alert(f"[{sym}] GTT STATE UNKNOWN for {filled} shares (order {order_id}): placing failed "
                            f"({error!r}); GTT {found[0]} matches this position, but the book was unreadable before "
                            f"arming, so it may be an earlier one. CHECK THE GTT BOOK.")
                return Fill(sym, filled, avg, order_id, None)
            trigger_id, status = found
            if status == 'triggered':
                self._alert(f"[{sym}] GTT {trigger_id} for {filled} shares (order {order_id}) had already TRIGGERED "
                            f"when its lost reply was resolved: the exit has fired. CHECK ORDERS AND HOLDINGS.")
            else:
                logger.info(f"🛡️ [{sym}] GTT OCO {trigger_id} found in the GTT book; the lost reply is resolved.")
            break
        if trigger_id is not None and ambiguous:
            await self._watch_duplicates(sym, plan, filled, order_id, trigger_id, known or set())
        if trigger_id is None and ambiguous:
            self._alert(f"[{sym}] GTT STATE UNKNOWN for {filled} shares (order {order_id}): every GTT request failed "
                        f"(last: {error!r}) and at least one may still have been created. CHECK THE GTT BOOK.")
        elif trigger_id is None:
            self._alert(f"[{sym}] POSITION OPEN WITHOUT EXITS: {filled} shares bought (order {order_id}) but no GTT "
                        f"could be placed (last: {error!r}).")
        return Fill(sym, filled, avg, order_id, trigger_id)

    async def _enter_and_protect(self, plan: OrderPlan, tag: str, known: Optional[set]) -> Optional[Fill]:
        k, sym = self.kite, plan.signal.symbol
        try:
            order_id = str(await asyncio.to_thread(
                k.place_order, variety=k.VARIETY_REGULAR, exchange=self.exchange, tradingsymbol=sym,
                transaction_type=k.TRANSACTION_TYPE_BUY, quantity=plan.quantity, product=self.product,
                order_type=k.ORDER_TYPE_LIMIT, price=plan.entry_limit, validity=k.VALIDITY_DAY, tag=tag))
        except Exception as e:
            if is_permanent_error(e) or self._never_sent(e) or self._tunnel_refused(e):
                raise                         # refused by the broker, or never sent: nothing exists
            logger.error(f"[{sym}] place_order failed ({e!r}); looking for tag {tag} at the broker.")
            order_id = await self._find_by_tag(sym, tag, e)
        logger.info(f"[{sym}] Entry order {order_id} placed: BUY {plan.quantity} LIMIT {plan.entry_limit:.2f}")

        terminal, last = await self._wait_terminal(order_id, self.fill_timeout, interruptible=True)
        if terminal is None:
            why = "shutdown began" if self.stopping else f"not filled in {self.fill_timeout:.0f}s"
            logger.warning(f"[{sym}] Entry {order_id} {why}; cancelling remainder.")
            terminal, last = await self._cancel_and_settle(sym, order_id, last)
        if terminal is None:
            # The cancel is unconfirmed: protect what is known to be bought, then keep the symbol blocked.
            fill = await self._protect(plan, order_id, last, known) if last and int(last.get('filled_quantity') or 0) > 0 else None
            known = ""
            if fill is not None:
                cover = f"covered by GTT {fill.exit_order_id}" if fill.exit_order_id else "NOT covered by a GTT"
                known = f"; {fill.quantity} shares already bought are {cover}"
            raise OrderStateUnknown(sym, order_id, f"not terminal {self.cancel_grace:.0f}s after cancelling{known}; "
                                                   f"the remainder may still be working", fill=fill)
        return await self._protect(plan, order_id, terminal, known)

    def _report_late(self, sym: str, task: asyncio.Task) -> None:
        """Outcome of an entry that finished settling after its caller was cancelled."""
        if task.cancelled():
            msg = f"[{sym}] Entry settlement was cancelled before it finished. CHECK THE BROKER TERMINAL."
        elif task.exception() is not None:
            e = task.exception()
            if isinstance(e, OrderStateUnknown) and e.fill is not None:
                self.late_fills.append(e.fill)
            msg = f"[{sym}] Entry ended during shutdown with an unresolved state: {e!r}"
        elif task.result() is None:
            logger.info(f"[{sym}] Entry settled during shutdown with no fill.")
            return
        else:
            fill = task.result()
            self.late_fills.append(fill)
            cover = f"exits armed (GTT {fill.exit_order_id})" if fill.exit_order_id else "exits NOT armed"
            msg = f"[{sym}] Entry {fill.order_id} filled {fill.quantity} while shutting down; {cover}."
        logger.critical(msg)
        self.alerts.append(msg)

    async def execute(self, plan: OrderPlan) -> Optional[Fill]:
        k, sym = self.kite, plan.signal.symbol
        # The GTT book's ids before anything is bought, so its latency never delays the exits, and the
        # LTP check and place_order stay back to back.
        known = await self._gtt_ids(sym)
        # The market may have moved since the bar closed: never buy below the stop or chase above the limit.
        key = f"{self.exchange}:{sym}"
        ltp = float((await asyncio.to_thread(k.ltp, key))[key]["last_price"])
        if not plan.stop_loss < ltp <= plan.entry_limit:
            logger.warning(f"[{sym}] Entry skipped: LTP {ltp:.2f} outside ({plan.stop_loss:.2f}, {plan.entry_limit:.2f}].")
            return None

        if self.stopping:
            logger.warning(f"[{sym}] Entry not sent: shutdown began.")
            return None

        tag = f"ipm{uuid.uuid4().hex[:12]}"   # finds the order if the reply to place_order is lost
        task = asyncio.ensure_future(self._enter_and_protect(plan, tag, known))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Never interrupt an entry that may exist at the broker: it finishes on its own deadlines.
            logger.warning(f"[{sym}] Shutdown with the entry in flight: settling it before exit.")
            task.add_done_callback(functools.partial(self._report_late, sym))
            raise

class ExecutionRouter:
    def __init__(self, oms_queue: asyncio.Queue, risk_per_trade: float, max_position_value: Optional[float] = None,
                 gateway: Optional[OrderGateway] = None, tick_size: float = 0.05, max_entry_slippage: float = 0.005,
                 max_signal_age: Optional[timedelta] = timedelta(seconds=60),
                 clock: Optional[Callable[[], datetime]] = None, bar_minutes: int = BAR_MINUTES,
                 stop_limit_buffer: float = 0.02):
        self.oms_queue = oms_queue
        self.risk_per_trade = risk_per_trade       # rupees lost if the stop is hit at its trigger
        self.max_position_value = max_position_value
        self.gateway = gateway or PaperGateway()
        self.tick_size = tick_size
        self.max_entry_slippage = max_entry_slippage
        self.max_signal_age = max_signal_age       # None disables the staleness check
        self.clock = clock or (lambda: datetime.now(IST))
        self.bar_minutes = bar_minutes
        self.stop_limit_buffer = stop_limit_buffer  # paper mirrors the live GTT's stop leg
        self.accepting = True                      # False once shutdown begins
        self.active_inventory = set()              # symbols with a pending or open position
        self.fills: List[Fill] = []
        self.positions: Dict[str, Position] = {}   # open positions
        self.closed_positions: List[Position] = []
        self.unresolved: List[str] = []            # live orders whose state could not be confirmed

    @property
    def settle_timeout(self) -> float:
        return self.gateway.settle_timeout + 5.0

    def _close_position(self, pos: Position, price: float, reason: str, bar_time: datetime) -> None:
        pos.exit_price, pos.exit_reason, pos.closed_at = price, reason, bar_time
        del self.positions[pos.symbol]
        self.closed_positions.append(pos)
        self.active_inventory.discard(pos.symbol)
        logger.info(f"🏁 [{pos.symbol}] PAPER EXIT {reason}: {pos.quantity}x @ {price:.2f} "
                    f"on the {to_ist(bar_time):%Y-%m-%d %H:%M} bar | P&L ₹{pos.pnl:,.0f}")

    def on_bar(self, sym: str, bar_time: datetime, bar: pd.Series) -> None:
        """Play the OCO against a closed bar exactly as the live GTT would (paper only).

        The stop leg is a SELL LIMIT ``stop_limit_buffer`` below its trigger: a gap through the
        trigger fills at the open if the open is above that limit, later in the bar if price
        recovers to it, and otherwise rests unfilled. An open beyond the target fills the
        target at the open. If a bar touches both levels from an open between them, the stop is
        assumed to have come first.
        """
        pos = self.positions.get(sym)
        if pos is None or not self.gateway.simulates_exits or pd.Timestamp(bar_time) < pd.Timestamp(pos.opened_at):
            return
        o, h, lo = float(bar['Open']), float(bar['High']), float(bar['Low'])
        limit = pos.stop_limit if pos.stop_limit is not None else pos.stop_loss
        if pos.stop_triggered:
            if h >= limit:
                self._close_position(pos, max(limit, o), "STOP", bar_time)
            return
        if o >= pos.target:
            self._close_position(pos, o, "TARGET", bar_time)
        elif o <= pos.stop_loss:
            if o >= limit:
                self._close_position(pos, o, "STOP", bar_time)
            elif h >= limit:
                self._close_position(pos, limit, "STOP", bar_time)
            else:
                pos.stop_triggered = True
                logger.critical(f"[{sym}] PAPER STOP TRIGGERED BUT UNFILLED: opened {o:.2f} below the "
                                f"{limit:.2f} stop limit; the SELL LIMIT rests until price recovers.")
        elif lo <= pos.stop_loss:
            self._close_position(pos, pos.stop_loss, "STOP", bar_time)
        elif h >= pos.target:
            self._close_position(pos, pos.target, "TARGET", bar_time)

    def signal_problem(self, signal: Signal) -> Optional[str]:
        """Why a signal must not be traded now, if anything: its bar closed long ago (e.g. at the
        next day's open), or it claims a bar that has not closed yet (a synthetic or corrupt tape)."""
        if signal.bar_time is None:
            return None
        bar_close = to_ist(signal.bar_time) + timedelta(minutes=self.bar_minutes)
        age = to_ist(self.clock()) - bar_close
        if age < -timedelta(seconds=5):
            return f"its {to_ist(signal.bar_time):%Y-%m-%d %H:%M} bar has not closed yet"
        age += timedelta(seconds=getattr(self.clock, "wall_gain", lambda: 0.0)())   # a suspend the clock missed
        if self.max_signal_age is not None and age > self.max_signal_age:
            return f"it is stale (the {to_ist(signal.bar_time):%Y-%m-%d %H:%M} bar closed {age.total_seconds():.0f}s ago)"
        return None

    def is_stale(self, signal: Signal) -> bool:
        return self.signal_problem(signal) is not None

    def _calculate_qty(self, entry: float, stop_loss: float) -> int:
        risk_per_share = round(entry - stop_loss, 9)   # 3.0000000000000004 must not cost a share
        if not (math.isfinite(entry) and math.isfinite(risk_per_share)) or entry <= 0 or risk_per_share <= 0:
            return 0
        qty = math.floor(self.risk_per_trade / risk_per_share)
        if self.max_position_value is not None:
            qty = min(qty, math.floor(self.max_position_value / entry))
        return max(0, qty)

    def plan(self, signal: Signal) -> Optional[OrderPlan]:
        """Tick-rounded prices, sized on the worst acceptable fill (the limit price)."""
        entry_limit = round_to_tick(signal.entry_price * (1 + self.max_entry_slippage), self.tick_size, "up")
        stop = round_to_tick(signal.stop_loss, self.tick_size, "down")
        target = round_to_tick(signal.target, self.tick_size, "down")
        qty = self._calculate_qty(entry_limit, stop)
        if qty <= 0 or target <= entry_limit:
            return None
        return OrderPlan(signal, qty, entry_limit, stop, target)

    async def process_orders(self):
        logger.info("OMS Router Online. Enforcing Risk Parameters...")
        while True:
            signal: Signal = await self.oms_queue.get()
            try:
                await self._route(signal)
            except Exception:
                logger.exception(f"[{signal.symbol}] Order routing failed.")
            finally:
                self.oms_queue.task_done()

    async def _route(self, signal: Signal) -> None:
        sym = signal.symbol
        if not self.accepting:
            logger.warning(f"[{sym}] Shutting down: signal from the {to_ist(signal.bar_time):%H:%M} bar not traded.")
            return
        if sym in self.active_inventory:
            logger.info(f"[{sym}] Signal ignored: position already pending/open.")
            return
        problem = self.signal_problem(signal)
        if problem:
            logger.warning(f"[{sym}] Signal not traded: {problem}.")
            return
        plan = self.plan(signal)
        if plan is None:
            logger.warning(f"[{sym}] Signal rejected by risk checks (entry {signal.entry_price:.2f}, "
                           f"stop {signal.stop_loss:.2f}, target {signal.target:.2f}).")
            return

        self.active_inventory.add(sym)  # reserve before awaiting so duplicates can't double-enter
        notional = plan.quantity * plan.entry_limit
        logger.info(f"🚀 [OMS DISPATCH] BUY {plan.quantity}x {sym} LIMIT {plan.entry_limit:.2f} "
                    f"(signal {signal.entry_price:.2f}) | notional ≤ ₹{notional:,.0f} | risk ≤ ₹{plan.quantity * (plan.entry_limit - plan.stop_loss):,.0f}")
        logger.info(f"🛡️ Target: {plan.target:.2f} | Stop Loss: {plan.stop_loss:.2f}")
        try:
            fill = await self.gateway.execute(plan)
        except OrderStateUnknown as e:
            # An order may be working: keep the symbol blocked so nothing enters twice.
            self.unresolved.append(str(e))
            logger.critical(f"{e}. {sym} stays blocked; CHECK THE BROKER TERMINAL.")
            if e.fill is not None:
                self._record_fill(signal, plan, e.fill)
            return
        except Exception:
            self.active_inventory.discard(sym)
            raise
        if fill is None:
            self.active_inventory.discard(sym)
            return
        self._record_fill(signal, plan, fill)

    def _record_fill(self, signal: Signal, plan: OrderPlan, fill: Fill) -> None:
        self.fills.append(fill)
        opened_at = (signal.bar_time + timedelta(minutes=self.bar_minutes)) if signal.bar_time else self.clock()
        stop_limit = round_to_tick(plan.stop_loss * (1 - self.stop_limit_buffer), self.tick_size, "down")
        self.positions[fill.symbol] = Position(fill.symbol, fill.quantity, fill.average_price, plan.stop_loss,
                                               plan.target, opened_at, stop_limit=stop_limit)


# ==============================================================================
# 5. DYNAMIC MARKET SIMULATOR (FOR OUT-OF-BOX DEMONSTRATION)
# ==============================================================================
async def simulate_live_market(tick_adapter: LiveTickAdapter, sym: str, df: pd.DataFrame,
                               base_high: Optional[float] = None, pause: float = 1.0, t0: Optional[datetime] = None):
    """Synthetic next-session tape that breaks the real historical base on heavy volume.

    Prices and volumes are invented; only the base high and last close come from data.
    It must never share a run with a live feed or live orders (main() enforces this).
    """
    await asyncio.sleep(pause)

    base_high = float(df['High'].iloc[:150].max()) if base_high is None else base_high
    last_close = float(df['Close'].iloc[-1])
    t0 = t0 or next_session_open(df.index[-1])
    t1, t2 = t0 + timedelta(minutes=BAR_MINUTES), t0 + timedelta(minutes=2 * BAR_MINUTES)

    logger.info(f"📡 [EXCHANGE] Initiating simulated stream for {sym} ({t0:%a %Y-%m-%d} session)")
    logger.info(f"📡 [EXCHANGE] Real Historical Base High to Beat: {base_high:.2f} | Last close: {last_close:.2f}")

    # 1. Opening bar trades quietly around the prior close.
    tick_adapter.broker_on_ticks(None, [
        {'symbol': sym, 'price': last_close, 'volume': 90_000, 'timestamp': t0},
        {'symbol': sym, 'price': last_close * 1.002, 'volume': 60_000, 'timestamp': t0 + timedelta(minutes=2)},
    ])
    await asyncio.sleep(pause)

    # 2. Institutional Block (Breaks the Base High dynamically with massive volume)
    breakout_price = base_high * 1.015 # 1.5% above historical high
    logger.info(f"📡 [EXCHANGE] Massive volume detected. Institutional sweep crossing {base_high:.2f}...")
    tick_adapter.broker_on_ticks(None, [
        {'symbol': sym, 'price': base_high * 1.002, 'volume': 250_000, 'timestamp': t1},
        {'symbol': sym, 'price': base_high * 1.017, 'volume': 400_000, 'timestamp': t1 + timedelta(minutes=2)},
        {'symbol': sym, 'price': breakout_price, 'volume': 200_000, 'timestamp': t1 + timedelta(minutes=4)},
    ])
    await asyncio.sleep(pause)

    # 3. The next bar's first print closes the breakout bar (the bar clock would otherwise).
    tick_adapter.broker_on_ticks(None, [{'symbol': sym, 'price': breakout_price, 'volume': 50_000, 'timestamp': t2}])


# ==============================================================================
# 6. MAIN DEPLOYMENT PIPELINE
# ==============================================================================
def start_kite_feed(api_key: str, access_token: str, tokens: List[int], tick_adapter: LiveTickAdapter,
                    feed_dead: Optional[asyncio.Event] = None):
    """Stream full-mode ticks (with cumulative volume and exchange time) into the synthesizer.

    Every (re)connection re-baselines the volume counters; when KiteTicker gives up
    reconnecting, ``feed_dead`` is set so main() can stop instead of running blind.
    """
    from kiteconnect import KiteTicker
    loop = tick_adapter.loop
    kws = KiteTicker(api_key, access_token)
    kws.on_ticks = tick_adapter.broker_on_ticks

    def on_connect(ws, response):
        loop.call_soon_threadsafe(tick_adapter.mark_feed_reset)   # runs before this connection's first tick
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        logger.info(f"Kite websocket connected; streaming {len(tokens)} instruments.")

    def on_noreconnect(ws):
        logger.critical("Kite websocket gave up reconnecting.")
        if feed_dead is not None:
            loop.call_soon_threadsafe(feed_dead.set)

    def on_close(ws, code, reason):
        logger.warning(f"Kite websocket closed ({code}: {reason}).")
        loop.call_soon_threadsafe(tick_adapter.mark_feed_down)      # the blind spot starts now

    kws.on_connect = on_connect
    def on_message(ws, payload, is_binary):
        # Heartbeats (1 byte) and text messages. A tick payload is stamped by broker_on_ticks once its
        # ticks are queued, so it cannot vouch for its own tail.
        if not is_binary or len(payload) <= 4:
            loop.call_soon_threadsafe(tick_adapter.note_feed_alive, tick_adapter.clock())

    kws.on_message = on_message
    kws.on_error = lambda ws, code, reason: logger.error(f"Kite websocket error {code}: {reason}")
    kws.on_close = on_close
    kws.on_reconnect = lambda ws, attempts: logger.warning(f"Kite websocket reconnecting (attempt {attempts}).")
    kws.on_noreconnect = on_noreconnect
    kws.connect(threaded=True)
    return kws

async def _watch_feed(feed_dead: asyncio.Event, tick_adapter: Optional[LiveTickAdapter] = None,
                      stall_after: float = 15.0, check_every: float = 1.0) -> None:
    """Fails when the feed is gone: KiteTicker gave up reconnecting, or (during the session) no
    message at all, heartbeats included, arrived for ``stall_after`` seconds. KiteTicker never
    pings, so a half-open socket would otherwise stay 'connected' and silent forever."""
    clock = tick_adapter.clock if tick_adapter is not None else (lambda: datetime.now(IST))
    started = clock()
    while True:
        try:
            await asyncio.wait_for(feed_dead.wait(), timeout=check_every)
            raise RuntimeError("the Kite websocket gave up reconnecting; no market data")
        except asyncio.TimeoutError:
            pass
        if tick_adapter is None:
            continue
        if tick_adapter.blind_for > stall_after:
            tick_adapter.mark_feed_down()
            raise RuntimeError(f"every tick for {tick_adapter.blind_for:.0f}s was stamped more than "
                               f"{tick_adapter.max_stamp_ahead:.0f}s ahead of this run's clock (the host clock runs "
                               f"far behind, or the host was suspended); restart once the time sync is fixed")
        last_drop, skew = tick_adapter._ahead_last, tick_adapter.clock_skew
        if last_drop is not None and (clock() - last_drop).total_seconds() < 60 \
                and skew < -(tick_adapter.max_stamp_ahead - 1.5):
            # Whole-second stamps straddle the limit: some prints are dropped, each drop discards its bar, and the
            # rest keep the run "alive" without a single usable bar. The clock is as untrustworthy as far behind.
            tick_adapter.mark_feed_down()
            raise RuntimeError(f"the host clock runs {-skew:.1f}s behind the exchange, within 1.5s of the "
                               f"{tick_adapter.max_stamp_ahead:.0f}s limit, and prints keep being dropped; restart once "
                               f"the time sync is fixed")
        now = clock()
        last = tick_adapter.last_alive or started
        in_session = now.weekday() < 5 and SESSION_OPEN <= now.time() < SESSION_CLOSE
        if in_session and (now - last).total_seconds() > stall_after:
            tick_adapter.mark_feed_down()
            raise RuntimeError(f"no market data (not even heartbeats) for {stall_after:.0f}s during the session")

def _ip_address(text: str) -> str:
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an IP address") from None

def _non_negative(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0 (0 = until Ctrl-C)")
    return value

def _lookback_days(text: str) -> int:
    value = int(text)
    if not 1 <= value <= MAX_LOOKBACK_DAYS:
        raise argparse.ArgumentTypeError(f"must be a whole number of days from 1 to {MAX_LOOKBACK_DAYS}")
    return value

def _positive(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number > 0")
    return value

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IPO momentum engine: history sync, bar synthesis, breakout alpha, execution.")
    p.add_argument("--source", choices=["yahoo", "csv", "kite"], default="yahoo", help="historical data source (default: yahoo)")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="bar file for --source csv")
    p.add_argument("--symbol", default="SWIGGY", help="NSE tradingsymbol (default: SWIGGY)")
    p.add_argument("--listing-date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=IST),
                   help="IPO listing date YYYY-MM-DD; required for --source kite (default for yahoo/csv demos: "
                        "the first bar of the last 20 days)")
    p.add_argument("--base-sessions", type=int, help="define the IPO base as the first N sessions (default: first 150 bars)")
    p.add_argument("--max-lookback-days", type=_lookback_days, default=180,
                   help="fetch history from at most this many days back, for every source (default: 180; "
                        "demos without --listing-date use at most 20)")
    p.add_argument("--risk-per-trade", type=_positive, default=15_000.0, help="rupees lost if the stop is hit")
    p.add_argument("--max-position-value", type=_positive, default=1_000_000.0, help="rupee cap on a position's notional")
    p.add_argument("--rvol-threshold", type=_positive, default=2.0)
    p.add_argument("--rvol-mode", choices=AlphaEngine.RVOL_MODES, default="trailing",
                   help="volume baseline: previous 20 bars, or the same slot in prior sessions")
    p.add_argument("--risk-reward", type=_positive, default=3.0)
    p.add_argument("--run-seconds", type=_non_negative, default=6.0, help="how long to run; 0 = until Ctrl-C")
    p.add_argument("--no-simulate", action="store_true", help="do not inject the synthetic breakout tape")
    p.add_argument("--allow-partial-history", action="store_true",
                   help="trade symbols whose history does not reach the listing (base anchored at first bar)")
    p.add_argument("--live-feed", action="store_true",
                   help="stream Kite websocket ticks (needs --source kite; disables the synthetic tape)")
    p.add_argument("--live-orders", action="store_true",
                   help="send REAL orders to Zerodha (needs --source kite --live-feed --expect-ip)")
    p.add_argument("--expect-ip", type=_ip_address,
                   help="abort unless the public IP equals this (required with --live-orders)")
    return p

def _config_error(args: argparse.Namespace) -> Optional[str]:
    if (args.live_orders or args.live_feed) and args.source != "kite":
        return "--live-orders/--live-feed require --source kite."
    if args.live_orders and not args.live_feed:
        return "--live-orders requires --live-feed: without a live feed only the synthetic tape could trigger orders."
    if args.live_orders and not args.expect_ip:
        return "--live-orders requires --expect-ip <the static IP registered with the broker>."
    if args.source == "kite" and args.listing_date is None:
        return "--source kite requires --listing-date: the IPO base and AVWAP are anchored at the listing."
    if args.base_sessions is not None and args.base_sessions < 1:
        return "--base-sessions must be at least 1."
    return None

def exchange_clock(get_ticker: Callable[[], "LiveTickAdapter"]) -> Callable[[], datetime]:
    """The ticker's host clock, corrected by how far it runs behind the exchange (its clock_skew). Latency
    is not removed: a signal that arrives late really is older."""
    def now() -> datetime:
        ticker = get_ticker()
        return ticker.clock() - timedelta(seconds=ticker.clock_skew)
    now.wall_gain = lambda: getattr(get_ticker().clock, "wall_gain", lambda: 0.0)()
    return now

STOP_SIGNALS = [s for s in (getattr(signal, n, None) for n in ("SIGINT", "SIGTERM", "SIGHUP")) if s is not None]

def _deliver_signal(loop: asyncio.AbstractEventLoop, callback: Callable[[int], None], signum: int, frame) -> None:
    """A plain signal handler (where the loop cannot own signals) that hands over thread-safely."""
    try:
        loop.call_soon_threadsafe(callback, signum)
    except RuntimeError:        # the loop has already closed
        pass

class _StopRoutes:
    """Routes the process's stop signals to every engine running in it.

    Signals belong to the process, not to one main() call, so each is hooked once, when the first
    engine on a loop starts, and handed back once, when the last one ends. Overlapping engines in one
    loop (one main() per IPO) all take a supervisor's stop, and the host gets back what it had.

    * While any engine runs, it owns the signal exclusively: a host callback such as ``loop.stop``
      must not cut an orderly shutdown short, so the host's own handling is displaced, not chained.
    * Stock asyncio exposes its loop callbacks, so the host's own ``loop.add_signal_handler`` callback
      is re-registered afterwards. Whatever the host set while engines ran (a loop callback, a plain
      handler, SIG_IGN) is what is handed back, and an engine that starts after such a change takes
      the signal back for its own run (one the host set to SIG_IGN stays ignored: the engine joins and is
      reached once the host restores it).
    * uvloop's callbacks cannot be read back. A host callback it held is lost with a warning, and the
      signal is back at its default: never left routed to a loop entry that no longer exists.
    * A loop that cannot own signals (Windows) gets a plain handler that hands the signal over
      thread-safely, replaced by the previous one afterwards.
    * A signal the parent ignored (nohup) stays ignored. A plain handler is re-installed only if it
      is not already in place, which keeps the SA_RESTART flag asyncio's own handler relies on.
    """
    def __init__(self):
        self.routes: Dict[int, dict] = {}

    def _dispatch(self, signum: int) -> None:
        route = self.routes.get(signum)
        for callback in list(route['callbacks'] if route else ()):
            callback(signum)

    def _owned(self, route: dict, signum: int) -> bool:
        """Whether ``signum`` still reaches _dispatch: the host has not re-registered, removed or replaced it."""
        current = signal.getsignal(signum)
        if route['handler'] is not None:
            return current is route['handler']
        if current is not route['installed']:
            return False
        handlers = route['handlers']
        entry = handlers.get(signum) if handlers is not None else None
        return handlers is None or (entry is not None and entry._callback == self._dispatch)

    def _take(self, loop: asyncio.AbstractEventLoop, signum: int) -> Optional[dict]:
        previous = signal.getsignal(signum)
        if previous == signal.SIG_IGN:
            return None
        handlers = getattr(loop, "_signal_handlers", None)
        route = {'loop': loop, 'callbacks': [], 'previous': previous, 'handler': None, 'installed': None,
                 'lost': False, 'handlers': handlers if isinstance(handlers, dict) else None,
                 'prior': handlers.get(signum) if isinstance(handlers, dict) else None}
        try:
            loop.add_signal_handler(signum, self._dispatch, signum)
            route['installed'] = signal.getsignal(signum)
        except (NotImplementedError, RuntimeError, ValueError):
            handler = functools.partial(_deliver_signal, loop, self._dispatch)
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):                 # not the main thread, or not supported here
                return None
            route['handler'] = handler
        return route

    def hook(self, loop: asyncio.AbstractEventLoop, callback: Callable[[int], None]) -> List[int]:
        """Route every stop signal this loop can take to ``callback``; returns the signals hooked."""
        hooked = []
        for signum in STOP_SIGNALS:
            route = self.routes.get(signum)
            if route is not None and route['loop'].is_closed():
                del self.routes[signum]                       # a loop that ended without handing back
                route = None
            if route is not None and route['loop'] is not loop:
                continue                                      # another live loop owns this signal
            if route is None or not self._owned(route, signum):
                # New, or the host took it while engines ran: (re)take it. What the host set is handed back.
                fresh = self._take(loop, signum)
                if fresh is None and route is None:
                    continue
                if fresh is not None:
                    if route is not None:
                        # Running engines carry over; one held after main() returned does not.
                        fresh['callbacks'] = [c for c in route['callbacks'] if not getattr(c, 'held', False)]
                        if fresh['prior'] is not None and fresh['prior']._callback == self._dispatch:
                            fresh['prior'] = route['prior']      # only the process-level handler was changed
                        fresh['lost'] = route['lost'] or (route['handlers'] is None and
                                                          getattr(route['previous'], "__self__", None) is loop)
                    self.routes[signum] = route = fresh
                # else ignored for now (SIG_IGN set mid-run): join, so a hand-back or a later re-take reaches us
            route['callbacks'].append(callback)
            hooked.append(signum)
        return hooked

    def unhook(self, loop: asyncio.AbstractEventLoop, callback: Callable[[int], None], hooked: List[int]) -> None:
        """Stop routing to ``callback``; the last engine out hands each signal back."""
        for signum in hooked:
            route = self.routes.get(signum)
            if route is None or route['loop'] is not loop:
                continue
            route['callbacks'] = [c for c in route['callbacks'] if c is not callback]
            if route['callbacks']:
                continue
            del self.routes[signum]
            previous = route['previous']
            if route['handler'] is not None:                  # a plain handler (no loop support)
                if signal.getsignal(signum) is route['handler'] and previous is not None:
                    signal.signal(signum, previous)
                continue
            current = signal.getsignal(signum)
            handlers = route['handlers']
            if handlers is not None:
                entry = handlers.get(signum)
                if entry is None or entry._callback != self._dispatch:
                    continue                                  # the host re-registered or removed it
            elif current is not route['installed'] and getattr(current, "__self__", None) is loop:
                continue                                      # uvloop: the host re-registered it
            prior = route['prior']
            unreadable = handlers is None and getattr(previous, "__self__", None) is loop   # uvloop
            lost = unreadable or route['lost']
            if current is not route['installed'] and handlers is not None:
                # asyncio, and the host set a plain handler (or SIG_IGN) mid-run: only our table entry is under
                # it, so that is all that changes. The handler stays exactly as set, flags included.
                if prior is not None:
                    handlers[signum] = prior
                else:
                    del handlers[signum]
                    if not handlers:
                        signal.set_wakeup_fd(-1)
            else:
                if prior is not None:                         # re-armed with its wakeup fd
                    loop.add_signal_handler(signum, prior._callback, *prior._args)
                else:
                    loop.remove_signal_handler(signum)
                if current is not route['installed']:
                    # uvloop: its table cannot be read, so our entry is removed and the host's mid-run handler
                    # put back (a stale entry would make a later loop.remove_signal_handler reset it).
                    if current is not None:
                        signal.signal(signum, current)
                elif not unreadable and previous is not None and previous is not signal.getsignal(signum):
                    signal.signal(signum, previous)           # a live plain handler beside a loop entry
            if lost:
                where = "back at its default" if current is route['installed'] and unreadable else \
                    "left as the host set it mid-run"
                logger.warning(f"Signal {signum}: the host's own {type(loop).__name__} callback for it cannot be "
                               f"read back, so the signal is {where}. Re-register it after main().")

    def release(self, loop: asyncio.AbstractEventLoop) -> None:
        """Forget a closed loop's routes (its handlers went with it)."""
        for signum in [s for s, r in self.routes.items() if r['loop'] is loop]:
            del self.routes[signum]

_STOP_ROUTES = _StopRoutes()

async def main(argv: Optional[List[str]] = None, hold_signals: bool = False) -> int:
    """The engine. With ``hold_signals`` (run() does this), stop signals stay routed to the
    ignoring shutdown handler after main() returns, until the caller restores them."""
    args = build_arg_parser().parse_args(argv)
    logger.info("=== INITIALIZING INSTITUTIONAL ENGINE ===")

    problem = _config_error(args)
    if problem:
        logger.critical(problem)
        return 2
    if args.expect_ip and not await verify_hardware_ip(args.expect_ip):
        return 1

    # 1. Map the Reality (Historical Data Sync)
    kite_creds = None
    if args.source == "kite":
        kite_creds = (os.environ.get("KITE_API_KEY"), os.environ.get("KITE_ACCESS_TOKEN"))
        if not all(kite_creds):
            logger.critical("Set KITE_API_KEY and KITE_ACCESS_TOKEN for --source kite.")
            return 2
        broker_adapter: BrokerAdapter = ZerodhaKiteAdapter(*kite_creds)
        as_of = datetime.now(timezone.utc)
    elif args.source == "csv":
        broker_adapter = CsvReplayAdapter({args.symbol: args.csv})
        try:
            last_bar = CsvReplayAdapter.load(args.csv).index[-1]
        except Exception as e:
            logger.critical(f"Cannot read bars from {args.csv}: {e!r}")
            return 1
        as_of = (last_bar + pd.Timedelta(minutes=BAR_MINUTES)).to_pydatetime()
    else:
        broker_adapter = PublicExchangeAdapter()
        as_of = datetime.now(timezone.utc)

    orchestrator = ProductionOrchestrator({args.symbol: args.listing_date}, broker_adapter, as_of=as_of,
                                          max_lookback_days=args.max_lookback_days,
                                          allow_partial_history=args.allow_partial_history)

    if not await orchestrator.build_the_ground():
        logger.critical("Failed to build historical context. Aborting.")
        return 1

    # 2. Spin up Core Engine Components
    loop = asyncio.get_running_loop()
    oms_queue = asyncio.Queue()
    history = orchestrator.market_state[args.symbol]

    # The synthetic tape never shares a run with a live feed. Simulated runs keep time by the tape
    # itself, so the demo behaves the same whatever the wall clock says.
    simulate = not args.no_simulate and not args.live_feed
    if args.live_feed and not args.no_simulate:
        logger.info("Live feed selected: the synthetic breakout tape is disabled.")
    tape_start = next_session_open(history.index[-1]) if simulate else None
    market_clock = (lambda: ticker.market_time or tape_start) if simulate else None

    alpha = AlphaEngine(rvol_threshold=args.rvol_threshold, risk_reward_ratio=args.risk_reward,
                        rvol_mode=args.rvol_mode, base_sessions=args.base_sessions)
    gateway: OrderGateway = PaperGateway()
    if args.live_orders:
        logger.warning("LIVE ORDERS ENABLED: signals will place real Zerodha orders.")
        gateway = KiteOrderGateway(broker_adapter.kite, tick_size=broker_adapter.tick_size(args.symbol))
    router_clock = market_clock or exchange_clock(lambda: ticker)
    oms = ExecutionRouter(oms_queue, risk_per_trade=args.risk_per_trade, max_position_value=args.max_position_value,
                          gateway=gateway, tick_size=broker_adapter.tick_size(args.symbol), clock=router_clock)
    # A live feed's holes are back-filled from the broker, strictly: a failed fetch must not look
    # like "nothing traded in the hole".
    ticker = LiveTickAdapter(orchestrator.market_state, alpha, oms_queue, loop, token_map=broker_adapter.token_map(),
                             started_at=tape_start - timedelta(seconds=1) if simulate else None,
                             bar_listeners=[oms.on_bar], backfill=broker_adapter.backfill if args.live_feed else None,
                             require_feed_liveness=args.live_feed, clock=steady_clock() if args.live_feed else None)

    # 3. Launch Async Coroutines
    tick_worker = asyncio.create_task(ticker.process_ticks(), name="tick-aggregator")
    clock_worker = asyncio.create_task(ticker.bar_clock(clock=market_clock), name="bar-clock")
    oms_worker = asyncio.create_task(oms.process_orders(), name="oms-router")
    workers = [tick_worker, clock_worker, oms_worker]
    helpers = []
    if simulate:
        # ⚠️ Starts the dynamic simulation pushing synthetic ticks into the system
        helpers.append(asyncio.create_task(
            simulate_live_market(ticker, args.symbol, history, alpha.base_high(history), t0=tape_start)))
    feed = None
    if args.live_feed:
        feed_dead = asyncio.Event()
        workers.append(asyncio.create_task(_watch_feed(feed_dead, ticker), name="kite-feed"))
        tokens = [t for t, s in broker_adapter.token_map().items() if s in orchestrator.market_state]
        feed = start_kite_feed(*kite_creds, tokens, ticker, feed_dead)

    exit_code, stop_signal, shutting_down = 0, None, False
    runtime = asyncio.create_task(asyncio.sleep(args.run_seconds) if args.run_seconds > 0 else asyncio.Event().wait())

    def on_stop_signal(signum: int):
        nonlocal stop_signal
        if shutting_down or stop_signal is not None:
            logger.warning(f"Signal {signum} received; shutdown already in progress, signal ignored.")
            return
        stop_signal = signum
        oms.accepting, gateway.stopping = False, True    # before anything else can run
        logger.warning(f"Signal {signum} received: shutting down in order.")
        runtime.cancel()

    # Ctrl-C, `kill`/systemd/docker stop and a closed terminal all take the orderly path below, so an
    # entry in flight is always settled. _StopRoutes honours nohup and hands every signal back afterwards.
    hooked = _STOP_ROUTES.hook(loop, on_stop_signal)

    try:
        try:
            # Workers loop forever; one finishing early means it crashed, and the engine must not run half-blind.
            done, _ = await asyncio.wait({runtime, *workers}, return_when=asyncio.FIRST_COMPLETED)
            for t in workers:
                if t in done:
                    exit_code = 1
                    logger.critical(f"Worker '{t.get_name()}' stopped unexpectedly: {t.exception()!r}. Shutting down.")
        except asyncio.CancelledError:
            stop_signal = stop_signal or getattr(signal, "SIGINT", 2)   # cancelled from outside (no handler)
        finally:
            shutting_down = True
            oms.accepting, gateway.stopping = False, True    # before the first await
            # 1. Stop everything that can produce ticks or signals.
            if feed is not None:
                feed.close()
            for t in [runtime, *helpers, tick_worker, clock_worker, *workers[3:]]:
                t.cancel()
            await asyncio.gather(runtime, *helpers, tick_worker, clock_worker, *workers[3:], return_exceptions=True)
            await ticker.cancel_backfills()
            if ticker.tick_queue.qsize():
                logger.info(f"{ticker.tick_queue.qsize()} queued ticks discarded at shutdown.")
            # 2. Let the order in flight settle: the gateway stops waiting for fills, cancels what is
            #    working and protects what filled. An entry that may exist at the broker runs as a
            #    shielded task, so even interrupting the router cannot abandon it.
            try:
                await asyncio.wait_for(oms_queue.join(), timeout=oms.settle_timeout)
            except asyncio.TimeoutError:
                exit_code = 1
                logger.critical(f"The order router did not settle within {oms.settle_timeout:.0f}s; "
                                f"waiting for the entry in flight to finish on its own deadlines.")
            oms_worker.cancel()
            await asyncio.gather(oms_worker, return_exceptions=True)
            await gateway.wait_inflight()
        return _halt_report(oms, gateway, ticker, exit_code, stop_signal)
    finally:
        if hold_signals:
            on_stop_signal.held = True        # still routed; dropped once the host takes the signal back
        else:
            _STOP_ROUTES.unhook(loop, on_stop_signal, hooked)

def _halt_report(oms: "ExecutionRouter", gateway: OrderGateway, ticker: LiveTickAdapter, exit_code: int,
                 stop_signal: Optional[int]) -> int:
    logger.info("=== SYSTEM HALT ===")
    logger.info(f"Final Inventory State: {oms.active_inventory or '{}'}")
    for f in oms.fills + gateway.late_fills:
        exits = f.exit_order_id or ("simulated OCO" if gateway.simulates_exits else "NONE")
        logger.info(f"   Fill {f.symbol}: {f.quantity} @ {f.average_price:.2f} (order {f.order_id}, exits: {exits})")
    for pos in oms.positions.values():
        state = " | STOP TRIGGERED, LIMIT UNFILLED" if pos.stop_triggered else ""
        logger.info(f"   Open {pos.symbol}: {pos.quantity} @ {pos.entry_price:.2f} | stop {pos.stop_loss:.2f} | "
                    f"target {pos.target:.2f}{state}")
    for f in gateway.late_fills:
        logger.info(f"   Open {f.symbol}: {f.quantity} @ {f.average_price:.2f} | filled during shutdown | "
                    f"exits: {f.exit_order_id or 'NONE'}")
    for pos in oms.closed_positions:
        logger.info(f"   Closed {pos.symbol}: {pos.exit_reason} @ {pos.exit_price:.2f} | P&L ₹{pos.pnl:,.0f}")
    if ticker.dropped_ticks:
        logger.info(f"   {ticker.dropped_ticks} ticks dropped (unknown instrument, off-session, late, or malformed).")
    attention = oms.unresolved + gateway.alerts
    for line in attention:
        logger.critical(f"   ATTENTION: {line}")
    # Failures outrank a stop signal: a supervisor keyed on exit 1 must not miss one.
    if attention or exit_code:
        return 1
    return 128 + int(stop_signal) if stop_signal is not None else 0

def run(argv: Optional[List[str]] = None) -> int:
    """CLI entry. Stop signals stay owned by the engine until asyncio.run has finished its teardown
    (which can wait seconds for a worker thread), so a late signal cannot replace main()'s result."""
    configure_logging()
    saved = {signum: signal.getsignal(signum) for signum in STOP_SIGNALS}
    result: List[int] = []
    loops: List[asyncio.AbstractEventLoop] = []

    async def engine_main() -> int:
        loops.append(asyncio.get_running_loop())
        result.append(await main(argv, hold_signals=True))
        return result[0]

    try:
        return asyncio.run(engine_main())
    except KeyboardInterrupt:
        if result:
            return result[0]                  # main() had finished; only the teardown was interrupted
        logger.info("Graceful exit invoked by Operator.")
        return 130
    finally:
        for loop in loops:
            _STOP_ROUTES.release(loop)
        for signum, previous in saved.items():
            if previous is not None:
                try:
                    signal.signal(signum, previous)
                except ValueError:      # not the main thread: main() hooked nothing to restore
                    pass

if __name__ == "__main__":
    sys.exit(run())
