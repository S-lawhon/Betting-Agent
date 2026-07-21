"""
tests/test_pod_runner_concurrent.py
────────────────────────────────────
Tests for concurrent pod scanning in MultiExecutor.
"""

import time
from typing import List
from unittest import TestCase
from unittest.mock import MagicMock

from src.base_pod import BasePod, ScanResult
from src.multi_executor import MultiExecutor, MultiExecutionResult


def _make_scan_result(pod_id="P-TEST", market_id="MARKET-1", side="YES"):
    return ScanResult(
        fingerprint="fp_" + market_id,
        timestamp_utc="2026-03-12T15:00:00",
        pod_id=pod_id,
        pod_name="Test Pod",
        mode="paper",
        venue="kalshi",
        market_type="sports_test",
        event="Test Event",
        market_id=market_id,
        side=side,
        fair_prob=0.55,
        venue_prob=0.50,
        edge_pct=0.10,
        ev=5.0,
        kelly_fraction=0.05,
        position_size_usd=50.0,
        action="PLACED",
    )


class FakePod(BasePod):
    """A test pod that sleeps to simulate I/O then returns scan results."""

    def __init__(self, pod_id, delay=0.0, results=None, error=None):
        # Skip BasePod.__init__ — we don't need log loading for tests
        self.pod_id = pod_id
        self.pod_name = f"Test {pod_id}"
        self.mode = "paper"
        self._delay = delay
        self._results = results or []
        self._error = error
        self._scan_count = 0

    def scan_once(self) -> List[ScanResult]:
        self._scan_count += 1
        if self._delay > 0:
            time.sleep(self._delay)
        if self._error:
            raise self._error
        return self._results

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config, **overrides):
        return cls(pod_id="P-TEST")


class TestConcurrentScanning(TestCase):
    """Test that concurrent scanning actually runs pods in parallel."""

    def _make_executor(self):
        return MultiExecutor(
            venue_clients={},
            risk_manager=None,
            aggregate_risk=None,
            mode="paper",
        )

    def test_concurrent_is_faster_than_sequential(self):
        """Two pods each sleeping 0.2s should complete in ~0.2s concurrent, ~0.4s sequential."""
        r1 = _make_scan_result(pod_id="P-A", market_id="M-A")
        r2 = _make_scan_result(pod_id="P-B", market_id="M-B")
        pods = [
            FakePod("P-A", delay=0.2, results=[r1]),
            FakePod("P-B", delay=0.2, results=[r2]),
        ]
        executor = self._make_executor()

        start = time.monotonic()
        results = executor.run_cycle(pods, concurrent=True)
        concurrent_time = time.monotonic() - start

        # Should be ~0.2s (parallel), not ~0.4s (sequential)
        self.assertLess(concurrent_time, 0.35,
                        f"Concurrent took {concurrent_time:.3f}s — should be < 0.35s")
        self.assertEqual(len(results), 2)

    def test_sequential_fallback(self):
        """With concurrent=False, pods run sequentially."""
        r1 = _make_scan_result(pod_id="P-A", market_id="M-A")
        pods = [
            FakePod("P-A", delay=0.1, results=[r1]),
            FakePod("P-B", delay=0.1, results=[]),
        ]
        executor = self._make_executor()

        start = time.monotonic()
        results = executor.run_cycle(pods, concurrent=False)
        seq_time = time.monotonic() - start

        # Sequential: should take ~0.2s (0.1 + 0.1)
        self.assertGreaterEqual(seq_time, 0.18)
        self.assertEqual(len(results), 1)  # only P-A has results

    def test_single_pod_runs_sequentially(self):
        """A single pod always runs sequentially (no thread overhead)."""
        r1 = _make_scan_result(pod_id="P-A", market_id="M-A")
        pods = [FakePod("P-A", delay=0.0, results=[r1])]
        executor = self._make_executor()

        results = executor.run_cycle(pods, concurrent=True)
        self.assertEqual(len(results), 1)

    def test_pod_error_does_not_crash_others(self):
        """If one pod raises, the other pods' results still come through."""
        r1 = _make_scan_result(pod_id="P-A", market_id="M-A")
        pods = [
            FakePod("P-A", delay=0.0, results=[r1]),
            FakePod("P-B", delay=0.0, error=RuntimeError("API down")),
        ]
        executor = self._make_executor()

        results = executor.run_cycle(pods, concurrent=True)

        # Should have 2 results: 1 from P-A (executed), 1 error from P-B
        self.assertEqual(len(results), 2)

        # P-B's error should be captured
        error_results = [r for r in results if r.error and "API down" in r.error]
        self.assertEqual(len(error_results), 1)

        # P-A should have produced a result (may be a "no client" error in test
        # env, but the key thing is it wasn't killed by P-B's crash)
        p_a_results = [r for r in results if r.pod_id == "P-A"]
        self.assertEqual(len(p_a_results), 1)

    def test_all_pods_scanned(self):
        """Every pod's scan_once is called exactly once."""
        pods = [
            FakePod("P-A", delay=0.0, results=[]),
            FakePod("P-B", delay=0.0, results=[]),
            FakePod("P-C", delay=0.0, results=[]),
        ]
        executor = self._make_executor()

        executor.run_cycle(pods, concurrent=True)

        for pod in pods:
            self.assertEqual(pod._scan_count, 1,
                             f"{pod.pod_id} scanned {pod._scan_count} times")

    def test_results_preserve_pod_order(self):
        """Results should be in pod order (not completion order)."""
        r1 = _make_scan_result(pod_id="P-A", market_id="M-A")
        r2 = _make_scan_result(pod_id="P-B", market_id="M-B")
        r3 = _make_scan_result(pod_id="P-C", market_id="M-C")
        pods = [
            FakePod("P-A", delay=0.15, results=[r1]),   # slowest
            FakePod("P-B", delay=0.05, results=[r2]),    # fastest
            FakePod("P-C", delay=0.10, results=[r3]),    # medium
        ]
        executor = self._make_executor()

        results = executor.run_cycle(pods, concurrent=True)

        pod_ids = [r.pod_id for r in results]
        self.assertEqual(pod_ids, ["P-A", "P-B", "P-C"],
                         "Results should be in pod list order, not completion order")

    def test_empty_pods_list(self):
        executor = self._make_executor()
        results = executor.run_cycle([], concurrent=True)
        self.assertEqual(results, [])
