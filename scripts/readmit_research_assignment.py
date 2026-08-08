#!/usr/bin/env python3
"""Re-triage one human-reviewed research assignment into the live queue.

A scheduled triage run allocates a scarce daily budget across hundreds of
candidates, so an assignment that was wrongly rejected in the past does not
come back on its own: the day's dispatch slots are consumed by whatever ranked
highest that morning.  This tool re-admits ONE named assignment through the
same :func:`triage_assignments` code path the scheduler uses, so the packet is
generated rather than hand-written, and records why.

Safety properties:

* It refuses to run without an explicit assignment id and a written reason.
* It reads the reviewed assignment and its source item from the real intake
  ledger; it never invents a packet and never regenerates the queue.
* It reopens exactly one id, so ledger state cannot permanently bury a record
  that has since been corrected, while every other id keeps its history.
* It grants exactly one dispatch slot and charges that slot to the day's
  allocation, so the scheduled run afterwards sees a fuller day rather than a
  budget that was quietly inflated.
* Queue backpressure is passed through unchanged.  If the review queue is
  full, this fails closed instead of overfilling it.
* It writes nothing unless ``--apply`` is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research_intake import quality_rejection_from_mapping  # noqa: E402
from src.research_triage import (  # noqa: E402
    mechanism_readiness_rejection,
    normalize_legacy_regulatory_assignment,
    normalize_legacy_regulatory_source_item,
    triage_assignments,
)
from scripts.run_research_triage import (  # noqa: E402
    _load_assignments,
    _load_source_items,
    _parse_now,
    _pending_dispatches,
    _read_json,
    _reviewed_assignments,
    _write_atomic,
)


ROOT = Path(__file__).resolve().parent.parent


class ReadmissionError(RuntimeError):
    """The requested re-admission is unsafe, ambiguous, or unnecessary."""


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()


def readmit(
    *,
    assignment_id: str,
    reason: str,
    assignments_dir: Path,
    source_batches_dir: Path,
    dispositions_dir: Path,
    output_dir: Path,
    triage_config: Dict[str, Any],
    now,
    pinned_assignment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the re-admission plan for one reviewed assignment."""
    assignment_id = assignment_id.strip()
    if not assignment_id:
        raise ReadmissionError("an explicit --assignment-id is required")
    if not reason.strip():
        raise ReadmissionError("a written --reason is required")
    if pinned_assignment_id and pinned_assignment_id != assignment_id:
        raise ReadmissionError(
            "assignment does not match the pinned runtime target: "
            f"{assignment_id} != {pinned_assignment_id}")

    assignments = {
        str(row["id"]): row for row in _load_assignments(assignments_dir)
        if row.get("id")
    }
    assignment = assignments.get(assignment_id)
    if assignment is None:
        raise ReadmissionError(
            f"assignment is not in the intake ledger: {assignment_id}")
    source_items = _load_source_items(source_batches_dir)
    source_item_id = str(assignment.get("source_item_id") or "")
    source_item = source_items.get(source_item_id)
    if source_item is None:
        raise ReadmissionError(
            f"source item is missing for {assignment_id}: {source_item_id}")

    normalized_source = normalize_legacy_regulatory_source_item(source_item)
    normalized_assignment = normalize_legacy_regulatory_assignment(
        assignment, normalized_source)
    quality_reason = quality_rejection_from_mapping(normalized_source, now=now)
    if quality_reason:
        raise ReadmissionError(
            f"source still fails the quality gate: {quality_reason}")
    readiness_reason = mechanism_readiness_rejection(
        normalized_assignment, normalized_source)
    if readiness_reason:
        raise ReadmissionError(
            f"source still fails the readiness gate: {readiness_reason}")

    reviewed, _, _, disposition_records = _reviewed_assignments(
        dispositions_dir, now=now)
    if assignment_id in reviewed:
        raise ReadmissionError(
            f"{assignment_id} already has a durable disposition; "
            "re-admitting it would ask for the same work twice")
    pending = _pending_dispatches(output_dir)
    if any(str(row.get("assignment_id") or "") == assignment_id
           for row in pending):
        raise ReadmissionError(
            f"{assignment_id} already has a pending dispatch packet")

    ledger_path = output_dir / "ledger.json"
    previous_ledger = _read_json(ledger_path, {})
    day = now.date().isoformat()
    today = dict((previous_ledger.get("daily_allocations") or {}).get(day) or {})
    portfolio = triage_config.get("portfolio") or {}
    backpressure = triage_config.get("backpressure") or {}
    budget_minutes = int(
        portfolio.get("max_research_minutes_per_utc_day",
                      portfolio.get("max_research_minutes_per_run", 300)))
    # Grant one slot on top of whatever the day has already spent, and let the
    # resulting ledger record the spend.  Lane concentration is meaningless for
    # a single named record, so it is opened rather than silently tripped.
    grant = {
        "max_dispatches": int(today.get("dispatches", 0)) + 1,
        "max_research_minutes": int(today.get("minutes", 0)) + budget_minutes,
        "lane_concentration_cap": 1.0,
    }
    ledger, manifest, packets = triage_assignments(
        [assignment],
        source_items_by_id={source_item_id: source_item},
        previous_ledger=previous_ledger,
        reviewed_assignment_ids=reviewed,
        redispatch_assignment_ids={assignment_id},
        disposition_history=[row.to_dict() for row in disposition_records],
        pending_packets=pending,
        now=now,
        max_dispatches=grant["max_dispatches"],
        max_research_minutes=grant["max_research_minutes"],
        lane_concentration_cap=grant["lane_concentration_cap"],
        max_pending_total=(
            int(backpressure["max_pending_total"])
            if backpressure.get("max_pending_total") is not None else None),
        max_pending_per_agent=(
            int(backpressure["max_pending_per_agent"])
            if backpressure.get("max_pending_per_agent") is not None else None),
        max_new_dispatches_per_run=1,
    )
    if len(packets) != 1 or packets[0].assignment_id != assignment_id:
        raise ReadmissionError(
            "triage did not produce the requested packet: "
            f"quality={manifest.get('quality_blocked_assignment_ids')} "
            f"readiness={manifest.get('readiness_blocked_assignment_ids')} "
            f"backpressure={manifest.get('backpressure')}")
    packet = packets[0]
    # Carry forward the fields run_research_triage owns but this narrow run
    # does not recompute, so a partial ledger cannot erase queue history.
    ledger["reclaimed_quarantined_assignment_ids"] = previous_ledger.get(
        "reclaimed_quarantined_assignment_ids") or []
    record = {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "assignment_id": assignment_id,
        "source_item_id": source_item_id,
        "reason": reason.strip(),
        "pinned_assignment_id": pinned_assignment_id,
        "dispatch_id": packet.id,
        "assigned_agent": packet.assigned_agent,
        "research_budget_minutes": packet.research_budget_minutes,
        "triage_score": packet.triage_score,
        "priority": packet.priority,
        "title_before": str(assignment.get("title") or ""),
        "title_after": str(packet.assignment.get("title") or ""),
        "source_title_before": str(source_item.get("title") or ""),
        "source_title_after": str(packet.source_item.get("title") or ""),
        "source_item_content_hash": str(source_item.get("content_hash") or ""),
        "source_item_content_hash_in_packet": str(
            packet.source_item.get("content_hash") or ""),
        "assignment_sha256": _canonical_sha256(assignment),
        "packet_sha256": _canonical_sha256(packet.to_dict()),
        "slot_grant": grant,
        "daily_allocation_before": today,
        "daily_allocation_after": dict(
            (ledger.get("daily_allocations") or {}).get(day) or {}),
        "backpressure": manifest.get("backpressure"),
        "safety": {
            "regenerates_queue": False,
            "restores_quarantine": False,
            "invokes_model": False,
            "authorizes_execution": False,
        },
    }
    return {
        "record": record, "ledger": ledger, "manifest": manifest,
        "packet": packet, "ledger_path": ledger_path,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument("--reason", required=True,
                        help="why this reviewed assignment is being re-admitted")
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config" / "research_triage.yaml")
    parser.add_argument(
        "--pinned-config", type=Path,
        help="runtime config whose target_assignment_id must match")
    parser.add_argument("--assignments-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "assignments")
    parser.add_argument("--source-batches-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "source_batches")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research" / "dispositions")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "research_triage")
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    parser.add_argument("--apply", action="store_true",
                        help="write the packet, ledger, and admission record")
    args = parser.parse_args(argv)

    pinned = None
    if args.pinned_config:
        pinned_config = yaml.safe_load(
            args.pinned_config.read_text(encoding="utf-8")) or {}
        pinned = str(pinned_config.get("target_assignment_id") or "").strip() or None
    try:
        plan = readmit(
            assignment_id=args.assignment_id,
            reason=args.reason,
            assignments_dir=args.assignments_dir,
            source_batches_dir=args.source_batches_dir,
            dispositions_dir=args.dispositions_dir,
            output_dir=args.output_dir,
            triage_config=yaml.safe_load(
                args.config.read_text(encoding="utf-8")) or {},
            now=_parse_now(args.now),
            pinned_assignment_id=pinned,
        )
    except ReadmissionError as exc:
        print(f"readmission refused: {exc}", file=sys.stderr)
        return 2

    record = dict(plan["record"], applied=bool(args.apply))
    if args.apply:
        packet = plan["packet"]
        _write_atomic(
            args.output_dir / "dispatches" / packet.assigned_agent
            / f"{packet.assignment_id}.json", packet.to_dict())
        _write_atomic(plan["ledger_path"], plan["ledger"])
        stamp = record["generated_at"].replace("-", "").replace(":", "")[:15] + "Z"
        _write_atomic(
            args.output_dir / "readmissions" / f"{stamp}.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
