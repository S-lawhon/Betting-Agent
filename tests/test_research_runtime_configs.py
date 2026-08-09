"""The shipped research runtime configs must load into valid limits.

These configs are consumed by an unattended systemd unit at 05:13 UTC. A typo
or an out-of-range limit would surface as a failed unit in the morning with no
research done, so it is checked here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.research_agent_worker import ScreeningLimits, WorkerLimits


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted((ROOT / "config").glob("research_agent_runtime*.yaml"))

# Every deep-research call measured to date. Codex reports input tokens
# cumulatively across its internal turns, so a pass costs far more than the
# packet it was handed. See the comment in the daily config.
OBSERVED_DEEP_INPUT_TOKENS = (
    36_868, 69_197, 74_134, 78_700, 88_948, 111_426, 202_807)


def _config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_configs_are_present():
    assert CONFIGS, "no research runtime configs found"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_runtime_config_loads_into_valid_limits(path):
    config = _config(path)
    assert config.get("schema_version") == 1
    assert str(config.get("worker_id") or "").strip()
    assert config.get("mode") in {"dry_run", "execute"}
    limits = WorkerLimits(**dict(config.get("limits") or {}))
    assert limits.max_minutes_per_task > 0
    screening = dict(config.get("screening") or {})
    ScreeningLimits(**dict(screening.get("limits") or {}))
    # Trading can never be enabled from a research runtime.
    assert (config.get("safety") or {}).get(
        "trading_execution_allowed") is False


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_execute_configs_declare_a_command_provider(path):
    config = _config(path)
    if config.get("mode") != "execute":
        return
    provider = dict(config.get("provider") or {})
    assert provider.get("type") == "command"
    assert provider.get("argv"), "an execute config needs a provider argv"
    screening = dict(config.get("screening") or {})
    if screening.get("enabled"):
        # Screening only applies to strategy-scout, so admitting another agent
        # would route its packet straight to the unscreened deep call.
        assert list(config.get("allowed_agents") or []) == ["strategy-scout"]
        assert dict(screening.get("provider") or {}).get("type") == "command"


def test_daily_config_covers_the_measured_deep_research_distribution():
    """The recurring config must not re-adopt a cap below the observed tail.

    The limit is checked only after the provider returns, so a cap under the
    tail cannot prevent spend -- it discards work already paid for and leaves
    the packet to fail the same way tomorrow. That happened twice before the
    cap was raised.
    """
    path = ROOT / "config" / "research_agent_runtime_screened_daily.yaml"
    limits = WorkerLimits(**dict(_config(path).get("limits") or {}))
    assert limits.max_input_tokens_per_task >= max(OBSERVED_DEEP_INPUT_TOKENS)
    assert limits.max_attempts_per_day == 2, (
        "attempts per day is the real runaway guard; do not widen it")


def test_pilot_config_stays_exact_target_and_manual():
    """The supervised one-off keeps its pin; only the daily config is open."""
    config = _config(
        ROOT / "config" / "research_agent_runtime_screened_pilot.yaml")
    assert str(config.get("target_assignment_id") or "").startswith("assignment_")
    assert config["limits"]["max_attempts_per_day"] == 2
    daily = _config(
        ROOT / "config" / "research_agent_runtime_screened_daily.yaml")
    assert not daily.get("target_assignment_id")
