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
    ScreeningDecision,
    ScreeningLimits,
    WorkerLimits,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def setup_packet(root: Path, *, agent="strategy-scout", minutes=30,
                 evidence=None) -> tuple:
    prompt = root / ".claude" / "agents" / f"{agent}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("research only; never trade\n", encoding="utf-8")
    dispatches = root / "data/research_triage/dispatches"
    packet = dispatches / agent / "a1.json"
    packet.parent.mkdir(parents=True)
    body = {
        "id": "d1", "assignment_id": "a1", "source_item_id": "s1",
        "assigned_agent": agent, "priority": "high", "triage_score": 80,
        "research_budget_minutes": minutes,
        "created_at": "2026-08-03T11:00:00Z",
        "source_item": {"title": "private-looking source text marker"},
    }
    if evidence is not None:
        body["market_evidence"] = evidence
        body["completion_contract"] = {
            "supplied_evidence": sorted(evidence.get("facts") or {})}
    packet.write_text(json.dumps(body), encoding="utf-8")
    dispositions = root / "research/dispositions"
    dispositions.mkdir(parents=True)
    return dispatches, root / "data/research_execution", dispositions


class FakeProvider:
    name = "fake"
    model = "test-model"

    def __init__(self, output=None, usage=None):
        self.calls = 0
        self.last_request = None
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
        self.last_request = request
        assert request["safety"]["trading_execution_allowed"] is False
        assert timeout_seconds == 60
        return ProviderResult(self.name, self.model, self.output, self.usage)


def worker(root: Path, provider=None, *, execution_enabled=False,
           limits=None, screening_provider=None,
           screening_enabled=False, evidence=None,
           agent="strategy-scout") -> ResearchAgentWorker:
    dispatches, state, dispositions = setup_packet(
        root, agent=agent, evidence=evidence)
    kwargs = {}
    if screening_enabled:
        # The constructor refuses an allowed agent outside the screened set.
        kwargs["allowed_agents"] = (agent,)
        kwargs["screening_agents"] = (agent,)
    return ResearchAgentWorker(
        root=root, dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="test-worker",
        limits=limits or WorkerLimits(timeout_seconds=60),
        provider=provider, execution_enabled=execution_enabled,
        screening_provider=screening_provider,
        screening_limits=ScreeningLimits(timeout_seconds=30),
        screening_enabled=screening_enabled,
        clock=lambda: NOW, **kwargs)


class FakeScreeningProvider:
    name = "fake-screen"
    model = "screen-model"

    def __init__(self, decision="reject"):
        self.calls = 0
        self.last_request = None
        self.decision = decision

    def invoke(self, request, *, timeout_seconds):
        self.calls += 1
        self.last_request = request
        assert timeout_seconds == 30
        assert request["output_contract"]["advance_allowed"] is False
        return ProviderResult(self.name, self.model, {
            "decision": self.decision,
            "reason_codes": ["screened_reason"],
            "evidence_checked": ["dispatch_packet"],
            "research_minutes": 2,
            "mechanism": (
                "maker inventory is mispriced" if self.decision == "deep_research"
                else ""),
            "cheapest_decisive_test": (
                "compare fee-net markout" if self.decision == "deep_research"
                else ""),
            "confidence": 0.8,
            "recheck_after": None,
            "notes": "bounded screen",
        }, ProviderUsage(500, 100, .02))


class FailingProvider(FakeProvider):
    def invoke(self, request, *, timeout_seconds):
        self.calls += 1
        raise ResearchWorkerError("provider crashed before usage report")


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


def test_dry_run_targets_exact_assignment_instead_of_higher_score(tmp_path):
    provider = FakeProvider()
    runtime = worker(tmp_path, provider)
    dispatches = tmp_path / "data/research_triage/dispatches/strategy-scout"
    (dispatches / "a2.json").write_text(json.dumps({
        "id": "d2", "assignment_id": "a2", "source_item_id": "s2",
        "assigned_agent": "strategy-scout", "priority": "high",
        "triage_score": 99, "research_budget_minutes": 30,
        "created_at": "2026-08-03T11:00:00Z",
        "source_item": {"title": "higher-scored packet"},
    }), encoding="utf-8")

    result = runtime.run_once(assignment_id="a1")

    assert result["status"] == "dry_run"
    assert result["assignment_id"] == "a1"
    assert provider.calls == 0


