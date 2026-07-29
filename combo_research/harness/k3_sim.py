import numpy as np, statistics as st
np.random.seed(11)
Z=np.load('/tmp/combo_research/kill/k3_res.npz')
days=[str(d) for d in Z['days']]
P={d:Z[f"p_{d}"] for d in days}; Y={d:Z[f"y_{d}"] for d in days}
tot=Z['tot']
HAIRCUT=1.14/2.59
dm={d: float((P[d]-Y[d]).mean()) for d in days}
print(f"days {len(days)}  sampled {sum(len(P[d]) for d in days):,}  population {tot.sum():,}")
print(f"last-price edge {100*np.mean(list(dm.values())):+.3f} c/ct -> first-print basis {100*np.mean(list(dm.values()))*HAIRCUT:+.3f} c/ct")
print(f"mean combo price {100*np.mean([P[d].mean() for d in days]):.2f}c  -> collateral {100*(1-np.mean([P[d].mean() for d in days])):.2f}c/contract")

def turn(budget, n=20000):
    out=np.empty(n)
    for i in range(n):
        d=days[np.random.randint(len(days))]
        p_,y_=P[d],Y[d]; adj=(1-HAIRCUT)*dm[d]
        k=int(budget/(1.0-p_.mean()))+50
        idx=np.random.randint(0,len(p_),size=k)
        p=p_[idx]; y=y_[idx]
        m=np.cumsum(1.0-p)<=budget
        out[i]=float(np.sum((p[m]-y[m])-adj))
    return out

print("\n=== ONE DEPLOYMENT TURN, by collateral budget (real same-day joint outcomes) ===")
print(f"{'collateral':>11} {'~contracts':>11} {'mean$':>9} {'sd$':>9} {'mean/sd':>8} {'P(loss)':>8} {'p1$':>10} {'p0.1$':>10}")
rows={}
for b in (250, 1000, 5000, 10000, 25000, 50000):
    o=turn(b); rows[b]=o
    k=int(b/0.84)
    print(f"${b:>10,} {k:>11,} {o.mean():>+9.2f} {o.std():>9.2f} {o.mean()/o.std():>8.3f} "
          f"{100*(o<0).mean():>7.1f}% {np.percentile(o,1):>+10.2f} {np.percentile(o,0.1):>+10.2f}")

print("\n=== BANKROLL PATHS, $100,000 start, 180 days ===")
for cap in (0.25, 0.10):
    b=100000*cap
    o=turn(b, n=30000); r=o/b
    print(f"\n--- pod cap {cap:.0%}  (${b:,.0f} collateral per turn) ---")
    print(f"per-turn: mean ${o.mean():+,.2f}  sd ${o.std():,.2f}  P(loss) {100*(o<0).mean():.1f}%  "
          f"p1 ${np.percentile(o,1):+,.0f}  worst ${o.min():+,.0f}")
    print(f"per-turn return on deployed collateral: {100*r.mean():+.3f}% (sd {100*r.std():.3f}%)")
    for tpd,lab in ((4,'4 turns/day'),(2,'2 turns/day'),(1,'1 turn/day')):
        N=180*tpd; fin=[]; dds=[]; ruin=0
        for _ in range(4000):
            bank=100000.0; peak=bank; dd=0.0; broke=False
            for x in r[np.random.randint(0,len(r),size=N)]:
                bank += bank*cap*x
                peak=max(peak,bank); dd=min(dd,(bank-peak)/peak)
                if bank<10000: broke=True; break
            fin.append(bank); dds.append(dd)
            if broke: ruin+=1
        fin=np.array(fin)
        print(f"  {lab:<14} median ${np.median(fin):>12,.0f}  p5 ${np.percentile(fin,5):>11,.0f}  "
              f"p95 ${np.percentile(fin,95):>13,.0f}  P(<start) {100*(fin<100000).mean():>4.1f}%  "
              f"P(ruin) {100*ruin/len(fin):.2f}%  medDD {100*np.median(dds):>5.1f}%")

print("\n=== CAPACITY: is the flow big enough to absorb $100k? ===")
prem=np.mean([ (P[d]*1.0).sum()/len(P[d]) for d in days])
print(f"mean price {100*prem:.2f}c; buyer premium in the measured tape was ~$2.49bn over 3 months (~$27M/day).")
for b in (25000,):
    k=int(b/0.84)
    print(f"$ {b:,} collateral -> ~{k:,} contracts -> ~${k*prem:,.0f} of premium sold per turn; "
          f"at 4 turns/day = ${4*k*prem:,.0f}/day = {100*4*k*prem/27e6:.3f}% of measured daily combo premium.")
