#!/usr/bin/env python3
"""
scripts/void_bad_p006_trades.py
───────────────────────────────
One-shot script: void all open P-006 trades whose condition_id resolves
to a non-sports market (e.g. "Will Joe Biden get Coronavirus...").

These trades were placed due to the Gamma API returning wrong condition_ids
inside event responses.  They will never settle naturally because the
oracle abandoned these markets (outcomePrices=[0,0]).

Usage:
  /opt/betting-pod-shop/venv/bin/python scripts/void_bad_p006_trades.py --dry-run
  /opt/betting-pod-shop/venv/bin/python scripts/void_bad_p006_trades.py --confirm
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

TRADE_LOG = Path("data/trade_logs/trade_log.jsonl")


def get_open_p006_entries() -> dict:
    """Return {market_id: placed_record} for unresolved P-006 PLACED entries."""
    if not TRADE_LOG.exists():
        print(f"Trade log not found: {TRADE_LOG}")
        return {}

    placed = {}
    resolved = set()

    for raw in TRADE_LOG.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue

        action = (rec.get("action") or "").upper()
        outcome = (rec.get("outcome") or "").upper()
        pod_id = rec.get("pod_id", "")

        if action == "BANKROLL_RESET":
            placed.clear()
            resolved.clear()
            continue

        if pod_id != "P-006":
            continue

        market_id = rec.get("market_id", "")
        if not market_id:
            continue

        if action == "PLACED":
            size = float(rec.get("position_size_usd") or 0)
            if size > 0:
                placed[market_id] = rec

        if outcome in ("WIN", "LOSS", "VOID"):
            resolved.add(market_id)

    return {mid: r for mid, r in placed.items() if mid not in resolved}


def void_trades(entries: dict, dry_run: bool = True):
    """Write VOID records for all open P-006 trades."""
    now = datetime.now(tz=timezone.utc)

    void_records = []
    for market_id, entry in entries.items():
        record = {
            **entry,
            "action": "VOID",
            "outcome": "VOID",
            "result": "void",
            "pnl_usd": 0.0,
            "settled_at_utc": now.isoformat(),
            "timestamp_utc": now.isoformat(),
            "void_reason": "wrong_market: condition_id mapped to non-sports market",
        }
        void_records.append(record)

    if dry_run:
        print(f"\n[DRY RUN] Would void {len(void_records)} trades:")
        total_size = 0
        for rec in void_records:
            size = rec.get("position_size_usd", 0)
            total_size += size
            event = rec.get("event", "?")[:60]
            mid = rec.get("market_id", "?")[:24]
            print(f"  {mid}...  size=${size:.2f}  event={event}")
        print(f"\n  Total exposure returned: ${total_size:.2f}")
        print(f"\n  Run with --confirm to write VOID records to {TRADE_LOG}")
    else:
        with TRADE_LOG.open("a", encoding="utf-8") as fh:
            for rec in void_records:
                fh.write(json.dumps(rec) + "\n")
        total_size = sum(r.get("position_size_usd", 0) for r in void_records)
        print(f"\nVoided {len(void_records)} trades.")
        print(f"Total exposure returned: ${total_size:.2f}")
        print(f"VOID records appended to {TRADE_LOG}")


def main():
    parser = argparse.ArgumentParser(description="Void bad P-006 trades")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be voided")
    group.add_argument("--confirm", action="store_true", help="Actually write VOID records")
    args = parser.parse_args()

    entries = get_open_p006_entries()
    if not entries:
        print("No open P-006 trades found.")
        return

    print(f"Found {len(entries)} open P-006 trades.")
    void_trades(entries, dry_run=not args.confirm)


if __name__ == "__main__":
    main()
