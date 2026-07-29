#!/usr/bin/env python3
"""
scripts/inspect_settlements.py
──────────────────────────────
Diagnostic: can the LossGuard actually SEE realised P&L on this account?

The guard enforces the hard loss cap by summing a P&L field on
``/portfolio/settlements``. If that field is named something else — or is not
returned at all — the guard sums nothing, concludes zero loss, and never halts.
A kill switch that silently reads nothing is worse than none, because it is
trusted.

This script is **read-only**. It prints settlement FIELD NAMES, their types, and
numeric aggregates. Pass ``--values`` to also show a couple of sample rows
(your own trading data — no credentials are ever printed).

Usage::

    python3 scripts/inspect_settlements.py
    python3 scripts/inspect_settlements.py --values
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env_loader import load
from src.kalshi_private import KalshiPrivate, fnum


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--values", action="store_true",
                    help="also print two sample rows in full")
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load()
    k = KalshiPrivate.from_env()

    rows = k.get_settlements(limit=a.limit)
    print(f"settlements fetched: {len(rows)}")
    if not rows:
        print("none returned — cannot verify the loss guard on this account")
        return 1

    # which keys exist, and how often
    keys = Counter()
    for r in rows:
        keys.update(r.keys())
    print(f"\n=== FIELDS PRESENT (of {len(rows)} rows) ===")
    for name, n in sorted(keys.items()):
        sample = next((r[name] for r in rows if r.get(name) not in (None, "")), None)
        print(f"  {name:34} {n:>5}/{len(rows)}  type={type(sample).__name__:<5} ")

    # anything that looks like money
    print("\n=== CANDIDATE P&L / MONEY FIELDS ===")
    hits = [x for x in keys if any(t in x.lower() for t in
            ("pnl", "profit", "revenue", "cost", "value", "amount", "payout"))]
    if not hits:
        print("  none matched — inspect the full field list above")
    for name in sorted(hits):
        vals = [fnum(r.get(name)) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            print(f"  {name:34} present but never numeric")
            continue
        neg = [v for v in vals if v < 0]
        print(f"  {name:34} n={len(vals):>4}  sum={sum(vals):+12,.4f}  "
              f"min={min(vals):+10,.4f}  max={max(vals):+10,.4f}  "
              f"negative_rows={len(neg)}")

    # what the guard reads today
    print("\n=== WHAT LossGuard READS TODAY ===")
    seen = 0
    total = 0.0
    for r in rows:
        v = fnum(r.get("realized_pnl_dollars"))
        if v is None and r.get("realized_pnl") is not None:
            v = r["realized_pnl"] / 100.0
        if v is not None:
            seen += 1
            if v < 0:
                total += -v
    print(f"  rows where a P&L value was found: {seen}/{len(rows)}")
    print(f"  realised LOSS the guard would compute: ${total:,.2f}")
    if seen == 0:
        print("\n  *** THE GUARD IS BLIND ON THIS ACCOUNT. ***")
        print("  It would report zero loss no matter what happened, and never halt.")
        print("  Fix the field mapping before enabling orders.")
    elif seen < len(rows):
        print(f"\n  WARNING: {len(rows)-seen} rows carried no readable P&L field.")

    if a.values:
        print("\n=== TWO SAMPLE ROWS ===")
        for r in rows[:2]:
            print(json.dumps(r, indent=2)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
