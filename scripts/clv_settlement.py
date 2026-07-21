#!/usr/bin/env python
"""Daily CLV-settlement job. For newly-settled P-001 MLB bets in the active
trade log, fetch the de-vigged Pinnacle CLOSING line and append a CLV record to
data/trade_logs/clv_log.jsonl. This is the forward-test's closing-line capture;
it is read-only with respect to the trading loop. Idempotent (dedup by fingerprint)."""
import json,re,os,glob,gzip,urllib.request,collections,sys,math
from datetime import datetime,timezone,timedelta
ROOT="/opt/betting-pod-shop"; sys.path.insert(0,ROOT)
from src.devig import devig_two_way, american_to_prob
from src.kalshi_fees import fee_per_contract
CLV_LOG=f"{ROOT}/data/trade_logs/clv_log.jsonl"
KEY=re.search(r'ODDS_API_KEY=([^\n\r"\']+)',open(f"{ROOT}/.env").read()).group(1).strip()
BASE="https://api.the-odds-api.com/v4"
MON=dict(JAN=1,FEB=2,MAR=3,APR=4,MAY=5,JUN=6,JUL=7,AUG=8,SEP=9,OCT=10,NOV=11,DEC=12)
def get(u):
    with urllib.request.urlopen(u,timeout=30) as r: return json.load(r)
def parse_ticker(tk):
    b=tk.split('-')[1]; m=re.match(r'(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})',b); yy,mo,dd,hh,mm=m.groups()
    return datetime(2000+int(yy),MON[mo],int(dd),int(hh),int(mm),tzinfo=timezone.utc)+timedelta(hours=4)
def norm(s): return re.sub(r'[^a-z]','',s.lower())
def close_fair(expected,names):
    try: d=get(f"{BASE}/historical/sports/baseball_mlb/odds?apiKey={KEY}&regions=eu&markets=h2h&oddsFormat=american&date={expected.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    except Exception: return None
    cands=[e for e in d.get('data',[]) if {norm(e['home_team']),norm(e['away_team'])}=={names[0],names[1]}]
    if not cands: return None
    e=min(cands,key=lambda e:abs((datetime.fromisoformat(e['commence_time'].replace('Z','+00:00'))-expected).total_seconds()))
    if abs((datetime.fromisoformat(e['commence_time'].replace('Z','+00:00'))-expected).total_seconds())>3*3600: return None
    for b in e.get('bookmakers',[]):
        if b['key']=='pinnacle':
            h=next((m for m in b['markets'] if m['key']=='h2h'),None)
            if h:
                px={o['name']:o['price'] for o in h['outcomes']}
                if e['home_team'] in px and e['away_team'] in px:
                    fh=devig_two_way(american_to_prob(px[e['home_team']]),american_to_prob(px[e['away_team']]))
                    return e['home_team'],e['away_team'],fh,e['commence_time']
    return None

done=set()
if os.path.exists(CLV_LOG):
    for ln in open(CLV_LOG):
        try: done.add(json.loads(ln)['fingerprint'])
        except: pass
# Read the active log PLUS recent archives.
#
# Why: P-001 settles between 01:00-02:00 UTC, the trade log rotates at 06:00,
# and this job runs at 07:30. On any day rotation fires, the night's
# settlements have already been moved into an archive and the active log
# window starts after them -- so this job was structurally guaranteed to find
# nothing. Rotation is size-triggered and therefore irregular (Jul 8, 11, 17,
# 20), which is why it silently worked on some days and not others rather than
# failing outright.
#
# Widening the input is safe by construction: `done` already dedupes by
# fingerprint, so re-reading rows that were captured previously is a no-op.
# ARCHIVE_LOOKBACK is bounded because each new game costs an Odds API call
# against a finite historical quota.
ARCHIVE_LOOKBACK=int(os.environ.get("CLV_ARCHIVE_LOOKBACK","3"))

def _iter_rows():
    active=f"{ROOT}/data/trade_logs/trade_log.jsonl"
    archives=sorted(glob.glob(f"{ROOT}/data/trade_logs/trade_log.archive_*.jsonl.gz"))
    if ARCHIVE_LOOKBACK>0:
        for path in archives[-ARCHIVE_LOOKBACK:]:
            try:
                with gzip.open(path,'rt',errors='replace') as fh:
                    for ln in fh: yield ln
            except OSError as exc:
                print(f"clv_settlement: WARNING could not read {path}: {exc}",file=sys.stderr)
    try:
        with open(active,errors='replace') as fh:
            for ln in fh: yield ln
    except OSError as exc:
        print(f"clv_settlement: ERROR could not read active log: {exc}",file=sys.stderr)
        sys.exit(1)

new={}
scanned=0
for ln in _iter_rows():
    try: r=json.loads(ln)
    except: continue
    scanned+=1
    if r.get('pod_id')=='P-001' and r.get('action') in('WIN','LOSS') and 'MLB' in (r.get('market_ticker') or '').upper():
        fp=r.get('fingerprint')
        if fp and fp not in done: new[fp]=r
games=collections.defaultdict(list)
for r in new.values(): games['-'.join(r['market_ticker'].split('-')[:2])].append(r)
written=0
with open(CLV_LOG,"a") as out:
    for gid,rs in games.items():
        res=close_fair(parse_ticker(rs[0]['market_ticker']),[norm(x) for x in rs[0]['event'].split(' vs ')])
        if not res: continue
        home,away,fh,com=res
        for r in rs:
            our='home' if ((r['side']=='YES')==(r.get('yes_side')=='home')) else 'away'
            fc=fh if our=='home' else 1-fh
            entry=r.get('fill_price') or r.get('kalshi_prob')
            clv=fc-entry
            rec=dict(fingerprint=r['fingerprint'],pod_id='P-001',market_ticker=r['market_ticker'],
                     event=r.get('event'),entry_price=round(entry,4),pinn_fair_close=round(fc,4),
                     clv_gross=round(clv,4),clv_net_maker=round(clv-fee_per_contract(entry,maker=True),4),
                     outcome=r.get('outcome'),commence=com,settled_at=r.get('settled_at_utc'))
            out.write(json.dumps(rec)+"\n"); written+=1
print(f"clv_settlement: scanned {scanned} rows (active + last {ARCHIVE_LOOKBACK} archives), "
      f"{len(new)} new settled bets, {len(games)} games, wrote {written} CLV records to clv_log.jsonl")
