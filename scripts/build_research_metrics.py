#!/usr/bin/env python3
"""Build source/lane research funnel metrics from assignments and dispositions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research_outcomes import ResearchDisposition, summarize_research  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


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


def _load_dispositions(directory: Path) -> tuple:
    records: List[ResearchDisposition] = []
    errors: List[Dict[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
            records.append(ResearchDisposition.from_dict(payload))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return records, errors


def _load_dispatches(directory: Path) -> tuple:
    records: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    if (directory / "dispatches").exists() or (directory / "dispatch_archive").exists():
        paths = (list(directory.glob("dispatches/*/*.json"))
                 + list(directory.glob("dispatch_archive/*/*.json")))
    else:
        # Backward-compatible for callers that pass the pending directory.
        paths = list(directory.glob("*/*.json"))
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict) or not payload.get("assignment_id"):
                raise ValueError("dispatch must be an object with assignment_id")
            records.append(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return records, errors


def _load_claims(directory: Path) -> tuple:
    records: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for bucket in ("claims", "claim_archive", "claim_expired", "claim_released"):
        for path in sorted(directory.glob("{}/*/*.json".format(bucket))):
            try:
                payload = json.loads(path.read_text())
                if not isinstance(payload, dict) or not payload.get("assignment_id"):
                    raise ValueError("claim must be an object with assignment_id")
                records.append(dict(payload) | {"_claim_bucket": bucket})
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append({"path": str(path), "error": str(exc)})
    return records, errors


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latency_summary(values: List[float]) -> Dict[str, Any]:
    ordered = sorted(round(max(0.0, value), 4) for value in values)
    if not ordered:
        return {"count": 0, "median_hours": None, "p90_hours": None,
                "max_hours": None}
    p90_index = max(0, min(len(ordered) - 1, (9 * len(ordered) + 9) // 10 - 1))
    return {
        "count": len(ordered),
        "median_hours": round(float(median(ordered)), 2),
        "p90_hours": round(ordered[p90_index], 2),
        "max_hours": round(ordered[-1], 2),
    }


def _research_operations(
    assignments: List[Dict[str, Any]],
    dispositions: List[ResearchDisposition],
    dispatches: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
    *,
    now: datetime,
    worker_status: Optional[Mapping[str, Any]] = None,
    window_hours: int = 24,
    overdue_hours: int = 48,
) -> Dict[str, Any]:
    """Summarize real queue progress without implying model execution.

    A dispatch artifact proves only that deterministic triage created a task.
    Until an invocation/claim event exists, "started" must remain unavailable.
    A disposition newer than the latest dispatch is the durable completion
    signal, including when an old deferred assignment is reopened.
    """
    assignment_ids = {str(item.get("id")) for item in assignments if item.get("id")}
    latest_dispatch: Dict[str, Dict[str, Any]] = {}
    for item in dispatches:
        assignment_id = str(item.get("assignment_id") or "")
        if assignment_id not in assignment_ids:
            continue
        current = latest_dispatch.get(assignment_id)
        if (current is None or str(item.get("created_at") or "") >=
                str(current.get("created_at") or "")):
            latest_dispatch[assignment_id] = item

    latest_disposition: Dict[str, ResearchDisposition] = {}
    for item in dispositions:
        if item.assignment_id not in assignment_ids:
            continue
        current = latest_disposition.get(item.assignment_id)
        if current is None or item.decided_at >= current.decided_at:
            latest_disposition[item.assignment_id] = item

    latest_claim: Dict[str, Dict[str, Any]] = {}
    for item in claims:
        assignment_id = str(item.get("assignment_id") or "")
        if assignment_id not in assignment_ids:
            continue
        current = latest_claim.get(assignment_id)
        if (current is None or str(item.get("claimed_at") or "") >=
                str(current.get("claimed_at") or "")):
            latest_claim[assignment_id] = item

    cutoff = now - timedelta(hours=window_hours)
    activity = Counter()
    agent_stats: Dict[str, Counter] = defaultdict(Counter)
    oldest_by_agent: Dict[str, datetime] = {}
    oldest_pending: Optional[datetime] = None
    dispatch_to_claim: List[float] = []
    claim_to_decision: List[float] = []
    dispatch_to_decision: List[float] = []

    for assignment_id, dispatch in latest_dispatch.items():
        agent = str(dispatch.get("assigned_agent") or "unknown")
        row = agent_stats[agent]
        row["dispatched"] += 1
        row["allocated_minutes"] += int(
            dispatch.get("research_budget_minutes") or 0)
        dispatched_at = _parse_timestamp(dispatch.get("created_at"))
        if dispatched_at and dispatched_at >= cutoff:
            row["dispatched_24h"] += 1
            activity["dispatched"] += 1

        claim = latest_claim.get(assignment_id)
        if claim:
            row["started"] += 1
            claimed_at = _parse_timestamp(claim.get("claimed_at"))
            if dispatched_at and claimed_at:
                dispatch_to_claim.append(
                    (claimed_at - dispatched_at).total_seconds() / 3600.0)
            if claimed_at and claimed_at >= cutoff:
                row["started_24h"] += 1
                activity["started"] += 1
            if claim.get("model_invocation_tracked"):
                row["invoked"] += 1
                invoked_at = _parse_timestamp(claim.get("invoked_at"))
                if invoked_at and invoked_at >= cutoff:
                    row["invoked_24h"] += 1
                    activity["invoked"] += 1

        disposition = latest_disposition.get(assignment_id)
        completed = bool(disposition) and (
            not dispatched_at
            or (_parse_timestamp(disposition.decided_at) is not None
                and _parse_timestamp(disposition.decided_at) >= dispatched_at)
        )
        if completed and disposition:
            row["reviewed"] += 1
            row[disposition.decision] += 1
            row["research_minutes"] += disposition.research_minutes
            decided_at = _parse_timestamp(disposition.decided_at)
            claimed_at = _parse_timestamp((claim or {}).get("claimed_at"))
            if claimed_at and decided_at:
                claim_to_decision.append(
                    (decided_at - claimed_at).total_seconds() / 3600.0)
            if dispatched_at and decided_at:
                dispatch_to_decision.append(
                    (decided_at - dispatched_at).total_seconds() / 3600.0)
            if decided_at and decided_at >= cutoff:
                row["reviewed_24h"] += 1
                row[f"{disposition.decision}_24h"] += 1
                row["research_minutes_24h"] += disposition.research_minutes
                activity["reviewed"] += 1
                activity[disposition.decision] += 1
                activity["research_minutes"] += disposition.research_minutes
            continue

        row["pending"] += 1
        if claim:
            lease_expires = _parse_timestamp(claim.get("lease_expires_at"))
            is_active = claim.get("_claim_bucket") == "claims"
            if is_active and lease_expires and lease_expires > now:
                row["in_progress"] += 1
            elif is_active:
                row["stale_claims"] += 1
        if dispatched_at:
            if oldest_pending is None or dispatched_at < oldest_pending:
                oldest_pending = dispatched_at
            if agent not in oldest_by_agent or dispatched_at < oldest_by_agent[agent]:
                oldest_by_agent[agent] = dispatched_at
            age_hours = max(0.0, (now - dispatched_at).total_seconds() / 3600.0)
            if age_hours >= overdue_hours:
                row["overdue"] += 1

    agents: Dict[str, Dict[str, Any]] = {}
    for agent, values in sorted(agent_stats.items()):
        oldest = oldest_by_agent.get(agent)
        pending = values["pending"]
        overdue = values["overdue"]
        agents[agent] = {
            key: values[key] for key in (
                "dispatched", "pending", "overdue", "reviewed", "advance",
                "reject", "defer", "allocated_minutes", "research_minutes",
                "started", "in_progress", "stale_claims", "dispatched_24h",
                "started_24h", "invoked", "invoked_24h", "reviewed_24h", "advance_24h",
                "reject_24h", "defer_24h", "research_minutes_24h")
        } | {
            "pending": pending,
            "overdue": overdue,
            "oldest_pending_created_at": (
                oldest.isoformat().replace("+00:00", "Z") if oldest else None),
            "oldest_pending_age_hours": (
                round(max(0.0, (now - oldest).total_seconds() / 3600.0), 1)
                if oldest else None),
            "queue_state": (
                "in_progress" if values["in_progress"] else
                ("overdue" if overdue else ("pending" if pending else "idle"))),
        }

    total_pending = sum(item["pending"] for item in agent_stats.values())
    total_overdue = sum(item["overdue"] for item in agent_stats.values())
    total_in_progress = sum(item["in_progress"] for item in agent_stats.values())
    total_stale_claims = sum(item["stale_claims"] for item in agent_stats.values())
    worker_status = dict(worker_status or {})
    worker = ({
        key: worker_status.get(key) for key in (
            "generated_at", "worker_id", "status", "mode",
            "provider_configured", "billing_mode",
            "invocation_tracking_available",
            "assignment_id", "error", "daily_usage", "limits", "safety")
    } if worker_status else {
        "status": "unavailable", "mode": None,
        "invocation_tracking_available": False,
    })
    return {
        "semantics": {
            "dispatch_means": "task_packet_created",
            "agent_invocation_tracked": bool(
                worker.get("invocation_tracking_available")),
            "started_tracking_available": True,
            "started_means": "active_worker_claim_created",
            "completion_signal": "durable_research_disposition",
        },
        "window_hours": window_hours,
        "overdue_after_hours": overdue_hours,
        "activity_24h": {
            key: activity[key] for key in (
                "dispatched", "started", "invoked", "reviewed", "advance", "reject", "defer",
                "research_minutes")
        },
        "queue": {
            "pending": total_pending,
            "unclaimed": max(0, total_pending - total_in_progress),
            "in_progress": total_in_progress,
            "stale_claims": total_stale_claims,
            "overdue": total_overdue,
            "oldest_pending_created_at": (
                oldest_pending.isoformat().replace("+00:00", "Z")
                if oldest_pending else None),
            "oldest_pending_age_hours": (
                round(max(0.0, (now - oldest_pending).total_seconds() / 3600.0), 1)
                if oldest_pending else None),
        },
        "stage_latency": {
            "dispatch_to_claim": _latency_summary(dispatch_to_claim),
            "claim_to_decision": _latency_summary(claim_to_decision),
            "dispatch_to_decision": _latency_summary(dispatch_to_decision),
        },
        "agents": agents,
        "worker": worker,
    }


def _x_pilot_metrics(assignments: List[Dict[str, Any]],
                     dispositions: List[ResearchDisposition],
                     intake_manifest: Dict[str, Any]) -> Dict[str, Any]:
    usage = intake_manifest.get("x_usage") or {}
    month = str(usage.get("month") or "")
    x_assignments = [item for item in assignments
                     if item.get("source_name") == "X"
                     and (not month or str(item.get("created_at") or "").startswith(month))]
    x_ids = {str(item.get("id")) for item in x_assignments}
    reviewed = [item for item in dispositions if item.assignment_id in x_ids]
    advanced = [item for item in reviewed if item.decision == "advance"]
    cost = usage.get("estimated_cost_month_usd")
    return {
        "month": month or None,
        "estimated_cost_usd": cost,
        "post_reads": usage.get("post_reads_month"),
        "user_reads": usage.get("user_reads_month"),
        "assignments": len(x_assignments),
        "reviewed": len(reviewed),
        "advanced": len(advanced),
        "cost_per_assignment_usd": (
            round(float(cost) / len(x_assignments), 4)
            if isinstance(cost, (int, float)) and x_assignments else None),
        "cost_per_advanced_usd": (
            round(float(cost) / len(advanced), 4)
            if isinstance(cost, (int, float)) and advanced else None),
        "estimate_is_conservative": usage.get("estimate_is_conservative"),
        "status": usage.get("status"),
    }


def _collector_health(intake_manifest: Dict[str, Any]) -> Dict[str, Any]:
    counts = intake_manifest.get("collector_counts") or {}
    errors = intake_manifest.get("collector_errors") or []
    raw_feeds = {
        str(key)[:-4]: int(value or 0) for key, value in counts.items()
        if str(key).startswith("feed:") and str(key).endswith(":raw")
    }
    # Backward-compatible until the first run from the quality-gated collector.
    if not raw_feeds:
        raw_feeds = {
            str(key): int(value or 0) for key, value in counts.items()
            if str(key).startswith("feed:") and not str(key).endswith(":raw")
        }
    zero_feeds = sorted(key for key, value in raw_feeds.items() if value == 0)
    expected_empty = sorted({
        str(key)[:-len(":expected_empty")]
        for key, value in counts.items()
        if str(key).startswith("feed:")
        and str(key).endswith(":expected_empty") and value
    })
    unexpected_zero = sorted(set(zero_feeds) - set(expected_empty))
    status = "unknown"
    if raw_feeds or errors:
        status = "degraded" if errors or unexpected_zero else "healthy"
    return {
        "status": status,
        "academic_feed_items_raw": sum(raw_feeds.values()),
        "zero_academic_feeds": zero_feeds,
        "expected_empty_academic_feeds": expected_empty,
        "unexpected_zero_academic_feeds": unexpected_zero,
        "collector_error_count": len(errors),
        "collector_errors": errors[:20],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignments-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "assignments")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research" / "dispositions")
    parser.add_argument("--dispatches-dir", type=Path,
                        default=ROOT / "data" / "research_triage")
    parser.add_argument("--execution-dir", type=Path,
                        default=ROOT / "data" / "research_execution")
    parser.add_argument("--intake-manifest", type=Path,
                        default=ROOT / "data" / "research_intake"
                        / "latest_manifest.json")
    parser.add_argument("--crossvenue-metrics", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue"
                        / "metrics.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data" / "research_intake" / "metrics.json")
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    args = parser.parse_args(argv)

    assignments = _load_assignments(args.assignments_dir)
    dispositions, errors = _load_dispositions(args.dispositions_dir)
    dispatches, dispatch_errors = _load_dispatches(args.dispatches_dir)
    claims, claim_errors = _load_claims(args.execution_dir)
    metrics = summarize_research(assignments, dispositions, dispatches)
    metrics["invalid_dispositions"] = errors
    metrics["invalid_dispatches"] = dispatch_errors
    metrics["invalid_claims"] = claim_errors
    intake_manifest = _read_json(args.intake_manifest, {})
    metrics["x_pilot"] = _x_pilot_metrics(
        assignments, dispositions, intake_manifest)
    metrics["crossvenue_pilot"] = _read_json(args.crossvenue_metrics, {})
    metrics["collector_health"] = _collector_health(intake_manifest)
    triage_manifest = _read_json(args.dispatches_dir / "latest_manifest.json", {})
    quarantine_files = list(
        args.dispatches_dir.glob("dispatch_quarantine/*/*.json"))
    quarantine_total = (
        len(quarantine_files) if quarantine_files else
        triage_manifest.get("dispatches_quarantined_quality"))
    metrics["quality_control"] = {
        "status": "available" if intake_manifest or triage_manifest else "unknown",
        "intake_rejected": intake_manifest.get("quality_rejected"),
        "intake_rejection_reasons": (
            intake_manifest.get("quality_rejection_reasons") or {}),
        "triage_blocked": triage_manifest.get("quality_blocked"),
        "triage_blocked_assignment_ids": (
            triage_manifest.get("quality_blocked_assignment_ids") or {}),
        "legacy_dispatches_quarantined": quarantine_total,
        "legacy_dispatches_quarantined_latest_run": (
            triage_manifest.get("dispatches_quarantined_quality")),
        "quarantined_assignment_ids": (
            triage_manifest.get("quarantined_assignment_ids") or {}),
    }
    generated_at = _parse_now(args.now)
    worker_status = _read_json(args.execution_dir / "worker_status.json", {})
    metrics["operations"] = _research_operations(
        assignments, dispositions, dispatches, claims, now=generated_at,
        worker_status=worker_status)
    metrics["generated_at"] = generated_at.isoformat().replace("+00:00", "Z")
    _write_atomic(args.output, metrics)
    print(
        f"research metrics: assignments={metrics['assignments']} "
        f"reviewed={metrics['reviewed']} unreviewed={metrics['unreviewed']} "
        f"dispatched={metrics['dispatch']['dispatched']} "
        f"invalid={len(errors) + len(dispatch_errors)}"
    )
    return 2 if errors or dispatch_errors or claim_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
