"""Deterministic handoffs between typed strategy-factory roles.

This module never invokes a model and never changes strategy state.  It turns a
successfully recorded artifact into one durable task for the only role allowed
to produce the next artifact.  The existing strategy-agent runtime remains the
sole registry writer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from src.strategy_orchestration import StrategyRecord


ROLE_TASK_TYPE = {
    "spec": "spec",
    "integrity": "integrity",
    "validation": "validation",
    "promotion": "promotion",
    "monitoring": "monitoring",
}


def next_role(record: StrategyRecord, trigger_type: str) -> Optional[str]:
    if record.state == "retired":
        return None
    if trigger_type == "opportunity" and record.spec is None:
        return "spec"
    if trigger_type == "spec" and record.integrity is None:
        return "integrity"
    if trigger_type == "integrity":
        return "validation" if record.integrity and record.integrity.passes() \
            else "promotion"
    if trigger_type == "validation":
        return "promotion"
    if trigger_type == "promotion" and record.state in {
            "paper_live", "live_small", "live_scaled", "degraded"}:
        return "monitoring"
    if trigger_type == "monitoring" and record.events:
        if record.events[-1].kind == "promotion_requested":
            return "promotion"
    return None


def enqueue_next_task(
    *, task_dir: Path, record: StrategyRecord, trigger_type: str,
) -> Optional[Path]:
    role = next_role(record, trigger_type)
    if role is None:
        return None
    event_number = len(record.events)
    strategy_id = record.opportunity.id
    path = Path(task_dir) / role / (
        f"{strategy_id}__e{event_number:04d}__{role}.json")
    if path.exists():
        return path
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "task_type": ROLE_TASK_TYPE[role],
        "assigned_role": role,
        "strategy_id": strategy_id,
        "trigger_type": trigger_type,
        "trigger_event_number": event_number,
        "created_at": (record.events[-1].timestamp_utc
                       if record.events else None),
        "record": record.to_dict(),
        "safety": {
            "mutates_registry": False,
            "deploys": False,
            "trades": False,
            "live_promotion_requires_human_approval": True,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return path
