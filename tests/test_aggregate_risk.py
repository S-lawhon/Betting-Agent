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


# ── Intra-cycle reservations ────────────────────────────────────────

class _SizedResult(_MockResult):
    """_MockResult that also carries the USD actually put at risk."""

    def __init__(self, position_size_usd=0.0, **kw):
        super().__init__(**kw)
        self.position_size_usd = position_size_usd


def _burst_guard(**kw):
    """Guard where only the per-pod cap ($250 of $1000) can bind."""
    defaults = dict(
        bankroll=1_000,
        max_pod_exposure_pct=0.25,
        max_total_exposure_pct=1.0,
        max_venue_exposure_pct=1.0,
        max_open_positions=10_000,
        _now_fn=lambda: _NOW,
    )
    defaults.update(kw)
    return AggregateRiskGuard(**defaults)


class TestIntraCycleReservations(unittest.TestCase):
    """A pod placing its whole book in ONE scan must still be capped.

    Exposure is only registered in update_post_cycle, so without
    reservations every trade in a burst is checked against the previous
    cycle's (usually zero) exposure and all of them are approved.  This is
    the P-017 golf case: all candidates become visible together, 4-10 days
    before close, so the pod places everything in a single scan.
    """

    def test_burst_is_cut_off_at_pod_exposure_cap(self):
        guard = _burst_guard()
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-017", "kalshi", f"MKT-{m}", 10.0)
        ]
        # $250 cap / $10 per trade = 25 trades, not all 100.
        self.assertEqual(len(approved), 25)
        self.assertAlmostEqual(guard._reserved(pod_id="P-017"), 250.0)

    def test_unreserved_check_trade_is_the_old_leaky_behaviour(self):
        """check_trade alone stays read-only — this is what the bug was."""
        guard = _burst_guard()
        approved = [
            m for m in range(100)
            if guard.check_trade("P-017", "kalshi", 10.0)
        ]
        self.assertEqual(len(approved), 100)

    def test_venue_cap_also_binds_within_a_cycle(self):
        guard = _burst_guard(max_venue_exposure_pct=0.10, max_pod_exposure_pct=1.0)
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-017", "kalshi", f"MKT-{m}", 10.0)
        ]
        self.assertEqual(len(approved), 10)

    def test_total_cap_also_binds_within_a_cycle(self):
        guard = _burst_guard(max_total_exposure_pct=0.05, max_pod_exposure_pct=1.0)
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-017", "kalshi", f"MKT-{m}", 10.0)
        ]
        self.assertEqual(len(approved), 5)

    def test_position_count_cap_counts_reservations(self):
        guard = _burst_guard(max_open_positions=3)
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-017", "kalshi", f"MKT-{m}", 1.0)
        ]
        self.assertEqual(len(approved), 3)

    def test_exempt_pod_still_capped_per_pod_within_a_cycle(self):
        guard = _burst_guard(exempt_pods=["P-014"])
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-014", "kalshi", f"MKT-{m}", 10.0)
        ]
        self.assertEqual(len(approved), 25)

    def test_reserving_same_market_twice_does_not_compound(self):
        """Pod pre-check then executor re-check must not double-count."""
        guard = _burst_guard()
        self.assertTrue(guard.reserve_trade("P-017", "kalshi", "MKT-1", 100.0))
        self.assertTrue(guard.reserve_trade("P-017", "kalshi", "MKT-1", 100.0))
        self.assertAlmostEqual(guard._reserved(pod_id="P-017"), 100.0)
        self.assertEqual(len(guard._reservations), 1)

    def test_reserve_without_market_id_falls_back_to_plain_check(self):
        guard = _burst_guard()
        self.assertTrue(guard.reserve_trade("P-017", "kalshi", "", 10.0))
        self.assertEqual(guard._reservations, {})


