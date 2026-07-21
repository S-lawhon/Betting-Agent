"""
tests/test_multi_executor_workers.py
────────────────────────────────────
Coverage for the concurrent-scan worker cap.

Kalshi throttles on CONCURRENT CONNECTIONS, not requests/second. Unbounded,
the executor opens one connection per active pod, which was measured
producing 829 HTTP 429s/24h on the droplet (2026-07-20/21) while the
single-threaded P-016 maker on the same box took zero.

These tests pin the property that the cap is actually honoured — a regression
here silently reintroduces the 429 storm, which degrades every pod at once
and can contaminate a running pre-registered gate.
"""

import threading
import time
from typing import List
from unittest import TestCase

from src.base_pod import BasePod, ScanResult
from src.multi_executor import MultiExecutor


def _scan_result(pod_id="P-TEST", market_id="M-1"):
    return ScanResult(
        fingerprint="fp_" + market_id,
        timestamp_utc="2026-07-21T15:00:00",
        pod_id=pod_id, pod_name="Test Pod", mode="paper", venue="kalshi",
        market_type="sports_test", event="Test Event", market_id=market_id,
        side="YES", fair_prob=0.55, venue_prob=0.50, edge_pct=0.10, ev=5.0,
        kelly_fraction=0.05, position_size_usd=50.0, action="PLACED",
    )


class _ConcurrencyProbe(BasePod):
    """Records the peak number of scans running simultaneously."""

    def __init__(self, pod_id, tracker, delay=0.05):
        self.pod_id = pod_id
        self.pod_name = f"Probe {pod_id}"
        self.mode = "paper"
        self._tracker = tracker
        self._delay = delay

    def scan_once(self) -> List[ScanResult]:
        self._tracker.enter()
        try:
            time.sleep(self._delay)
        finally:
            self._tracker.exit()
        return []

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config):        # required by BasePod
        raise NotImplementedError("test double")


class _PeakTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def enter(self):
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def exit(self):
        with self._lock:
            self.current -= 1


class TestScanWorkerCap(TestCase):

    def _executor(self, max_scan_workers=None):
        return MultiExecutor(
            venue_clients={}, risk_manager=None, aggregate_risk=None,
            mode="paper", max_scan_workers=max_scan_workers,
        )

    def test_cap_limits_actual_concurrency(self):
        """The whole point: never more than N pods scanning at once."""
        tracker = _PeakTracker()
        pods = [_ConcurrencyProbe(f"P-{i}", tracker) for i in range(8)]
        self._executor(max_scan_workers=2).run_cycle(pods, concurrent=True)
        self.assertLessEqual(
            tracker.peak, 2,
            f"peak concurrency {tracker.peak} exceeded the cap of 2 — "
            "the 429 storm is back",
        )

    def test_cap_of_one_serialises(self):
        tracker = _PeakTracker()
        pods = [_ConcurrencyProbe(f"P-{i}", tracker) for i in range(4)]
        self._executor(max_scan_workers=1).run_cycle(pods, concurrent=True)
        self.assertEqual(tracker.peak, 1)

    def test_unset_cap_preserves_old_unbounded_behaviour(self):
        """Default must not change behaviour for callers that never configure it."""
        tracker = _PeakTracker()
        pods = [_ConcurrencyProbe(f"P-{i}", tracker) for i in range(4)]
        self._executor(max_scan_workers=None).run_cycle(pods, concurrent=True)
        self.assertGreater(tracker.peak, 1)

    def test_explicit_arg_overrides_configured_cap(self):
        tracker = _PeakTracker()
        pods = [_ConcurrencyProbe(f"P-{i}", tracker) for i in range(6)]
        self._executor(max_scan_workers=4).run_cycle(
            pods, concurrent=True, max_workers=1
        )
        self.assertEqual(tracker.peak, 1)

    def test_all_pods_still_scanned_under_cap(self):
        """Capping concurrency must not drop pods — only serialise them."""
        scanned = []
        lock = threading.Lock()

        class _Recording(_ConcurrencyProbe):
            def scan_once(self_inner):
                with lock:
                    scanned.append(self_inner.pod_id)
                return [_scan_result(pod_id=self_inner.pod_id,
                                     market_id=self_inner.pod_id)]

        tracker = _PeakTracker()
        pods = [_Recording(f"P-{i}", tracker) for i in range(6)]
        results = self._executor(max_scan_workers=2).run_cycle(
            pods, concurrent=True
        )
        self.assertEqual(sorted(scanned), sorted(f"P-{i}" for i in range(6)))
        self.assertEqual(len(results), 6)

    def test_pod_error_still_isolated_under_cap(self):
        tracker = _PeakTracker()

        class _Exploding(_ConcurrencyProbe):
            def scan_once(self_inner):
                raise RuntimeError("API down")

        pods = [
            _ConcurrencyProbe("P-OK", tracker),
            _Exploding("P-BAD", tracker),
        ]
        results = self._executor(max_scan_workers=1).run_cycle(
            pods, concurrent=True
        )
        errors = [r for r in results if r.error]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].pod_id, "P-BAD")


class TestFromConfig(TestCase):

    def _build(self, config):
        return MultiExecutor.from_config(config, venue_clients={})

    def test_reads_max_scan_workers(self):
        ex = self._build({"execution": {"max_scan_workers": 3}})
        self.assertEqual(ex.max_scan_workers, 3)

    def test_absent_block_leaves_unbounded(self):
        self.assertIsNone(self._build({}).max_scan_workers)

    def test_absent_key_leaves_unbounded(self):
        self.assertIsNone(self._build({"execution": {}}).max_scan_workers)

    def test_zero_is_clamped_to_one_not_treated_as_unset(self):
        """0 workers would deadlock; falsy-vs-None is the trap here."""
        self.assertEqual(
            self._build({"execution": {"max_scan_workers": 0}}).max_scan_workers,
            1,
        )

    def test_garbage_value_falls_back_to_unbounded(self):
        ex = self._build({"execution": {"max_scan_workers": "lots"}})
        self.assertIsNone(ex.max_scan_workers)

    def test_shipped_config_sets_a_cap(self):
        """The deployed config must actually carry the fix."""
        import yaml
        with open("config_multi_pod.yaml") as fh:
            cfg = yaml.safe_load(fh)
        workers = (cfg.get("execution") or {}).get("max_scan_workers")
        self.assertIsNotNone(workers, "execution.max_scan_workers is missing")
        self.assertLessEqual(workers, 3)
        self.assertGreaterEqual(workers, 1)

    def test_shipped_config_pins_the_kalshi_rate_limit(self):
        """kalshi.rate_limit must be present and below the 5 req/s default.

        The block being ABSENT is the actual failure mode: KalshiClient
        silently falls back to 5 req/s burst 10, which is where 100% of the
        droplet's 429s came from (measured 2026-07-21). An absent block looks
        identical to a configured one unless something asserts on it.
        """
        import yaml
        with open("config_multi_pod.yaml") as fh:
            cfg = yaml.safe_load(fh)
        rl = (cfg.get("kalshi") or {}).get("rate_limit")
        self.assertIsNotNone(rl, "kalshi.rate_limit is missing — defaults to 5 req/s")
        self.assertLessEqual(
            rl.get("requests_per_second", 5), 4,
            "rate is at or above the default that caused the 429 storm",
        )
        self.assertGreaterEqual(rl.get("requests_per_second", 0), 1)
        self.assertLessEqual(rl.get("burst", 10), 10)
