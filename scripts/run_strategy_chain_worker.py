#!/usr/bin/env python3
"""Consume one deterministic strategy-role task and queue its typed output."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.research_agent_worker import CommandProvider  # noqa: E402
from src.strategy_orchestration import (  # noqa: E402
    IntegrityReport, MonitoringReport, PromotionDecision, StrategyRegistry,
    StrategySpec, ValidationReport,
)


ARTIFACT_CLASS = {
    "spec": StrategySpec,
    "integrity": IntegrityReport,
    "validation": ValidationReport,
    "promotion": PromotionDecision,
    "monitoring": MonitoringReport,
}
PROMPT_NAME = {
    "spec": "strategy-spec.toml",
    "integrity": "strategy-integrity.toml",
    "validation": "strategy-validator.toml",
    "promotion": "strategy-promotion.toml",
    "monitoring": "strategy-monitor.toml",
}


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _config(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if payload.get("schema_version") != 1:
        raise ValueError("strategy chain config must use schema_version 1")
    return payload


def _retry_state(state_dir: Path, path: Path) -> dict:
    retry = state_dir / "chain_retry" / path.parent.name / path.name
    try:
        return json.loads(retry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _retry_blocked(state_dir: Path, path: Path, now: datetime) -> bool:
    state = _retry_state(state_dir, path)
    if state.get("dead_lettered"):
        return True
    value = state.get("retry_after")
    if not value:
        return False
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) > now


def _tasks(task_dir: Path, state_dir: Path, now: datetime) -> list[Path]:
    paths: list[Path] = []
    for role in ARTIFACT_CLASS:
        paths.extend(task_dir.glob(f"{role}/*.json"))
    return sorted(
        (path for path in paths if not _retry_blocked(state_dir, path, now)),
        key=lambda path: (path.stat().st_mtime, str(path)))


def _record_failure(state_dir: Path, path: Path, error: Exception,
                    now: datetime) -> dict:
    previous = _retry_state(state_dir, path)
    failures = int(previous.get("failure_count") or 0) + 1
    dead = failures >= 3
    cooldown = min(180 * (2 ** (failures - 1)), 1440)
    payload = {
        "failure_count": failures,
        "last_failed_at": now.isoformat(),
        "last_error": f"{type(error).__name__}: {error}"[:500],
        "retry_after": None if dead else (now + timedelta(minutes=cooldown)).isoformat(),
        "dead_lettered": dead,
    }
    _write_atomic(state_dir / "chain_retry" / path.parent.name / path.name, payload)
    return payload


def _attempts_today(state_dir: Path, now: datetime) -> int:
    directory = state_dir / "chain_runs" / now.date().isoformat()
    return len(list(directory.glob("*.json")))


def _archive_task(path: Path, state_dir: Path, bucket: str,
                  payload: Mapping[str, Any]) -> None:
    destination = state_dir / bucket / path.parent.name / path.name
    _write_atomic(destination, payload)
    path.unlink(missing_ok=True)


def _finish(state_dir: Path, payload: Mapping[str, Any], code: int) -> int:
    _write_atomic(state_dir / "chain_status.json", payload)
    print(json.dumps(dict(payload), indent=2, sort_keys=True))
    return code


def _task_is_current(task: Mapping[str, Any], registry: StrategyRegistry) -> bool:
    strategy_id = str(task.get("strategy_id") or "")
    if strategy_id not in registry:
        return False
    record = registry.get(strategy_id)
    return len(record.events) == int(task.get("trigger_event_number") or -1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config/strategy_chain_runtime.yaml")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    config = _config(args.config)
    if not config.get("enabled"):
        raise SystemExit("strategy chain runtime is disabled")
    task_dir = args.root / "data/strategy_agents/tasks"
    state_dir = args.root / "data/strategy_agents"
    registry_path = state_dir / "registry.json"
    registry = StrategyRegistry.load_or_empty(registry_path)
    now = datetime.now(timezone.utc)
    tasks = _tasks(task_dir, state_dir, now)
    while tasks:
        path = tasks.pop(0)
        task = json.loads(path.read_text(encoding="utf-8"))
        if _task_is_current(task, registry):
            break
        _archive_task(path, state_dir, "task_obsolete", {
            "status": "obsolete", "task": task,
            "archived_at": datetime.now(timezone.utc).isoformat(),
        })
    else:
        return _finish(state_dir, {
            "status": "idle", "worker_id": config["worker_id"],
            "generated_at": now.isoformat(),
        }, 1)
    if not args.execute:
        return _finish(state_dir, {
            "status": "dry_run", "worker_id": config["worker_id"],
            "task": str(path), "generated_at": now.isoformat(),
        }, 0)
    if config.get("mode") != "execute":
        raise SystemExit("--execute requires mode=execute")
    if _attempts_today(state_dir, now) >= int(config["max_attempts_per_utc_day"]):
        return _finish(state_dir, {
            "status": "blocked", "worker_id": config["worker_id"],
            "error": "daily attempt limit", "generated_at": now.isoformat(),
        }, 1)
    role = str(task["assigned_role"])
    prompt = args.root / ".Codex/agents" / PROMPT_NAME[role]
    request = {
        "mode": "strategy_factory_role",
        "role": role,
        "task": task,
        "agent_instructions": prompt.read_text(encoding="utf-8"),
        "output_contract": {
            "return": "one direct typed JSON artifact",
            "python_type": ARTIFACT_CLASS[role].__name__,
        },
        "safety": config["safety"],
    }
    provider_config = config["provider"]
    provider = CommandProvider(
        argv=provider_config["argv"], name=provider_config["name"],
        model=provider_config["model"], cwd=args.root,
        pass_env=provider_config.get("pass_env") or [],
        billing_mode=provider_config.get("billing_mode") or "metered_api")
    try:
        result = provider.invoke(
            request, timeout_seconds=int(config["timeout_seconds"]))
        artifact = dict(result.output.get("payload") or result.output)
        typed = ARTIFACT_CLASS[role].from_dict(artifact)
        if (role == "promotion" and typed.decision == "promote"
                and typed.to_state in {"live_small", "live_scaled"}):
            raise ValueError("unattended worker cannot queue live promotion")
    except Exception as exc:
        retry = _record_failure(state_dir, path, exc, now)
        failure = {
            "status": "failed", "worker_id": config["worker_id"],
            "strategy_id": task["strategy_id"], "role": role,
            "task_path": str(path), "retry": retry,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "safety": config["safety"],
        }
        run_path = (state_dir / "chain_runs" / now.date().isoformat()
                    / f"{path.stem}__failure_{retry['failure_count']}.json")
        _write_atomic(run_path, failure)
        return _finish(state_dir, failure, 2)
    generated = state_dir / "generated" / role / path.name
    _write_atomic(generated, artifact)
    request_path = state_dir / "queue" / role / path.name
    _write_atomic(request_path, {
        "type": role,
        "strategy_id": task["strategy_id"],
        "actor": role,
        "payload": artifact,
        "source_task": str(path),
    })
    run = {
        "status": "submitted", "worker_id": config["worker_id"],
        "strategy_id": task["strategy_id"], "role": role,
        "task_path": str(path), "generated_path": str(generated),
        "request_path": str(request_path), "usage": result.usage.to_dict(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "safety": config["safety"],
    }
    run_path = (state_dir / "chain_runs" / now.date().isoformat()
                / f"{path.stem}.json")
    _write_atomic(run_path, run)
    _archive_task(path, state_dir, "task_submitted", {
        "status": "submitted", "task": task, "run": run,
    })
    return _finish(state_dir, run, 0)


if __name__ == "__main__":
    raise SystemExit(main())
