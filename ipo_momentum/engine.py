"""
====================================================================================
INSTITUTIONAL QUANTITATIVE ENGINE - IPO MOMENTUM & LIVE EXECUTION (V1.0)
====================================================================================
Architecture:
1. Data Harmonization (Historical Reality Sync via REST)
2. Live Tick Ingestion (Thread-Safe In-Memory Synthesizer)
3. Vectorized Alpha Engine (AVWAP, RVOL, Base Breakouts)
4. Execution Router (Dynamic Sizing, Fixed Fractional Risk Constraints)
====================================================================================
"""

import asyncio
import logging
import urllib.request
import urllib.error
import json
import time
import math
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np

# Standard Timezone Handling
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)-8s | [%(name)s] | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("QUANT_ENGINE")


# ==============================================================================
# 1. EVENT DATA STRUCTURES
# ==============================================================================
@dataclass
class Tick:
    symbol: str
    price: float
    volume: int
    timestamp: datetime

@dataclass
class Signal:
    symbol: str
    entry_price: float
    stop_loss: float
    target: float
    reason: str


# ==============================================================================
# 2. DATA HARMONIZATION & BROKER ADAPTERS
# ==============================================================================
async def verify_hardware_ip(expected_static_ip: str) -> bool:
    logger.info("Executing hardware IP verification...")
    providers = ['https://api.ipify.org', 'https://ifconfig.me/ip', 'https://ident.me']
    for provider in providers:
        req = urllib.request.Request(provider, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
            actual_ip = response.read().decode('utf-8').strip()
            if actual_ip != expected_static_ip:
                logger.critical(f"FATAL: IP Mismatch. Authorized: {expected_static_ip}, Actual: {actual_ip}.")
                return False
            logger.info(f"IP Verification Confirmed via {provider}. IP: {actual_ip}")
            return True
        except Exception as e:
            logger.warning(f"IP provider {provider} failed: {e}. Retrying fallback...")
    logger.critical("FATAL: Network isolation or all IP verifiers failed.")
    return False

class TokenBucketRateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls: List[float] = []
        self._lock = asyncio.Lock()

    async def wait_for_capacity(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                self.calls = [t for t in self.calls if now - t < self.period]
                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return
                sleep_time = self.period - (now - self.calls[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

class BrokerAdapter(ABC):
    async def boot(self): pass
    def _empty_map(self) -> pd.DataFrame:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

    @abstractmethod
    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str) -> pd.DataFrame:
        pass

class ZerodhaKiteAdapter(BrokerAdapter):
    """Real Money Zerodha Setup (Asyncio-safe)"""
    def __init__(self, api_key: str, access_token: str):
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            raise ImportError("FATAL: kiteconnect SDK not installed. Run 'pip install kiteconnect'.")

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.limiter = TokenBucketRateLimiter(max_calls=3, period=1.1)
        self._instrument_cache = {}

    async def boot(self):
        logger.info("Downloading master instrument mapping from Exchange...")
        instruments = await asyncio.to_thread(self.kite.instruments, "NSE")
        for inst in instruments:
            self._instrument_cache[inst['tradingsymbol']] = inst['instrument_token']
        logger.info(f"Instrument mapping sealed. {len(self._instrument_cache)} NSE equities registered.")

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        token = self._instrument_cache.get(symbol)
        if not token: return self._empty_map()

        kite_interval = "5minute" if interval == "5m" else interval
        max_days, current_start, chunks = 90, start_date, []

        while current_start < end_date:
            chunk_end = min(current_start + timedelta(days=max_days), end_date)
            from_ist = current_start.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')
            to_ist = chunk_end.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')

            chunk_success = False
            for attempt in range(3):
                await self.limiter.wait_for_capacity()
                try:
                    records = await asyncio.to_thread(
                        self.kite.historical_data, instrument_token=token, from_date=from_ist, to_date=to_ist, interval=kite_interval
                    )
                    if records: chunks.extend(records)
                    chunk_success = True; break
                except Exception as e:
                    if attempt == 2: return self._empty_map()
                    await asyncio.sleep(2 ** attempt)

            if not chunk_success: return self._empty_map()
            current_start = chunk_end + timedelta(seconds=1)

        if not chunks: return self._empty_map()
        df = pd.DataFrame(chunks)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.index = df.index.tz_localize(IST) if df.index.tz is None else df.index.tz_convert(IST)
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']]

        df['Close'] = df['Close'].ffill().bfill()
        for col in ['Open', 'High', 'Low']: df[col] = df[col].fillna(df['Close'])
        df['Volume'] = df['Volume'].fillna(0)
        df.dropna(inplace=True)
        return df[~df.index.duplicated(keep='last')].sort_index()

class PublicExchangeAdapter(BrokerAdapter):
    """Fetches real market prints via Yahoo Finance to bypass broker SDK limits for testing."""
    def __init__(self):
        self.limiter = TokenBucketRateLimiter(max_calls=3, period=1.1)

    async def fetch_historical_bars(self, symbol: str, start_date: datetime, end_date: datetime, interval: str = "5m") -> pd.DataFrame:
        logger.info(f"[{symbol}] Fetching public exchange prints via direct HTTP...")
        cutoff = datetime.now(timezone.utc) - timedelta(days=59)
        if start_date < cutoff: start_date = cutoff

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?interval={interval}&period1={int(start_date.timestamp())}&period2={int(end_date.timestamp())}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        req = urllib.request.Request(url, headers=headers)

        data, success = None, False
        for attempt in range(3):
            await self.limiter.wait_for_capacity()
            try:
                res = await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
                data = json.loads(res.read().decode('utf-8'))
                success = True
                break
            except Exception as e:
                if attempt == 2: return self._empty_map()
                await asyncio.sleep(2 ** attempt)

        if not success or not data.get('chart', {}).get('result'): return self._empty_map()
        res = data['chart']['result'][0]
        if 'timestamp' not in res: return self._empty_map()

        quote = res['indicators']['quote'][0]
        df = pd.DataFrame({
            'Open': quote['open'], 'High': quote['high'], 'Low': quote['low'],
            'Close': quote['close'], 'Volume': quote['volume']
        }, index=pd.to_datetime(res['timestamp'], unit='s', utc=True))

        df.index = df.index.tz_convert(IST)
        df['Close'] = df['Close'].ffill().bfill()
        for col in ['Open', 'High', 'Low']: df[col] = df[col].fillna(df['Close'])
        df['Volume'] = df['Volume'].fillna(0)
        df.dropna(inplace=True)
        return df[~df.index.duplicated(keep='last')].sort_index()

class ProductionOrchestrator:
    def __init__(self, target_ipos: Dict[str, datetime], broker: BrokerAdapter):
        self.watchlist = target_ipos
        self.broker = broker
        self.market_state: Dict[str, pd.DataFrame] = {}

    async def build_the_ground(self) -> bool:
        await self.broker.boot()
        end_date = datetime.now(timezone.utc)

        for symbol, listing_date in self.watchlist.items():
            start_date = max(listing_date, end_date - timedelta(days=180))
            df = await self.broker.fetch_historical_bars(symbol, start_date, end_date, "5m")
            if not df.empty:
                self.market_state[symbol] = df
                logger.info(f"[{symbol}] Map established. {len(df)} concrete 5m bars acquired.")
        return bool(self.market_state)


# ==============================================================================
# 3. ALPHA ENGINE & IN-MEMORY SYNTHESIZER
# ==============================================================================
class AlphaEngine:
    def __init__(self, rvol_threshold: float = 2.0, risk_reward_ratio: float = 3.0):
        self.rvol_threshold = rvol_threshold
        self.rr_ratio = risk_reward_ratio

    def evaluate(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < 20: return None

        df = df.copy()
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3.0
        df['AVWAP'] = (df['Typical_Price'] * df['Volume']).cumsum() / df['Volume'].cumsum().replace(0, 1)
        df['Vol_SMA_20'] = df['Volume'].rolling(20, min_periods=1).mean()
        df['RVOL'] = df['Volume'] / df['Vol_SMA_20'].replace(0, 1)
        df['Base_High'] = df['High'].iloc[:150].max() if len(df) > 150 else df['High'].max()
        df['ATR'] = (df['High'] - df['Low']).rolling(14, min_periods=1).mean()

        latest, prev = df.iloc[-1], df.iloc[-2]

        # Breakout condition: Crossed the historical base high on high relative volume & above anchored VWAP
        is_breakout = (prev['Close'] <= latest['Base_High']) and (latest['Close'] > latest['Base_High'])
        above_avwap = latest['Close'] > latest['AVWAP']
        strong_vol = latest['RVOL'] > self.rvol_threshold

        if is_breakout and above_avwap and strong_vol:
            logger.info(f"[{symbol}] 🟢 ALPHA TRIGGER: Base Breakout @ {latest['Close']:.2f} | RVOL: {latest['RVOL']:.2f}x")

            # Risk bounded by 1.5 ATR, or the AVWAP structural floor (whichever is closer)
            stop_loss = min(latest['Close'] - (1.5 * latest['ATR']), latest['AVWAP'])
            target = latest['Close'] + ((latest['Close'] - stop_loss) * self.rr_ratio)

            return Signal(symbol, latest['Close'], stop_loss, target, "IPO_BASE_BREAKOUT")
        return None

class LiveTickAdapter:
    def __init__(self, market_state: Dict[str, pd.DataFrame], alpha_engine: AlphaEngine, oms_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.market_state = market_state
        self.alpha = alpha_engine
        self.oms_queue = oms_queue
        self.loop = loop
        self.tick_queue = asyncio.Queue()
        self.current_bars: Dict[str, dict] = {}

    def broker_on_ticks(self, ws, ticks: list):
        """Websocket Callback. Safely catches Zerodha kite payload (instrument_token, last_price)."""
        for t in ticks:
            tick = Tick(
                symbol=t.get('instrument_token', t.get('symbol', 'UNKNOWN')),
                price=t.get('last_price', t.get('price', 0.0)),
                volume=t.get('last_traded_quantity', t.get('volume', 100)),
                timestamp=t.get('timestamp', datetime.now(IST))
            )
            # Thread-safe dispatch from the broker's C-Thread to our Async Event Loop
            self.loop.call_soon_threadsafe(self.tick_queue.put_nowait, tick)

    async def process_ticks(self):
        logger.info("Tick Aggregator Online. Awaiting live websocket events...")
        while True:
            tick: Tick = await self.tick_queue.get()
            sym = tick.symbol

            # Floor the timestamp to the current 5-minute block
            boundary = tick.timestamp.replace(minute=(tick.timestamp.minute // 5) * 5, second=0, microsecond=0)

            if sym not in self.current_bars:
                self.current_bars[sym] = {'timestamp': boundary, 'Open': tick.price, 'High': tick.price, 'Low': tick.price, 'Close': tick.price, 'Volume': tick.volume}
                self.tick_queue.task_done()
                continue

            active = self.current_bars[sym]

            if boundary > active['timestamp']:
                closed_bar = active.copy()
                idx = closed_bar.pop('timestamp')

                # FUTURE-PROOFED: pd.Series prevents Pandas indexing errors & warnings
                self.market_state[sym].loc[idx] = pd.Series(closed_bar)
                logger.info(f"📊 [{sym}] 5m Bar Closed | C: {closed_bar['Close']:.2f} | V: {closed_bar['Volume']}")

                # Immediately evaluate the fully formed historical bar
                if signal := self.alpha.evaluate(sym, self.market_state[sym]):
                    await self.oms_queue.put(signal)

                # Reset for the new forming candle
                self.current_bars[sym] = {'timestamp': boundary, 'Open': tick.price, 'High': tick.price, 'Low': tick.price, 'Close': tick.price, 'Volume': tick.volume}
            else:
                active['High'] = max(active['High'], tick.price)
                active['Low'] = min(active['Low'], tick.price)
                active['Close'] = tick.price
                active['Volume'] += tick.volume

            self.tick_queue.task_done()


# ==============================================================================
# 4. EXECUTION ROUTER (OMS)
# ==============================================================================
class ExecutionRouter:
    def __init__(self, oms_queue: asyncio.Queue, risk_per_trade: float):
        self.oms_queue = oms_queue
        self.risk_per_trade = risk_per_trade
        self.active_inventory = set()

    def _calculate_qty(self, entry: float, stop_loss: float) -> int:
        risk_per_share = entry - stop_loss
        return 0 if risk_per_share <= 0 else max(0, math.floor(self.risk_per_trade / risk_per_share))

    async def process_orders(self):
        logger.info("OMS Router Online. Enforcing Risk Parameters...")
        while True:
            signal: Signal = await self.oms_queue.get()
            sym = signal.symbol

            if sym in self.active_inventory:
                self.oms_queue.task_done()
                continue

            qty = self._calculate_qty(signal.entry_price, signal.stop_loss)
            if qty <= 0:
                self.oms_queue.task_done()
                continue

            self.active_inventory.add(sym)

            logger.info(f"🚀 [OMS DISPATCH] BUY {qty}x {sym} @ {signal.entry_price:.2f}")
            logger.info(f"🛡️ Target: {signal.target:.2f} | Stop Loss: {signal.stop_loss:.2f}")

            # [KITE INTEGRATION] await asyncio.to_thread(kite.place_order, variety=kite.VARIETY_BO, ...)

            await asyncio.sleep(0.3) # Simulate Broker HTTP Order Placement Latency
            logger.info(f"✅ [{sym}] BROKER FILLED. Bracket Order Active.")

            self.oms_queue.task_done()


# ==============================================================================
# 5. DYNAMIC MARKET SIMULATOR (FOR OUT-OF-BOX DEMONSTRATION)
# ==============================================================================
async def simulate_live_market(tick_adapter: LiveTickAdapter, sym: str, df: pd.DataFrame):
    """Dynamically generates live ticks to force a breakout based on the real historical data."""
    await asyncio.sleep(2)

    # Calculate real-world metrics from the downloaded Yahoo dataframe
    base_high = df['High'].iloc[:150].max() if len(df) > 150 else df['High'].max()
    last_close = df['Close'].iloc[-1]

    logger.info(f"📡 [EXCHANGE] Initiating Live Stream for {sym}")
    logger.info(f"📡 [EXCHANGE] Real Historical High to Beat: {base_high:.2f}")

    current_time = datetime.now(IST).replace(minute=0, second=0, microsecond=0)

    # 1. Normal Tick (No breakout yet, just moving inside the bar)
    tick_adapter.broker_on_ticks(None, [{'symbol': sym, 'price': last_close, 'volume': 1000, 'timestamp': current_time}])
    await asyncio.sleep(1)

    # 2. Institutional Block (Breaks the Base High dynamically with massive volume)
    breakout_price = base_high * 1.015 # 1.5% above historical high
    next_bar_time = current_time + timedelta(minutes=5)

    logger.info(f"📡 [EXCHANGE] Massive volume detected. Institutional sweep crossing {base_high:.2f}...")
    tick_adapter.broker_on_ticks(None, [{'symbol': sym, 'price': breakout_price, 'volume': 850000, 'timestamp': next_bar_time}])


# ==============================================================================
# 6. MAIN DEPLOYMENT PIPELINE
# ==============================================================================
async def main():
    logger.info("=== INITIALIZING INSTITUTIONAL ENGINE ===")

    # Optional: Hardware verify (Uncomment and add your VPS IP for production)
    # await verify_hardware_ip("192.168.1.1")

    # 1. Map the Reality (Historical Data Sync)
    target_ipos = {"SWIGGY": datetime.now(timezone.utc) - timedelta(days=20)}

    broker_adapter = PublicExchangeAdapter()
    orchestrator = ProductionOrchestrator(target_ipos, broker_adapter)

    if not await orchestrator.build_the_ground():
        logger.critical("Failed to build historical context. Aborting.")
        return

    # 2. Spin up Core Engine Components
    loop = asyncio.get_running_loop()
    oms_queue = asyncio.Queue()

    alpha = AlphaEngine(rvol_threshold=2.0, risk_reward_ratio=3.0)
    oms = ExecutionRouter(oms_queue, risk_per_trade=15000.0) # Rs 15,000 max portfolio hit per trade
    ticker = LiveTickAdapter(orchestrator.market_state, alpha, oms_queue, loop)

    # 3. Launch Async Coroutines
    tasks = [
        asyncio.create_task(ticker.process_ticks()),
        asyncio.create_task(oms.process_orders()),

        # ⚠️ Starts the dynamic simulation pushing mock ticks into the system
        asyncio.create_task(simulate_live_market(ticker, "SWIGGY", orchestrator.market_state["SWIGGY"]))
    ]

    # Allow time for simulation to execute before stopping
    await asyncio.sleep(6)

    logger.info("=== SYSTEM HALT ===")
    logger.info(f"Final Inventory State: {oms.active_inventory}")
    for t in tasks: t.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful exit invoked by Operator.")
