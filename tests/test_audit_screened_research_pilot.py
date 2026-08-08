from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_screened_research_pilot import audit_pilot


TARGET = "assignment_target"


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def config(root: Path) -> Path:
    path = root / "config.yaml"
    path.write_text(f"""target_assignment_id: {TARGET}
worker_id: pilot-worker
allowed_agents: [strategy-scout]
limits:
  max_input_tokens_per_task: 75000
  max_output_tokens_per_task: 5000
  max_cost_usd_per_task: 0.01
provider:
  name: deep-provider
  model: model-1
screening:
  enabled: true
  limits:
    max_input_tokens: 25000
    max_output_tokens: 1000
    max_cost_usd: 0.01
  provider:
    name: screen-provider
    model: model-1
safety:
  trading_execution_allowed: false
""", encoding="utf-8")
    return path


def test_audit_is_pending_before_scheduled_run(tmp_path):
    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "pending"
    assert not report["failures"]


def test_audit_accepts_fail_closed_missing_target(tmp_path):
    write(tmp_path / "data/research_execution/worker_status.json", {
        "worker_id": "pilot-worker", "status": "blocked",
        "assignment_id": None,
        "error": f"target assignment is not available: {TARGET}",
        "safety": {"model_invoked": False},
    })

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "safe_noop"
    assert not report["failures"]


def test_cleanup_failure_dominates_safe_noop(tmp_path):
    write(tmp_path / "data/research_execution/worker_status.json", {
        "worker_id": "pilot-worker", "status": "blocked",
        "assignment_id": None,
        "error": f"target assignment is not available: {TARGET}",
        "safety": {"model_invoked": False},
    })
    marker = tmp_path / "marker"
    marker.touch()

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path), marker_path=marker)

    assert report["status"] == "fail"
    assert any("marker_removed" in item for item in report["failures"])


def completed_fixture(root: Path, *, screen_input: int = 20000) -> None:
    disposition = {
        "assignment_id": TARGET, "source_item_id": "source-1",
        "decided_at": "2026-08-09T04:21:00Z", "decision": "defer",
        "reason_codes": ["await_evidence"],
        "evidence_checked": ["CFTC filing"], "research_minutes": 4,
        "opportunity_id": None, "recheck_after": "2026-08-16T04:00:00Z",
        "notes": "screening_only",
    }
    write(root / f"research/dispositions/{TARGET}.json", disposition)
    write(root / f"data/research_execution/screenings/strategy-scout/{TARGET}.json", {
        "assignment_id": TARGET, "source_item_id": "source-1",
        "screening": {"decision": "defer"},
        "safety": {"authorizes_execution": False,
                   "authorizes_advancement": False},
    })
    write(root / f"data/research_execution/artifacts/strategy-scout/{TARGET}.json", {
        "assignment_id": TARGET, "artifact_type": "research_disposition",
        "safety": {"authorizes_execution": False},
    })
    write(root / f"data/research_execution/claim_archive/strategy-scout/{TARGET}--claim1.json", {
        "assignment_id": TARGET, "claim_id": "claim1", "status": "completed",
    })
    write(root / "data/research_execution/runs/2026-08-09/claim1.json", {
        "assignment_id": TARGET, "claim_id": "claim1", "status": "completed",
        "terminal_stage": "screening",
        "safety": {"authorizes_execution": False},
        "usage": {"input_tokens": screen_input, "output_tokens": 500,
                  "cost_usd": 0},
        "invocations": [{
            "phase": "screening", "provider": "screen-provider",
            "model": "model-1", "usage_measured": True,
            "usage": {"input_tokens": screen_input, "output_tokens": 500,
                      "cost_usd": 0},
        }],
    })


def test_audit_accepts_completed_screen_only_defer(tmp_path):
    completed_fixture(tmp_path)

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "pass"
    assert report["decision"] == "defer"
    assert report["invocation_phases"] == ["screening"]
    assert not report["failures"]


def test_audit_rejects_screening_over_token_limit(tmp_path):
    completed_fixture(tmp_path, screen_input=25001)

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "fail"
    assert any("screening_input_limit" in item for item in report["failures"])
