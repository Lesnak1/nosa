import pandas as pd
from binance.client import Client
import datetime
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BacktestData")

def fetch_historical_klines(symbol, interval, lookback_days):
    logger.info(f"Fetching {lookback_days} days of {interval} klines for {symbol}...")
    
    # Initialize anonymous client
    client = Client()
    
    start_str = f"{lookback_days} days ago UTC"
    klines = client.futures_historical_klines(symbol, interval, start_str)
    
    if not klines:
        logger.error(f"Failed to fetch data for {symbol}")
        return None
        
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    
    # Convert types
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
        
    # Keep only necessary columns
    df = df[['open', 'high', 'low', 'close', 'volume']]
    
    os.makedirs('data', exist_ok=True)
    filepath = f"data/{symbol}_{interval}_{lookback_days}d.csv"
    df.to_csv(filepath)
    logger.info(f"Saved {len(df)} rows to {filepath}")
    
    return filepath

if __name__ == "__main__":
    timeframes = [Client.KLINE_INTERVAL_15MINUTE, Client.KLINE_INTERVAL_1HOUR, Client.KLINE_INTERVAL_4HOUR]
    symbols = ['BTCUSDT', 'ETHUSDT']
    days = 180 # 6 months of data for robust testing
    
    for sym in symbols:
        for tf in timeframes:
            fetch_historical_klines(sym, tf, days)
