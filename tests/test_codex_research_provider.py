from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.codex_research_provider import CodexProviderError, invoke_codex


def test_codex_adapter_is_ephemeral_read_only_and_measures_usage(
        tmp_path, monkeypatch):
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    output = {
        "disposition": {"assignment_id": "a1"},
        "artifact_type": "scout_rejection",
        "artifact": {"reason_codes": ["no_edge"]},
    }
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["input"] = kwargs["input"]
        final_path = Path(argv[argv.index("--output-last-message") + 1])
        final_path.write_text(json.dumps(output))
        events = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 1200, "output_tokens": 200,
                "reasoning_output_tokens": 50}}),
        ])
        return subprocess.CompletedProcess(argv, 0, events, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = invoke_codex(
        {"assignment_id": "a1"}, codex="/usr/local/bin/codex",
        model="gpt-test", schema=schema, workspace=tmp_path / "workspace",
        timeout_seconds=30)

    argv = captured["argv"]
    assert ["--ask-for-approval", "never"] == argv[1:3]
    assert "--search" in argv
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert json.loads(captured["input"])["assignment_id"] == "a1"
    assert result["output"] == output
    assert result["usage"] == {
        "input_tokens": 1200, "output_tokens": 250, "cost_usd": 0.0}
    assert not list((tmp_path / "workspace").glob("codex-research-*.json"))


def test_codex_adapter_rejects_local_command_execution(tmp_path, monkeypatch):
    schema = tmp_path / "schema.json"
    schema.write_text("{}")

    def fake_run(argv, **kwargs):
        final_path = Path(argv[argv.index("--output-last-message") + 1])
        final_path.write_text("{}")
        events = "\n".join([
            json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "cat .env"}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 1, "output_tokens": 1}}),
        ])
        return subprocess.CompletedProcess(argv, 0, events, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CodexProviderError, match="local command"):
        invoke_codex(
            {}, codex="codex", model="gpt-test", schema=schema,
            workspace=tmp_path / "workspace", timeout_seconds=30)


def test_codex_pilot_unit_is_manual_only_and_masks_betting_secrets():
    service = Path(
        "scripts/systemd/research-agent-codex-pilot.service").read_text()
    config = Path(
        "config/research_agent_runtime_codex_pilot.yaml").read_text()
    timers = list(Path("scripts/systemd").glob("research-agent-codex-pilot.timer"))

    assert not timers
    assert "ConditionPathExists=/var/lib/research-codex/pilot-enabled" in service
    assert "ConditionPathExists=/var/lib/research-codex/auth.json" in service
    assert "--execute" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in service
    assert "InaccessiblePaths=-/opt/betting-pod-shop/.env" in service
    assert "InaccessiblePaths=-/opt/betting-pod-shop/kalshi_private_key.pem" in service
    assert "max_attempts_per_day: 1" in config
    assert "billing_mode: chatgpt_subscription" in config
    assert "KALSHI" not in config
    assert "PROPHETX" not in config
    assert "X_BEARER_TOKEN" not in config


def test_codex_pilot_setup_keeps_each_run_explicit_and_self_disabling():
    script = Path("scripts/setup_codex_research_pilot.sh").read_text()

    assert "ACTION=${1:-check}" in script
    assert '[[ -f "$STATE/auth.json" ]]' in script
    assert 'install -o bettingbot -g bettingbot -m 0600 /dev/null "$STATE/pilot-enabled"' in script
    assert script.count('rm -f "$STATE/pilot-enabled"') == 2
    assert "systemctl enable" not in script
    assert "systemctl start \"$UNIT\"" in script
