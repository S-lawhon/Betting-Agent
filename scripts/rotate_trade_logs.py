#!/usr/bin/env python3
"""
scripts/rotate_trade_logs.py
────────────────────────────
Archive trade log entries older than N days into compressed monthly files.

Usage:
    python -m scripts.rotate_trade_logs [--days 30] [--dry-run]
    crontab: 0 3 1 * * /opt/betting-pod-shop/.venv/bin/python -m scripts.rotate_trade_logs

Process:
    1. Read trade_log.jsonl
    2. Partition entries by month
    3. Entries older than --days → compressed into data/trade_logs/archive/YYYY-MM.jsonl.gz
    4. Recent entries → rewritten to trade_log.jsonl
    5. Atomic write (write temp, fsync, rename)
"""

import argparse
import gzip
import json
import logging
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("data/trade_logs/trade_log.jsonl")
DEFAULT_ARCHIVE_DIR = Path("data/trade_logs/archive")
DEFAULT_DAYS = 30


def _parse_timestamp(entry: dict) -> Optional[datetime]:
    """Extract and parse timestamp from a trade log entry."""
    ts = entry.get("timestamp_utc") or entry.get("timestamp")
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return None
    except (ValueError, TypeError):
        return None


def rotate(
    log_path: Path,
    archive_dir: Path,
    cutoff_days: int = 30,
    dry_run: bool = False,
) -> dict:
    """Rotate trade log entries.

    Returns:
        dict with keys: total, archived, kept, archive_files
    """
    if not log_path.exists():
        logger.info("No trade log at %s, nothing to rotate", log_path)
        return {"total": 0, "archived": 0, "kept": 0, "archive_files": []}

    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    logger.info("Cutoff: %s (%d days ago)", cutoff.isoformat(), cutoff_days)

    # Read all entries
    entries = []
    bad_lines = 0
    with open(log_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                bad_lines += 1

    if bad_lines:
        logger.warning("Skipped %d malformed lines", bad_lines)

    # Partition
    recent = []
    monthly_archive: dict[str, list] = defaultdict(list)

    for entry in entries:
        ts = _parse_timestamp(entry)
        if ts is None or ts >= cutoff:
            recent.append(entry)
        else:
            month_key = ts.strftime("%Y-%m")
            monthly_archive[month_key].append(entry)

    total_archived = sum(len(v) for v in monthly_archive.values())
    logger.info(
        "Total: %d | Keep: %d | Archive: %d across %d months",
        len(entries), len(recent), total_archived, len(monthly_archive),
    )

    if dry_run:
        logger.info("[DRY RUN] Would archive %d entries, keep %d", total_archived, len(recent))
        return {
            "total": len(entries),
            "archived": total_archived,
            "kept": len(recent),
            "archive_files": sorted(monthly_archive.keys()),
        }

    if total_archived == 0:
        logger.info("Nothing to archive.")
        return {"total": len(entries), "archived": 0, "kept": len(recent), "archive_files": []}

    # Write compressed monthly archives
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_files = []

    for month_key in sorted(monthly_archive.keys()):
        archive_path = archive_dir / "{}.jsonl.gz".format(month_key)

        # Append to existing archive if present
        existing = []
        if archive_path.exists():
            try:
                with gzip.open(archive_path, "rt") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            existing.append(json.loads(line))
            except (gzip.BadGzipFile, json.JSONDecodeError) as exc:
                logger.warning("Corrupt archive %s, overwriting: %s", archive_path, exc)
                existing = []

        combined = existing + monthly_archive[month_key]

        with gzip.open(archive_path, "wt") as f:
            for entry in combined:
                f.write(json.dumps(entry, default=str) + "\n")

        logger.info(
            "Archived %s: %d entries (%d new)",
            archive_path.name,
            len(combined),
            len(monthly_archive[month_key]),
        )
        archive_files.append(str(archive_path))

    # Atomic rewrite of main log with recent entries only
    fd, tmp_path = tempfile.mkstemp(
        dir=str(log_path.parent), suffix=".tmp", prefix="trade_log_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            for entry in recent:
                f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(log_path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Rewrote %s with %d recent entries", log_path, len(recent))

    return {
        "total": len(entries),
        "archived": total_archived,
        "kept": len(recent),
        "archive_files": archive_files,
    }


def main():
    parser = argparse.ArgumentParser(description="Rotate trade log files")
    parser.add_argument(
        "--log-path", type=Path, default=DEFAULT_LOG_PATH,
        help="Path to trade_log.jsonl (default: %(default)s)",
    )
    parser.add_argument(
        "--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR,
        help="Archive directory (default: %(default)s)",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help="Keep entries newer than N days (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be archived without making changes",
    )
    args = parser.parse_args()

    result = rotate(args.log_path, args.archive_dir, args.days, args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
