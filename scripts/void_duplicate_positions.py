#!/usr/bin/env python3
"""
One-time cleanup script: void duplicate open positions.

For each (pod_id, market_id) pair with multiple unresolved PLACED entries,
keep only the most recent one and write VOID records for the rest.

Also voids Polymarket legs from P-002 where the linked Kalshi leg on the
same event has already been settled (WIN/LOSS/VOID).

Usage:
    python -m scripts.void_duplicate_positions [--dry-run]
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TRADE_LOG = Path("data/trade_logs/trade_log.jsonl")


def main():
    dry_run = "--dry-run" in sys.argv

    # Pass 1: load all entries
    placed = {}       # fingerprint → record (PLACED only)
    resolved = set()  # fingerprints that have WIN/LOSS/VOID

    with open(TRADE_LOG) as f:
        for line in f:
            rec = json.loads(line)
            fp = rec.get("fingerprint", "")
            action = rec.get("action", "")
            if action == "PLACED" and fp:
                placed[fp] = rec
            elif action in ("WIN", "LOSS", "VOID") and fp:
                resolved.add(fp)

    # Open (unresolved) positions
    open_pos = {fp: r for fp, r in placed.items() if fp not in resolved}
    print(f"Open positions: {len(open_pos)}")

    # Pass 2: group by (pod_id, market_id) to find duplicates
    groups = defaultdict(list)
    for fp, rec in open_pos.items():
        pod = rec.get("pod_id", "")
        mid = rec.get("market_id") or rec.get("market_ticker") or ""
        groups[(pod, mid)].append((fp, rec))

    to_void = []

    for (pod, mid), entries in groups.items():
        if len(entries) <= 1:
            continue
        # Sort by timestamp, keep newest
        entries.sort(
            key=lambda x: x[1].get("timestamp_utc", ""), reverse=True
        )
        newest_fp = entries[0][0]
        for fp, rec in entries[1:]:
            to_void.append((fp, rec, "DUPLICATE"))

    # Pass 3: void Polymarket legs whose linked Kalshi leg is resolved
    # P-002 places Kalshi + Polymarket legs for the same event.
    # If the Kalshi leg is settled, the Polymarket leg should be too.
    kalshi_resolved_events = set()
    for fp in resolved:
        rec = placed.get(fp)
        if rec and rec.get("pod_id") == "P-002":
            venue = (rec.get("venue") or "").lower()
            if venue == "kalshi" or (rec.get("market_ticker") or "").startswith("KX"):
                event = rec.get("event", "")
                if event:
                    kalshi_resolved_events.add(event)

    for fp, rec in open_pos.items():
        if rec.get("pod_id") != "P-002":
            continue
        venue = (rec.get("venue") or "").lower()
        if venue != "polymarket":
            continue
        event = rec.get("event", "")
        if event and event in kalshi_resolved_events:
            # Check not already in to_void
            if not any(v[0] == fp for v in to_void):
                to_void.append((fp, rec, "LINKED_KALSHI_SETTLED"))

    print(f"Positions to void: {len(to_void)}")
    for fp, rec, reason in to_void[:10]:
        print(
            f"  {reason}: {rec.get('pod_id')} {rec.get('venue','?')} "
            f"{(rec.get('market_id') or rec.get('market_ticker') or '?')[:30]} "
            f"${rec.get('position_size_usd', 0):.2f}"
        )
    if len(to_void) > 10:
        print(f"  ... and {len(to_void) - 10} more")

    total_exposure_freed = sum(r.get("position_size_usd", 0) for _, r, _ in to_void)
    print(f"Total exposure freed: ${total_exposure_freed:.2f}")

    if dry_run:
        print("\n[DRY RUN] No changes written.")
        return

    # Write VOID records
    now = datetime.now(timezone.utc).isoformat()
    with open(TRADE_LOG, "a") as f:
        for fp, rec, reason in to_void:
            void_rec = {
                "fingerprint": fp,
                "timestamp_utc": now,
                "pod_id": rec.get("pod_id", ""),
                "pod_name": rec.get("pod_name", ""),
                "mode": rec.get("mode", "paper"),
                "venue": rec.get("venue", ""),
                "market_type": rec.get("market_type", ""),
                "event": rec.get("event", ""),
                "market_ticker": rec.get("market_ticker") or rec.get("market_id", ""),
                "market_id": rec.get("market_id") or rec.get("market_ticker", ""),
                "side": rec.get("side", ""),
                "action": "VOID",
                "outcome": "VOID",
                "result": "void",
                "pnl_usd": 0.0,
                "skip_reason": f"cleanup: {reason}",
            }
            f.write(json.dumps(void_rec) + "\n")

    print(f"\nWrote {len(to_void)} VOID records to {TRADE_LOG}")


if __name__ == "__main__":
    main()
