import asyncio
import json
import websockets
import logging
import aiohttp
import time
from typing import Callable, Dict, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DataFetcher")

class BinanceDataFetcher:
    def __init__(self, symbols: List[str]):
        self.symbols = [s.lower() for s in symbols]
        self.ws_url = "wss://fstream.binance.com/stream"
        self.rest_url = "https://fapi.binance.com"
        self.spot_rest = "https://api.binance.com"
        
        self.callbacks = {
            "liquidation": [],
            "price": [],
            "orderbook": [],
            "oi": [],
            "candle_signal": [],  # NEW: candle-based signals
        }
        self.running = False
        
        self.open_interest = {s: 0.0 for s in self.symbols}
        self.orderbooks = {s: {"bids": [], "asks": []} for s in self.symbols}
        self.candle_cache = {}  # symbol -> {tf: dataframe}
        
        # Build stream path
        streams = []
        for sym in self.symbols:
            streams.append(f"{sym}@forceOrder")
            streams.append(f"{sym}@markPrice")
            streams.append(f"{sym}@depth10@100ms")
            
        self.stream_url = f"{self.ws_url}?streams={'/'.join(streams)}"

    def register_callback(self, event_type: str, callback: Callable):
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)

    async def _poll_open_interest(self):
        """Poll Open Interest every 5 seconds"""
        async with aiohttp.ClientSession() as session:
            while self.running:
                for symbol in self.symbols:
                    try:
                        url = f"{self.rest_url}/fapi/v1/openInterest?symbol={symbol.upper()}"
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                oi = float(data['openInterest'])
                                self.open_interest[symbol] = oi
                                for cb in self.callbacks['oi']:
                                    await cb({'symbol': symbol, 'oi': oi})
                    except Exception as e:
                        logger.error(f"OI poll error {symbol}: {e}")
                await asyncio.sleep(5)

    async def _poll_candles(self):
        """
        Poll SPOT 1h candles every 60 seconds.
        Detect volume spike + wick pattern (same as backtest).
        This is the PRIMARY signal source — matches backtest exactly.
        """
        async with aiohttp.ClientSession() as session:
            # Wait for initial data
            await asyncio.sleep(5)
            
            while self.running:
                for symbol in self.symbols:
                    for interval in ['1h', '4h']:  # Both timeframes
                        try:
                            url = f"{self.spot_rest}/api/v3/klines"
                            params = {
                                'symbol': symbol.upper(),
                                'interval': interval,
                                'limit': 50
                            }
                            async with session.get(url, params=params, 
                                                 timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    await self._analyze_candles(symbol, data, interval)
                        except Exception as e:
                            logger.error(f"Candle poll error {symbol}/{interval}: {e}")
                await asyncio.sleep(60)  # Check every 60 seconds

    async def _analyze_candles(self, symbol, raw_candles, interval='1h'):
        """
        Analyze candles for volume spike + wick pattern.
        Exactly matches backtest signal logic.
        """
        if len(raw_candles) < 25:
            return
        
        candles = []
        for c in raw_candles:
            candles.append({
                'open': float(c[1]), 'high': float(c[2]),
                'low': float(c[3]), 'close': float(c[4]),
                'volume': float(c[5])
            })
        
        # Volume spike detection (VW=10, VM=2.0)
        volumes = [c['volume'] for c in candles]
        if len(volumes) < 11:
            return
        
        recent_vols = volumes[-11:-1]  # Last 10 candles (excluding current)
        vol_ma = sum(recent_vols) / len(recent_vols)
        vol_std = (sum((v - vol_ma)**2 for v in recent_vols) / len(recent_vols)) ** 0.5
        vol_threshold = vol_ma + 2.0 * vol_std
        
        current = candles[-1]
        if current['volume'] <= vol_threshold:
            return  # No volume spike
        
        # Wick ratio check (WR=0.5)
        total_range = current['high'] - current['low']
        if total_range <= 0:
            return
        
        upper_wick = current['high'] - max(current['open'], current['close'])
        lower_wick = min(current['open'], current['close']) - current['low']
        
        signal = None
        if lower_wick / total_range >= 0.5:
            signal = 'BUY'  # Long wick down = buy signal
        elif upper_wick / total_range >= 0.5:
            signal = 'SELL'  # Long wick up = sell signal
        
        if not signal:
            return
        
        # Calculate ATR from real candles
        trs = []
        for i in range(1, len(candles)):
            h = candles[i]['high']
            l = candles[i]['low']
            pc = candles[i-1]['close']
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        atr = sum(trs[-14:]) / min(14, len(trs[-14:])) if trs else 0
        
        logger.info(f"CANDLE SIGNAL [{interval}]: {signal} on {symbol.upper()} | "
                     f"Vol: {current['volume']:.0f} (thresh: {vol_threshold:.0f}) | ATR: {atr:.2f}")
        
        for cb in self.callbacks['candle_signal']:
            await cb({
                'symbol': symbol,
                'signal': signal,
                'price': current['close'],
                'atr': atr,
                'volume': current['volume'],
                'vol_threshold': vol_threshold
            })

    async def _handle_message(self, message: str):
        data = json.loads(message)
        
        if 'data' not in data:
            return
            
        stream_data = data['data']
        event_type = stream_data.get('e')
        
        if event_type == 'forceOrder':
            order_data = stream_data['o']
            liq_event = {
                'symbol': order_data['s'].lower(),
                'side': order_data['S'],
                'price': float(order_data['p']),
                'quantity': float(order_data['q']),
                'time': stream_data['E']
            }
            for cb in self.callbacks['liquidation']:
                await cb(liq_event)
                
        elif event_type == 'markPriceUpdate':
            price_event = {
                'symbol': stream_data['s'].lower(),
                'price': float(stream_data['p']),
                'time': stream_data['E']
            }
            for cb in self.callbacks['price']:
                await cb(price_event)
                
        elif event_type == 'depthUpdate':
            symbol = stream_data['s'].lower()
            bids = [{'price': float(b[0]), 'qty': float(b[1])} for b in stream_data['b']]
            asks = [{'price': float(a[0]), 'qty': float(a[1])} for a in stream_data['a']]
            
            self.orderbooks[symbol] = {'bids': bids, 'asks': asks}
            for cb in self.callbacks['orderbook']:
                await cb({'symbol': symbol, 'bids': bids, 'asks': asks})

    def get_latest_oi(self, symbol: str) -> float:
        return self.open_interest.get(symbol.lower(), 0.0)
        
    def get_latest_orderbook(self, symbol: str) -> dict:
        return self.orderbooks.get(symbol.lower(), {"bids": [], "asks": []})

    async def start(self):
        self.running = True
        logger.info(f"Connecting to Binance streams: {self.stream_url}")
        
        asyncio.create_task(self._poll_open_interest())
        asyncio.create_task(self._poll_candles())  # NEW: candle signal polling
        
        while self.running:
            try:
                async with websockets.connect(
                    self.stream_url, ping_interval=20, ping_timeout=10, close_timeout=5
                ) as ws:
                    logger.info("Connected to Binance WebSocket!")
                    while self.running:
                        msg = await ws.recv()
                        await self._handle_message(msg)
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
                
    def stop(self):
        self.running = False
