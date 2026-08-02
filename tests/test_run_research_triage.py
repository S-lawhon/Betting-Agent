from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from scripts.run_research_triage import main


class TestRunResearchTriage(TestCase):
    def test_runner_writes_agent_queues_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "triage.yaml"
            config.write_text("""portfolio:
  max_dispatches_per_utc_day: 2
  max_research_minutes_per_utc_day: 100
  lane_concentration_cap: 1.0
""")
            assignments = root / "assignments"
            sources = root / "sources"
            dispositions = root / "dispositions"
            assignments.mkdir()
            sources.mkdir()
            dispositions.mkdir()
            (assignments / "batch.json").write_text(json.dumps({
                "assignments": [{
                    "id": "a1", "source_item_id": "s1", "score": 55,
                    "lane": "literature", "title": "Calibration paper",
                    "source_type": "paper", "source_name": "arXiv",
                    "attribution_key": "arXiv", "venue_ids": [],
                    "product_family": "*", "legal_decisions": [],
                    "assignment": {"agent": "literature-scout"},
                }],
            }))
            (sources / "batch.json").write_text(json.dumps({"items": [{
                "id": "s1", "url": "https://example.test/paper",
                "summary": "A probability calibration model",
            }]}))
            output = root / "output"
            args = [
                "--config", str(config),
                "--assignments-dir", str(assignments),
                "--source-batches-dir", str(sources),
                "--dispositions-dir", str(dispositions),
                "--strategy-registry", str(root / "missing-registry.json"),
                "--output-dir", str(output),
                "--now", "2026-08-02T14:00:00Z",
            ]
            self.assertEqual(main(args), 0)
            packet = output / "dispatches" / "literature-scout" / "a1.json"
            self.assertTrue(packet.exists())
            self.assertEqual(json.loads(packet.read_text())["source_item"]["id"], "s1")
            self.assertEqual(main(args), 0)
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(manifest["dispatched"], 0)
            self.assertEqual(manifest["already_dispatched"], 1)

            (dispositions / "a1.json").write_text(json.dumps({
                "assignment_id": "a1", "source_item_id": "s1",
                "decided_at": "2026-08-02T15:00:00Z", "decision": "reject",
                "reason_codes": ["no_mechanism"],
                "evidence_checked": ["paper"], "research_minutes": 15,
            }))
            self.assertEqual(main(args), 0)
            self.assertFalse(packet.exists())
            archived = output / "dispatch_archive" / "literature-scout" / "a1.json"
            self.assertTrue(archived.exists())
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(manifest["dispatches_archived_completed"], 1)

    def test_runner_reports_malformed_defer_date_without_dispatching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "triage.yaml"
            config.write_text("portfolio: {}\n")
            assignments = root / "assignments"
            sources = root / "sources"
            dispositions = root / "dispositions"
            assignments.mkdir()
            sources.mkdir()
            dispositions.mkdir()
            (assignments / "batch.json").write_text(json.dumps({
                "assignments": [{
                    "id": "a1", "source_item_id": "s1", "score": 55,
                    "lane": "literature", "title": "Calibration paper",
                    "source_type": "paper", "source_name": "arXiv",
                    "attribution_key": "arXiv", "venue_ids": [],
                    "product_family": "*", "legal_decisions": [],
                    "assignment": {"agent": "literature-scout"},
                }],
            }))
            (sources / "batch.json").write_text(json.dumps({"items": []}))
            (dispositions / "a1.json").write_text(json.dumps({
                "assignment_id": "a1", "source_item_id": "s1",
                "decided_at": "2026-08-01T12:00:00Z", "decision": "defer",
                "reason_codes": ["await_data"], "evidence_checked": ["paper"],
                "research_minutes": 10, "recheck_after": "not-a-date",
            }))
            output = root / "output"
            result = main([
                "--config", str(config),
                "--assignments-dir", str(assignments),
                "--source-batches-dir", str(sources),
                "--dispositions-dir", str(dispositions),
                "--strategy-registry", str(root / "missing-registry.json"),
                "--output-dir", str(output),
                "--now", "2026-08-02T14:00:00Z",
            ])
            self.assertEqual(result, 2)
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(len(manifest["invalid_dispositions"]), 1)
            self.assertEqual(manifest["dispatched"], 0)
