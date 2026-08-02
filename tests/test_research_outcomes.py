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
        metrics = summarize_research(assignments, dispositions)
        self.assertEqual(metrics["unreviewed"], 1)
        self.assertEqual(metrics["minutes_per_advance"], 40)
        self.assertEqual(metrics["by_source_type"]["paper"]["advance_rate"], 0.5)
        self.assertEqual(metrics["by_source_name"]["arXiv"]["assigned"], 2)
        self.assertEqual(metrics["by_attribution"]["X:@analyst"]["reject"], 1)
        self.assertEqual(metrics["top_rejection_reasons"], {"no_mechanism": 1})
