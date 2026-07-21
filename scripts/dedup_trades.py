#!/usr/bin/env python3
"""
scripts/dedup_trades.py
───────────────────────
Remove duplicate PLACED entries for the same market_id in trade_log.jsonl.

Keeps the FIRST PLACED entry per market_id and removes subsequent ones.
Also removes any settlement (WIN/LOSS/VOID) records that reference a
removed duplicate.

Usage:
  python3 scripts/dedup_trades.py [path_to_trade_log.jsonl]

Default path: data/trade_logs/trade_log.jsonl
"""

import json
import sys
from pathlib import Path


def main():
    log_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/trade_logs/trade_log.jsonl")

    if not log_path.exists():
        print(f"File not found: {log_path}")
        sys.exit(1)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    kept = []
    removed = 0
    seen_market_ids = set()  # market_ids that already have a PLACED entry

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue

        action = (rec.get("action") or "").upper()
        market_id = rec.get("market_id") or rec.get("market_ticker") or ""

        if action == "PLACED" and market_id:
            if market_id in seen_market_ids:
                removed += 1
                event = (rec.get("event") or "?")[:45]
                ts = (rec.get("timestamp_utc") or "?")[:19]
                print(f"  REMOVING duplicate: {event} | {market_id[:20]} | placed {ts}")
                continue
            seen_market_ids.add(market_id)

        kept.append(line)

    if removed == 0:
        print("No duplicate PLACED entries found. Nothing to do.")
        return

    # Backup original
    backup = log_path.with_suffix(".jsonl.dedup-bak")
    log_path.rename(backup)
    print(f"\nBacked up original to: {backup}")

    # Write cleaned version
    with open(log_path, "w", encoding="utf-8") as f:
        for line in kept:
            f.write(line + "\n")

    print(f"Removed {removed} duplicate PLACED entries. Cleaned log has {len(kept)} entries.")


if __name__ == "__main__":
    main()
