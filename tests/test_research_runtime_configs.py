"""The shipped research runtime configs must load into valid limits.

These configs are consumed by an unattended systemd unit at 05:13 UTC. A typo
or an out-of-range limit would surface as a failed unit in the morning with no
research done, so it is checked here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.run_research_agent_worker import _config as load_runtime_config
from src.research_agent_worker import RetryLimits, ScreeningLimits, WorkerLimits


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted((ROOT / "config").glob("research_agent_runtime*.yaml"))

# Every deep-research call measured to date. Codex reports input tokens
# cumulatively across its internal turns, so a pass costs far more than the
# packet it was handed. See the comment in the daily config.
OBSERVED_DEEP_INPUT_TOKENS = (
    36_868, 69_197, 74_134, 78_700, 88_948, 111_426, 202_807)


def _config(path: Path) -> dict:
    return load_runtime_config(path)


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
    RetryLimits(**dict(config.get("retry") or {}))
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
        # Every allowed agent must be behind the screen: an allowed agent
        # outside screening.agents would route its packet straight to the
        # unscreened deep call. The worker refuses to start on this, but the
        # unit fails at 05:10 UTC with nobody watching, so catch it here.
        screened = list(screening.get("agents") or ["strategy-scout"])
        allowed = list(config.get("allowed_agents") or [])
        assert allowed, "a screening config must name its allowed agents"
        assert all(agent in screened for agent in allowed), (
            f"allowed agents {allowed} must all be in screening.agents "
            f"{screened}")
        assert dict(screening.get("provider") or {}).get("type") == "command"


def test_daily_config_covers_the_measured_deep_research_distribution():
    """The recurring config must not re-adopt a cap below the observed tail.

    The limit is checked only after the provider returns, so a cap under the
    tail cannot prevent spend -- it discards work already paid for and leaves
    the packet to fail the same way tomorrow. That happened twice before the
    cap was raised.
    """
    path = ROOT / "config" / "research_agent_runtime_screened_daily.yaml"
    config = _config(path)
    limits = WorkerLimits(**dict(config.get("limits") or {}))
    assert limits.max_input_tokens_per_task >= max(OBSERVED_DEEP_INPUT_TOKENS)
    assert limits.max_attempts_per_day == 5
    assert config.get("stage_mode") == "screen_only"
    deep = _config(
        ROOT / "config" / "research_agent_runtime_deep_daily.yaml")
    assert deep.get("stage_mode") == "deep_only"
    assert deep["limits"]["max_attempts_per_day"] == 2


def test_pilot_config_stays_exact_target_and_manual():
    """The supervised one-off keeps its pin; only the daily config is open.

    The pilot's attempt limit is 4, not 2: the attempt ledger is global per
    state_dir per UTC day, so a supervised same-day retry after a failed
    daily run must reserve 2 on top of the 2 the failure consumed. That
    headroom belongs ONLY in the human-triggered pinned config — the daily
    config's 2 is pinned above.
    """
    config = _config(
        ROOT / "config" / "research_agent_runtime_screened_pilot.yaml")
    assert str(config.get("target_assignment_id") or "").startswith("assignment_")
    assert config["limits"]["max_attempts_per_day"] == 4
    daily = _config(
        ROOT / "config" / "research_agent_runtime_screened_daily.yaml")
    assert not daily.get("target_assignment_id")
