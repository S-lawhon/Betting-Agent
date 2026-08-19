from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

MANAGER = Path(__file__).resolve().parent.parent / "manager"
ROOT = MANAGER.parent
sys.path.insert(0, str(MANAGER))

collect = pytest.importorskip("collect")
checks = pytest.importorskip("checks")
brief = pytest.importorskip("brief")


def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(
        "meta:\n  project_root: {}\n  local_root: {}\n"
        "services: []\njobs: []\nworkstreams: []\n".format(tmp_path, tmp_path),
        encoding="utf-8")
    return path


def test_collector_reads_checkpoint_and_rejects_wrong_identity(tmp_path, monkeypatch):
    latest = tmp_path / "latest.json"
    payload = {"id": "R-MLB-PROPS", "trial": "V2",
               "metric": "clean_execution_game_days",
               "progress": 16, "threshold": 27, "verdict": "NO DECISION",
               "rule_sha256": collect.MLB_PROPS_RULE_SHA256}
    latest.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(collect, "MLB_PROPS_CHECKPOINT_PATH", latest)
    collector = collect.Collector(registry_file(tmp_path), root=tmp_path,
                                  local_root=tmp_path)

    assert collector.mlb_props_gate()["checkpoint"] == payload
    payload["threshold"] = 26
    latest.write_text(json.dumps(payload), encoding="utf-8")
    assert collector.mlb_props_gate()["available"] is False


@pytest.mark.parametrize("verdict,key,severity", [
    ("BUILD_CANDIDATE", "gate.R-MLB-PROPS.build_candidate", "action"),
    ("STOP", "gate.R-MLB-PROPS.stop", "warn"),
    ("RESULTS_PENDING", "gate.R-MLB-PROPS.results_pending", "warn"),
    ("UNSCORABLE", "gate.R-MLB-PROPS.unscorable", "info"),
    ("DATA_ERROR", "gate.R-MLB-PROPS.data_error", "warn"),
])
def test_terminal_gate_states_surface(verdict, key, severity):
    checkpoint = {"verdict": verdict, "progress": 27, "reason": "locked"}
    findings = {finding.key: finding for finding in checks.check_gates(
        {"mlb_props": {"checkpoint": checkpoint}}, {})}

    assert findings[key].severity == severity


def test_brief_includes_clean_day_progress():
    snapshot = {
        "mlb_props": {"checkpoint": {
            "progress": 16, "threshold": 27, "verdict": "NO DECISION",
            "reason": "outcome-blind",
        }},
        "workstreams": [], "services": [],
    }

    built = brief.build(snapshot, [])

    row = next(gate for gate in built["gates"] if gate["id"] == "R-MLB-PROPS")
    assert row["current"] == 16
    assert row["threshold"] == 27
    assert "Outcomes stay blind" in row["note"]


def test_registry_matches_locked_reader_and_units():
    registry = yaml.safe_load((ROOT / "manager" / "registry.yaml").read_text())
    workstream = next(row for row in registry["workstreams"]
                      if row["id"] == "R-MLB-PROPS")
    gate = workstream["gate"]

    assert gate["threshold"] == 27
    assert gate["source"] == "mlb_props_checkpoint"
    assert gate["reader"] == "mlb_props_research/execution_checkpoint.py"
    assert gate["rule_document"] == (
        "mlb_props_research/MLB_PROPS_EXECUTION_RULE_V2.md")
    assert gate["rule_sha256"] == collect.MLB_PROPS_RULE_SHA256
    assert str(gate["read_not_before"]) == "2026-09-15"
    units = {row["unit"]: row for row in registry["systemd_units"]}
    assert "mlb-props-execution-checkpoint.timer" in units
    assert "mlb-props-execution-checkpoint.service" in units
