from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.research_agent_worker import (
    CommandProvider,
    ProviderResult,
    ProviderUsage,
    ResearchAgentWorker,
    ResearchWorkerError,
    WorkerLimits,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def setup_packet(root: Path, *, agent="strategy-scout", minutes=30) -> tuple:
    prompt = root / ".claude" / "agents" / f"{agent}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("research only; never trade\n", encoding="utf-8")
    dispatches = root / "data/research_triage/dispatches"
    packet = dispatches / agent / "a1.json"
    packet.parent.mkdir(parents=True)
    packet.write_text(json.dumps({
        "id": "d1", "assignment_id": "a1", "source_item_id": "s1",
        "assigned_agent": agent, "priority": "high", "triage_score": 80,
        "research_budget_minutes": minutes,
        "created_at": "2026-08-03T11:00:00Z",
        "source_item": {"title": "private-looking source text marker"},
    }), encoding="utf-8")
    dispositions = root / "research/dispositions"
    dispositions.mkdir(parents=True)
    return dispatches, root / "data/research_execution", dispositions


class FakeProvider:
    name = "fake"
    model = "test-model"

    def __init__(self, output=None, usage=None):
        self.calls = 0
        self.output = output or {
            "disposition": {
                "assignment_id": "a1", "source_item_id": "s1",
                "decided_at": "2026-08-03T12:00:00Z",
                "decision": "reject", "reason_codes": ["no_mechanism"],
                "evidence_checked": ["source"], "research_minutes": 12,
            },
            "artifact_type": "scout_rejection",
            "artifact": {
                "assignment_id": "a1", "reason_codes": ["no_mechanism"],
                "evidence_checked": ["source"],
            },
        }
        self.usage = usage or ProviderUsage(1200, 300, .08)

    def invoke(self, request, *, timeout_seconds):
        self.calls += 1
        assert request["safety"]["trading_execution_allowed"] is False
        assert timeout_seconds == 60
        return ProviderResult(self.name, self.model, self.output, self.usage)


def worker(root: Path, provider=None, *, execution_enabled=False,
           limits=None) -> ResearchAgentWorker:
    dispatches, state, dispositions = setup_packet(root)
    return ResearchAgentWorker(
        root=root, dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="test-worker",
        limits=limits or WorkerLimits(timeout_seconds=60),
        provider=provider, execution_enabled=execution_enabled,
        clock=lambda: NOW)


def test_dry_run_plans_without_claiming_or_invoking(tmp_path):
    provider = FakeProvider()
    runtime = worker(tmp_path, provider)

    result = runtime.run_once()

    assert result["status"] == "dry_run"
    assert result["plan"]["assignment_id"] == "a1"
    assert result["plan"]["execution_enabled"] is False
    assert result["safety"]["model_invoked"] is False
    assert provider.calls == 0
    assert not list((tmp_path / "data/research_execution/claims").glob("*/*.json"))
    status_text = (tmp_path / "data/research_execution/worker_status.json").read_text()
    assert "private-looking source text marker" not in status_text
    assert "research only; never trade" not in status_text
    assert "request_sha256" in status_text


def test_execute_requires_independent_runtime_gate_before_claim(tmp_path):
    runtime = worker(tmp_path, FakeProvider(), execution_enabled=False)

    with pytest.raises(ResearchWorkerError, match="execution_enabled"):
        runtime.run_once(execute=True)

    assert not list((tmp_path / "data/research_execution/claims").glob("*/*.json"))


def test_valid_provider_result_completes_claim_and_records_usage(tmp_path):
    provider = FakeProvider()
    runtime = worker(tmp_path, provider, execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "completed"
    assert provider.calls == 1
    assert (tmp_path / "research/dispositions/a1.json").exists()
    archives = list((tmp_path / "data/research_execution/claim_archive"
                     / "strategy-scout").glob("*.json"))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text())
    assert archived["model_invocation_tracked"] is True
    assert archived["provider"] == "fake"
    artifacts = list((tmp_path / "data/research_execution/artifacts"
                      / "strategy-scout").glob("*.json"))
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text())["safety"][
        "authorizes_execution"] is False
    assert result["daily_usage"]["cost_usd"] == pytest.approx(.08)


