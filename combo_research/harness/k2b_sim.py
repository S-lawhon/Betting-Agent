import json, random, statistics as st
from collections import Counter
random.seed(20260728)
D=json.load(open('/tmp/combo_research/kill/k2_reservoir.json'))
res={k:v for k,v in D['res'].items()}; cnt=D['cnt']
days=[d for d in sorted(res) if cnt[d]>=1000 and len(res[d])>=500]
print(f"days {len(days)}  sampled contracts {sum(len(res[d]) for d in days):,}")

HAIRCUT=1.14/2.59   # first-print / last-price, measured on the n=7,922 fill sample
daymean={d: sum(p-pay for p,pay,_ in res[d])/len(res[d]) for d in days}
print(f"mean last-price edge across days: {st.mean(daymean.values())*100:+.3f} c/ct"
      f" -> haircut to first-print basis: {st.mean(daymean.values())*100*HAIRCUT:+.3f} c/ct")

print("\n=== LEG OVERLAP (is a small combo book actually diversified?) ===")
ov=[]
for d in days:
    c=Counter()
    for _,_,legs in res[d]:
        for L in legs: c[L]+=1
    n=len(res[d])
    top=c.most_common(1)[0][1] if c else 0
    top10=sum(v for _,v in c.most_common(10))
    ov.append((len(c), top/n*100, top10/max(1,sum(c.values()))*100, sum(c.values())/n))
print(f"median distinct legs available per day : {st.median([x[0] for x in ov]):.0f}")
print(f"median %% of a day's combos containing the single most-used leg: {st.median([x[1] for x in ov]):.1f}%")
print(f"median share of all leg-slots taken by the top-10 legs          : {st.median([x[2] for x in ov]):.1f}%")
print(f"median legs per combo: {st.median([x[3] for x in ov]):.1f}")

def draw(d, k):
    pool=res[d]; adj=(1-HAIRCUT)*daymean[d]
    pnl=0.0; coll=0.0
    for _ in range(k):
        p,pay,_=pool[random.randrange(len(pool))]
        pnl += (p-pay) - adj
        coll += (1.0-p)
    return pnl, coll

print("\n=== ONE DAY'S BOOK, k contracts, real same-day joint outcomes ===")
print("(systematic edge haircut to first-print basis; outcome variance kept in full)")
print(f"{'k':>6} {'coll$':>9} {'mean$':>9} {'sd$':>8} {'P(loss)':>9} {'p5$':>9} {'p1$':>9} {'worst$':>9}")
for k in (25,50,100,300,1000,5000):
    o=[draw(random.choice(days),k) for _ in range(12000)]
    pn=sorted(x[0] for x in o); cl=[x[1] for x in o]; n=len(pn)
    print(f"{k:>6} {st.mean(cl):>9.2f} {st.mean(pn):>+9.3f} {st.pstdev(pn):>8.3f} "
          f"{sum(1 for x in pn if x<0)/n*100:>8.1f}% {pn[int(0.05*n)]:>+9.3f} {pn[int(0.01*n)]:>+9.3f} {pn[0]:>+9.3f}")

print("\n=== BANKROLL PATH: $1,000 start, 25% pod cap, 4 turns/day (5.4h median hold), 180 days ===")
for cap,label in ((0.25,'25% pod cap'),(0.10,'10% conservative')):
    finals=[]; dds=[]; ruin=0
    for _ in range(3000):
        bank=1000.0; peak=1000.0; dd=0.0; broke=False
        for _dd in range(180):
            for _t in range(4):
                budget=bank*cap
                if budget<5: broke=True; break
                d=random.choice(days); pool=res[d]; adj=(1-HAIRCUT)*daymean[d]
                pnl=0.0; coll=0.0; guard=0
                while coll<budget and guard<20000:
                    guard+=1
                    p,pay,_=pool[random.randrange(len(pool))]
                    c=1.0-p
                    if coll+c>budget: break
                    coll+=c; pnl+=(p-pay)-adj
                bank+=pnl
                peak=max(peak,bank); dd=min(dd,(bank-peak)/peak)
                if bank<100: broke=True; break
            if broke: break
        finals.append(bank); dds.append(dd)
        if broke: ruin+=1
    finals.sort(); n=len(finals)
    print(f"\n--- {label} ---")
    print(f"median final ${finals[n//2]:,.0f}   mean ${st.mean(finals):,.0f}   "
          f"p5 ${finals[int(0.05*n)]:,.0f}   p95 ${finals[int(0.95*n)]:,.0f}")
    print(f"P(below start after 180d): {sum(1 for x in finals if x<1000)/n*100:.1f}%   "
          f"P(ruin <$100): {ruin/n*100:.2f}%")
    print(f"median max drawdown {st.median(dds)*100:.1f}%   p5 worst {sorted(dds)[int(0.05*n)]*100:.1f}%")
