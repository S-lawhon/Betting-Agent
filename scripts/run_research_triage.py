#!/usr/bin/env python3
"""Allocate unreviewed research assignments to bounded specialist queues."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research_outcomes import ResearchDisposition  # noqa: E402
from src.research_triage import triage_assignments  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_assignments(directory: Path) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for assignment in payload.get("assignments") or []:
            if isinstance(assignment, dict) and assignment.get("id"):
                by_id[str(assignment["id"])] = assignment
    return list(by_id.values())


def _load_source_items(directory: Path) -> Dict[str, Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for item in payload.get("items") or []:
            if isinstance(item, dict) and item.get("id"):
                by_id[str(item["id"])] = item
    return by_id


def _reviewed_assignments(directory: Path, *, now: datetime) -> tuple:
    reviewed: Set[str] = set()
    due_deferrals: Set[str] = set()
    errors: List[Dict[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        payload: Any = None
        try:
            payload = json.loads(path.read_text())
            record = ResearchDisposition.from_dict(payload)
            if record.decision == "defer" and record.recheck_after:
                if date.fromisoformat(record.recheck_after[:10]) <= now.date():
                    due_deferrals.add(record.assignment_id)
                    continue
            reviewed.add(record.assignment_id)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            if isinstance(payload, dict) and payload.get("assignment_id"):
                reviewed.add(str(payload["assignment_id"]))
    return reviewed, due_deferrals, errors


def _opportunity_assignments(path: Path) -> Set[str]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    out: Set[str] = set()
    for record in (payload.get("records") or {}).values():
        opportunity = record.get("opportunity") or {}
        extra = opportunity.get("extra") or {}
        assignment_id = extra.get("assignment_id")
        if assignment_id:
            out.add(str(assignment_id))
    return out


def _archive_completed_dispatches(output_dir: Path,
                                  completed_assignment_ids: Set[str]) -> int:
    archived = 0
    dispatch_root = output_dir / "dispatches"
    for path in sorted(dispatch_root.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("assignment_id") or "") not in completed_assignment_ids:
            continue
        destination = output_dir / "dispatch_archive" / path.parent.name / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, destination)
        archived += 1
    return archived


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config" / "research_triage.yaml")
    parser.add_argument("--assignments-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "assignments")
    parser.add_argument("--source-batches-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "source_batches")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research" / "dispositions")
    parser.add_argument("--strategy-registry", type=Path,
                        default=ROOT / "data" / "strategy_agents" / "registry.json")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "research_triage")
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    args = parser.parse_args(argv)

    config = yaml.safe_load(args.config.read_text()) or {}
    portfolio = config.get("portfolio") or {}
    now = _parse_now(args.now)
    assignments = _load_assignments(args.assignments_dir)
    source_items = _load_source_items(args.source_batches_dir)
    reviewed, due_deferrals, disposition_errors = _reviewed_assignments(
        args.dispositions_dir, now=now)
    opportunities = _opportunity_assignments(args.strategy_registry)
    archived_completed = _archive_completed_dispatches(
        args.output_dir, reviewed | opportunities)
    ledger_path = args.output_dir / "ledger.json"
    ledger, manifest, packets = triage_assignments(
        assignments,
        source_items_by_id=source_items,
        previous_ledger=_read_json(ledger_path, {}),
        reviewed_assignment_ids=reviewed,
        opportunity_assignment_ids=opportunities,
        redispatch_assignment_ids=due_deferrals,
        now=now,
        max_dispatches=int(portfolio.get(
            "max_dispatches_per_utc_day",
            portfolio.get("max_dispatches_per_run", 10))),
        max_research_minutes=int(
            portfolio.get("max_research_minutes_per_utc_day",
                          portfolio.get("max_research_minutes_per_run", 300))),
        lane_concentration_cap=float(
            portfolio.get("lane_concentration_cap", 0.40)),
    )
    manifest["invalid_dispositions"] = disposition_errors
    manifest["dispatches_archived_completed"] = archived_completed

    _write_atomic(ledger_path, ledger)
    _write_atomic(args.output_dir / "latest_manifest.json", manifest)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    _write_atomic(args.output_dir / "manifests" / f"{stamp}.json", manifest)
    for packet in packets:
        _write_atomic(
            args.output_dir / "dispatches" / packet.assigned_agent
            / f"{packet.assignment_id}.json",
            packet.to_dict(),
        )

    print(
        f"research triage: seen={manifest['assignments_seen']} "
        f"dispatched={manifest['dispatched']} deferred={manifest['deferred']} "
        f"minutes={manifest['research_minutes_allocated']} "
        f"invalid_dispositions={len(disposition_errors)}"
    )
    return 2 if disposition_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
