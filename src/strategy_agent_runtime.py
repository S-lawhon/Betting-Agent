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
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from src.strategy_chain import enqueue_next_task

logger = logging.getLogger(__name__)
UTC = timezone.utc

ROLE_BY_TYPE = {
    "opportunity": "scout",
    "spec": "spec",
    "integrity": "integrity",
    "validation": "validation",
    "promotion": "promotion",
    "monitoring": "monitoring",
}
ROLES = tuple(ROLE_BY_TYPE.values())


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
    queue_depth: int
    oldest_queue_age_minutes: Optional[float]
    task_depth: int = 0
    task_counts: Optional[Dict[str, int]] = None
    oldest_task_age_minutes: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "registry_size": self.registry_size,
            "state_counts": self.state_counts,
            "queue_depth": self.queue_depth,
            "oldest_queue_age_minutes": self.oldest_queue_age_minutes,
            "task_depth": self.task_depth,
            "task_counts": dict(self.task_counts or {}),
            "oldest_task_age_minutes": self.oldest_task_age_minutes,
        }


class StrategyAgentRuntime:
    def __init__(
        self,
        registry_path: Path,
        queue_dir: Path,
        processed_dir: Optional[Path] = None,
        heartbeat_path: Optional[Path] = None,
        task_dir: Optional[Path] = None,
        poll_interval_s: float = 60.0,
        heartbeat_max_bytes: int = 5_000_000,
        clock: Optional[Any] = None,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.queue_dir = Path(queue_dir)
        self.processed_dir = Path(processed_dir or self.queue_dir.parent / "processed")
        self.heartbeat_path = Path(
            heartbeat_path or self.queue_dir.parent / "heartbeat.jsonl"
        )
        self.task_dir = Path(task_dir or self.queue_dir.parent / "tasks")
        self.poll_interval_s = float(poll_interval_s)
        self.heartbeat_max_bytes = int(heartbeat_max_bytes)
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
            raw_request = ""
            try:
                raw_request = request_path.read_text(encoding="utf-8")
                request = json.loads(raw_request)
                role = request_path.parent.name
                self._apply_request(registry, request, role=role)
                registry.dump(self.registry_path)
                strategy_id = str(
                    request.get("strategy_id") or request.get("id") or "")
                if strategy_id and strategy_id in registry:
                    enqueue_next_task(
                        task_dir=self.task_dir,
                        record=registry.get(strategy_id),
                        trigger_type=str(request.get("type") or ""))
                self._archive_request(request_path, "done", request, raw_request)
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - surface the failure
                failed += 1
                logger.exception("strategy-agent request failed: %s", request_path)
                try:
                    self._archive_request(
                        request_path,
                        "failed",
                        {"error": f"{type(exc).__name__}: {exc}"},
                        raw_request,
                    )
                except Exception:  # noqa: BLE001 - preserve loop + heartbeat
                    logger.exception("could not archive failed request: %s", request_path)
                # A rejected operation must not influence later requests in the
                # same pass, even if a future mutation path regresses atomicity.
                registry = StrategyRegistry.load_or_empty(self.registry_path)

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
            queue_depth=len(self._iter_requests()),
            oldest_queue_age_minutes=self._oldest_queue_age_minutes(),
            task_depth=len(list(self.task_dir.glob("*/*.json"))),
            task_counts=self._task_counts(),
            oldest_task_age_minutes=self._oldest_task_age_minutes(),
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

    def _apply_request(self, registry: StrategyRegistry, request: Dict[str, Any],
                       role: str) -> None:
        kind = str(request.get("type") or "").strip().lower()
        strategy_id = str(request.get("strategy_id") or request.get("id") or "").strip()
        payload = request.get("payload") or {}
        if not kind:
            raise ValueError("request missing type")
        if kind not in ROLE_BY_TYPE:
            raise ValueError(f"unsupported request type: {kind}")
        expected_role = ROLE_BY_TYPE[kind]
        if role != expected_role:
            raise ValueError(
                f"request type {kind} belongs to {expected_role}, not {role}"
            )
        actor = role  # authority comes from the inbox, never caller-controlled JSON

        if kind == "opportunity":
            card = OpportunityCard.from_dict(payload)
            if not strategy_id:
                strategy_id = card.id
            if card.id != strategy_id:
                raise ValueError("request id does not match opportunity id")
            if card.source_agent != actor:
                raise ValueError("opportunity source_agent must match scout inbox")
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
        else:
            raise ValueError(f"unsupported request type: {kind}")

    def _archive_request(self, request_path: Path, status: str,
                         result: Dict[str, Any], raw_request: str) -> None:
        rel = request_path.name
        dest_dir = self.processed_dir / request_path.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = _utc_now().replace(":", "").replace("-", "")
        dest = dest_dir / f"{stamp}_{_sanitize_name(rel)}"
        payload = {
            "status": status,
            "processed_at": _utc_now(),
            "request": self._decode_for_archive(raw_request),
            "result": result,
        }
        serialized = json.dumps(payload, indent=2, sort_keys=True, default=_jsonable)
        with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=str(dest_dir), prefix=".archive-",
                suffix=".tmp", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
            tmp_path = Path(fh.name)
        os.replace(tmp_path, dest)
        request_path.unlink()

    @staticmethod
    def _decode_for_archive(raw_request: str) -> Any:
        try:
            return json.loads(raw_request)
        except (TypeError, json.JSONDecodeError):
            return {"raw": raw_request}

    def _append_heartbeat(self, summary: AgentSummary) -> None:
        previous = self._last_heartbeat()
        consecutive_failed = (
            int(previous.get("consecutive_failed_passes", 0)) + 1
            if summary.failed else 0
        )
        row = {
            "timestamp_utc": _utc_now(),
            "registry": str(self.registry_path),
            "queue_dir": str(self.queue_dir),
            "processed": summary.processed,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
            "registry_size": summary.registry_size,
            "state_counts": summary.state_counts,
            "queue_depth": summary.queue_depth,
            "oldest_queue_age_minutes": summary.oldest_queue_age_minutes,
            "task_depth": summary.task_depth,
            "task_counts": dict(summary.task_counts or {}),
            "oldest_task_age_minutes": summary.oldest_task_age_minutes,
            "consecutive_failed_passes": consecutive_failed,
        }
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        if (self.heartbeat_path.exists()
                and self.heartbeat_path.stat().st_size >= self.heartbeat_max_bytes):
            rotated = self.heartbeat_path.with_suffix(
                self.heartbeat_path.suffix + ".1"
            )
            os.replace(self.heartbeat_path, rotated)
        with self.heartbeat_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def _last_heartbeat(self) -> Dict[str, Any]:
        if not self.heartbeat_path.exists():
            return {}
        try:
            lines = self.heartbeat_path.read_text(encoding="utf-8").splitlines()
            return json.loads(lines[-1]) if lines else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _oldest_queue_age_minutes(self) -> Optional[float]:
        requests = self._iter_requests()
        if not requests:
            return None
        oldest_mtime = min(path.stat().st_mtime for path in requests)
        return round(max(0.0, time.time() - oldest_mtime) / 60.0, 1)

    def _state_counts(self, registry: StrategyRegistry) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in registry.values():
            counts[record.state] = counts.get(record.state, 0) + 1
        return counts

    def _task_counts(self) -> Dict[str, int]:
        return {
            role: len(list((self.task_dir / role).glob("*.json")))
            for role in ROLES
            if (self.task_dir / role).exists()
        }

    def _oldest_task_age_minutes(self) -> Optional[float]:
        tasks = list(self.task_dir.glob("*/*.json"))
        if not tasks:
            return None
        oldest_mtime = min(path.stat().st_mtime for path in tasks)
        return round(max(0.0, time.time() - oldest_mtime) / 60.0, 1)
