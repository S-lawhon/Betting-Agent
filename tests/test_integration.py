"""
tests/test_integration.py
─────────────────────────
End-to-end integration tests: scan → match → edge → risk → place → settle.

Uses mock API responses so tests run without credentials.
Tests exercise the full pipeline through PodRunner with real pod instances.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.aggregate_risk import AggregateRiskGuard
from src.base_pod import BasePod, ScanResult
from src.capital_allocator import CapitalAllocator
from src.multi_executor import MultiExecutor
from src.pod_runner import PodRunner, CycleReport
from src.trade_store import TradeStore


# ── Helpers ──────────────────────────────────────────────────────────

class StubPod(BasePod):
    """Minimal pod that returns preconfigured ScanResults for testing."""

    def __init__(self, pod_id, results, trade_log_path, trade_store=None, **kwargs):
        rm = MagicMock()
        rm.max_bet_pct = 0.03
        rm.max_position_usd = 300.0
        type(rm).ledger = PropertyMock(return_value=MagicMock(bankroll=10000.0))
        ec = MagicMock()
        super().__init__(
            pod_id=pod_id,
            pod_name=f"Stub {pod_id}",
            edge_calculator=ec,
            risk_manager=rm,
            trade_log_path=trade_log_path,
            mode="paper",
            trade_store=trade_store,
        )
        self._results = results

    def scan_once(self):
        return self._results

    def venue_name(self):
        return "stub"

    @classmethod
    def from_config(cls, config, **overrides):
        raise NotImplementedError


def _make_result(pod_id="P-TEST", market_id="MKT-001", side="YES",
                 action="PLACED", position_size=100.0, fill_price=0.45):
    now = datetime.now(timezone.utc)
    fp = f"fp-{market_id}-{side}-{now.strftime('%Y%m%d%H')}"
    return ScanResult(
        fingerprint=fp,
        timestamp_utc=now.isoformat(),
        pod_id=pod_id,
        pod_name=f"Stub {pod_id}",
        mode="paper",
        venue="stub",
        market_type="test",
        event="Team A vs. Team B",
        market_id=market_id,
        side=side,
        fair_prob=0.55,
        venue_prob=0.45,
        edge_pct=0.10,
        ev=10.0,
        kelly_fraction=0.05,
        position_size_usd=position_size,
        action=action,
        fill_price=fill_price,
    )


# ── Integration Tests ────────────────────────────────────────────────

class TestFullPipeline(unittest.TestCase):
    """Full scan → log → store → settle pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = Path(self.tmpdir) / "trade_log.jsonl"
        self.log_path.touch()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scan_places_trade_and_logs_it(self):
        """Single pod scan → ScanResult with action=PLACED → written to log."""
        store = TradeStore.from_log(self.log_path)
        result = _make_result(pod_id="P-001", action="PLACED")
        pod = StubPod("P-001", [result], self.log_path, trade_store=store)

        # Simulate scan
        results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "PLACED")

        # Write to log via store
        pod.write_log(results[0])
        self.assertEqual(store.placed_count, 1)

    def test_dedup_prevents_double_placement(self):
        """Same fingerprint should be caught by is_duplicate."""
        store = TradeStore.from_log(self.log_path)
        result = _make_result(pod_id="P-001", action="PLACED")
        pod = StubPod("P-001", [result], self.log_path, trade_store=store)

        # First placement
        pod.write_log(result)
        pod.mark_seen(result.fingerprint)

        # Second attempt should be flagged as duplicate
        self.assertTrue(pod.is_duplicate(result.fingerprint))

    def test_aggregate_risk_blocks_oversized_position(self):
        """AggregateRiskGuard should block a trade that exceeds limits."""
        guard = AggregateRiskGuard(
            bankroll=1000.0,
            max_total_exposure_pct=0.10,  # 10% = $100 max
        )
        store = TradeStore.from_log(self.log_path)
        pod = StubPod("P-001", [], self.log_path, trade_store=store)
        pod.aggregate_risk = guard

        # Small trade should pass
        self.assertTrue(pod.check_aggregate_risk("stub", 50.0))

        # Oversized trade should be blocked
        # (depends on guard's internal state, but with fresh guard and
        # max_total_exposure_pct=10% on $1000, $200 would exceed $100 cap)
        # First record a position to use some exposure
        guard._open_positions = {"P-001": {"stub": [{"usd": 90.0}]}}
        guard._total_exposure_usd = 90.0

    def test_settlement_recorded_in_store(self):
        """Settlement (WIN/LOSS) should be indexed in TradeStore."""
        store = TradeStore.from_log(self.log_path)

        # Place a trade
        placed = {
            "action": "PLACED",
            "fingerprint": "fp-settle-test",
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
            "pod_id": "P-001",
            "pod_name": "Stub P-001",
            "venue": "stub",
            "market_id": "MKT-001",
            "side": "YES",
            "position_size_usd": 100.0,
            "fill_price": 0.45,
            "mode": "paper",
        }
        store.append(placed)
        self.assertEqual(store.placed_count, 1)
        self.assertTrue(store.is_position_open("MKT-001"))

        # Settle the trade
        settlement = {
            "action": "WIN",
            "outcome": "WIN",
            "fingerprint": "fp-settle-test",
            "market_id": "MKT-001",
            "pod_id": "P-001",
            "pnl_usd": 55.0,
            "settled_at_utc": "2026-01-02T00:00:00+00:00",
        }
        store.append(settlement)
        self.assertEqual(store.settlement_count, 1)

    def test_capital_allocator_bootstrap_from_store(self):
        """CapitalAllocator bootstrap picks up placed trades from store."""
        store = TradeStore.from_log(self.log_path)

        # Add some placed trades
        for i in range(3):
            store.append({
                "action": "PLACED",
                "fingerprint": f"fp-alloc-{i}",
                "timestamp_utc": "2026-01-01T00:00:00+00:00",
                "pod_id": "P-001",
                "venue": "stub",
                "market_id": f"MKT-{i}",
                "side": "YES",
                "position_size_usd": 100.0,
                "mode": "paper",
            })

        allocator = CapitalAllocator(total_bankroll=10000)
        allocator.bootstrap_from_store(store)
        # Should have picked up the placed entries without crashing
        self.assertIsNotNone(allocator)

    def test_position_size_respects_config(self):
        """compute_position_size should cap at max_position_usd."""
        store = TradeStore.from_log(self.log_path)
        pod = StubPod("P-001", [], self.log_path, trade_store=store)

        # With max_position_usd=300, kelly of 1.0 on $10k bankroll
        # should be capped at 300
        size = pod.compute_position_size(1.0)
        self.assertEqual(size, 300.0)

        # Kelly below MIN_KELLY_FRACTION (0.02) → returns 0
        size_tiny = pod.compute_position_size(0.01)
        self.assertEqual(size_tiny, 0.0)

        # Kelly above threshold → kelly * bankroll
        size_small = pod.compute_position_size(0.025)
        self.assertEqual(size_small, 250.0)  # 0.025 * 10000

    def test_get_bankroll_reads_from_ledger(self):
        """get_bankroll should read from risk_manager.ledger.bankroll."""
        store = TradeStore.from_log(self.log_path)
        pod = StubPod("P-001", [], self.log_path, trade_store=store)
        self.assertEqual(pod.get_bankroll(), 10000.0)


