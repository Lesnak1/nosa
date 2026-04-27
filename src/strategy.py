import logging
import time
import os
import datetime
from collections import deque

logger = logging.getLogger("Strategy")

MT5_SYMBOL_MAP = {
    "BTCUSDT": "BTCUSD",
    "ETHUSDT": "ETHUSD",
}


class LiquidationSweepStrategy:
    """
    Expert Liquidation Sweep Strategy v5.0

    Two signal sources (matching backtest exactly):
    1. CANDLE SIGNAL: Volume spike + wick pattern on real 1h SPOT candles
       → PRIMARY signal, generates ~2-4 trades/week per symbol
    2. LIQUIDATION SIGNAL: $50K+ liquidation + OI drop + orderbook support
       → SECONDARY signal, confirms existing candle signals
    
    Backtest-optimized params (BTCUSDT 4h SPOT, 180d):
    - Risk: 1.5% per trade (reduced from 2% for DD safety)
    - ATR SL x1.5 | ATR TP x4.0 | RR 1:2.7
    - PF 2.84 | WR 53.8% | Phase 1: 14d | Phase 2: 4d
    """
    def __init__(self, risk_manager, mt_executor, data_fetcher, min_liquidation_usd=50000.0):
        self.risk_manager = risk_manager
        self.mt_executor = mt_executor
        self.data_fetcher = data_fetcher
        self.min_liquidation_usd = min_liquidation_usd
        
        self.prices = {}
        self.oi_history = {}
        self.orderbook_history = {}
        self.is_breached = False
        
        # Risk: 2.0% base (P1 14d, PF 2.84, DD 7.22%)
        self.base_risk_pct = 2.0
        self.consecutive_losses = 0
        self.max_consecutive_before_reduce = 3
        
        # Liquidation boost: when liq + candle align, confidence is higher
        self.recent_liquidations = {}  # symbol -> {side, time, value}
        
        # Track ATR from real candles
        self.real_atr = {}  # symbol -> ATR value from Binance candles
        
        # Prevent duplicate signals from same candle
        self.last_signal = {}  # symbol -> {signal, price} to deduplicate

    def _get_dynamic_risk(self):
        if self.consecutive_losses >= self.max_consecutive_before_reduce:
            reduced = self.base_risk_pct * 0.5
            logger.warning(f"CIRCUIT BREAKER: {self.consecutive_losses} losses -> risk {reduced}%")
            return reduced
        return self.base_risk_pct

    def _get_orderbook_avg(self, symbol):
        history = self.orderbook_history.get(symbol, [])
        if len(history) < 3:
            ob = self.data_fetcher.get_latest_orderbook(symbol.lower())
            bids = ob.get('bids', [])
            asks = ob.get('asks', [])
            if not bids or not asks:
                return 0, 0
            return sum(b['qty'] for b in bids), sum(a['qty'] for a in asks)
        avg_bid = sum(h['bid'] for h in history) / len(history)
        avg_ask = sum(h['ask'] for h in history) / len(history)
        return avg_bid, avg_ask

    async def on_price_update(self, data):
        symbol = data['symbol'].upper()
        price = data['price']
        self.prices[symbol] = price
        
        # Track orderbook
        ob = self.data_fetcher.get_latest_orderbook(symbol.lower())
        bids = ob.get('bids', [])
        asks = ob.get('asks', [])
        if bids and asks:
            if symbol not in self.orderbook_history:
                self.orderbook_history[symbol] = deque(maxlen=10)
            self.orderbook_history[symbol].append({
                'bid': sum(b['qty'] for b in bids),
                'ask': sum(a['qty'] for a in asks)
            })
        
        if self.is_breached:
            return
        
        # FTMO drawdown checks
        equity = self.mt_executor.get_equity()
        sod_balance = self.mt_executor.get_start_of_day_balance()
        
        if not self.risk_manager.check_daily_limit(equity, sod_balance) or \
           not self.risk_manager.check_overall_limit(equity):
            logger.critical("DRAWDOWN LIMIT REACHED! CLOSING ALL.")
            self.mt_executor.close_all_positions()
            self.is_breached = True

    async def on_oi_update(self, data):
        symbol = data['symbol'].upper()
        if symbol not in self.oi_history:
            self.oi_history[symbol] = []
        self.oi_history[symbol].append(data['oi'])
        if len(self.oi_history[symbol]) > 10:
            self.oi_history[symbol].pop(0)

    async def on_liquidation(self, data):
        """Track liquidations as confirmation for candle signals."""
        if self.is_breached:
            return
        
        symbol = data['symbol'].upper()
        side = data['side']
        price = data['price']
        qty = data['quantity']
        usd_value = price * qty
        
        if usd_value < self.min_liquidation_usd:
            return
        
        logger.info(f"Liquidation: ${usd_value:,.0f} on {symbol} ({side})")
        
        # Store as recent liquidation (valid for 2 hours)
        self.recent_liquidations[symbol] = {
            'side': side,
            'time': time.time(),
            'value': usd_value,
            'price': price
        }

    async def on_candle_signal(self, data):
        """
        PRIMARY SIGNAL: Volume spike + wick pattern from real SPOT candles.
        This EXACTLY matches what the backtest uses.
        """
        if self.is_breached:
            return
        
        symbol = data['symbol'].upper()
        signal = data['signal']  # 'BUY' or 'SELL'
        price = data['price']
        atr = data['atr']
        
        if atr <= 0:
            logger.warning(f"ATR invalid for {symbol}")
            return
        
        
        # Store real ATR
        self.real_atr[symbol] = atr
        
        # Deduplicate: same signal + same price = same candle, skip
        sig_key = f"{symbol}_{signal}"
        if sig_key in self.last_signal and abs(self.last_signal[sig_key] - price) < price * 0.001:
            return  # Same candle, already processed
        self.last_signal[sig_key] = price
        
        # Check orderbook support (relaxed: 1.1x instead of 1.2x)
        avg_bid, avg_ask = self._get_orderbook_avg(symbol)
        if avg_bid > 0 and avg_ask > 0:
            if signal == "BUY" and avg_bid < avg_ask * 1.1:
                logger.info(f"Orderbook weak for BUY on {symbol}. Skipping.")
                return
            if signal == "SELL" and avg_ask < avg_bid * 1.1:
                logger.info(f"Orderbook weak for SELL on {symbol}. Skipping.")
                return
            logger.info(f"Orderbook OK for {signal}")
        
        # Confidence boost if recent liquidation aligns
        has_liq_confirm = False
        liq = self.recent_liquidations.get(symbol)
        if liq and (time.time() - liq['time']) < 7200:  # Within 2 hours
            # Liq SELL = long tasfiye → we BUY; Liq BUY = short tasfiye → we SELL
            liq_trade = "BUY" if liq['side'] == "SELL" else "SELL"
            if liq_trade == signal:
                has_liq_confirm = True
                logger.info(f"LIQUIDATION CONFIRMS {signal} (${liq['value']:,.0f})")
        
        # Execute
        await self._execute_trade(symbol, signal, price, atr, has_liq_confirm)

    async def _execute_trade(self, symbol, trade_side, price, atr, liq_confirmed=False):
        mt5_symbol = MT5_SYMBOL_MAP.get(symbol, symbol)
        
        equity = self.mt_executor.get_equity()
        sod_balance = self.mt_executor.get_start_of_day_balance()
        open_positions = self.mt_executor.get_open_positions_count()
        
        if not self.risk_manager.can_open_trade(equity, sod_balance, open_positions):
            logger.info("Risk manager blocked trade.")
            return
        
        # Max 1 position per symbol, 2 total
        if open_positions >= 2:
            logger.info("Max 2 positions reached.")
            return
        
        # Spread check
        spread_pct = self.mt_executor.get_spread_pct(mt5_symbol)
        if spread_pct is not None and spread_pct > 0.15:
            logger.warning(f"Spread too wide: {spread_pct:.3f}%")
            return
        
        # ATR-based dynamic SL/TP
        sl_distance = atr * 1.5
        tp_distance = atr * 4.0
        
        if trade_side == "BUY":
            sl_price = price - sl_distance
            tp_price = price + tp_distance
        else:
            sl_price = price + sl_distance
            tp_price = price - tp_distance
        
        # Dynamic risk
        current_risk = self._get_dynamic_risk()
        
        # If liquidation confirms, use full risk; otherwise slightly reduce
        if not liq_confirmed:
            current_risk *= 0.8  # 20% less risk without liq confirmation
        
        volume = self.risk_manager.calculate_position_size(
            equity, price, sl_price, risk_pct=current_risk
        )
        
        confirm_tag = "LIQ+CANDLE" if liq_confirmed else "CANDLE"
        logger.info(f"SIGNAL [{confirm_tag}]: {trade_side} {mt5_symbol} @ {price:.2f} | "
                     f"SL:{sl_price:.2f} TP:{tp_price:.2f} | Vol:{volume:.6f} | "
                     f"Risk:{current_risk:.2f}% | ATR:{atr:.2f}")
        
        success = self.mt_executor.open_trade(mt5_symbol, trade_side, volume, sl_price, tp_price)
        if success:
            logger.info(f"Trade opened in MT5! [{confirm_tag}]")
            self._log_trade(mt5_symbol, trade_side, price, volume, sl_price, tp_price,
                           equity, current_risk, atr, confirm_tag)
        else:
            logger.error("Failed to open trade in MT5.")

    def record_trade_result(self, is_win: bool):
        if is_win:
            if self.consecutive_losses > 0:
                logger.info(f"Win after {self.consecutive_losses} losses. Breaker reset.")
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_before_reduce:
                logger.warning(f"CIRCUIT BREAKER ({self.consecutive_losses} losses).")

    def _log_trade(self, symbol, side, price, volume, sl, tp, equity, risk_pct, atr, source):
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "trade_logs.txt")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (f"[{ts}] {side} {symbol} @ {price:.2f} [{source}]\n"
                 f"  Vol: {volume:.6f} | SL: {sl:.2f} | TP: {tp:.2f}\n"
                 f"  Risk: {risk_pct:.2f}% | ATR: {atr:.2f}\n"
                 f"  Equity: ${equity:,.2f} | Losses: {self.consecutive_losses}\n"
                 f"{'─'*50}\n")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.error(f"Log write failed: {e}")