class TestReservationsDoNotLeak(unittest.TestCase):
    """An approved trade the pod later drops must give its capacity back.

    P-017 drops positions under $1 once depth caps apply, and the executor
    can reject or error after approval.  If those reservations stuck, the
    pod would starve itself a little more every cycle.
    """

    def test_post_cycle_releases_reservations_that_never_placed(self):
        guard = _burst_guard()
        for m in range(25):
            guard.reserve_trade("P-017", "kalshi", f"MKT-{m}", 10.0)
        self.assertEqual(len(guard._reservations), 25)

        # Only one of them actually became a position.
        guard.update_post_cycle(_MockReport(results=[
            _SizedResult(pod_id="P-017", market_id="MKT-0",
                         position_size_usd=10.0),
        ]))

        self.assertEqual(guard._reservations, {})
        self.assertEqual(list(guard._open_positions), ["MKT-0"])
        self.assertAlmostEqual(guard._pod_exposure["P-017"], 10.0)

        # Capacity is back: the pod can fill the cap again next cycle.
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-017", "kalshi", f"NEXT-{m}", 10.0)
        ]
        self.assertEqual(len(approved), 24)  # 240 more on top of the 10 held

    def test_errored_result_does_not_become_a_position(self):
        guard = _burst_guard()
        guard.reserve_trade("P-017", "kalshi", "MKT-1", 10.0)
        guard.update_post_cycle(_MockReport(results=[
            _SizedResult(pod_id="P-017", market_id="MKT-1",
                         position_size_usd=10.0, success=False,
                         error="boom"),
        ]))
        self.assertEqual(guard._reservations, {})
        self.assertNotIn("MKT-1", guard._open_positions)
        self.assertAlmostEqual(guard._pod_exposure.get("P-017", 0.0), 0.0)

    def test_pre_cycle_clears_reservations_from_a_cycle_that_never_reported(self):
        guard = _burst_guard()
        for m in range(25):
            guard.reserve_trade("P-017", "kalshi", f"MKT-{m}", 10.0)
        # No update_post_cycle — the cycle died before its callback ran.
        guard.check_pre_cycle()
        self.assertEqual(guard._reservations, {})

    def test_explicit_release_frees_capacity_mid_scan(self):
        guard = _burst_guard()
        self.assertTrue(guard.reserve_trade("P-017", "kalshi", "MKT-1", 250.0))
        self.assertFalse(guard.reserve_trade("P-017", "kalshi", "MKT-2", 10.0))
        guard.release_reservation("MKT-1")
        self.assertTrue(guard.reserve_trade("P-017", "kalshi", "MKT-2", 10.0))


class TestPositionAccountingIsReversible(unittest.TestCase):
    """Venue/pod buckets used to only ever go up."""

    def test_close_position_credits_back_venue_and_pod(self):
        guard = _burst_guard()
        guard._add_position("P-017", "kalshi", "MKT-1", 200.0)
        self.assertAlmostEqual(guard._pod_exposure["P-017"], 200.0)

        guard.close_position("MKT-1", pnl=5.0)
        self.assertAlmostEqual(guard._pod_exposure["P-017"], 0.0)
        self.assertAlmostEqual(guard._venue_exposure["kalshi"], 0.0)
        self.assertNotIn("MKT-1", guard._open_positions)

    def test_pod_is_not_muted_after_its_positions_settle(self):
        guard = _burst_guard()
        for m in range(25):
            guard._add_position("P-017", "kalshi", f"MKT-{m}", 10.0)
        self.assertFalse(guard.check_trade("P-017", "kalshi", 10.0))

        for m in range(25):
            guard.close_position(f"MKT-{m}", pnl=0.0)
        self.assertTrue(guard.check_trade("P-017", "kalshi", 10.0))

    def test_add_position_is_idempotent(self):
        guard = _burst_guard()
        guard._add_position("P-017", "kalshi", "MKT-1", 100.0)
        guard._add_position("P-017", "kalshi", "MKT-1", 100.0)
        self.assertAlmostEqual(guard._pod_exposure["P-017"], 100.0)
        self.assertAlmostEqual(guard._venue_exposure["kalshi"], 100.0)

    def test_add_position_replaces_a_resized_market(self):
        guard = _burst_guard()
        guard._add_position("P-017", "kalshi", "MKT-1", 100.0)
        guard._add_position("P-017", "kalshi", "MKT-1", 30.0)
        self.assertAlmostEqual(guard._pod_exposure["P-017"], 30.0)


