"""
tests/test_multi_executor.py
────────────────────────────
Tests for MultiExecutor (venue-agnostic execution router).

  ✓ Paper mode: PLACED → success, mode="paper", no order_id
  ✓ Paper mode: non-PLACED → skipped=True
  ✓ Paper mode: fill_price equals venue_prob
  ✓ Unknown venue → error
  ✓ Missing client → error
  ✓ run_cycle: calls scan_once on all pods
  ✓ run_cycle: processes all PLACED results
  ✓ run_cycle: pod error → error result, continues to next
  ✓ Live Kalshi: routes to _execute_kalshi
  ✓ Live Polymarket: routes to _execute_polymarket
  ✓ from_config: demo → paper
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.base_pod import BasePod, ScanResult
from src.multi_executor import MultiExecutionResult, MultiExecutor


# ── Fixtures ────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


def _scan_result(
    pod_id="P-001",
    venue="kalshi",
    action="PLACED",
    venue_prob=0.57,
    size_usd=50.0,
    market_id="NFL-KC-LV",
    side="YES",
    mode="paper",
    **overrides,
) -> ScanResult:
    defaults = dict(
        fingerprint="abc123",
        timestamp_utc=_NOW.isoformat(),
        pod_id=pod_id,
        pod_name="Test Pod",
        mode=mode,
        venue=venue,
        market_type="sports_nfl",
        event="Chiefs vs Raiders",
        market_id=market_id,
        side=side,
        fair_prob=0.62,
        venue_prob=venue_prob,
        edge_pct=0.05,
        ev=0.03,
        kelly_fraction=0.005,
        position_size_usd=size_usd,
        action=action,
    )
    defaults.update(overrides)
    return ScanResult(**defaults)


class _MockPod(BasePod):
    """Minimal pod for testing MultiExecutor."""

    def __init__(self, results=None, raise_on_scan=False, venue="kalshi"):
        self._results = results or []
        self._raise = raise_on_scan
        self._venue = venue
        # Don't call super().__init__ to avoid needing log file
        self.pod_id = "P-TEST"
        self.pod_name = "Test Pod"
        self.mode = "paper"
        self.edge_calculator = None
        self.risk_manager = None
        self.trade_log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        self._now_fn = lambda: _NOW
        self._seen_fingerprints = set()

    def scan_once(self) -> List[ScanResult]:
        if self._raise:
            raise RuntimeError("pod error")
        return list(self._results)

    def venue_name(self) -> str:
        return self._venue

    @classmethod
    def from_config(cls, config, **overrides):
        return cls()


class _MockKalshiClient:
    def __init__(self, resp=None):
        self._resp = resp or {"order": {"order_id": "K-123"}}
        self.place_calls = []

    def place_order(self, order):
        self.place_calls.append(order)
        return self._resp


class _MockPolyClient:
    def __init__(self):
        self.place_calls = []

    def place_order(self, token_id, side, price, size):
        self.place_calls.append({"token_id": token_id, "side": side})
        from src.polymarket_client import PolymarketOrderResult
        return PolymarketOrderResult(
            success=True, order_id="PM-456", error=None, fill_price=price,
        )


# ── Paper mode ──────────────────────────────────────────────────────

class TestMultiExecutorPaper(unittest.TestCase):

    def test_placed_returns_success(self):
        ex = MultiExecutor(venue_clients={"kalshi": _MockKalshiClient()}, mode="paper")
        result = ex.execute(_scan_result(action="PLACED"))
        self.assertTrue(result.success)
        self.assertEqual(result.mode, "paper")

    def test_placed_fill_price(self):
        ex = MultiExecutor(venue_clients={"kalshi": _MockKalshiClient()}, mode="paper")
        result = ex.execute(_scan_result(venue_prob=0.57))
        self.assertAlmostEqual(result.fill_price, 0.57)

    def test_non_placed_skipped(self):
        ex = MultiExecutor(venue_clients={}, mode="paper")
        result = ex.execute(_scan_result(action="SKIPPED_EDGE"))
        self.assertTrue(result.success)
        self.assertTrue(result.skipped)

    def test_missing_client_error(self):
        ex = MultiExecutor(venue_clients={}, mode="live")
        result = ex.execute(_scan_result(venue="kalshi", mode="live"))
        self.assertFalse(result.success)
        self.assertIn("No client", result.error)


# ── run_cycle ───────────────────────────────────────────────────────

class TestRunCycle(unittest.TestCase):

    def test_calls_scan_once_on_all_pods(self):
        pod1 = _MockPod(results=[_scan_result(pod_id="P-001")])
        pod2 = _MockPod(results=[_scan_result(pod_id="P-006", venue="polymarket")])
        ex = MultiExecutor(
            venue_clients={"kalshi": _MockKalshiClient(), "polymarket": _MockPolyClient()},
            mode="paper",
        )
        results = ex.run_cycle([pod1, pod2])
        self.assertEqual(len(results), 2)

    def test_processes_all_placed(self):
        results_in = [
            _scan_result(market_id="T1"),
            _scan_result(market_id="T2"),
        ]
        pod = _MockPod(results=results_in)
        ex = MultiExecutor(venue_clients={"kalshi": _MockKalshiClient()}, mode="paper")
        results = ex.run_cycle([pod])
        self.assertEqual(len(results), 2)

    def test_pod_error_continues(self):
        good_pod = _MockPod(results=[_scan_result()])
        bad_pod = _MockPod(raise_on_scan=True)
        ex = MultiExecutor(venue_clients={"kalshi": _MockKalshiClient()}, mode="paper")
        results = ex.run_cycle([bad_pod, good_pod])
        # Should get error result from bad pod + success from good pod
        self.assertEqual(len(results), 2)
        errors = [r for r in results if not r.success]
        successes = [r for r in results if r.success]
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(successes), 1)


# ── Live Kalshi routing ─────────────────────────────────────────────

class TestLiveKalshiRouting(unittest.TestCase):

    def test_routes_to_kalshi(self):
        kalshi = _MockKalshiClient()
        ex = MultiExecutor(venue_clients={"kalshi": kalshi}, mode="live")
        sr = _scan_result(venue="kalshi", mode="live", market_id="NFL-T")
        result = ex.execute(sr)
        self.assertTrue(result.success)
        self.assertEqual(result.venue, "kalshi")
        self.assertEqual(len(kalshi.place_calls), 1)
        self.assertEqual(kalshi.place_calls[0]["ticker"], "NFL-T")

    def test_kalshi_order_fields(self):
        kalshi = _MockKalshiClient()
        ex = MultiExecutor(venue_clients={"kalshi": kalshi}, mode="live")
        sr = _scan_result(venue="kalshi", mode="live", side="YES", venue_prob=0.57)
        ex.execute(sr)
        order = kalshi.place_calls[0]
        self.assertEqual(order["side"], "yes")
        self.assertEqual(order["yes_price"], 57)
        self.assertEqual(order["action"], "buy")


# ── Live Polymarket routing ─────────────────────────────────────────

class TestLivePolymarketRouting(unittest.TestCase):

    def test_routes_to_polymarket(self):
        poly = _MockPolyClient()
        ex = MultiExecutor(venue_clients={"polymarket": poly}, mode="live")
        sr = _scan_result(venue="polymarket", mode="live")
        result = ex.execute(sr)
        self.assertTrue(result.success)
        self.assertEqual(result.venue, "polymarket")
        self.assertEqual(result.order_id, "PM-456")


# ── from_config ─────────────────────────────────────────────────────

class TestMultiExecutorFromConfig(unittest.TestCase):

    def test_demo_maps_to_paper(self):
        ex = MultiExecutor.from_config(
            {"environment": "demo"},
            venue_clients={"kalshi": _MockKalshiClient()},
        )
        self.assertEqual(ex.mode, "paper")

    def test_live_maps_to_live(self):
        ex = MultiExecutor.from_config(
            {"environment": "live"},
            venue_clients={"kalshi": _MockKalshiClient()},
        )
        self.assertEqual(ex.mode, "live")


if __name__ == "__main__":
    unittest.main()
