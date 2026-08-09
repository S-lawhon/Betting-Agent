#!/usr/bin/env python3
"""Read-only acceptance audit for one exact-target screened research pilot."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from src.research_outcomes import ResearchDisposition


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _aware_iso(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None


def _latest_run_assignment(state: Path, *, worker_id: str = "") -> str:
    """Assignment id of the newest recorded run, optionally for one worker."""
    newest_key = ()
    newest_id = ""
    for path in sorted((state / "runs").glob("*/*.json")):
        payload = _read_json(path)
        candidate = str(payload.get("assignment_id") or "")
        if not candidate:
            continue
        key = (str(payload.get("started_at") or ""), path.name)
        if key > newest_key:
            newest_key, newest_id = key, candidate
    return newest_id


def audit_pilot(
    *, root: Path, config_path: Path, marker_path: Path,
) -> dict[str, Any]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assignment_id = str(config.get("target_assignment_id") or "").strip()
    allowed_agents = [str(value) for value in config.get("allowed_agents") or []]
    agent = allowed_agents[0] if len(allowed_agents) == 1 else ""
    limits = config.get("limits") or {}
    screening_limits = (config.get("screening") or {}).get("limits") or {}
    state = root / "data/research_execution"
    pinned = bool(assignment_id)
    if not pinned:
        # A recurring worker has no pinned target, so audit whatever it most
        # recently ran. Resolved from the run record rather than the queue:
        # the queue says what COULD have been worked, the run says what was.
        assignment_id = _latest_run_assignment(
            state, worker_id=str(config.get("worker_id") or ""))
    disposition_path = root / "research/dispositions" / f"{assignment_id}.json"
    screening_path = state / "screenings" / agent / f"{assignment_id}.json"
    artifact_path = state / "artifacts" / agent / f"{assignment_id}.json"
    active_claims = list((state / "claims").glob(f"*/{assignment_id}.json"))
    archived_claims = list(
        (state / "claim_archive").glob(f"*/{assignment_id}--*.json"))
    released_claims = list(
        (state / "claim_released").glob(f"*/{assignment_id}--*.json"))
    runs = [
        payload for path in sorted((state / "runs").glob("*/*.json"))
        if (payload := _read_json(path)).get("assignment_id") == assignment_id
    ]
    worker = _read_json(state / "worker_status.json")
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            failures.append(f"{name}: {detail}")

    check("target_configured", bool(assignment_id) or not pinned,
          assignment_id or ("no run to audit yet" if not pinned else "missing"))
    check("single_allowed_agent", agent == "strategy-scout",
          repr(allowed_agents))
    check("screening_enabled", bool((config.get("screening") or {}).get(
        "enabled")), "screening must be enabled")
    check("trading_disabled", not bool((config.get("safety") or {}).get(
        "trading_execution_allowed")), "trading_execution_allowed must be false")
    check("marker_removed", not marker_path.exists(), str(marker_path))
    check("no_active_target_claim", not active_claims,
          f"active_claims={len(active_claims)}")

    if not runs and not disposition_path.exists():
        target_worker = worker.get("worker_id") == config.get("worker_id")
        safe_noop = (
            target_worker and worker.get("status") == "blocked"
            and worker.get("assignment_id") is None
            and not bool((worker.get("safety") or {}).get("model_invoked"))
            and "target assignment is not available" in str(worker.get("error") or "")
        )
        if not pinned and not assignment_id:
            # An unpinned worker with no recorded run has not invoked a model.
            # That is the same "nothing happened" outcome, reached because the
            # queue was empty or every packet was already claimed.
            safe_noop = True
        if released_claims:
            failures.append("target has a released claim but no durable disposition")
        status = ("fail" if failures else
                  "safe_noop" if safe_noop else "pending")
        return {
            "schema_version": 1, "status": status,
            "assignment_id": assignment_id, "decision": None,
            "invocation_phases": [], "usage": {},
            "checks": checks, "failures": failures, "warnings": warnings,
        }

    check("single_run", len(runs) == 1, f"runs={len(runs)}")
    run = runs[-1] if runs else {}
    check("run_completed", run.get("status") == "completed",
          str(run.get("status") or "missing"))
    check("run_cannot_execute",
          (run.get("safety") or {}).get("authorizes_execution") is False,
          repr((run.get("safety") or {}).get("authorizes_execution")))
    check("single_completed_claim", len(archived_claims) == 1,
          f"completed_claims={len(archived_claims)}")
    check("no_released_claim", not released_claims,
          f"released_claims={len(released_claims)}")

    disposition_payload = _read_json(disposition_path)
    try:
        disposition = ResearchDisposition.from_dict(disposition_payload)
    except (KeyError, TypeError, ValueError) as exc:
        disposition = None
        failures.append(f"invalid_disposition: {exc}")
    check("disposition_target_matches",
          bool(disposition and disposition.assignment_id == assignment_id),
          str(disposition_payload.get("assignment_id") or "missing"))
    if disposition:
        check("disposition_has_evidence",
              bool(disposition.reason_codes and disposition.evidence_checked),
              "reason_codes and evidence_checked are required")
        check("disposition_time_is_aware", _aware_iso(disposition.decided_at),
              disposition.decided_at)
        if disposition.decision == "defer":
            check("defer_has_dated_recheck", _aware_iso(disposition.recheck_after),
                  str(disposition.recheck_after))

    screening = _read_json(screening_path)
    screen_decision = str((screening.get("screening") or {}).get("decision") or "")
    check("screening_record_present", bool(screening), str(screening_path))
    check("screening_target_matches",
          screening.get("assignment_id") == assignment_id,
          str(screening.get("assignment_id") or "missing"))
    check("screening_cannot_advance",
          (screening.get("safety") or {}).get("authorizes_advancement") is False,
          repr((screening.get("safety") or {}).get("authorizes_advancement")))
    if disposition and screen_decision in {"reject", "defer"}:
        check("screening_disposition_agree",
              disposition.decision == screen_decision,
              f"screen={screen_decision} disposition={disposition.decision}")

    artifact = _read_json(artifact_path)
    check("artifact_present", bool(artifact), str(artifact_path))
    check("artifact_target_matches", artifact.get("assignment_id") == assignment_id,
          str(artifact.get("assignment_id") or "missing"))
    check("artifact_cannot_execute",
          (artifact.get("safety") or {}).get("authorizes_execution") is False,
          repr((artifact.get("safety") or {}).get("authorizes_execution")))

    invocations = run.get("invocations") or []
    phases = [str(row.get("phase") or "") for row in invocations]
    expected_phases = (["screening", "deep_research"]
                       if screen_decision == "deep_research" else ["screening"])
    check("phase_sequence", phases == expected_phases,
          f"actual={phases!r} expected={expected_phases!r}")
    check("usage_measured",
          bool(invocations) and all(row.get("usage_measured") is True
                                    for row in invocations),
          repr([row.get("usage_measured") for row in invocations]))
    check("terminal_stage",
          run.get("terminal_stage") == expected_phases[-1],
          str(run.get("terminal_stage") or "missing"))
    aggregate = {
        "input_tokens": sum(int((row.get("usage") or {}).get(
            "input_tokens") or 0) for row in invocations),
        "output_tokens": sum(int((row.get("usage") or {}).get(
            "output_tokens") or 0) for row in invocations),
        "cost_usd": round(sum(float((row.get("usage") or {}).get(
            "cost_usd") or 0) for row in invocations), 6),
    }
    run_usage = run.get("usage") or {}
    check("aggregate_usage_matches", all(
        run_usage.get(key) == value for key, value in aggregate.items()),
        f"run={run_usage!r} aggregate={aggregate!r}")
    for row in invocations:
        phase = str(row.get("phase") or "")
        usage: Mapping[str, Any] = row.get("usage") or {}
        phase_limits = screening_limits if phase == "screening" else limits
        phase_provider = ((config.get("screening") or {}).get("provider") or {}
                          if phase == "screening" else config.get("provider") or {})
        check(f"{phase}_provider_identity",
              row.get("provider") == phase_provider.get("name"),
              str(row.get("provider") or "missing"))
        check(f"{phase}_model_identity",
              row.get("model") == phase_provider.get("model"),
              str(row.get("model") or "missing"))
        check(f"{phase}_input_limit",
              int(usage.get("input_tokens") or 0)
              <= int(phase_limits.get("max_input_tokens")
                     or phase_limits.get("max_input_tokens_per_task") or 0),
              str(usage.get("input_tokens") or 0))
        check(f"{phase}_output_limit",
              int(usage.get("output_tokens") or 0)
              <= int(phase_limits.get("max_output_tokens")
                     or phase_limits.get("max_output_tokens_per_task") or 0),
              str(usage.get("output_tokens") or 0))
        check(f"{phase}_cost_limit",
              float(usage.get("cost_usd") or 0)
              <= float(phase_limits.get("max_cost_usd")
                       or phase_limits.get("max_cost_usd_per_task") or 0),
              str(usage.get("cost_usd") or 0))

    if disposition and disposition.decision == "advance":
        warnings.append("advance disposition requires explicit human review; "
                        "this audit does not promote strategy state")
    return {
        "schema_version": 1, "status": "pass" if not failures else "fail",
        "assignment_id": assignment_id,
        "decision": disposition.decision if disposition else None,
        "screening_decision": screen_decision or None,
        "invocation_phases": phases, "usage": run.get("usage") or {},
        "checks": checks, "failures": failures, "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument(
        "--config", type=Path,
        default=Path("config/research_agent_runtime_screened_pilot.yaml"))
    parser.add_argument(
        "--marker", type=Path,
        default=Path("/var/lib/research-codex/pilot-enabled"))
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else args.root / args.config
    report = audit_pilot(
        root=args.root, config_path=config_path, marker_path=args.marker)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"pass", "safe_noop"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
