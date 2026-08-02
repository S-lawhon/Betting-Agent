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
from typing import Any, Dict, List, Optional

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


def _research_operations(
    assignments: List[Dict[str, Any]],
    dispositions: List[ResearchDisposition],
    dispatches: List[Dict[str, Any]],
    *,
    now: datetime,
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

    cutoff = now - timedelta(hours=window_hours)
    activity = Counter()
    agent_stats: Dict[str, Counter] = defaultdict(Counter)
    oldest_by_agent: Dict[str, datetime] = {}
    oldest_pending: Optional[datetime] = None

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
            if decided_at and decided_at >= cutoff:
                row["reviewed_24h"] += 1
                row[f"{disposition.decision}_24h"] += 1
                row["research_minutes_24h"] += disposition.research_minutes
                activity["reviewed"] += 1
                activity[disposition.decision] += 1
                activity["research_minutes"] += disposition.research_minutes
            continue

        row["pending"] += 1
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
                "dispatched_24h", "reviewed_24h", "advance_24h",
                "reject_24h", "defer_24h", "research_minutes_24h")
        } | {
            "pending": pending,
            "overdue": overdue,
            "oldest_pending_created_at": (
                oldest.isoformat().replace("+00:00", "Z") if oldest else None),
            "oldest_pending_age_hours": (
                round(max(0.0, (now - oldest).total_seconds() / 3600.0), 1)
                if oldest else None),
            "queue_state": "overdue" if overdue else ("pending" if pending else "idle"),
        }

    total_pending = sum(item["pending"] for item in agent_stats.values())
    total_overdue = sum(item["overdue"] for item in agent_stats.values())
    return {
        "semantics": {
            "dispatch_means": "task_packet_created",
            "agent_invocation_tracked": False,
            "started_tracking_available": False,
            "completion_signal": "durable_research_disposition",
        },
        "window_hours": window_hours,
        "overdue_after_hours": overdue_hours,
        "activity_24h": {
            key: activity[key] for key in (
                "dispatched", "reviewed", "advance", "reject", "defer",
                "research_minutes")
        },
        "queue": {
            "pending": total_pending,
            "overdue": total_overdue,
            "oldest_pending_created_at": (
                oldest_pending.isoformat().replace("+00:00", "Z")
                if oldest_pending else None),
            "oldest_pending_age_hours": (
                round(max(0.0, (now - oldest_pending).total_seconds() / 3600.0), 1)
                if oldest_pending else None),
        },
        "agents": agents,
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignments-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "assignments")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research" / "dispositions")
    parser.add_argument("--dispatches-dir", type=Path,
                        default=ROOT / "data" / "research_triage")
    parser.add_argument("--intake-manifest", type=Path,
                        default=ROOT / "data" / "research_intake"
                        / "latest_manifest.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data" / "research_intake" / "metrics.json")
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    args = parser.parse_args(argv)

    assignments = _load_assignments(args.assignments_dir)
    dispositions, errors = _load_dispositions(args.dispositions_dir)
    dispatches, dispatch_errors = _load_dispatches(args.dispatches_dir)
    metrics = summarize_research(assignments, dispositions, dispatches)
    metrics["invalid_dispositions"] = errors
    metrics["invalid_dispatches"] = dispatch_errors
    metrics["x_pilot"] = _x_pilot_metrics(
        assignments, dispositions, _read_json(args.intake_manifest, {}))
    generated_at = _parse_now(args.now)
    metrics["operations"] = _research_operations(
        assignments, dispositions, dispatches, now=generated_at)
    metrics["generated_at"] = generated_at.isoformat().replace("+00:00", "Z")
    _write_atomic(args.output, metrics)
    print(
        f"research metrics: assignments={metrics['assignments']} "
        f"reviewed={metrics['reviewed']} unreviewed={metrics['unreviewed']} "
        f"dispatched={metrics['dispatch']['dispatched']} "
        f"invalid={len(errors) + len(dispatch_errors)}"
    )
    return 2 if errors or dispatch_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
