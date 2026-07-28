"""Tests for src/trade_log_schema.py."""
import json
import tempfile
import unittest
from pathlib import Path

from src.trade_log_schema import TradeLogSchema


class TestNormalize(unittest.TestCase):

    def test_renames_market_ticker(self):
        entry = {"market_ticker": "KXNCAA-DUC", "action": "PLACED"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["market_id"], "KXNCAA-DUC")
        # Original key preserved
        self.assertEqual(result["market_ticker"], "KXNCAA-DUC")

    def test_renames_kalshi_prob(self):
        entry = {"kalshi_prob": 0.55, "action": "PLACED"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["venue_prob"], 0.55)

    def test_applies_defaults(self):
        entry = {"action": "PLACED", "fingerprint": "fp1"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["pod_id"], "P-001")
        self.assertEqual(result["pod_name"], "Kalshi vs Sharp's Strategy")
        self.assertEqual(result["venue"], "kalshi")

    def test_does_not_overwrite_existing(self):
        entry = {"pod_id": "P-006", "pod_name": "Polymarket Consensus", "action": "PLACED"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["pod_id"], "P-006")
        self.assertEqual(result["pod_name"], "Polymarket Consensus")

    def test_coerces_numeric_fields(self):
        entry = {"fair_prob": "0.55", "position_size_usd": "100.50", "action": "PLACED"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["fair_prob"], 0.55)
        self.assertEqual(result["position_size_usd"], 100.50)

    def test_pnl_fallback(self):
        entry = {"pnl": 42.50, "action": "WIN"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["pnl_usd"], 42.50)

    def test_outcome_from_action(self):
        entry = {"action": "WIN", "fingerprint": "fp1"}
        result = TradeLogSchema.normalize(entry)
        self.assertEqual(result["outcome"], "WIN")


def _placed(**overrides):
    """A minimal PLACED entry that validates clean."""
    entry = {
        "action": "PLACED",
        "fingerprint": "fp1",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "pod_id": "P-001",
        "venue": "kalshi",
        "market_id": "KXNCAA-DUC",
        "side": "YES",
        "fill_price": 0.42,
    }
    entry.update(overrides)
    return entry


class TestValidate(unittest.TestCase):

    def test_valid_placed_entry(self):
        issues = TradeLogSchema.validate(_placed())
        self.assertEqual(issues, [])

    # ── fill_price: the 356-row P-014 regression ─────────────────────
    # P-014 wrote `fill_price: null` on every PLACED row from 2026-03-28
    # to 2026-07-28 and validation passed all of them, because the old
    # check only tested "numeric IF not None".  No price means no fee
    # (0.07*P*(1-P)) and no contract count (size / price), so the pod was
    # the one pod that could not be billed.

    def test_null_fill_price_on_placed_is_an_issue(self):
        issues = TradeLogSchema.validate(_placed(fill_price=None))
        self.assertTrue(
            any("fill_price" in i for i in issues),
            f"null fill_price must be flagged, got {issues!r}",
        )

    def test_absent_fill_price_on_placed_is_an_issue(self):
        entry = _placed()
        del entry["fill_price"]
        issues = TradeLogSchema.validate(entry)
        self.assertTrue(any("fill_price" in i for i in issues))

    def test_zero_fill_price_on_placed_is_an_issue(self):
        """contracts = size / fill_price — zero is as unusable as null."""
        issues = TradeLogSchema.validate(_placed(fill_price=0.0))
        self.assertTrue(any("fill_price" in i for i in issues))

    def test_non_numeric_fill_price_on_placed_is_an_issue(self):
        issues = TradeLogSchema.validate(_placed(fill_price="cheap"))
        self.assertTrue(any("non-numeric fill_price" in i for i in issues))

    def test_fill_price_not_required_on_settlement_rows(self):
        """Settlement rows carry no fill; only PLACED is checked."""
        issues = TradeLogSchema.validate(
            {"action": "WIN", "fingerprint": "fp1", "pnl_usd": 3.0}
        )
        self.assertEqual(issues, [])

    def test_missing_required_fields(self):
        entry = {"action": "PLACED"}
        issues = TradeLogSchema.validate(entry)
        self.assertGreater(len(issues), 0)
        self.assertTrue(any("fingerprint" in i for i in issues))

    def test_settlement_without_id(self):
        entry = {"action": "WIN"}
        issues = TradeLogSchema.validate(entry)
        self.assertTrue(any("fingerprint or market_id" in i for i in issues))

    def test_settlement_with_fingerprint_ok(self):
        entry = {"action": "WIN", "fingerprint": "fp1"}
        issues = TradeLogSchema.validate(entry)
        self.assertEqual(issues, [])

    def test_non_numeric_position_size(self):
        entry = {
            "action": "PLACED",
            "fingerprint": "fp1",
            "timestamp_utc": "2026-01-01",
            "pod_id": "P-001",
            "venue": "kalshi",
            "market_id": "X",
            "side": "YES",
            "position_size_usd": "not_a_number",
        }
        issues = TradeLogSchema.validate(entry)
        self.assertTrue(any("non-numeric" in i for i in issues))


class TestMigrateFile(unittest.TestCase):

    def test_normalizes_and_rewrites(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            entries = [
                {"action": "PLACED", "market_ticker": "KXNCAA", "fingerprint": "fp1",
                 "timestamp_utc": "2026-01-01", "side": "YES"},
                {"action": "WIN", "fingerprint": "fp1", "pnl": 50.0},
            ]
            with open(log, "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

            result = TradeLogSchema.migrate_file(log)
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["normalized"], 2)

            # Verify rewritten file
            with open(log) as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(lines[0]["market_id"], "KXNCAA")
            self.assertEqual(lines[0]["pod_id"], "P-001")
            self.assertEqual(lines[1]["pnl_usd"], 50.0)

    def test_dry_run_no_changes(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            original = {"action": "PLACED", "market_ticker": "X", "fingerprint": "fp"}
            with open(log, "w") as f:
                f.write(json.dumps(original) + "\n")

            result = TradeLogSchema.migrate_file(log, dry_run=True)
            self.assertEqual(result["total"], 1)

            # File should be unchanged
            with open(log) as f:
                data = json.loads(f.read().strip())
            self.assertNotIn("market_id", data)

    def test_missing_file(self):
        result = TradeLogSchema.migrate_file(Path("/nonexistent/file.jsonl"))
        self.assertEqual(result["total"], 0)


if __name__ == "__main__":
    unittest.main()
