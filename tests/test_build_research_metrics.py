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
            assignments.mkdir()
            dispositions.mkdir()
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
            output = root / "metrics.json"
            self.assertEqual(main([
                "--assignments-dir", str(assignments),
                "--dispositions-dir", str(dispositions),
                "--output", str(output),
            ]), 0)
            metrics = json.loads(output.read_text())
            self.assertEqual(metrics["reviewed"], 1)
            self.assertEqual(metrics["top_rejection_reasons"], {
                "not_replicable": 1,
            })
