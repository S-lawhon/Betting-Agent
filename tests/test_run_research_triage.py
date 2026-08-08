from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts.run_research_triage import main


class TestRunResearchTriage(TestCase):
    def test_quarantine_reclaims_same_day_capacity_for_valid_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "triage.yaml"
            config.write_text("""portfolio:
  max_dispatches_per_utc_day: 1
  max_research_minutes_per_utc_day: 60
  lane_concentration_cap: 1.0
""")
            memory = root / "memory.yaml"
            memory.write_text("hypotheses: []\n")
            assignments = root / "assignments"
            sources = root / "sources"
            dispositions = root / "dispositions"
            for path in (assignments, sources, dispositions):
                path.mkdir()
            (assignments / "batch.json").write_text(json.dumps({"assignments": [
                {"id": "a1", "source_item_id": "s1", "score": 80,
                 "lane": "new_products", "title": "Resolved market",
                 "source_type": "venue_market", "source_name": "Venue",
                 "legal_decisions": [], "assignment": {"agent": "strategy-scout"}},
                {"id": "a2", "source_item_id": "s2", "score": 70,
                 "lane": "literature", "title": "Calibration model paper",
                 "source_type": "paper", "source_name": "arXiv",
                 "legal_decisions": [], "assignment": {"agent": "literature-scout"}},
            ]}))
            source1 = {
                "id": "s1", "source_type": "venue_market", "source_name": "Venue",
                "external_id": "m1", "title": "Resolved market", "summary": "",
                "url": "https://example.test/m1", "published_at": None,
                "retrieved_at": "2026-08-02T12:00:00Z", "content_hash": "h1",
                "topics": [], "venue_ids": [], "product_family": "sports",
                "reliability": "primary", "rights": "metadata_and_link",
                "metadata": {"status": "RESOLVED"},
            }
            source2 = dict(source1) | {
                "id": "s2", "source_type": "paper", "source_name": "arXiv",
                "external_id": "p1", "title": "Calibration model paper",
                "url": "https://arxiv.org/abs/p1", "content_hash": "h2",
                "metadata": {}, "product_family": "*",
            }
            (sources / "batch.json").write_text(json.dumps({"items": [source1, source2]}))
            output = root / "output"
            pending = output / "dispatches/strategy-scout/a1.json"
            pending.parent.mkdir(parents=True)
            pending.write_text(json.dumps({
                "id": "d1", "assignment_id": "a1", "source_item_id": "s1",
                "assigned_agent": "strategy-scout", "created_at": "2026-08-02T13:00:00Z",
                "research_budget_minutes": 30,
                "assignment": {"lane": "new_products"},
            }))
            (output / "ledger.json").write_text(json.dumps({
                "dispatched_assignment_ids": ["a1"],
                "daily_allocations": {"2026-08-02": {
                    "dispatches": 1, "minutes": 30,
                    "by_lane": {"new_products": 1},
                }},
            }))
            args = [
                "--config", str(config), "--research-memory", str(memory),
                "--assignments-dir", str(assignments),
                "--source-batches-dir", str(sources),
                "--dispositions-dir", str(dispositions),
                "--strategy-registry", str(root / "missing.json"),
                "--output-dir", str(output), "--now", "2026-08-02T14:00:00Z",
            ]
            self.assertEqual(main(args), 0)
            self.assertTrue((output / "dispatches/literature-scout/a2.json").exists())
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(manifest["quarantine_capacity_reclaimed"]["dispatches"], 1)
            self.assertEqual(manifest["dispatched"], 1)
            self.assertEqual(main(args), 0)
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(manifest["quarantine_capacity_reclaimed"]["dispatches"], 0)

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
                "--now", "2026-08-02T14:00:00Z", "--no-evidence",
            ])
            self.assertEqual(result, 2)
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(len(manifest["invalid_dispositions"]), 1)
            self.assertEqual(manifest["dispatched"], 0)

    def test_runner_quarantines_legacy_dispatch_that_fails_quality_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "triage.yaml"
            config.write_text("portfolio: {}\n")
            memory = root / "memory.yaml"
            memory.write_text("hypotheses: []\n")
            assignments = root / "assignments"
            sources = root / "sources"
            dispositions = root / "dispositions"
            assignments.mkdir()
            sources.mkdir()
            dispositions.mkdir()
            output = root / "output"
            pending = output / "dispatches" / "strategy-scout" / "a1.json"
            pending.parent.mkdir(parents=True)
            pending.write_text(json.dumps({
                "assignment_id": "a1", "source_item_id": "s1",
            }))
            (sources / "batch.json").write_text(json.dumps({"items": [{
                    "id": "s1", "source_type": "venue_market",
                    "source_name": "Polymarket US", "external_id": "m1",
                    "title": "Resolved basketball game", "summary": "",
                    "url": "https://example.test/m1",
                    "retrieved_at": "2026-08-01T12:00:00Z",
                    "published_at": None, "authors": [], "topics": [],
                    "venue_ids": ["polymarket-us"],
                    "product_family": "sports", "reliability": "unknown",
                    "rights": "public_metadata", "metadata": {
                        "status": "MARKET_STATUS_RESOLVED",
                        "end_at": "2026-01-14T00:00:00Z",
                    }, "content_hash": "hash",
                }]}))
            result = main([
                "--config", str(config), "--research-memory", str(memory),
                "--assignments-dir", str(assignments),
                "--source-batches-dir", str(sources),
                "--dispositions-dir", str(dispositions),
                "--strategy-registry", str(root / "missing-registry.json"),
                "--output-dir", str(output),
                "--now", "2026-08-02T14:00:00Z", "--no-evidence",
            ])
            self.assertEqual(result, 0)
            self.assertFalse(pending.exists())
            self.assertTrue((
                output / "dispatch_quarantine" / "strategy-scout" / "a1.json"
            ).exists())
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertEqual(manifest["dispatches_quarantined_quality"], 1)
            self.assertEqual(
                manifest["quarantined_assignment_ids"]["a1"],
                "terminal_market")

    def test_dispatched_packets_carry_resolved_public_evidence(self):
        class FakeResolver:
            name = "fake"
            seen = []

            def applies_to(self, assignment, source_item):
                return True

            def resolve(self, assignment, source_item, *, now=None):
                FakeResolver.seen.append(str(assignment.get("id")))
                return {"status": "measured", "source": "fake",
                        "measured_at": "2026-08-02T14:00:00Z",
                        "facts": {"series_ticker": "KXTEST",
                                  "fees": {"maker_charges_fee": False}},
                        "notes": []}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "triage.yaml"
            config.write_text("portfolio:\n  max_dispatches_per_utc_day: 1\n")
            assignments = root / "assignments"
            sources = root / "sources"
            dispositions = root / "dispositions"
            for path in (assignments, sources, dispositions):
                path.mkdir()
            (assignments / "batch.json").write_text(json.dumps({"assignments": [
                {"id": "a1", "source_item_id": "s1", "score": 80,
                 "lane": "information_latency",
                 "title": "Spread mispricing on a listed series",
                 "source_type": "venue_market", "source_name": "Kalshi",
                 "venue_ids": ["kalshi"], "legal_decisions": [],
                 "assignment": {"agent": "strategy-scout"}},
                # A second, lower-ranked candidate proves enrichment runs only
                # for packets actually dispatched, not the whole pool.
                {"id": "a2", "source_item_id": "s2", "score": 10,
                 "lane": "information_latency", "title": "Another listed series",
                 "source_type": "venue_market", "source_name": "Kalshi",
                 "venue_ids": ["kalshi"], "legal_decisions": [],
                 "assignment": {"agent": "strategy-scout"}},
            ]}))
            base = {
                "source_type": "venue_market", "source_name": "Kalshi",
                "title": "Spread", "summary": "census seed", "url": "",
                "published_at": None, "retrieved_at": "2026-08-02T12:00:00Z",
                "topics": [], "venue_ids": ["kalshi"],
                "product_family": "sports", "reliability": "primary",
                "rights": "metadata_and_link",
                "metadata": {"market_family": "KXTEST", "census_run_id": "c1"},
            }
            (sources / "batch.json").write_text(json.dumps({"items": [
                dict(base, id="s1", external_id="m1", content_hash="h1"),
                dict(base, id="s2", external_id="m2", content_hash="h2"),
            ]}))
            output = root / "output"
            with patch("scripts.run_research_triage.build_resolvers",
                       return_value=[FakeResolver()]) as built:
                result = main([
                    "--config", str(config),
                    "--assignments-dir", str(assignments),
                    "--source-batches-dir", str(sources),
                    "--dispositions-dir", str(dispositions),
                    "--strategy-registry", str(root / "missing-registry.json"),
                    "--output-dir", str(output),
                    "--now", "2026-08-02T14:00:00Z",
                ])
            self.assertEqual(result, 0)
            built.assert_called_once_with(True)
            self.assertEqual(FakeResolver.seen, ["a1"])
            packet = json.loads((
                output / "dispatches" / "strategy-scout" / "a1.json").read_text())
            self.assertEqual(packet["market_evidence"]["status"], "measured")
            self.assertEqual(
                packet["completion_contract"]["supplied_evidence"],
                ["fees", "series_ticker"])
            manifest = json.loads((output / "latest_manifest.json").read_text())
            self.assertIs(manifest["evidence_enabled"], True)
            self.assertEqual(
                manifest["evidence_status_by_assignment"], {"a1": "measured"})
