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
    challenge_type = os.getenv("FTMO_CHALLENGE_TYPE", "2-step")  # "1-step" or "2-step"
    
    # 2. Initialize Risk Manager with correct FTMO rules
    risk_manager = RiskManager(
        initial_balance=initial_balance,
        challenge_type=challenge_type
    )
    
    # 3. Initialize Data Fetcher (monitor both, but only trade BTCUSD)
    fetcher = BinanceDataFetcher(['btcusdt'])
    
    # 4. Initialize Strategy
    strategy = LiquidationSweepStrategy(
        risk_manager=risk_manager,
        mt_executor=executor,
        data_fetcher=fetcher,
        min_liquidation_usd=50000.0  # Lowered for more liq confirmations
    )
    
    # Register Callbacks
    fetcher.register_callback('price', strategy.on_price_update)
    fetcher.register_callback('liquidation', strategy.on_liquidation)
    fetcher.register_callback('oi', strategy.on_oi_update)
    fetcher.register_callback('candle_signal', strategy.on_candle_signal)  # PRIMARY signal
    
    # Position monitor: track closed trades for circuit breaker
    async def monitor_positions():
        """Check every 30s if any position closed, feed result to strategy."""
        known_tickets = set()
        import MetaTrader5 as mt5
        while True:
            await asyncio.sleep(30)
            try:
                positions = mt5.positions_get()
                current_tickets = {p.ticket for p in positions} if positions else set()
                
                # Detect closed positions
                closed = known_tickets - current_tickets
                for ticket in closed:
                    # Check deal history for this ticket
                    from datetime import datetime, timedelta
                    deals = mt5.history_deals_get(
                        datetime.now() - timedelta(hours=1), datetime.now()
                    )
                    if deals:
                        for deal in deals:
                            if deal.position_id == ticket and deal.entry == 1:  # Exit deal
                                is_win = deal.profit > 0
                                strategy.record_trade_result(is_win)
                                logger.info(f"Trade closed: {'WIN' if is_win else 'LOSS'} "
                                          f"${deal.profit:.2f} | Consecutive losses: {strategy.consecutive_losses}")
                
                known_tickets = current_tickets
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
    
    # 5. Start Event Loop
    try:
        monitor_task = asyncio.create_task(monitor_positions())
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
