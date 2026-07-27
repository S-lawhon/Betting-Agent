#!/usr/bin/env python3
"""
scripts/ncaaf_listing_watch.py
──────────────────────────────
R5 follow-up item 3: the NCAAF bulk-listing snapshot.

**Pre-registered. The thresholds below were fixed BEFORE any data was seen and
must not be renegotiated afterwards** — that is the P-013 failure mode, and it
is the only reason this test is worth running at all.

The hypothesis: when Kalshi drops the full NCAAF Week-1 slate (~2026-08-10 to
08-25; Week 1 is 2026-08-29), the non-marquee games are listed by an
inattentive market and drift toward fair over the following days.

Protocol, as registered:
  1. Poll `/markets?series_ticker=KXNCAAFGAME&status=open` daily from
     2026-08-01. Trigger = count jumping from ~30 to several hundred.
  2. Within 6h of the bulk `open_time`, snapshot bid/ask for every new market.
     Anchor contemporaneity is the whole point: a 68h-stale anchor once
     manufactured a +9.5c artifact with a CI excluding zero.
  3. Restrict to two-sided books, ask in [0.85, 0.975], NON-MARQUEE games.
  4. Confirm finalists on the orderbook: >=$100 within 3c on BOTH sides.
  5. Re-snapshot at T+24h and T+72h. **The drift is the alpha.**
  6. **KILL if drift < 3.5c, regardless of statistical significance.**
     Required edge = 1c tick + spread + 0.4c fee + 1c margin.

**Stated in advance: this is expected to fail.** Marquee games are already
priced tightly, the target band across live NCAAF shows a 27c median spread,
and backwater families quote 87-93c. On NCAAF, attention and liquidity are
perfectly correlated, so the inattention pocket and the tradeable pocket are
disjoint sets. It runs because it is nearly free and the snapshot cannot be
reconstructed after the fact.

    python3 -m scripts.ncaaf_listing_watch            # one poll
    python3 -m scripts.ncaaf_listing_watch --status   # what has been captured

State: data/ncaaf_listing_watch/{polls,snapshots}.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_public import KalshiPublic          # noqa: E402

SERIES = "KXNCAAFGAME"
DIR = Path("data/ncaaf_listing_watch")
POLLS = DIR / "polls.jsonl"
SNAPS = DIR / "snapshots.jsonl"

# ── PRE-REGISTERED PARAMETERS — do not edit after the drop ──
BULK_TRIGGER = 100          # markets; baseline is ~30 marquee-only
ASK_LO, ASK_HI = 0.85, 0.975
MIN_DEPTH_USD = 100.0       # within 3c, BOTH sides
DEPTH_WITHIN = 0.03
KILL_DRIFT_CENTS = 3.5      # drift below this = dead, significance irrelevant
RESNAPSHOT_HOURS = (24, 72)
SNAPSHOT_DEADLINE_H = 6     # anchor must be within 6h of bulk open_time


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, rec: Dict[str, Any]) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    rec.setdefault("iso", _now().isoformat())
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def _read(path: Path) -> List[Dict[str, Any]]:
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except OSError:
        return []


def fnum(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def depth_within(k: KalshiPublic, ticker: str) -> Dict[str, float]:
    """Dollars resting within DEPTH_WITHIN of the top, per side."""
    ob = k.get(f"/markets/{ticker}/orderbook") or {}
    fp = ob.get("orderbook_fp") or (ob.get("orderbook") or {}).get("orderbook_fp") or {}
    out = {}
    for side, key in (("yes", "yes_dollars"), ("no", "no_dollars")):
        lv = [(float(p), float(s)) for p, s in (fp.get(key) or [])]
        if not lv:
            out[side] = 0.0
            continue
        best = max(p for p, _ in lv)
        out[side] = sum(p * s for p, s in lv if p >= best - DEPTH_WITHIN)
    return out


def snapshot(k: KalshiPublic, markets: List[Dict[str, Any]],
             label: str) -> Dict[str, Any]:
    """Bid/ask for every market, plus depth on the band finalists."""
    rows, finalists = [], []
    for m in markets:
        bid, ask = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
        row = {"ticker": m.get("ticker"), "bid": bid, "ask": ask,
               "open_time": m.get("open_time"), "title": m.get("title")}
        rows.append(row)
        # two-sided only; a one-sided book is INSUFFICIENT DATA, not an edge
        if bid and ask and ASK_LO <= ask <= ASK_HI:
            finalists.append(row)
    for r in finalists:
        d = depth_within(k, r["ticker"])
        r["depth_yes_usd"], r["depth_no_usd"] = d.get("yes"), d.get("no")
        r["passes_depth"] = (d.get("yes", 0) >= MIN_DEPTH_USD
                             and d.get("no", 0) >= MIN_DEPTH_USD)
    rec = {"label": label, "n_markets": len(rows),
           "n_in_band_two_sided": len(finalists),
           "n_passing_depth": sum(1 for r in finalists if r.get("passes_depth")),
           "rows": rows, "finalists": finalists}
    _write(SNAPS, rec)
    return rec


def poll() -> int:
    k = KalshiPublic()
    ms = k.open_markets(SERIES, max_pages=10)
    n = len(ms)
    prior = _read(POLLS)
    prev_n = prior[-1]["n_open"] if prior else None
    bulk_seen = any(p.get("bulk_detected") for p in prior)
    bulk = (not bulk_seen) and n >= BULK_TRIGGER

    _write(POLLS, {"n_open": n, "prev_n": prev_n, "bulk_detected": bulk,
                   "trigger": BULK_TRIGGER})
    print(f"KXNCAAFGAME open markets: {n}"
          + (f"  (was {prev_n})" if prev_n is not None else ""))

    if bulk:
        print(f"*** BULK LISTING DETECTED ({prev_n} -> {n}) — snapshotting now ***")
        rec = snapshot(k, ms, "T0")
        print(f"    {rec['n_in_band_two_sided']} two-sided in "
              f"[{ASK_LO}, {ASK_HI}]; {rec['n_passing_depth']} pass depth")
        return 0

    if bulk_seen:
        t0 = next((s for s in _read(SNAPS) if s.get("label") == "T0"), None)
        if t0:
            age_h = (_now() - datetime.fromisoformat(t0["iso"])).total_seconds() / 3600
            done = {s.get("label") for s in _read(SNAPS)}
            for h in RESNAPSHOT_HOURS:
                lbl = f"T+{h}h"
                if lbl not in done and age_h >= h:
                    print(f"*** {lbl} re-snapshot due (T0 is {age_h:.1f}h old) ***")
                    snapshot(k, ms, lbl)
                    break
    return 0


def status() -> int:
    polls, snaps = _read(POLLS), _read(SNAPS)
    print("NCAAF listing watch — PRE-REGISTERED")
    print("=" * 58)
    print(f"  polls recorded : {len(polls)}")
    if polls:
        print(f"  latest         : {polls[-1]['iso'][:19]}  n_open={polls[-1]['n_open']}")
    print(f"  bulk detected  : {any(p.get('bulk_detected') for p in polls)}")
    print(f"  snapshots      : {[s.get('label') for s in snaps] or 'none'}")
    by = {s.get("label"): s for s in snaps}
    if "T0" in by:
        for h in RESNAPSHOT_HOURS:
            lbl = f"T+{h}h"
            if lbl in by:
                a = {r["ticker"]: r for r in by["T0"]["finalists"]}
                drift = [100 * (r["ask"] - a[r["ticker"]]["ask"])
                         for r in by[lbl]["rows"]
                         if r["ticker"] in a and r.get("ask")
                         and a[r["ticker"]].get("ask")]
                if drift:
                    med = sorted(drift)[len(drift) // 2]
                    verdict = ("KILL" if abs(med) < KILL_DRIFT_CENTS
                               else "above the kill threshold")
                    print(f"  {lbl}: n={len(drift)} median drift {med:+.2f}c "
                          f"-> {verdict} (threshold {KILL_DRIFT_CENTS}c)")
    print(f"\n  Registered kill: drift < {KILL_DRIFT_CENTS}c is dead regardless "
          "of significance.")
    print("  Expected to FAIL — on NCAAF, attention and liquidity are perfectly")
    print("  correlated, so the inattention pocket and the tradeable pocket are")
    print("  disjoint. It runs because the snapshot cannot be rebuilt later.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    return status() if a.status else poll()


if __name__ == "__main__":
    sys.exit(main())
