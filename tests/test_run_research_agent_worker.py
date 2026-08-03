from __future__ import annotations

import json
from pathlib import Path

from scripts.run_research_agent_worker import main


def setup_runtime(tmp_path: Path):
    prompt = tmp_path / ".claude/agents/strategy-scout.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("research only")
    dispatch = tmp_path / "dispatches/strategy-scout/a1.json"
    dispatch.parent.mkdir(parents=True)
    dispatch.write_text(json.dumps({
        "id": "d1", "assignment_id": "a1", "source_item_id": "s1",
        "assigned_agent": "strategy-scout", "priority": "high",
        "triage_score": 80, "research_budget_minutes": 30,
        "created_at": "2026-08-03T11:00:00Z",
    }))
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    config = tmp_path / "runtime.yaml"
    config.write_text("""schema_version: 1
enabled: true
mode: dry_run
worker_id: test-worker
allowed_agents: [strategy-scout]
limits:
  max_minutes_per_task: 45
  timeout_seconds: 60
  max_input_tokens_per_task: 1000
  max_output_tokens_per_task: 500
  max_cost_usd_per_task: 1
  max_cost_usd_per_day: 5
  max_output_bytes: 10000
provider:
  type: none
""")
    common = [
        "--config", str(config), "--root", str(tmp_path),
        "--dispatches-dir", str(tmp_path / "dispatches"),
        "--state-dir", str(tmp_path / "execution"),
        "--dispositions-dir", str(dispositions),
    ]
    return common, config


def test_cli_defaults_to_persistent_dry_run(tmp_path, capsys):
    common, _ = setup_runtime(tmp_path)

    assert main(common) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "dry_run"
    assert result["safety"]["model_invoked"] is False
    assert (tmp_path / "execution/worker_status.json").exists()
    assert not list((tmp_path / "execution/claims").glob("*/*.json"))


def test_cli_execute_is_blocked_by_dry_run_config(tmp_path, capsys):
    common, _ = setup_runtime(tmp_path)

    assert main(common + ["--execute"]) == 2

    assert "requires config mode=execute" in capsys.readouterr().err
    assert not (tmp_path / "execution").exists()


def test_cli_no_write_status_is_a_pure_preview(tmp_path, capsys):
    common, _ = setup_runtime(tmp_path)

    assert main(common + ["--no-write-status"]) == 0
    capsys.readouterr()

    assert not (tmp_path / "execution").exists()
