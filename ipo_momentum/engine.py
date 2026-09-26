"""
====================================================================================
INSTITUTIONAL QUANTITATIVE ENGINE - IPO MOMENTUM & LIVE EXECUTION (V1.1)
====================================================================================
Architecture:
1. Data Harmonization (Historical Reality Sync via REST, or offline CSV replay)
2. Live Tick Ingestion (Thread-Safe In-Memory Synthesizer + session bar clock)
3. Vectorized Alpha Engine (AVWAP, RVOL, Base Breakouts)
4. Execution Router (Fixed-Risk Sizing, Notional Cap, Paper or Zerodha Execution)

Quick start (see README.md):
    python engine.py                 # Yahoo history + simulated breakout, paper fills
    python engine.py --source csv    # bundled real SWIGGY 5m bars, fully offline
    python engine.py --source kite   # Zerodha history (KITE_API_KEY, KITE_ACCESS_TOKEN)

Paper execution is the default. Real orders require --source kite --live-orders.
Nothing here is investment advice.
====================================================================================
"""

import argparse
import asyncio
import ipaddress
import json
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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
SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 30)
DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "SWIGGY_5m_2026-09-08_2026-09-25.csv"
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
    """Start of the ``minutes``-wide bar containing ``ts`` (bars are aligned to the hour)."""
    return ts.replace(minute=ts.minute - ts.minute % minutes, second=0, microsecond=0)


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
    df = df[OHLCV].apply(pd.to_numeric, errors="coerce").astype("float64")
    df = df[df["Close"].notna()].copy()
    for col in ["Open", "High", "Low"]:
        df[col] = df[col].fillna(df["Close"])
    df["Volume"] = df["Volume"].fillna(0.0)
    df.index = df.index.tz_localize(IST) if df.index.tz is None else df.index.tz_convert(IST)
    df.index.name = "datetime"
    return df[~df.index.duplicated(keep="last")].sort_index()


def drop_incomplete_bars(df: pd.DataFrame, as_of: datetime, minutes: int) -> pd.DataFrame:
    """Drop bars still forming at ``as_of``; the live synthesizer owns those buckets."""
    return df[df.index + pd.Timedelta(minutes=minutes) <= pd.Timestamp(as_of)]


# ==============================================================================
# 2. DATA HARMONIZATION & BROKER ADAPTERS
# ==============================================================================
def is_permanent_error(e: BaseException) -> bool:
    """Errors a retry cannot fix: HTTP 4xx other than 429, and Kite auth/permission/input errors."""
    if isinstance(e, urllib.error.HTTPError):
        return 400 <= e.code < 500 and e.code != 429
    return type(e).__name__ in ("TokenException", "PermissionException", "InputException")

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

    Callers reserve start times in arrival order (asyncio.Lock is FIFO), so no waiter can
    be starved by later arrivals.
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

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        token = self._instrument_cache.get(symbol)
        if not token:
            logger.error(f"[{symbol}] Unknown {self.exchange} tradingsymbol (did boot() run?).")
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
    def __init__(self, suffix: str = ".NS"):
        self.suffix = suffix
        self.limiter = TokenBucketRateLimiter(max_calls=3, period=1.1)

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        logger.info(f"[{symbol}] Fetching public exchange prints via direct HTTP...")
        cutoff = datetime.now(timezone.utc) - timedelta(days=59)  # Yahoo serves 5m bars for ~60 days
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

        chart = (data or {}).get('chart') or {}
        if chart.get('error') or not chart.get('result'):
            logger.error(f"[{symbol}] Yahoo returned no chart data: {chart.get('error')}")
            return self._empty_map()
        res = chart['result'][0]
        if not res.get('timestamp'):
            logger.error(f"[{symbol}] Yahoo chart has no bars in the requested window.")
            return self._empty_map()

        quote = res['indicators']['quote'][0]
        n = len(res['timestamp'])
        df = pd.DataFrame({col: quote.get(col.lower()) or [None] * n for col in OHLCV},
                          index=pd.to_datetime(res['timestamp'], unit='s', utc=True))
        minutes = INTERVAL_MINUTES.get(interval, BAR_MINUTES)
        df = harmonize_bars(df)
        # Yahoo can append an off-grid "live" row (e.g. 15:29:59) and pre-open prints; neither is a bar.
        t = df.index
        on_grid = (t.second == 0) & (t.minute % minutes == 0) & (t.time >= SESSION_OPEN) & (t.time < SESSION_CLOSE)
        return drop_incomplete_bars(df[on_grid], end_date, minutes)

