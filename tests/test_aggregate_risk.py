"""
tests/test_aggregate_risk.py
────────────────────────────
Tests for AggregateRiskGuard.

  ✓ check_pre_cycle: returns True when clear
  ✓ check_pre_cycle: returns False when halted
  ✓ daily loss halt: triggers at threshold
  ✓ daily loss: resets at midnight
  ✓ cooldown: resumes after cooldown_minutes
  ✓ check_trade: rejects over exposure limit
  ✓ check_trade: rejects over venue limit
  ✓ check_trade: rejects over position count
  ✓ check_trade: approves within limits
  ✓ exempt pod: bypasses total exposure cap
  ✓ exempt pod: still respects per-pod limit
  ✓ exempt pod: still respects venue limit
  ✓ exempt pod: still respects halt
  ✓ from_config: reads exempt_pods
  ✓ close_position: removes from tracking
  ✓ emergency_halt: requires manual resume
  ✓ snapshot: captures current state
  ✓ from_config
"""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.aggregate_risk import AggregateRiskGuard, RiskSnapshot

_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class _MockReport:
    def __init__(self, results=None):
        self.results = results or []


class _MockResult:
    def __init__(self, pod_id="P-TEST", venue="kalshi", market_id="MKT-1",
                 success=True, skipped=False, error=None, fill_price=50.0):
        self.pod_id = pod_id
        self.venue = venue
        self.market_id = market_id
        self.success = success
        self.skipped = skipped
        self.error = error
        self.fill_price = fill_price


# ── Tests ───────────────────────────────────────────────────────────

class TestCheckPreCycle(unittest.TestCase):

    def test_returns_true_when_clear(self):
        guard = AggregateRiskGuard(bankroll=10_000, _now_fn=lambda: _NOW)
        self.assertTrue(guard.check_pre_cycle())

    def test_returns_false_when_halted(self):
        guard = AggregateRiskGuard(bankroll=10_000, _now_fn=lambda: _NOW)
        guard.emergency_halt("test")
        self.assertFalse(guard.check_pre_cycle())

    def test_is_halted_property(self):
        guard = AggregateRiskGuard(_now_fn=lambda: _NOW)
        self.assertFalse(guard.is_halted)
        guard.emergency_halt("test")
        self.assertTrue(guard.is_halted)


class TestDailyLoss(unittest.TestCase):

    def test_halt_on_daily_loss(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_daily_loss_pct=0.05,
            _now_fn=lambda: _NOW,
        )
        guard.check_pre_cycle()  # Initialize daily date
        guard.record_pnl(-600)   # -6% > 5% threshold
        self.assertFalse(guard.check_pre_cycle())
        self.assertTrue(guard.is_halted)

    def test_no_halt_below_threshold(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_daily_loss_pct=0.05,
            _now_fn=lambda: _NOW,
        )
        guard.check_pre_cycle()
        guard.record_pnl(-400)   # -4% < 5% threshold
        self.assertTrue(guard.check_pre_cycle())

    def test_daily_reset_at_midnight(self):
        t = [_NOW]
        guard = AggregateRiskGuard(
            bankroll=10_000, max_daily_loss_pct=0.05,
            _now_fn=lambda: t[0],
        )
        guard.check_pre_cycle()
        guard.record_pnl(-600)  # Halted
        self.assertFalse(guard.check_pre_cycle())

        # Advance to next day + past cooldown
        t[0] = _NOW + timedelta(days=1, hours=2)
        guard._halt_time = _NOW  # Reset halt time
        guard.cooldown_minutes = 60  # 1 hour cooldown
        self.assertTrue(guard.check_pre_cycle())


