from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.readmit_research_assignment import ReadmissionError, main


NOW = "2026-08-08T19:45:00Z"
DESCRIPTION = ("Modifications to Equity Index Perp Style Futures "
               "Market Maker Program")
ASSIGNMENT_ID = "assignment_44c86b342e8ac95d8f67"
SOURCE_ITEM_ID = "src_95f6533902b2cf2e6343"


def _source_item(**changes):
    payload = {
        "id": SOURCE_ITEM_ID,
        "source_type": "regulatory_filing",
        "source_name": "CFTC",
        "external_id": "https://www.cftc.gov/filing/62095",
        "title": "COIN",
        "summary": "COIN: COIN",
        "url": "https://www.cftc.gov/filing/62095",
        "published_at": "07/30/2026",
        "retrieved_at": "2026-08-02T13:46:48.274032Z",
        "content_hash": "c205b7e9",
        "topics": ["rules"],
        "venue_ids": [],
        "product_family": "*",
        "reliability": "primary",
        "rights": "metadata_and_link",
        "metadata": {
            "Organization": "COIN", "Status": "10 Day Review",
            "Filing Description": DESCRIPTION, "Receipt Date": "07/30/2026",
        },
    }
    payload.update(changes)
    return payload


def _assignment(**changes):
    payload = {
        "id": ASSIGNMENT_ID,
        "source_item_id": SOURCE_ITEM_ID,
        "created_at": "2026-08-02T13:47:26.010201Z",
        "score": 93,
        "lane": "market_structure",
        "title": "COIN",
        "source_type": "regulatory_filing",
        "source_name": "CFTC",
        "attribution_key": "CFTC:COIN",
        "venue_ids": [],
        "product_family": "*",
        "legal_decisions": [],
        "assignment": {"agent": "strategy-scout", "instruction": "falsify it"},
        "may_enter_strategy_registry": False,
    }
    payload.update(changes)
    return payload


