#!/usr/bin/env python3
"""P-001 post-fix admissibility, using the SANCTIONED reader's own parser."""
import sys, json, math, random
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import p001_checkpoint as cp

CUTOFFS = {
    "conservative (last restart of the 07-26 deploy sequence)":
        datetime(2026, 7, 26, 22, 36, 30, tzinfo=timezone.utc),
    "earliest defensible (fixed process start)":
        datetime(2026, 7, 26, 21, 31, 32, tzinfo=timezone.utc),
    "the reader's current MATCHER_FIX_EPOCH":
        cp.MATCHER_FIX_EPOCH,
}

pl = cp.load_placements(cp.DEFAULT_TRADE_LOG)
rows = []
for p in pl.values():
    mid, gt, placed = p.get("market_id"), p.get("game_time"), p.get("placed_at")
    if not mid or not str(mid).startswith("KXMLBGAME") or gt is None or placed is None:
        continue
    start = cp.ticker_start(mid)          # sanctioned parser: ET, not UTC
    if start is None:
        continue
    d = abs((start - gt).total_seconds()) / 3600.0
    rows.append({"market_id": mid, "placed": placed, "delta_h": d,
                 "ok": d <= cp.ADMISSIBLE_HOURS,
                 "game_day_et": start.date().isoformat(),
                 "ticker_start": start.isoformat(),
                 "priced_game_time": gt.isoformat()})
rows.sort(key=lambda r: r["placed"])
print(f"ALL-TIME joinable MLB placements: {len(rows)}  admissible "
      f"{sum(1 for r in rows if r['ok'])} = "
      f"{sum(1 for r in rows if r['ok'])/len(rows)*100:.1f}%")
print()

def cluster_ci(sel, n_boot=20000, seed=20260729):
    """Bootstrap the rate by GAME-DAY cluster: placements on one MLB day share
    the same odds-list snapshot and the same matcher decision."""
    days = {}
    for r in sel:
        days.setdefault(r["game_day_et"], []).append(r["ok"])
    keys = list(days)
    if not keys:
        return None
    pt = sum(1 for r in sel if r["ok"]) / len(sel)
    if len(keys) < 2:
        return pt, None, None, len(keys)
    rng = random.Random(seed)
    draws = []
    for _ in range(n_boot):
        num = den = 0
        for _ in range(len(keys)):
            for v in days[keys[rng.randrange(len(keys))]]:
                num += 1 if v else 0
                den += 1
        if den:
            draws.append(num / den)
    draws.sort()
    return pt, draws[int(.025*len(draws))], draws[int(.975*len(draws))], len(keys)

for label, cut in CUTOFFS.items():
    sel = [r for r in rows if r["placed"] >= cut]
    ok = sum(1 for r in sel if r["ok"])
    res = cluster_ci(sel)
    print(f"--- {label}")
    print(f"    cutoff {cut.isoformat()}   n={len(sel)}  admissible={ok}")
    if res:
        pt, lo, hi, G = res
        ci = f"[{lo*100:.1f}%, {hi*100:.1f}%]" if lo is not None else "(1 cluster — no CI)"
        print(f"    rate {pt*100:.1f}%  game-day-clustered 95% CI {ci}   clusters={G}")
    bad = [r for r in sel if not r["ok"]]
    print(f"    INADMISSIBLE: {len(bad)}")
    for r in bad:
        sig = "  <-- EXACTLY 24.00h (the original tie-break fingerprint)" if abs(r["delta_h"]-24.0) < 0.02 else ""
        print(f"      {r['market_id']:34s} delta={r['delta_h']:7.2f}h "
              f"placed={r['placed'].isoformat()} ticker={r['ticker_start']} "
              f"priced={r['priced_game_time']}{sig}")
    print()

# placement cadence, for the re-projection
if rows:
    import collections
    by_day = collections.Counter(r["placed"].date().isoformat() for r in rows)
    recent = sorted(by_day.items())[-14:]
    print("placements/day (last 14 days with activity):")
    for d, n in recent: print(f"   {d}  {n}")
    days_active = len(recent)
    print(f"   mean over those days: {sum(n for _, n in recent)/days_active:.1f}/day"
          f"  -> {sum(n for _, n in recent)/days_active*7:.0f}/week")
