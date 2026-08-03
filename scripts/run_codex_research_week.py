"""Temporary autonomous Codex research window for August 4-8, 2026.

Tuesday through Friday execute at most one assignment through the normal
budgeted worker. Saturday is status-only and writes a bounded checkpoint. The
systemd timer contains only absolute dates, and this script independently
fails closed outside the same window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import yaml


ROOT = Path("/opt/betting-pod-shop")
STATE = Path("/var/lib/research-codex")
CONFIG = ROOT / "config/research_agent_runtime_codex_pilot.yaml"
RUN_DAYS = {
    date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7),
}
CHECK_DAY = date(2026, 8, 8)


def action_for_day(day: date) -> str:
    if day in RUN_DAYS:
        return "run"
    if day >= CHECK_DAY:
        return "check"
    return "idle"


def _config() -> dict:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    limits = payload.get("limits") or {}
    if limits.get("max_attempts_per_day") != 1:
        raise RuntimeError("autonomous pilot requires daily attempt cap 1")
    if limits.get("max_input_tokens_per_task") != 250_000:
        raise RuntimeError("autonomous pilot requires input-token cap 250000")
    safety = payload.get("safety") or {}
    if safety.get("trading_execution_allowed") is not False:
        raise RuntimeError("autonomous pilot requires trading disabled")
    return payload


def _write_checkpoint(now: datetime, config: dict) -> None:
    completed = subprocess.run(
        [str(ROOT / "venv/bin/python"), "-m",
         "scripts.run_research_execution", "status"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    status = json.loads(completed.stdout)
    checkpoint = {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window": "2026-08-04/2026-08-08",
        "autonomous_runs_ended": True,
        "pilot_marker_present": (STATE / "pilot-enabled").exists(),
        "limits": {
            "max_attempts_per_day": config["limits"]["max_attempts_per_day"],
            "max_input_tokens_per_task":
                config["limits"]["max_input_tokens_per_task"],
        },
        "execution": status,
    }
    STATE.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="week-check.", dir=STATE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(checkpoint, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(name, 0o600)
        os.replace(name, STATE / "week-check-2026-08-08.json")
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
    print(json.dumps(checkpoint, indent=2, sort_keys=True))


def main() -> int:
    now = datetime.now(timezone.utc)
    action = action_for_day(now.date())
    config = _config()
    if action == "idle":
        print("outside autonomous Codex research window; no action")
        return 0
    if action == "check":
        _write_checkpoint(now, config)
        return 0
    completed = subprocess.run(
        [str(ROOT / "venv/bin/python"), "-m",
         "scripts.run_research_agent_worker", "--config", str(CONFIG),
         "--execute"],
        cwd=ROOT,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
