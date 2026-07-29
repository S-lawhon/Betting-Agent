"""K2: does the edge survive at a $1,000 bankroll?

The K1 daily aggregate is diversified over ~70,000 contracts/day. A $1k book
holds ~50-300. Resample real same-day outcomes into small books and measure
the P&L distribution, drawdown and ruin risk that an actual account would face.
Also measures leg-overlap concentration, which is what makes a small combo
book correlated in a way a naive binomial model would miss.
"""
import gzip, json, random, statistics as st
from collections import defaultdict, Counter

random.seed(20260728)
FILES = ['/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz',
         '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz']
PER_DAY = 6000           # reservoir per day
HAIRCUT = 1.14/2.59      # first-print vs last-price, from the n=7,922 sample

res = defaultdict(list)  # day -> [(price, payout, legs_tuple)]
cnt = defaultdict(int)

for path in FILES:
    with gzip.open(path,'rt') as f:
        for line in f:
            r = json.loads(line)
            if r.get('st')!='finalized': continue
            lp = r.get('lp')
            if not lp: continue
            p = float(lp)
            if not (0.0<p<1.0): continue
            if float(r.get('vol') or 0) <= 0: continue
            rs = r.get('res')
            if rs=='yes': pay=1.0
            elif rs=='no': pay=0.0
            elif rs=='scalar':
                sv=r.get('sv')
                if sv is None: continue
                pay=float(sv)
            else: continue
            ct=r.get('ct')
            if not ct: continue
            d=ct[:10]; cnt[d]+=1
            legs=tuple(sorted(x[0] for x in (r.get('lg') or [])))
            b=res[d]
            if len(b)<PER_DAY: b.append((p,pay,legs))
            else:
                j=random.randrange(cnt[d])
                if j<PER_DAY: b[j]=(p,pay,legs)

days=[d for d in sorted(res) if cnt[d]>=1000]
print(f"days usable: {len(days)}   sampled contracts: {sum(len(res[d]) for d in days):,}")

# ---- leg-overlap concentration ----
print("\n=== LEG OVERLAP (why a small combo book is NOT independent) ===")
ov=[]
for d in days:
    c=Counter()
    for _,_,legs in res[d]:
        for L in legs: c[L]+=1
    n=len(res[d]); tot=sum(c.values())
    top=c.most_common(1)[0][1] if c else 0
    ov.append((len(c), top/n*100, tot/n))
print(f"median distinct legs per day: {st.median([x[0] for x in ov]):.0f}")
print(f"median share of a day's combos containing the single most-used leg: {st.median([x[1] for x in ov]):.1f}%")
print(f"median legs per combo: {st.median([x[2] for x in ov]):.1f}")

# ---- small-book simulation ----
def sim(k, trials=20000, haircut=True):
    """Sell k contracts on a random day, sampling real same-day outcomes."""
    out=[]
    for _ in range(trials):
        d=random.choice(days); pool=res[d]
        pnl=0.0; coll=0.0
        for _ in range(k):
            p,pay,_=random.choice(pool)
            edge = (p-pay)
            if haircut:
                # shrink only the systematic component, keep full outcome variance
                edge = (p-pay) - (1-HAIRCUT)*  (p - (0.0 if pay==0 else pay))*0  # no-op guard
                edge = (p - pay) - (1-HAIRCUT)*_daymean.get(d,0.0)
            pnl += edge; coll += (1.0-p)
        out.append((pnl, coll))
    return out

_daymean={d: sum(p-pay for p,pay,_ in res[d])/len(res[d]) for d in days}

print("\n=== SMALL-BOOK P&L, one day's book, real same-day outcomes ===")
print("(edge haircut to the first-print basis; outcome variance kept in full)")
print(f"{'k':>6} {'coll$':>8} {'mean$':>8} {'sd$':>8} {'P(loss)':>8} {'p5$':>9} {'p1$':>9} {'worst$':>9}")
for k in (25,50,100,300,1000):
    o=sim(k)
    pn=[x[0] for x in o]; cl=[x[1] for x in o]
    pn.sort()
    n=len(pn)
    print(f"{k:>6} {st.mean(cl):>8.2f} {st.mean(pn):>+8.3f} {st.pstdev(pn):>8.3f} "
          f"{sum(1 for x in pn if x<0)/n*100:>7.1f}% {pn[int(0.05*n)]:>+9.3f} {pn[int(0.01*n)]:>+9.3f} {pn[0]:>+9.3f}")

# ---- multi-day path: bankroll $1000, 25% pod cap, ~4 turns/day (5.4h hold) ----
print("\n=== BANKROLL PATH: $1,000, 25% pod cap ($250 collateral), 4 turns/day, 180 days ===")
finals=[]; maxdd=[]; ruin=0
for _ in range(4000):
    bank=1000.0; peak=bank; dd=0.0; broke=False
    for _day in range(180):
        for _turn in range(4):
            budget=min(250.0, bank*0.25)
            if budget<=1: broke=True; break
            d=random.choice(days); pool=res[d]
            pnl=0.0; coll=0.0
            while coll<budget:
                p,pay,_=random.choice(pool)
                c=(1.0-p)
                if coll+c>budget: break
                coll+=c
                pnl+= (p-pay) - (1-HAIRCUT)*_daymean[d]
            bank+=pnl
            peak=max(peak,bank); dd=min(dd,(bank-peak)/peak)
            if bank<100: broke=True; break
        if broke: break
    finals.append(bank); maxdd.append(dd)
    if broke: ruin+=1
finals.sort()
n=len(finals)
print(f"median final bankroll: ${finals[n//2]:,.0f}   mean ${st.mean(finals):,.0f}")
print(f"p5 ${finals[int(0.05*n)]:,.0f}   p25 ${finals[int(n*0.25)]:,.0f}   p75 ${finals[int(n*0.75)]:,.0f}   p95 ${finals[int(0.95*n)]:,.0f}")
print(f"P(bankroll below start after 180d): {sum(1 for x in finals if x<1000)/n*100:.1f}%")
print(f"P(ruin, bank<$100): {ruin/n*100:.2f}%")
print(f"median max drawdown: {st.median(maxdd)*100:.1f}%   p95 worst drawdown: {sorted(maxdd)[int(0.05*n)]*100:.1f}%")