def test_targeted_worker_fails_closed_when_assignment_is_missing(tmp_path):
    provider = FakeProvider()
    runtime = worker(tmp_path, provider, execution_enabled=True)

    result = runtime.run_once(execute=True, assignment_id="missing")

    assert result["status"] == "blocked"
    assert result["error"] == "target assignment is not available: missing"
    assert provider.calls == 0
    assert not list((
        tmp_path / "data/research_execution/claims").glob("*/*.json"))


def test_execute_requires_independent_runtime_gate_before_claim(tmp_path):
    runtime = worker(tmp_path, FakeProvider(), execution_enabled=False)

    with pytest.raises(ResearchWorkerError, match="execution_enabled"):
        runtime.run_once(execute=True)

    assert not list((tmp_path / "data/research_execution/claims").glob("*/*.json"))


def test_valid_provider_result_completes_claim_and_records_usage(tmp_path):
    provider = FakeProvider()
    runtime = worker(tmp_path, provider, execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert provider.last_request["claim_context"] == {
        "claimed_at": "2026-08-03T12:00:00Z",
        "decided_at_requirement": (
            "disposition.decided_at must be an ISO-8601 UTC timestamp "
            "greater than or equal to claimed_at"
        ),
    }

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


def test_screening_rejection_completes_without_deep_research(tmp_path):
    deep = FakeProvider()
    screen = FakeScreeningProvider("reject")
    runtime = worker(
        tmp_path, deep, execution_enabled=True,
        screening_provider=screen, screening_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "completed"
    assert screen.calls == 1
    assert deep.calls == 0
    assert result["run"]["terminal_stage"] == "screening"
    assert result["run"]["usage"] == {
        "input_tokens": 500, "output_tokens": 100, "cost_usd": .02}
    assert result["daily_usage"]["attempts"] == 1
    disposition = json.loads(
        (tmp_path / "research/dispositions/a1.json").read_text())
    assert disposition["decision"] == "reject"
    screening = json.loads((
        tmp_path / "data/research_execution/screenings/strategy-scout/a1.json"
    ).read_text())
    assert screening["safety"]["authorizes_advancement"] is False


def test_screening_survivor_invokes_deep_research_and_aggregates_usage(tmp_path):
    deep = FakeProvider()
    screen = FakeScreeningProvider("deep_research")
    runtime = worker(
        tmp_path, deep, execution_enabled=True,
        screening_provider=screen, screening_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "completed"
    assert screen.calls == deep.calls == 1
    assert [item["phase"] for item in result["run"]["invocations"]] == [
        "screening", "deep_research"]
    assert result["run"]["usage"] == {
        "input_tokens": 1700, "output_tokens": 400, "cost_usd": .1}
    assert result["daily_usage"]["attempts"] == 2
    archives = list((tmp_path / "data/research_execution/claim_archive"
                     / "strategy-scout").glob("*.json"))
    archived = json.loads(archives[0].read_text())
    assert [item["phase"] for item in archived["model_invocations"]] == [
        "screening", "deep_research"]


def test_screening_guard_refuses_unscreened_allowed_agent(tmp_path):
    dispatches, state, dispositions = setup_packet(tmp_path)
    with pytest.raises(ValueError, match="bypass the screen"):
        ResearchAgentWorker(
            root=tmp_path, dispatches_dir=dispatches, state_dir=state,
            dispositions_dir=dispositions, worker_id="test-worker",
            limits=WorkerLimits(timeout_seconds=60),
            screening_enabled=True,
            allowed_agents=("strategy-scout", "literature-scout"),
            clock=lambda: NOW)


def test_screening_agents_default_matches_pinned_pilot(tmp_path):
    dispatches, state, dispositions = setup_packet(tmp_path)
    runtime = ResearchAgentWorker(
        root=tmp_path, dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="test-worker",
        limits=WorkerLimits(timeout_seconds=60),
        screening_enabled=True, allowed_agents=("strategy-scout",),
        clock=lambda: NOW)
    assert runtime.screening_agents == ("strategy-scout",)


def test_screening_covers_other_scout_lanes_when_configured(tmp_path):
    deep = FakeProvider()
    screen = FakeScreeningProvider("reject")
    runtime = worker(
        tmp_path, deep, execution_enabled=True,
        screening_provider=screen, screening_enabled=True,
        agent="literature-scout")

    result = runtime.run_once(execute=True)

    assert result["status"] == "completed"
    assert screen.calls == 1
    assert deep.calls == 0
    assert result["run"]["terminal_stage"] == "screening"
    # scout_rejection exists only for strategy-scout; every other lane's
    # screen-reject must carry the disposition itself.
    artifact = json.loads((
        tmp_path / "data/research_execution/artifacts/literature-scout/a1.json"
    ).read_text())
    assert artifact["artifact_type"] == "research_disposition"
    disposition = json.loads(
        (tmp_path / "research/dispositions/a1.json").read_text())
    assert disposition["decision"] == "reject"
    screening = json.loads((
        tmp_path / "data/research_execution/screenings/literature-scout/a1.json"
    ).read_text())
    assert screening["safety"]["authorizes_advancement"] is False


def test_screening_reserves_two_daily_attempts_before_claim(tmp_path):
    runtime = worker(
        tmp_path, FakeProvider(), execution_enabled=True,
        screening_provider=FakeScreeningProvider(), screening_enabled=True,
        limits=WorkerLimits(timeout_seconds=60, max_attempts_per_day=1))

    result = runtime.run_once(execute=True)

    assert result["status"] == "blocked"
    assert "model-attempt limit" in result["error"]
    assert not list((tmp_path / "data/research_execution/claims").glob("*/*.json"))


def test_failed_deep_call_still_counts_as_an_attempt(tmp_path):
    runtime = worker(
        tmp_path, FailingProvider(), execution_enabled=True,
        screening_provider=FakeScreeningProvider("deep_research"),
        screening_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "failed"
    assert result["daily_usage"]["attempts"] == 2
    assert result["run"]["invocations"][1]["usage_measured"] is False


def test_deferred_screen_requires_timezone_aware_iso_recheck():
    with pytest.raises(ValueError, match="ISO-8601"):
        ScreeningDecision(
            decision="defer", reason_codes=["missing_terms"],
            evidence_checked=["filing"], research_minutes=2,
            recheck_after="after terms are published")
    with pytest.raises(ValueError, match="timezone"):
        ScreeningDecision(
            decision="defer", reason_codes=["missing_terms"],
            evidence_checked=["filing"], research_minutes=2,
            recheck_after="2026-08-14T15:00:00")


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


def test_predated_disposition_is_rejected_before_artifact_write(tmp_path):
    output = FakeProvider().output | {
        "disposition": FakeProvider().output["disposition"] | {
            "decided_at": "2026-08-03T11:59:59Z",
        },
    }
    runtime = worker(
        tmp_path, FakeProvider(output=output), execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "failed"
    assert "predates the claim" in result["error"]
    artifact = (tmp_path / "data/research_execution/artifacts/strategy-scout/a1.json")
    assert not artifact.exists()


def test_rejected_provider_output_is_preserved_for_diagnosis(tmp_path):
    base = FakeProvider().output
    output = base | {
        "artifact_type": "research_disposition",
        # Valid disposition, but a structured field differs from the completion
        # one — the exact failure the rejected-artifact record exists to
        # diagnose. (`notes` drift alone no longer rejects.)
        "artifact": base["disposition"] | {"reason_codes": ["drifted_code"]},
    }
    runtime = worker(tmp_path, FakeProvider(output=output),
                     execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "failed"
    assert "differs from completion" in result["error"]
    rejected_dir = (tmp_path
                    / "data/research_execution/rejected_artifacts/strategy-scout")
    files = list(rejected_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].name == f"a1--{result['run']['claim_id']}.json"
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["output"] == output
    assert record["output_truncated"] is False
    assert "differs from completion" in record["error"]
    assert record["safety"] == {"authorizes_execution": False,
                                "authorizes_advancement": False}
    # The rejected copy must not double as a delivered artifact.
    artifact = (tmp_path
                / "data/research_execution/artifacts/strategy-scout/a1.json")
    assert not artifact.exists()


def test_paraphrased_notes_do_not_reject_the_disposition_artifact(tmp_path):
    base = FakeProvider().output
    disposition = base["disposition"] | {
        "notes": "Observation: the long-form findings live here.",
    }
    output = base | {
        "disposition": disposition,
        "artifact_type": "research_disposition",
        # Models paraphrase free text between their two copies; only the
        # structured fields are held to byte-identity.
        "artifact": disposition | {"notes": "One-line summary."},
    }
    runtime = worker(tmp_path, FakeProvider(output=output),
                     execution_enabled=True)

    result = runtime.run_once(execute=True)

    assert result["status"] == "completed"
    artifact = (tmp_path
                / "data/research_execution/artifacts/strategy-scout/a1.json")
    record = json.loads(artifact.read_text(encoding="utf-8"))
    # The completion's notes are canonical in the stored artifact.
    assert record["artifact"]["notes"] == disposition["notes"]
    rejected_dir = (tmp_path
                    / "data/research_execution/rejected_artifacts/strategy-scout")
    assert not list(rejected_dir.glob("*.json"))


def test_oversized_rejected_output_is_truncated_to_byte_limit(tmp_path):
    limits = WorkerLimits(timeout_seconds=60, max_output_bytes=64)
    runtime = worker(tmp_path, FakeProvider(), execution_enabled=True,
                     limits=limits)

    result = runtime.run_once(execute=True)

    assert result["status"] == "failed"
    assert "exceeded byte limit" in result["error"]
    rejected_dir = (tmp_path
                    / "data/research_execution/rejected_artifacts/strategy-scout")
    files = list(rejected_dir.glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["output_truncated"] is True
    assert "output" not in record
    assert len(record["output_preview"].encode("utf-8")) <= 64 + 3
    assert record["output_bytes"] > 64


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


def test_command_provider_surfaces_only_redacted_codex_diagnostic(tmp_path):
    code = (
        "import sys; print(\"codex research provider error: model rejected "
        "Bearer secret-token\", file=sys.stderr); raise SystemExit(2)")
    provider = CommandProvider(
        argv=[sys.executable, "-c", code], name="local", model="fixture",
        cwd=tmp_path)

    with pytest.raises(ResearchWorkerError) as caught:
        provider.invoke({}, timeout_seconds=5)

    message = str(caught.value)
    assert "model rejected" in message
    assert "secret-token" not in message
    assert "[REDACTED]" in message


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


def test_screening_rules_route_fetchable_unknowns_to_deep_research(tmp_path):
    screen = FakeScreeningProvider("reject")
    runtime = worker(
        tmp_path, FakeProvider(), execution_enabled=True,
        screening_provider=screen, screening_enabled=True)

    runtime.run_once(execute=True)

    request = screen.last_request
    rules = " ".join(request["screening_rules"])
    # Defer is for a fact that does not exist yet, not one behind a fetch.
    # Screening has no network; deep research does, so deferring something
    # fetchable spends the assignment and resolves nothing.
    assert "DOES NOT EXIST YET" in rules
    assert "FETCHABLE NOW" in rules
    assert "that is deep_research, " in rules and "NOT defer" in rules
    # And the screen is pointed at the evidence it was actually handed.
    assert "dispatch_packet.market_evidence" in rules
    assert request["output_contract"]["decisions"] == [
        "reject", "defer", "deep_research"]
    assert request["output_contract"]["advance_allowed"] is False


def test_screening_record_captures_what_evidence_was_in_hand(tmp_path):
    runtime = worker(
        tmp_path, FakeProvider(), execution_enabled=True,
        screening_provider=FakeScreeningProvider("reject"),
        screening_enabled=True, evidence={
            "status": "measured", "source": "cftc_filing_documents",
            "measured_at": "2026-08-03T11:30:00Z",
            "facts": {"documents": [{"url": "https://cftc.test/rule.pdf"}]},
        })

    runtime.run_once(execute=True)

    screening = json.loads((
        tmp_path / "data/research_execution/screenings/strategy-scout/a1.json"
    ).read_text())
    context = screening["evidence_context"]
    assert context["evidence_status"] == "measured"
    assert context["evidence_source"] == "cftc_filing_documents"
    assert context["supplied_evidence"] == ["documents"]
    assert context["decided_with_measured_evidence"] is True


def test_screening_record_marks_an_evidence_free_decision(tmp_path):
    runtime = worker(
        tmp_path, FakeProvider(), execution_enabled=True,
        screening_provider=FakeScreeningProvider("reject"),
        screening_enabled=True)

    runtime.run_once(execute=True)

    context = json.loads((
        tmp_path / "data/research_execution/screenings/strategy-scout/a1.json"
    ).read_text())["evidence_context"]
    assert context["evidence_status"] == "none"
    assert context["supplied_evidence"] == []
    assert context["decided_with_measured_evidence"] is False
