#!/usr/bin/env python3
"""
scripts/migrate_trade_log.py
────────────────────────────
One-time migration: normalize all trade log entries to canonical schema.

Usage:
    python -m scripts.migrate_trade_log --dry-run    # preview changes
    python -m scripts.migrate_trade_log              # apply migration

Creates a backup at trade_log.jsonl.bak before rewriting.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from src.trade_log_schema import TradeLogSchema

DEFAULT_LOG_PATH = Path("data/trade_logs/trade_log.jsonl")


def main():
    parser = argparse.ArgumentParser(description="Migrate trade log to canonical schema")
    parser.add_argument(
        "--log-path", type=Path, default=DEFAULT_LOG_PATH,
        help="Path to trade_log.jsonl (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show stats without making changes",
    )
    args = parser.parse_args()

    if not args.log_path.exists():
        print(f"Trade log not found: {args.log_path}")
        return 1

    if not args.dry_run:
        backup = args.log_path.with_suffix(".jsonl.bak")
        shutil.copy2(args.log_path, backup)
        print(f"Backup created: {backup}")

    result = TradeLogSchema.migrate_file(args.log_path, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