# ── Bootstrap: paper positions from pods that settle ────────────────

class _StubStore:
    def __init__(self, positions):
        self._positions = positions

    def get_open_positions_for_bootstrap(self):
        return self._positions


_PAPER_POSITIONS = [
    {"pod_id": "P-017", "venue": "kalshi", "market_id": "GOLF-1",
     "position_size_usd": 10.0, "mode": "paper"},
    {"pod_id": "P-002", "venue": "kalshi", "market_id": "ARB-1",
     "position_size_usd": 20.0, "mode": "paper"},
    {"pod_id": "P-001", "venue": "kalshi", "market_id": "LIVE-1",
     "position_size_usd": 30.0, "mode": "live"},
]


class TestBootstrapPaperSkip(unittest.TestCase):
    """Paper positions count for pods that can actually settle them.

    The blanket paper-skip predates the P-015/P-017 settlers.  Since the
    whole engine runs mode: paper, it meant the guard started every
    process believing it had zero exposure.
    """

    def test_none_skips_all_paper(self):
        guard = _burst_guard()
        guard.bootstrap_from_store(_StubStore(_PAPER_POSITIONS))
        self.assertEqual(set(guard._open_positions), {"LIVE-1"})

    def test_paper_tracked_only_for_pods_with_a_settler(self):
        guard = _burst_guard()
        guard.bootstrap_from_store(
            _StubStore(_PAPER_POSITIONS), settled_pod_ids={"P-017", "P-001"},
        )
        # P-017 settles → tracked.  P-002 has no settler → still skipped.
        self.assertEqual(set(guard._open_positions), {"GOLF-1", "LIVE-1"})
        self.assertAlmostEqual(guard._pod_exposure["P-017"], 10.0)

    def test_bootstrapped_exposure_constrains_the_next_burst(self):
        guard = _burst_guard()
        guard.bootstrap_from_store(
            _StubStore([
                {"pod_id": "P-017", "venue": "kalshi", "market_id": f"G-{i}",
                 "position_size_usd": 10.0, "mode": "paper"}
                for i in range(20)
            ]),
            settled_pod_ids={"P-017"},
        )
        # $200 already at risk against a $250 cap → only 5 more $10 trades.
        approved = [
            m for m in range(100)
            if guard.reserve_trade("P-017", "kalshi", f"NEW-{m}", 10.0)
        ]
        self.assertEqual(len(approved), 5)


class TestSettledPodIds(unittest.TestCase):

    def test_collects_pod_ids_from_built_settlers(self):
        from src.engine import _settled_pod_ids

        class _S:
            def __init__(self, pod_id):
                self._pod_id = pod_id

        deps = {
            "settler": object(),              # generic Kalshi settler
            "golf_settler": _S("P-017"),
            "tennis_settler": _S("P-015"),
            "polymarket_settler": None,       # not built
            "nowcast_client": object(),       # not a settler
        }
        # P-014 rides on the generic settler's explicit tuple — it has no
        # settler object of its own, so it can only appear via _SETTLER_DEP_PODS.
        self.assertEqual(
            _settled_pod_ids(deps), {"P-001", "P-014", "", "P-017", "P-015"},
        )

    def test_empty_when_no_settlers(self):
        from src.engine import _settled_pod_ids
        self.assertEqual(_settled_pod_ids({"nowcast_client": object()}), set())