class CsvReplayAdapter(BrokerAdapter):
    """Offline source: real bars from CSV files (datetime_ist, open, high, low, close, volume).

    ``datetime_ist`` is the bar start as naive IST wall-clock time (see data/README.md).
    """
    def __init__(self, files: Dict[str, Path]):
        self.files = {sym: Path(p) for sym, p in files.items()}

    @staticmethod
    def load(path: Path) -> pd.DataFrame:
        raw = pd.read_csv(path)
        df = raw.rename(columns={c: c.capitalize() for c in ["open", "high", "low", "close", "volume"]})
        df.index = pd.DatetimeIndex(pd.to_datetime(raw["datetime_ist"])).tz_localize(IST)
        return harmonize_bars(df)

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        path = self.files.get(symbol)
        if path is None or not path.exists():
            logger.error(f"[{symbol}] No CSV file for symbol (looked for {path}).")
            return self._empty_map()
        df = await asyncio.to_thread(self.load, path)
        window = df[(df.index >= pd.Timestamp(start_date)) & (df.index < pd.Timestamp(end_date))]
        logger.info(f"[{symbol}] Replaying {len(window)} bars from {path.name}.")
        return window

class ProductionOrchestrator:
    """Loads each IPO's history from its listing date.

    The base and AVWAP are anchored to the first bar, so a symbol whose history does not
    reach its listing (vendor limits, or listed more than ``max_lookback_days`` ago) has no
    IPO base: it is excluded unless ``allow_partial_history`` is set.
    """
    LISTING_TOLERANCE = pd.Timedelta(days=5)   # listing on a Friday + weekend + holidays

    def __init__(self, target_ipos: Dict[str, datetime], broker: BrokerAdapter,
                 as_of: Optional[datetime] = None, max_lookback_days: int = 180, allow_partial_history: bool = False):
        # Naive dates are IST (comparing naive with aware datetimes raises TypeError).
        self.watchlist = {s: d if d.tzinfo else d.replace(tzinfo=IST) for s, d in target_ipos.items()}
        self.broker = broker
        self.as_of = as_of
        self.max_lookback_days = max_lookback_days
        self.allow_partial_history = allow_partial_history
        self.market_state: Dict[str, pd.DataFrame] = {}

    async def build_the_ground(self) -> bool:
        await self.broker.boot()
        end_date = self.as_of or datetime.now(timezone.utc)

        for symbol, listing_date in self.watchlist.items():
            start_date = max(listing_date, end_date - timedelta(days=self.max_lookback_days))
            df = await self.broker.fetch_historical_bars(symbol, start_date, end_date, "5m")
            if df.empty:
                logger.error(f"[{symbol}] No historical bars acquired; symbol excluded.")
                continue
            gap = df.index[0] - pd.Timestamp(listing_date)
            if gap > self.LISTING_TOLERANCE:
                msg = (f"[{symbol}] History starts {df.index[0]:%Y-%m-%d}, {gap.days} days after the "
                       f"{listing_date:%Y-%m-%d} listing, so the IPO base and AVWAP anchor are unknown")
                if not self.allow_partial_history:
                    logger.error(f"{msg}; symbol excluded (--allow-partial-history to anchor at the first bar).")
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
    """IPO base breakout: the first close above the high of the first ``base_bars`` bars
    since listing, above the listing-anchored VWAP, on high relative volume.

    Every indicator is causal: the value on bar *t* uses bars <= *t* only, and the
    base is fully formed (``base_bars`` completed bars) before any breakout counts.
    """
    RVOL_MODES = ("trailing", "time_of_day")

    def __init__(self, rvol_threshold: float = 2.0, risk_reward_ratio: float = 3.0, base_bars: int = 150,
                 rvol_lookback: int = 20, atr_period: int = 14, atr_stop_multiple: float = 1.5,
                 rvol_mode: str = "trailing", rvol_sessions: int = 10):
        if rvol_mode not in self.RVOL_MODES:
            raise ValueError(f"rvol_mode must be one of {self.RVOL_MODES}")
        self.rvol_threshold = rvol_threshold
        self.rr_ratio = risk_reward_ratio
        self.base_bars = base_bars
        self.rvol_lookback = rvol_lookback
        self.rvol_mode = rvol_mode              # time_of_day: vs the same 5-minute slot of prior sessions
        self.rvol_sessions = rvol_sessions
        self.atr_period = atr_period
        self.atr_stop_multiple = atr_stop_multiple

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df[OHLCV].copy()
        typical = (df['High'] + df['Low'] + df['Close']) / 3.0
        cum_vol = df['Volume'].cumsum()
        df['AVWAP'] = (typical * df['Volume']).cumsum() / cum_vol.where(cum_vol > 0)
        # Relative volume against *preceding* bars only; the current bar is not in its own baseline.
        # A bar whose price moved on zero volume is a vendor gap, not a quiet bar: it is left out.
        vol = df['Volume'].where(~((df['Volume'] == 0) & (df['High'] > df['Low'])))
        min_obs = max(1, math.ceil(0.75 * self.rvol_lookback))
        vol_base = vol.rolling(self.rvol_lookback, min_periods=min_obs).mean().shift(1)
        if self.rvol_mode == "time_of_day":
            # Opening and closing bars are structurally heavy; compare each bar with its own slot.
            slot_base = vol.groupby(df.index.strftime('%H:%M')).transform(
                lambda s: s.shift(1).rolling(self.rvol_sessions, min_periods=2).mean())
            vol_base = slot_base.fillna(vol_base)   # young listings: trailing until slot history exists
        df['RVOL'] = df['Volume'] / vol_base.where(vol_base > 0)
        prev_close = df['Close'].shift(1)
        true_range = pd.concat([df['High'] - df['Low'], (df['High'] - prev_close).abs(),
                                (df['Low'] - prev_close).abs()], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(self.atr_period, min_periods=1).mean()
        df['Base_High'] = df['High'].iloc[:self.base_bars].max() if len(df) > self.base_bars else float('nan')
        df['Breakout'] = ((prev_close <= df['Base_High']) & (df['Close'] > df['Base_High'])
                          & (df['Close'] > df['AVWAP']) & (df['RVOL'] > self.rvol_threshold))
        df.loc[df.index[:self.base_bars], 'Breakout'] = False  # the base itself cannot break out
        return df

    def _signal(self, symbol: str, bar: pd.Series, bar_time: datetime) -> Signal:
        close = float(bar['Close'])
        # Risk is bounded by 1.5 ATR, or the AVWAP structural floor, whichever is closer.
        stop_loss = max(close - self.atr_stop_multiple * float(bar['ATR']), float(bar['AVWAP']))
        target = close + (close - stop_loss) * self.rr_ratio
        return Signal(symbol, close, stop_loss, target, "IPO_BASE_BREAKOUT", bar_time)

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) <= max(self.base_bars, self.rvol_lookback): return None

        ind = self.indicators(df)
        latest = ind.iloc[-1]
        if not bool(latest['Breakout']):
            return None
        logger.info(f"[{symbol}] 🟢 ALPHA TRIGGER: Base Breakout @ {latest['Close']:.2f} "
                    f"(base {latest['Base_High']:.2f}) | RVOL: {latest['RVOL']:.2f}x | AVWAP: {latest['AVWAP']:.2f}")
        return self._signal(symbol, latest, ind.index[-1])

    def scan(self, symbol: str, df: pd.DataFrame) -> List[Signal]:
        """Every bar on which evaluate() would have fired, in one vectorized pass (walk-forward)."""
        if len(df) <= max(self.base_bars, self.rvol_lookback): return []
        ind = self.indicators(df)
        return [self._signal(symbol, row, ts) for ts, row in ind[ind['Breakout']].iterrows()]

