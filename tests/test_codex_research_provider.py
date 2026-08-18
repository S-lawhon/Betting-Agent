from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.codex_research_provider import (
    CodexProviderError, _safe_stderr, invoke_codex,
)
from scripts.run_codex_research_week import action_for_day


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


def test_codex_adapter_can_disable_search_and_lower_screening_reasoning(
        tmp_path, monkeypatch):
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        final_path = Path(argv[argv.index("--output-last-message") + 1])
        final_path.write_text("{}")
        events = json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        })
        return subprocess.CompletedProcess(argv, 0, events, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    invoke_codex(
        {"mode": "research_calibration_screening_only"},
        codex="codex", model="gpt-test", schema=schema,
        workspace=tmp_path / "workspace", timeout_seconds=30,
        allow_search=False, reasoning_effort="low")

    argv = captured["argv"]
    assert "--search" not in argv
    assert 'model_reasoning_effort="low"' in argv


def test_stderr_diagnostic_is_bounded_and_redacts_credentials():
    diagnostic = _safe_stderr(
        "context\nrequest failed Authorization: Bearer secret-value "
        "access_token=also-secret sk-private\n")
    assert "secret-value" not in diagnostic
    assert "also-secret" not in diagnostic
    assert "sk-private" not in diagnostic
    assert "[REDACTED]" in diagnostic
    assert len(diagnostic) <= 500


def test_stderr_diagnostic_prefers_error_over_trailing_progress():
    diagnostic = _safe_stderr(
        "error: invalid value for --model\nReading additional input from stdin...\n")
    assert diagnostic == "error: invalid value for --model"


def test_stderr_diagnostic_ignores_stdin_progress_when_error_is_unlabelled():
    diagnostic = _safe_stderr(
        "unexpected argument '--ignore-rules' found\n"
        "Reading additional input from stdin...\n")
    assert diagnostic == "unexpected argument '--ignore-rules' found"


def test_nonzero_exit_prefers_redacted_json_error_event(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}")

    def fake_run(argv, **kwargs):
        events = "\n".join([
            json.dumps({"type": "thread.started", "message": "ignore me"}),
            json.dumps({
                "type": "error",
                "message": "request failed Authorization: Bearer secret-value",
            }),
        ])
        return subprocess.CompletedProcess(
            argv, 1, events, "Reading additional input from stdin...\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CodexProviderError) as exc_info:
        invoke_codex(
            {}, codex="codex", model="gpt-test", schema=schema,
            workspace=tmp_path / "workspace", timeout_seconds=30)
    message = str(exc_info.value)
    assert "request failed" in message
    assert "secret-value" not in message
    assert "[REDACTED]" in message


def test_nonzero_exit_ignores_non_error_json_event(monkeypatch, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}")

    def fake_run(argv, **kwargs):
        events = json.dumps({"type": "item.completed", "message": "source text"})
        return subprocess.CompletedProcess(argv, 1, events, "fatal: safe failure\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CodexProviderError, match="fatal: safe failure"):
        invoke_codex(
            {}, codex="codex", model="gpt-test", schema=schema,
            workspace=tmp_path / "workspace", timeout_seconds=30)


def test_codex_pilot_unit_is_manual_only_and_masks_betting_secrets():
    service = Path(
        "scripts/systemd/research-agent-codex-pilot.service").read_text()
    config = Path(
        "config/research_agent_runtime_screened_pilot.yaml").read_text()
    timers = list(Path("scripts/systemd").glob("research-agent-codex-pilot.timer"))

    assert not timers
    assert "ConditionPathExists=/var/lib/research-codex/pilot-enabled" in service
    assert "ConditionPathExists=/var/lib/research-codex/auth.json" in service
    assert "--execute" in service
    assert "research_agent_runtime_screened_pilot.yaml" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in service
    assert "InaccessiblePaths=-/opt/betting-pod-shop/.env" in service
    assert "InaccessiblePaths=-/opt/betting-pod-shop/kalshi_private_key.pem" in service
    assert "target_assignment_id: assignment_0c5bb6106a1add3dfcba" in config
    # 4, not 2: the attempt ledger is global per UTC day, and the pilot's job
    # now includes supervised same-day retries after a failed daily run (2
    # burned + screen + deep). The unattended daily config keeps 2.
    assert "max_attempts_per_day: 4" in config
    assert "screening:\n  enabled: true" in config
    assert "--disable-search" in config
    assert "--reasoning-effort\n      - low" in config
    assert "billing_mode: chatgpt_subscription" in config
    assert "KALSHI" not in config
    assert "PROPHETX" not in config
    assert "X_BEARER_TOKEN" not in config


def test_codex_output_schema_is_strict_for_every_object():
    schema = json.loads(Path("config/research_agent_output.schema.json").read_text())

    def check(node):
        if isinstance(node, dict):
            if "const" in node:
                assert "type" in node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                properties = node.get("properties", {})
                assert set(node.get("required", [])) == set(properties)
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


def test_codex_provider_schemas_avoid_unsupported_composition_keywords():
    """Codex structured outputs reject conditional JSON Schema composition.

    Decision-dependent defer/needs_work rules are enforced after parsing by
    ScreeningDecision and ResearchDisposition instead.
    """
    forbidden = {"allOf", "if", "then", "else", "not"}

    def check(node, path):
        if isinstance(node, dict):
            assert not (forbidden & set(node)), path
            for key, value in node.items():
                check(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                check(value, f"{path}[{index}]")

    for schema_path in (
            Path("config/research_agent_output.schema.json"),
            Path("config/research_screen_output.schema.json")):
        check(json.loads(schema_path.read_text()), str(schema_path))


def test_codex_pilot_setup_keeps_each_run_explicit_and_self_disabling():
    script = Path("scripts/setup_codex_research_pilot.sh").read_text()

    assert "ACTION=${1:-check}" in script
    assert "research_agent_runtime_screened_pilot.yaml" in script
    assert '[[ -f "$STATE/auth.json" ]]' in script
    assert 'install -o bettingbot -g bettingbot -m 0600 /dev/null "$STATE/pilot-enabled"' in script
    assert script.count('rm -f "$STATE/pilot-enabled"') == 2
    assert "systemctl enable" not in script
    assert "systemctl start \"$UNIT\"" in script


def test_codex_daily_setup_retires_week_timer_and_keeps_run_now_bounded():
    script = Path("scripts/setup_codex_research_daily.sh").read_text()

    assert "research-agent-screened-daily.service" in script
    assert "research-agent-screened-daily.timer" in script
    assert "research-agent-codex-week.timer" in script
    assert 'systemctl disable --now "$LEGACY_TIMER"' in script
    assert 'systemctl enable --now "$TIMER"' in script
    assert 'systemctl start "$SERVICE"' in script
    assert "research_agent_runtime_screened_daily.yaml" in script
    assert "max_minutes_per_task: 45" in script
    assert "max_attempts_per_day: 6" in script
    assert "research-agent-deep-daily" in script
    assert "max_input_tokens_per_task: 250000" in script
    assert "max_attempts_per_day: 4" in script


def test_temporary_codex_week_has_fixed_dates_and_saturday_check():
    from datetime import date

    timer = Path("scripts/systemd/research-agent-codex-week.timer").read_text()
    service = Path("scripts/systemd/research-agent-codex-week.service").read_text()
    config = Path("config/research_agent_runtime_codex_pilot.yaml").read_text()

    assert action_for_day(date(2026, 8, 3)) == "idle"
    assert action_for_day(date(2026, 8, 4)) == "run"
    assert action_for_day(date(2026, 8, 7)) == "run"
    assert action_for_day(date(2026, 8, 8)) == "check"
    assert action_for_day(date(2026, 8, 9)) == "check"
    assert "OnUnitActiveSec" not in timer
    assert timer.count("OnCalendar=") == 5
    assert "2026-08-08 14:00:00 UTC" in timer
    assert "User=bettingbot" in service
    assert "trading_execution_allowed: false" in config
    assert "max_attempts_per_day: 1" in config
    assert "max_input_tokens_per_task: 250000" in config
