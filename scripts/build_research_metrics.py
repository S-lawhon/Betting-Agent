#!/usr/bin/env python3
"""Build source/lane research funnel metrics from assignments and dispositions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research_outcomes import ResearchDisposition, summarize_research  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignments-dir", type=Path,
                        default=ROOT / "data" / "research_intake" / "assignments")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research" / "dispositions")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data" / "research_intake" / "metrics.json")
    args = parser.parse_args(argv)

    assignments = _load_assignments(args.assignments_dir)
    dispositions, errors = _load_dispositions(args.dispositions_dir)
    metrics = summarize_research(assignments, dispositions)
    metrics["invalid_dispositions"] = errors
    _write_atomic(args.output, metrics)
    print(
        f"research metrics: assignments={metrics['assignments']} "
        f"reviewed={metrics['reviewed']} unreviewed={metrics['unreviewed']} "
        f"invalid={len(errors)}"
    )
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
