"""K5: is the edge decay real competition, or a composition artefact?

May is NBA playoffs, July is MLB/WNBA -- the mix of price level, leg count and
sport changed. Recompute the monthly edge INSIDE matched cells so composition
cannot drive the trend.
"""
import gzip, json
import numpy as np
from collections import defaultdict

FILES=['/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz',
       '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz']
PB=[(0,.02),(.02,.05),(.05,.10),(.10,.20),(.20,.35),(.35,.60),(.60,1.0)]
def pb(p):
    for i,(a,b) in enumerate(PB):
        if a<=p<b: return i
    return len(PB)-1
def lb(n):
    return 0 if n<=2 else 1 if n<=3 else 2 if n<=4 else 3 if n<=6 else 4 if n<=9 else 5

cell=defaultdict(lambda:[0,0.0,0.0,0.0,0.0,0.0])   # n, sum p, sum pay, vol, vol*p, vol*pay
sport=defaultdict(lambda: defaultdict(int))
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
            m=ct[:7]
            lg=r.get('lg') or []
            k=(m,pb(p),lb(len(lg)))
            c=cell[k]
            c[0]+=1; c[1]+=p; c[2]+=pay; c[3]+=v; c[4]+=v*p; c[5]+=v*pay
            pre=(lg[0][0].split('-')[0] if lg else 'NA')
            sport[m][pre]+=1

months=sorted({k[0] for k in cell})
print("=== composition check: top leg-series by month (share of combos) ===")
for m in months:
    t=sum(sport[m].values())
    top=sorted(sport[m].items(), key=lambda x:-x[1])[:6]
    print(f"  {m}: " + "  ".join(f"{k} {100*v/t:.0f}%" for k,v in top))

print("\n=== edge by month INSIDE matched (price-bucket x leg-bucket) cells ===")
print("    cells present in all months are used; weights fixed at the pooled cell mix")
common=[c for c in {(k[1],k[2]) for k in cell} if all((m,)+c in cell and cell[(m,)+c][0]>=200 for m in months)]
print(f"    matched cells: {len(common)}")
W={c: sum(cell[(m,)+c][0] for m in months) for c in common}
Wt=sum(W.values())
for m in months:
    uw=sum(W[c]*((cell[(m,)+c][1]-cell[(m,)+c][2])/cell[(m,)+c][0]) for c in common)/Wt
    vw_num=sum(cell[(m,)+c][4]-cell[(m,)+c][5] for c in common)
    vw_den=sum(cell[(m,)+c][3] for c in common)
    n=sum(cell[(m,)+c][0] for c in common)
    print(f"  {m}: n {n:>9,}  standardised unweighted {100*uw:+7.3f} c/ct   contract-weighted {100*vw_num/vw_den:+7.3f} c/ct")

print("\n=== edge by month WITHIN each price bucket (contract-weighted) ===")
hdr="  ".join(f"{a*100:.0f}-{b*100:.0f}c" for a,b in PB)
print(f"  {'month':8} {hdr}")
for m in months:
    row=[]
    for i,(a,b) in enumerate(PB):
        num=sum(cell[(m,i,l)][4]-cell[(m,i,l)][5] for l in range(6) if (m,i,l) in cell)
        den=sum(cell[(m,i,l)][3] for l in range(6) if (m,i,l) in cell)
        row.append(f"{100*num/den:+6.2f}" if den>0 else "   n/a")
    print(f"  {m:8} " + "  ".join(row))

print("\n=== edge by month WITHIN each leg bucket (contract-weighted) ===")
LL=['2','3','4','5-6','7-9','10+']
print(f"  {'month':8} " + "  ".join(f"{x:>6}" for x in LL))
for m in months:
    row=[]
    for l in range(6):
        num=sum(cell[(m,i,l)][4]-cell[(m,i,l)][5] for i in range(len(PB)) if (m,i,l) in cell)
        den=sum(cell[(m,i,l)][3] for i in range(len(PB)) if (m,i,l) in cell)
        row.append(f"{100*num/den:+6.2f}" if den>0 else "   n/a")
    print(f"  {m:8} " + "  ".join(row))
