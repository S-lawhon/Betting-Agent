"""
tests/test_trade_store.py
─────────────────────────
Unit tests for the centralised TradeStore.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase


from src.trade_store import TradeStore, normalize_entry


def _now():
    return datetime(2026, 3, 12, 15, 0, 0, tzinfo=timezone.utc)


def _make_placed(fp="fp1", pod_id="P-001", ticker="KXNBAGAME-ABC", side="YES",
                 position_size=100.0, venue="kalshi", ts="2026-03-12T14:00:00"):
    return {
        "fingerprint": fp,
        "action": "PLACED",
        "pod_id": pod_id,
        "pod_name": "Test Pod",
        "venue": venue,
        "market_id": ticker,
        "side": side,
        "fair_prob": 0.55,
        "venue_prob": 0.50,
        "edge_pct": 0.10,
        "position_size_usd": position_size,
        "timestamp_utc": ts,
        "mode": "paper",
    }


def _make_settlement(fp="fp1", outcome="WIN", pnl=42.50, ticker="KXNBAGAME-ABC",
                     pod_id="P-001", ts="2026-03-12T16:00:00"):
    return {
        "fingerprint": fp,
        "outcome": outcome,
        "pnl_usd": pnl,
        "market_id": ticker,
        "pod_id": pod_id,
        "settled_at_utc": ts,
        "timestamp_utc": ts,
    }


class TestNormalizeEntry(TestCase):
    """Test the field normalisation function."""

    def test_market_ticker_aliased_to_market_id(self):
        entry = {"market_ticker": "KXNBA-123"}
        result = normalize_entry(entry)
        self.assertEqual(result["market_id"], "KXNBA-123")
        # Original key preserved
        self.assertEqual(result["market_ticker"], "KXNBA-123")

    def test_kalshi_prob_aliased_to_venue_prob(self):
        entry = {"kalshi_prob": 0.55}
        result = normalize_entry(entry)
        self.assertEqual(result["venue_prob"], 0.55)

    def test_existing_canonical_not_overwritten(self):
        entry = {"market_ticker": "OLD", "market_id": "NEW"}
        result = normalize_entry(entry)
        self.assertEqual(result["market_id"], "NEW")

    def test_legacy_defaults_applied(self):
        result = normalize_entry({})
        self.assertEqual(result["pod_id"], "P-001")
        self.assertEqual(result["pod_name"], "Kalshi vs Sharp's Strategy")

    def test_existing_pod_id_not_overwritten(self):
        result = normalize_entry({"pod_id": "P-006", "pod_name": "Custom"})
        self.assertEqual(result["pod_id"], "P-006")
        self.assertEqual(result["pod_name"], "Custom")


class TestTradeStoreLoad(TestCase):
    """Test loading from JSONL files."""

    def _write_log(self, entries):
        """Write entries to a temp JSONL file and return the path."""
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        return Path(path)

    def test_load_empty_file(self):
        path = self._write_log([])
        try:
            store = TradeStore.from_log(path, _now_fn=_now)
            self.assertEqual(store.entry_count, 0)
            self.assertEqual(store.placed_count, 0)
        finally:
            path.unlink()

    def test_load_nonexistent_file(self):
        store = TradeStore.from_log(Path("/tmp/nonexistent_trade_log.jsonl"))
        self.assertEqual(store.entry_count, 0)

    def test_load_placed_entries(self):
        entries = [
            _make_placed(fp="a1", ticker="T1"),
            _make_placed(fp="a2", ticker="T2"),
        ]
        path = self._write_log(entries)
        try:
            store = TradeStore.from_log(path)
            self.assertEqual(store.entry_count, 2)
            self.assertEqual(store.placed_count, 2)
            self.assertEqual(store.open_count, 2)
        finally:
            path.unlink()

    def test_load_with_settlements(self):
        entries = [
            _make_placed(fp="a1", ticker="T1"),
            _make_placed(fp="a2", ticker="T2"),
            _make_settlement(fp="a1", outcome="WIN", pnl=50.0, ticker="T1"),
        ]
        path = self._write_log(entries)
        try:
            store = TradeStore.from_log(path)
            self.assertEqual(store.placed_count, 2)
            self.assertEqual(store.open_count, 1)  # only a2 is open
            self.assertEqual(store.settlement_count, 1)
        finally:
            path.unlink()

    def test_malformed_lines_skipped(self):
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        path = Path(path_str)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(_make_placed(fp="ok1")) + "\n")
            fh.write("this is not valid json\n")
            fh.write("{truncated\n")
            fh.write(json.dumps(_make_placed(fp="ok2")) + "\n")
        try:
            store = TradeStore.from_log(path)
            self.assertEqual(store.entry_count, 2)
            self.assertEqual(store._load_errors, 2)
        finally:
            path.unlink()

    def test_blank_lines_ignored(self):
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        path = Path(path_str)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(_make_placed(fp="a1")) + "\n")
            fh.write("\n")
            fh.write("   \n")
            fh.write(json.dumps(_make_placed(fp="a2")) + "\n")
        try:
            store = TradeStore.from_log(path)
            self.assertEqual(store.entry_count, 2)
        finally:
            path.unlink()

    def test_legacy_field_names_normalised(self):
        legacy = {
            "fingerprint": "leg1",
            "action": "PLACED",
            "market_ticker": "KXNBA-XYZ",
            "kalshi_prob": 0.55,
            "position_size_usd": 100.0,
            "timestamp_utc": "2026-01-01T00:00:00",
            "mode": "paper",
        }
        path = self._write_log([legacy])
        try:
            store = TradeStore.from_log(path)
            entry = store.get_by_fingerprint("leg1")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["market_id"], "KXNBA-XYZ")
            self.assertEqual(entry["venue_prob"], 0.55)
            self.assertEqual(entry["pod_id"], "P-001")
        finally:
            path.unlink()


class TestTradeStoreQueries(TestCase):
    """Test index-based queries."""

    def setUp(self):
        entries = [
            _make_placed(fp="p1", pod_id="P-001", ticker="T1", ts="2026-03-12T10:00:00"),
            _make_placed(fp="p2", pod_id="P-006", ticker="T2", ts="2026-03-12T11:00:00"),
            _make_placed(fp="p3", pod_id="P-001", ticker="T3", ts="2026-03-12T12:00:00"),
            _make_settlement(fp="p1", outcome="WIN", pnl=50.0, ticker="T1"),
        ]
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        self.path = Path(path_str)
        self.store = TradeStore.from_log(self.path, _now_fn=_now)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_get_by_fingerprint(self):
        entry = self.store.get_by_fingerprint("p2")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["pod_id"], "P-006")

    def test_get_by_fingerprint_missing(self):
        self.assertIsNone(self.store.get_by_fingerprint("nonexistent"))

    def test_get_by_ticker(self):
        entries = self.store.get_by_ticker("T1")
        # Should include placed + settlement
        self.assertEqual(len(entries), 2)

    def test_get_trades_for_pod(self):
        p001 = self.store.get_trades_for_pod("P-001")
        p006 = self.store.get_trades_for_pod("P-006")
        # P-001 has 2 placed + 1 settlement = 3 entries
        self.assertEqual(len(p001), 3)
        self.assertEqual(len(p006), 1)

    def test_get_placed_entries_all(self):
        placed = self.store.get_placed_entries()
        self.assertEqual(len(placed), 3)

    def test_get_placed_entries_filtered(self):
        placed = self.store.get_placed_entries(pod_id="P-006")
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0]["fingerprint"], "p2")

    def test_get_open_trades(self):
        open_trades = self.store.get_open_trades()
        fps = {t["fingerprint"] for t in open_trades}
        self.assertNotIn("p1", fps)  # settled
        self.assertIn("p2", fps)
        self.assertIn("p3", fps)

    def test_get_open_trades_filtered(self):
        open_trades = self.store.get_open_trades(pod_id="P-006")
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0]["fingerprint"], "p2")

    def test_is_fingerprint_seen(self):
        self.assertTrue(self.store.is_fingerprint_seen("p1"))
        self.assertFalse(self.store.is_fingerprint_seen("nonexistent"))

    def test_is_position_open(self):
        self.assertFalse(self.store.is_position_open("T1"))  # settled
        self.assertTrue(self.store.is_position_open("T2"))
        self.assertTrue(self.store.is_position_open("T3"))

    def test_get_open_market_ids(self):
        ids = self.store.get_open_market_ids()
        self.assertNotIn("T1", ids)
        self.assertIn("T2", ids)
        self.assertIn("T3", ids)


class TestTradeStoreAppend(TestCase):
    """Test appending new entries."""

    def setUp(self):
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.path = Path(path_str)
        self.store = TradeStore(log_path=self.path, _now_fn=_now)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_append_placed(self):
        entry = _make_placed(fp="new1")
        self.store.append(entry)
        self.assertEqual(self.store.entry_count, 1)
        self.assertEqual(self.store.placed_count, 1)
        self.assertEqual(self.store.open_count, 1)

    def test_append_persists_to_disk(self):
        entry = _make_placed(fp="new1")
        self.store.append(entry)

        # Read back from disk
        with open(self.path) as fh:
            lines = [l.strip() for l in fh if l.strip()]
        self.assertEqual(len(lines), 1)
        disk_entry = json.loads(lines[0])
        self.assertEqual(disk_entry["fingerprint"], "new1")

    def test_append_normalises_legacy_fields(self):
        legacy = {
            "fingerprint": "leg1",
            "action": "PLACED",
            "market_ticker": "OLD-TICKER",
        }
        self.store.append(legacy)
        entry = self.store.get_by_fingerprint("leg1")
        self.assertEqual(entry["market_id"], "OLD-TICKER")
        self.assertEqual(entry["pod_id"], "P-001")

    def test_append_settlement_closes_position(self):
        self.store.append(_make_placed(fp="x1"))
        self.assertEqual(self.store.open_count, 1)

        self.store.append(_make_settlement(fp="x1", outcome="WIN", pnl=50))
        self.assertEqual(self.store.open_count, 0)
        self.assertEqual(self.store.settlement_count, 1)


class TestTradeStoreRecordSettlement(TestCase):
    """Test the record_settlement convenience method."""

    def setUp(self):
        entries = [
            _make_placed(fp="s1", ticker="T1"),
            _make_placed(fp="s2", ticker="T2"),
        ]
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        self.path = Path(path_str)
        self.store = TradeStore.from_log(self.path, _now_fn=_now)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_settle_existing_trade(self):
        result = self.store.record_settlement("s1", "WIN", 42.50)
        self.assertTrue(result)
        self.assertEqual(self.store.open_count, 1)  # only s2 open now
        settlement = self.store.get_settlement("s1")
        self.assertIsNotNone(settlement)
        self.assertEqual(settlement["outcome"], "WIN")

    def test_settle_already_settled(self):
        self.store.record_settlement("s1", "WIN", 42.50)
        result = self.store.record_settlement("s1", "WIN", 42.50)
        self.assertFalse(result)

    def test_settle_unknown_fingerprint(self):
        result = self.store.record_settlement("unknown", "WIN", 10.0)
        self.assertFalse(result)

    def test_settlement_persisted_to_disk(self):
        self.store.record_settlement("s1", "LOSS", -20.0)

        # Read back
        with open(self.path) as fh:
            lines = [l.strip() for l in fh if l.strip()]
        # 2 original + 1 settlement
        self.assertEqual(len(lines), 3)
        last = json.loads(lines[-1])
        self.assertEqual(last["outcome"], "LOSS")
        self.assertAlmostEqual(last["pnl_usd"], -20.0)


class TestTradeStoreDashboardHelpers(TestCase):
    """Test the dashboard helper methods."""

    def setUp(self):
        entries = [
            _make_placed(fp="d1", ticker="T1", ts="2026-03-12T10:00:00"),
            _make_placed(fp="d2", ticker="T2", ts="2026-03-12T11:00:00"),
            _make_placed(fp="d3", ticker="T3", ts="2026-03-12T12:00:00"),
            _make_settlement(fp="d1", outcome="WIN", pnl=100.0, ticker="T1"),
            _make_settlement(fp="d2", outcome="LOSS", pnl=-30.0, ticker="T2"),
        ]
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        self.path = Path(path_str)
        self.store = TradeStore.from_log(self.path, _now_fn=_now)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_get_recent_placed_with_status(self):
        trades = self.store.get_recent_placed_with_status()
        self.assertEqual(len(trades), 3)

        # Check statuses
        by_fp = {t["fingerprint"]: t for t in trades}
        self.assertEqual(by_fp["d1"]["status"], "WIN")
        self.assertAlmostEqual(by_fp["d1"]["pnl_usd"], 100.0)
        self.assertEqual(by_fp["d2"]["status"], "LOSS")
        self.assertEqual(by_fp["d3"]["status"], "OPEN")

    def test_get_recent_placed_newest_first(self):
        trades = self.store.get_recent_placed_with_status()
        timestamps = [t["timestamp_utc"] for t in trades]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_get_recent_placed_max_trades(self):
        trades = self.store.get_recent_placed_with_status(max_trades=2)
        self.assertEqual(len(trades), 2)

    def test_compute_settlement_summary(self):
        summary = self.store.compute_settlement_summary()
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["voids"], 0)
        self.assertAlmostEqual(summary["total_pnl"], 70.0)
        self.assertAlmostEqual(summary["win_rate"], 0.5)
        self.assertEqual(summary["total_settled"], 2)


class TestTradeStoreThreadSafety(TestCase):
    """Basic thread safety verification."""

    def test_concurrent_appends(self):
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        path = Path(path_str)
        store = TradeStore(log_path=path, _now_fn=_now)

        errors = []

        def append_batch(start, count):
            try:
                for i in range(count):
                    store.append(_make_placed(fp=f"thread_{start}_{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=append_batch, args=(t * 100, 50))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        path.unlink(missing_ok=True)

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(store.entry_count, 200)
        self.assertEqual(store.placed_count, 200)


class TestTradeStoreBootstrapHelpers(TestCase):
    """Test helpers used by AggregateRiskGuard and CapitalAllocator."""

    def test_get_open_positions_for_bootstrap(self):
        entries = [
            _make_placed(fp="b1", ticker="T1", pod_id="P-001", venue="kalshi",
                         position_size=100.0),
            _make_placed(fp="b2", ticker="T2", pod_id="P-006", venue="polymarket",
                         position_size=50.0),
            _make_settlement(fp="b1", outcome="WIN", pnl=30.0, ticker="T1"),
        ]
        fd, path_str = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        path = Path(path_str)
        try:
            store = TradeStore.from_log(path)
            positions = store.get_open_positions_for_bootstrap()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["pod_id"], "P-006")
            self.assertEqual(positions[0]["venue"], "polymarket")
            self.assertAlmostEqual(positions[0]["position_size_usd"], 50.0)
        finally:
            path.unlink()
