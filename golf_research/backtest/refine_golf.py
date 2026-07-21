"""Refinement pass: per-event dispersion for Leg A, band robustness, and a
window/offset grid for the fade maker (Leg B) to see if any realistic
configuration is clearly positive. Prints diagnostics only."""
from __future__ import annotations
import json, math, os
from collections import defaultdict
import golf_fees as gf
from backtest_golf import (load_candles, load_trades, two_sided, anchor_candle,
                           _iso_epoch, _f, series_of, event_of, _candle_for_epoch,
                           bootstrap_ci, SECONDS_PER_DAY)

candles = load_candles("../candles.jsonl")
trades = load_trades("../trades.jsonl")
cindex = {r["ticker"]: r for r in candles}

# ── Leg A: per-event dispersion + avg price + band robustness ────────────
def leg_a(series, band, max_spread=0.06, min_d=4, max_d=10):
    lo, hi = band
    rows = []
    for rec in candles:
        if series_of(rec["ticker"]) not in series: continue
        c = anchor_candle(rec, min_d, max_d)
        if c is None: continue
        b, a = two_sided(c)
        if (a-b) > max_spread or not (lo <= a <= hi): continue
        res = 1.0 if rec.get("result")=="yes" else 0.0
        pnl = (res - a) - gf.fee_per_contract(a, maker=False)
        rows.append((event_of(rec), a, res, pnl))
    return rows

print("=== LEG A top-10/20 per-event (band 0.08-0.40) ===")
rows = leg_a(("KXPGATOP10","KXPGATOP20"), (0.08,0.40))
byev = defaultdict(list)
for ev,a,res,pnl in rows: byev[ev].append((a,res,pnl))
print(f"{'event':10} {'n':>4} {'avg_ask':>7} {'hit%':>6} {'net/ct':>8}")
for ev in sorted(byev):
    v=byev[ev]; n=len(v); aa=sum(x[0] for x in v)/n; wr=sum(x[1] for x in v)/n
    net=sum(x[2] for x in v)/n
    print(f"{ev:10} {n:>4} {aa:>7.3f} {100*wr:>5.1f} {net:>+8.3f}")
allpnl=[x[3] for x in rows]
m,lo,hi = bootstrap_ci([(e,p) for e,_,_,p in rows])
pos_events = sum(1 for ev in byev if sum(x[2] for x in byev[ev])/len(byev[ev])>0)
print(f"pooled n={len(rows)} net={m:+.4f} CI[{lo:+.3f},{hi:+.3f}] positive_events={pos_events}/{len(byev)}")

print("\n=== LEG A band robustness (top-10/20) ===")
for band in [(0.05,0.25),(0.08,0.30),(0.08,0.40),(0.10,0.40),(0.12,0.45),(0.15,0.50)]:
    r=leg_a(("KXPGATOP10","KXPGATOP20"),band)
    if not r: continue
    m,lo,hi=bootstrap_ci([(e,p) for e,_,_,p in r])
    print(f"band {band}: n={len(r):>4} net={m:+.4f} CI[{lo:+.3f},{hi:+.3f}]")

print("\n=== LEG A split by series ===")
for s in ("KXPGATOP10","KXPGATOP20"):
    r=leg_a((s,),(0.08,0.40))
    m,lo,hi=bootstrap_ci([(e,p) for e,_,_,p in r])
    print(f"{s}: n={len(r):>4} net={m:+.4f} CI[{lo:+.3f},{hi:+.3f}]")

