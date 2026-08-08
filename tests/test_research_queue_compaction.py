from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.compact_research_queue import main
from src.research_queue_compaction import (
    ResearchQueueCompactionError,
    active_claim_assignment_ids,
    plan_queue_compaction,
)


NOW = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)


def _packet(index: int, agent: str, score: float) -> dict:
    return {
        "assignment_id": f"a{index}",
        "assigned_agent": agent,
        "triage_score": score,
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_plan_keeps_strongest_with_total_and_agent_caps():
    packets = [
        _packet(1, "strategy-scout", 99),
        _packet(2, "strategy-scout", 98),
        _packet(3, "strategy-scout", 97),
        _packet(4, "literature-scout", 80),
        _packet(5, "social-scout", 70),
    ]
    plan = plan_queue_compaction(
        packets, active_assignment_ids=set(),
        max_pending_total=3, max_pending_per_agent=2)
    assert plan["kept_assignment_ids"] == ["a1", "a2", "a4"]
    assert plan["quarantined_assignment_ids"] == ["a3", "a5"]
    assert plan["kept_packets"][0]["triage_score"] == 99
    assert plan["quarantined_packets"][0]["assigned_agent"] == "strategy-scout"
    assert plan["recoverable"] is True


def test_active_claim_is_kept_ahead_of_higher_scores():
    packets = [
        _packet(1, "strategy-scout", 10),
        _packet(2, "strategy-scout", 99),
        _packet(3, "literature-scout", 90),
    ]
    plan = plan_queue_compaction(
        packets, active_assignment_ids={"a1"},
        max_pending_total=2, max_pending_per_agent=1)
    assert plan["kept_assignment_ids"] == ["a1", "a3"]


def test_active_claims_over_a_limit_fail_closed():
    packets = [_packet(1, "strategy-scout", 10),
               _packet(2, "strategy-scout", 20)]
    with pytest.raises(ResearchQueueCompactionError, match="per_agent"):
        plan_queue_compaction(
            packets, active_assignment_ids={"a1", "a2"},
            max_pending_total=2, max_pending_per_agent=1)


def test_active_claim_without_pending_packet_fails_closed():
    with pytest.raises(ResearchQueueCompactionError, match="no pending packet"):
        plan_queue_compaction(
            [_packet(1, "strategy-scout", 10)],
            active_assignment_ids={"missing"},
            max_pending_total=2, max_pending_per_agent=1)


def test_claim_reader_ignores_expired_lease_and_protects_live_one(tmp_path):
    claims = tmp_path / "claims" / "strategy-scout"
    _write(claims / "a1.json", {
        "assignment_id": "a1", "lease_expires_at": "2026-08-08T13:59:59Z"})
    _write(claims / "a2.json", {
        "assignment_id": "a2", "lease_expires_at": "2026-08-08T14:00:01Z"})
    assert active_claim_assignment_ids(tmp_path, now=NOW) == {"a2"}


def test_cli_dry_run_does_not_move_packets(tmp_path, capsys):
    triage = tmp_path / "triage"
    state = tmp_path / "execution"
    config = tmp_path / "triage.yaml"
    config.write_text(
        "backpressure:\n  max_pending_total: 1\n"
        "  max_pending_per_agent: 1\n", encoding="utf-8")
    packet_path = triage / "dispatches" / "strategy-scout" / "a1.json"
    _write(packet_path, _packet(1, "strategy-scout", 10))
    _write(
        triage / "dispatches" / "strategy-scout" / "a2.json",
        _packet(2, "strategy-scout", 20))
    assert main([
        "--config", str(config), "--triage-dir", str(triage),
        "--execution-state-dir", str(state), "--now", NOW.isoformat(),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry_run"
    assert packet_path.exists()
    assert not (triage / "dispatch_quarantine").exists()


def test_cli_apply_moves_overflow_and_writes_manifest(tmp_path, capsys):
    triage = tmp_path / "triage"
    state = tmp_path / "execution"
    config = tmp_path / "triage.yaml"
    config.write_text(
        "backpressure:\n  max_pending_total: 1\n"
        "  max_pending_per_agent: 1\n", encoding="utf-8")
    low = triage / "dispatches" / "strategy-scout" / "a1.json"
    high = triage / "dispatches" / "strategy-scout" / "a2.json"
    _write(low, _packet(1, "strategy-scout", 10))
    _write(high, _packet(2, "strategy-scout", 20))
    assert main([
        "--config", str(config), "--triage-dir", str(triage),
        "--execution-state-dir", str(state), "--now", NOW.isoformat(),
        "--apply",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "applied"
    assert not low.exists()
    assert high.exists()
    moved = triage / "dispatch_quarantine" / "strategy-scout" / "a1.json"
    assert moved.exists()
    manifest = json.loads(
        (triage / "latest_queue_compaction.json").read_text())
    assert manifest["quarantine_reason"] == "backpressure_overflow"
    assert manifest["pending_after"] == 1
