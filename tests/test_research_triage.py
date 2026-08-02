from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from src.research_intake import SourceItem
from src.research_triage import title_tokens, triage_assignments


NOW = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)


def _assignment(index, *, source="social", lane="social_signal",
                score=50, legal=None, title=None):
    agent = {"social": "social-scout", "paper": "literature-scout"}.get(
        source, "strategy-scout")
    return {
        "id": f"a{index}", "source_item_id": f"s{index}",
        "created_at": "2026-08-02T13:00:00Z", "score": score,
        "lane": lane, "title": title or f"Distinct mechanism idea {index}",
        "source_type": source, "source_name": "Source",
        "attribution_key": "Source", "venue_ids": [],
        "product_family": "*", "legal_decisions": legal or [],
        "assignment": {"agent": agent, "instruction": "falsify it"},
        "may_enter_strategy_registry": False,
    }


class TestResearchTriage(TestCase):
    def test_dispatch_preserves_unknown_evidence_and_source_provenance(self):
        assignment = _assignment(
            1, source="social", title="Settlement fee mispricing research")
        source_item = {
            "id": "s1", "url": "https://x.com/u/status/1",
            "summary": "Public analysis of a settlement fee mechanism",
            "external_id": "1", "metadata": {"username": "u"},
        }
        _, manifest, packets = triage_assignments(
            [assignment], source_items_by_id={"s1": source_item}, now=NOW)
        self.assertEqual(manifest["dispatched"], 1)
        packet = packets[0]
        self.assertEqual(packet.assigned_agent, "social-scout")
        self.assertEqual(packet.source_item["url"], source_item["url"])
        self.assertIsNone(packet.score_components["capacity"]["score"])
        self.assertIsNone(packet.score_components["net_edge"]["score"])
        self.assertFalse(packet.safety["creates_opportunity_card"])
        self.assertIn("research-critic", packet.handoff_chain)

    def test_portfolio_has_exploration_floor_and_lane_cap(self):
        assignments = [
            _assignment(i, source="regulatory_filing", lane="market_structure",
                        score=95 - i) for i in range(1, 9)
        ]
        assignments += [
            _assignment(20, source="paper", lane="literature", score=55),
            _assignment(21, source="social", lane="social_signal", score=35),
        ]
        _, manifest, packets = triage_assignments(
            assignments, now=NOW, max_dispatches=5,
            max_research_minutes=200, lane_concentration_cap=0.40)
        lanes = [packet.assignment["lane"] for packet in packets]
        self.assertIn("literature", lanes)
        self.assertIn("social_signal", lanes)
        self.assertLessEqual(lanes.count("market_structure"), 2)
        self.assertEqual(manifest["by_lane"]["market_structure"], 2)

    def test_previous_reviewed_opportunity_and_prohibited_are_not_dispatched(self):
        prohibited = [{
            "status": "prohibited", "research_allowed": False,
            "execution_allowed": False, "stale": False,
        }]
        assignments = [_assignment(i) for i in range(1, 5)]
        assignments.append(_assignment(5, legal=prohibited))
        previous = {
            "dispatched_assignment_ids": ["a1"],
            "title_tokens_by_assignment": {"a1": title_tokens("old idea")},
        }
        _, manifest, packets = triage_assignments(
            assignments, previous_ledger=previous,
            reviewed_assignment_ids={"a2"},
            opportunity_assignment_ids={"a3"}, now=NOW)
        self.assertEqual([packet.assignment_id for packet in packets], ["a4"])
        self.assertEqual(manifest["already_dispatched"], 1)
        self.assertEqual(manifest["already_reviewed"], 1)
        self.assertEqual(manifest["already_opportunity"], 1)
        self.assertEqual(manifest["legally_blocked"], 1)

    def test_quality_gate_rechecks_legacy_assignment_before_dispatch(self):
        assignment = _assignment(
            1, source="regulatory_filing", lane="new_products",
            title="First inning run contract")
        source = SourceItem.create(
            source_type="regulatory_filing", source_name="CFTC",
            external_id="filing-1", title="First inning run contract",
            summary="Certified product listing", url="https://example.test/filing",
            retrieved_at="2026-08-02T13:00:00Z")
        _, manifest, packets = triage_assignments(
            [assignment], source_items_by_id={"s1": source.to_dict()},
            memory_rules=[{
                "id": "P-024", "status": "killed",
                "match_any": ["first inning"],
            }], now=NOW)
        self.assertEqual(packets, [])
        self.assertEqual(manifest["quality_blocked"], 1)
        self.assertEqual(
            manifest["quality_blocked_assignment_ids"]["a1"],
            "prior_research_killed")

    def test_second_pass_is_idempotent(self):
        ledger, _, first = triage_assignments([_assignment(1)], now=NOW)
        _, manifest, second = triage_assignments(
            [_assignment(1)], previous_ledger=ledger, now=NOW)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(manifest["already_dispatched"], 1)

    def test_due_deferral_can_be_redispatched(self):
        ledger, _, first = triage_assignments([_assignment(1)], now=NOW)
        next_ledger, manifest, reopened = triage_assignments(
            [_assignment(1)], previous_ledger=ledger,
            redispatch_assignment_ids={"a1"}, now=NOW)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(reopened), 1)
        self.assertEqual(manifest["reopened_due_deferral"], 1)
        self.assertIn("a1", next_ledger["dispatched_assignment_ids"])

    def test_same_day_retry_cannot_exceed_daily_allocation(self):
        ledger, first_manifest, first = triage_assignments(
            [_assignment(1)], now=NOW, max_dispatches=1,
            max_research_minutes=20)
        _, retry_manifest, retry = triage_assignments(
            [_assignment(1), _assignment(2)], previous_ledger=ledger,
            now=NOW, max_dispatches=1, max_research_minutes=20)
        self.assertEqual(len(first), 1)
        self.assertEqual(first_manifest["daily_remaining"]["dispatches"], 0)
        self.assertEqual(retry, [])
        self.assertEqual(retry_manifest["daily_totals"]["dispatches"], 1)
        self.assertEqual(retry_manifest["daily_remaining"]["minutes"], 0)
