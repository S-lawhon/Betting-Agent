from unittest import TestCase

from src.research_outcomes import ResearchDisposition, summarize_research


class TestResearchOutcomes(TestCase):
    def test_invalid_decisions_and_incomplete_advances_fail(self):
        common = dict(
            assignment_id="a1", source_item_id="s1", decided_at="2026-08-01",
            reason_codes=[], evidence_checked=[], research_minutes=10,
        )
        with self.assertRaises(ValueError):
            ResearchDisposition(decision="maybe", **common)
        with self.assertRaises(ValueError):
            ResearchDisposition(decision="advance", **common)
        with self.assertRaises(ValueError):
            ResearchDisposition(decision="defer", **common)

    def test_waiting_and_missing_work_are_distinct_states(self):
        waiting = ResearchDisposition(
            assignment_id="a1", source_item_id="s1", decided_at="now",
            decision="defer", reason_codes=["await_release"],
            evidence_checked=["calendar"], research_minutes=1,
            recheck_after="2026-09-01T00:00:00Z",
            blocking_fact="the scheduled public data release",
        )
        assert waiting.next_action is None

        work = ResearchDisposition(
            assignment_id="a2", source_item_id="s2", decided_at="now",
            decision="needs_work", reason_codes=["dataset_missing"],
            evidence_checked=["API docs"], research_minutes=2,
            next_action="build the timestamp-aligned dataset",
        )
        assert work.recheck_after is None
        with self.assertRaisesRegex(ValueError, "cannot use a calendar"):
            ResearchDisposition(
                assignment_id="a3", source_item_id="s3", decided_at="now",
                decision="needs_work", reason_codes=["dataset_missing"],
                evidence_checked=["API docs"], research_minutes=2,
                next_action="build it", recheck_after="2026-11-01T00:00:00Z",
            )

    def test_metrics_attribute_yield_and_rejections(self):
        assignments = [
            {"id": "a1", "source_type": "paper", "source_name": "arXiv", "attribution_key": "arXiv", "lane": "literature"},
            {"id": "a2", "source_type": "social", "source_name": "X", "attribution_key": "X:@analyst", "lane": "social_signal"},
            {"id": "a3", "source_type": "paper", "source_name": "arXiv", "attribution_key": "arXiv", "lane": "literature"},
        ]
        dispositions = [
            ResearchDisposition(
                assignment_id="a1", source_item_id="s1", decided_at="now",
                decision="advance", reason_codes=[], evidence_checked=["paper"],
                research_minutes=30, opportunity_id="op1"),
            ResearchDisposition(
                assignment_id="a2", source_item_id="s2", decided_at="now",
                decision="reject", reason_codes=["no_mechanism"],
                evidence_checked=["post"], research_minutes=10),
        ]
        dispatches = [{
            "assignment_id": "a1", "assigned_agent": "literature-scout",
            "priority": "high", "research_budget_minutes": 45,
        }, {
            "assignment_id": "a2", "assigned_agent": "social-scout",
            "priority": "exploratory", "research_budget_minutes": 20,
        }]
        metrics = summarize_research(assignments, dispositions, dispatches)
        self.assertEqual(metrics["unreviewed"], 1)
        self.assertEqual(metrics["minutes_per_advance"], 40)
        self.assertEqual(metrics["by_source_type"]["paper"]["advance_rate"], 0.5)
        self.assertEqual(metrics["by_source_name"]["arXiv"]["assigned"], 2)
        self.assertEqual(metrics["by_attribution"]["X:@analyst"]["reject"], 1)
        self.assertEqual(metrics["top_rejection_reasons"], {"no_mechanism": 1})
        self.assertEqual(metrics["funnel"], {
            "assignments": 3, "dispatched": 2,
            "dispatched_reviewed": 2, "dispatched_advanced": 1,
        })
        self.assertEqual(metrics["dispatch"]["pending_review"], 0)
        self.assertEqual(
            metrics["dispatch"]["by_agent"]["literature-scout"]["advance"], 1)

    def test_redispatch_after_old_defer_is_pending_again(self):
        assignments = [{
            "id": "a1", "source_type": "paper", "source_name": "arXiv",
            "attribution_key": "arXiv", "lane": "literature",
        }]
        dispositions = [ResearchDisposition(
            assignment_id="a1", source_item_id="s1",
            decided_at="2026-08-01T00:00:00Z", decision="defer",
            reason_codes=["await_data"], evidence_checked=["paper"],
            research_minutes=10, recheck_after="2026-08-02",
            blocking_fact="the external dataset release",
        )]
        dispatches = [{
            "assignment_id": "a1", "assigned_agent": "literature-scout",
            "priority": "medium", "research_budget_minutes": 45,
            "created_at": "2026-08-03T00:00:00Z",
        }]
        metrics = summarize_research(assignments, dispositions, dispatches)
        self.assertEqual(metrics["dispatch"]["pending_review"], 1)
        self.assertEqual(metrics["dispatch"]["reviewed"], 0)

    def test_latest_dispatch_wins_when_archive_and_reopened_packet_coexist(self):
        assignments = [{"id": "a1", "source_type": "paper", "lane": "literature"}]
        dispositions = [ResearchDisposition(
            assignment_id="a1", source_item_id="s1",
            decided_at="2026-08-02T12:00:00Z", decision="defer",
            reason_codes=["await_data"], evidence_checked=["paper"],
            research_minutes=10, recheck_after="2026-08-03",
            blocking_fact="the external dataset release",
        )]
        dispatches = [{
            "assignment_id": "a1", "created_at": "2026-08-01T12:00:00Z",
            "assigned_agent": "literature-scout", "priority": "medium",
            "research_budget_minutes": 30,
        }, {
            "assignment_id": "a1", "created_at": "2026-08-03T12:00:00Z",
            "assigned_agent": "literature-scout", "priority": "high",
            "research_budget_minutes": 45,
        }]
        metrics = summarize_research(assignments, dispositions, dispatches)
        self.assertEqual(metrics["dispatch"]["pending_review"], 1)
        self.assertEqual(metrics["dispatch"]["allocated_minutes"], 45)
        self.assertEqual(metrics["dispatch"]["by_priority"]["high"]["dispatched"], 1)
