"""Auditable claim and completion lifecycle for research dispatch packets.

This module does not invoke a model.  It provides the concurrency and artifact
boundary a human session or explicitly configured model runner must use before
research work can truthfully be reported as started.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from src.research_outcomes import ResearchDisposition


SCHEMA_VERSION = 1
PRIORITY_ORDER = {"high": 0, "medium": 1, "exploratory": 2}


class ResearchExecutionError(ValueError):
    """Raised when a claim lifecycle transition would be unsafe."""


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}.tmp".format(path.name, os.getpid()))
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


@contextmanager
def _state_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "worker.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _append_event(state_dir: Path, payload: Mapping[str, Any]) -> None:
    path = state_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True)
class ResearchClaim:
    claim_id: str
    assignment_id: str
    source_item_id: str
    dispatch_id: str
    assigned_agent: str
    worker_id: str
    claimed_at: str
    lease_expires_at: str
    packet_path: str
    agent_prompt_path: str
    status: str = "claimed"
    schema_version: int = SCHEMA_VERSION
    model_invocation_tracked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchClaim":
        return cls(**{
            key: payload[key] for key in cls.__dataclass_fields__
            if key in payload
        })


def _claim_path(state_dir: Path, agent: str, assignment_id: str) -> Path:
    return state_dir / "claims" / agent / "{}.json".format(assignment_id)


def _archive_claim(state_dir: Path, path: Path, bucket: str,
                   payload: Mapping[str, Any]) -> Path:
    claim_id = str(payload.get("claim_id") or "").strip()
    filename = (
        "{}--{}.json".format(path.stem, claim_id) if claim_id else path.name)
    destination = state_dir / bucket / path.parent.name / filename
    _write_atomic(destination, payload)
    path.unlink(missing_ok=True)
    return destination


def _expire_stale_claims(state_dir: Path, now: datetime) -> List[str]:
    expired: List[str] = []
    for path in sorted((state_dir / "claims").glob("*/*.json")):
        payload = _read_json(path)
        expires = _parse_time(payload.get("lease_expires_at"))
        if expires and expires > now:
            continue
        payload.update({"status": "expired", "expired_at": _utc_iso(now)})
        _archive_claim(state_dir, path, "claim_expired", payload)
        assignment_id = str(payload.get("assignment_id") or path.stem)
        expired.append(assignment_id)
        _append_event(state_dir, {
            "event": "claim_expired", "at": _utc_iso(now),
            "assignment_id": assignment_id,
            "worker_id": payload.get("worker_id"),
            "assigned_agent": payload.get("assigned_agent"),
        })
    return expired


def _ranked_packets(dispatches_dir: Path, agent: Optional[str]) -> List[Tuple[Path, Dict[str, Any]]]:
    pattern = "{}/*.json".format(agent) if agent else "*/*.json"
    packets: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted(dispatches_dir.glob(pattern)):
        payload = _read_json(path)
        if payload.get("assignment_id") and payload.get("assigned_agent"):
            packets.append((path, payload))
    packets.sort(key=lambda row: (
        PRIORITY_ORDER.get(str(row[1].get("priority") or ""), 9),
        -float(row[1].get("triage_score") or 0),
        str(row[1].get("created_at") or ""),
        str(row[1].get("assignment_id") or ""),
    ))
    return packets


def preview_next(
    *, dispatches_dir: Path, state_dir: Path, dispositions_dir: Path,
    agent: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """Return the next available packet without mutating claim state.

    Expired leases are treated as available, but are not archived here. The
    mutating ``claim_next`` call performs that recovery under the state lock.
    """
    now = now or datetime.now(timezone.utc)
    active_ids = set()
    for path in (state_dir / "claims").glob("*/*.json"):
        payload = _read_json(path)
        expires = _parse_time(payload.get("lease_expires_at"))
        if expires and expires > now:
            active_ids.add(str(payload.get("assignment_id") or path.stem))
    disposition_ids = set()
    for path in dispositions_dir.glob("*.json"):
        payload = _read_json(path)
        disposition_ids.add(str(payload.get("assignment_id") or path.stem))
    for packet_path, packet in _ranked_packets(dispatches_dir, agent):
        assignment_id = str(packet["assignment_id"])
        if assignment_id not in active_ids and assignment_id not in disposition_ids:
            return packet_path, packet
    return None


def claim_next(
    *,
    dispatches_dir: Path,
    state_dir: Path,
    dispositions_dir: Path,
    worker_id: str,
    agent: Optional[str] = None,
    now: Optional[datetime] = None,
    lease_minutes: int = 90,
) -> Optional[ResearchClaim]:
    """Atomically claim the highest-priority available dispatch packet."""
    if not worker_id.strip():
        raise ResearchExecutionError("worker_id is required")
    if not 5 <= lease_minutes <= 480:
        raise ResearchExecutionError("lease_minutes must be between 5 and 480")
    now = now or datetime.now(timezone.utc)
    with _state_lock(state_dir):
        _expire_stale_claims(state_dir, now)
        active_ids = {
            path.stem for path in (state_dir / "claims").glob("*/*.json")
        }
        disposition_ids = set()
        for path in dispositions_dir.glob("*.json"):
            payload = _read_json(path)
            disposition_ids.add(str(payload.get("assignment_id") or path.stem))
        for packet_path, packet in _ranked_packets(dispatches_dir, agent):
            assignment_id = str(packet["assignment_id"])
            if assignment_id in active_ids or assignment_id in disposition_ids:
                continue
            assigned_agent = str(packet["assigned_agent"])
            claimed_at = _utc_iso(now)
            digest = hashlib.sha256(
                "{}:{}:{}".format(assignment_id, worker_id, claimed_at).encode()
            ).hexdigest()[:20]
            claim = ResearchClaim(
                claim_id="claim_{}".format(digest),
                assignment_id=assignment_id,
                source_item_id=str(packet.get("source_item_id") or ""),
                dispatch_id=str(packet.get("id") or ""),
                assigned_agent=assigned_agent,
                worker_id=worker_id.strip(),
                claimed_at=claimed_at,
                lease_expires_at=_utc_iso(
                    now + timedelta(minutes=lease_minutes)),
                packet_path=str(packet_path),
                agent_prompt_path=str(
                    Path(".claude/agents") / "{}.md".format(assigned_agent)),
            )
            _write_atomic(
                _claim_path(state_dir, assigned_agent, assignment_id),
                claim.to_dict())
            _append_event(state_dir, {
                "event": "claim_started", "at": claimed_at,
                "claim_id": claim.claim_id,
                "assignment_id": assignment_id,
                "worker_id": claim.worker_id,
                "assigned_agent": assigned_agent,
                "model_invocation_tracked": False,
            })
            return claim
    return None


def complete_claim(
    *,
    claim_path: Path,
    disposition_payload: Mapping[str, Any],
    state_dir: Path,
    dispositions_dir: Path,
    now: Optional[datetime] = None,
) -> ResearchDisposition:
    """Validate a disposition and atomically complete its active claim."""
    now = now or datetime.now(timezone.utc)
    with _state_lock(state_dir):
        claim_payload = _read_json(claim_path)
        if not claim_payload:
            raise ResearchExecutionError("active claim does not exist")
        claim = ResearchClaim.from_dict(claim_payload)
        expires = _parse_time(claim.lease_expires_at)
        if not expires or expires <= now:
            raise ResearchExecutionError("claim lease has expired")
        disposition = ResearchDisposition.from_dict(disposition_payload)
        if disposition.assignment_id != claim.assignment_id:
            raise ResearchExecutionError("disposition assignment_id does not match claim")
        if disposition.source_item_id != claim.source_item_id:
            raise ResearchExecutionError("disposition source_item_id does not match claim")
        decided_at = _parse_time(disposition.decided_at)
        claimed_at = _parse_time(claim.claimed_at)
        if not decided_at or (claimed_at and decided_at < claimed_at):
            raise ResearchExecutionError("disposition predates the claim")
        destination = dispositions_dir / "{}.json".format(claim.assignment_id)
        if destination.exists():
            existing = ResearchDisposition.from_dict(_read_json(destination))
            if existing.to_dict() != disposition.to_dict():
                raise ResearchExecutionError("a different disposition already exists")
        else:
            _write_atomic(destination, disposition.to_dict())
        completed = dict(claim_payload) | {
            "status": "completed", "completed_at": _utc_iso(now),
            "decision": disposition.decision,
            "disposition_path": str(destination),
        }
        _archive_claim(state_dir, claim_path, "claim_archive", completed)
        _append_event(state_dir, {
            "event": "claim_completed", "at": _utc_iso(now),
            "claim_id": claim.claim_id,
            "assignment_id": claim.assignment_id,
            "worker_id": claim.worker_id,
            "assigned_agent": claim.assigned_agent,
            "decision": disposition.decision,
        })
        return disposition


def mark_claim_invoked(
    *, claim_path: Path, state_dir: Path, provider: str, model: str,
    budget: Mapping[str, Any], now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Attach a model invocation to an active claim before accepting output."""
    if not provider.strip() or not model.strip():
        raise ResearchExecutionError("provider and model are required")
    now = now or datetime.now(timezone.utc)
    with _state_lock(state_dir):
        payload = _read_json(claim_path)
        if not payload:
            raise ResearchExecutionError("active claim does not exist")
        expires = _parse_time(payload.get("lease_expires_at"))
        if not expires or expires <= now:
            raise ResearchExecutionError("claim lease has expired")
        if payload.get("model_invocation_tracked"):
            raise ResearchExecutionError("claim invocation is already recorded")
        payload.update({
            "model_invocation_tracked": True,
            "invoked_at": _utc_iso(now),
            "provider": provider.strip(),
            "model": model.strip(),
            "invocation_budget": dict(budget),
        })
        _write_atomic(claim_path, payload)
        _append_event(state_dir, {
            "event": "model_invoked", "at": _utc_iso(now),
            "claim_id": payload.get("claim_id"),
            "assignment_id": payload.get("assignment_id"),
            "worker_id": payload.get("worker_id"),
            "assigned_agent": payload.get("assigned_agent"),
            "provider": provider.strip(), "model": model.strip(),
            "budget": dict(budget),
        })
        return payload


