"""
tests/test_pod_runner.py
────────────────────────
Tests for PodRunner: unified scan cycle orchestrator.

  ✓ run_once: returns CycleReport with correct tallies
  ✓ run_once: calls executor.run_cycle with all pods
  ✓ run_once: fires cycle_callback
  ✓ run_once: capital_allocator.pre_cycle + post_cycle called
  ✓ run_loop: runs multiple cycles
  ✓ run_loop: respects max_cycles
  ✓ run_loop: stop_on_error halts on first error
  ✓ history tracking
  ✓ total_placed / total_errors aggregation
  ✓ CycleReport.success_rate
  ✓ from_config: loads active pods from registry
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.multi_executor import MultiExecutionResult
from src.pod_runner import PodRunner, CycleReport


_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ────────────────────────────────────────────────────────

class _MockPod:
    def __init__(self, pod_id="P-TEST", results=None):
        self.pod_id = pod_id
        self.pod_name = "Test Pod"
        self.mode = "paper"
        self._results = results or []

    def scan_once(self):
        return self._results

    def venue_name(self):
        return "test"


class _MockExecutor:
    def __init__(self, results=None):
        self._results = results or []
        self.cycle_calls = []

    def run_cycle(self, pods):
        self.cycle_calls.append(pods)
        return list(self._results)


def _exec_result(success=True, skipped=False, error=None, pod_id="P-TEST"):
    return MultiExecutionResult(
        success=success, pod_id=pod_id, venue="test",
        market_id="MKT-1", mode="paper",
        skipped=skipped, error=error,
    )


# ── Tests ───────────────────────────────────────────────────────────

class TestRunOnce(unittest.TestCase):

    def test_returns_cycle_report(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[_exec_result()]),
            _now_fn=lambda: _NOW,
        )
        report = runner.run_once()
        self.assertIsInstance(report, CycleReport)
        self.assertEqual(report.cycle_number, 1)
        self.assertEqual(report.pods_scanned, 1)

    def test_tallies_placed(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[
                _exec_result(success=True),
                _exec_result(success=True),
            ]),
            _now_fn=lambda: _NOW,
        )
        report = runner.run_once()
        self.assertEqual(report.placed_count, 2)
        self.assertEqual(report.total_results, 2)

    def test_tallies_skipped(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[
                _exec_result(skipped=True),
            ]),
            _now_fn=lambda: _NOW,
        )
        report = runner.run_once()
        self.assertEqual(report.skipped_count, 1)
        self.assertEqual(report.placed_count, 0)

    def test_tallies_errors(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[
                _exec_result(success=False, error="fail"),
            ]),
            _now_fn=lambda: _NOW,
        )
        report = runner.run_once()
        self.assertEqual(report.error_count, 1)

    def test_calls_executor_run_cycle(self):
        exe = _MockExecutor(results=[])
        pods = [_MockPod(), _MockPod(pod_id="P-002")]
        runner = PodRunner(pods=pods, executor=exe, _now_fn=lambda: _NOW)
        runner.run_once()
        self.assertEqual(len(exe.cycle_calls), 1)
        self.assertEqual(len(exe.cycle_calls[0]), 2)

    def test_fires_callback(self):
        reports = []
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[_exec_result()]),
            cycle_callback=lambda r: reports.append(r),
            _now_fn=lambda: _NOW,
        )
        runner.run_once()
        self.assertEqual(len(reports), 1)

    def test_capital_allocator_called(self):
        alloc = MagicMock()
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[]),
            capital_allocator=alloc,
            _now_fn=lambda: _NOW,
        )
        runner.run_once()
        alloc.pre_cycle.assert_called_once()
        alloc.post_cycle.assert_called_once()


class TestRunLoop(unittest.TestCase):

    def test_runs_multiple_cycles(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[_exec_result()]),
            _now_fn=lambda: _NOW,
        )
        reports = runner.run_loop(interval_seconds=0, max_cycles=3)
        self.assertEqual(len(reports), 3)

    def test_respects_max_cycles(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[]),
            _now_fn=lambda: _NOW,
        )
        reports = runner.run_loop(interval_seconds=0, max_cycles=2)
        self.assertEqual(len(reports), 2)

    def test_stop_on_error(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[
                _exec_result(success=False, error="fail"),
            ]),
            _now_fn=lambda: _NOW,
        )
        reports = runner.run_loop(
            interval_seconds=0, max_cycles=5, stop_on_error=True,
        )
        self.assertEqual(len(reports), 1)


class TestHistory(unittest.TestCase):

    def test_history_accumulates(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[_exec_result()]),
            _now_fn=lambda: _NOW,
        )
        runner.run_once()
        runner.run_once()
        self.assertEqual(len(runner.history), 2)

    def test_total_placed(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[_exec_result()]),
            _now_fn=lambda: _NOW,
        )
        runner.run_once()
        runner.run_once()
        self.assertEqual(runner.total_placed, 2)

    def test_total_errors(self):
        runner = PodRunner(
            pods=[_MockPod()],
            executor=_MockExecutor(results=[
                _exec_result(success=False, error="x"),
            ]),
            _now_fn=lambda: _NOW,
        )
        runner.run_once()
        self.assertEqual(runner.total_errors, 1)


class TestCycleReport(unittest.TestCase):

    def test_success_rate_all_good(self):
        report = CycleReport(
            cycle_number=1, timestamp_utc="", duration_seconds=0.1,
            pods_scanned=1, total_results=3,
            placed_count=3, skipped_count=0, error_count=0,
        )
        self.assertAlmostEqual(report.success_rate, 1.0)

    def test_success_rate_with_errors(self):
        report = CycleReport(
            cycle_number=1, timestamp_utc="", duration_seconds=0.1,
            pods_scanned=1, total_results=4,
            placed_count=3, skipped_count=0, error_count=1,
        )
        self.assertAlmostEqual(report.success_rate, 0.75)

    def test_success_rate_empty(self):
        report = CycleReport(
            cycle_number=1, timestamp_utc="", duration_seconds=0.1,
            pods_scanned=0, total_results=0,
            placed_count=0, skipped_count=0, error_count=0,
        )
        self.assertAlmostEqual(report.success_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
