"""K4: three adversarial checks on the headline edge.

A. CONTAMINATION. Is `last_price` written/updated at or near settlement? If the
   final print drifts toward the outcome, the whole edge is circular. This is the
   check the project's own history says to run before trusting a price field.
B. CONTRACT-WEIGHTING. A maker earns per CONTRACT, not per market. The unweighted
   edge is not the tradeable one.
C. TIME TREND. Three months, one regime, a product 10 months old. Is the edge
   decaying as quoters arrive?
"""
import gzip, json, math, statistics as st
import numpy as np
from collections import defaultdict

FILES=['/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz',
       '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz']

day=defaultdict(lambda: {'n':0,'sp':0.0,'sy':0.0,'v':0.0,'vp':0.0,'vy':0.0})
byres=defaultdict(list); nprint=defaultdict(int)
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
            d=ct[:10]; b=day[d]
            b['n']+=1; b['sp']+=p; b['sy']+=pay
            b['v']+=v; b['vp']+=v*p; b['vy']+=v*pay
            if len(byres[rs])<400000: byres[rs].append(p)

print("=== A. CONTAMINATION CHECK: does last_price know the outcome? ===")
for rs in ('no','yes','scalar'):
    a=np.array(byres[rs])
    if len(a)==0: continue
    print(f"  res={rs:<7} n={len(a):>8,}  mean {100*a.mean():6.2f}c  median {100*np.median(a):6.2f}c  "
          f"p90 {100*np.percentile(a,90):6.2f}c  p99 {100*np.percentile(a,99):6.2f}c  "
          f"frac>90c {100*(a>0.90).mean():5.2f}%  frac<2c {100*(a<0.02).mean():5.1f}%")
ay=np.array(byres['yes']); an=np.array(byres['no'])
print(f"  YES-settled markets priced >90c: {100*(ay>0.90).mean():.2f}%   "
      f"NO-settled priced <2c: {100*(an<0.02).mean():.1f}%")
print("  READ: if last_price were rewritten at settlement, YES-settled would pile up near 100c")
print("        and NO-settled near 0c. Compare the two distributions above.")

days=sorted(d for d in day if day[d]['n']>=100)
print(f"\n=== B. CONTRACT-WEIGHTED vs UNWEIGHTED EDGE ({len(days)} event-days) ===")
uw=np.array([ (day[d]['sp']-day[d]['sy'])/day[d]['n'] for d in days])*100
vw=np.array([ (day[d]['vp']-day[d]['vy'])/day[d]['v']  for d in days])*100
wts=np.array([day[d]['v'] for d in days])
def boot(x,w=None,B=8000):
    n=len(x); out=np.empty(B)
    for i in range(B):
        idx=np.random.randint(0,n,size=n)
        out[i]= x[idx].mean() if w is None else np.average(x[idx],weights=w[idx])
    return np.percentile(out,[2.5,97.5])
np.random.seed(3)
print(f"  UNWEIGHTED  (per market)  : {uw.mean():+.3f} c/ct  day-clustered 95% CI [{boot(uw)[0]:+.3f}, {boot(uw)[1]:+.3f}]")
print(f"  CONTRACT-WEIGHTED per day : {vw.mean():+.3f} c/ct  day-clustered 95% CI [{boot(vw)[0]:+.3f}, {boot(vw)[1]:+.3f}]")
print(f"  CONTRACT-WEIGHTED overall : {100*(sum(day[d]['vp'] for d in days)-sum(day[d]['vy'] for d in days))/sum(day[d]['v'] for d in days):+.3f} c/ct")
print(f"  losing days unweighted {100*(uw<0).mean():.1f}%   contract-weighted {100*(vw<0).mean():.1f}%")
print("  (all on the LAST-PRICE basis; multiply by ~0.44 for the first-print basis)")

print(f"\n=== C. TIME TREND ===")
mon=defaultdict(lambda:[0.0,0.0,0.0,0.0,0])
for d in days:
    m=d[:7]; b=day[d]
    mon[m][0]+=b['sp']; mon[m][1]+=b['sy']; mon[m][2]+=b['vp']-b['vy']; mon[m][3]+=b['v']; mon[m][4]+=b['n']
for m in sorted(mon):
    a=mon[m]
    print(f"  {m}: markets {a[4]:>9,}  unweighted {100*(a[0]-a[1])/a[4]:+7.3f} c/ct   "
          f"contract-weighted {100*a[2]/a[3]:+7.3f} c/ct   contracts {a[3]:>13,.0f}")
x=np.arange(len(days)); 
sl=np.polyfit(x,uw,1)[0]
print(f"  OLS slope of daily unweighted edge: {sl:+.4f} c/ct per day  ({sl*30:+.3f} c/ct per month)")
sl2=np.polyfit(x,vw,1)[0]
print(f"  OLS slope of daily contract-weighted edge: {sl2:+.4f} c/ct per day ({sl2*30:+.3f} per month)")
