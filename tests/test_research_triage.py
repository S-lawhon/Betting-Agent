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

    def test_backpressure_stops_dispatch_when_pending_queue_is_full(self):
        pending = [{
            "assignment_id": "old", "assigned_agent": "strategy-scout",
        }]
        _, manifest, packets = triage_assignments(
            [_assignment(1)], pending_packets=pending, now=NOW,
            max_pending_total=1, max_new_dispatches_per_run=1)
        self.assertEqual(packets, [])
        self.assertTrue(manifest["backpressure"]["saturated"])
        self.assertEqual(manifest["backpressure"]["pending_before"], 1)

    def test_backpressure_stops_one_agent_from_owning_the_queue(self):
        pending = [{
            "assignment_id": "old", "assigned_agent": "social-scout",
        }]
        _, manifest, packets = triage_assignments(
            [_assignment(1)], pending_packets=pending, now=NOW,
            max_pending_total=10, max_pending_per_agent=1)
        self.assertEqual(packets, [])
        self.assertEqual(
            manifest["backpressure"]["agent_capacity_blocked_assignment_ids"],
            ["a1"])

    def test_market_listing_without_mechanism_is_not_dispatched(self):
        assignment = _assignment(
            1, source="venue_market", lane="information_latency",
            title="Valparaiso vs. Belmont")
        source = SourceItem.create(
            source_type="venue_market", source_name="Venue",
            external_id="game-1", title="Valparaiso vs. Belmont",
            summary="Valparaiso vs. Belmont",
            url="https://example.test/game-1",
            retrieved_at="2026-08-02T13:00:00Z",
            product_family="sports")
        _, manifest, packets = triage_assignments(
            [assignment], source_items_by_id={"s1": source.to_dict()}, now=NOW)
        self.assertEqual(packets, [])
        self.assertEqual(
            manifest["readiness_blocked_assignment_ids"]["a1"],
            "event_listing_without_mechanism")

    def test_legacy_regulatory_code_uses_filing_description_in_dispatch(self):
        assignment = _assignment(
            1, source="regulatory_filing", lane="market_structure",
            score=93, title="ARST")
        source = SourceItem.create(
            source_type="regulatory_filing", source_name="CFTC",
            external_id="filing-1", title="ARST", summary="ARST: ARST",
            url="https://example.test/filing-1",
            retrieved_at="2026-08-02T13:00:00Z",
            metadata={
                "Organization": "ARST",
                "Filing Description": "Rule additions relevant to RFQ system.",
                "Status": "Certified",
            })
        original_hash = source.content_hash
        _, manifest, packets = triage_assignments(
            [assignment], source_items_by_id={"s1": source.to_dict()}, now=NOW)
        self.assertEqual(manifest["dispatched"], 1)
        self.assertEqual(
            packets[0].assignment["title"],
            "Rule additions relevant to RFQ system.")
        # The packet is what a specialist reads, so its embedded source record
        # is corrected too -- without moving the dedup identity.
        self.assertEqual(
            packets[0].source_item["title"],
            "Rule additions relevant to RFQ system.")
        self.assertEqual(
            packets[0].source_item["summary"],
            "ARST: Rule additions relevant to RFQ system.")
        self.assertEqual(packets[0].source_item["original_title"], "ARST")
        self.assertEqual(
            packets[0].source_item["title_source"],
            "metadata.Filing Description")
        self.assertEqual(packets[0].source_item["id"], source.id)
        self.assertEqual(packets[0].source_item["content_hash"], original_hash)

    def test_ledger_state_does_not_permanently_block_a_corrected_assignment(self):
        assignment = _assignment(
            1, source="regulatory_filing", lane="market_structure",
            score=93, title="COIN")
        source = SourceItem.create(
            source_type="regulatory_filing", source_name="CFTC",
            external_id="filing-coin", title="COIN", summary="COIN: COIN",
            url="https://example.test/filing-coin",
            retrieved_at="2026-08-02T13:00:00Z",
            metadata={
                "Organization": "COIN", "Status": "10 Day Review",
                "Filing Description": (
                    "Modifications to Equity Index Perp Style Futures "
                    "Market Maker Program"),
            })
        ledger = {
            "dispatched_assignment_ids": ["a1"],
            "title_tokens_by_assignment": {"a1": ["coin"]},
        }
        sources = {"s1": source.to_dict()}
        # Without an explicit reopen the ledger keeps it out, which is what a
        # normal run should do.
        _, blocked_manifest, blocked_packets = triage_assignments(
            [assignment], source_items_by_id=sources,
            previous_ledger=ledger, now=NOW)
        self.assertEqual(blocked_packets, [])
        self.assertEqual(blocked_manifest["already_dispatched"], 1)
        # Re-admission reopens exactly that assignment and it carries the
        # corrected title, under its original id.
        new_ledger, manifest, packets = triage_assignments(
            [assignment], source_items_by_id=sources,
            previous_ledger=ledger, redispatch_assignment_ids={"a1"}, now=NOW)
        self.assertEqual(manifest["dispatched"], 1)
        self.assertEqual(manifest["reopened_due_deferral"], 1)
        self.assertEqual(packets[0].assignment_id, "a1")
        self.assertEqual(packets[0].assigned_agent, "strategy-scout")
        self.assertEqual(
            packets[0].assignment["title"],
            "Modifications to Equity Index Perp Style Futures "
            "Market Maker Program")
        self.assertIn("a1", new_ledger["dispatched_assignment_ids"])

    def test_triage_keeps_one_packet_per_research_family(self):
        first = _assignment(1, source="paper", lane="literature", score=80)
        second = _assignment(2, source="paper", lane="literature", score=70)
        first["assignment"]["market_family_key"] = "family:calibration"
        second["assignment"]["market_family_key"] = "family:calibration"
        _, manifest, packets = triage_assignments(
            [first, second], now=NOW, lane_concentration_cap=1.0)
        self.assertEqual([packet.assignment_id for packet in packets], ["a1"])
        self.assertEqual(
            manifest["family_duplicate_representatives"], {"a2": "a1"})

    def test_prior_dispositions_penalize_low_context_sources(self):
        good_history = _assignment(10, source="paper", lane="literature")
        good_history["attribution_key"] = "Good"
        bad_history = _assignment(11, source="paper", lane="literature")
        bad_history["attribution_key"] = "Bad"
        good = _assignment(1, source="paper", lane="literature", score=60)
        good["attribution_key"] = "Good"
        bad = _assignment(2, source="paper", lane="literature", score=60)
        bad["attribution_key"] = "Bad"
        dispositions = [
            {"assignment_id": "a10", "decision": "advance",
             "reason_codes": []},
            {"assignment_id": "a11", "decision": "reject",
             "reason_codes": ["no_specific_market_mechanism"]},
        ]
        _, _, packets = triage_assignments(
            [good_history, bad_history, good, bad],
            reviewed_assignment_ids={"a10", "a11"},
            disposition_history=dispositions, now=NOW,
            max_dispatches=1, lane_concentration_cap=1.0)
        self.assertEqual([packet.assignment_id for packet in packets], ["a1"])
        self.assertGreater(
            packets[0].score_components["historical_yield"]["score"], 50)
