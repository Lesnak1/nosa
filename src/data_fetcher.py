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
        
        self.callbacks = {
            "liquidation": [],
            "price": [],
            "orderbook": [],
            "oi": []
        }
        self.running = False
        
        self.open_interest = {s: 0.0 for s in self.symbols}
        self.orderbooks = {s: {"bids": [], "asks": []} for s in self.symbols}
        
        # Build stream path
        streams = []
        for sym in self.symbols:
            streams.append(f"{sym}@forceOrder")
            streams.append(f"{sym}@markPrice")
            streams.append(f"{sym}@depth10@100ms") # Partial book depth, no need to manage local snapshot
            
        self.stream_url = f"{self.ws_url}?streams={'/'.join(streams)}"

    def register_callback(self, event_type: str, callback: Callable):
        """Register a callback for events"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)

    async def _poll_open_interest(self):
        """Poll Open Interest every 5 seconds"""
        async with aiohttp.ClientSession() as session:
            while self.running:
                for symbol in self.symbols:
                    try:
                        url = f"{self.rest_url}/fapi/v1/openInterest?symbol={symbol.upper()}"
                        async with session.get(url, timeout=5) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                oi = float(data['openInterest'])
                                self.open_interest[symbol] = oi
                                for cb in self.callbacks['oi']:
                                    await cb({'symbol': symbol, 'oi': oi})
                    except Exception as e:
                        logger.error(f"Error polling OI for {symbol}: {e}")
                await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        data = json.loads(message)
        
        if 'data' not in data:
            return
            
        stream_data = data['data']
        event_type = stream_data.get('e')
        
        if event_type == 'forceOrder':
            # Liquidation event
            order_data = stream_data['o']
            liq_event = {
                'symbol': order_data['s'].lower(),
                'side': order_data['S'], # SELL means Long liquidation, BUY means Short liquidation
                'price': float(order_data['p']),
                'quantity': float(order_data['q']),
                'time': stream_data['E']
            }
            logger.info(f"LIQUIDATION: {liq_event['symbol']} {liq_event['side']} {liq_event['quantity']} @ {liq_event['price']}")
            for cb in self.callbacks['liquidation']:
                await cb(liq_event)
                
        elif event_type == 'markPriceUpdate':
            # Price update event
            price_event = {
                'symbol': stream_data['s'].lower(),
                'price': float(stream_data['p']),
                'time': stream_data['E']
            }
            for cb in self.callbacks['price']:
                await cb(price_event)
                
        elif event_type == 'depthUpdate':
            # Partial Orderbook Update (depth10)
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
        
        while self.running:
            try:
                async with websockets.connect(self.stream_url) as ws:
                    logger.info("Connected to Binance WebSocket!")
                    while self.running:
                        msg = await ws.recv()
                        await self._handle_message(msg)
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
                
    def stop(self):
        self.running = False
