"""
Binance SPOT verilerini indir (Futures degil!)
FTMO CFD fiyatlamasi spot'a yakindir.
180 gunluk 15m, 1h, 4h mumlar — BTCUSDT & ETHUSDT SPOT.
"""
import requests, pandas as pd, os, time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# SPOT API (not futures!)
BASE = "https://api.binance.com/api/v3/klines"

def fetch(symbol, interval, days=180):
    limit = 1000
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 3600 * 1000)
    
    all_data = []
    while start_ms < end_ms:
        params = {
            'symbol': symbol, 'interval': interval,
            'startTime': start_ms, 'limit': limit
        }
        resp = requests.get(BASE, params=params, timeout=30)
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        start_ms = data[-1][0] + 1
        time.sleep(0.2)
    
    df = pd.DataFrame(all_data, columns=[
        'timestamp','open','high','low','close','volume',
        'close_time','quote_vol','trades','taker_buy_vol','taker_buy_quote','ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    df = df[['open','high','low','close','volume']]
    df = df[~df.index.duplicated(keep='first')]
    return df

symbols = ['BTCUSDT', 'ETHUSDT']
timeframes = ['15m', '1h', '4h']

for sym in symbols:
    for tf in timeframes:
        print(f"SPOT indiriliyor: {sym} {tf}...")
        df = fetch(sym, tf, days=180)
        fpath = os.path.join(DATA_DIR, f"{sym}_{tf}_180d_spot.csv")
        df.to_csv(fpath)
        print(f"  {len(df)} mum -> {fpath}")
        print(f"  {df.index[0]} ~ {df.index[-1]}")

print("\nTum SPOT verileri guncellendi!")
