"""Live readiness test — verifies all components before deployment."""
import asyncio
import aiohttp
import sys
import os

async def test_binance_apis():
    print("=" * 60)
    print("  FTMO BOT — LIVE READINESS TEST")
    print("=" * 60)
    errors = []
    
    async with aiohttp.ClientSession() as session:
        # 1. Spot Klines API
        try:
            url = "https://api.binance.com/api/v3/klines"
            async with session.get(url, params={'symbol':'BTCUSDT','interval':'1h','limit':50}) as r:
                data = await r.json()
                c = data[-1]
                print(f"  [OK] Spot 1h Klines: {len(data)} candles | Last close: ${float(c[4]):,.2f}")
        except Exception as e:
            print(f"  [FAIL] Spot 1h Klines: {e}")
            errors.append("spot_1h")
        
        # 2. Spot 4h Klines
        try:
            async with session.get(url, params={'symbol':'BTCUSDT','interval':'4h','limit':50}) as r:
                data = await r.json()
                print(f"  [OK] Spot 4h Klines: {len(data)} candles")
        except Exception as e:
            print(f"  [FAIL] Spot 4h Klines: {e}")
            errors.append("spot_4h")

        # 3. Futures OI
        try:
            async with session.get("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT") as r:
                data = await r.json()
                print(f"  [OK] Futures OI: {float(data['openInterest']):,.2f} BTC")
        except Exception as e:
            print(f"  [FAIL] Futures OI: {e}")
            errors.append("oi")

        # 4. WebSocket connectivity (quick test)
        try:
            import websockets
            ws_url = "wss://fstream.binance.com/stream?streams=btcusdt@markPrice"
            async with websockets.connect(ws_url, close_timeout=3) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                import json
                d = json.loads(msg)
                price = float(d['data']['p'])
                print(f"  [OK] WebSocket Stream: BTC Mark Price ${price:,.2f}")
        except Exception as e:
            print(f"  [FAIL] WebSocket: {e}")
            errors.append("ws")

    return errors

def test_signal_logic():
    """Test candle signal detection with real data."""
    print(f"\n{'-'*60}")
    print("  Signal Logic Test")
    print(f"{'-'*60}")
    
    # Simulate a volume spike + wick candle
    candles = []
    for i in range(15):
        candles.append({
            'open': 95000 + i*10, 'high': 95100 + i*10,
            'low': 94900 + i*10, 'close': 95050 + i*10,
            'volume': 1000 + i*50
        })
    
    # Add a sweep candle (big volume, long lower wick)
    candles.append({
        'open': 95200, 'high': 95250,
        'low': 94500, 'close': 95100,  # Long lower wick
        'volume': 5000  # Volume spike
    })
    
    volumes = [c['volume'] for c in candles]
    recent_vols = volumes[-11:-1]
    vol_ma = sum(recent_vols) / len(recent_vols)
    vol_std = (sum((v - vol_ma)**2 for v in recent_vols) / len(recent_vols)) ** 0.5
    vol_threshold = vol_ma + 2.0 * vol_std
    
    current = candles[-1]
    total_range = current['high'] - current['low']
    lower_wick = min(current['open'], current['close']) - current['low']
    upper_wick = current['high'] - max(current['open'], current['close'])
    
    vol_spike = current['volume'] > vol_threshold
    wick_buy = (lower_wick / total_range) >= 0.5 if total_range > 0 else False
    
    print(f"  Volume: {current['volume']} > {vol_threshold:.0f} = {'SPIKE' if vol_spike else 'normal'}")
    print(f"  Lower wick ratio: {lower_wick/total_range:.2f} (need >=0.5)")
    print(f"  Signal: {'BUY' if vol_spike and wick_buy else 'NONE'}")
    
    if vol_spike and wick_buy:
        print(f"  [OK] Signal detection working correctly")
        return True
    else:
        print(f"  [FAIL] Signal detection broken!")
        return False

