import json, urllib.request, time, datetime as dt, os, threading
from concurrent.futures import ThreadPoolExecutor
BASE = "https://api.elections.kalshi.com/trade-api/v2"
lock = threading.Lock(); rate_lock = threading.Lock(); last=[0.0]
def throttle():
    with rate_lock:
        now=time.time(); wait=last[0]+0.15-now
        if wait>0: time.sleep(wait)
        last[0]=max(now,last[0]+0.15)
def get(path, retries=4):
    for i in range(retries):
        try:
            throttle()
            req = urllib.request.Request(BASE+path, headers={'User-Agent':'research/1.0'})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if i==retries-1: raise
            time.sleep(2*(i+1))
def ts(s): return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())

series_list = ["KXPGATOUR","KXLPGATOUR","KXLIVTOUR","KXCHAMPTOUR"]
allm = {}
for s in series_list:
    ms=[]; cursor=None
    while True:
        d = get(f"/markets?series_ticker={s}&limit=1000"+(f"&cursor={cursor}" if cursor else ""))
        b = d.get('markets',[]); ms+=b; cursor=d.get('cursor')
        if not cursor or not b: break
    allm[s]=ms
    print(s, len(ms), flush=True)
json.dump(allm, open('winner_markets.json','w'))

f = open('winner_candles.jsonl','a')
done=set()
if os.path.exists('winner_candles.jsonl'):
    for line in open('winner_candles.jsonl'):
        try: done.add(json.loads(line)['ticker'])
        except: pass
count=[0]
def work(args):
    s,m = args
    t=m['ticker']
    if t in done or m['status'] in ('unopened',): return
    try:
        st,et = ts(m['open_time']), ts(m['close_time'])
        c = get(f"/series/{s}/markets/{t}/candlesticks?start_ts={st}&end_ts={et}&period_interval=1440")
        rec = {'ticker':t,'series':s,'event':m.get('event_ticker'),'result':m.get('result'),'status':m['status'],
               'title':m.get('title'),'open_time':m['open_time'],'close_time':m['close_time'],'candles':c.get('candlesticks',[])}
    except Exception as e:
        rec = {'ticker':t,'series':s,'error':str(e)}
    with lock:
        f.write(json.dumps(rec)+'\n'); count[0]+=1
        if count[0]%500==0: f.flush(); print(count[0], flush=True)

jobs=[(s,m) for s in series_list for m in allm[s]]
print('jobs:', len(jobs), flush=True)
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(work, jobs))
f.flush(); print('done', count[0])
