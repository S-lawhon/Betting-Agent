#!/usr/bin/env python3
"""Run one bounded research-agent worker pass (dry-run by default)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.research_agent_worker import (  # noqa: E402
    CommandProvider,
    ResearchAgentWorker,
    ResearchWorkerError,
    ScreeningLimits,
    WorkerLimits,
)


def _config(path: Path) -> dict:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ResearchWorkerError(f"runtime config unavailable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ResearchWorkerError("runtime config must use schema_version 1")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config/research_agent_runtime.yaml")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dispatches-dir", type=Path,
                        default=ROOT / "data/research_triage/dispatches")
    parser.add_argument("--state-dir", type=Path,
                        default=ROOT / "data/research_execution")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research/dispositions")
    parser.add_argument("--execute", action="store_true",
                        help="invoke the configured provider; requires two "
                             "additional config gates")
    parser.add_argument(
        "--assignment-id",
        help="fail closed unless this exact pending assignment is available")
    parser.add_argument("--no-write-status", action="store_true",
                        help="preview without writing worker_status.json")
    args = parser.parse_args(argv)
    try:
        config = _config(args.config)
        if not config.get("enabled"):
            raise ResearchWorkerError("research runtime is disabled in config")
        limits = WorkerLimits(**dict(config.get("limits") or {}))
        screening_config = dict(config.get("screening") or {})
        screening_enabled = bool(screening_config.get("enabled"))
        screening_limits = ScreeningLimits(**dict(
            screening_config.get("limits") or {}))
        provider = None
        screening_provider = None
        execution_enabled = False
        provider_config = dict(config.get("provider") or {})
        if args.execute:
            if config.get("mode") != "execute":
                raise ResearchWorkerError(
                    "--execute requires config mode=execute")
            if provider_config.get("type") != "command":
                raise ResearchWorkerError(
                    "--execute requires provider.type=command")
            provider = CommandProvider(
                argv=list(provider_config.get("argv") or []),
                name=str(provider_config.get("name") or ""),
                model=str(provider_config.get("model") or ""),
                cwd=args.root,
                pass_env=list(provider_config.get("pass_env") or []),
                billing_mode=str(
                    provider_config.get("billing_mode") or "metered_api"))
            if screening_enabled:
                screen_provider_config = dict(
                    screening_config.get("provider") or {})
                if screen_provider_config.get("type") != "command":
                    raise ResearchWorkerError(
                        "screening execution requires provider.type=command")
                screening_provider = CommandProvider(
                    argv=list(screen_provider_config.get("argv") or []),
                    name=str(screen_provider_config.get("name") or ""),
                    model=str(screen_provider_config.get("model") or ""),
                    cwd=args.root,
                    pass_env=list(
                        screen_provider_config.get("pass_env") or []),
                    billing_mode=str(screen_provider_config.get(
                        "billing_mode") or "metered_api"))
            execution_enabled = True
        worker = ResearchAgentWorker(
            root=args.root, dispatches_dir=args.dispatches_dir,
            state_dir=args.state_dir, dispositions_dir=args.dispositions_dir,
            worker_id=str(config.get("worker_id") or ""), limits=limits,
            provider=provider, execution_enabled=execution_enabled,
            screening_provider=screening_provider,
            screening_limits=screening_limits,
            screening_enabled=screening_enabled,
            screening_agents=list(
                screening_config.get("agents") or ["strategy-scout"]),
            allowed_agents=list(config.get("allowed_agents") or []))
        target_assignment_id = (
            str(args.assignment_id or config.get("target_assignment_id") or "").strip()
            or None)
        result = worker.run_once(
            execute=args.execute, assignment_id=target_assignment_id,
            persist_status=not args.no_write_status)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result["status"] in {"blocked", "failed"} else 0
    except (OSError, TypeError, ValueError, ResearchWorkerError) as exc:
        print(f"research worker error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