class TestReadmitResearchAssignment(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.assignments_dir = self.root / "assignments"
        self.sources_dir = self.root / "source_batches"
        self.dispositions_dir = self.root / "dispositions"
        self.output_dir = self.root / "research_triage"
        for directory in (self.assignments_dir, self.sources_dir,
                          self.dispositions_dir, self.output_dir):
            directory.mkdir(parents=True)
        self.write_assignment(_assignment())
        self.write_source(_source_item())
        # The day already spent its only slot, which is exactly the state that
        # keeps a corrected assignment out of the scheduled run.
        (self.output_dir / "ledger.json").write_text(json.dumps({
            "schema_version": 1,
            "dispatched_assignment_ids": ["assignment_other"],
            "daily_allocations": {"2026-08-08": {
                "dispatches": 1, "minutes": 30,
                "by_lane": {"social_signal": 1}}},
            "reclaimed_quarantined_assignment_ids": ["assignment_old"],
        }))
        (self.root / "triage.yaml").write_text(
            "portfolio:\n"
            "  max_dispatches_per_utc_day: 1\n"
            "  max_research_minutes_per_utc_day: 45\n"
            "backpressure:\n"
            "  max_pending_total: 10\n"
            "  max_pending_per_agent: 4\n")

    def write_assignment(self, payload):
        (self.assignments_dir / "batch.json").write_text(json.dumps(
            {"assignments": [payload]}))

    def write_source(self, payload):
        (self.sources_dir / "batch.json").write_text(json.dumps(
            {"items": [payload]}))

    def run_tool(self, *extra, assignment_id=ASSIGNMENT_ID):
        return main([
            "--assignment-id", assignment_id,
            "--reason", "reviewed CFTC filing; title corrected",
            "--config", str(self.root / "triage.yaml"),
            "--assignments-dir", str(self.assignments_dir),
            "--source-batches-dir", str(self.sources_dir),
            "--dispositions-dir", str(self.dispositions_dir),
            "--output-dir", str(self.output_dir),
            "--now", NOW, *extra,
        ])

    def packet_path(self):
        return (self.output_dir / "dispatches" / "strategy-scout"
                / f"{ASSIGNMENT_ID}.json")

    def test_dry_run_plans_without_touching_the_queue(self):
        self.assertEqual(self.run_tool(), 0)
        self.assertFalse(self.packet_path().exists())
        self.assertFalse((self.output_dir / "readmissions").exists())
        ledger = json.loads((self.output_dir / "ledger.json").read_text())
        self.assertNotIn(ASSIGNMENT_ID, ledger["dispatched_assignment_ids"])

    def test_apply_generates_the_packet_with_the_corrected_title(self):
        self.assertEqual(self.run_tool("--apply"), 0)
        packet = json.loads(self.packet_path().read_text())
        self.assertEqual(packet["assignment_id"], ASSIGNMENT_ID)
        self.assertEqual(packet["source_item_id"], SOURCE_ITEM_ID)
        self.assertEqual(packet["assigned_agent"], "strategy-scout")
        self.assertEqual(packet["assignment"]["title"], DESCRIPTION)
        self.assertEqual(packet["source_item"]["title"], DESCRIPTION)
        self.assertEqual(packet["source_item"]["original_title"], "COIN")
        # Identity and dedup state are untouched by the correction.
        self.assertEqual(packet["source_item"]["id"], SOURCE_ITEM_ID)
        self.assertEqual(packet["source_item"]["content_hash"], "c205b7e9")
        self.assertLessEqual(packet["research_budget_minutes"], 30)

        ledger = json.loads((self.output_dir / "ledger.json").read_text())
        self.assertIn(ASSIGNMENT_ID, ledger["dispatched_assignment_ids"])
        self.assertIn("assignment_other", ledger["dispatched_assignment_ids"])
        # The granted slot is charged to the day, not quietly added to it.
        self.assertEqual(
            ledger["daily_allocations"]["2026-08-08"]["dispatches"], 2)
        self.assertEqual(
            ledger["reclaimed_quarantined_assignment_ids"], ["assignment_old"])

        records = sorted((self.output_dir / "readmissions").glob("*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text())
        self.assertEqual(record["assignment_id"], ASSIGNMENT_ID)
        self.assertEqual(record["title_before"], "COIN")
        self.assertEqual(record["title_after"], DESCRIPTION)
        self.assertEqual(record["reason"],
                         "reviewed CFTC filing; title corrected")
        self.assertIs(record["safety"]["invokes_model"], False)
        self.assertIs(record["safety"]["regenerates_queue"], False)

    def test_second_apply_is_refused_rather_than_duplicating_work(self):
        self.assertEqual(self.run_tool("--apply"), 0)
        self.assertEqual(self.run_tool("--apply"), 2)
        self.assertEqual(
            len(list((self.output_dir / "readmissions").glob("*.json"))), 1)

    def test_unknown_and_already_reviewed_targets_fail_closed(self):
        self.assertEqual(self.run_tool(assignment_id="assignment_missing"), 2)
        (self.dispositions_dir / f"{ASSIGNMENT_ID}.json").write_text(json.dumps({
            "assignment_id": ASSIGNMENT_ID, "source_item_id": SOURCE_ITEM_ID,
            "decided_at": "2026-08-08T18:00:00Z", "decision": "reject",
            "reason_codes": ["no_testable_edge_mechanism"],
            "evidence_checked": ["cftc filing"], "research_minutes": 5,
        }))
        self.assertEqual(self.run_tool("--apply"), 2)
        self.assertFalse(self.packet_path().exists())

    def test_a_still_opaque_filing_is_not_re_admitted(self):
        self.write_source(_source_item(metadata={
            "Organization": "COIN", "Status": "10 Day Review"}))
        self.assertEqual(self.run_tool("--apply"), 2)
        self.assertFalse(self.packet_path().exists())

    def test_pinned_config_mismatch_is_refused(self):
        pinned = self.root / "pinned.yaml"
        pinned.write_text("target_assignment_id: assignment_someone_else\n")
        self.assertEqual(
            self.run_tool("--pinned-config", str(pinned)), 2)
        pinned.write_text(f"target_assignment_id: {ASSIGNMENT_ID}\n")
        self.assertEqual(
            self.run_tool("--pinned-config", str(pinned)), 0)

    def test_reason_is_mandatory(self):
        with self.assertRaises(ReadmissionError):
            from scripts.readmit_research_assignment import readmit
            readmit(
                assignment_id=ASSIGNMENT_ID, reason="   ",
                assignments_dir=self.assignments_dir,
                source_batches_dir=self.sources_dir,
                dispositions_dir=self.dispositions_dir,
                output_dir=self.output_dir, triage_config={},
                now=None)
