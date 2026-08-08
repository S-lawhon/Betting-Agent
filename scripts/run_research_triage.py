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
from src.research_intake import quality_rejection_from_mapping  # noqa: E402
from src.research_evidence import build_resolvers, resolve_evidence  # noqa: E402
from src.research_triage import (  # noqa: E402
    attach_evidence,
    mechanism_readiness_rejection,
    triage_assignments,
)


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
    records: List[ResearchDisposition] = []
    for path in sorted(directory.glob("*.json")):
        payload: Any = None
        try:
            payload = json.loads(path.read_text())
            record = ResearchDisposition.from_dict(payload)
            records.append(record)
            if record.decision == "defer" and record.recheck_after:
                if date.fromisoformat(record.recheck_after[:10]) <= now.date():
                    due_deferrals.add(record.assignment_id)
                    continue
            reviewed.add(record.assignment_id)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            if isinstance(payload, dict) and payload.get("assignment_id"):
                reviewed.add(str(payload["assignment_id"]))
    return reviewed, due_deferrals, errors, records


def _pending_dispatches(output_dir: Path) -> List[Dict[str, Any]]:
    pending: List[Dict[str, Any]] = []
    for path in sorted((output_dir / "dispatches").glob("*/*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("assignment_id"):
            pending.append(payload)
    return pending


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


def _quarantine_invalid_dispatches(
    output_dir: Path,
    *,
    source_items_by_id: Dict[str, Dict[str, Any]],
    now: datetime,
    memory_rules: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Move legacy pending packets that fail today's source-quality gate."""
    quarantined: Dict[str, str] = {}
    dispatch_root = output_dir / "dispatches"
    for path in sorted(dispatch_root.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        source_item = payload.get("source_item") or source_items_by_id.get(
            str(payload.get("source_item_id") or "")) or {}
        reason = quality_rejection_from_mapping(
            source_item, now=now,
            memory_rules=memory_rules)
        if not reason:
            reason = mechanism_readiness_rejection(
                payload.get("assignment") or {}, source_item)
        if not reason:
            continue
        assignment_id = str(payload.get("assignment_id") or path.stem)
        destination = (
            output_dir / "dispatch_quarantine" / path.parent.name / path.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, destination)
        quarantined[assignment_id] = reason
    return quarantined


def _reconcile_quarantined_allocations(
    output_dir: Path,
    ledger: Dict[str, Any],
    *,
    now: datetime,
) -> tuple:
    """Return same-day capacity consumed by packets now in quarantine.

    The durable reclaimed-ID set makes repeated triage runs idempotent. Older
    quarantines remain historical evidence and never alter today's budget.
    """
    updated = dict(ledger)
    allocations = {
        str(key): dict(value) for key, value in
        (updated.get("daily_allocations") or {}).items()
        if isinstance(value, dict)
    }
    day = now.date().isoformat()
    today = dict(allocations.get(day) or {})
    lanes = {
        str(key): int(value) for key, value in
        (today.get("by_lane") or {}).items()
    }
    reclaimed_ids = set(
        updated.get("reclaimed_quarantined_assignment_ids") or [])
    reclaimed_now: List[str] = []
    minutes = 0
    for path in sorted((output_dir / "dispatch_quarantine").glob("*/*.json")):
        payload = _read_json(path, {})
        assignment_id = str(payload.get("assignment_id") or path.stem)
        if assignment_id in reclaimed_ids:
            continue
        created_at = _parse_now(payload.get("created_at")) if payload.get(
            "created_at") else None
        if not created_at or created_at.date().isoformat() != day:
            continue
        reclaimed_ids.add(assignment_id)
        reclaimed_now.append(assignment_id)
        budget = int(payload.get("research_budget_minutes") or 0)
        minutes += budget
        lane = str((payload.get("assignment") or {}).get("lane") or "")
        if lane and lane in lanes:
            lanes[lane] = max(0, lanes[lane] - 1)
    if reclaimed_now:
        today["dispatches"] = max(
            0, int(today.get("dispatches") or 0) - len(reclaimed_now))
        today["minutes"] = max(0, int(today.get("minutes") or 0) - minutes)
        today["by_lane"] = {key: value for key, value in sorted(lanes.items())
                            if value > 0}
        allocations[day] = today
    updated["daily_allocations"] = dict(sorted(allocations.items()))
    updated["reclaimed_quarantined_assignment_ids"] = sorted(reclaimed_ids)
    return updated, {
        "assignment_ids": sorted(reclaimed_now),
        "dispatches": len(reclaimed_now),
        "minutes": minutes,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config" / "research_triage.yaml")
    parser.add_argument("--research-memory", type=Path,
                        default=ROOT / "config" / "research_memory.yaml")
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
    parser.add_argument(
        "--no-evidence", action="store_true",
        help="skip public fee/quote/document lookups (offline or replay runs)")
    args = parser.parse_args(argv)

    config = yaml.safe_load(args.config.read_text()) or {}
    memory = (yaml.safe_load(args.research_memory.read_text()) or {}
              if args.research_memory.exists() else {})
    portfolio = config.get("portfolio") or {}
    now = _parse_now(args.now)
    assignments = _load_assignments(args.assignments_dir)
    source_items = _load_source_items(args.source_batches_dir)
    reviewed, due_deferrals, disposition_errors, disposition_records = (
        _reviewed_assignments(args.dispositions_dir, now=now)
    )
    opportunities = _opportunity_assignments(args.strategy_registry)
    archived_completed = _archive_completed_dispatches(
        args.output_dir, reviewed | opportunities)
    memory_rules = memory.get("hypotheses") or []
    quarantined = _quarantine_invalid_dispatches(
        args.output_dir, source_items_by_id=source_items, now=now,
        memory_rules=memory_rules)
    ledger_path = args.output_dir / "ledger.json"
    previous_ledger, reclaimed = _reconcile_quarantined_allocations(
        args.output_dir, _read_json(ledger_path, {}), now=now)
    backpressure = config.get("backpressure") or {}
    ledger, manifest, packets = triage_assignments(
        assignments,
        source_items_by_id=source_items,
        previous_ledger=previous_ledger,
        reviewed_assignment_ids=reviewed,
        opportunity_assignment_ids=opportunities,
        redispatch_assignment_ids=due_deferrals,
        disposition_history=[record.to_dict() for record in disposition_records],
        pending_packets=_pending_dispatches(args.output_dir),
        memory_rules=memory_rules,
        now=now,
        max_dispatches=int(portfolio.get(
            "max_dispatches_per_utc_day",
            portfolio.get("max_dispatches_per_run", 10))),
        max_research_minutes=int(
            portfolio.get("max_research_minutes_per_utc_day",
                          portfolio.get("max_research_minutes_per_run", 300))),
        lane_concentration_cap=float(
            portfolio.get("lane_concentration_cap", 0.40)),
        max_pending_total=(
            int(backpressure["max_pending_total"])
            if backpressure.get("max_pending_total") is not None else None),
        max_pending_per_agent=(
            int(backpressure["max_pending_per_agent"])
            if backpressure.get("max_pending_per_agent") is not None else None),
        max_new_dispatches_per_run=(
            int(backpressure["max_new_dispatches_per_run"])
            if backpressure.get("max_new_dispatches_per_run") is not None else None),
    )
    manifest["invalid_dispositions"] = disposition_errors
    manifest["dispatches_archived_completed"] = archived_completed
    manifest["dispatches_quarantined_quality"] = len(quarantined)
    manifest["quarantined_assignment_ids"] = dict(sorted(quarantined.items()))
    manifest["quarantine_capacity_reclaimed"] = reclaimed
    ledger["reclaimed_quarantined_assignment_ids"] = previous_ledger.get(
        "reclaimed_quarantined_assignment_ids") or []

    # Resolve public evidence only for the packets actually being dispatched,
    # so a broad candidate pool never turns into a broad API sweep.
    resolvers = build_resolvers(not args.no_evidence)
    evidence_status: Dict[str, str] = {}
    enriched = []
    for packet in packets:
        pack = resolve_evidence(
            packet.assignment, packet.source_item, resolvers, now=now)
        evidence_status[packet.assignment_id] = str(pack.get("status") or "")
        enriched.append(attach_evidence(packet, pack))
    manifest["evidence_enabled"] = not args.no_evidence
    manifest["evidence_status_by_assignment"] = dict(sorted(
        evidence_status.items()))

    _write_atomic(ledger_path, ledger)
    _write_atomic(args.output_dir / "latest_manifest.json", manifest)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    _write_atomic(args.output_dir / "manifests" / f"{stamp}.json", manifest)
    for packet in enriched:
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
