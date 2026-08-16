from __future__ import annotations

import json
from pathlib import Path

import scripts.audit_screened_research_pilot as audit_script
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


def test_unpinned_audit_reports_current_preclaim_block_not_stale_run(tmp_path):
    path = config(tmp_path)
    path.write_text(path.read_text().replace(
        f"target_assignment_id: {TARGET}\n", "status_filename: screening_status.json\n"))
    completed_fixture(tmp_path)
    write(tmp_path / "data/research_execution/screening_status.json", {
        "worker_id": "pilot-worker", "status": "blocked",
        "assignment_id": "assignment_literature",
        "error": "packet budget 45m exceeds worker limit 30m",
        "safety": {"model_invoked": False},
    })

    report = audit_pilot(
        root=tmp_path, config_path=path, marker_path=tmp_path / "marker")

    assert report["status"] == "pending"
    assert report["assignment_id"] == "assignment_literature"
    assert report["invocation_phases"] == []


def test_unpinned_audit_accepts_daily_attempt_cap_as_safe_noop(tmp_path):
    path = config(tmp_path)
    path.write_text(path.read_text().replace(
        f"target_assignment_id: {TARGET}\n", "status_filename: screening_status.json\n"))
    write(tmp_path / "data/research_execution/screening_status.json", {
        "worker_id": "pilot-worker", "status": "blocked",
        "assignment_id": "assignment_waiting",
        "error": "daily model-attempt limit: reservation would exceed it",
        "safety": {"model_invoked": False},
    })

    report = audit_pilot(
        root=tmp_path, config_path=path, marker_path=tmp_path / "marker")

    assert report["status"] == "safe_noop"
    assert report["assignment_id"] == "assignment_waiting"
    assert not report["failures"]


def test_cli_failure_uses_exit_two_so_systemd_cannot_accept_it(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit_script, "audit_pilot",
        lambda **kwargs: {"status": "fail", "failures": ["bad run"]})

    result = audit_script.main([
        "--root", str(tmp_path), "--config", "daily.yaml",
        "--marker", str(tmp_path / "marker"),
    ])

    assert result == 2
    service = Path(
        "scripts/systemd/research-agent-screened-daily.service").read_text()
    assert "SuccessExitStatus=0 1" in service
    assert "SuccessExitStatus=0 1 2" not in service


