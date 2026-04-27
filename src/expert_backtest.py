"""
v5.1 FAST — Multi-TF (1h+4h) + Higher Risk
BTC+ETH, 2.5% risk, lower thresholds for more signals
"""
import pandas as pd
import numpy as np
import os, logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("v51")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def prep(df, vw=10, vm=2.0, wr=0.4):
    df = df.copy()
    df['vma'] = df['volume'].rolling(vw).mean()
    df['vs'] = df['volume'].rolling(vw).std()
    df['uw'] = df['high'] - df[['open','close']].max(axis=1)
    df['lw'] = df[['open','close']].min(axis=1) - df['low']
    df['tr_'] = (df['high']-df['low']).replace(0,0.0001)
    vs = df['volume'] > (df['vma'] + vm * df['vs'])
    h,l,pc = df['high'],df['low'],df['close'].shift(1)
    tr = pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['sig_l'] = vs & ((df['lw']/df['tr_']) >= wr)
    df['sig_s'] = vs & ((df['uw']/df['tr_']) >= wr)
    return df.dropna()


def run_multi_tf(dfs, risk=0.025, sl_m=1.5, tp_m=4.0):
    """Run backtest combining signals from multiple timeframes."""
    ib=10000; bal=ib; pk=ib; mdd=0; ot=None
    trades=[]; cl=0; sod=ib; cd=None; tdays=set()
    t10=None; t5=None; br=False
    last_entry_time = None
    cooldown_hours = 4  # 4 hour cooldown between trades
    
    # Merge all signals into one timeline
    signals = []
    for label, df in dfs.items():
        for i, r in df.iterrows():
            if r['sig_l'] or r['sig_s']:
                signals.append((i, label, r))
    
    # Also need price action for SL/TP checks
    # Use the finest timeframe for price tracking
    all_bars = []
    for label, df in dfs.items():
        for i, r in df.iterrows():
            all_bars.append((i, label, r))
    all_bars.sort(key=lambda x: x[0])
    
    for ts, label, r in all_bars:
        if br: break
        d = ts.date()
        if cd != d: cd = d; sod = bal
        p = r['close']; atr = r['atr']
        
        # Check open trade
        if ot:
            t = ot
            hs = ht = False; ep = 0
            if t['s'] == 'L':
                if r['low'] <= t['sl']: hs=True; ep=t['sl']
                elif r['high'] >= t['tp']: ht=True; ep=t['tp']
            else:
                if r['high'] >= t['sl']: hs=True; ep=t['sl']
                elif r['low'] <= t['tp']: ht=True; ep=t['tp']
            if hs or ht:
                pnl = (ep-t['ep'])*t['sz'] if t['s']=='L' else (t['ep']-ep)*t['sz']
                pnl -= t['ep']*t['sz']*0.001
                bal += pnl
                if pnl > 0: cl = 0
                else: cl += 1
                trades.append({**t,'xp':ep,'xt':ts,'pnl':pnl,'xr':'SL' if hs else 'TP'})
                ot = None
                tdays.add(d)
                if not t10 and bal >= ib*1.10: t10 = ts
                if not t5 and bal >= ib*1.05: t5 = ts
        
        # Equity
        fl = 0
        if ot: fl = (p-ot['ep'])*ot['sz'] if ot['s']=='L' else (ot['ep']-p)*ot['sz']
        eq = bal + fl
        if eq > pk: pk = eq
        dd_ = (pk-eq)/pk*100
        if dd_ > mdd: mdd = dd_
        if eq <= sod-(ib*0.045): br=True; break
        if eq <= ib*0.91: br=True; break
        
        # Open new trade
        if not ot and pd.notna(atr) and atr > 0:
            # Cooldown check
            if last_entry_time and (ts - last_entry_time).total_seconds() < cooldown_hours*3600:
                continue
            
            side = None
            if r['sig_l']: side = 'L'
            elif r['sig_s']: side = 'S'
            if side:
                sld = atr*sl_m; tpd = atr*tp_m
                rp = risk
                if cl >= 3: rp *= 0.5
                ra = eq*rp; dc = ib*0.045*0.5
                if ra > dc: ra = dc
                sz = ra/sld
                if side == 'L': sl_ = p-sld; tp_ = p+tpd
                else: sl_ = p+sld; tp_ = p-tpd
                ot = {'s':side,'et':ts,'ep':p,'sl':sl_,'tp':tp_,'sz':sz,'tf':label}
                tdays.add(d)
                last_entry_time = ts
    
    return trades, br, mdd, t10, t5, tdays, bal


