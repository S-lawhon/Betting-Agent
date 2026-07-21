#!/usr/bin/env python3
"""
scripts/fix_void_trades.py
──────────────────────────
Remove incorrectly-written VOID settlement records from trade_log.jsonl.

The Polymarket settler bug caused trades to be voided when outcomePrices
were ["0","0"] (market closed for trading but not resolved).

This script:
  1. Reads trade_log.jsonl
  2. Removes any VOID records that have pod_id=P-006 and result="void"
     (these are the bogus settler entries)
  3. Writes the cleaned log back

Usage:
  python3 scripts/fix_void_trades.py [path_to_trade_log.jsonl]

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

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue

        # Remove bogus VOID records from the polymarket settler
        outcome = (rec.get("outcome") or "").upper()
        result = (rec.get("result") or "").lower()
        pod_id = rec.get("pod_id", "")

        if outcome == "VOID" and result == "void" and pod_id == "P-006":
            removed += 1
            print(f"  REMOVING: {rec.get('event', '?')[:50]} | market_id={rec.get('market_id', '?')[:20]}")
            continue

        kept.append(line)

    if removed == 0:
        print("No bogus VOID records found. Nothing to do.")
        return

    # Backup original
    backup = log_path.with_suffix(".jsonl.bak")
    log_path.rename(backup)
    print(f"\nBacked up original to: {backup}")

    # Write cleaned version
    with open(log_path, "w", encoding="utf-8") as f:
        for line in kept:
            f.write(line + "\n")

    print(f"Removed {removed} bogus VOID records. Cleaned log has {len(kept)} entries.")


if __name__ == "__main__":
    main()
