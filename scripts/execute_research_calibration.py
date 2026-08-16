#!/usr/bin/env python3
"""Execute the blinded research calibration through the real Codex provider.

This runner is isolated from dispatch queues and strategy state. It persists
only calibration observations and their graded report under the requested
output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.research_agent_worker import (  # noqa: E402
    CommandProvider,
    ProviderResult,
    ResearchWorkerError,
    ScreeningDecision,
)
from src.research_calibration import (  # noqa: E402
    ResearchCalibrationError,
    blinded_suite,
    grade_suite,
    validate_suite,
)
from src.research_outcomes import ResearchDisposition  # noqa: E402
from src.strategy_orchestration import OpportunityCard  # noqa: E402


UTC = timezone.utc
SCREEN_INPUT_LIMIT = 25_000
SCREEN_OUTPUT_LIMIT = 1_000
DEEP_INPUT_LIMIT = 75_000
DEEP_OUTPUT_LIMIT = 5_000


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)


def _provider(
    *, codex: Path, model: str, schema: Path, workspace: Path,
    timeout_seconds: int, name: str, allow_search: bool = True,
    reasoning_effort: str | None = None,
) -> CommandProvider:
    argv = [
        sys.executable, str(ROOT / "scripts/codex_research_provider.py"),
        "--codex", str(codex), "--model", model,
        "--schema", str(schema.resolve()),
        "--workspace", str(workspace.resolve()),
        "--timeout-seconds", str(timeout_seconds - 30),
    ]
    if not allow_search:
        argv.append("--disable-search")
    if reasoning_effort:
        argv.extend(("--reasoning-effort", reasoning_effort))
    return CommandProvider(
        argv=argv,
        name=name, model=model, cwd=ROOT,
        pass_env=["HOME", "CODEX_HOME"],
        billing_mode="chatgpt_subscription")


def _screen_request(case: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "research_calibration_screening_only",
        "case_id": case["case_id"],
        "assignment": dict(case["assignment"]),
        "screening_rules": [
            "Reject only if the named mechanism is absent, duplicated, illegal, "
            "or already falsified by supplied or public evidence.",
            "Defer only when a named external fact and recheck time can resolve "
            "the uncertainty. recheck_after must be an ISO-8601 UTC timestamp, "
            "not a prose condition, and blocking_fact must name the fact.",
            "Choose deep_research only with a concrete economic mechanism and "
            "the cheapest decisive test.",
            "Screening cannot advance, create an OpportunityCard, or authorize "
            "execution.",
        ],
        "output_contract": {
            "schema": "config/research_screen_output.schema.json",
            "decisions": ["reject", "defer", "deep_research"],
            "advance_allowed": False,
        },
        "budget": {
            "max_input_tokens": SCREEN_INPUT_LIMIT,
            "max_output_tokens": SCREEN_OUTPUT_LIMIT,
        },
        "safety": {
            "trading_execution_allowed": False,
            "queue_mutation_allowed": False,
            "registry_mutation_allowed": False,
            "local_commands_allowed": False,
        },
    }


def _deep_request(
    case: Mapping[str, Any], screening: ScreeningDecision,
    agent_instructions: str,
) -> Dict[str, Any]:
    assignment = dict(case["assignment"])
    return {
        "schema_version": 1,
        "mode": "research_calibration_deep_research",
        "case_id": case["case_id"],
        "assignment_id": assignment["assignment_id"],
        "source_item_id": assignment["source_item_id"],
        "assigned_agent": "strategy-scout",
        "agent_instructions": agent_instructions,
        "dispatch_packet": assignment,
        "screening_context": screening.to_dict(),
        "output_contract": {
            "required": ["disposition", "artifact_type", "artifact"],
            "allowed_artifact_types": [
                "opportunity_card", "research_disposition", "scout_rejection"],
            "disposition_schema": "src.research_outcomes.ResearchDisposition",
        },
        "budget": {
            "research_minutes": int(
                assignment.get("research_budget_minutes") or 30),
            "max_input_tokens": DEEP_INPUT_LIMIT,
            "max_output_tokens": DEEP_OUTPUT_LIMIT,
        },
        "safety": {
            "trading_execution_allowed": False,
            "queue_mutation_allowed": False,
            "registry_mutation_allowed": False,
            "local_commands_allowed": False,
        },
    }


def _check_usage(result: ProviderResult, *, screen: bool) -> None:
    input_limit = SCREEN_INPUT_LIMIT if screen else DEEP_INPUT_LIMIT
    output_limit = SCREEN_OUTPUT_LIMIT if screen else DEEP_OUTPUT_LIMIT
    if result.usage.input_tokens > input_limit:
        raise ResearchCalibrationError(
            f"{'screening' if screen else 'deep'} input-token limit exceeded: "
            f"{result.usage.input_tokens} > {input_limit}")
    if result.usage.output_tokens > output_limit:
        raise ResearchCalibrationError(
            f"{'screening' if screen else 'deep'} output-token limit exceeded: "
            f"{result.usage.output_tokens} > {output_limit}")


def _validate_deep(
    output: Mapping[str, Any], assignment: Mapping[str, Any],
) -> ResearchDisposition:
    disposition = ResearchDisposition.from_dict(output["disposition"])
    if disposition.assignment_id != assignment["assignment_id"]:
        raise ResearchCalibrationError("deep disposition assignment mismatch")
    if disposition.source_item_id != assignment["source_item_id"]:
        raise ResearchCalibrationError("deep disposition source mismatch")
    if not disposition.reason_codes or not disposition.evidence_checked:
        raise ResearchCalibrationError("deep disposition lacks reasons/evidence")
    artifact_type = str(output.get("artifact_type") or "")
    artifact = output.get("artifact")
    if not isinstance(artifact, Mapping):
        raise ResearchCalibrationError("deep artifact must be an object")
    if artifact_type == "opportunity_card":
        card = OpportunityCard.from_dict(dict(artifact))
        if disposition.decision != "advance" or disposition.opportunity_id != card.id:
            raise ResearchCalibrationError(
                "opportunity card and advance disposition do not agree")
    elif artifact_type == "scout_rejection":
        if disposition.decision != "reject":
            raise ResearchCalibrationError(
                "scout rejection requires reject disposition")
    elif artifact_type == "research_disposition":
        parsed = ResearchDisposition.from_dict(artifact)
        if parsed.to_dict() != disposition.to_dict():
            raise ResearchCalibrationError(
                "research disposition artifact differs from completion")
    else:
        raise ResearchCalibrationError(f"invalid deep artifact type: {artifact_type}")
    return disposition


def _screen_completion(
    screening: ScreeningDecision, assignment: Mapping[str, Any],
) -> ResearchDisposition:
    return ResearchDisposition(
        assignment_id=str(assignment["assignment_id"]),
        source_item_id=str(assignment["source_item_id"]),
        decided_at=_iso_now(), decision=screening.decision,
        reason_codes=list(screening.reason_codes),
        evidence_checked=list(screening.evidence_checked),
        research_minutes=screening.research_minutes,
        recheck_after=screening.recheck_after,
        blocking_fact=screening.blocking_fact,
        notes=("screening_only: " + screening.notes).strip())


def execute(
    suite_payload: Mapping[str, Any], *, screen_provider: CommandProvider,
    deep_provider: CommandProvider, output_dir: Path,
    reuse_screening_dir: Path | None = None,
) -> Dict[str, Any]:
    suite = validate_suite(suite_payload)
    public_cases = {case["case_id"]: case for case in
                    blinded_suite(suite)["cases"]}
    agent_instructions = (ROOT / ".claude/agents/strategy-scout.md").read_text(
        encoding="utf-8")
    observations = []
    for source_case in suite["cases"]:
        case = public_cases[source_case["case_id"]]
        assignment = case["assignment"]
        row: Dict[str, Any] = {
            "case_id": case["case_id"], "started_at": _iso_now(),
            "invocation_phases": [], "usage": {},
            "safety": {
                "authorizes_execution": False,
                "queue_or_registry_mutated": False,
            },
        }
        try:
            screening = None
            reuse_path = ((reuse_screening_dir / f"{case['case_id']}.json")
                          if reuse_screening_dir else None)
            if reuse_path and reuse_path.is_file():
                try:
                    prior = json.loads(reuse_path.read_text(encoding="utf-8"))
                    screening = ScreeningDecision.from_dict(prior["screening"])
                    prior_usage = prior.get("usage") or {}
                    input_tokens = int(
                        prior_usage.get("screening_input_tokens") or 0)
                    output_tokens = int(
                        prior_usage.get("screening_output_tokens") or 0)
                    if input_tokens > SCREEN_INPUT_LIMIT:
                        raise ResearchCalibrationError(
                            "reused screening input-token limit exceeded")
                    if output_tokens > SCREEN_OUTPUT_LIMIT:
                        raise ResearchCalibrationError(
                            "reused screening output-token limit exceeded")
                    row["invocation_phases"].append("screening")
                    row["usage"]["screening_input_tokens"] = input_tokens
                    row["usage"]["screening_output_tokens"] = output_tokens
                    row["screening"] = screening.to_dict()
                    row["screening_reused_from"] = str(reuse_path)
                except (KeyError, TypeError, ValueError, OSError,
                        json.JSONDecodeError) as exc:
                    screening = None
                    row["screening_reuse_rejected"] = (
                        f"{type(exc).__name__}: {exc}"[:500])
            if screening is None:
                screen_result = screen_provider.invoke(
                    _screen_request(case), timeout_seconds=210)
                row["invocation_phases"].append("screening")
                row["usage"]["screening_input_tokens"] = (
                    screen_result.usage.input_tokens)
                row["usage"]["screening_output_tokens"] = (
                    screen_result.usage.output_tokens)
                screening = ScreeningDecision.from_dict(screen_result.output)
                row["screening"] = screening.to_dict()
                _check_usage(screen_result, screen=True)
            if screening.decision == "deep_research":
                deep_result = deep_provider.invoke(
                    _deep_request(case, screening, agent_instructions),
                    timeout_seconds=630)
                row["invocation_phases"].append("deep_research")
                row["usage"]["deep_input_tokens"] = (
                    deep_result.usage.input_tokens)
                row["usage"]["deep_output_tokens"] = (
                    deep_result.usage.output_tokens)
                row["deep_output"] = dict(deep_result.output)
                _check_usage(deep_result, screen=False)
                disposition = _validate_deep(deep_result.output, assignment)
                row["final_disposition"] = disposition.to_dict()
            else:
                row["final_disposition"] = _screen_completion(
                    screening, assignment).to_dict()
            row["status"] = "completed"
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        row["finished_at"] = _iso_now()
        observations.append(row)
        _write_atomic(output_dir / f"{case['case_id']}.json", row)
    observation_payload = {
        "schema_version": 1, "suite_id": suite.get("suite_id"),
        "generated_at": _iso_now(), "observations": observations,
        "safety": {
            "authorizes_execution": False,
            "queue_or_registry_mutated": False,
        },
    }
    report = grade_suite(suite, observations)
    report["generated_at"] = _iso_now()
    report["observations_path"] = str(output_dir / "observations.json")
    _write_atomic(output_dir / "observations.json", observation_payload)
    _write_atomic(output_dir / "report.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path,
        default=ROOT / "config/research_calibration_cases.yaml")
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--reuse-screening-dir", type=Path,
        help="reuse prior measured screens that satisfy the current contract")
    parser.add_argument(
        "--ack-subscription-usage", action="store_true",
        help="required acknowledgement that this invokes the real provider")
    args = parser.parse_args(argv)
    if not args.ack_subscription_usage:
        print("calibration error: --ack-subscription-usage is required",
              file=sys.stderr)
        return 2
    try:
        suite = yaml.safe_load(args.cases.read_text(encoding="utf-8")) or {}
        workspace = args.output_dir / "workspace"
        screen_provider = _provider(
            codex=args.codex, model=args.model,
            schema=ROOT / "config/research_screen_output.schema.json",
            workspace=workspace, timeout_seconds=180,
            name="openai-codex-pro-screen", allow_search=False,
            reasoning_effort="low")
        deep_provider = _provider(
            codex=args.codex, model=args.model,
            schema=ROOT / "config/research_agent_output.schema.json",
            workspace=workspace, timeout_seconds=600,
            name="openai-codex-pro")
        report = execute(
            suite, screen_provider=screen_provider,
            deep_provider=deep_provider, output_dir=args.output_dir,
            reuse_screening_dir=args.reuse_screening_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, yaml.YAMLError, ResearchWorkerError,
            ResearchCalibrationError) as exc:
        print(f"calibration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
