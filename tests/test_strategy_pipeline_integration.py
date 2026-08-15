"""End-to-end factory handoff, including the research-advance bridge."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.research_agent_worker import (
    ProviderResult, ProviderUsage, ResearchAgentWorker, WorkerLimits,
)
from src.strategy_agent_runtime import StrategyAgentRuntime


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class OpportunityProvider:
    name = "fixture"
    model = "fixture-model"
    billing_mode = "test"

    def invoke(self, request, *, timeout_seconds):
        card = {
            "id": "op_fast_001",
            "created_at": "2026-08-15T12:00:00Z",
            "source_agent": "scout",
            "market_family": "KXTEST",
            "thesis": "A falsifiable structural edge exists.",
            "edge_source": ["market_structure"],
            "external_data_needed": [],
            "confidence": 0.4,
            "urgency": "medium",
        }
        return ProviderResult(self.name, self.model, {
            "disposition": {
                "assignment_id": "assignment_fast",
                "source_item_id": "source_fast",
                "decided_at": "2026-08-15T12:00:00Z",
                "decision": "advance",
                "reason_codes": ["falsifiable_mechanism"],
                "evidence_checked": ["public_terms"],
                "research_minutes": 10,
                "opportunity_id": card["id"],
            },
            "artifact_type": "opportunity_card",
            "artifact": card,
        }, ProviderUsage(100, 50, 0))


def test_research_advance_hands_off_through_monitoring_without_live_promotion(
        tmp_path):
    prompt = tmp_path / ".claude/agents/strategy-scout.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("research only", encoding="utf-8")
    dispatch = tmp_path / "data/research_triage/dispatches/strategy-scout/a.json"
    write(dispatch, {
        "id": "dispatch_fast", "assignment_id": "assignment_fast",
        "source_item_id": "source_fast", "assigned_agent": "strategy-scout",
        "priority": "high", "triage_score": 99,
        "research_budget_minutes": 30,
        "created_at": "2026-08-15T11:00:00Z",
    })
    dispositions = tmp_path / "research/dispositions"
    dispositions.mkdir(parents=True)
    research = ResearchAgentWorker(
        root=tmp_path,
        dispatches_dir=tmp_path / "data/research_triage/dispatches",
        state_dir=tmp_path / "data/research_execution",
        dispositions_dir=dispositions,
        worker_id="research", limits=WorkerLimits(timeout_seconds=60),
        provider=OpportunityProvider(), execution_enabled=True,
        allowed_agents=("strategy-scout",), clock=lambda: NOW)

    assert research.run_once(execute=True)["status"] == "completed"
    queue = tmp_path / "data/strategy_agents/queue"
    assert len(list((queue / "scout").glob("*.json"))) == 1

    registry = tmp_path / "data/strategy_agents/registry.json"
    tasks = tmp_path / "data/strategy_agents/tasks"
    runtime = StrategyAgentRuntime(
        registry_path=registry, queue_dir=queue,
        processed_dir=tmp_path / "data/strategy_agents/processed",
        heartbeat_path=tmp_path / "data/strategy_agents/heartbeat.jsonl",
        task_dir=tasks)
    assert runtime.run_once().succeeded == 1
    assert len(list((tasks / "spec").glob("*.json"))) == 1

    write(queue / "spec/001.json", {
        "type": "spec", "strategy_id": "op_fast_001", "payload": {
            "id": "strat_fast_001", "opportunity_id": "op_fast_001",
            "name": "fast chain", "version": "1", "state": "spec",
            "entry_logic": {"type": "threshold"},
            "exit_logic": {"type": "time"},
            "fair_value_model": {"type": "fixture"},
            "fee_model": {"venue": "kalshi"},
            "risk_limits": {"max_usd_at_risk": 10},
            "null_hypothesis": "no edge", "alternative_hypothesis": "edge",
        }})
    assert runtime.run_once().succeeded == 1
    assert len(list((tasks / "integrity").glob("*.json"))) == 1

    provenance = {
        "created_by": "fixture", "code_commit": "abc",
        "artifact_path": "research/fixture.json",
    }
    write(queue / "integrity/002.json", {
        "type": "integrity", "strategy_id": "op_fast_001", "payload": {
            "strategy_id": "strat_fast_001",
            "checked_at": "2026-08-15T12:10:00Z", "status": "pass",
            "contract_terms": {"passed": True},
            "settlement_mapping": {"passed": True},
            "fee_mapping": {"passed": True},
            "api_field_reliability": {"passed": True},
            "external_schedule_dependency": {"required": False},
            "blocking_issues": [], "provenance": provenance,
        }})
    assert runtime.run_once().succeeded == 1
    assert len(list((tasks / "validation").glob("*.json"))) == 1

    write(queue / "validation/003.json", {
        "type": "validation", "strategy_id": "op_fast_001", "payload": {
            "strategy_id": "strat_fast_001", "run_id": "run_fast",
            "checked_at": "2026-08-15T12:20:00Z", "status": "pass",
            "sample_size": {"events": 20, "trades": 100},
            "performance": {"net_edge": 0.02, "clv": 0.01},
            "uncertainty": {"ci_lower": 0.005, "ci_upper": 0.03},
            "stress_tests": {}, "capacity": {}, "go_no_go": "go",
            "provenance": provenance | {
                "dataset_hash": "data", "gate_path": "gate.md",
                "gate_hash": "gate"},
        }})
    assert runtime.run_once().succeeded == 1
    assert len(list((tasks / "promotion").glob("*.json"))) == 1

    write(queue / "promotion/004.json", {
        "type": "promotion", "strategy_id": "op_fast_001", "payload": {
            "strategy_id": "strat_fast_001",
            "decision_at": "2026-08-15T12:30:00Z",
            "from_state": "validated", "to_state": "paper_live",
            "decision": "promote", "rationale": ["gate passed"],
            "required_conditions": [], "owner_agent": "promotion",
            "review_window_days": 7,
        }})
    assert runtime.run_once().succeeded == 1
    assert len(list((tasks / "monitoring").glob("*.json"))) == 1

    write(queue / "monitoring/005.json", {
        "type": "monitoring", "strategy_id": "op_fast_001", "payload": {
            "strategy_id": "strat_fast_001",
            "observed_at": "2026-08-15T13:00:00Z", "state": "paper_live",
            "window": "24h", "status": "ok", "realized_edge": 0.02,
            "clv": 0.01, "pnl_usd": 5, "fill_quality": {"fills": 10},
            "drift_signals": [], "guardrail_breaches": [],
            "recommended_state": "live_small",
        }})
    assert runtime.run_once().succeeded == 1
    final = json.loads(registry.read_text())["records"]["op_fast_001"]
    assert final["state"] == "paper_live"
    assert final["events"][-1]["kind"] == "promotion_requested"
    assert len(list((tasks / "promotion").glob("*.json"))) == 2
