"""Pin manager metadata to the gate artifacts that actually make decisions."""

from pathlib import Path

import yaml

from scripts import p022_checkpoint


ROOT = Path(__file__).resolve().parent.parent


def _registry():
    return yaml.safe_load((ROOT / "manager" / "registry.yaml").read_text())


def _workstream(registry, workstream_id):
    return next(w for w in registry["workstreams"] if w["id"] == workstream_id)


def test_p022_registry_matches_the_sanctioned_reader():
    gate = _workstream(_registry(), "P-022")["gate"]

    assert gate["threshold"] == p022_checkpoint.MIN_T_DECISION
    assert gate["minimum_filled_markets"] == p022_checkpoint.MIN_FILLED_DECISION
    assert gate["extension_threshold"] == p022_checkpoint.MIN_T_EXTENSION
    assert (
        gate["extension_minimum_filled_markets"]
        == p022_checkpoint.MIN_FILLED_EXTENSION
    )
    assert (
        gate["max_adverse_per_tournament"]
        == p022_checkpoint.MAX_ADVERSE_PER_TOURNAMENT
    )
    assert gate["rule_document"] == "golf_quirks_research/P022_DECISION_RULE.md"


def test_p029_registry_records_closed_gate_and_frozen_forward_gate():
    workstream = _workstream(_registry(), "P-029")

    assert workstream["blocked_on"] == "time"
    assert str(workstream["recheck_after"]) == "2026-08-23"
    assert workstream["prior_gate"]["verdict"] == "STOP"
    assert workstream["prior_gate"]["resolved_on"].isoformat() == "2026-08-05"
    gate = workstream["gate"]
    assert gate["rule_document"].endswith("PREREG_P029_Gate0c_Forward.md")
    assert gate["source"] == "p029_checkpoint"
    assert gate["reader"] == "scripts/p029_gate0c_checkpoint.py"
    assert gate["model_sha256"] == (
        "01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af"
    )
    assert str(gate["read_not_before"]) == "2026-08-23"


def test_closed_tennis_research_is_not_waiting_on_sam():
    workstream = _workstream(_registry(), "R-TENNIS")

    assert workstream["decision"]["verdict"] == "DROPPED"
    assert workstream["blocked_on"] == "nothing"


def test_p029_provisioning_keeps_gate0c_memory_headroom():
    provisioner = (ROOT / "scripts" / "provision_p029_vps.sh").read_text()
    shadow_unit = provisioner.split(
        "Description=P-029 combo shadow logger", 1
    )[1].split("EOF", 1)[0]

    assert "MemoryMax=768M" in shadow_unit
    assert "MemoryMax=512M" not in shadow_unit
