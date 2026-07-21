import json, statistics
from collections import defaultdict

def load(path='candles.jsonl'):
    recs = []
    for line in open(path):
        try: recs.append(json.loads(line))
        except: pass
    return recs

def f(x):
    try: return float(x)
    except: return None

def analyze(recs):
    by_series = defaultdict(list)
    for r in recs:
        if 'error' in r or not r.get('candles'): continue
        by_series[r['series']].append(r)

    print(f"{'series':16} {'nmkt':>5} {'traded':>6} {'tot_vol$':>10} {'med_spread':>10} {'2side%':>7}")
    cal = defaultdict(list)   # calibration points: (price_T-1, result)
    for s, rs in sorted(by_series.items()):
        spreads, traded, totvol, twoside, quoted_days = [], 0, 0.0, 0, 0
        for r in rs:
            vol = 0.0
            any_trade = False
            for c in r['candles']:
                v = f(c.get('volume_fp')) or 0
                vol += v
                if v > 0: any_trade = True
                ya, yb = c.get('yes_ask') or {}, c.get('yes_bid') or {}
                a, b = f(ya.get('close_dollars')), f(yb.get('close_dollars'))
                if a is not None and b is not None and b > 0 and a < 1:
                    quoted_days += 1; twoside += 1
                    spreads.append((a-b)*100)
            totvol += vol
            if any_trade: traded += 1
            # calibration: use last candle BEFORE final day with trades: take trade price close
            pcs = [c for c in r['candles'] if (c.get('price') or {}).get('close_dollars')]
            if len(pcs) >= 2:
                p = f(pcs[-2]['price']['close_dollars'])   # close of second-to-last trading day
                if p is not None and 0 < p < 1:
                    cal[s].append((p, 1 if r['result']=='yes' else 0))
        n_candles = sum(len(r['candles']) for r in rs)
        print(f"{s:16} {len(rs):>5} {traded:>6} {totvol:>10,.0f} {statistics.median(spreads) if spreads else -1:>10.1f} {100*twoside/max(n_candles,1):>6.1f}%")
    return cal

recs = load()
print('records:', len(recs))
cal = analyze(recs)
print()
# favorite-longshot calibration by price bucket (pooled)
buckets = [(0,.05),(.05,.10),(.10,.20),(.20,.35),(.35,.50),(.50,.65),(.65,.80),(.80,.95),(.95,1)]
allpts = [p for s in cal for p in cal[s]]
print('pooled calibration (price day before settle vs outcome), n =', len(allpts))
print(f"{'bucket':>12} {'n':>6} {'avg_price':>9} {'win_rate':>8} {'edge_if_buy_yes':>15}")
for lo,hi in buckets:
    pts = [x for x in allpts if lo < x[0] <= hi]
    if not pts: continue
    ap = statistics.mean(x[0] for x in pts)
    wr = statistics.mean(x[1] for x in pts)
    print(f"{f'{lo:.2f}-{hi:.2f}':>12} {len(pts):>6} {ap:>9.3f} {wr:>8.3f} {(wr-ap)*100:>14.1f}c")
