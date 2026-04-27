import asyncio
import logging
import os
from dotenv import load_dotenv
from data_fetcher import BinanceDataFetcher
from risk_manager import RiskManager
from mt_executor import MT5Executor
from strategy import LiquidationSweepStrategy

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Main")

load_dotenv() # Load variables from .env file

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

TRADE_LOG_FILE = os.path.join(LOG_DIR, "trade_logs.txt")
# Clear the file on startup
with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
    f.write("=== FTMO Bot Trade Logs ===\n")

async def main():
    logger.info("Starting FTMO Expert Bot...")
    
    account_number = os.getenv("MT5_ACCOUNT")
    account_number = int(account_number) if account_number else None
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    
    # 1. Initialize MT5 Executor
    executor = MT5Executor(
        account_number=account_number, 
        password=password,       
        server=server          
    )
    
    if not executor.connect():
        logger.error("Failed to connect to MT5. Check your credentials in .env file.")
        return
        
    initial_balance = executor.get_start_of_day_balance()
    
    # 2. Initialize Risk Manager
    risk_manager = RiskManager(
        initial_balance=initial_balance,
        max_daily_loss_pct=4.5,
        max_overall_loss_pct=9.0
    )
    
    # 3. Initialize Data Fetcher
    fetcher = BinanceDataFetcher(['btcusdt', 'ethusdt'])
    
    # 4. Initialize Strategy with Expert Filters
    strategy = LiquidationSweepStrategy(
        risk_manager=risk_manager,
        mt_executor=executor,
        data_fetcher=fetcher, # Strategy needs fetcher to check orderbook
        min_liquidation_usd=250000.0 
    )
    
    # Register Callbacks
    fetcher.register_callback('price', strategy.on_price_update)
    fetcher.register_callback('liquidation', strategy.on_liquidation)
    fetcher.register_callback('oi', strategy.on_oi_update)
    
    # 5. Start Event Loop
    try:
        await fetcher.start()
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
    finally:
        fetcher.stop()
        executor.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