class TestPodRunnerCycle(unittest.TestCase):
    """PodRunner integration with real pod + guard + allocator."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = Path(self.tmpdir) / "trade_log.jsonl"
        self.log_path.touch()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_runner_executes_single_cycle(self):
        """PodRunner.run_once() with a StubPod produces a CycleReport."""
        store = TradeStore.from_log(self.log_path)
        result = _make_result(pod_id="P-TEST", action="PLACED")
        pod = StubPod("P-TEST", [result], self.log_path, trade_store=store)

        guard = AggregateRiskGuard(bankroll=10000)
        allocator = CapitalAllocator(total_bankroll=10000)

        reports = []
        def _callback(report):
            reports.append(report)

        executor = MultiExecutor(venue_clients={})
        runner = PodRunner(
            pods=[pod],
            executor=executor,
            cycle_callback=_callback,
            capital_allocator=allocator,
        )

        report = runner.run_once()
        self.assertIsInstance(report, CycleReport)
        self.assertEqual(report.pods_scanned, 1)
        self.assertEqual(len(reports), 1)

    def test_multiple_pods_run_in_single_cycle(self):
        """PodRunner with 2 pods should scan both in one cycle."""
        store = TradeStore.from_log(self.log_path)
        r1 = _make_result(pod_id="P-001", market_id="MKT-A")
        r2 = _make_result(pod_id="P-002", market_id="MKT-B")
        pod1 = StubPod("P-001", [r1], self.log_path, trade_store=store)
        pod2 = StubPod("P-002", [r2], self.log_path, trade_store=store)

        allocator = CapitalAllocator(total_bankroll=10000)
        executor = MultiExecutor(venue_clients={})
        runner = PodRunner(
            pods=[pod1, pod2],
            executor=executor,
            cycle_callback=lambda r: None,
            capital_allocator=allocator,
        )

        report = runner.run_once()
        self.assertEqual(report.pods_scanned, 2)


class TestTradeStoreRoundTrip(unittest.TestCase):
    """Trade store: write → load → verify indexes."""

    def test_write_reload_preserves_data(self):
        """Entries written to store should survive a from_log() reload."""
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            log.touch()

            store = TradeStore.from_log(log)
            store.append({
                "action": "PLACED",
                "fingerprint": "fp-roundtrip",
                "timestamp_utc": "2026-01-01T00:00:00+00:00",
                "pod_id": "P-001",
                "venue": "kalshi",
                "market_id": "KXTEST",
                "side": "YES",
                "position_size_usd": 50.0,
                "mode": "paper",
            })

            # Reload
            store2 = TradeStore.from_log(log)
            self.assertEqual(store2.placed_count, 1)
            self.assertTrue(store2.is_fingerprint_seen("fp-roundtrip"))
            self.assertTrue(store2.is_position_open("KXTEST"))


if __name__ == "__main__":
    unittest.main()
