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
            assignments.mkdir()
            dispositions.mkdir()
            dispatches.mkdir(parents=True)
            (assignments / "batch.json").write_text(json.dumps({
                "assignments": [{
                    "id": "a1", "source_type": "paper", "lane": "literature",
                }],
            }))
            (dispositions / "a1.json").write_text(json.dumps({
                "assignment_id": "a1", "source_item_id": "s1",
                "decided_at": "2026-08-01T00:00:00Z", "decision": "reject",
                "reason_codes": ["not_replicable"],
                "evidence_checked": ["code"], "research_minutes": 20,
            }))
            (dispatches / "a1.json").write_text(json.dumps({
                "assignment_id": "a1", "assigned_agent": "literature-scout",
                "priority": "high", "research_budget_minutes": 45,
            }))
            intake_manifest = root / "latest_manifest.json"
            intake_manifest.write_text(json.dumps({"x_usage": {
                "month": "2026-08", "estimated_cost_month_usd": 1.25,
                "post_reads_month": 75, "user_reads_month": 70,
                "estimate_is_conservative": True, "status": "completed",
            }}))
            output = root / "metrics.json"
            self.assertEqual(main([
                "--assignments-dir", str(assignments),
                "--dispositions-dir", str(dispositions),
                "--dispatches-dir", str(dispatches.parent),
                "--intake-manifest", str(intake_manifest),
                "--output", str(output),
            ]), 0)
            metrics = json.loads(output.read_text())
            self.assertEqual(metrics["reviewed"], 1)
            self.assertEqual(metrics["top_rejection_reasons"], {
                "not_replicable": 1,
            })
            self.assertEqual(metrics["dispatch"]["dispatched"], 1)
            self.assertEqual(metrics["funnel"]["dispatched_reviewed"], 1)
            self.assertEqual(metrics["x_pilot"]["estimated_cost_usd"], 1.25)