class TestCooldown(unittest.TestCase):

    def test_resumes_after_cooldown(self):
        t = [_NOW]
        guard = AggregateRiskGuard(
            bankroll=10_000, cooldown_minutes=30,
            _now_fn=lambda: t[0],
        )
        guard._halt("test halt")
        self.assertFalse(guard.check_pre_cycle())

        # Advance 31 minutes
        t[0] = _NOW + timedelta(minutes=31)
        self.assertTrue(guard.check_pre_cycle())

    def test_still_halted_during_cooldown(self):
        t = [_NOW]
        guard = AggregateRiskGuard(
            bankroll=10_000, cooldown_minutes=60,
            _now_fn=lambda: t[0],
        )
        guard._halt("test halt")

        t[0] = _NOW + timedelta(minutes=30)
        self.assertFalse(guard.check_pre_cycle())


class TestCheckTrade(unittest.TestCase):

    def test_approves_within_limits(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_total_exposure_pct=0.50,
            _now_fn=lambda: _NOW,
        )
        self.assertTrue(guard.check_trade("P-001", "kalshi", 1000))

    def test_rejects_over_total_exposure(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_total_exposure_pct=0.50,
            _now_fn=lambda: _NOW,
        )
        # Add existing exposure
        guard._open_positions["MKT-1"] = 4000
        guard._open_positions["MKT-2"] = 1000
        # 5000 + 1000 = 6000 = 60% > 50%
        self.assertFalse(guard.check_trade("P-001", "kalshi", 1000))

    def test_rejects_over_venue_exposure(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_venue_exposure_pct=0.30,
            _now_fn=lambda: _NOW,
        )
        guard._venue_exposure["kalshi"] = 2500
        # 2500 + 1000 = 3500 = 35% > 30%
        self.assertFalse(guard.check_trade("P-001", "kalshi", 1000))

    def test_rejects_over_position_count(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_open_positions=2,
            _now_fn=lambda: _NOW,
        )
        guard._open_positions["MKT-1"] = 100
        guard._open_positions["MKT-2"] = 100
        self.assertFalse(guard.check_trade("P-001", "kalshi", 100))

    def test_rejects_when_halted(self):
        guard = AggregateRiskGuard(_now_fn=lambda: _NOW)
        guard.emergency_halt("test")
        self.assertFalse(guard.check_trade("P-001", "kalshi", 100))


class TestExemptPods(unittest.TestCase):

    def test_exempt_pod_bypasses_total_exposure(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_total_exposure_pct=0.50,
            max_pod_exposure_pct=0.25, exempt_pods=["P-014"],
            _now_fn=lambda: _NOW,
        )
        # Fill portfolio to 60% — over the 50% cap
        guard._open_positions["MKT-1"] = 4000
        guard._open_positions["MKT-2"] = 2000
        # Non-exempt pod is rejected
        self.assertFalse(guard.check_trade("P-001", "kalshi", 500))
        # Exempt pod is allowed through
        self.assertTrue(guard.check_trade("P-014", "kalshi", 500))

    def test_exempt_pod_still_respects_pod_limit(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_pod_exposure_pct=0.10,
            exempt_pods=["P-014"], _now_fn=lambda: _NOW,
        )
        guard._pod_exposure["P-014"] = 900
        # 900 + 200 = 1100 = 11% > 10% per-pod cap
        self.assertFalse(guard.check_trade("P-014", "kalshi", 200))

    def test_exempt_pod_still_respects_venue_limit(self):
        guard = AggregateRiskGuard(
            bankroll=10_000, max_venue_exposure_pct=0.30,
            exempt_pods=["P-014"], _now_fn=lambda: _NOW,
        )
        guard._venue_exposure["kalshi"] = 2900
        # 2900 + 200 = 3100 = 31% > 30% venue cap
        self.assertFalse(guard.check_trade("P-014", "kalshi", 200))

    def test_exempt_pod_still_respects_halt(self):
        guard = AggregateRiskGuard(
            exempt_pods=["P-014"], _now_fn=lambda: _NOW,
        )
        guard.emergency_halt("test")
        self.assertFalse(guard.check_trade("P-014", "kalshi", 100))

    def test_from_config_reads_exempt_pods(self):
        cfg = {
            "risk": {"initial_bankroll": 10_000},
            "aggregate_risk": {
                "exempt_pods": ["P-014", "P-015"],
            },
        }
        guard = AggregateRiskGuard.from_config(cfg)
        self.assertEqual(guard.exempt_pods, {"P-014", "P-015"})

    def test_from_config_defaults_empty(self):
        guard = AggregateRiskGuard.from_config({})
        self.assertEqual(guard.exempt_pods, set())


