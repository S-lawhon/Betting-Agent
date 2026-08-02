#!/usr/bin/env python3
"""Build source/lane research funnel metrics from assignments and dispositions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
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
    args = parser.parse_args(argv)

    assignments = _load_assignments(args.assignments_dir)
    dispositions, errors = _load_dispositions(args.dispositions_dir)
    dispatches, dispatch_errors = _load_dispatches(args.dispatches_dir)
    metrics = summarize_research(assignments, dispositions, dispatches)
    metrics["invalid_dispositions"] = errors
    metrics["invalid_dispatches"] = dispatch_errors
    metrics["x_pilot"] = _x_pilot_metrics(
        assignments, dispositions, _read_json(args.intake_manifest, {}))
    metrics["generated_at"] = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z")
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
