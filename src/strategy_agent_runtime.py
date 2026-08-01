"""
src/strategy_agent_runtime.py
──────────────────────────────
Queue-based worker for the recursive strategy registry.

The worker is intentionally boring:

- strategy requests are dropped as JSON files into role-specific inboxes
- the registry is persisted atomically after each successful mutation
- a heartbeat JSONL file tells the manager the daemon is alive

This keeps the live loop auditable and lets a service restart resume from disk
without special recovery code.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.strategy_orchestration import (
    IntegrityReport,
    MonitoringReport,
    OpportunityCard,
    PromotionDecision,
    StrategyRegistry,
    StrategySpec,
    StrategyTransitionError,
    ValidationReport,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc

ROLES = ("scout", "integrity", "validation", "promotion", "monitoring")
REQUEST_TYPES = {
    "opportunity": "scout",
    "spec": "integrity",
    "integrity": "validation",
    "validation": "promotion",
    "promotion": "monitoring",
    "monitoring": "monitoring",
    "transition": "monitoring",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, (datetime, Path)):
        return str(obj)
    return obj


def _sanitize_name(name: str) -> str:
    return name.replace("/", "__").replace("\\", "__")


@dataclass(frozen=True)
class AgentSummary:
    processed: int
    succeeded: int
    failed: int
    registry_size: int
    state_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "registry_size": self.registry_size,
            "state_counts": self.state_counts,
        }


class StrategyAgentRuntime:
    def __init__(
        self,
        registry_path: Path,
        queue_dir: Path,
        processed_dir: Optional[Path] = None,
        heartbeat_path: Optional[Path] = None,
        poll_interval_s: float = 60.0,
        clock: Optional[Any] = None,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.queue_dir = Path(queue_dir)
        self.processed_dir = Path(processed_dir or self.queue_dir.parent / "processed")
        self.heartbeat_path = Path(
            heartbeat_path or self.queue_dir.parent / "heartbeat.jsonl"
        )
        self.poll_interval_s = float(poll_interval_s)
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_once(self) -> AgentSummary:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)

        registry = StrategyRegistry.load_or_empty(self.registry_path)
        processed = succeeded = failed = 0

        for request_path in self._iter_requests():
            processed += 1
            try:
                request = json.loads(request_path.read_text())
                self._apply_request(registry, request)
                registry.dump(self.registry_path)
                self._archive_request(request_path, "done", request)
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - surface the failure
                failed += 1
                self._archive_request(
                    request_path,
                    "failed",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                logger.exception("strategy-agent request failed: %s", request_path)

        if processed == 0:
            # Still refresh the on-disk registry so a missing file becomes
            # visible to the service, and keep the heartbeat fresh.
            registry.dump(self.registry_path)

        summary = AgentSummary(
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            registry_size=len(registry.values()),
            state_counts=self._state_counts(registry),
        )
        self._append_heartbeat(summary)
        return summary

    def run_forever(self, stop: Optional[Any] = None) -> None:
        stop = stop or (lambda: False)
        while not stop():
            self.run_once()
            deadline = time.time() + self.poll_interval_s
            while time.time() < deadline and not stop():
                time.sleep(1.0)

    def _iter_requests(self) -> List[Path]:
        paths: List[Path] = []
        for role in ROLES:
            role_dir = self.queue_dir / role
            if not role_dir.exists():
                continue
            paths.extend(sorted(role_dir.glob("*.json")))
        return paths

    def _apply_request(self, registry: StrategyRegistry, request: Dict[str, Any]) -> None:
        kind = str(request.get("type") or "").strip().lower()
        strategy_id = str(request.get("strategy_id") or request.get("id") or "").strip()
        actor = str(request.get("actor") or request.get("source_agent") or kind or "system")
        payload = request.get("payload") or {}
        if not kind:
            raise ValueError("request missing type")
        if kind not in REQUEST_TYPES:
            raise ValueError(f"unsupported request type: {kind}")

        if kind == "opportunity":
            card = OpportunityCard.from_dict(payload)
            if not strategy_id:
                strategy_id = card.id
            if card.id != strategy_id:
                raise ValueError("request id does not match opportunity id")
            registry.register_opportunity(card)
            return

        if not strategy_id:
            raise ValueError("request missing strategy_id")

        if kind == "spec":
            registry.attach_spec(strategy_id, StrategySpec.from_dict(payload), actor=actor)
        elif kind == "integrity":
            registry.record_integrity(strategy_id, IntegrityReport.from_dict(payload),
                                      actor=actor)
        elif kind == "validation":
            registry.record_validation(strategy_id, ValidationReport.from_dict(payload),
                                       actor=actor)
        elif kind == "promotion":
            registry.record_promotion(strategy_id, PromotionDecision.from_dict(payload),
                                      actor=actor)
        elif kind == "monitoring":
            registry.record_monitoring(strategy_id, MonitoringReport.from_dict(payload),
                                       actor=actor)
        elif kind == "transition":
            to_state = str(payload.get("to_state") or "").strip()
            if not to_state:
                raise ValueError("transition request missing payload.to_state")
            registry.transition(
                strategy_id,
                to_state,
                actor=actor,
                kind=str(payload.get("kind") or "state_transition"),
                payload=payload,
            )
        else:
            raise ValueError(f"unsupported request type: {kind}")

    def _archive_request(self, request_path: Path, status: str, result: Dict[str, Any]) -> None:
        rel = request_path.name
        dest_dir = self.processed_dir / request_path.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = _utc_now().replace(":", "").replace("-", "")
        dest = dest_dir / f"{stamp}_{_sanitize_name(rel)}"
        payload = {
            "status": status,
            "processed_at": _utc_now(),
            "request": json.loads(request_path.read_text()),
            "result": result,
        }
        dest.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_jsonable))
        request_path.unlink()

    def _append_heartbeat(self, summary: AgentSummary) -> None:
        row = {
            "timestamp_utc": _utc_now(),
            "registry": str(self.registry_path),
            "queue_dir": str(self.queue_dir),
            "processed": summary.processed,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
            "registry_size": summary.registry_size,
            "state_counts": summary.state_counts,
        }
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        with self.heartbeat_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def _state_counts(self, registry: StrategyRegistry) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in registry.values():
            counts[record.state] = counts.get(record.state, 0) + 1
        return counts
