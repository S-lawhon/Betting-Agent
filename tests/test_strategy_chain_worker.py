from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scripts.run_strategy_chain_worker as chain_worker
from src.research_agent_worker import ProviderResult, ProviderUsage
from src.strategy_chain import enqueue_next_task
from src.strategy_orchestration import OpportunityCard, StrategyRegistry


class FakeCommandProvider:
    def __init__(self, **kwargs):
        pass

    def invoke(self, request, *, timeout_seconds):
        assert request["safety"]["unattended_live_promotion_allowed"] is False
        return ProviderResult("fake", "fixture", {
            "id": "strat_1", "opportunity_id": "op_1",
            "name": "fixture", "version": "1", "state": "spec",
            "entry_logic": {"type": "threshold"},
            "exit_logic": {"type": "time"},
            "fair_value_model": {"type": "fixture"},
            "fee_model": {"venue": "kalshi"},
            "risk_limits": {"max_usd_at_risk": 10},
            "null_hypothesis": "none", "alternative_hypothesis": "edge",
        }, ProviderUsage(10, 10, 0))


def write_config(path: Path) -> None:
    path.write_text("""schema_version: 1
enabled: true
mode: execute
worker_id: chain-test
max_attempts_per_utc_day: 4
timeout_seconds: 60
provider:
  type: command
  name: fake
  model: fixture
  argv: [fake]
  pass_env: []
safety:
  trading_execution_allowed: false
  deployment_allowed: false
  unattended_live_promotion_allowed: false
""", encoding="utf-8")


def test_chain_worker_submits_typed_task_to_registry_inbox(tmp_path, monkeypatch):
    prompt = tmp_path / ".Codex/agents/strategy-spec.toml"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("spec only", encoding="utf-8")
    registry = StrategyRegistry()
    registry.register_opportunity(OpportunityCard(
        id="op_1", created_at="2026-08-15T00:00:00Z",
        source_agent="scout", market_family="KXTEST", thesis="edge",
        edge_source=["structure"], external_data_needed=[], confidence=.2,
        urgency="medium"))
    registry_path = tmp_path / "data/strategy_agents/registry.json"
    registry.dump(registry_path)
    enqueue_next_task(
        task_dir=tmp_path / "data/strategy_agents/tasks",
        record=registry.get("op_1"), trigger_type="opportunity")
    config = tmp_path / "chain.yaml"
    write_config(config)
    monkeypatch.setattr(chain_worker, "CommandProvider", FakeCommandProvider)

    assert chain_worker.main([
        "--root", str(tmp_path), "--config", str(config), "--execute",
    ]) == 0

    requests = list((tmp_path / "data/strategy_agents/queue/spec").glob("*.json"))
    assert len(requests) == 1
    payload = json.loads(requests[0].read_text())
    assert payload["type"] == "spec"
    assert payload["payload"]["id"] == "strat_1"
    assert not list((tmp_path / "data/strategy_agents/tasks/spec").glob("*.json"))
    assert len(list((tmp_path / "data/strategy_agents/task_submitted/spec").glob(
        "*.json"))) == 1


def test_chain_failures_back_off_then_dead_letter(tmp_path):
    task = tmp_path / "data/strategy_agents/tasks/spec/task.json"
    task.parent.mkdir(parents=True)
    task.write_text("{}", encoding="utf-8")
    state = tmp_path / "data/strategy_agents"
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    for number in range(1, 4):
        retry = chain_worker._record_failure(
            state, task, ValueError("bad artifact"), now)
        assert retry["failure_count"] == number
    assert retry["dead_lettered"] is True
    assert chain_worker._retry_blocked(state, task, now) is True