def test_budget_breach_releases_claim_and_never_completes(tmp_path):
    provider = FakeProvider(usage=ProviderUsage(1200, 8001, .08))
    runtime = worker(tmp_path, provider, execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "failed"
    assert "output-token limit" in result["error"]
    assert not (tmp_path / "research/dispositions/a1.json").exists()
    released = list((tmp_path / "data/research_execution/claim_released"
                     / "strategy-scout").glob("*.json"))
    assert len(released) == 1
    assert json.loads(released[0].read_text())["model_invocation_tracked"] is True
    assert result["daily_usage"]["cost_usd"] == pytest.approx(.08)


def test_daily_cost_reservation_blocks_before_claim(tmp_path):
    runtime = worker(tmp_path, FakeProvider(), execution_enabled=True,
                     limits=WorkerLimits(timeout_seconds=60,
                                         max_cost_usd_per_task=2,
                                         max_cost_usd_per_day=10))
    run = tmp_path / "data/research_execution/runs/2026-08-03/old.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"usage": {"cost_usd": 9}}))

    result = runtime.run_once(execute=True)

    assert result["status"] == "blocked"
    assert "daily cost reservation" in result["error"]
    assert not list((tmp_path / "data/research_execution/claims").glob("*/*.json"))


def test_daily_attempt_limit_blocks_before_claim(tmp_path):
    runtime = worker(
        tmp_path, FakeProvider(), execution_enabled=True,
        limits=WorkerLimits(timeout_seconds=60, max_attempts_per_day=1))
    run = tmp_path / "data/research_execution/runs/2026-08-03/old.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"usage": {"cost_usd": 0}}))

    result = runtime.run_once(execute=True)

    assert result["status"] == "blocked"
    assert "daily model-attempt limit" in result["error"]
    assert not list((tmp_path / "data/research_execution/claims").glob("*/*.json"))


def test_wrong_artifact_for_agent_is_released(tmp_path):
    output = FakeProvider().output | {
        "artifact_type": "opportunity_card",
        "artifact": {},
    }
    runtime = worker(tmp_path, FakeProvider(output=output), execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "failed"
    assert "invalid for strategy-scout" not in result["error"]
    assert "OpportunityCard" in result["error"] or "missing" in result["error"]


def test_command_provider_passes_only_allowlisted_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "allowed")
    monkeypatch.setenv("KALSHI_API_KEY", "must-not-leak")
    code = (
        "import json,os,sys; json.load(sys.stdin); "
        "print(json.dumps({'output': {'allowed': os.getenv('MODEL_API_KEY'), "
        "'betting_secret': os.getenv('KALSHI_API_KEY')}, "
        "'usage': {'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 0}}))"
    )
    provider = CommandProvider(
        argv=[sys.executable, "-c", code], name="local", model="fixture",
        cwd=tmp_path, pass_env=["MODEL_API_KEY"])

    result = provider.invoke({"request": True}, timeout_seconds=5)

    assert result.output == {"allowed": "allowed", "betting_secret": None}


def test_command_provider_does_not_persist_stderr_text(tmp_path):
    code = "import sys; print('secret-value', file=sys.stderr); raise SystemExit(7)"
    provider = CommandProvider(
        argv=[sys.executable, "-c", code], name="local", model="fixture",
        cwd=tmp_path)

    with pytest.raises(ResearchWorkerError) as caught:
        provider.invoke({}, timeout_seconds=5)

    assert "secret-value" not in str(caught.value)
    assert "stderr_sha256=" in str(caught.value)


def test_worker_module_has_no_trading_imports():
    source = Path("src/research_agent_worker.py").read_text()
    for forbidden in ("kalshi_private", "multi_executor", "place_order",
                      "create_order", "submit_order"):
        assert forbidden not in source


def test_phase1_service_is_network_denied_and_cannot_execute():
    service = Path("scripts/systemd/research-agent-worker.service").read_text()
    timer = Path("scripts/systemd/research-agent-worker.timer").read_text()
    config = Path("config/research_agent_runtime.yaml").read_text()

    assert "RestrictAddressFamilies=AF_UNIX\n" in service
    assert "--execute" not in service
    assert "ReadWritePaths=/opt/betting-pod-shop/data/research_execution" in service
    assert "OnCalendar=hourly" in timer
    assert "mode: dry_run" in config
    assert "type: none" in config
