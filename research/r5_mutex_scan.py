"""
research/r5_mutex_scan.py
─────────────────────────
R5 follow-up item 2: re-scan the SKEWED-partition sub-case.

Hunt E dismissed the mutex/partition trade as "structurally impossible" via
`fee = 0.07(1 - sum(Pi^2)) >= 0.07(1 - 1/n)`. **That inequality is backwards.**
sum(Pi^2) is MINIMISED at uniform, so 0.07(1 - 1/n) is a CEILING:

    n=2  uniform [.5,.5]        -> 3.50c        n=2  skewed [.97,.03]      -> 0.41c
    n=10 uniform                -> 6.30c        n=10 skewed [.97,.003x9]   -> 0.41c

So the skewed case — ~10x cheaper in fees — is exactly what the flawed proof
would have thrown away. This re-scans it honestly.

The trade: on a mutually-exclusive AND exhaustive family, buying every leg costs
sum(ask_i) and pays exactly $1. Gross = 1 - sum(ask_i); fee is charged per leg at
that leg's OWN traded price, never at an average.

Three things this scan refuses to fudge:

* **`mutually_exclusive` is not `exhaustive`.** The flag says at most one leg
  wins, not that one must. `USELECTION.pdf` has a special-election clause
  resolving No for ALL legs; `GPUMON.pdf` ladders are nested (`greater`), not
  buckets. Any survivor must have its contract terms read before it counts.
* **Executability is the gate, not the price.** A prior scan found 0 of 1,514
  ladder pairs executable. Inside one family one leg had 152,862 contracts and
  another had 1 — both passing "spread <= 2c". Screen on top-of-book SIZE.
* **Two-sided only.** A one-sided leg is INSUFFICIENT DATA, never an edge.

Run:  python3 -m research.r5_mutex_scan
"""
from __future__ import annotations

import collections
import json
import os
from typing import Any, Dict, List, Optional

from src.kalshi_public import KalshiPublic

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "r5_mutex_scan.json")

TAKER = 0.07
MIN_LEG_SIZE_USD = 100.0      # required resting depth on EVERY leg


