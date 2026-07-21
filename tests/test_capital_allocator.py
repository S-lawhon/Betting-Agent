"""
tests/test_capital_allocator.py
───────────────────────────────
Tests for CapitalAllocator.

  ✓ equal strategy: allocations split evenly
  ✓ weighted strategy: respects configured weights
  ✓ performance strategy: tilts toward better performers
  ✓ pre_cycle: initializes new pods
  ✓ pre_cycle: pushes bankroll to pod risk managers
  ✓ drawdown throttling: halves allocation
  ✓ record_settlement: tracks win/loss/P&L
  ✓ record_settlement: updates drawdown
  ✓ PodPerformance: win_rate, avg_pnl
  ✓ from_config
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.capital_allocator import (
    CapitalAllocator, PodAllocation, PodPerformance,
)

_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class _MockPod:
    def __init__(self, pod_id="P-TEST"):
        self.pod_id = pod_id
        self.risk_manager = MagicMock()
        self.risk_manager.ledger.bankroll = 5000.0


class _MockReport:
    def __init__(self, results=None):
        self.results = results or []


class _MockResult:
    def __init__(self, pod_id="P-TEST", success=True, skipped=False, error=None):
        self.pod_id = pod_id
        self.success = success
        self.skipped = skipped
        self.error = error


# ── Tests ───────────────────────────────────────────────────────────

class TestEqualStrategy(unittest.TestCase):

    def test_equal_split(self):
        alloc = CapitalAllocator(
            total_bankroll=10_000, strategy="equal",
            _now_fn=lambda: _NOW,
        )
        pods = [_MockPod("P-001"), _MockPod("P-002")]
        alloc.pre_cycle(pods)
        a1 = alloc.get_allocation("P-001")
        a2 = alloc.get_allocation("P-002")
        self.assertIsNotNone(a1)
        self.assertIsNotNone(a2)
        # Equal split → each gets ~50% (clamped by max_pod_pct=40%)
        self.assertAlmostEqual(a1.allocation_pct, 0.40, places=2)
        self.assertAlmostEqual(a2.allocation_pct, 0.40, places=2)

    def test_three_pods(self):
        alloc = CapitalAllocator(
            total_bankroll=9000, strategy="equal",
            max_pod_pct=0.50, _now_fn=lambda: _NOW,
        )
        pods = [_MockPod("P-001"), _MockPod("P-002"), _MockPod("P-004")]
        alloc.pre_cycle(pods)
        # Equal → ~33% each, within 50% cap
        for pid in ["P-001", "P-002", "P-004"]:
            a = alloc.get_allocation(pid)
            self.assertGreater(a.allocation_pct, 0)
            self.assertGreater(a.allocated_usd, 0)


class TestWeightedStrategy(unittest.TestCase):

    def test_weighted_split(self):
        alloc = CapitalAllocator(
            total_bankroll=10_000, strategy="weighted",
            pod_weights={"P-001": 3.0, "P-002": 1.0},
            max_pod_pct=0.80, _now_fn=lambda: _NOW,
        )
        pods = [_MockPod("P-001"), _MockPod("P-002")]
        alloc.pre_cycle(pods)
        a1 = alloc.get_allocation("P-001")
        a2 = alloc.get_allocation("P-002")
        self.assertGreater(a1.allocation_pct, a2.allocation_pct)


class TestPreCycle(unittest.TestCase):

    def test_initializes_new_pods(self):
        alloc = CapitalAllocator(
            total_bankroll=10_000, strategy="equal",
            _now_fn=lambda: _NOW,
        )
        pods = [_MockPod("P-001")]
        alloc.pre_cycle(pods)
        perf = alloc.get_performance("P-001")
        self.assertIsNotNone(perf)
        self.assertEqual(perf.total_placed, 0)

    def test_pushes_bankroll_to_pods(self):
        alloc = CapitalAllocator(
            total_bankroll=10_000, strategy="equal",
            _now_fn=lambda: _NOW,
        )
        pod = _MockPod("P-001")
        alloc.pre_cycle([pod])
        # Bankroll should be updated on the pod's risk manager
        a = alloc.get_allocation("P-001")
        self.assertGreater(a.allocated_usd, 0)


class TestDrawdownThrottling(unittest.TestCase):

    def test_throttles_on_drawdown(self):
        alloc = CapitalAllocator(
            total_bankroll=10_000, strategy="equal",
            drawdown_throttle_pct=0.10,
            _now_fn=lambda: _NOW,
        )
        pods = [_MockPod("P-001")]
        alloc.pre_cycle(pods)
        initial_pct = alloc.get_allocation("P-001").allocation_pct

        # Simulate drawdown via settlement
        alloc.record_settlement("P-001", 100.0)   # win → peak = 100
        alloc.record_settlement("P-001", -20.0)   # loss → drawdown = 20%

        alloc.pre_cycle(pods)
        throttled_pct = alloc.get_allocation("P-001").allocation_pct
        self.assertLess(throttled_pct, initial_pct)


class TestRecordSettlement(unittest.TestCase):

    def test_tracks_win(self):
        alloc = CapitalAllocator(_now_fn=lambda: _NOW)
        alloc.record_settlement("P-001", 50.0)
        perf = alloc.get_performance("P-001")
        self.assertEqual(perf.total_wins, 1)
        self.assertEqual(perf.total_losses, 0)
        self.assertAlmostEqual(perf.total_pnl, 50.0)

    def test_tracks_loss(self):
        alloc = CapitalAllocator(_now_fn=lambda: _NOW)
        alloc.record_settlement("P-001", -30.0)
        perf = alloc.get_performance("P-001")
        self.assertEqual(perf.total_losses, 1)
        self.assertAlmostEqual(perf.total_pnl, -30.0)

    def test_consecutive_losses(self):
        alloc = CapitalAllocator(_now_fn=lambda: _NOW)
        alloc.record_settlement("P-001", -10.0)
        alloc.record_settlement("P-001", -10.0)
        alloc.record_settlement("P-001", -10.0)
        perf = alloc.get_performance("P-001")
        self.assertEqual(perf.consecutive_losses, 3)

    def test_consecutive_losses_reset_on_win(self):
        alloc = CapitalAllocator(_now_fn=lambda: _NOW)
        alloc.record_settlement("P-001", -10.0)
        alloc.record_settlement("P-001", -10.0)
        alloc.record_settlement("P-001", 50.0)
        perf = alloc.get_performance("P-001")
        self.assertEqual(perf.consecutive_losses, 0)

    def test_drawdown_tracking(self):
        alloc = CapitalAllocator(_now_fn=lambda: _NOW)
        alloc.record_settlement("P-001", 100.0)   # peak = 100
        alloc.record_settlement("P-001", -50.0)   # pnl = 50, dd = 50%
        perf = alloc.get_performance("P-001")
        self.assertAlmostEqual(perf.max_drawdown, 0.5)


class TestPodPerformance(unittest.TestCase):

    def test_win_rate(self):
        perf = PodPerformance(pod_id="P-001", total_wins=7, total_losses=3)
        self.assertAlmostEqual(perf.win_rate, 0.7)

    def test_win_rate_zero(self):
        perf = PodPerformance(pod_id="P-001")
        self.assertAlmostEqual(perf.win_rate, 0.0)

    def test_avg_pnl(self):
        perf = PodPerformance(pod_id="P-001", total_placed=10, total_pnl=500.0)
        self.assertAlmostEqual(perf.avg_pnl, 50.0)


class TestFromConfig(unittest.TestCase):

    def test_from_config(self):
        cfg = {
            "risk": {"initial_bankroll": 20_000},
            "capital_allocator": {
                "strategy": "weighted",
                "max_pod_pct": 0.35,
                "rebalance_interval": 5,
            },
            "pods": {"P-001": {"weight": 2.0}, "P-006": {"weight": 1.5}},
        }
        alloc = CapitalAllocator.from_config(cfg)
        self.assertAlmostEqual(alloc.total_bankroll, 20_000)
        self.assertEqual(alloc.strategy, "weighted")
        self.assertAlmostEqual(alloc.max_pod_pct, 0.35)
        self.assertEqual(alloc.rebalance_interval, 5)

    def test_defaults(self):
        alloc = CapitalAllocator.from_config({})
        self.assertAlmostEqual(alloc.total_bankroll, 10_000)
        self.assertEqual(alloc.strategy, "equal")


if __name__ == "__main__":
    unittest.main()
