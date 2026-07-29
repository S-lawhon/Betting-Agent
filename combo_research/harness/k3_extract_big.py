import gzip, json, random
import numpy as np
from collections import defaultdict
random.seed(20260728)
FILES=['/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz',
       '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz']
PER_DAY=60000
res=defaultdict(list); cnt=defaultdict(int)
for path in FILES:
    with gzip.open(path,'rt') as f:
        for line in f:
            r=json.loads(line)
            if r.get('st')!='finalized': continue
            lp=r.get('lp')
            if not lp: continue
            p=float(lp)
            if not (0.0<p<1.0): continue
            if float(r.get('vol') or 0)<=0: continue
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
            d=ct[:10]; cnt[d]+=1
            nl=len(r.get('lg') or [])
            b=res[d]
            if len(b)<PER_DAY: b.append((p,pay,nl))
            else:
                j=random.randrange(cnt[d])
                if j<PER_DAY: b[j]=(p,pay,nl)
days=sorted(d for d in res if cnt[d]>=1000 and len(res[d])>=2000)
np.savez_compressed('/tmp/combo_research/kill/k3_res.npz',
    days=np.array(days),
    **{f"p_{d}": np.array([x[0] for x in res[d]]) for d in days},
    **{f"y_{d}": np.array([x[1] for x in res[d]]) for d in days},
    **{f"n_{d}": np.array([x[2] for x in res[d]]) for d in days},
    tot=np.array([cnt[d] for d in days]))
print("days",len(days),"sampled",sum(len(res[d]) for d in days),"population",sum(cnt[d] for d in days))