def fnum(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def open_mutex_events(k: KalshiPublic, max_pages: int = 400) -> List[Dict[str, Any]]:
    """Open mutually-exclusive events WITH their markets inline.

    `with_nested_markets=true` collapses what was one /markets call per event
    into a single paged sweep. Without it the scan is ~1 request per event and
    does not finish inside a sane budget.
    """
    out, params = [], {"status": "open", "limit": 200,
                       "with_nested_markets": "true"}
    for i in range(max_pages):
        d = k.get("/events", params)
        if not d:
            break
        page = d.get("events", [])
        out.extend(e for e in page if e.get("mutually_exclusive"))
        cur = d.get("cursor")
        if not cur:
            break
        params["cursor"] = cur
        if (i + 1) % 20 == 0:
            print(f"    ...page {i+1}, {len(out)} mutex events so far")
    return out


def legs_for(k: KalshiPublic, event: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [m for m in (event.get("markets") or [])
            if not str(m.get("ticker", "")).startswith("KXMVE")
            and str(m.get("status", "")).lower() in ("active", "open")]


def top_of_book_usd(k: KalshiPublic, ticker: str) -> Optional[float]:
    """Dollars resting at the best YES ask — what you could actually lift."""
    ob = k.get(f"/markets/{ticker}/orderbook") or {}
    fp = ob.get("orderbook_fp") or (ob.get("orderbook") or {}).get("orderbook_fp") or {}
    no_side = fp.get("no_dollars") or []
    if not no_side:
        return None
    # best YES ask = 1 - best NO bid; size there is the NO bid's size
    best = max(no_side, key=lambda lv: float(lv[0]))
    px, sz = float(best[0]), float(best[1])
    return (1.0 - px) * sz


def main() -> None:
    k = KalshiPublic()
    print("enumerating open mutually-exclusive events...")
    evs = open_mutex_events(k)
    print(f"  {len(evs)} open events with mutually_exclusive=true")

    rows: List[Dict[str, Any]] = []
    skipped = collections.Counter()
    for i, e in enumerate(evs, 1):
        et = e["event_ticker"]
        ms = legs_for(k, e)
        if len(ms) < 2:
            skipped["fewer_than_2_legs"] += 1
            continue
        asks = [fnum(m.get("yes_ask_dollars")) for m in ms]
        bids = [fnum(m.get("yes_bid_dollars")) for m in ms]
        if any(a is None or a <= 0 or a >= 1 for a in asks) or \
           any(b is None or b <= 0 for b in bids):
            skipped["not_fully_two_sided"] += 1
            continue
        cost = sum(asks)
        gross = 1.0 - cost
        fee = sum(TAKER * a * (1.0 - a) for a in asks)   # per leg, at ITS price
        rows.append({
            "event": et, "series": e.get("series_ticker"),
            "category": e.get("category"), "n_legs": len(ms),
            "sum_ask": cost, "gross": gross, "fee": fee, "net": gross - fee,
            "max_leg": max(asks),
            "tickers": [m["ticker"] for m in ms],
        })
        if i % 50 == 0:
            print(f"  ...{i}/{len(evs)} events, {len(rows)} fully two-sided")

    print(f"\nfully two-sided mutex families: {len(rows)}   skipped: {dict(skipped)}")

    def bucket(mx: float) -> str:
        return ("<0.5" if mx < 0.5 else "0.5-0.85" if mx < 0.85
                else "0.85-0.95" if mx < 0.95 else ">=0.95")

    by = collections.defaultdict(list)
    for r in rows:
        by[bucket(r["max_leg"])].append(r)

    print(f"\n{'skew bucket':12} {'n':>5} {'best gross':>11} {'med fee':>9} "
          f"{'best net':>10} {'n net>0':>8}")
    summary = {}
    for b in ("<0.5", "0.5-0.85", "0.85-0.95", ">=0.95"):
        v = by.get(b, [])
        if not v:
            print(f"  {b:10} {0:5}")
            summary[b] = {"n": 0}
            continue
        fees = sorted(r["fee"] for r in v)
        best_net = max(r["net"] for r in v)
        pos = [r for r in v if r["net"] > 0]
        summary[b] = {"n": len(v),
                      "best_gross_c": 100 * max(r["gross"] for r in v),
                      "median_fee_c": 100 * fees[len(fees) // 2],
                      "best_net_c": 100 * best_net,
                      "n_net_positive": len(pos)}
        print(f"  {b:10} {len(v):5} {100*max(r['gross'] for r in v):10.2f}c "
              f"{100*fees[len(fees)//2]:8.2f}c {100*best_net:9.2f}c {len(pos):8}")

    winners = sorted((r for r in rows if r["net"] > 0),
                     key=lambda r: -r["net"])
    print(f"\npositive-NET families: {len(winners)}")
    checked = []
    for r in winners[:15]:
        sizes = [top_of_book_usd(k, t) for t in r["tickers"]]
        ok = all(s is not None and s >= MIN_LEG_SIZE_USD for s in sizes)
        thin = min((s for s in sizes if s is not None), default=0.0)
        r["min_leg_usd"] = round(thin, 2)
        r["executable"] = ok
        checked.append(r)
        print(f"  {r['event']:34} legs={r['n_legs']:3} net={100*r['net']:+.2f}c "
              f"max_leg={r['max_leg']:.3f} thinnest_leg=${thin:,.0f} "
              f"{'EXECUTABLE' if ok else 'NOT EXECUTABLE'}")

    json.dump({"n_events": len(evs), "n_two_sided": len(rows),
               "skipped": dict(skipped), "summary": summary,
               "winners_checked": checked, "rows": rows},
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
