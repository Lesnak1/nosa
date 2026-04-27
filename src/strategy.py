import logging
import time

logger = logging.getLogger("Strategy")

class LiquidationSweepStrategy:
    def __init__(self, risk_manager, mt_executor, data_fetcher, min_liquidation_usd=150000.0):
        self.risk_manager = risk_manager
        self.mt_executor = mt_executor
        self.data_fetcher = data_fetcher
        self.min_liquidation_usd = min_liquidation_usd
        
        self.prices = {}
        self.oi_history = {} # Tracks previous OI to detect drops
        self.last_trade_time = 0
        self.cooldown_seconds = 60 * 60 # 1 hour cooldown between trades for higher timeframe logic

    async def on_price_update(self, data):
        symbol = data['symbol'].upper()
        self.prices[symbol] = data['price']
        
        equity = self.mt_executor.get_equity()
        sod_balance = self.mt_executor.get_start_of_day_balance()
        
        if not self.risk_manager.check_daily_limit(equity, sod_balance) or not self.risk_manager.check_overall_limit(equity):
            logger.critical("DRAWDOWN LIMIT REACHED. CLOSING ALL POSITIONS.")
            self.mt_executor.close_all_positions()

    async def on_oi_update(self, data):
        """Track Open Interest to detect drops"""
        symbol = data['symbol'].upper()
        current_oi = data['oi']
        
        if symbol not in self.oi_history:
            self.oi_history[symbol] = []
            
        self.oi_history[symbol].append(current_oi)
        if len(self.oi_history[symbol]) > 5:
            self.oi_history[symbol].pop(0)

    async def on_liquidation(self, data):
        symbol = data['symbol'].upper()
        mt5_symbol = symbol 
        
        side = data['side']
        price = data['price']
        qty = data['quantity']
        usd_value = price * qty
        
        if usd_value < self.min_liquidation_usd:
            return 
            
        logger.info(f"Large Liquidation Detected: {usd_value}$ on {symbol} ({side})")
        
        current_time = time.time()
        if current_time - self.last_trade_time < self.cooldown_seconds:
            logger.info("Strategy is in cooldown. Ignoring signal.")
            return

        # EXPERT FILTER 1: Open Interest Drop Check
        # We want to see that OI has dropped compared to 10-20 seconds ago, proving real liquidation.
        history = self.oi_history.get(symbol, [])
        if len(history) >= 2:
            oldest_oi = history[0]
            current_oi = history[-1]
            if current_oi >= oldest_oi:
                logger.warning(f"OI did not drop (Old: {oldest_oi}, Current: {current_oi}). Fake Liquidation. Ignoring.")
                return
            else:
                logger.info(f"OI Drop Confirmed: {oldest_oi} -> {current_oi}")
        else:
            logger.warning("Not enough OI history to confirm drop. Waiting for more data.")
            return

        # EXPERT FILTER 2: Orderbook Imbalance Check
        ob = self.data_fetcher.get_latest_orderbook(symbol)
        bids = ob['bids']
        asks = ob['asks']
        
        if not bids or not asks:
            logger.warning("Orderbook data missing. Ignoring.")
            return
            
        total_bid_qty = sum([b['qty'] for b in bids])
        total_ask_qty = sum([a['qty'] for a in asks])
        
        trade_side = "BUY" if side == "SELL" else "SELL"
        
        # If we want to BUY, we need stronger Bids (Support)
        # If we want to SELL, we need stronger Asks (Resistance)
        imbalance_threshold = 1.2 # Require 20% more liquidity on our side
        
        if trade_side == "BUY":
            if total_bid_qty < total_ask_qty * imbalance_threshold:
                logger.warning(f"Orderbook Imbalance rejected BUY. Bids: {total_bid_qty}, Asks: {total_ask_qty}")
                return
        else:
            if total_ask_qty < total_bid_qty * imbalance_threshold:
                logger.warning(f"Orderbook Imbalance rejected SELL. Bids: {total_bid_qty}, Asks: {total_ask_qty}")
                return
                
        logger.info(f"Orderbook Imbalance Confirmed for {trade_side}.")

        equity = self.mt_executor.get_equity()
        sod_balance = self.mt_executor.get_start_of_day_balance()
        open_positions = self.mt_executor.get_open_positions_count()
        
        if not self.risk_manager.can_open_trade(equity, sod_balance, open_positions):
            return
        
        sl_distance_pct = 0.010 # 1.0% stop loss (1h Timeframe Optimized)
        tp_distance_pct = 0.020 # 2.0% take profit (1h Timeframe Optimized 1:2 RR)
        
        if trade_side == "BUY":
            sl_price = price * (1 - sl_distance_pct)
            tp_price = price * (1 + tp_distance_pct)
        else:
            sl_price = price * (1 + sl_distance_pct)
            tp_price = price * (1 - tp_distance_pct)
            
        volume = self.risk_manager.calculate_position_size(equity, price, sl_price, risk_pct=0.5)
        
        logger.info(f"EXPERT SIGNAL GENERATED: {trade_side} {mt5_symbol} at {price}. SL: {sl_price}, TP: {tp_price}, Vol: {volume}")
        
        success = self.mt_executor.open_trade(mt5_symbol, trade_side, volume, sl_price, tp_price)
        if success:
            self.last_trade_time = current_time
            logger.info("Trade successfully opened in MT5.")
            
            # Log to specific trade log file
            import os
            import datetime
            log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
            log_file = os.path.join(log_dir, "trade_logs.txt")
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = (f"[{timestamp}] SIGNAL EXECUTED\n"
                         f"  Symbol: {mt5_symbol}\n"
                         f"  Side: {trade_side}\n"
                         f"  Price: {price}\n"
                         f"  Volume: {volume}\n"
                         f"  SL: {sl_price}\n"
                         f"  TP: {tp_price}\n"
                         f"  Liquidation Trigger: {usd_value:,.2f}$\n"
                         f"  Equity at Entry: {equity:,.2f}$\n"
                         f"--------------------------------------------------\n")
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(log_entry)
            except Exception as e:
                logger.error(f"Failed to write to trade_logs.txt: {e}")
                
        else:
            logger.error("Failed to open trade in MT5.")

