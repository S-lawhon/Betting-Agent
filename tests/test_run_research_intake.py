from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts.run_research_intake import _collect_x, collect_live, main
from src.research_intake import SourceItem


X_CONFIG = {
    "enabled": True,
    "max_results_per_query": 25,
    "max_queries_per_run": 3,
    "queries": ["query one", "query two", "query three"],
    "budget": {
        "max_runs_per_utc_day": 1,
        "post_read_usd": 0.005,
        "user_read_usd": 0.010,
        "warning_usd": 40.0,
        "halt_usd": 45.0,
        "x_hard_limit_usd": 50.0,
    },
}


class _FakeXCollector:
    calls = []

    def __init__(self, token):
        self.token = token

    def collect(self, query, *, max_results, now):
        self.calls.append((query, max_results, now))
        return [SourceItem.create(
            source_type="social", source_name="X",
            external_id=f"{query}-post", title=f"Idea from {query}",
            url="https://x.com/researcher/status/1",
            retrieved_at=now.isoformat(),
            metadata={"author_id": f"author-{query}"},
        )]


class TestRunResearchIntake(TestCase):
    @patch("scripts.run_research_intake.FeedCollector.fetch", return_value=[])
    def test_zero_item_academic_feed_is_reported_as_degraded(self, _fetch):
        items, errors, counts, telemetry, changed = collect_live({
            "collectors": {"feeds": [{
                "id": "academic", "enabled": True, "name": "Academic",
                "type": "paper", "url": "https://example.test/rss",
            }]},
        }, now=_utc("2026-08-03T04:05:00Z"))
        self.assertEqual(items, [])
        self.assertEqual(counts["feed:academic:raw"], 0)
        self.assertEqual(errors[0]["source"], "feed:academic")
        self.assertIn("zero raw items", errors[0]["error"])
        self.assertEqual(telemetry["status"], "not_requested")
        self.assertFalse(changed)

    @patch("scripts.run_research_intake.FeedCollector.fetch", return_value=[])
    def test_weekend_empty_arxiv_feed_is_expected_not_an_error(self, _fetch):
        items, errors, counts, _, _ = collect_live({
            "collectors": {"feeds": [{
                "id": "arxiv", "enabled": True, "name": "arXiv",
                "type": "paper", "url": "https://rss.arxiv.org/rss/q-fin.TR",
            }]},
        }, now=_utc("2026-08-02T04:05:00Z"))
        self.assertEqual(items, [])
        self.assertEqual(errors, [])
        self.assertEqual(counts["feed:arxiv:raw"], 0)
        self.assertEqual(counts["feed:arxiv:expected_empty"], 1)

    def test_offline_run_writes_all_artifacts_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text("intake:\n  max_assignments: 5\n")
            eligibility = root / "venues.yaml"
            eligibility.write_text("""schema_version: 1
defaults:
  unknown_status: pending_review
venues:
  - id: venue
    status: pending_review
    research_allowed: true
    execution_allowed: false
    verified_at: 2026-08-01
    expires_at: 2026-09-01
    products: []
""")
            item = SourceItem.create(
                source_type="paper", source_name="Test Papers",
                external_id="paper-1", title="A testable market effect",
                url="https://example.test/paper-1",
                retrieved_at="2026-08-01T12:00:00Z",
            )
            offline = root / "items.json"
            offline.write_text(json.dumps({"items": [item.to_dict()]}))
            output = root / "output"
            rc = main([
                "--config", str(config), "--eligibility", str(eligibility),
                "--offline-items", str(offline), "--output-dir", str(output),
                "--now", "2026-08-01T12:00:00Z",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue((output / "ledger.json").exists())
            self.assertTrue((output / "latest_manifest.json").exists())
            assignment_path = output / "assignments" / "20260801T120000Z.json"
            assignment = json.loads(assignment_path.read_text())
            self.assertEqual(assignment["kind"], "research_assignment_inbox")
            self.assertEqual(assignment["assignments"][0]["lane"], "literature")
            self.assertFalse(
                assignment["assignments"][0]["may_enter_strategy_registry"])

    def test_x_config_and_runtime_flag_are_both_required(self):
        usage = {"schema_version": 1, "days": {}}
        disabled = {**X_CONFIG, "enabled": False}
        _, errors, _, telemetry, changed = _collect_x(
            disabled, now=_utc("2026-08-02T04:05:00Z"), usage=usage)
        self.assertEqual(telemetry["status"], "disabled_by_config")
        self.assertTrue(errors)
        self.assertFalse(changed)

    @patch.dict(os.environ, {"X_BEARER_TOKEN": "test-token"})
    @patch("scripts.run_research_intake.XRecentSearchCollector", _FakeXCollector)
    def test_x_usage_is_counted_and_second_daily_run_is_skipped(self):
        _FakeXCollector.calls = []
        usage = {"schema_version": 1, "days": {}}
        items, errors, counts, telemetry, changed = _collect_x(
            X_CONFIG, now=_utc("2026-08-02T04:05:00Z"), usage=usage)
        self.assertEqual(len(items), 3)
        self.assertEqual(errors, [])
        self.assertEqual(sum(counts.values()), 3)
        self.assertEqual(telemetry["status"], "completed")
        self.assertEqual(telemetry["post_reads_this_run"], 3)
        self.assertEqual(telemetry["user_reads_this_run"], 3)
        self.assertEqual(telemetry["estimated_cost_this_run_usd"], 0.045)
        self.assertTrue(changed)

        items, errors, counts, telemetry, changed = _collect_x(
            X_CONFIG, now=_utc("2026-08-02T12:00:00Z"), usage=usage)
        self.assertEqual(items, [])
        self.assertEqual(errors, [])
        self.assertEqual(counts, {})
        self.assertEqual(telemetry["status"], "skipped_daily_limit")
        self.assertFalse(changed)
        self.assertEqual(len(_FakeXCollector.calls), 3)

    @patch.dict(os.environ, {"X_BEARER_TOKEN": "test-token"})
    @patch("scripts.run_research_intake.XRecentSearchCollector", _FakeXCollector)
    def test_x_halts_before_conservative_projection_exceeds_limit(self):
        usage = {"schema_version": 1, "days": {
            "2026-08-01": {
                "runs": 1, "requests": 3, "post_reads": 100,
                "user_reads": 100, "estimated_cost_usd": 44.0,
            },
        }}
        items, errors, counts, telemetry, changed = _collect_x(
            X_CONFIG, now=_utc("2026-08-02T04:05:00Z"), usage=usage)
        self.assertEqual(items, [])
        self.assertEqual(errors, [])
        self.assertEqual(counts, {})
        self.assertEqual(telemetry["status"], "halted_monthly_budget")
        self.assertEqual(telemetry["projected_month_cost_usd"], 45.125)
        self.assertFalse(changed)


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
