import json, urllib.request, time, os, threading
from concurrent.futures import ThreadPoolExecutor
BASE = "https://api.elections.kalshi.com/trade-api/v2"
lock = threading.Lock(); rate_lock = threading.Lock(); last=[0.0]
def throttle():
    with rate_lock:
        now=time.time(); wait=last[0]+0.5-now  # slow: winners collector also running
        if wait>0: time.sleep(wait)
        last[0]=max(now,last[0]+0.5)
def get(path, retries=4):
    for i in range(retries):
        try:
            throttle()
            req = urllib.request.Request(BASE+path, headers={'User-Agent':'research/1.0'})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if i==retries-1: raise
            time.sleep(2*(i+1))

# sample: all TOP10/TOP20 markets from 3 recent big events (majors)
d = json.load(open('settled_markets.json'))
targets = []
for s in ["KXPGATOP10","KXPGATOP20"]:
    for m in d[s]:
        if any(ev in m['event_ticker'] for ev in ['THOC26','USO26','PGC26','TRAV26','GESO26']):
            targets.append((s,m))
print('target markets:', len(targets), flush=True)
f = open('trades.jsonl','a')
done=set()
if os.path.exists('trades.jsonl'):
    for line in open('trades.jsonl'):
        try: done.add(json.loads(line)['ticker'])
        except: pass
def work(args):
    s,m = args
    t=m['ticker']
    if t in done: return
    trades=[]; cursor=None
    try:
        while True:
            dd = get(f"/markets/trades?ticker={t}&limit=1000"+(f"&cursor={cursor}" if cursor else ""))
            b = dd.get('trades',[]); trades+=b; cursor=dd.get('cursor')
            if not cursor or not b: break
        rec={'ticker':t,'series':s,'event':m['event_ticker'],'result':m['result'],'close_time':m['close_time'],'trades':trades}
    except Exception as e:
        rec={'ticker':t,'series':s,'error':str(e)}
    with lock:
        f.write(json.dumps(rec)+'\n')
with ThreadPoolExecutor(max_workers=3) as ex:
    list(ex.map(work, targets))
f.flush(); print('done')