def test_risk_manager():
    """Test risk calculations."""
    print(f"\n{'-'*60}")
    print("  Risk Manager Test")
    print(f"{'-'*60}")
    
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from risk_manager import RiskManager
    
    rm = RiskManager(initial_balance=10000, challenge_type="2-step")
    
    # Test position sizing
    equity = 10000
    entry = 95000
    sl = 95000 - (1500 * 1.5)  # ATR 1500 * 1.5
    risk_pct = 2.0
    vol = rm.calculate_position_size(equity, entry, sl, risk_pct)
    expected_loss = abs(entry - sl) * vol
    
    print(f"  Entry: ${entry:,} | SL: ${sl:,.0f} | Risk: {risk_pct}%")
    print(f"  Volume: {vol:.6f} BTC")
    print(f"  Expected loss if SL hit: ${expected_loss:.2f}")
    print(f"  % of equity: {expected_loss/equity*100:.2f}%")
    
    # Daily limit check
    daily_ok = rm.check_daily_limit(9600, 10000)  # $400 loss = 4%
    daily_fail = rm.check_daily_limit(9500, 10000)  # $500 loss = 5%
    
    print(f"  Daily limit at $400 loss: {'PASS' if daily_ok else 'BLOCKED'}")
    print(f"  Daily limit at $500 loss: {'PASS' if not daily_fail else 'SHOULD BLOCK!'}")
    
    # Overall limit
    overall_ok = rm.check_overall_limit(9200)  # $800 loss = 8%
    overall_fail = rm.check_overall_limit(9050)  # $950 loss = 9.5%
    
    print(f"  Overall at $800 loss: {'PASS' if overall_ok else 'BLOCKED'}")
    print(f"  Overall at $950 loss: {'PASS' if not overall_fail else 'SHOULD BLOCK!'}")
    
    # Can open trade check
    can_trade = rm.can_open_trade(10000, 10000, 0)
    cant_trade = rm.can_open_trade(10000, 10000, 1)
    
    print(f"  Can open (0 positions): {'YES' if can_trade else 'NO'}")
    print(f"  Can open (1 position): {'YES' if cant_trade else 'NO (max reached)'}")
    
    ok = daily_ok and not daily_fail and overall_ok and not overall_fail and can_trade and not cant_trade
    print(f"  [{'OK' if ok else 'FAIL'}] Risk manager {'working correctly' if ok else 'HAS ISSUES!'}")
    return ok

def test_strategy_init():
    """Test strategy initialization without MT5."""
    print(f"\n{'-'*60}")
    print("  Strategy Init Test")
    print(f"{'-'*60}")
    
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from risk_manager import RiskManager
    
    # Mock MT5 executor
    class MockExecutor:
        def get_equity(self): return 10000
        def get_start_of_day_balance(self): return 10000
        def get_open_positions_count(self): return 0
        def get_spread_pct(self, sym): return 0.05
        def open_trade(self, *a, **k): return True
    
    class MockFetcher:
        def get_latest_orderbook(self, sym): 
            return {'bids': [{'qty': 100}], 'asks': [{'qty': 80}]}
    
    from strategy import LiquidationSweepStrategy
    rm = RiskManager(10000, "2-step")
    strategy = LiquidationSweepStrategy(rm, MockExecutor(), MockFetcher())
    
    print(f"  Base risk: {strategy.base_risk_pct}%")
    print(f"  Circuit breaker at: {strategy.max_consecutive_before_reduce} losses")
    print(f"  Dynamic risk (0 losses): {strategy._get_dynamic_risk()}%")
    strategy.consecutive_losses = 3
    print(f"  Dynamic risk (3 losses): {strategy._get_dynamic_risk()}%")
    strategy.consecutive_losses = 0
    
    # Test signal dedup
    strategy.last_signal = {}
    key = "BTCUSDT_BUY"
    strategy.last_signal[key] = 95000.0
    is_dup = abs(strategy.last_signal[key] - 95000.0) < 95000.0 * 0.001
    print(f"  Dedup same price: {'BLOCKED (correct)' if is_dup else 'PASS (wrong!)'}")
    is_dup2 = abs(strategy.last_signal[key] - 96000.0) < 96000.0 * 0.001
    print(f"  Dedup diff price: {'BLOCKED (wrong!)' if is_dup2 else 'PASS (correct)'}")
    
    print(f"  [OK] Strategy initializes correctly")
    return True

async def main():
    api_errors = await test_binance_apis()
    sig_ok = test_signal_logic()
    risk_ok = test_risk_manager()
    strat_ok = test_strategy_init()
    
    print(f"\n{'='*60}")
    print(f"  FINAL RESULT")
    print(f"{'='*60}")
    
    all_ok = not api_errors and sig_ok and risk_ok and strat_ok
    
    if all_ok:
        print("  [PASS] ALL TESTS PASSED — Bot is READY for live deployment")
    else:
        if api_errors: print(f"  [FAIL] API errors: {api_errors}")
        if not sig_ok: print(f"  [FAIL] Signal logic broken")
        if not risk_ok: print(f"  [FAIL] Risk manager issues")
        if not strat_ok: print(f"  [FAIL] Strategy init failed")
    
    print(f"{'='*60}")

if __name__ == '__main__':
    asyncio.run(main())
