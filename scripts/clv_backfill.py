#!/usr/bin/env python
"""One-time CLV BACKFILL for the legacy P-001 settlement backlog.

Context: the daily clv_settlement job reads only the last CLV_ARCHIVE_LOOKBACK=3
archives, so bets that settled before the rotation-bug fix (and back to April)
aged out of the window and were never priced. Steady-state capture is healthy
(82-100% at 1-4d); this recovers the ~548 legacy misses that the daily job can
no longer reach.

Faithful to production close_fair(): same endpoint, same offset (0), same devig,
same fee. Idempotent (dedups by fingerprint against the LIVE clv_log). Writes
recovered records to a STAGING file only -- does NOT touch clv_log.jsonl. A
separate append step merges after review. Records the failure branch for every
miss so the residual can be characterized.
"""
import json,re,os,glob,gzip,urllib.request,collections,sys,argparse
from datetime import datetime,timezone,timedelta
ROOT="/opt/betting-pod-shop"; sys.path.insert(0,ROOT)
from src.devig import devig_two_way, american_to_prob
from src.kalshi_fees import fee_per_contract
from src.et_time import parse_mlb_ticker_start
CLV_LOG=f"{ROOT}/data/trade_logs/clv_log.jsonl"
STAGE=os.environ.get("CLV_BACKFILL_OUT","/tmp/clv_backfill.jsonl")
KEY=re.search(r'ODDS_API_KEY=([^\n\r"\']+)',open(f"{ROOT}/.env").read()).group(1).strip()
BASE="https://api.the-odds-api.com/v4"
parse_ticker=parse_mlb_ticker_start
def norm(s): return re.sub(r'[^a-z]','',s.lower())
def get(u):
    with urllib.request.urlopen(u,timeout=30) as r: return json.load(r)

ap=argparse.ArgumentParser()
ap.add_argument("--limit",type=int,default=0,help="max games to process (0=all)")
ap.add_argument("--dry",action="store_true",help="characterize only, no API calls")
args=ap.parse_args()

# close_fair, but returns (result_or_None, branch_tag) for accounting. Logic identical
# to production for the success path (offset 0, pinnacle EU, devig two-way).
def close_fair(expected,names):
    try:
        d=get(f"{BASE}/historical/sports/baseball_mlb/odds?apiKey={KEY}&regions=eu&markets=h2h&oddsFormat=american&date={expected.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    except Exception as e:
        return None,f"API_EXC:{str(e)[:40]}"
    data=d.get('data',[])
    cands=[e for e in data if {norm(e['home_team']),norm(e['away_team'])}=={names[0],names[1]}]
    if not cands: return None,"NO_TEAM_MATCH"
    e=min(cands,key=lambda e:abs((datetime.fromisoformat(e['commence_time'].replace('Z','+00:00'))-expected).total_seconds()))
    if abs((datetime.fromisoformat(e['commence_time'].replace('Z','+00:00'))-expected).total_seconds())>3*3600:
        return None,"TIME_GT_3H"
    has_pinn=False
    for b in e.get('bookmakers',[]):
        if b['key']=='pinnacle':
            has_pinn=True
            h=next((m for m in b['markets'] if m['key']=='h2h'),None)
            if h:
                px={o['name']:o['price'] for o in h['outcomes']}
                if e['home_team'] in px and e['away_team'] in px:
                    fh=devig_two_way(american_to_prob(px[e['home_team']]),american_to_prob(px[e['away_team']]))
                    return (e['home_team'],e['away_team'],fh,e['commence_time']),"OK"
            return None,"PINN_NO_H2H"
    return None,("NO_PINNACLE" if not has_pinn else "PINN_NAME_MISMATCH")

# dedup against LIVE clv_log
done=set()
if os.path.exists(CLV_LOG):
    for ln in open(CLV_LOG):
        try: done.add(json.loads(ln)['fingerprint'])
        except: pass

def iter_rows():
    # ALL archives (not just last 3) + active
    for path in sorted(glob.glob(f"{ROOT}/data/trade_logs/trade_log.archive_*.jsonl.gz")):
        with gzip.open(path,'rt',errors='replace') as fh:
            for ln in fh: yield ln
    with open(f"{ROOT}/data/trade_logs/trade_log.jsonl",errors='replace') as fh:
        for ln in fh: yield ln

new={}
for ln in iter_rows():
    try: r=json.loads(ln)
    except: continue
    if r.get('pod_id')=='P-001' and r.get('action') in('WIN','LOSS') and 'MLB' in (r.get('market_ticker') or '').upper():
        fp=r.get('fingerprint')
        if fp and fp not in done: new[fp]=r
games=collections.defaultdict(list)
for r in new.values(): games['-'.join(r['market_ticker'].split('-')[:2])].append(r)
print(f"legacy misses: {len(new)} bets across {len(games)} games (dedup vs live clv_log)")

if args.dry:
    print("dry run -- no API calls."); sys.exit(0)

items=list(games.items())
if args.limit: items=items[:args.limit]
branch=collections.Counter()
written=0
recovered_games=0
with open(STAGE,"w") as out:
    for i,(gid,rs) in enumerate(items):
        tk=rs[0]['market_ticker']
        expected=parse_ticker(tk)
        if expected is None: branch["UNPARSEABLE_TICKER"]+=1; continue
        parts=(rs[0].get('event') or '').split(' vs ')
        if len(parts)!=2: branch["EVENT_SPLIT"]+=1; continue
        res,tag=close_fair(expected,[norm(x) for x in parts])
        branch[tag]+=1
        if not res: continue
        recovered_games+=1
        home,away,fh,com=res
        for r in rs:
            our='home' if ((r['side']=='YES')==(r.get('yes_side')=='home')) else 'away'
            fc=fh if our=='home' else 1-fh
            entry=r.get('fill_price') or r.get('kalshi_prob')
            clv=fc-entry
            rec=dict(fingerprint=r['fingerprint'],pod_id='P-001',market_ticker=r['market_ticker'],
                     event=r.get('event'),entry_price=round(entry,4),pinn_fair_close=round(fc,4),
                     clv_gross=round(clv,4),clv_net_maker=round(clv-fee_per_contract(entry,maker=True),4),
                     outcome=r.get('outcome'),commence=com,settled_at=r.get('settled_at_utc'),
                     _source="backfill")
            out.write(json.dumps(rec)+"\n"); written+=1
        if (i+1)%50==0: print(f"  ...{i+1}/{len(items)} games, {written} records so far",flush=True)
print(f"\nDONE: {recovered_games}/{len(items)} games recovered, {written} CLV records staged -> {STAGE}")
print("branch breakdown:")
for k,v in branch.most_common(): print(f"   {v:4d}  {k}")
