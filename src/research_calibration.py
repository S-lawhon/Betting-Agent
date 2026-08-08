"""Blinded calibration suite and grader for the staged research worker."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from src.research_agent_worker import ScreeningDecision
from src.research_outcomes import ResearchDisposition


class ResearchCalibrationError(ValueError):
    """A calibration case or observation is incomplete or inconsistent."""


def validate_suite(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if int(payload.get("schema_version") or 0) != 1:
        raise ResearchCalibrationError("calibration suite must use schema_version 1")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ResearchCalibrationError("calibration suite requires exactly three cases")
    seen = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ResearchCalibrationError("each calibration case must be an object")
        case_id = str(case.get("case_id") or "")
        if not case_id or case_id in seen:
            raise ResearchCalibrationError("calibration case IDs must be unique")
        seen.add(case_id)
        if not isinstance(case.get("assignment"), Mapping):
            raise ResearchCalibrationError(f"{case_id}: assignment is required")
        oracle = case.get("oracle")
        if not isinstance(oracle, Mapping) or not oracle.get("screening_decisions"):
            raise ResearchCalibrationError(f"{case_id}: oracle is required")
        invalid = set(oracle["screening_decisions"]) - {
            "reject", "defer", "deep_research"}
        if invalid:
            raise ResearchCalibrationError(
                f"{case_id}: invalid screening oracle {sorted(invalid)}")
    return dict(payload)


def blinded_suite(payload: Mapping[str, Any]) -> Dict[str, Any]:
    suite = validate_suite(payload)
    return {
        "schema_version": 1,
        "suite_id": suite.get("suite_id"),
        "purpose": suite.get("purpose"),
        "cases": [{
            "case_id": case["case_id"],
            "assignment": dict(case["assignment"]),
            "required_output": {
                "screening": "config/research_screen_output.schema.json",
                "deep_research": "config/research_agent_output.schema.json",
            },
            "safety": {
                "authorizes_execution": False,
                "authorizes_registry_mutation": False,
                "expected_labels_disclosed": False,
            },
        } for case in suite["cases"]],
    }


def grade_suite(payload: Mapping[str, Any], observations: Sequence[Mapping[str, Any]],
                *, screening_input_limit: int = 25_000) -> Dict[str, Any]:
    suite = validate_suite(payload)
    cases = {str(case["case_id"]): case for case in suite["cases"]}
    rows = {str(row.get("case_id") or ""): row for row in observations}
    failures = []
    case_results = []
    for case_id, case in cases.items():
        row = rows.get(case_id)
        case_failures = []
        if not row:
            case_failures.append("missing observation")
            case_results.append({
                "case_id": case_id, "passed": False,
                "failures": case_failures})
            failures.extend(f"{case_id}: {item}" for item in case_failures)
            continue
        try:
            screening = ScreeningDecision.from_dict(row.get("screening") or {})
        except (KeyError, TypeError, ValueError) as exc:
            screening = None
            case_failures.append(f"invalid screening output: {exc}")
        oracle = case["oracle"]
        if screening:
            if screening.decision not in set(oracle["screening_decisions"]):
                case_failures.append(
                    f"screening decision {screening.decision!r} is outside oracle")
            usage = row.get("usage") or {}
            if int(usage.get("screening_input_tokens") or 0) > screening_input_limit:
                case_failures.append("screening input-token limit exceeded")
            phases = list(row.get("invocation_phases") or [])
            expected_phases = (["screening", "deep_research"]
                               if screening.decision == "deep_research"
                               else ["screening"])
            if phases != expected_phases:
                case_failures.append(
                    f"invocation phases {phases!r} != {expected_phases!r}")
            final_payload = row.get("final_disposition")
            expected_final = set(oracle.get("final_decisions") or [])
            if screening.decision == "deep_research" and not final_payload:
                case_failures.append("deep_research survivor has no final disposition")
            if expected_final and not final_payload:
                case_failures.append("oracle requires a final disposition")
            if final_payload:
                try:
                    final = ResearchDisposition.from_dict(final_payload)
                    if expected_final and final.decision not in expected_final:
                        case_failures.append(
                            f"final decision {final.decision!r} is outside oracle")
                except (KeyError, TypeError, ValueError) as exc:
                    case_failures.append(f"invalid final disposition: {exc}")
        passed = not case_failures
        case_results.append({
            "case_id": case_id, "passed": passed,
            "failures": case_failures})
        failures.extend(f"{case_id}: {item}" for item in case_failures)
    extras = sorted(set(rows) - set(cases) - {""})
    if extras:
        failures.append(f"unexpected observations: {extras}")
    return {
        "schema_version": 1,
        "suite_id": suite.get("suite_id"),
        "passed": not failures,
        "cases_passed": sum(bool(row["passed"]) for row in case_results),
        "cases_total": len(case_results),
        "case_results": case_results,
        "failures": failures,
        "acceptance": {
            "all_cases_observed": len(rows) == len(cases),
            "screening_input_limit": screening_input_limit,
            "screening_cannot_advance": True,
            "deep_research_only_for_survivors": True,
        },
    }