class LiveTickAdapter:
    """Turns broker ticks into completed 5-minute bars and evaluates each one.

    Threading: ``broker_on_ticks`` runs on the broker's websocket thread and only
    enqueues; all bar state is owned by the event loop (``process_ticks`` and
    ``bar_clock``), so no locks are needed.
    """
    def __init__(self, market_state: Dict[str, pd.DataFrame], alpha_engine: AlphaEngine, oms_queue: asyncio.Queue,
                 loop: asyncio.AbstractEventLoop, token_map: Optional[Dict[int, str]] = None,
                 bar_minutes: int = BAR_MINUTES, started_at: Optional[datetime] = None,
                 bar_listeners: Optional[List[Callable[[str, datetime, pd.Series], None]]] = None):
        self.market_state = market_state
        self.alpha = alpha_engine
        self.oms_queue = oms_queue
        self.loop = loop
        self.token_map = dict(token_map or {})
        self.bar_minutes = bar_minutes
        self.started_at = to_ist(started_at or datetime.now(IST))
        self.tick_queue = asyncio.Queue()
        self.current_bars: Dict[str, dict] = {}
        self.last_closed: Dict[str, datetime] = {}
        self._cum_volume: Dict[str, Tuple[object, int]] = {}
        self.dropped_ticks = 0
        self.bar_listeners = list(bar_listeners or [])
        self._unanchored: set = set()

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
            ts = datetime.now(IST)
        cumulative = t.get('volume_traded')
        volume = t.get('last_traded_quantity', t.get('volume', 0)) if cumulative is None else 0
        return Tick(symbol=symbol, price=float(price), volume=int(volume or 0), timestamp=ts,
                    cumulative_volume=None if cumulative is None else int(cumulative))

    def broker_on_ticks(self, ws, ticks: list):
        """Websocket Callback. Accepts Kite payloads (instrument_token, last_price, volume_traded,
        exchange_timestamp) and simple dicts (symbol, price, volume, timestamp)."""
        for t in ticks:
            tick = self._normalize(t)
            if tick is None:
                self.dropped_ticks += 1
                continue
            # Thread-safe dispatch from the broker's C-Thread to our Async Event Loop
            self.loop.call_soon_threadsafe(self.tick_queue.put_nowait, tick)

    def _traded_quantity(self, tick: Tick) -> int:
        """Quantity to add to the bar: per-tick volume, or the delta of the day's cumulative volume."""
        if tick.cumulative_volume is None:
            return max(0, tick.volume)
        day = tick.timestamp.date()
        prev_day, prev_cum = self._cum_volume.get(tick.symbol, (None, 0))
        if prev_day != day:
            # First print of the session for this symbol. If we were running at the open the counter
            # holds only this session's trades; if we joined mid-session it holds everything traded
            # before we started, which must not be dumped into the current bar.
            prev_cum = 0 if self.started_at <= datetime.combine(day, SESSION_OPEN, tzinfo=IST) else tick.cumulative_volume
        elif tick.cumulative_volume < prev_cum:
            prev_cum = 0  # counter restarted (feed reset)
        self._cum_volume[tick.symbol] = (day, tick.cumulative_volume)
        return tick.cumulative_volume - prev_cum

    def _open_bar(self, sym: str, bucket: datetime, price: float, volume: int) -> None:
        # The first bucket seen after start-up is incomplete if the engine started inside it.
        partial = sym not in self.last_closed and self.started_at > bucket
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
        # Floor the timestamp to the current 5-minute block
        boundary = bar_floor(tick.timestamp, self.bar_minutes)
        if not SESSION_OPEN <= boundary.time() < SESSION_CLOSE:
            # Pre-open auction and post-close prints are not continuous-session bars. Dropping them
            # before the volume baseline moves puts auction volume in the 09:15 bar, as brokers do.
            self.dropped_ticks += 1
            return
        volume = self._traded_quantity(tick)

        if sym in self.last_closed and boundary <= self.last_closed[sym]:
            self.dropped_ticks += 1  # late print for a bar that is already closed
            return
        active = self.current_bars.get(sym)
        if active is None:
            self._open_bar(sym, boundary, tick.price, volume)
        elif boundary > active['timestamp']:
            self.close_bar(sym)
            self._open_bar(sym, boundary, tick.price, volume)
        elif boundary < active['timestamp']:
            self.dropped_ticks += 1  # out-of-order print from an earlier bucket
        else:
            active['High'] = max(active['High'], tick.price)
            active['Low'] = min(active['Low'], tick.price)
            active['Close'] = tick.price
            active['Volume'] += volume

    def close_bar(self, sym: str) -> None:
        bar = self.current_bars.pop(sym)
        idx = bar['timestamp']
        self.last_closed[sym] = idx
        if bar['partial']:
            logger.info(f"[{sym}] Discarding partial {idx:%H:%M} bar (engine started mid-bar).")
            return

        history = self.market_state.get(sym)
        if history is None:
            history = empty_bars()
        if len(history) and idx <= history.index[-1]:
            logger.warning(f"[{sym}] Live bar {idx} overlaps history (last {history.index[-1]}); keeping history.")
            return
        row = pd.DataFrame([[float(bar[c]) for c in OHLCV]], columns=OHLCV,
                           index=pd.DatetimeIndex([idx], name=history.index.name))
        self.market_state[sym] = history = pd.concat([history, row]) if len(history) else row
        logger.info(f"📊 [{sym}] 5m Bar Closed {idx:%Y-%m-%d %H:%M} | O: {bar['Open']:.2f} H: {bar['High']:.2f} "
                    f"L: {bar['Low']:.2f} C: {bar['Close']:.2f} | V: {bar['Volume']:,}")

        for listener in self.bar_listeners:
            try:
                listener(sym, idx, row.iloc[0])
            except Exception:
                logger.exception(f"[{sym}] Bar listener failed on the {idx:%Y-%m-%d %H:%M} bar.")

        # Immediately evaluate the fully formed historical bar. A failing evaluation must not
        # abort bar bookkeeping, or the tick that triggered this close would be lost too.
        try:
            signal = self.alpha.evaluate(sym, history)
        except Exception:
            logger.exception(f"[{sym}] Alpha evaluation failed on the {idx:%Y-%m-%d %H:%M} bar.")
            return
        if signal:
            self.oms_queue.put_nowait(signal)

    def flush_due_bars(self, now: datetime, grace: timedelta = timedelta(seconds=2)) -> None:
        """Close bars whose bucket has ended; without this, a bar waits for the *next* tick,
        which never comes for an illiquid name or the session's final bar."""
        now = to_ist(now)
        for sym in [s for s, b in self.current_bars.items()
                    if b['timestamp'] + timedelta(minutes=self.bar_minutes) + grace <= now]:
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
        clock = clock or (lambda: datetime.now(IST))
        while True:
            await asyncio.sleep(interval)
            try:
                self.flush_due_bars(clock())
            except Exception:
                logger.exception("Bar clock flush failed.")


