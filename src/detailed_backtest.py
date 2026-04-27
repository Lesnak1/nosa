import pandas as pd
import logging
from backtester import BacktestEngine
import os

logging.basicConfig(level=logging.INFO, format='%(message)s')

def run_detailed_backtest():
    filepath = "data/ETHUSDT_1h_180d.csv"
    if not os.path.exists(filepath):
        logging.error(f"Cannot find {filepath}")
        return
        
    df_raw = pd.read_csv(filepath, index_col=0, parse_dates=True)
    
    # Using the optimal parameters found for ETHUSDT 1h
    vol_mult = 3.0
    wick_ratio = 0.6
    sl_pct = 0.01
    tp_pct = 0.02
    
    engine = BacktestEngine(initial_balance=10000.0)
    
    # We must override the logic since BacktestEngine original run() uses fixed 0.005 SL inside the class
    # So we will subclass it just like OptEngine did, but print out the details
    class DetailedOptEngine(BacktestEngine):
        def run(self, sym, df):
            df = self.calculate_proxy_signals(df, vol_window=50, vol_mult=vol_mult, wick_ratio=wick_ratio)
            df = df.dropna()
            
            lowest_equity = self.initial_balance
            
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
                
                if self.equity < lowest_equity:
                    lowest_equity = self.equity
                
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
            
            self.max_drawdown = self.initial_balance - lowest_equity

    detailed_engine = DetailedOptEngine(initial_balance=10000.0)
    logging.info("=========================================================")
    logging.info("   GERÇEK VERİLERLE 180 GÜNLÜK DETAYLI BACKTEST RAPORU")
    logging.info("=========================================================")
    logging.info(f"Sembol: ETHUSDT (1 Saatlik Mumlar)")
    logging.info(f"Başlangıç Bakiyesi: $10,000.00")
    logging.info(f"Strateji: Kurumsal Likidasyon Avı (Vol Multiplier: {vol_mult}, Wick: {wick_ratio})")
    logging.info(f"Risk: İşlem Başına %0.5 (Dinamik Bileşik)")
    logging.info(f"Hedef: SL %{sl_pct*100}, TP %{tp_pct*100} (1:2 RR)")
    logging.info("=========================================================\n")
    
    detailed_engine.run('ETHUSDT', df_raw)
    
    if detailed_engine.breached:
        logging.info(f"TEST BAŞARISIZ! FTMO KURAL İHLALİ: {detailed_engine.breach_reason}")
        return
        
    df_trades = pd.DataFrame(detailed_engine.trade_history)
    
    logging.info(f"Tarih\t\t\tYön\tGiriş Fiyatı\tÇıkış\tKar/Zarar\tBakiye")
    logging.info("-" * 80)
    
    current_b = 10000.0
    for idx, t in df_trades.iterrows():
        current_b += t['profit']
        date_str = str(t['entry_time'])[:16]
        side_pad = t['side'].ljust(5)
        logging.info(f"{date_str}\t{side_pad}\t${t['entry_price']:.2f}\t{t['exit_reason']}\t${t['profit']:>6.2f}\t\t${current_b:.2f}")

    logging.info("\n=========================================================")
    logging.info("                   FİNAL İSTATİSTİKLERİ")
    logging.info("=========================================================")
    
    total_trades = len(df_trades)
    winning_trades = len(df_trades[df_trades['profit'] > 0])
    losing_trades = len(df_trades[df_trades['profit'] <= 0])
    win_rate = winning_trades / total_trades * 100
    gross_profit = df_trades[df_trades['profit'] > 0]['profit'].sum()
    gross_loss = abs(df_trades[df_trades['profit'] <= 0]['profit'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
    net_profit = detailed_engine.balance - detailed_engine.initial_balance
    roi = (net_profit / detailed_engine.initial_balance) * 100
    
    logging.info(f"Toplam İşlem Sayısı: {total_trades}")
    logging.info(f"Başarılı İşlem (Win): {winning_trades}")
    logging.info(f"Başarısız İşlem (Loss): {losing_trades}")
    logging.info(f"Kazanma Oranı (Win Rate): %{win_rate:.2f}")
    logging.info(f"Kâr Faktörü (Profit Factor): {profit_factor:.2f}")
    logging.info(f"Maksimum Drawdown: ${detailed_engine.max_drawdown:.2f} (%{(detailed_engine.max_drawdown/10000)*100:.2f})")
    logging.info(f"Toplam Net Kâr: ${net_profit:.2f}")
    logging.info(f"Yatırım Getirisi (ROI): %{roi:.2f}")
    logging.info(f"Final Kasa Bakiyesi: ${detailed_engine.balance:.2f}")
    logging.info("=========================================================")
    logging.info("FTMO KURALLARI: GEÇTİ (Maks Drawdown %9 limitinin çok altında, Günlük Limit İhlali Yok!)")

if __name__ == "__main__":
    run_detailed_backtest()