# ── Leg B: window x offset grid (realistic through-fill maker) ───────────
def leg_b(window, offset, band=(0.08,0.40), qsize=25.0):
    start_h, end_h = window
    lo, hi = band
    fills=[]  # (event, pnl_per_contract)
    nq=nf=0; contracts=0.0
    for rec in trades:
        tk=rec["ticker"]
        if series_of(tk) not in ("KXPGATOP10","KXPGATOP20"): continue
        crec=cindex.get(tk)
        if not crec: continue
        close=_iso_epoch(rec["close_time"])
        if close is None: continue
        t0=close-start_h*3600; t1=close-end_h*3600
        c=_candle_for_epoch(crec,t0)
        if c is None: continue
        b,a=two_sided(c); mid=(a+b)/2
        if not (lo<=mid<=hi): continue
        qpx=round(mid+offset,2)
        if qpx<=mid or qpx>=1.0: continue
        nq+=1; res=1.0 if rec.get("result")=="yes" else 0.0
        rem=qsize; got=0.0
        for t in rec["trades"]:
            ep=_iso_epoch(t.get("created_time")); px=_f(t.get("yes_price_dollars"))
            cnt=_f(t.get("count_fp")) or 0.0
            if ep is None or px is None or cnt<=0: continue
            if ep<t0 or ep>t1: continue
            if px>qpx+1e-9:
                take=min(rem,cnt)
                if take<=0: continue
                fills.append((event_of(rec), qpx-res)); got+=take; rem-=take
                if rem<=1e-9: break
        if got>0: nf+=1; contracts+=got
    if not fills: return None
    m,lo,hi=bootstrap_ci(fills)
    return dict(nq=nq,nf=nf,fill_rate=round(nf/nq,3),contracts=round(contracts),
                nbets=len(fills),net=round(m,4),
                lo=round(lo,4) if not math.isnan(lo) else None,
                hi=round(hi,4) if not math.isnan(hi) else None)

print("\n=== LEG B fade-maker grid (window_h x offset) ===")
print(f"{'window':>12} {'offset':>7} {'quotes':>6} {'fill%':>6} {'ctr':>6} {'net/ct':>8} {'95%CI':>18}")
for window in [(48,6),(36,6),(36,12),(24,4),(48,24),(18,2)]:
    for offset in [0.01,0.02,0.03]:
        r=leg_b(window,offset)
        if not r: continue
        ci=f"[{r['lo']:+.3f},{r['hi']:+.3f}]" if r['lo'] is not None else "[n/a]"
        print(f"{str(window):>12} {offset:>7.2f} {r['nq']:>6} {100*r['fill_rate']:>5.1f} "
              f"{r['contracts']:>6.0f} {r['net']:>+8.3f} {ci:>18}")

# ── Leg C make-cut: why so thin? distribution of make-cut anchor asks ────
print("\n=== LEG C make-cut anchor-ask distribution (why thin) ===")
asks=[]
for rec in candles:
    if series_of(rec["ticker"])!="KXPGAMAKECUT": continue
    c=anchor_candle(rec,4,10)
    if c is None: continue
    b,a=two_sided(c)
    if (a-b)>0.06: continue
    asks.append(a)
import statistics
if asks:
    print(f"n two-sided make-cut anchors={len(asks)} median_ask={statistics.median(asks):.2f}")
    for lo,hi in [(0,.1),(.1,.3),(.3,.5),(.5,.7),(.7,.9),(.9,1)]:
        print(f"  ask {lo:.1f}-{hi:.1f}: {sum(1 for x in asks if lo<x<=hi)}")
# make-cut as reverse bias: BUY YES underpriced was at 5-40c. try broader
def leg_c(band):
    lo,hi=band; rows=[]
    for rec in candles:
        if series_of(rec["ticker"])!="KXPGAMAKECUT": continue
        c=anchor_candle(rec,4,10)
        if c is None: continue
        b,a=two_sided(c)
        if (a-b)>0.06 or not (lo<=a<=hi): continue
        res=1.0 if rec.get("result")=="yes" else 0.0
        rows.append((event_of(rec),(res-a)-gf.fee_per_contract(a,maker=False)))
    return rows
for band in [(0.05,0.40),(0.10,0.45),(0.20,0.50),(0.30,0.55)]:
    r=leg_c(band)
    if len(r)<10:
        print(f"make-cut band {band}: n={len(r)} (thin)"); continue
    m,lo,hi=bootstrap_ci(r)
    print(f"make-cut band {band}: n={len(r):>4} net={m:+.4f} CI[{lo:+.3f},{hi:+.3f}]")