# ==============================================================================
# 4. EXECUTION ROUTER (OMS)
# ==============================================================================
class OrderGateway(ABC):
    simulates_exits = False                     # True: the engine itself must play the stop/target

    @abstractmethod
    async def execute(self, plan: OrderPlan) -> Optional[Fill]:
        """Enter the position and attach its stop/target exits; None if nothing was bought."""

class PaperGateway(OrderGateway):
    """Simulated execution: fills at the signal price, sends nothing anywhere.
    Its stop/target OCO is played by ExecutionRouter.on_bar against closed bars."""
    simulates_exits = True

    def __init__(self, latency: float = 0.3):
        self.latency = latency
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
    """
    def __init__(self, kite, exchange: str = "NSE", product: str = "CNC", fill_timeout: float = 30.0,
                 poll_interval: float = 1.0, stop_limit_buffer: float = 0.005, tick_size: float = 0.05):
        self.kite = kite
        self.exchange = exchange
        self.product = product
        self.fill_timeout = fill_timeout
        self.poll_interval = poll_interval
        self.stop_limit_buffer = stop_limit_buffer
        self.tick_size = tick_size

    async def _order_state(self, order_id: str) -> dict:
        history = await asyncio.to_thread(self.kite.order_history, order_id)
        return history[-1] if history else {}

    async def _await_terminal(self, order_id: str) -> dict:
        deadline = time.monotonic() + self.fill_timeout
        while True:
            state = await self._order_state(order_id)
            if state.get('status') in (self.kite.STATUS_COMPLETE, self.kite.STATUS_REJECTED, self.kite.STATUS_CANCELLED):
                return state
            if time.monotonic() >= deadline:
                logger.warning(f"Entry order {order_id} not filled in {self.fill_timeout:.0f}s; cancelling remainder.")
                try:
                    await asyncio.to_thread(self.kite.cancel_order, self.kite.VARIETY_REGULAR, order_id)
                except Exception as e:
                    logger.error(f"Cancel of {order_id} failed: {e!r}")
                return await self._order_state(order_id)
            await asyncio.sleep(self.poll_interval)

    async def execute(self, plan: OrderPlan) -> Optional[Fill]:
        k, sym = self.kite, plan.signal.symbol
        # The market may have moved since the bar closed: never buy below the stop or chase above the limit.
        key = f"{self.exchange}:{sym}"
        ltp = float((await asyncio.to_thread(k.ltp, key))[key]["last_price"])
        if not plan.stop_loss < ltp <= plan.entry_limit:
            logger.warning(f"[{sym}] Entry skipped: LTP {ltp:.2f} outside ({plan.stop_loss:.2f}, {plan.entry_limit:.2f}].")
            return None
        order_id = await asyncio.to_thread(
            k.place_order, variety=k.VARIETY_REGULAR, exchange=self.exchange, tradingsymbol=sym,
            transaction_type=k.TRANSACTION_TYPE_BUY, quantity=plan.quantity, product=self.product,
            order_type=k.ORDER_TYPE_LIMIT, price=plan.entry_limit, validity=k.VALIDITY_DAY, tag="ipomomentum")
        logger.info(f"[{sym}] Entry order {order_id} placed: BUY {plan.quantity} LIMIT {plan.entry_limit:.2f}")

        state = await self._await_terminal(order_id)
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
        try:
            gtt = await asyncio.to_thread(
                k.place_gtt, trigger_type=k.GTT_TYPE_OCO, tradingsymbol=sym, exchange=self.exchange,
                trigger_values=[plan.stop_loss, plan.target], last_price=avg, orders=legs)
            trigger_id = str(gtt['trigger_id'])
            logger.info(f"🛡️ [{sym}] GTT OCO {trigger_id} armed: stop {plan.stop_loss:.2f} / target {plan.target:.2f}")
        except Exception as e:
            trigger_id = None
            logger.critical(f"[{sym}] POSITION OPEN WITHOUT EXITS: {filled} shares bought but GTT failed: {e!r}")
        return Fill(sym, filled, avg, str(order_id), trigger_id)

class ExecutionRouter:
    def __init__(self, oms_queue: asyncio.Queue, risk_per_trade: float, max_position_value: Optional[float] = None,
                 gateway: Optional[OrderGateway] = None, tick_size: float = 0.05, max_entry_slippage: float = 0.005,
                 max_signal_age: Optional[timedelta] = timedelta(seconds=60),
                 clock: Optional[Callable[[], datetime]] = None, bar_minutes: int = BAR_MINUTES):
        self.oms_queue = oms_queue
        self.risk_per_trade = risk_per_trade       # rupees lost if the stop is hit at its trigger
        self.max_position_value = max_position_value
        self.gateway = gateway or PaperGateway()
        self.tick_size = tick_size
        self.max_entry_slippage = max_entry_slippage
        self.max_signal_age = max_signal_age       # None disables the staleness check
        self.clock = clock or (lambda: datetime.now(IST))
        self.bar_minutes = bar_minutes
        self.active_inventory = set()              # symbols with a pending or open position
        self.fills: List[Fill] = []
        self.positions: Dict[str, Position] = {}   # open positions
        self.closed_positions: List[Position] = []

    def on_bar(self, sym: str, bar_time: datetime, bar: pd.Series) -> None:
        """Play a simulated OCO against a closed bar (paper only; a live broker holds real exits).

        A stop that the bar gaps through fills at the open; if one bar touches both
        levels the stop is assumed to have come first.
        """
        pos = self.positions.get(sym)
        if pos is None or not self.gateway.simulates_exits or pd.Timestamp(bar_time) < pd.Timestamp(pos.opened_at):
            return
        if bar['Low'] <= pos.stop_loss:
            pos.exit_price, pos.exit_reason = min(pos.stop_loss, float(bar['Open'])), "STOP"
        elif bar['High'] >= pos.target:
            pos.exit_price, pos.exit_reason = max(pos.target, float(bar['Open'])), "TARGET"
        else:
            return
        pos.closed_at = bar_time
        del self.positions[sym]
        self.closed_positions.append(pos)
        self.active_inventory.discard(sym)
        logger.info(f"🏁 [{sym}] PAPER EXIT {pos.exit_reason}: {pos.quantity}x @ {pos.exit_price:.2f} "
                    f"on the {to_ist(bar_time):%Y-%m-%d %H:%M} bar | P&L ₹{pos.pnl:,.0f}")

    def is_stale(self, signal: Signal) -> bool:
        """A signal is only actionable right after its bar closed (e.g. not at the next day's open)."""
        if self.max_signal_age is None or signal.bar_time is None:
            return False
        bar_close = to_ist(signal.bar_time) + timedelta(minutes=self.bar_minutes)
        return to_ist(self.clock()) - bar_close > self.max_signal_age

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
        if sym in self.active_inventory:
            logger.info(f"[{sym}] Signal ignored: position already pending/open.")
            return
        if self.is_stale(signal):
            logger.warning(f"[{sym}] Signal from the {to_ist(signal.bar_time):%Y-%m-%d %H:%M} bar is stale; not trading it.")
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
        except Exception:
            self.active_inventory.discard(sym)
            raise
        if fill is None:
            self.active_inventory.discard(sym)
            return
        self.fills.append(fill)
        opened_at = (signal.bar_time + timedelta(minutes=self.bar_minutes)) if signal.bar_time else self.clock()
        self.positions[sym] = Position(sym, fill.quantity, fill.average_price, plan.stop_loss, plan.target, opened_at)


# ==============================================================================
# 5. DYNAMIC MARKET SIMULATOR (FOR OUT-OF-BOX DEMONSTRATION)
# ==============================================================================
async def simulate_live_market(tick_adapter: LiveTickAdapter, sym: str, df: pd.DataFrame,
                               base_bars: int = 150, pause: float = 1.0):
    """Synthetic next-session tape that breaks the real historical base on heavy volume.

    Prices and volumes are invented; only the base high and last close come from data.
    """
    await asyncio.sleep(pause)

    base_high = float(df['High'].iloc[:base_bars].max())
    last_close = float(df['Close'].iloc[-1])
    t0 = next_session_open(df.index[-1])
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
def start_kite_feed(api_key: str, access_token: str, tokens: List[int], tick_adapter: LiveTickAdapter):
    """Stream full-mode ticks (with cumulative volume and exchange time) into the synthesizer."""
    from kiteconnect import KiteTicker
    kws = KiteTicker(api_key, access_token)
    kws.on_ticks = tick_adapter.broker_on_ticks

    def on_connect(ws, response):
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        logger.info(f"Kite websocket connected; streaming {len(tokens)} instruments.")

    kws.on_connect = on_connect
    kws.on_error = lambda ws, code, reason: logger.error(f"Kite websocket error {code}: {reason}")
    kws.connect(threaded=True)
    return kws

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IPO momentum engine: history sync, bar synthesis, breakout alpha, execution.")
    p.add_argument("--source", choices=["yahoo", "csv", "kite"], default="yahoo", help="historical data source (default: yahoo)")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="bar file for --source csv")
    p.add_argument("--symbol", default="SWIGGY", help="NSE tradingsymbol (default: SWIGGY)")
    p.add_argument("--listing-date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=IST),
                   help="IPO listing date YYYY-MM-DD (default: 20 days before the data's as-of time)")
    p.add_argument("--risk-per-trade", type=float, default=15_000.0, help="rupees lost if the stop is hit")
    p.add_argument("--max-position-value", type=float, default=1_000_000.0, help="rupee cap on a position's notional")
    p.add_argument("--rvol-threshold", type=float, default=2.0)
    p.add_argument("--rvol-mode", choices=AlphaEngine.RVOL_MODES, default="trailing",
                   help="volume baseline: previous 20 bars, or the same slot in prior sessions")
    p.add_argument("--risk-reward", type=float, default=3.0)
    p.add_argument("--run-seconds", type=float, default=6.0, help="how long to run; 0 = until Ctrl-C")
    p.add_argument("--no-simulate", action="store_true", help="do not inject the synthetic breakout tape")
    p.add_argument("--allow-partial-history", action="store_true",
                   help="trade symbols whose history does not reach the listing (base anchored at first bar)")
    p.add_argument("--live-feed", action="store_true", help="stream Kite websocket ticks (needs --source kite)")
    p.add_argument("--live-orders", action="store_true", help="send REAL orders to Zerodha (needs --source kite)")
    p.add_argument("--expect-ip", help="abort unless the public IP equals this (required with --live-orders)")
    return p