class TestShadowMode(unittest.TestCase):
    """enforce=False: measure every limit, block nothing."""

    def _guard(self, **kw):
        kw.setdefault("bankroll", 1000.0)
        kw.setdefault("enforce", False)
        kw.setdefault("_now_fn", lambda: _NOW)
        return AggregateRiskGuard(**kw)

    def test_over_total_exposure_is_allowed_through(self):
        g = self._guard(max_total_exposure_pct=0.10)
        g._add_position("P-001", "kalshi", "MKT-A", 500.0)
        # 500 + 100 = 60% of bankroll, way past the 10% cap.
        self.assertTrue(g.check_trade("P-002", "kalshi", 100.0))
        self.assertEqual(g.shadow_counts.get("total_exposure"), 1)

    def test_enforcing_guard_still_blocks_the_same_trade(self):
        """The shadow path must not change what the limits MEAN."""
        g = self._guard(max_total_exposure_pct=0.10, enforce=True)
        g._add_position("P-001", "kalshi", "MKT-A", 500.0)
        self.assertFalse(g.check_trade("P-002", "kalshi", 100.0))
        self.assertEqual(g.shadow_counts, {})

    def test_venue_and_pod_breaches_are_classified(self):
        g = self._guard(max_venue_exposure_pct=0.05, max_pod_exposure_pct=0.99)
        self.assertTrue(g.check_trade("P-001", "kalshi", 100.0))
        self.assertEqual(g.shadow_counts.get("venue_exposure"), 1)

        g2 = self._guard(max_pod_exposure_pct=0.05, max_venue_exposure_pct=0.99)
        self.assertTrue(g2.check_trade("P-001", "kalshi", 100.0))
        self.assertEqual(g2.shadow_counts.get("pod_exposure"), 1)

    def test_position_count_breach_is_allowed_through(self):
        g = self._guard(max_open_positions=1)
        g._add_position("P-001", "kalshi", "MKT-A", 10.0)
        self.assertTrue(g.check_trade("P-002", "kalshi", 10.0))
        self.assertEqual(g.shadow_counts.get("position_count"), 1)

    def test_halt_does_not_stop_cycles_or_trades(self):
        """The portfolio-wide halt is the worst offender for research —
        it blanks every pod at once. Shadow must record it, not apply it."""
        g = self._guard(max_daily_loss_pct=0.05)
        g.record_pnl(-100.0)              # 10% of bankroll → halt
        self.assertTrue(g.is_halted)      # state is still tracked...
        self.assertTrue(g.check_pre_cycle())   # ...but nothing is blocked
        self.assertTrue(g.check_trade("P-001", "kalshi", 5.0))
        self.assertEqual(g.shadow_counts.get("halted"), 1)

    def test_observed_halt_does_not_report_as_halted(self):
        """health_check, manager/checks, web_dashboard and both HTML
        templates read snapshot.halted as 'trading is stopped'. In shadow
        mode nothing is stopped, so reporting True would page the whole
        monitoring stack for a halt that halted nothing."""
        g = self._guard(max_daily_loss_pct=0.05)
        g.record_pnl(-100.0)
        snap = g.snapshot()
        self.assertFalse(snap.halted)       # nothing actually stopped
        self.assertTrue(snap.would_halt)    # ...but it is observable
        self.assertIn("daily_loss_limit", snap.halt_reason or "")

    def test_enforcing_halt_reports_as_halted(self):
        g = self._guard(max_daily_loss_pct=0.05, enforce=True)
        g.record_pnl(-100.0)
        snap = g.snapshot()
        self.assertTrue(snap.halted)
        self.assertTrue(snap.would_halt)

    def test_halt_still_stops_everything_when_enforcing(self):
        g = self._guard(max_daily_loss_pct=0.05, enforce=True)
        g.record_pnl(-100.0)
        self.assertFalse(g.check_pre_cycle())
        self.assertFalse(g.check_trade("P-001", "kalshi", 5.0))

    def test_exposure_accounting_still_runs_in_shadow(self):
        """Reservations must keep working — the recorded verdicts are
        only meaningful if the book state behind them is real."""
        g = self._guard(max_total_exposure_pct=0.10)
        self.assertTrue(g.reserve_trade("P-001", "kalshi", "MKT-A", 80.0))
        self.assertEqual(g._reserved(), 80.0)

    def test_breach_is_recorded_once_per_market_per_cycle(self):
        """Pod pre-check and executor check the same market; counting it
        twice would overstate the constraint on replay."""
        g = self._guard(max_total_exposure_pct=0.01)
        g.check_trade("P-001", "kalshi", 50.0, market_id="MKT-A")
        g.check_trade("P-001", "kalshi", 50.0, market_id="MKT-A")
        self.assertEqual(g.shadow_counts.get("total_exposure"), 1)

    def test_dedup_resets_at_the_cycle_boundary(self):
        g = self._guard(max_total_exposure_pct=0.01)
        g.check_trade("P-001", "kalshi", 50.0, market_id="MKT-A")
        g.check_pre_cycle()
        g.check_trade("P-001", "kalshi", 50.0, market_id="MKT-A")
        self.assertEqual(g.shadow_counts.get("total_exposure"), 2)

    def test_snapshot_reports_shadow_state(self):
        g = self._guard(max_total_exposure_pct=0.01)
        g.check_trade("P-001", "kalshi", 50.0, market_id="MKT-A")
        snap = g.snapshot()
        self.assertFalse(snap.enforcing)
        self.assertEqual(snap.shadow_counts.get("total_exposure"), 1)

    def test_shadow_log_records_replayable_state(self):
        import json as _json
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "shadow.jsonl"
            g = self._guard(max_total_exposure_pct=0.01, shadow_log_path=path)
            g._add_position("P-009", "kalshi", "MKT-OLD", 25.0)
            g.check_trade("P-001", "kalshi", 50.0, market_id="MKT-A")

            rows = [_json.loads(ln) for ln in path.read_text().splitlines() if ln]

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["pod_id"], "P-001")
        self.assertEqual(row["venue"], "kalshi")
        self.assertEqual(row["market_id"], "MKT-A")
        self.assertEqual(row["position_size_usd"], 50.0)
        self.assertEqual(row["breach_kind"], "total_exposure")
        # Exposure state at decision time is what a replay needs.
        self.assertEqual(row["bankroll"], 1000.0)
        self.assertEqual(row["total_exposure_usd"], 25.0)
        self.assertEqual(row["open_positions"], 1)
        self.assertEqual(row["limit_pct"], 0.01)

    def test_shadow_log_failure_never_breaks_the_scan(self):
        # Path is a directory → open() raises; the trade must still pass.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            g = self._guard(max_total_exposure_pct=0.01, shadow_log_path=Path(td))
            self.assertTrue(g.check_trade("P-001", "kalshi", 50.0))
        self.assertEqual(g.shadow_counts.get("total_exposure"), 1)