def mark_claim_phase_invoked(
    *, claim_path: Path, state_dir: Path, provider: str, model: str,
    budget: Mapping[str, Any], phase: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Record one named invocation phase on an active claim.

    This is the multi-stage counterpart to :func:`mark_claim_invoked`.  Named
    phases may each be recorded once, making screening and deep-research calls
    independently auditable without weakening the legacy single-call guard.
    """
    if not provider.strip() or not model.strip() or not phase.strip():
        raise ResearchExecutionError("provider, model, and phase are required")
    now = now or datetime.now(timezone.utc)
    with _state_lock(state_dir):
        payload = _read_json(claim_path)
        if not payload:
            raise ResearchExecutionError("active claim does not exist")
        expires = _parse_time(payload.get("lease_expires_at"))
        if not expires or expires <= now:
            raise ResearchExecutionError("claim lease has expired")
        invocations = list(payload.get("model_invocations") or [])
        if any(str(item.get("phase") or "") == phase.strip()
               for item in invocations if isinstance(item, Mapping)):
            raise ResearchExecutionError(
                f"claim invocation phase is already recorded: {phase.strip()}")
        invocation = {
            "phase": phase.strip(), "invoked_at": _utc_iso(now),
            "provider": provider.strip(), "model": model.strip(),
            "budget": dict(budget),
        }
        invocations.append(invocation)
        payload["model_invocations"] = invocations
        payload["model_invocation_tracked"] = True
        # Preserve the legacy first-invocation fields consumed by metrics and
        # old operational tooling.
        if not payload.get("invoked_at"):
            payload.update({
                "invoked_at": invocation["invoked_at"],
                "provider": invocation["provider"],
                "model": invocation["model"],
                "invocation_budget": invocation["budget"],
            })
        _write_atomic(claim_path, payload)
        _append_event(state_dir, {
            "event": "model_invoked", "at": invocation["invoked_at"],
            "claim_id": payload.get("claim_id"),
            "assignment_id": payload.get("assignment_id"),
            "worker_id": payload.get("worker_id"),
            "assigned_agent": payload.get("assigned_agent"),
            "phase": invocation["phase"],
            "provider": invocation["provider"],
            "model": invocation["model"],
            "budget": invocation["budget"],
        })
        return payload


def release_claim(
    *, claim_path: Path, state_dir: Path, reason: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Release a claim without completing the underlying dispatch packet."""
    if not reason.strip():
        raise ResearchExecutionError("release reason is required")
    now = now or datetime.now(timezone.utc)
    with _state_lock(state_dir):
        payload = _read_json(claim_path)
        if not payload:
            raise ResearchExecutionError("active claim does not exist")
        released = payload | {
            "status": "released", "released_at": _utc_iso(now),
            "release_reason": reason.strip(),
        }
        _archive_claim(state_dir, claim_path, "claim_released", released)
        _append_event(state_dir, {
            "event": "claim_released", "at": _utc_iso(now),
            "assignment_id": payload.get("assignment_id"),
            "worker_id": payload.get("worker_id"),
            "assigned_agent": payload.get("assigned_agent"),
            "reason": reason.strip(),
        })
        return released


def execution_status(state_dir: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return active and historical claim counts without changing state."""
    now = now or datetime.now(timezone.utc)
    active = [_read_json(path) for path in
              sorted((state_dir / "claims").glob("*/*.json"))]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_iso(now),
        "active": len(active),
        "expired_active": sum(
            bool(_parse_time(item.get("lease_expires_at"))
                 and _parse_time(item.get("lease_expires_at")) <= now)
            for item in active),
        "completed": len(list((state_dir / "claim_archive").glob("*/*.json"))),
        "released": len(list((state_dir / "claim_released").glob("*/*.json"))),
        "expired": len(list((state_dir / "claim_expired").glob("*/*.json"))),
        "claims": active,
        "semantics": {
            "claim_means": "research_work_started",
            "model_invocation_tracked": False,
            "completion_requires": "durable_research_disposition",
        },
    }