def main():
    log.info("="*90)
    log.info("  v5.1 MULTI-TF FAST — 1h+4h birlikte, yuksek risk")
    log.info("="*90)
    
    results = []
    
    for sym in ['BTCUSDT', 'ETHUSDT']:
        fp1h = os.path.join(DATA_DIR, f"{sym}_1h_180d_spot.csv")
        fp4h = os.path.join(DATA_DIR, f"{sym}_4h_180d_spot.csv")
        if not os.path.exists(fp1h) or not os.path.exists(fp4h):
            continue
        raw1h = pd.read_csv(fp1h, index_col=0, parse_dates=True)
        raw4h = pd.read_csv(fp4h, index_col=0, parse_dates=True)
        log.info(f"\n  {sym}: 1h={len(raw1h)} + 4h={len(raw4h)} mum")
        
        for risk in [0.020, 0.025, 0.030]:
            for vw in [10, 15]:
                for vm in [1.5, 2.0]:
                    for wr in [0.3, 0.4, 0.5]:
                        for sl_m in [1.0, 1.5]:
                            for tp_m in [3.0, 4.0]:
                                for cd in [2, 4, 8]:
                                    df1h = prep(raw1h.copy(), vw, vm, wr)
                                    df4h = prep(raw4h.copy(), vw, vm, wr)
                                    
                                    # Multi-TF: both 1h and 4h
                                    tr,br,mdd,t10,t5,td,fb = run_multi_tf(
                                        {'1h':df1h, '4h':df4h},
                                        risk, sl_m, tp_m
                                    )
                                    if tr and not br:
                                        dt = pd.DataFrame(tr)
                                        gp=dt[dt['pnl']>0]['pnl'].sum()
                                        gl=abs(dt[dt['pnl']<=0]['pnl'].sum())
                                        pf=gp/gl if gl>0 else 0
                                        net=fb-10000; nw=len(dt[dt['pnl']>0])
                                        if net>0 and len(dt)>=4 and pf>=1.0:
                                            d10=d5=None; f_=dt['et'].min()
                                            if t10: d10=(t10-f_).days
                                            if t5: d5=(t5-f_).days
                                            cs=0;st=[]
                                            for _,t in dt.iterrows():
                                                if t['pnl']<=0: cs+=1
                                                else:
                                                    if cs>0: st.append(cs)
                                                    cs=0
                                            if cs>0: st.append(cs)
                                            ms=max(st) if st else 0
                                            results.append({
                                                'sym':sym,'risk':risk,'vw':vw,'vm':vm,'wr':wr,
                                                'slm':sl_m,'tpm':tp_m,'cd':cd,
                                                'n':len(dt),'pf':round(pf,2),
                                                'w':round(nw/len(dt)*100,1),
                                                'dd':round(mdd,2),'net':round(net,0),
                                                'ms':ms,'d10':d10,'d5':d5,
                                                'h10':t10 is not None,'h5':t5 is not None,
                                                'td':len(td)
                                            })
                                    
                                    # 4h only
                                    tr2,br2,mdd2,t102,t52,td2,fb2 = run_multi_tf(
                                        {'4h':df4h}, risk, sl_m, tp_m
                                    )
                                    if tr2 and not br2:
                                        dt2 = pd.DataFrame(tr2)
                                        gp2=dt2[dt2['pnl']>0]['pnl'].sum()
                                        gl2=abs(dt2[dt2['pnl']<=0]['pnl'].sum())
                                        pf2=gp2/gl2 if gl2>0 else 0
                                        net2=fb2-10000; nw2=len(dt2[dt2['pnl']>0])
                                        if net2>0 and len(dt2)>=4 and pf2>=1.0:
                                            d102=d52=None; f2=dt2['et'].min()
                                            if t102: d102=(t102-f2).days
                                            if t52: d52=(t52-f2).days
                                            cs=0;st=[]
                                            for _,t in dt2.iterrows():
                                                if t['pnl']<=0: cs+=1
                                                else:
                                                    if cs>0: st.append(cs)
                                                    cs=0
                                            if cs>0: st.append(cs)
                                            ms2=max(st) if st else 0
                                            results.append({
                                                'sym':sym+'_4h','risk':risk,'vw':vw,'vm':vm,'wr':wr,
                                                'slm':sl_m,'tpm':tp_m,'cd':cd,
                                                'n':len(dt2),'pf':round(pf2,2),
                                                'w':round(nw2/len(dt2)*100,1),
                                                'dd':round(mdd2,2),'net':round(net2,0),
                                                'ms':ms2,'d10':d102,'d5':d52,
                                                'h10':t102 is not None,'h5':t52 is not None,
                                                'td':len(td2)
                                            })

    log.info(f"\n{'='*90}")
    log.info(f"  {len(results)} karli kombinasyon")
    log.info(f"{'='*90}")

    # Phase 1 <= 14 days
    f14 = sorted([r for r in results if r['h10'] and r['d10'] is not None and r['d10']<=14],
                 key=lambda x: (x['dd'], -x['pf']))
    if f14:
        log.info(f"\n  PHASE 1 <=14 GUN ({len(f14)} adet):")
        _pt(f14[:15])

    # Phase 1 <= 21 days + DD < 8
    f21 = sorted([r for r in results if r['h10'] and r['d10'] is not None and r['d10']<=21 and r['dd']<8],
                 key=lambda x: (x['d10'], x['dd']))
    if f21:
        log.info(f"\n  PHASE 1 <=21 GUN & DD<%8 ({len(f21)} adet):")
        _pt(f21[:15])

    # Phase 2 fast
    f2 = sorted([r for r in results if r['h5'] and r['d5'] is not None and r['d5']<=7],
                key=lambda x: (x['d5'], x['dd']))
    if f2:
        log.info(f"\n  PHASE 2 <=7 GUN ({len(f2)} adet):")
        _pt(f2[:10])

    # Top combined speed
    both = sorted([r for r in results if r['h10'] and r['h5'] and r['d10'] is not None and r['d5'] is not None],
                  key=lambda x: (x['d10']+x['d5'], x['dd']))
    if both:
        log.info(f"\n  EN HIZLI P1+P2 TOPLAM:")
        _pt(both[:10])
        b = both[0]
        log.info(f"\n  >>> OPTIMAL: {b['sym']} | R={b['risk']*100}% SL={b['slm']} TP={b['tpm']} "
                 f"VW={b['vw']} VM={b['vm']} WR={b['wr']}")
        log.info(f"  >>> P1: {b['d10']}d + P2: {b['d5']}d = {b['d10']+b['d5']}d toplam | "
                 f"DD {b['dd']}% | {b['n']} trade | PF {b['pf']}")

    log.info(f"\n{'='*90}")


def _pt(data):
    log.info(f"  {'Sym':<14} {'R%':>4} {'SLm':>4} {'TPm':>4} {'VW':>3} {'VM':>4} {'WR':>4} "
             f"{'#':>4} {'PF':>5} {'W%':>5} {'DD%':>5} {'Net$':>7} {'LS':>3} {'P1d':>4} {'P2d':>4}")
    log.info(f"  {'-'*85}")
    for r in data:
        p1=str(r['d10']) if r['d10'] is not None else '-'
        p2=str(r['d5']) if r['d5'] is not None else '-'
        log.info(f"  {r['sym']:<14} {r['risk']*100:>3.1f} {r['slm']:>4.1f} {r['tpm']:>4.1f} "
                 f"{r['vw']:>3} {r['vm']:>4.1f} {r['wr']:>4.1f} "
                 f"{r['n']:>4} {r['pf']:>5.2f} {r['w']:>5.1f} {r['dd']:>5.2f} "
                 f"${r['net']:>+6.0f} {r['ms']:>3} {p1:>4} {p2:>4}")


if __name__ == '__main__':
    main()
