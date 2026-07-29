"""K6: the decision-relevant number -- edge and decay INSIDE the target zone
that K5 identified (few legs, mid price), weekly, with a CI on the slope."""
import gzip, json
import numpy as np
from collections import defaultdict
FILES=['/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz',
       '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz']
HAIRCUT=1.14/2.59
def zone(p,nl):  # target: 2-4 legs, 10-35c
    return (2<=nl<=4) and (0.10<=p<0.35)
day=defaultdict(lambda:[0,0.0,0.0,0.0,0.0,0.0])
allday=defaultdict(lambda:[0,0.0,0.0,0.0,0.0,0.0])
for path in FILES:
    with gzip.open(path,'rt') as f:
        for line in f:
            r=json.loads(line)
            if r.get('st')!='finalized': continue
            lp=r.get('lp')
            if not lp: continue
            p=float(lp)
            if not (0.0<p<1.0): continue
            v=float(r.get('vol') or 0)
            if v<=0: continue
            rs=r.get('res')
            if rs=='yes': pay=1.0
            elif rs=='no': pay=0.0
            elif rs=='scalar':
                sv=r.get('sv')
                if sv is None: continue
                pay=float(sv)
            else: continue
            ct=r.get('ct')
            if not ct: continue
            d=ct[:10]; nl=len(r.get('lg') or [])
            for tgt,store in ((True,allday),(zone(p,nl),day)):
                if not tgt: continue
                c=store[d]; c[0]+=1; c[1]+=p; c[2]+=pay; c[3]+=v; c[4]+=v*p; c[5]+=v*pay
days=sorted(d for d in day if day[d][0]>=50)
print(f"target-zone days {len(days)}  markets {sum(day[d][0] for d in days):,}  "
      f"contracts {sum(day[d][3] for d in days):,.0f}")
vw=np.array([(day[d][4]-day[d][5])/day[d][3] for d in days])*100
uw=np.array([(day[d][1]-day[d][2])/day[d][0] for d in days])*100
px=np.array([day[d][1]/day[d][0] for d in days])*100
np.random.seed(5)
def boot(x,B=8000):
    n=len(x); o=np.array([x[np.random.randint(0,n,n)].mean() for _ in range(B)])
    return np.percentile(o,[2.5,97.5])
print(f"\nTARGET ZONE (2-4 legs, 10-35c), last-price basis:")
print(f"  contract-weighted edge {vw.mean():+.3f} c/ct  day-clustered 95% CI [{boot(vw)[0]:+.3f}, {boot(vw)[1]:+.3f}]")
print(f"  mean price {px.mean():.2f}c -> collateral {100-px.mean():.2f}c -> return on collateral {vw.mean()/(100-px.mean())*100:.2f}% per turn")
print(f"  FIRST-PRINT basis (x{HAIRCUT:.2f}): {vw.mean()*HAIRCUT:+.3f} c/ct "
      f"[{boot(vw)[0]*HAIRCUT:+.3f}, {boot(vw)[1]*HAIRCUT:+.3f}]  "
      f"-> {vw.mean()*HAIRCUT/(100-px.mean())*100:.2f}% per turn on collateral")
print(f"  losing days {100*(vw<0).mean():.1f}%")
# weekly trend + slope CI
wk=defaultdict(lambda:[0.0,0.0,0.0])
for i,d in enumerate(days):
    import datetime as dt
    w=dt.date.fromisoformat(d).isocalendar()[:2]
    wk[w][0]+=day[d][4]-day[d][5]; wk[w][1]+=day[d][3]; wk[w][2]+=day[d][0]
ws=sorted(wk)
print(f"\n  weekly contract-weighted edge (target zone):")
ys=[]
for i,w in enumerate(ws):
    e=100*wk[w][0]/wk[w][1]; ys.append(e)
    print(f"    {w[0]}-W{w[1]:02d}  n {int(wk[w][2]):>7,}  {e:+7.3f} c/ct   (first-print {e*HAIRCUT:+.3f})")
ys=np.array(ys); xs=np.arange(len(ys))
sl=np.polyfit(xs,ys,1)[0]
sl_b=np.array([np.polyfit(xs[i],ys[i],1)[0] for i in
      (np.random.randint(0,len(ys),len(ys)) for _ in range(4000))])
lo,hi=np.percentile(sl_b,[2.5,97.5])
print(f"\n  OLS slope: {sl:+.4f} c/ct per WEEK  (bootstrap 95% CI [{lo:+.4f}, {hi:+.4f}])")
cur=ys[-3:].mean()
print(f"  last-3-week level: {cur:+.3f} c/ct last-price = {cur*HAIRCUT:+.3f} first-print")
if sl<0:
    print(f"  weeks to zero at current slope: {cur/-sl:.0f}  (~{cur/-sl/4.3:.1f} months)")
    for wks in (6,12,26):
        print(f"    projected in {wks:>2}w: {cur+sl*wks:+.3f} c/ct last-price = {(cur+sl*wks)*HAIRCUT:+.3f} first-print")