class TestClosePosition(unittest.TestCase):

    def test_removes_position(self):
        guard = AggregateRiskGuard(_now_fn=lambda: _NOW)
        guard._open_positions["MKT-1"] = 500
        guard.close_position("MKT-1", pnl=50)
        self.assertNotIn("MKT-1", guard._open_positions)

    def test_records_pnl(self):
        guard = AggregateRiskGuard(bankroll=10_000, _now_fn=lambda: _NOW)
        guard.check_pre_cycle()
        guard.close_position("MKT-1", pnl=-200)
        self.assertAlmostEqual(guard._daily_pnl, -200)


class TestEmergencyHalt(unittest.TestCase):

    def test_requires_manual_resume(self):
        t = [_NOW]
        guard = AggregateRiskGuard(
            cooldown_minutes=10, _now_fn=lambda: t[0],
        )
        guard.emergency_halt("manual stop")

        # Even after long time, still halted
        t[0] = _NOW + timedelta(hours=24)
        self.assertFalse(guard.check_pre_cycle())

        # Manual resume
        guard.resume()
        self.assertTrue(guard.check_pre_cycle())


class TestSnapshot(unittest.TestCase):

    def test_snapshot_fields(self):
        guard = AggregateRiskGuard(bankroll=10_000, _now_fn=lambda: _NOW)
        guard._open_positions["MKT-1"] = 500
        guard._venue_exposure["kalshi"] = 500
        snap = guard.snapshot()
        self.assertIsInstance(snap, RiskSnapshot)
        self.assertAlmostEqual(snap.total_exposure_usd, 500)
        self.assertAlmostEqual(snap.total_exposure_pct, 0.05)
        self.assertEqual(snap.open_positions, 1)
        self.assertFalse(snap.halted)


class TestUpdatePostCycle(unittest.TestCase):

    def test_tracks_new_position(self):
        guard = AggregateRiskGuard(_now_fn=lambda: _NOW)
        report = _MockReport(results=[
            _MockResult(market_id="MKT-1", fill_price=50.0),
        ])
        guard.update_post_cycle(report)
        self.assertIn("MKT-1", guard._open_positions)

    def test_skips_skipped_results(self):
        guard = AggregateRiskGuard(_now_fn=lambda: _NOW)
        report = _MockReport(results=[
            _MockResult(market_id="MKT-1", skipped=True),
        ])
        guard.update_post_cycle(report)
        self.assertNotIn("MKT-1", guard._open_positions)


class TestFromConfig(unittest.TestCase):

    def test_from_config(self):
        cfg = {
            "risk": {"initial_bankroll": 20_000},
            "aggregate_risk": {
                "max_total_exposure_pct": 0.40,
                "max_daily_loss_pct": 0.03,
                "cooldown_minutes": 120,
            },
        }
        guard = AggregateRiskGuard.from_config(cfg)
        self.assertAlmostEqual(guard.bankroll, 20_000)
        self.assertAlmostEqual(guard.max_total_exposure_pct, 0.40)
        self.assertAlmostEqual(guard.max_daily_loss_pct, 0.03)
        self.assertAlmostEqual(guard.cooldown_minutes, 120)

    def test_defaults(self):
        guard = AggregateRiskGuard.from_config({})
        self.assertAlmostEqual(guard.bankroll, 10_000)
        self.assertAlmostEqual(guard.max_total_exposure_pct, 0.50)


if __name__ == "__main__":
    unittest.main()
