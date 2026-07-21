"""
tests/test_risk_manager.py
──────────────────────────
Phase 2 must-pass tests:
  ✓ can_trade() returns True when all conditions met
  ✓ can_trade() blocks on max_positions
  ✓ can_trade() blocks on max_exposure
  ✓ can_trade() blocks on max_daily_loss → triggers cooldown
  ✓ can_trade() blocks during cooldown
  ✓ can_trade() blocks below min_confidence
  ✓ Cooldown expires and trading resumes automatically
  ✓ calculate_position_size() scales by bankroll
  ✓ Ledger: open/close tracks P&L, exposure, drawdown correctly
  ✓ Ledger: daily_pnl only counts today's closed positions
  ✓ Ledger: bankroll updates on close
  ✓ Ledger: peak equity and drawdown are monotone-correct
  ✓ RiskManager.from_config wires correctly
  ✓ Snapshot reflects accurate state
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.risk_manager import (
    ClosedPosition,
    Ledger,
    LedgerSnapshot,
    Position,
    RiskManager,
    TradeDecision,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(year=2024, month=6, day=1, hour=12, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _make_ledger(bankroll: float = 10_000.0, now: datetime = None) -> Ledger:
    t = now or _utc()
    return Ledger(initial_bankroll=bankroll, _now_fn=lambda: t)


def _make_rm(ledger: Ledger = None, now_fn=None, **kwargs) -> RiskManager:
    ledger = ledger or Ledger(10_000.0, _now_fn=now_fn)
    return RiskManager(
        ledger=ledger,
        max_positions=5,
        max_exposure_pct=0.20,
        max_daily_loss_pct=0.05,
        cooldown_minutes=60,
        kelly_fraction=0.25,
        max_bet_pct=0.03,
        min_confidence=0.55,
        _now_fn=now_fn,
        **kwargs,
    )


def _open_pos(
    ledger: Ledger,
    ticker: str = "MARKET-1",
    side: str = "YES",
    size: float = 100.0,
    price: float = 0.50,
    at: datetime = None,
) -> Position:
    pos = Position.new(
        market_ticker=ticker,
        side=side,
        size_usd=size,
        kalshi_price=price,
        opened_at=at or _utc(),
    )
    ledger.open_position(pos)
    return pos


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Position dataclass
# ═══════════════════════════════════════════════════════════════════════════════

class TestPosition(unittest.TestCase):

    def test_position_new_assigns_unique_ids(self):
        p1 = Position.new("M-1", "YES", 100.0, 0.50)
        p2 = Position.new("M-1", "YES", 100.0, 0.50)
        self.assertNotEqual(p1.position_id, p2.position_id)

    def test_position_fields(self):
        p = Position.new("MARKET-ABC", "NO", 250.0, 0.45)
        self.assertEqual(p.market_ticker, "MARKET-ABC")
        self.assertEqual(p.side, "NO")
        self.assertAlmostEqual(p.size_usd, 250.0)
        self.assertAlmostEqual(p.kalshi_price, 0.45)
        self.assertIsNotNone(p.position_id)
        self.assertIsNotNone(p.opened_at)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TradeDecision
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeDecision(unittest.TestCase):

    def test_ok_decision_is_allowed(self):
        d = TradeDecision.ok()
        self.assertTrue(d.allowed)
        self.assertEqual(d.block_code, TradeDecision.OK)

    def test_blocked_decision_is_not_allowed(self):
        d = TradeDecision.blocked(TradeDecision.BLOCK_MAX_POSITIONS, "Too many.")
        self.assertFalse(d.allowed)
        self.assertEqual(d.block_code, TradeDecision.BLOCK_MAX_POSITIONS)
        self.assertIn("Too many", d.reason)

    def test_frozen(self):
        d = TradeDecision.ok()
        with self.assertRaises((AttributeError, TypeError)):
            d.allowed = False  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Ledger
# ═══════════════════════════════════════════════════════════════════════════════

class TestLedgerBasics(unittest.TestCase):

    def test_initial_state(self):
        ledger = _make_ledger(5_000.0)
        self.assertAlmostEqual(ledger.bankroll, 5_000.0)
        self.assertEqual(ledger.open_count, 0)
        self.assertAlmostEqual(ledger.exposure_usd, 0.0)
        self.assertAlmostEqual(ledger.drawdown_usd, 0.0)

    def test_rejects_non_positive_bankroll(self):
        with self.assertRaises(ValueError):
            Ledger(0.0)
        with self.assertRaises(ValueError):
            Ledger(-1000.0)

    def test_open_position_increments_count(self):
        ledger = _make_ledger()
        _open_pos(ledger, "M-1", size=200.0)
        self.assertEqual(ledger.open_count, 1)

    def test_open_position_increases_exposure(self):
        ledger = _make_ledger()
        _open_pos(ledger, "M-1", size=300.0)
        _open_pos(ledger, "M-2", size=200.0)
        self.assertAlmostEqual(ledger.exposure_usd, 500.0)

    def test_duplicate_open_raises(self):
        ledger = _make_ledger()
        pos = _open_pos(ledger)
        with self.assertRaises(ValueError):
            ledger.open_position(pos)  # same id again

    def test_close_unknown_position_raises(self):
        ledger = _make_ledger()
        with self.assertRaises(KeyError):
            ledger.close_position("nonexistent-id", 50.0)


class TestLedgerPnL(unittest.TestCase):

    def test_bankroll_increases_on_winning_close(self):
        ledger = _make_ledger(10_000.0)
        pos = _open_pos(ledger, size=500.0, price=0.50)
        ledger.close_position(pos.position_id, pnl_usd=+250.0)
        self.assertAlmostEqual(ledger.bankroll, 10_250.0)

    def test_bankroll_decreases_on_losing_close(self):
        ledger = _make_ledger(10_000.0)
        pos = _open_pos(ledger, size=500.0, price=0.50)
        ledger.close_position(pos.position_id, pnl_usd=-500.0)
        self.assertAlmostEqual(ledger.bankroll, 9_500.0)

    def test_exposure_drops_after_close(self):
        ledger = _make_ledger()
        pos1 = _open_pos(ledger, "M-1", size=200.0)
        pos2 = _open_pos(ledger, "M-2", size=300.0)
        ledger.close_position(pos1.position_id, pnl_usd=50.0)
        self.assertAlmostEqual(ledger.exposure_usd, 300.0)
        self.assertEqual(ledger.open_count, 1)

    def test_daily_pnl_sums_todays_closed(self):
        today = _utc(hour=10)
        tomorrow = _utc(day=2, hour=10)
        ledger = Ledger(10_000.0, _now_fn=lambda: today)

        pos1 = _open_pos(ledger, "M-1", at=today)
        pos2 = _open_pos(ledger, "M-2", at=today)
        ledger.close_position(pos1.position_id, pnl_usd=+100.0)
        ledger.close_position(pos2.position_id, pnl_usd=-50.0)

        self.assertAlmostEqual(ledger.daily_pnl(today), 50.0)

    def test_daily_pnl_excludes_other_days(self):
        today = _utc(day=1, hour=10)
        yesterday = _utc(day=1, hour=10)
        tomorrow_dt = _utc(day=2, hour=10)

        ledger = Ledger(10_000.0, _now_fn=lambda: today)
        pos = _open_pos(ledger, "M-1", at=today)
        ledger.close_position(pos.position_id, pnl_usd=-200.0)

        # Tomorrow's P&L should be zero (nothing closed tomorrow)
        self.assertAlmostEqual(ledger.daily_pnl(tomorrow_dt), 0.0)

    def test_multiple_close_pnl_accumulates(self):
        ledger = _make_ledger(10_000.0)
        for i in range(3):
            pos = _open_pos(ledger, f"M-{i}", size=100.0)
            ledger.close_position(pos.position_id, pnl_usd=-100.0)
        # Total P&L today: -300
        now = _utc()
        self.assertAlmostEqual(ledger.daily_pnl(now), -300.0)
        self.assertAlmostEqual(ledger.bankroll, 9_700.0)


class TestLedgerDrawdown(unittest.TestCase):

    def test_no_drawdown_initially(self):
        ledger = _make_ledger(10_000.0)
        self.assertAlmostEqual(ledger.drawdown_usd, 0.0)
        self.assertAlmostEqual(ledger.drawdown_pct, 0.0)

    def test_drawdown_after_loss(self):
        ledger = _make_ledger(10_000.0)
        pos = _open_pos(ledger, size=1_000.0)
        ledger.close_position(pos.position_id, pnl_usd=-1_000.0)
        self.assertAlmostEqual(ledger.drawdown_usd, 1_000.0)
        self.assertAlmostEqual(ledger.drawdown_pct, 0.10, places=5)

    def test_peak_equity_updates_on_profit(self):
        ledger = _make_ledger(10_000.0)
        pos = _open_pos(ledger, size=1_000.0)
        ledger.close_position(pos.position_id, pnl_usd=+2_000.0)
        self.assertAlmostEqual(ledger.peak_equity, 12_000.0)

    def test_drawdown_floor_at_zero(self):
        """Drawdown cannot go negative even when above peak."""
        ledger = _make_ledger(10_000.0)
        pos = _open_pos(ledger, size=100.0)
        ledger.close_position(pos.position_id, pnl_usd=+500.0)
        # Bankroll is now above original — drawdown should be 0
        self.assertAlmostEqual(ledger.drawdown_usd, 0.0)

    def test_drawdown_recovers_after_profit(self):
        ledger = _make_ledger(10_000.0)
        p1 = _open_pos(ledger, "M-1", size=1_000.0)
        ledger.close_position(p1.position_id, pnl_usd=-1_000.0)  # down to 9k

        p2 = _open_pos(ledger, "M-2", size=500.0)
        ledger.close_position(p2.position_id, pnl_usd=+1_500.0)  # up to 10.5k

        self.assertAlmostEqual(ledger.bankroll, 10_500.0)
        self.assertAlmostEqual(ledger.peak_equity, 10_500.0)
        self.assertAlmostEqual(ledger.drawdown_usd, 0.0)


class TestLedgerSnapshot(unittest.TestCase):

    def test_snapshot_fields(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        _open_pos(ledger, "M-1", size=500.0)

        snap = ledger.snapshot()
        self.assertAlmostEqual(snap.bankroll, 10_000.0)
        self.assertEqual(snap.open_positions, 1)
        self.assertAlmostEqual(snap.exposure_usd, 500.0)
        self.assertAlmostEqual(snap.exposure_pct, 0.05)
        self.assertFalse(snap.in_cooldown)
        self.assertIsNone(snap.cooldown_until)

    def test_snapshot_exposure_pct(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        _open_pos(ledger, "M-1", size=2_000.0)
        snap = ledger.snapshot()
        self.assertAlmostEqual(snap.exposure_pct, 0.20, places=5)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — RiskManager.can_trade()
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanTradeAllPass(unittest.TestCase):

    def test_can_trade_when_all_clear(self):
        rm = _make_rm()
        decision = rm.can_trade(proposed_size_usd=100.0, confidence=0.80)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.OK)


class TestCanTradeMinConfidence(unittest.TestCase):

    def test_blocks_below_min_confidence(self):
        rm = _make_rm()
        decision = rm.can_trade(100.0, confidence=0.40)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_MIN_CONFIDENCE)

    def test_allows_at_exactly_min_confidence(self):
        rm = _make_rm()  # min_confidence=0.55
        decision = rm.can_trade(100.0, confidence=0.55)
        self.assertTrue(decision.allowed)

    def test_blocks_just_below_min_confidence(self):
        rm = _make_rm()
        decision = rm.can_trade(100.0, confidence=0.549)
        self.assertFalse(decision.allowed)


class TestCanTradeMaxPositions(unittest.TestCase):

    def test_blocks_when_at_max_positions(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        for i in range(5):  # max_positions=5
            _open_pos(ledger, f"M-{i}", size=100.0)

        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_MAX_POSITIONS)

    def test_allows_one_below_max(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        for i in range(4):  # one below max of 5
            _open_pos(ledger, f"M-{i}", size=100.0)

        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertTrue(decision.allowed)


class TestCanTradeMaxExposure(unittest.TestCase):

    def test_blocks_when_exposure_would_exceed_limit(self):
        # Bankroll 10k, max_exposure 20% = $2000
        # Already have $1900 open → adding $200 would push to 21%
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        _open_pos(ledger, "M-1", size=1_900.0)
        decision = rm.can_trade(proposed_size_usd=200.0, confidence=0.80)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_MAX_EXPOSURE)

    def test_allows_when_just_within_exposure_limit(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        _open_pos(ledger, "M-1", size=1_900.0)
        # $100 more → exactly $2000 = 20%
        decision = rm.can_trade(proposed_size_usd=100.0, confidence=0.80)
        self.assertTrue(decision.allowed)

    def test_exposure_pct_calculated_on_current_bankroll(self):
        """After a loss the bankroll shrinks, making exposure% larger.

        Loss must be below the daily loss limit (5% = $500) so that daily-loss
        check doesn't fire before the exposure check.
        Lose $400 → bankroll $9,600. Existing $1,800 open (18.75%).
        Propose $200 → projected $2,000 / $9,600 = 20.83% > 20% → BLOCK_MAX_EXPOSURE.
        """
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        # Lose $400 (4% of $10k — below the 5% daily limit), bankroll → $9,600
        pos = _open_pos(ledger, "M-1", size=100.0)
        ledger.close_position(pos.position_id, pnl_usd=-400.0)

        # $1,800 existing exposure on $9,600 bankroll = 18.75% (under limit)
        _open_pos(ledger, "M-2", size=1_800.0)

        # Propose $200 → ($1,800 + $200) / $9,600 = 20.83% → BLOCK
        decision = rm.can_trade(proposed_size_usd=200.0, confidence=0.80)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_MAX_EXPOSURE)


class TestCanTradeDailyLoss(unittest.TestCase):

    def _rm_with_loss(self, loss_usd: float):
        """Helper: create a RM with a specific daily loss already booked."""
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)
        pos = _open_pos(ledger, "M-1", size=abs(loss_usd), at=now)
        ledger.close_position(pos.position_id, pnl_usd=-abs(loss_usd))
        return rm, ledger

    def test_blocks_when_daily_loss_limit_breached(self):
        # 5% of $10k = $500; lose $600 → breach
        rm, _ = self._rm_with_loss(600.0)
        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_DAILY_LOSS)

    def test_allows_just_below_daily_loss_limit(self):
        # Lose exactly $499 → still OK (limit is $500 = 5% of $10k)
        rm, _ = self._rm_with_loss(499.0)
        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertTrue(decision.allowed)

    def test_daily_loss_triggers_cooldown(self):
        rm, _ = self._rm_with_loss(600.0)
        rm.can_trade(100.0, confidence=0.80)  # triggers cooldown
        self.assertTrue(rm.in_cooldown)
        self.assertIsNotNone(rm.cooldown_until)

    def test_cooldown_duration_correct(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)
        pos = _open_pos(ledger, "M-1", size=600.0, at=now)
        ledger.close_position(pos.position_id, pnl_usd=-600.0)

        rm.can_trade(100.0)  # triggers cooldown
        expected = now + timedelta(minutes=60)
        self.assertAlmostEqual(
            rm.cooldown_until.timestamp(), expected.timestamp(), delta=1
        )


class TestCanTradeCooldown(unittest.TestCase):

    def test_blocks_during_cooldown(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        # Manually set cooldown (as if daily loss was hit)
        rm._cooldown_until = now + timedelta(hours=1)
        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_COOLDOWN)

    def test_cooldown_expires_and_trading_resumes(self):
        """After cooldown_until passes, the next can_trade call succeeds."""
        start = _utc()

        # Simulate time passing: use a mutable container so lambda can update
        current_time = [start]

        def now_fn():
            return current_time[0]

        ledger = Ledger(10_000.0, _now_fn=now_fn)
        rm = _make_rm(ledger=ledger, now_fn=now_fn)

        # Set cooldown for 60 minutes from now
        rm._cooldown_until = start + timedelta(minutes=60)

        # Still in cooldown
        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_COOLDOWN)

        # Advance time past cooldown
        current_time[0] = start + timedelta(minutes=61)
        decision = rm.can_trade(100.0, confidence=0.80)
        self.assertTrue(decision.allowed, f"Expected OK after cooldown, got: {decision.reason}")
        self.assertIsNone(rm._cooldown_until)  # cleared after expiry

    def test_cooldown_not_active_by_default(self):
        rm = _make_rm()
        self.assertFalse(rm.in_cooldown)
        self.assertIsNone(rm.cooldown_until)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — calculate_position_size()
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalculatePositionSize(unittest.TestCase):

    def test_scales_by_bankroll(self):
        # kelly_fractional=0.02 on $10k bankroll → $200
        rm = _make_rm()
        size = rm.calculate_position_size(kelly_fractional=0.02)
        self.assertAlmostEqual(size, 200.0)

    def test_zero_kelly_gives_zero_size(self):
        rm = _make_rm()
        size = rm.calculate_position_size(kelly_fractional=0.0)
        self.assertAlmostEqual(size, 0.0)

    def test_floored_at_zero_for_negative_kelly(self):
        rm = _make_rm()
        size = rm.calculate_position_size(kelly_fractional=-0.05)
        self.assertAlmostEqual(size, 0.0)

    def test_updates_with_bankroll_changes(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        # Lose $1000, bankroll → $9000
        pos = _open_pos(ledger, size=100.0)
        ledger.close_position(pos.position_id, pnl_usd=-1_000.0)

        size = rm.calculate_position_size(kelly_fractional=0.03)
        self.assertAlmostEqual(size, 270.0)  # 3% of $9000

    def test_large_kelly_not_further_capped_here(self):
        """Position sizing doesn't apply max_bet_pct — that's EdgeCalculator's job."""
        rm = _make_rm()
        size = rm.calculate_position_size(kelly_fractional=0.50)
        self.assertAlmostEqual(size, 5_000.0)  # 50% of $10k — no cap here


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — from_config + snapshot
# ═══════════════════════════════════════════════════════════════════════════════

