"""Recoverable compaction for an over-capacity research dispatch queue."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Set, Tuple


UTC = timezone.utc


class ResearchQueueCompactionError(ValueError):
    """The queue cannot be compacted safely under the requested limits."""


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchQueueCompactionError(
            f"invalid claim lease timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ResearchQueueCompactionError(
            f"claim lease timestamp lacks timezone: {value!r}")
    return parsed.astimezone(UTC)


def active_claim_assignment_ids(
    state_dir: Path, *, now: datetime,
) -> Set[str]:
    """Return assignments with a live lease, failing closed on bad claims."""
    active: Set[str] = set()
    for path in sorted((state_dir / "claims").glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchQueueCompactionError(
                f"cannot read active claim {path}: {exc}") from exc
        assignment_id = str(payload.get("assignment_id") or "").strip()
        expires_at = payload.get("lease_expires_at")
        if not assignment_id or not expires_at:
            raise ResearchQueueCompactionError(
                f"active claim is incomplete: {path}")
        if _parse_time(expires_at) > now.astimezone(UTC):
            active.add(assignment_id)
    return active


def load_pending_packets(
    triage_dir: Path,
) -> Sequence[Tuple[Path, Dict[str, Any]]]:
    """Load valid pending packets, failing closed rather than skipping one."""
    rows = []
    seen = set()
    for path in sorted((triage_dir / "dispatches").glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchQueueCompactionError(
                f"cannot read pending packet {path}: {exc}") from exc
        assignment_id = str(payload.get("assignment_id") or "").strip()
        agent = str(payload.get("assigned_agent") or "").strip()
        if not assignment_id or not agent:
            raise ResearchQueueCompactionError(
                f"pending packet is incomplete: {path}")
        if assignment_id in seen:
            raise ResearchQueueCompactionError(
                f"duplicate pending assignment: {assignment_id}")
        seen.add(assignment_id)
        rows.append((path, payload))
    return rows


def _score(packet: Mapping[str, Any]) -> float:
    try:
        return float(packet.get("triage_score") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ResearchQueueCompactionError(
            f"invalid triage score for {packet.get('assignment_id')}") from exc


def _packet_summary(packet: Mapping[str, Any]) -> Dict[str, Any]:
    assignment = packet.get("assignment") or {}
    source_item = packet.get("source_item") or {}
    return {
        "assignment_id": str(packet.get("assignment_id") or ""),
        "assigned_agent": str(packet.get("assigned_agent") or ""),
        "triage_score": _score(packet),
        "title": str(assignment.get("title") or source_item.get("title") or ""),
        "source_type": str(
            assignment.get("source_type") or source_item.get("source_type") or ""),
    }


def plan_queue_compaction(
    packets: Sequence[Mapping[str, Any]], *, active_assignment_ids: Set[str],
    max_pending_total: int, max_pending_per_agent: int,
) -> Dict[str, Any]:
    """Choose the strongest bounded queue while protecting active claims.

    Packets rank by the triage score already recorded when they were dispatched,
    then by assignment ID for deterministic ties. The function never invents a
    new research score and never treats the ranking as evidence of edge.
    """
    if max_pending_total < 1 or max_pending_per_agent < 1:
        raise ResearchQueueCompactionError("pending limits must be positive")
    by_id: Dict[str, Mapping[str, Any]] = {}
    for packet in packets:
        assignment_id = str(packet.get("assignment_id") or "").strip()
        agent = str(packet.get("assigned_agent") or "").strip()
        if not assignment_id or not agent:
            raise ResearchQueueCompactionError("pending packet is incomplete")
        if assignment_id in by_id:
            raise ResearchQueueCompactionError(
                f"duplicate pending assignment: {assignment_id}")
        by_id[assignment_id] = packet

    live_claims = set(active_assignment_ids) & set(by_id)
    missing_claim_packets = sorted(set(active_assignment_ids) - set(by_id))
    if missing_claim_packets:
        raise ResearchQueueCompactionError(
            "active claims have no pending packet: "
            + ", ".join(missing_claim_packets))
    active_by_agent = Counter(
        str(by_id[assignment_id]["assigned_agent"])
        for assignment_id in live_claims)
    if len(live_claims) > max_pending_total:
        raise ResearchQueueCompactionError(
            "active claims exceed max_pending_total; no packet was moved")
    overfull_agents = sorted(
        agent for agent, count in active_by_agent.items()
        if count > max_pending_per_agent)
    if overfull_agents:
        raise ResearchQueueCompactionError(
            "active claims exceed max_pending_per_agent for: "
            + ", ".join(overfull_agents))

    ranked = sorted(
        by_id.values(),
        key=lambda packet: (
            -_score(packet), str(packet.get("assignment_id") or "")
        ),
    )
    kept = set(live_claims)
    kept_by_agent = Counter(active_by_agent)
    for packet in ranked:
        assignment_id = str(packet["assignment_id"])
        agent = str(packet["assigned_agent"])
        if assignment_id in kept:
            continue
        if len(kept) >= max_pending_total:
            continue
        if kept_by_agent[agent] >= max_pending_per_agent:
            continue
        kept.add(assignment_id)
        kept_by_agent[agent] += 1

    quarantined = set(by_id) - kept
    return {
        "pending_before": len(by_id),
        "pending_after": len(kept),
        "max_pending_total": max_pending_total,
        "max_pending_per_agent": max_pending_per_agent,
        "ranking": "triage_score_desc_then_assignment_id",
        "active_claim_assignment_ids": sorted(live_claims),
        "active_claims_without_pending_packets": [],
        "kept_assignment_ids": sorted(kept),
        "quarantined_assignment_ids": sorted(quarantined),
        "kept_packets": [
            _packet_summary(by_id[assignment_id])
            for assignment_id in sorted(kept)
        ],
        "quarantined_packets": [
            _packet_summary(by_id[assignment_id])
            for assignment_id in sorted(quarantined)
        ],
        "kept_by_agent": dict(sorted(kept_by_agent.items())),
        "quarantined_by_agent": dict(sorted(Counter(
            str(by_id[assignment_id]["assigned_agent"])
            for assignment_id in quarantined).items())),
        "quarantine_reason": "backpressure_overflow",
        "recoverable": True,
    }


def apply_queue_compaction(
    triage_dir: Path, rows: Sequence[Tuple[Path, Mapping[str, Any]]],
    plan: Mapping[str, Any], *, now: datetime,
) -> Path:
    """Move overflow packets to quarantine and atomically write the audit."""
    quarantine_ids = set(plan.get("quarantined_assignment_ids") or [])
    moves = []
    for source, packet in rows:
        assignment_id = str(packet.get("assignment_id") or "")
        if assignment_id not in quarantine_ids:
            continue
        agent = str(packet.get("assigned_agent") or source.parent.name)
        destination = triage_dir / "dispatch_quarantine" / agent / source.name
        if destination.exists():
            raise ResearchQueueCompactionError(
                f"quarantine destination already exists: {destination}")
        moves.append((source, destination))

    if len(moves) != len(quarantine_ids):
        raise ResearchQueueCompactionError(
            "compaction plan does not match the current pending queue")
    for source, destination in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)

    applied_at = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    record = dict(plan) | {
        "schema_version": 1,
        "mode": "applied",
        "applied_at": applied_at,
    }
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = triage_dir / "queue_compactions" / f"{stamp}.json"
    _write_atomic(path, record)
    _write_atomic(triage_dir / "latest_queue_compaction.json", record)
    return path


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)