async def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logger.info("=== INITIALIZING INSTITUTIONAL ENGINE ===")

    if (args.live_orders or args.live_feed) and args.source != "kite":
        logger.critical("--live-orders/--live-feed require --source kite.")
        return 2
    if args.live_orders and not args.expect_ip:
        logger.critical("--live-orders requires --expect-ip <the static IP registered with the broker>.")
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
        except (OSError, ValueError, KeyError, IndexError) as e:
            logger.critical(f"Cannot read bars from {args.csv}: {e!r}")
            return 1
        as_of = (last_bar + pd.Timedelta(minutes=BAR_MINUTES)).to_pydatetime()
    else:
        broker_adapter = PublicExchangeAdapter()
        as_of = datetime.now(timezone.utc)

    listing_date = args.listing_date or (as_of - timedelta(days=20))
    orchestrator = ProductionOrchestrator({args.symbol: listing_date}, broker_adapter, as_of=as_of,
                                          allow_partial_history=args.allow_partial_history)

    if not await orchestrator.build_the_ground():
        logger.critical("Failed to build historical context. Aborting.")
        return 1

    # 2. Spin up Core Engine Components
    loop = asyncio.get_running_loop()
    oms_queue = asyncio.Queue()

    alpha = AlphaEngine(rvol_threshold=args.rvol_threshold, risk_reward_ratio=args.risk_reward, rvol_mode=args.rvol_mode)
    gateway: OrderGateway = PaperGateway()
    if args.live_orders:
        logger.warning("LIVE ORDERS ENABLED: signals will place real Zerodha orders.")
        gateway = KiteOrderGateway(broker_adapter.kite, tick_size=broker_adapter.tick_size(args.symbol))
    oms = ExecutionRouter(oms_queue, risk_per_trade=args.risk_per_trade, max_position_value=args.max_position_value,
                          gateway=gateway, tick_size=broker_adapter.tick_size(args.symbol))
    ticker = LiveTickAdapter(orchestrator.market_state, alpha, oms_queue, loop, token_map=broker_adapter.token_map(),
                             bar_listeners=[oms.on_bar])

    # 3. Launch Async Coroutines
    workers = [
        asyncio.create_task(ticker.process_ticks(), name="tick-aggregator"),
        asyncio.create_task(ticker.bar_clock(), name="bar-clock"),
        asyncio.create_task(oms.process_orders(), name="oms-router"),
    ]
    helpers = []
    if not args.no_simulate:
        # ⚠️ Starts the dynamic simulation pushing synthetic ticks into the system
        helpers.append(asyncio.create_task(
            simulate_live_market(ticker, args.symbol, orchestrator.market_state[args.symbol], alpha.base_bars)))
    feed = None
    if args.live_feed:
        tokens = [t for t, s in broker_adapter.token_map().items() if s in orchestrator.market_state]
        feed = start_kite_feed(*kite_creds, tokens, ticker)

    exit_code = 0
    runtime = asyncio.create_task(asyncio.sleep(args.run_seconds) if args.run_seconds > 0 else asyncio.Event().wait())
    try:
        # Workers loop forever; one finishing early means it crashed, and the engine must not run half-blind.
        done, _ = await asyncio.wait({runtime, *workers}, return_when=asyncio.FIRST_COMPLETED)
        for t in workers:
            if t in done:
                exit_code = 1
                logger.critical(f"Worker '{t.get_name()}' stopped unexpectedly: {t.exception()!r}. Shutting down.")
    finally:
        if feed is not None:
            feed.close()
        if exit_code == 0:
            # Drain what is already queued, then stop the workers cleanly.
            try:
                await asyncio.wait_for(asyncio.gather(ticker.tick_queue.join(), oms_queue.join()), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("Queues did not drain within 10s; pending work abandoned.")
        for t in [runtime, *workers, *helpers]: t.cancel()
        await asyncio.gather(runtime, *workers, *helpers, return_exceptions=True)

    logger.info("=== SYSTEM HALT ===")
    logger.info(f"Final Inventory State: {oms.active_inventory or '{}'}")
    for f in oms.fills:
        exits = f.exit_order_id or ("simulated OCO" if gateway.simulates_exits else "NONE")
        logger.info(f"   Fill {f.symbol}: {f.quantity} @ {f.average_price:.2f} (order {f.order_id}, exits: {exits})")
    for pos in oms.positions.values():
        logger.info(f"   Open {pos.symbol}: {pos.quantity} @ {pos.entry_price:.2f} | stop {pos.stop_loss:.2f} | target {pos.target:.2f}")
    for pos in oms.closed_positions:
        logger.info(f"   Closed {pos.symbol}: {pos.exit_reason} @ {pos.exit_price:.2f} | P&L ₹{pos.pnl:,.0f}")
    if ticker.dropped_ticks:
        logger.info(f"   {ticker.dropped_ticks} ticks dropped (unknown instrument, late, or malformed).")
    return exit_code

def run(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    try:
        return asyncio.run(main(argv))
    except KeyboardInterrupt:
        logger.info("Graceful exit invoked by Operator.")
        return 130

if __name__ == "__main__":
    sys.exit(run())
