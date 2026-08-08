#!/usr/bin/env python3
"""Dry-run or apply recoverable compaction to the research dispatch queue."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.research_queue_compaction import (  # noqa: E402
    ResearchQueueCompactionError,
    active_claim_assignment_ids,
    apply_queue_compaction,
    load_pending_packets,
    plan_queue_compaction,
)


def _now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ResearchQueueCompactionError("--now must include a timezone")
    return parsed.astimezone(timezone.utc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config/research_triage.yaml")
    parser.add_argument(
        "--triage-dir", type=Path, default=ROOT / "data/research_triage")
    parser.add_argument(
        "--execution-state-dir", type=Path,
        default=ROOT / "data/research_execution")
    parser.add_argument("--now", help="ISO-8601 timestamp (test/replay only)")
    parser.add_argument(
        "--apply", action="store_true",
        help="move overflow packets; default is a read-only dry run")
    args = parser.parse_args(argv)

    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        backpressure = config.get("backpressure") or {}
        max_total = int(backpressure["max_pending_total"])
        max_agent = int(backpressure["max_pending_per_agent"])
        now = _now(args.now)
        rows = load_pending_packets(args.triage_dir)
        active = active_claim_assignment_ids(
            args.execution_state_dir, now=now)
        plan = plan_queue_compaction(
            [packet for _, packet in rows],
            active_assignment_ids=active,
            max_pending_total=max_total,
            max_pending_per_agent=max_agent)
        result = dict(plan) | {
            "schema_version": 1,
            "mode": "dry_run",
            "generated_at": now.isoformat().replace("+00:00", "Z"),
        }
        if args.apply:
            manifest = apply_queue_compaction(
                args.triage_dir, rows, plan, now=now)
            result["mode"] = "applied"
            result["manifest_path"] = str(manifest)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, KeyError, TypeError, ValueError,
            ResearchQueueCompactionError) as exc:
        print(f"research queue compaction failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
