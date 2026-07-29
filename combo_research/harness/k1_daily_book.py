"""K1: Correlated-book risk, measured from settled outcomes.

For each event day, treat a quoter who SOLD 1 contract of every combo that
settled that day at its own traded price. P&L/ct = price - payout.
The dispersion ACROSS DAYS is the correlated-book risk -- it is measured,
not simulated, because the real joint outcomes are in the data.
"""
import gzip, json, math, statistics as st
from collections import defaultdict

FILES = [
    ('cross', '/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz'),
    ('nba',   '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz'),
]

day = defaultdict(lambda: {'n':0,'prem':0.0,'pay':0.0,'coll':0.0,'yes':0,'scal':0,'hold':[]})
skipped = defaultdict(int)
tot = 0

for tag, path in FILES:
    with gzip.open(path, 'rt') as f:
        for line in f:
            tot += 1
            r = json.loads(line)
            if r.get('st') != 'finalized':
                skipped['not_final'] += 1; continue
            lp = r.get('lp')
            if lp in (None, '', 0, '0'):
                skipped['no_price'] += 1; continue
            p = float(lp)
            if not (0.0 < p < 1.0):
                skipped['bad_price'] += 1; continue
            vol = float(r.get('vol') or 0)
            if vol <= 0:
                skipped['no_vol'] += 1; continue
            res = r.get('res')
            if res == 'yes':   payout = 1.0
            elif res == 'no':  payout = 0.0
            elif res == 'scalar':
                sv = r.get('sv')
                if sv is None: skipped['scalar_nosv'] += 1; continue
                payout = float(sv)
            else:
                skipped['res_'+str(res)] += 1; continue
            ct = r.get('ct')
            if not ct: skipped['no_ct'] += 1; continue
            d = ct[:10]
            b = day[d]
            b['n'] += 1
            b['prem'] += p
            b['pay']  += payout
            b['coll'] += (1.0 - p)
            if res == 'yes': b['yes'] += 1
            if res == 'scalar': b['scal'] += 1
            ot = r.get('ot')
            if ot:
                try:
                    import datetime as dt
                    a = dt.datetime.fromisoformat(ot.replace('Z','+00:00'))
                    z = dt.datetime.fromisoformat(ct.replace('Z','+00:00'))
                    b['hold'].append((z-a).total_seconds()/3600.0)
                except Exception: pass

print(f"rows read {tot:,}")
print("skipped:", dict(sorted(skipped.items(), key=lambda x:-x[1])[:8]))

rows = []
for d, b in sorted(day.items()):
    if b['n'] < 100:   # ignore stub days
        continue
    pnl = b['prem'] - b['pay']              # quoter P&L in contract-dollars
    rows.append({
        'day': d, 'n': b['n'],
        'pnl_ct': pnl / b['n'] * 100.0,     # cents per contract
        'ret_coll': pnl / b['coll'] * 100.0,# % return on collateral posted
        'meanpx': b['prem']/b['n']*100.0,
        'yesrate': b['yes']/b['n']*100.0,
        'hold_h': (st.median(b['hold']) if b['hold'] else float('nan')),
    })

print(f"\nusable event-days: {len(rows)}  total contracts: {sum(r['n'] for r in rows):,}")
print(f"{'day':12} {'n':>9} {'meanpx':>8} {'yes%':>7} {'pnl c/ct':>9} {'ret/coll%':>10} {'hold_h':>7}")
for r in rows:
    print(f"{r['day']:12} {r['n']:>9,} {r['meanpx']:>8.2f} {r['yesrate']:>7.2f} {r['pnl_ct']:>9.3f} {r['ret_coll']:>10.3f} {r['hold_h']:>7.1f}")

v = [r['pnl_ct'] for r in rows]
rc = [r['ret_coll'] for r in rows]
n = len(v)
mean = sum(v)/n; sd = st.pstdev(v)
print(f"\n=== DAILY P&L (cents per contract sold), n={n} days ===")
print(f"mean {mean:+.3f}  sd {sd:.3f}  min {min(v):+.3f}  max {max(v):+.3f}")
print(f"losing days: {sum(1 for x in v if x<0)}/{n} = {sum(1 for x in v if x<0)/n*100:.1f}%")
sv_ = sorted(v)
for q,lab in [(0.05,'p5'),(0.25,'p25'),(0.5,'p50'),(0.75,'p75'),(0.95,'p95')]:
    print(f"  {lab}: {sv_[int(q*(n-1))]:+.3f}")
print(f"Sharpe-like (mean/sd, per day): {mean/sd:.3f}")
mr = sum(rc)/len(rc)
print(f"\n=== RETURN ON COLLATERAL, per day ===")
print(f"mean {mr:+.3f}%  sd {st.pstdev(rc):.3f}%  min {min(rc):+.3f}%  max {max(rc):+.3f}%")
hs = [r['hold_h'] for r in rows if r['hold_h']==r['hold_h']]
if hs: print(f"\nmedian holding period (open->close): {st.median(hs):.1f}h")

# worst consecutive drawdown in cents/ct terms
peak=0.0; cum=0.0; dd=0.0
for x in v:
    cum += x
    peak = max(peak, cum)
    dd = min(dd, cum-peak)
print(f"\ncumulative P&L over window: {cum:+.2f} c/ct   max drawdown: {dd:+.2f} c/ct")
json.dump(rows, open('/tmp/combo_research/kill/k1_daily.json','w'), indent=1)
