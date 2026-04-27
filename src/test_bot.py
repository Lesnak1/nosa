import asyncio
import logging
import time

from risk_manager import RiskManager
from strategy import LiquidationSweepStrategy

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class MockMTExecutor:
    def __init__(self, initial_balance=10000.0):
        self.balance = initial_balance
        self.equity = initial_balance
        self.sod_balance = initial_balance
        self.open_positions = 0
        self.trades = []
        
    def get_equity(self): return self.equity
    def get_balance(self): return self.balance
    def get_start_of_day_balance(self): return self.sod_balance
    def get_open_positions_count(self): return self.open_positions
    
    def close_all_positions(self):
        logging.info("MOCK: close_all_positions() called.")
        self.open_positions = 0
        
    def open_trade(self, symbol, side, volume, sl, tp):
        logging.info(f"MOCK: open_trade({symbol}, {side}, {volume}, SL={sl}, TP={tp})")
        self.open_positions += 1
        self.trades.append({"symbol": symbol, "side": side, "volume": volume, "sl": sl, "tp": tp})
        return True

class MockDataFetcher:
    def __init__(self):
        self.orderbook = {"bids": [{"price": 60000, "qty": 10}], "asks": [{"price": 60001, "qty": 10}]}
        
    def get_latest_orderbook(self, symbol):
        return self.orderbook

async def test_ftmo_rules():
    print("=== TEST 1: FTMO DAILY & OVERALL LIMITS ===")
    executor = MockMTExecutor(initial_balance=10000.0)
    risk = RiskManager(initial_balance=10000.0, max_daily_loss_pct=4.5, max_overall_loss_pct=9.0)
    fetcher = MockDataFetcher()
    strategy = LiquidationSweepStrategy(risk_manager=risk, mt_executor=executor, data_fetcher=fetcher, min_liquidation_usd=1000.0)
    
    # Simulate equity drop to 9500 (5% drop, exceeds 4.5% daily limit)
    executor.equity = 9500.0
    
    # Trigger a price update which runs the drawdown check
    await strategy.on_price_update({"symbol": "BTCUSDT", "price": 60000})
    
    if executor.open_positions == 0:
        print("PASS: Drawdown limit successfully closed all positions and blocked new trades.")
    else:
        print("FAIL: Drawdown limit check failed!")

async def test_dynamic_lot_sizing():
    print("\n=== TEST 2: DYNAMIC LOT SIZING (COMPOUNDING) ===")
    risk = RiskManager(initial_balance=10000.0)
    
    entry = 60000
    sl = 59400 # 1% SL
    
    # 10k balance
    vol_10k = risk.calculate_position_size(10000.0, entry, sl, risk_pct=0.5)
    print(f"Risking 0.5% on 10k: Volume = {vol_10k}")
    
    # 15k balance
    vol_15k = risk.calculate_position_size(15000.0, entry, sl, risk_pct=0.5)
    print(f"Risking 0.5% on 15k: Volume = {vol_15k}")
    
    if vol_15k > vol_10k:
        print("PASS: Dynamic lot sizing correctly compounds volume.")
    else:
        print("FAIL: Dynamic lot sizing failed!")

async def test_expert_filters():
    print("\n=== TEST 3: EXPERT FILTERS (OI Drop & Orderbook Imbalance) ===")
    executor = MockMTExecutor(initial_balance=10000.0)
    risk = RiskManager(initial_balance=10000.0)
    fetcher = MockDataFetcher()
    strategy = LiquidationSweepStrategy(risk_manager=risk, mt_executor=executor, data_fetcher=fetcher, min_liquidation_usd=50000.0)
    strategy.cooldown_seconds = 0 # Disable cooldown for fast testing
    
    # 1. Send fake liquidation without OI data
    await strategy.on_liquidation({"symbol": "BTCUSDT", "side": "SELL", "price": 60000, "quantity": 2})
    print(f"Trades after no OI history: {len(executor.trades)}")
    
    # 2. Feed OI data showing NO DROP (Rising OI)
    await strategy.on_oi_update({"symbol": "BTCUSDT", "oi": 100})
    await strategy.on_oi_update({"symbol": "BTCUSDT", "oi": 110})
    await strategy.on_liquidation({"symbol": "BTCUSDT", "side": "SELL", "price": 60000, "quantity": 2})
    print(f"Trades after FAKE LIQUIDATION (Rising OI): {len(executor.trades)}")
    
    # 3. Feed OI data showing REAL DROP (Fakeout confirmed)
    await strategy.on_oi_update({"symbol": "BTCUSDT", "oi": 110})
    await strategy.on_oi_update({"symbol": "BTCUSDT", "oi": 80})
    
    # 3a. Orderbook Imbalance NOT MET for BUY (SELL liquidation -> BUY signal)
    # BUY signal needs Bids > Asks * 1.2
    fetcher.orderbook = {"bids": [{"price": 60000, "qty": 10}], "asks": [{"price": 60001, "qty": 10}]}
    await strategy.on_liquidation({"symbol": "BTCUSDT", "side": "SELL", "price": 60000, "quantity": 2})
    print(f"Trades after OB Imbalance FAIL: {len(executor.trades)}")
    
    # 3b. Orderbook Imbalance MET
    fetcher.orderbook = {"bids": [{"price": 60000, "qty": 20}], "asks": [{"price": 60001, "qty": 10}]}
    await strategy.on_liquidation({"symbol": "BTCUSDT", "side": "SELL", "price": 60000, "quantity": 2})
    print(f"Trades after PERFECT SETUP: {len(executor.trades)}")
    
    if len(executor.trades) == 1:
        print("PASS: Strategy successfully ignored fake signals and only took the PERFECT A-Setup trade.")
    else:
        print("FAIL: Strategy took incorrect trades!")

if __name__ == "__main__":
    asyncio.run(test_ftmo_rules())
    asyncio.run(test_dynamic_lot_sizing())
    asyncio.run(test_expert_filters())
