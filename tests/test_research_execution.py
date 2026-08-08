from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.research_execution import (
    ResearchExecutionError,
    claim_next,
    complete_claim,
    execution_status,
    mark_claim_invoked,
    mark_claim_phase_invoked,
    preview_next,
    release_claim,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def packet(path: Path, assignment: str, priority: str, score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": "dispatch-{}".format(assignment),
        "assignment_id": assignment,
        "source_item_id": "source-{}".format(assignment),
        "assigned_agent": path.parent.name,
        "priority": priority,
        "triage_score": score,
        "created_at": "2026-08-03T11:00:00Z",
    }))


def test_claim_is_priority_ordered_and_prevents_double_claim(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "strategy-scout/a1.json", "a1", "medium", 99)
    packet(dispatches / "social-scout/a2.json", "a2", "high", 50)
    first = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-1", now=NOW)
    second = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-2", now=NOW)
    assert first and first.assignment_id == "a2"
    assert second and second.assignment_id == "a1"
    assert execution_status(state, now=NOW)["active"] == 2


def test_preview_is_read_only_and_uses_claim_availability(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "strategy-scout/a1.json", "a1", "high", 70)

    preview = preview_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, now=NOW)

    assert preview and preview[1]["assignment_id"] == "a1"
    assert not state.exists()


def test_model_invocation_is_attached_once_to_active_claim(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "strategy-scout/a1.json", "a1", "high", 70)
    claim = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-1", now=NOW)
    path = state / "claims/strategy-scout/a1.json"

    marked = mark_claim_invoked(
        claim_path=path, state_dir=state, provider="fake", model="fixture",
        budget={"max_cost_usd": 1}, now=NOW)

    assert claim and marked["model_invocation_tracked"] is True
    assert marked["provider"] == "fake"
    with pytest.raises(ResearchExecutionError, match="already recorded"):
        mark_claim_invoked(
            claim_path=path, state_dir=state, provider="fake", model="fixture",
            budget={}, now=NOW)


def test_named_invocation_phases_are_each_recorded_once(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "strategy-scout/a1.json", "a1", "high", 70)
    claim = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-1", now=NOW)
    path = state / "claims/strategy-scout/a1.json"

    first = mark_claim_phase_invoked(
        claim_path=path, state_dir=state, provider="screen", model="small",
        budget={"max_input_tokens": 1000}, phase="screening", now=NOW)
    second = mark_claim_phase_invoked(
        claim_path=path, state_dir=state, provider="deep", model="large",
        budget={"max_input_tokens": 10000}, phase="deep_research", now=NOW)

    assert claim and [item["phase"] for item in second["model_invocations"]] == [
        "screening", "deep_research"]
    assert first["provider"] == "screen"
    with pytest.raises(ResearchExecutionError, match="already recorded"):
        mark_claim_phase_invoked(
            claim_path=path, state_dir=state, provider="screen", model="small",
            budget={}, phase="screening", now=NOW)


def test_expired_claim_is_recovered(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "social-scout/a1.json", "a1", "high", 50)
    first = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-1", now=NOW,
        lease_minutes=5)
    later = datetime(2026, 8, 3, 12, 6, tzinfo=timezone.utc)
    reclaimed = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-2", now=later)
    assert first and reclaimed
    assert reclaimed.assignment_id == first.assignment_id
    assert reclaimed.worker_id == "worker-2"
    assert len(list((state / "claim_expired/social-scout").glob("*.json"))) == 1


def test_complete_validates_ids_and_persists_disposition(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "literature-scout/a1.json", "a1", "high", 80)
    claim = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-1", now=NOW)
    assert claim
    claim_path = state / "claims/literature-scout/a1.json"
    payload = {
        "assignment_id": "wrong", "source_item_id": "source-a1",
        "decided_at": "2026-08-03T12:10:00Z", "decision": "reject",
        "reason_codes": ["no_mechanism"], "evidence_checked": ["paper"],
        "research_minutes": 10,
    }
    with pytest.raises(ResearchExecutionError):
        complete_claim(
            claim_path=claim_path, disposition_payload=payload,
            state_dir=state, dispositions_dir=dispositions,
            now=datetime(2026, 8, 3, 12, 10, tzinfo=timezone.utc))
    payload["assignment_id"] = "a1"
    disposition = complete_claim(
        claim_path=claim_path, disposition_payload=payload,
        state_dir=state, dispositions_dir=dispositions,
        now=datetime(2026, 8, 3, 12, 10, tzinfo=timezone.utc))
    assert disposition.decision == "reject"
    assert (dispositions / "a1.json").exists()
    assert not claim_path.exists()
    assert len(list((state / "claim_archive/literature-scout").glob(
        "a1--claim_*.json"))) == 1


def test_release_returns_packet_to_available_queue(tmp_path):
    dispatches = tmp_path / "dispatches"
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    packet(dispatches / "strategy-scout/a1.json", "a1", "high", 70)
    claim = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-1", now=NOW)
    assert claim
    release_claim(
        claim_path=state / "claims/strategy-scout/a1.json",
        state_dir=state, reason="worker shutdown", now=NOW)
    reclaimed = claim_next(
        dispatches_dir=dispatches, state_dir=state,
        dispositions_dir=dispositions, worker_id="worker-2", now=NOW)
    assert reclaimed and reclaimed.assignment_id == "a1"
