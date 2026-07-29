import gzip, json, random
from collections import defaultdict
random.seed(20260728)
FILES=['/tmp/combo_research/cache/mve_markets_KXMVECROSSCATEGORY.jsonl.gz',
       '/tmp/combo_research/cache/mve_markets_KXMVENBASINGLEGAME.jsonl.gz']
PER_DAY=6000
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
            legs=sorted(x[0] for x in (r.get('lg') or []))
            b=res[d]
            if len(b)<PER_DAY: b.append([p,pay,legs])
            else:
                j=random.randrange(cnt[d])
                if j<PER_DAY: b[j]=[p,pay,legs]
json.dump({'cnt':dict(cnt),'res':{k:v for k,v in res.items()}},
          open('/tmp/combo_research/kill/k2_reservoir.json','w'))
print("days", len(res), "sampled", sum(len(v) for v in res.values()), "total", sum(cnt.values()))