def completed_fixture(root: Path, *, screen_input: int = 20000,
                      agent: str = "strategy-scout") -> None:
    disposition = {
        "assignment_id": TARGET, "source_item_id": "source-1",
        "decided_at": "2026-08-09T04:21:00Z", "decision": "defer",
        "reason_codes": ["await_evidence"],
        "evidence_checked": ["CFTC filing"], "research_minutes": 4,
        "opportunity_id": None, "recheck_after": "2026-08-16T04:00:00Z",
        "blocking_fact": "the scheduled filing publication",
        "next_action": None,
        "notes": "screening_only",
    }
    write(root / f"research/dispositions/{TARGET}.json", disposition)
    write(root / f"data/research_execution/screenings/{agent}/{TARGET}.json", {
        "assignment_id": TARGET, "source_item_id": "source-1",
        "screening": {"decision": "defer"},
        "safety": {"authorizes_execution": False,
                   "authorizes_advancement": False},
    })
    write(root / f"data/research_execution/artifacts/{agent}/{TARGET}.json", {
        "assignment_id": TARGET, "artifact_type": "research_disposition",
        "safety": {"authorizes_execution": False},
    })
    write(root / f"data/research_execution/claim_archive/{agent}/{TARGET}--claim1.json", {
        "assignment_id": TARGET, "claim_id": "claim1", "status": "completed",
    })
    write(root / "data/research_execution/runs/2026-08-09/claim1.json", {
        "assignment_id": TARGET, "claim_id": "claim1", "status": "completed",
        "assigned_agent": agent,
        "started_at": "2026-08-09T05:10:00Z",
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


def failed_attempt_fixture(root: Path, *, day: str = "2026-08-08") -> None:
    write(root / f"data/research_execution/runs/{day}/claim0.json", {
        "assignment_id": TARGET, "claim_id": "claim0", "status": "failed",
        "started_at": f"{day}T05:10:00Z",
        "error": "provider exceeded input-token limit",
    })
    write(root / "data/research_execution/claim_released/strategy-scout/"
          f"{TARGET}--claim0.json", {
              "assignment_id": TARGET, "claim_id": "claim0",
              "status": "released",
          })


def test_audit_accepts_retry_after_released_failure(tmp_path):
    completed_fixture(tmp_path)
    failed_attempt_fixture(tmp_path)

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "pass"
    assert not report["failures"]


def test_audit_rejects_released_claim_with_no_failed_run(tmp_path):
    completed_fixture(tmp_path)
    write(tmp_path / "data/research_execution/claim_released/strategy-scout/"
          f"{TARGET}--claim9.json", {
              "assignment_id": TARGET, "claim_id": "claim9",
              "status": "released",
          })

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "fail"
    assert any("released_claims_match_failed_runs" in item
               for item in report["failures"])


def test_audit_rejects_second_completed_run(tmp_path):
    completed_fixture(tmp_path)
    write(tmp_path / "data/research_execution/runs/2026-08-10/claim2.json", {
        "assignment_id": TARGET, "claim_id": "claim2", "status": "completed",
        "started_at": "2026-08-10T05:10:00Z",
    })

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "fail"
    assert any("single_completed_run" in item for item in report["failures"])


def test_audit_rejects_failed_attempt_after_completion(tmp_path):
    completed_fixture(tmp_path)
    failed_attempt_fixture(tmp_path, day="2026-08-10")

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "fail"
    assert any("no_attempt_after_completion" in item
               for item in report["failures"])


def test_audit_rejects_screening_over_token_limit(tmp_path):
    completed_fixture(tmp_path, screen_input=25001)

    report = audit_pilot(
        root=tmp_path, config_path=config(tmp_path),
        marker_path=tmp_path / "marker")

    assert report["status"] == "fail"
    assert any("screening_input_limit" in item for item in report["failures"])


def test_unpinned_config_audits_the_most_recent_run(tmp_path):
    config = tmp_path / "daily.yaml"
    config.write_text(
        "worker_id: daily\nallowed_agents: [strategy-scout]\n"
        "screening: {enabled: true, limits: {max_input_tokens: 25000, "
        "max_output_tokens: 1000, max_cost_usd: 0.01},"
        " provider: {name: screen, model: m}}\n"
        "limits: {max_input_tokens_per_task: 75000}\n"
        "provider: {name: deep, model: m}\n"
        "safety: {trading_execution_allowed: false}\n", encoding="utf-8")
    report = audit_pilot(root=tmp_path, config_path=config,
                         marker_path=tmp_path / "absent")
    # Nothing has run yet: that is a quiet day, not a fault.
    assert report["status"] == "safe_noop"
    assert report["assignment_id"] == ""

    runs = tmp_path / "data/research_execution/runs/2026-08-09"
    runs.mkdir(parents=True)
    (runs / "claim_x.json").write_text(json.dumps({
        "assignment_id": "a_newer", "started_at": "2026-08-09T05:10:00Z",
        "status": "completed"}), encoding="utf-8")
    older = tmp_path / "data/research_execution/runs/2026-08-08"
    older.mkdir(parents=True)
    (older / "claim_w.json").write_text(json.dumps({
        "assignment_id": "a_older", "started_at": "2026-08-08T19:37:41Z",
        "status": "completed"}), encoding="utf-8")

    report = audit_pilot(root=tmp_path, config_path=config,
                         marker_path=tmp_path / "absent")
    assert report["assignment_id"] == "a_newer"


def multi_lane_config(root: Path, *, screened) -> Path:
    path = root / "multi.yaml"
    path.write_text(f"""target_assignment_id: {TARGET}
worker_id: pilot-worker
allowed_agents: [strategy-scout, literature-scout, social-scout]
limits:
  max_input_tokens_per_task: 75000
  max_output_tokens_per_task: 5000
  max_cost_usd_per_task: 0.01
provider:
  name: deep-provider
  model: model-1
screening:
  enabled: true
  agents: [{", ".join(screened)}]
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


def test_audit_resolves_lane_from_run_record_with_multiple_agents(tmp_path):
    completed_fixture(tmp_path, agent="literature-scout")

    report = audit_pilot(
        root=tmp_path,
        config_path=multi_lane_config(tmp_path, screened=(
            "strategy-scout", "literature-scout", "social-scout")),
        marker_path=tmp_path / "marker")

    assert report["status"] == "pass"
    assert not report["failures"]


def test_audit_rejects_unscreened_allowed_agent(tmp_path):
    completed_fixture(tmp_path)

    report = audit_pilot(
        root=tmp_path,
        config_path=multi_lane_config(
            tmp_path, screened=("strategy-scout",)),
        marker_path=tmp_path / "marker")

    assert report["status"] == "fail"
    assert any("allowed_agents_screened" in item
               for item in report["failures"])
