#!/usr/bin/env python3
"""
scripts/undo_p017_spurious_voids.py
───────────────────────────────────
One-off correction for the 2026-07-26 incident: the generic Kalshi ``Settler``
voided 16 recovered P-017 golf positions at $0.00 seconds after a restart,
because Kalshi leaves top-N markets ``status="active"`` with an empty ``result``
for ~a day post-tournament and that settler reads "no result yet" as "void".

Root cause is fixed in ``Legacy/Kalshi Arb Project/src/settler.py`` (pod scoping
now applies to ``_open_placed_entries``, not just the ledger rebuild). This
script repairs the rows that bad write already produced, so the positions return
to OPEN and ``KalshiGolfSettler`` can settle them on a real result.

Deliberately narrow. It removes a VOID row only when ALL of these hold:

  * ``pod_id == "P-017"``
  * ``action``/``outcome`` is ``VOID``
  * ``pnl_usd`` is 0
  * the market ticker is in the explicit ``TICKERS`` allow-list below
  * the row was written at or after ``--since`` (default: the incident minute)

The allow-list matters: ``KXPGATOP20-3MO26-JDAY`` is ALSO a wrongly-booked VOID
(it settled ``result="scalar"`` at $0.24 and is a separate correction), and it
is deliberately NOT in this list — this script must not touch it.

Usage
─────
    python3 scripts/undo_p017_spurious_voids.py --dry-run
    python3 scripts/undo_p017_spurious_voids.py

Stop the service first: the engine appends to this log continuously and a
rewrite underneath an open append handle can lose records.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path("data/trade_logs/trade_log.jsonl")
DEFAULT_SINCE = "2026-07-26T21:21:00+00:00"

# The exact 16 markets the generic settler voided. All were `status="active"`
# with an empty `result` on Kalshi at the moment they were booked.
TICKERS = {
    "KXPGATOP10-3MO26-ASMO", "KXPGATOP10-3MO26-BHOS", "KXPGATOP10-3MO26-EGRI",
    "KXPGATOP10-3MO26-JSVE", "KXPGATOP10-3MO26-KMIT", "KXPGATOP10-3MO26-MBRE",
    "KXPGATOP10-3MO26-RCAS",
    "KXPGATOP20-3MO26-AEWA", "KXPGATOP20-3MO26-BHOS", "KXPGATOP20-3MO26-CBEZ",
    "KXPGATOP20-3MO26-CJAR", "KXPGATOP20-3MO26-JDAH", "KXPGATOP20-3MO26-JKNA",
    "KXPGATOP20-3MO26-KMIT", "KXPGATOP20-3MO26-LGRI", "KXPGATOP20-3MO26-MHUB",
}
EXPECTED = 16


def _ts(rec: dict):
    raw = rec.get("timestamp_utc") or rec.get("timestamp") or ""
    try:
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def is_spurious(rec: dict, since: datetime) -> bool:
    if rec.get("pod_id") != "P-017":
        return False
    if (rec.get("action") or "").upper() != "VOID":
        return False
    if (rec.get("outcome") or "VOID").upper() != "VOID":
        return False
    ticker = rec.get("market_id") or rec.get("market_ticker") or ""
    if ticker not in TICKERS:
        return False
    if float(rec.get("pnl_usd") or 0) != 0.0:
        return False
    t = _ts(rec)
    return t is not None and t >= since


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-path", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    log = args.log_path
    if not log.exists():
        print(f"ERROR: {log} not found", file=sys.stderr)
        return 2

    kept, dropped = [], []
    with open(log, encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                kept.append(s)          # unparseable lines are preserved verbatim
                continue
            if is_spurious(rec, since):
                dropped.append(rec)     # dicts, for the report below
            else:
                kept.append(s)          # raw text, so surviving rows are byte-identical

    print(f"log            : {log}")
    print(f"rows in        : {len(kept) + len(dropped)}")
    print(f"spurious VOIDs : {len(dropped)} (expected {EXPECTED})")
    for rec in dropped:
        print(f"   DROP {rec.get('market_id') or rec.get('market_ticker')}  "
              f"{rec.get('timestamp_utc') or rec.get('timestamp')}")

    if len(dropped) != EXPECTED:
        print(f"\nREFUSING: expected exactly {EXPECTED} rows, found {len(dropped)}. "
              f"Investigate before rerunning.", file=sys.stderr)
        return 3
    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    backup = log.with_suffix(f".jsonl.bak_spurious_voids_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    shutil.copy2(log, backup)
    print(f"\nbackup         : {backup}")

    fd, tmp = tempfile.mkstemp(dir=str(log.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for s in kept:
            fh.write(s + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, log)
    print(f"rows out       : {len(kept)}  (removed {len(dropped)})")
    print("positions are OPEN again; KalshiGolfSettler will settle them on a real result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
