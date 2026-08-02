from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from scripts.build_research_metrics import main


class TestBuildResearchMetrics(TestCase):
    def test_builds_metrics_from_artifact_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assignments = root / "assignments"
            dispositions = root / "dispositions"
            dispatches = root / "dispatches" / "literature-scout"
            claims = root / "execution" / "claim_archive" / "literature-scout"
            assignments.mkdir()
            dispositions.mkdir()
            dispatches.mkdir(parents=True)
            claims.mkdir(parents=True)
            (assignments / "batch.json").write_text(json.dumps({
                "assignments": [{
                    "id": "a1", "source_type": "paper", "lane": "literature",
                }],
            }))
            (dispositions / "a1.json").write_text(json.dumps({
                "assignment_id": "a1", "source_item_id": "s1",
                "decided_at": "2026-08-01T18:00:00Z", "decision": "reject",
                "reason_codes": ["not_replicable"],
                "evidence_checked": ["code"], "research_minutes": 20,
            }))
            (dispatches / "a1.json").write_text(json.dumps({
                "assignment_id": "a1", "assigned_agent": "literature-scout",
                "priority": "high", "research_budget_minutes": 45,
                "created_at": "2026-08-01T12:00:00Z",
            }))
            (claims / "a1--claim-1.json").write_text(json.dumps({
                "assignment_id": "a1", "assigned_agent": "literature-scout",
                "worker_id": "worker-1", "claimed_at": "2026-08-01T13:00:00Z",
                "lease_expires_at": "2026-08-01T14:30:00Z",
                "status": "completed",
            }))
            (dispatches.parent / "latest_manifest.json").write_text(json.dumps({
                "quality_blocked": 2,
                "quality_blocked_assignment_ids": {"a2": "terminal_market"},
                "dispatches_quarantined_quality": 1,
                "quarantined_assignment_ids": {"a3": "prior_research_killed"},
            }))
            quarantine = (
                dispatches.parent / "dispatch_quarantine" / "strategy-scout")
            quarantine.mkdir(parents=True)
            for assignment_id in ("a3", "a4", "a5"):
                (quarantine / f"{assignment_id}.json").write_text("{}")
            intake_manifest = root / "latest_manifest.json"
            intake_manifest.write_text(json.dumps({
                "x_usage": {
                    "month": "2026-08", "estimated_cost_month_usd": 1.25,
                    "post_reads_month": 75, "user_reads_month": 70,
                    "estimate_is_conservative": True, "status": "completed",
                },
                "collector_counts": {
                    "feed:arxiv_qfin_trading:raw": 0,
                    "feed:arxiv_qfin_statistics:raw": 3,
                },
                "collector_errors": [{
                    "source": "feed:arxiv_qfin_trading",
                    "error": "enabled academic feed returned zero raw items",
                }],
                "quality_rejected": 4,
                "quality_rejection_reasons": {"expired_market": 4},
            }))
            output = root / "metrics.json"
            self.assertEqual(main([
                "--assignments-dir", str(assignments),
                "--dispositions-dir", str(dispositions),
                "--dispatches-dir", str(dispatches.parent),
                "--execution-dir", str(root / "execution"),
                "--intake-manifest", str(intake_manifest),
                "--output", str(output),
                "--now", "2026-08-02T00:00:00Z",
            ]), 0)
            metrics = json.loads(output.read_text())
            self.assertEqual(metrics["reviewed"], 1)
            self.assertEqual(metrics["top_rejection_reasons"], {
                "not_replicable": 1,
            })
            self.assertEqual(metrics["dispatch"]["dispatched"], 1)
            self.assertEqual(metrics["funnel"]["dispatched_reviewed"], 1)
            self.assertEqual(metrics["x_pilot"]["estimated_cost_usd"], 1.25)
            self.assertEqual(metrics["collector_health"]["status"], "degraded")
            self.assertEqual(
                metrics["collector_health"]["academic_feed_items_raw"], 3)
            self.assertEqual(metrics["collector_health"]["zero_academic_feeds"], [
                "feed:arxiv_qfin_trading",
            ])
            self.assertEqual(metrics["quality_control"]["intake_rejected"], 4)
            self.assertEqual(metrics["quality_control"]["triage_blocked"], 2)
            self.assertEqual(
                metrics["quality_control"]["legacy_dispatches_quarantined"], 3)
            self.assertEqual(
                metrics["quality_control"][
                    "legacy_dispatches_quarantined_latest_run"], 1)
            operations = metrics["operations"]
            self.assertFalse(
                operations["semantics"]["agent_invocation_tracked"])
            self.assertEqual(operations["activity_24h"]["reviewed"], 1)
            self.assertEqual(operations["activity_24h"]["started"], 1)
            self.assertEqual(operations["activity_24h"]["reject"], 1)
            self.assertEqual(
                operations["agents"]["literature-scout"]["queue_state"],
                "idle")

    def test_operations_report_pending_and_overdue_per_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assignments = root / "assignments"
            dispositions = root / "dispositions"
            dispatches = root / "triage" / "dispatches" / "social-scout"
            execution = root / "execution" / "claims" / "social-scout"
            assignments.mkdir()
            dispositions.mkdir()
            dispatches.mkdir(parents=True)
            execution.mkdir(parents=True)
            (assignments / "batch.json").write_text(json.dumps({
                "assignments": [
                    {"id": "a1", "source_type": "social", "lane": "social_signal"},
                    {"id": "a2", "source_type": "social", "lane": "social_signal"},
                ],
            }))
            for assignment_id, created_at in (
                    ("a1", "2026-08-01T00:00:00Z"),
                    ("a2", "2026-08-03T00:00:00Z")):
                (dispatches / f"{assignment_id}.json").write_text(json.dumps({
                    "assignment_id": assignment_id,
                    "assigned_agent": "social-scout",
                    "priority": "high", "research_budget_minutes": 20,
                    "created_at": created_at,
                }))
            (execution / "a2.json").write_text(json.dumps({
                "assignment_id": "a2", "assigned_agent": "social-scout",
                "worker_id": "worker-1", "claimed_at": "2026-08-03T11:00:00Z",
                "lease_expires_at": "2026-08-03T13:00:00Z",
                "status": "claimed",
            }))
            output = root / "metrics.json"
            self.assertEqual(main([
                "--assignments-dir", str(assignments),
                "--dispositions-dir", str(dispositions),
                "--dispatches-dir", str(root / "triage"),
                "--execution-dir", str(root / "execution"),
                "--intake-manifest", str(root / "missing.json"),
                "--output", str(output),
                "--now", "2026-08-03T12:00:00Z",
            ]), 0)
            operations = json.loads(output.read_text())["operations"]
            agent = operations["agents"]["social-scout"]
            self.assertEqual(agent["pending"], 2)
            self.assertEqual(agent["overdue"], 1)
            self.assertEqual(agent["dispatched_24h"], 1)
            self.assertEqual(agent["started_24h"], 1)
            self.assertEqual(agent["in_progress"], 1)
            self.assertEqual(agent["oldest_pending_age_hours"], 60.0)
            self.assertEqual(agent["queue_state"], "in_progress")

    def test_expected_weekend_empty_feed_remains_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("assignments", "dispositions", "triage"):
                (root / name).mkdir()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "collector_counts": {
                    "feed:arxiv:raw": 0,
                    "feed:arxiv:expected_empty": 1,
                },
                "collector_errors": [],
            }))
            output = root / "metrics.json"
            self.assertEqual(main([
                "--assignments-dir", str(root / "assignments"),
                "--dispositions-dir", str(root / "dispositions"),
                "--dispatches-dir", str(root / "triage"),
                "--intake-manifest", str(manifest),
                "--output", str(output),
                "--now", "2026-08-02T12:00:00Z",
            ]), 0)
            health = json.loads(output.read_text())["collector_health"]
            self.assertEqual(health["status"], "healthy")
            self.assertEqual(
                health["expected_empty_academic_feeds"], ["feed:arxiv"])
            self.assertEqual(health["unexpected_zero_academic_feeds"], [])
