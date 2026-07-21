"""
tests/test_settlement_cadence.py
────────────────────────────────
Coverage for the settlement-pass cadence gate in _run_guarded_loop.

All four settlers used to poll on EVERY scan cycle. Measured 2026-07-21 on
the droplet, that was the dominant source of HTTP 429s — every 429 cluster
fell in a settler-pass minute or the one after it, and none fell anywhere
else. Nothing in the book resolves on a 5-minute boundary.

The properties worth pinning:
  - the gate actually reduces settler calls
  - a restart still settles immediately (iteration 1 always runs)
  - the default is unchanged behaviour, so no caller is silently altered
  - scanning is NOT gated — only settlement
"""

from typing import List, Optional
from unittest import TestCase

from src.engine import _run_guarded_loop


class _FakeSettler:
    def __init__(self):
        self.calls = 0

    def settle_cycle(self):
        self.calls += 1
        return []


class _FakeGuard:
    def __init__(self, allow=True):
        self._allow = allow
        self.closed = []

    def check_pre_cycle(self):
        return self._allow

    def close_position(self, market_id, pnl):
        self.closed.append((market_id, pnl))

    def snapshot(self):
        class _S:
            halt_reason = "test"
        return _S()


class _FakeReport:
    placed_count = 0


class _FakeRunner:
    def __init__(self):
        self.cycles = 0
        self.pods: List = []

    def run_once(self):
        self.cycles += 1
        return _FakeReport()


def _run(cycles, interval_cycles, n_settlers=1):
    runner, guard = _FakeRunner(), _FakeGuard()
    settlers = [_FakeSettler() for _ in range(n_settlers)]
    kwargs = {}
    names = ["settler", "polymarket_settler", "tennis_settler", "golf_settler"]
    for name, s in zip(names, settlers):
        kwargs[name] = s
    _run_guarded_loop(
        runner, guard, interval=0.0, max_cycles=cycles,
        settlement_interval_cycles=interval_cycles, **kwargs
    )
    return runner, settlers


class TestSettlementCadence(TestCase):

    def test_default_settles_every_cycle(self):
        """Unchanged behaviour for any caller that does not set the knob."""
        runner, (settler,) = _run(cycles=6, interval_cycles=1)
        self.assertEqual(settler.calls, 6)
        self.assertEqual(runner.cycles, 6)

    def test_gate_reduces_settlement_passes(self):
        # 12 cycles, every 6th -> iterations 1 and 7
        _, (settler,) = _run(cycles=12, interval_cycles=6)
        self.assertEqual(settler.calls, 2)

    def test_first_cycle_always_settles(self):
        """A restart must reconcile immediately, not wait N cycles."""
        _, (settler,) = _run(cycles=1, interval_cycles=100)
        self.assertEqual(settler.calls, 1)

    def test_scanning_is_not_gated(self):
        """Only settlement is throttled — every cycle still scans."""
        runner, (settler,) = _run(cycles=12, interval_cycles=6)
        self.assertEqual(runner.cycles, 12)
        self.assertEqual(settler.calls, 2)

    def test_all_four_settlers_are_gated_together(self):
        _, settlers = _run(cycles=12, interval_cycles=6, n_settlers=4)
        for s in settlers:
            self.assertEqual(s.calls, 2)

    def test_zero_or_none_falls_back_to_every_cycle(self):
        """0 would be a divide-by-zero; None is 'unset'. Both mean 'every'."""
        for bad in (0, None):
            _, (settler,) = _run(cycles=4, interval_cycles=bad)
            self.assertEqual(settler.calls, 4, f"interval_cycles={bad!r}")

    def test_negative_is_clamped_not_crashing(self):
        _, (settler,) = _run(cycles=4, interval_cycles=-3)
        self.assertEqual(settler.calls, 4)

    def test_settler_exception_does_not_break_the_loop(self):
        class _Exploding(_FakeSettler):
            def settle_cycle(self):
                self.calls += 1
                raise RuntimeError("kalshi down")

        runner, guard = _FakeRunner(), _FakeGuard()
        boom = _Exploding()
        _run_guarded_loop(
            runner, guard, interval=0.0, max_cycles=3,
            settler=boom, settlement_interval_cycles=1,
        )
        self.assertEqual(boom.calls, 3)
        self.assertEqual(runner.cycles, 3)


class TestShippedConfig(TestCase):

    def test_config_sets_a_sane_cadence(self):
        """The deployed config must carry the fix, and stay under the
        fastest real settlement time (tennis/MLB resolve in hours)."""
        import yaml
        with open("config_multi_pod.yaml") as fh:
            cfg = yaml.safe_load(fh)
        n = (cfg.get("settlement") or {}).get("interval_cycles")
        self.assertIsNotNone(n, "settlement.interval_cycles is missing")
        self.assertGreaterEqual(n, 1)
        # 5-min cycles: keep the settlement lag under an hour
        self.assertLessEqual(n * 5, 60, "settlement lag exceeds one hour")
