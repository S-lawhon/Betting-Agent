"""
tests/test_settlement_bridge.py
────────────────────────────────
Tests for src/settlement_bridge.py.

Uses temporary directories so no real filesystem state leaks between
tests.  All CapitalAllocator and AggregateRiskGuard calls are mocked.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.settlement_bridge import SettlementBridge, TradeRecord, SettlementRecord


# ── Helpers ──────────────────────────────────────────────────────────

_FP1 = "a" * 64
_FP2 = "b" * 64
_FP3 = "c" * 64


def _placed_line(fp, pod_id="P-006", market_id="MKT-001",
                 position_size=100.0) -> str:
    return json.dumps({
        "fingerprint": fp,
        "action": "PLACED",
        "pod_id": pod_id,
        "pod_name": "Test Pod",
        "venue": "polymarket",
        "market_id": market_id,
        "market_type": "sports_moneyline",
        "event": "Chiefs vs Raiders",
        "side": "YES",
        "fair_prob": 0.60,
        "venue_prob": 0.52,
        "edge_pct": 0.154,
        "ev": 8.0,
        "kelly_fraction": 0.08,
        "position_size_usd": position_size,
        "timestamp_utc": "2026-01-15T18:00:00+00:00",
        "mode": "paper",
        "order_id": None,
        "fill_price": 0.52,
        "extra": {},
    })


def _skipped_line(fp) -> str:
    return json.dumps({
        "fingerprint": fp,
        "action": "SKIPPED_EDGE",
        "pod_id": "P-006",
        "pod_name": "Test Pod",
        "venue": "polymarket",
        "market_id": "MKT-SKIP",
        "market_type": "sports_moneyline",
        "event": "Skipped Event",
        "side": "YES",
        "fair_prob": 0.51,
        "venue_prob": 0.50,
        "edge_pct": 0.02,
        "ev": 0.5,
        "kelly_fraction": 0.01,
        "position_size_usd": 0.0,
        "timestamp_utc": "2026-01-15T18:00:00+00:00",
        "mode": "paper",
        "extra": {},
    })


def _make_bridge(td: str, allocator=None, risk=None) -> SettlementBridge:
    """Create a SettlementBridge pointed at a temp dir."""
    pod_log_dir = Path(td) / "pods"
    pod_log_dir.mkdir(parents=True, exist_ok=True)
    settlement_log = Path(td) / "settlements.jsonl"
    return SettlementBridge(
        pod_log_dir=pod_log_dir,
        settlement_log=settlement_log,
        capital_allocator=allocator,
        aggregate_risk=risk,
    )


def _write_pod_log(td: str, filename: str, lines: list) -> None:
    pod_dir = Path(td) / "pods"
    pod_dir.mkdir(parents=True, exist_ok=True)
    with open(pod_dir / filename, "w") as fh:
        for line in lines:
            fh.write(line + "\n")


# ── TestGetOpenTrades ─────────────────────────────────────────────────

class TestGetOpenTrades(unittest.TestCase):

    def test_returns_placed_trades(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            open_trades = bridge.get_open_trades()
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0].fingerprint, _FP1)

    def test_excludes_skipped_trades(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [
                _placed_line(_FP1),
                _skipped_line(_FP2),
            ])
            bridge = _make_bridge(td)
            open_trades = bridge.get_open_trades()
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0].fingerprint, _FP1)

    def test_empty_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = _make_bridge(td)
            # pods dir exists but no files
            open_trades = bridge.get_open_trades()
        self.assertEqual(open_trades, [])

    def test_missing_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = SettlementBridge(
                pod_log_dir=Path(td) / "nonexistent",
                settlement_log=Path(td) / "settle.jsonl",
            )
            open_trades = bridge.get_open_trades()
        self.assertEqual(open_trades, [])

    def test_excludes_already_settled(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [
                _placed_line(_FP1),
                _placed_line(_FP2),
            ])
            bridge = _make_bridge(td)
            # Pre-settle FP1
            bridge.settle(_FP1, pnl=50.0)
            open_trades = bridge.get_open_trades()
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0].fingerprint, _FP2)

    def test_reads_multiple_pod_files(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-001.jsonl", [_placed_line(_FP1, pod_id="P-001")])
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP2, pod_id="P-006")])
            bridge = _make_bridge(td)
            open_trades = bridge.get_open_trades()
        self.assertEqual(len(open_trades), 2)


# ── TestSettle ────────────────────────────────────────────────────────

class TestSettle(unittest.TestCase):

    def test_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            result = bridge.settle(_FP1, pnl=42.0)
        self.assertTrue(result)

    def test_returns_false_unknown_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = _make_bridge(td)
            result = bridge.settle("unknown_fp", pnl=10.0)
        self.assertFalse(result)

    def test_returns_false_duplicate_settlement(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            bridge.settle(_FP1, pnl=10.0)
            result = bridge.settle(_FP1, pnl=10.0)  # duplicate
        self.assertFalse(result)

    def test_persists_to_settlement_log(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            bridge.settle(_FP1, pnl=25.0)

            settlement_log = Path(td) / "settlements.jsonl"
            self.assertTrue(settlement_log.exists())
            with open(settlement_log) as fh:
                data = json.loads(fh.read().strip())
            self.assertEqual(data["fingerprint"], _FP1)
            self.assertAlmostEqual(data["pnl"], 25.0)

    def test_auto_outcome_win(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            bridge.settle(_FP1, pnl=10.0, outcome="WIN")
            history = bridge.get_settlement_history()
        self.assertEqual(history[0].outcome, "WIN")

    def test_auto_outcome_loss_corrected(self):
        """outcome="WIN" with negative pnl → corrected to "LOSS"."""
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            bridge.settle(_FP1, pnl=-15.0, outcome="WIN")
            history = bridge.get_settlement_history()
        self.assertEqual(history[0].outcome, "LOSS")

    def test_notifies_capital_allocator(self):
        allocator = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td, allocator=allocator)
            bridge.settle(_FP1, pnl=30.0)
        allocator.record_settlement.assert_called_once_with(
            "P-006", 30.0, fingerprint=_FP1,
        )

    def test_notifies_aggregate_risk(self):
        risk = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1, market_id="MKT-001")])
            bridge = _make_bridge(td, risk=risk)
            bridge.settle(_FP1, pnl=-20.0)
        risk.close_position.assert_called_once_with("MKT-001", -20.0)


# ── TestSettleBatch ───────────────────────────────────────────────────

class TestSettleBatch(unittest.TestCase):

    def test_batch_settle_all_success(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [
                _placed_line(_FP1),
                _placed_line(_FP2),
            ])
            bridge = _make_bridge(td)
            ok, fail = bridge.settle_batch([(_FP1, 50.0), (_FP2, -25.0)])
        self.assertEqual(ok, 2)
        self.assertEqual(fail, 0)

    def test_batch_settle_partial_failure(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td)
            ok, fail = bridge.settle_batch([(_FP1, 10.0), ("unknown", 5.0)])
        self.assertEqual(ok, 1)
        self.assertEqual(fail, 1)


# ── TestSummary ───────────────────────────────────────────────────────

class TestSummary(unittest.TestCase):

    def test_empty_summary(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = _make_bridge(td)
            s = bridge.summary()
        self.assertEqual(s["total_pnl"], 0.0)
        self.assertEqual(s["total_settled"], 0)

    def test_summary_after_settlements(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [
                _placed_line(_FP1),
                _placed_line(_FP2),
                _placed_line(_FP3),
            ])
            bridge = _make_bridge(td)
            bridge.settle(_FP1, pnl=100.0, outcome="WIN")
            bridge.settle(_FP2, pnl=-40.0, outcome="LOSS")
            bridge.settle(_FP3, pnl=0.0, outcome="VOID")
            s = bridge.summary()

        self.assertEqual(s["total_settled"], 3)
        self.assertAlmostEqual(s["total_pnl"], 60.0)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["voids"], 1)
        self.assertAlmostEqual(s["win_rate"], 0.5)

    def test_per_pod_breakdown(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-001.jsonl", [_placed_line(_FP1, pod_id="P-001")])
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP2, pod_id="P-006")])
            bridge = _make_bridge(td)
            bridge.settle(_FP1, pnl=80.0)
            bridge.settle(_FP2, pnl=-30.0)
            s = bridge.summary()

        self.assertAlmostEqual(s["per_pod"]["P-001"], 80.0)
        self.assertAlmostEqual(s["per_pod"]["P-006"], -30.0)


# ── TestFromConfig ────────────────────────────────────────────────────

class TestFromConfig(unittest.TestCase):

    def test_defaults(self):
        bridge = SettlementBridge.from_config({})
        self.assertEqual(bridge._pod_log_dir, Path("data/pods"))
        self.assertEqual(bridge._settlement_log, Path("data/trade_logs/settlements.jsonl"))

    def test_reads_paths_from_config(self):
        config = {
            "paths": {
                "pod_logs": "custom/pods",
                "trade_log": "custom/logs/trade.jsonl",
            }
        }
        bridge = SettlementBridge.from_config(config)
        self.assertEqual(bridge._pod_log_dir, Path("custom/pods"))
        self.assertEqual(bridge._settlement_log, Path("custom/logs/settlements.jsonl"))

    def test_wires_allocator_and_risk(self):
        allocator = MagicMock()
        risk = MagicMock()
        bridge = SettlementBridge.from_config({}, capital_allocator=allocator, aggregate_risk=risk)
        self.assertIs(bridge._capital_allocator, allocator)
        self.assertIs(bridge._aggregate_risk, risk)


# ── TestRobustness ────────────────────────────────────────────────────

class TestRobustness(unittest.TestCase):

    def test_corrupt_json_line_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [
                "{not valid json",
                _placed_line(_FP1),
            ])
            bridge = _make_bridge(td)
            open_trades = bridge.get_open_trades()
        self.assertEqual(len(open_trades), 1)

    def test_allocator_error_doesnt_crash_settle(self):
        allocator = MagicMock()
        allocator.record_settlement.side_effect = RuntimeError("db error")
        with tempfile.TemporaryDirectory() as td:
            _write_pod_log(td, "P-006.jsonl", [_placed_line(_FP1)])
            bridge = _make_bridge(td, allocator=allocator)
            result = bridge.settle(_FP1, pnl=10.0)
        # Should still write the settlement even if allocator failed
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