class TestFromConfig(unittest.TestCase):

    def _load_config(self):
        import yaml
        with (ROOT / "config.yaml").open() as fh:
            return yaml.safe_load(fh)

    def test_from_config_wires_all_params(self):
        config = self._load_config()
        ledger = Ledger(10_000.0)
        rm = RiskManager.from_config(config, ledger)

        risk = config["risk"]
        self.assertEqual(rm.max_positions, risk["max_positions"])
        self.assertAlmostEqual(rm.max_exposure_pct, risk["max_exposure_pct"])
        self.assertAlmostEqual(rm.max_daily_loss_pct, risk["max_daily_loss_pct"])
        self.assertEqual(rm.cooldown_minutes, risk["cooldown_minutes"])
        self.assertAlmostEqual(rm.kelly_fraction, risk["kelly_fraction"])
        self.assertAlmostEqual(rm.max_bet_pct, risk["max_bet_pct"])
        self.assertAlmostEqual(rm.min_confidence, risk["min_confidence"])

    def test_snapshot_from_risk_manager(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        snap = rm.snapshot()
        self.assertIsInstance(snap, LedgerSnapshot)
        self.assertAlmostEqual(snap.bankroll, 10_000.0)
        self.assertFalse(snap.in_cooldown)
        self.assertIsNone(snap.cooldown_until)

    def test_snapshot_reflects_cooldown(self):
        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)
        rm._cooldown_until = now + timedelta(hours=1)

        snap = rm.snapshot()
        self.assertTrue(snap.in_cooldown)
        self.assertIsNotNone(snap.cooldown_until)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Integration: edge calculator → risk manager pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeToRiskPipeline(unittest.TestCase):
    """
    End-to-end: edge calc produces a kelly_fractional,
    risk manager sizes and gates the position.
    """

    def test_full_sizing_pipeline(self):
        from src.edge_calculator import EdgeCalculator

        # Edge: fair 62%, Kalshi 55% → strong YES edge
        calc = EdgeCalculator(fee_pct=0.07, kelly_fraction=0.25, max_bet_pct=0.03)
        result = calc.evaluate(fair_prob=0.62, kalshi_yes_price=0.55, side="YES")

        self.assertTrue(result.has_edge)

        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = _make_rm(ledger=ledger, now_fn=lambda: now)

        size_usd = rm.calculate_position_size(result.kelly_fractional)
        decision = rm.can_trade(size_usd, confidence=0.80)

        self.assertTrue(decision.allowed)
        self.assertGreater(size_usd, 0)
        self.assertLessEqual(size_usd, 10_000.0 * 0.03)  # 3% bankroll cap

    def test_risk_blocks_after_max_positions_filled(self):
        from src.edge_calculator import EdgeCalculator

        now = _utc()
        ledger = Ledger(10_000.0, _now_fn=lambda: now)
        rm = RiskManager(ledger, max_positions=3, max_exposure_pct=0.99,
                         max_daily_loss_pct=0.50, cooldown_minutes=60,
                         kelly_fraction=0.25, max_bet_pct=0.03,
                         min_confidence=0.0, _now_fn=lambda: now)

        for i in range(3):
            _open_pos(ledger, f"M-{i}", size=50.0)

        calc = EdgeCalculator()
        result = calc.evaluate(0.65, 0.50, "YES")
        size_usd = rm.calculate_position_size(result.kelly_fractional)
        decision = rm.can_trade(size_usd, confidence=0.80)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.block_code, TradeDecision.BLOCK_MAX_POSITIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
