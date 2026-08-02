from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from scripts.run_research_intake import main
from src.research_intake import SourceItem


class TestRunResearchIntake(TestCase):
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
