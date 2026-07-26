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
from src.et_time import parse_mlb_ticker_start
from src.clv_close import close_fair as _close_fair_reason, norm as _norm
from src.mlb_teams import teams_from_mlb_ticker
CLV_LOG=f"{ROOT}/data/trade_logs/clv_log.jsonl"
KEY=re.search(r'ODDS_API_KEY=([^\n\r"\']+)',open(f"{ROOT}/.env").read()).group(1).strip()
BASE="https://api.the-odds-api.com/v4"
def get(u):
    with urllib.request.urlopen(u,timeout=30) as r: return json.load(r)
# Kalshi tickers encode ET WALL-CLOCK time. This used to be
# `datetime(..., tzinfo=utc) + timedelta(hours=4)` -- a hardcoded EDT offset,
# an hour wrong from 2026-11-01 (EDT->EST) onward. The expected time it
# produces is handed to the Odds API historical endpoint and used to pick the
# nearest game, so drift there can mis-select or miss a game outright.
# Now delegated to src.et_time, which resolves the offset from tzdata.
parse_ticker = parse_mlb_ticker_start
norm=_norm
# close_fair() used to have FIVE silent `return None` paths that the caller
# could not tell apart, which is why "10 of 14 settled bets produced no CLV
# record" (2026-07-21) stayed unexplained. The logic now lives in
# src/clv_close.py and returns (result, reason); this wrapper preserves the
# original single-value contract while `reasons` accumulates the histogram
# printed at the end of the run.
reasons=collections.Counter()
def clv_names(r):
    """Normalised team names to look the closing line up by.

    Prefer the TICKER's teams over the row's `event` string. `event` records
    the Odds API game the pod priced from, which is not always the market it
    traded: 4 of 671 settled MLB rows (2026-07-26 audit) name two teams that
    appear nowhere in their own ticker, and those rows can never match a
    snapshot. The ticker names the market whose close we are trying to price.
    Falls back to `event` when the ticker cannot be parsed.
    """
    t=teams_from_mlb_ticker(r.get('market_ticker') or '')
    if t: return [norm(t[0]),norm(t[1])]
    return [norm(x) for x in (r.get('event') or '').split(' vs ')]
def close_fair(expected,names):
    res,why=_close_fair_reason(expected,list(names),KEY)
    reasons[why]+=1
    return res

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
        expected=parse_ticker(rs[0]['market_ticker'])
        if expected is None:
            print(f"clv_settlement: WARNING unparseable ticker {rs[0]['market_ticker']}",file=sys.stderr)
            continue
        res=close_fair(expected,clv_names(rs[0]))
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
if reasons:
    print("clv_settlement: close_fair reasons " +
          " ".join(f"{k}={v}" for k,v in reasons.most_common()))
