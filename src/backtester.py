import pandas as pd
import numpy as np
import logging
from typing import List, Dict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Backtester")

class BacktestEngine:
    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        
        # FTMO Constraints
        self.max_daily_loss_pct = 0.045
        self.max_overall_loss_pct = 0.09
        self.risk_per_trade_pct = 0.005
        
        self.open_trades = []
        self.trade_history = []
        self.breached = False
        self.breach_reason = ""
        
        self.current_day = None
        self.start_of_day_balance = initial_balance

    def calculate_proxy_signals(self, df: pd.DataFrame, vol_window: int = 50, vol_mult: float = 3.0, wick_ratio: float = 0.6):
        """
        Calculates liquidation sweep signals based on price action wicks and abnormal volume.
        Returns the modified DataFrame.
        """
        # Volume moving average and std dev
        df['vol_ma'] = df['volume'].rolling(vol_window).mean()
        df['vol_std'] = df['volume'].rolling(vol_window).std()
        
        # Wick calculations
        df['body'] = abs(df['close'] - df['open'])
        df['upper_wick'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['lower_wick'] = df[['open', 'close']].min(axis=1) - df['low']
        df['total_range'] = df['high'] - df['low']
        
        # Avoid division by zero
        df['total_range'] = df['total_range'].replace(0, 0.0001)
        
        # Conditions for LONG signal (sweeping lows - lower wick is large)
        long_cond1 = df['volume'] > (df['vol_ma'] + vol_mult * df['vol_std'])
        long_cond2 = (df['lower_wick'] / df['total_range']) >= wick_ratio
        df['signal_long'] = long_cond1 & long_cond2
        
        # Conditions for SHORT signal (sweeping highs - upper wick is large)
        short_cond1 = df['volume'] > (df['vol_ma'] + vol_mult * df['vol_std'])
        short_cond2 = (df['upper_wick'] / df['total_range']) >= wick_ratio
        df['signal_short'] = short_cond1 & short_cond2
        
        return df

    def run(self, symbol: str, df: pd.DataFrame):
        logger.info(f"Starting backtest for {symbol} on {len(df)} rows.")
        
        df = self.calculate_proxy_signals(df)
        df = df.dropna()
        
        for idx, row in df.iterrows():
            if self.breached:
                break
                
            current_date = idx.date()
            if self.current_day != current_date:
                self.current_day = current_date
                self.start_of_day_balance = self.balance
                
            current_price = row['close']
            
            # 1. Update open trades & check SL/TP
            trades_to_close = []
            for trade in self.open_trades:
                if trade['side'] == 'LONG':
                    if row['low'] <= trade['sl']:
                        trade['exit_price'] = trade['sl']
                        trade['exit_reason'] = 'SL'
                        trades_to_close.append(trade)
                    elif row['high'] >= trade['tp']:
                        trade['exit_price'] = trade['tp']
                        trade['exit_reason'] = 'TP'
                        trades_to_close.append(trade)
                else: # SHORT
                    if row['high'] >= trade['sl']:
                        trade['exit_price'] = trade['sl']
                        trade['exit_reason'] = 'SL'
                        trades_to_close.append(trade)
                    elif row['low'] <= trade['tp']:
                        trade['exit_price'] = trade['tp']
                        trade['exit_reason'] = 'TP'
                        trades_to_close.append(trade)
            
            # Process closures
            for trade in trades_to_close:
                profit = (trade['exit_price'] - trade['entry_price']) * trade['size'] if trade['side'] == 'LONG' else (trade['entry_price'] - trade['exit_price']) * trade['size']
                # Rough commision/spread sim: 0.05%
                commission = (trade['entry_price'] * trade['size']) * 0.0005
                profit -= commission
                
                self.balance += profit
                trade['profit'] = profit
                trade['exit_time'] = idx
                self.trade_history.append(trade)
                self.open_trades.remove(trade)
            
            # 2. Update Equity and Check FTMO Rules
            floating_pnl = 0
            for trade in self.open_trades:
                if trade['side'] == 'LONG':
                    floating_pnl += (current_price - trade['entry_price']) * trade['size']
                else:
                    floating_pnl += (trade['entry_price'] - current_price) * trade['size']
                    
            self.equity = self.balance + floating_pnl
            
            # Check Daily Loss
            daily_loss_limit = self.start_of_day_balance * (1 - self.max_daily_loss_pct)
            if self.equity <= daily_loss_limit:
                self.breached = True
                self.breach_reason = f"Daily Loss Limit Reached on {idx}. Equity: {self.equity}"
                logger.error(self.breach_reason)
                break
                
            # Check Overall Loss
            overall_loss_limit = self.initial_balance * (1 - self.max_overall_loss_pct)
            if self.equity <= overall_loss_limit:
                self.breached = True
                self.breach_reason = f"Overall Loss Limit Reached on {idx}. Equity: {self.equity}"
                logger.error(self.breach_reason)
                break
            
            # 3. Check for new signals
            if len(self.open_trades) == 0: # Only 1 trade at a time for safety
                if row['signal_long']:
                    sl_dist = current_price * 0.005 # 0.5% SL
                    tp_dist = current_price * 0.010 # 1.0% TP (1:2 RR)
                    
                    sl = current_price - sl_dist
                    tp = current_price + tp_dist
                    
                    risk_amount = self.equity * self.risk_per_trade_pct
                    size = risk_amount / sl_dist
                    
                    self.open_trades.append({
                        'symbol': symbol,
                        'side': 'LONG',
                        'entry_time': idx,
                        'entry_price': current_price,
                        'sl': sl,
                        'tp': tp,
                        'size': size
                    })
                    
                elif row['signal_short']:
                    sl_dist = current_price * 0.005
                    tp_dist = current_price * 0.010
                    
                    sl = current_price + sl_dist
                    tp = current_price - tp_dist
                    
                    risk_amount = self.equity * self.risk_per_trade_pct
                    size = risk_amount / sl_dist
                    
                    self.open_trades.append({
                        'symbol': symbol,
                        'side': 'SHORT',
                        'entry_time': idx,
                        'entry_price': current_price,
                        'sl': sl,
                        'tp': tp,
                        'size': size
                    })

    def print_results(self):
        logger.info("\n--- BACKTEST RESULTS ---")
        if self.breached:
            logger.info(f"TEST FAILED: {self.breach_reason}")
            
        logger.info(f"Final Balance: ${self.balance:.2f}")
        logger.info(f"Total Return: {((self.balance - self.initial_balance) / self.initial_balance) * 100:.2f}%")
        
        if not self.trade_history:
            logger.info("No trades executed.")
            return
            
        df_trades = pd.DataFrame(self.trade_history)
        total_trades = len(df_trades)
        winning_trades = len(df_trades[df_trades['profit'] > 0])
        losing_trades = len(df_trades[df_trades['profit'] <= 0])
        
        win_rate = winning_trades / total_trades * 100
        
        gross_profit = df_trades[df_trades['profit'] > 0]['profit'].sum()
        gross_loss = abs(df_trades[df_trades['profit'] < 0]['profit'].sum())
        
        profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
        
        logger.info(f"Total Trades: {total_trades}")
        logger.info(f"Win Rate: {win_rate:.2f}%")
        logger.info(f"Profit Factor: {profit_factor:.2f}")
        logger.info(f"Max Profit Trade: ${df_trades['profit'].max():.2f}")
        logger.info(f"Max Loss Trade: ${df_trades['profit'].min():.2f}")

if __name__ == "__main__":
    import os
    
    symbols = ['BTCUSDT', 'ETHUSDT']
    timeframes = ['1m', '15m', '1h', '4h']
    
    results_summary = []
    
    for symbol in symbols:
        for tf in timeframes:
            filepath = f"data/{symbol}_{tf}_180d.csv"
            if not os.path.exists(filepath):
                logger.warning(f"File {filepath} not found. Skipping.")
                continue
                
            df_raw = pd.read_csv(filepath, index_col=0, parse_dates=True)
            logger.info(f"\n=====================================")
            logger.info(f"STARTING OPTIMIZATION: {symbol} - {tf}")
            logger.info(f"=====================================")
            
            best_pf = 0
            best_params = {}
            
            # For higher timeframes, liquidations/wicks happen less often, so we can lower the volume multiplier
            vol_mults = [2.0, 3.0] if tf in ['1h', '4h'] else [3.0, 4.0]
            wicks = [0.6, 0.7]
            sl_pcts = [0.005, 0.01] if tf in ['1h', '4h'] else [0.003, 0.005]
            tp_pcts = [0.01, 0.02, 0.03] if tf in ['1h', '4h'] else [0.006, 0.010]
            
            for vol_mult in vol_mults:
                for wick_ratio in wicks:
                    for sl_pct in sl_pcts:
                        for tp_pct in tp_pcts:
                            
                            class OptEngine(BacktestEngine):
                                def run(self, sym, df):
                                    df = self.calculate_proxy_signals(df, vol_window=50, vol_mult=vol_mult, wick_ratio=wick_ratio)
                                    df = df.dropna()
                                    for idx, row in df.iterrows():
                                        if self.breached: break
                                        
                                        current_date = idx.date()
                                        if self.current_day != current_date:
                                            self.current_day = current_date
                                            self.start_of_day_balance = self.balance
                                            
                                        current_price = row['close']
                                        
                                        trades_to_close = []
                                        for trade in self.open_trades:
                                            if trade['side'] == 'LONG':
                                                if row['low'] <= trade['sl']:
                                                    trade['exit_price'] = trade['sl']; trade['exit_reason'] = 'SL'; trades_to_close.append(trade)
                                                elif row['high'] >= trade['tp']:
                                                    trade['exit_price'] = trade['tp']; trade['exit_reason'] = 'TP'; trades_to_close.append(trade)
                                            else:
                                                if row['high'] >= trade['sl']:
                                                    trade['exit_price'] = trade['sl']; trade['exit_reason'] = 'SL'; trades_to_close.append(trade)
                                                elif row['low'] <= trade['tp']:
                                                    trade['exit_price'] = trade['tp']; trade['exit_reason'] = 'TP'; trades_to_close.append(trade)
                                                    
                                        for trade in trades_to_close:
                                            profit = (trade['exit_price'] - trade['entry_price']) * trade['size'] if trade['side'] == 'LONG' else (trade['entry_price'] - trade['exit_price']) * trade['size']
                                            commission = (trade['entry_price'] * trade['size']) * 0.0005
                                            profit -= commission
                                            self.balance += profit
                                            trade['profit'] = profit; self.trade_history.append(trade); self.open_trades.remove(trade)
                                            
                                        floating_pnl = 0
                                        for trade in self.open_trades:
                                            floating_pnl += (current_price - trade['entry_price']) * trade['size'] if trade['side'] == 'LONG' else (trade['entry_price'] - current_price) * trade['size']
                                        self.equity = self.balance + floating_pnl
                                        
                                        if self.equity <= self.start_of_day_balance * (1 - self.max_daily_loss_pct): self.breached = True; break
                                        if self.equity <= self.initial_balance * (1 - self.max_overall_loss_pct): self.breached = True; break
                                        
                                        if len(self.open_trades) == 0:
                                            if row['signal_long']:
                                                sl = current_price * (1 - sl_pct)
                                                tp = current_price * (1 + tp_pct)
                                                size = (self.equity * self.risk_per_trade_pct) / (current_price * sl_pct)
                                                self.open_trades.append({'symbol': sym, 'side': 'LONG', 'entry_time': idx, 'entry_price': current_price, 'sl': sl, 'tp': tp, 'size': size})
                                            elif row['signal_short']:
                                                sl = current_price * (1 + sl_pct)
                                                tp = current_price * (1 - tp_pct)
                                                size = (self.equity * self.risk_per_trade_pct) / (current_price * sl_pct)
                                                self.open_trades.append({'symbol': sym, 'side': 'SHORT', 'entry_time': idx, 'entry_price': current_price, 'sl': sl, 'tp': tp, 'size': size})

                            opt_engine = OptEngine(initial_balance=10000.0)
                            opt_engine.run(symbol, df_raw)
                            
                            df_trades = pd.DataFrame(opt_engine.trade_history)
                            if len(df_trades) > 5 and not opt_engine.breached:
                                gross_profit = df_trades[df_trades['profit'] > 0]['profit'].sum()
                                gross_loss = abs(df_trades[df_trades['profit'] < 0]['profit'].sum())
                                pf = gross_profit / gross_loss if gross_loss != 0 else 0
                                win_rate = len(df_trades[df_trades['profit'] > 0]) / len(df_trades) * 100
                                ret = ((opt_engine.balance-10000)/10000)*100
                                
                                if pf > best_pf and ret > 0:
                                    best_pf = pf
                                    best_params = {'tf': tf, 'sym': symbol, 'vol_mult': vol_mult, 'wick': wick_ratio, 'sl': sl_pct, 'tp': tp_pct, 'pf': pf, 'wr': win_rate, 'ret': ret, 'trades': len(df_trades)}
                                    
            if best_params:
                logger.info(f"*** BEST FOR {symbol} {tf} *** -> {best_params}")
                results_summary.append(best_params)
            else:
                logger.info(f"No profitable setup found for {symbol} {tf}")
                
    logger.info("\n=====================================")
    logger.info("FINAL OPTIMIZATION SUMMARY:")
    for res in sorted(results_summary, key=lambda x: x['pf'], reverse=True):
        logger.info(res)
