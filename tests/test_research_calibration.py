from __future__ import annotations

from pathlib import Path

import yaml

from scripts.execute_research_calibration import execute
from src.research_agent_worker import ProviderResult, ProviderUsage
from src.research_calibration import blinded_suite, grade_suite


ROOT = Path(__file__).resolve().parents[1]


def suite():
    return yaml.safe_load((
        ROOT / "config/research_calibration_cases.yaml").read_text())


def screen(decision: str):
    return {
        "decision": decision,
        "reason_codes": ["calibration_reason"],
        "evidence_checked": ["case evidence"],
        "research_minutes": 2,
        "mechanism": "testable maker mechanism" if decision == "deep_research" else "",
        "cheapest_decisive_test": (
            "measure fee-net markout" if decision == "deep_research" else ""),
        "confidence": .8,
        "recheck_after": "2026-08-14T00:00:00Z" if decision == "defer" else None,
        "blocking_fact": "the scheduled filing release" if decision == "defer" else None,
        "notes": "calibration",
    }


def disposition(case: int, decision: str):
    return {
        "assignment_id": f"calibration_assignment_{case:03d}",
        "source_item_id": f"calibration_source_{case:03d}",
        "decided_at": "2026-08-07T12:00:00Z",
        "decision": decision,
        "reason_codes": ["calibration_reason"],
        "evidence_checked": ["case evidence"],
        "research_minutes": 10,
        "opportunity_id": "op_calibration" if decision == "advance" else None,
        "recheck_after": None,
        "blocking_fact": None,
        "next_action": None,
        "notes": "calibration",
    }


def test_blinded_suite_never_discloses_oracle():
    blinded = blinded_suite(suite())

    assert len(blinded["cases"]) == 3
    assert "oracle" not in str(blinded)
    assert all(case["safety"]["expected_labels_disclosed"] is False
               for case in blinded["cases"])


def test_three_case_known_answer_calibration_passes():
    observations = [
        {
            "case_id": "cal_001", "screening": screen("deep_research"),
            "final_disposition": disposition(1, "advance"),
            "usage": {"screening_input_tokens": 4000},
            "invocation_phases": ["screening", "deep_research"],
        },
        {
            "case_id": "cal_002", "screening": screen("reject"),
            "final_disposition": disposition(2, "reject"),
            "usage": {"screening_input_tokens": 3500},
            "invocation_phases": ["screening"],
        },
        {
            "case_id": "cal_003", "screening": screen("defer"),
            "usage": {"screening_input_tokens": 3000},
            "invocation_phases": ["screening"],
        },
    ]

    report = grade_suite(suite(), observations)

    assert report["passed"] is True
    assert report["cases_passed"] == report["cases_total"] == 3


def test_calibration_rejects_deep_call_after_screening_rejection():
    observations = [
        {
            "case_id": "cal_001", "screening": screen("deep_research"),
            "final_disposition": disposition(1, "advance"),
            "usage": {"screening_input_tokens": 4000},
            "invocation_phases": ["screening", "deep_research"],
        },
        {
            "case_id": "cal_002", "screening": screen("reject"),
            "final_disposition": disposition(2, "reject"),
            "usage": {"screening_input_tokens": 3500},
            "invocation_phases": ["screening", "deep_research"],
        },
        {
            "case_id": "cal_003", "screening": screen("defer"),
            "usage": {"screening_input_tokens": 3000},
            "invocation_phases": ["screening"],
        },
    ]

    report = grade_suite(suite(), observations)

    assert report["passed"] is False
    assert any("invocation phases" in failure for failure in report["failures"])


class CalibrationProvider:
    def __init__(self, stage):
        self.stage = stage

    def invoke(self, request, *, timeout_seconds):
        case_id = request["case_id"]
        if self.stage == "screen":
            decision = {
                "cal_001": "deep_research",
                "cal_002": "reject",
                "cal_003": "defer",
            }[case_id]
            output = screen(decision)
            usage = ProviderUsage(3000, 200, 0)
        else:
            assert case_id == "cal_001"
            output = {
                "disposition": disposition(1, "advance"),
                "artifact_type": "opportunity_card",
                "artifact": {
                    "id": "op_calibration",
                    "created_at": "2026-08-07T12:00:00Z",
                    "source_agent": "scout",
                    "market_family": "golf round leader",
                    "thesis": "pro-rata settlement can affect low-price fair value",
                    "edge_source": ["settlement mechanic"],
                    "external_data_needed": ["forward paper fills"],
                    "confidence": .7,
                    "urgency": "low",
                },
            }
            usage = ProviderUsage(5000, 500, 0)
        return ProviderResult("fixture", "fixture", output, usage)


def test_isolated_executor_persists_only_calibration_outputs(tmp_path):
    report = execute(
        suite(), screen_provider=CalibrationProvider("screen"),
        deep_provider=CalibrationProvider("deep"), output_dir=tmp_path)

    assert report["passed"] is True
    assert (tmp_path / "observations.json").exists()
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "queue").exists()
    observations = yaml.safe_load((tmp_path / "observations.json").read_text())
    assert [row["invocation_phases"] for row in observations["observations"]] == [
        ["screening", "deep_research"], ["screening"], ["screening"]]