class TestShadowModeConfigGate(unittest.TestCase):
    """enforce:false is paper-only and fails closed."""

    CFG = {
        "risk": {"initial_bankroll": 1000.0},
        "aggregate_risk": {"enforce": False, "shadow_log": "data/shadow.jsonl"},
    }

    def test_paper_mode_honours_shadow(self):
        g = AggregateRiskGuard.from_config(self.CFG, mode="paper")
        self.assertFalse(g.enforce)
        self.assertEqual(g.shadow_log_path, Path("data/shadow.jsonl"))

    def test_live_mode_forces_enforcement_on(self):
        g = AggregateRiskGuard.from_config(self.CFG, mode="live")
        self.assertTrue(g.enforce)

    def test_unstated_mode_forces_enforcement_on(self):
        """The P-022 standalone maker calls from_config() with no mode.
        A caller that cannot say it is paper does not get shadow mode."""
        g = AggregateRiskGuard.from_config(self.CFG)
        self.assertTrue(g.enforce)

    def test_defaults_to_enforcing_when_key_absent(self):
        g = AggregateRiskGuard.from_config(
            {"risk": {}, "aggregate_risk": {}}, mode="paper",
        )
        self.assertTrue(g.enforce)
        self.assertIsNone(g.shadow_log_path)


if __name__ == "__main__":
    unittest.main()
