import json, urllib.request, time
BASE = "https://api.elections.kalshi.com/trade-api/v2"
def get(path, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(BASE+path, headers={'User-Agent':'research/1.0'})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if i == retries-1: raise
            time.sleep(1+i)

# 1) settled golf markets across key series (winner + props)
series_list = ["KXGOLFTOURN","KXPGATOP5","KXPGATOP10","KXPGATOP20","KXPGATOP40","KXPGAMAKECUT","KXPGAH2H","KXGOLFH2H","KXPGA3BALL","KXTHEOPEN","KXMASTERS","KXPGA","KXDPWTH2H","KXPGAR1LEAD","KXPGAR2LEAD","KXPGAR3LEAD"]
settled = {}
for s in series_list:
    ms = []
    cursor = None
    try:
        while True:
            path = f"/markets?series_ticker={s}&status=settled&limit=1000" + (f"&cursor={cursor}" if cursor else "")
            d = get(path)
            batch = d.get('markets', [])
            ms += batch
            cursor = d.get('cursor')
            if not cursor or not batch: break
            time.sleep(0.15)
    except Exception as e:
        print(s, 'ERR', e, flush=True)
    settled[s] = ms
    print(s, len(ms), flush=True)
json.dump(settled, open('settled_markets.json','w'))
print('total settled:', sum(len(v) for v in settled.values()))
