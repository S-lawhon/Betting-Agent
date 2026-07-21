"""Tests for src/pnl_calculator.py."""
import unittest
from src.pnl_calculator import PnLCalculator, VenueFees, VENUE_FEES, PnLResult


class TestVenueFees(unittest.TestCase):

    def test_kalshi_defaults(self):
        f = VENUE_FEES["kalshi"]
        self.assertEqual(f.profit_fee_pct, 0.07)
        self.assertEqual(f.commission_pct, 0.0)

    def test_polymarket_zero_fees(self):
        f = VENUE_FEES["polymarket"]
        self.assertEqual(f.profit_fee_pct, 0.0)

    def test_forecastex_apy(self):
        f = VENUE_FEES["forecastex"]
        self.assertAlmostEqual(f.collateral_apy, 0.0314)


class TestPnLCalculator(unittest.TestCase):

    def setUp(self):
        self.calc = PnLCalculator()

    # ── Kalshi (7% profit fee) ────────────────────────────────────

    def test_kalshi_yes_win(self):
        """Buy YES at 0.40, settles at 1.00 → gross = 0.60, fee = 0.042."""
        r = self.calc.compute("kalshi", "YES", 0.40, 1.00, contracts=1)
        self.assertAlmostEqual(r.gross_pnl, 0.60)
        self.assertAlmostEqual(r.fees, 0.042)
        self.assertAlmostEqual(r.net_pnl, 0.558)

    def test_kalshi_yes_loss(self):
        """Buy YES at 0.40, settles at 0.00 → gross = -0.40, fee = 0 (no profit)."""
        r = self.calc.compute("kalshi", "YES", 0.40, 0.00, contracts=1)
        self.assertAlmostEqual(r.gross_pnl, -0.40)
        self.assertAlmostEqual(r.fees, 0.0)
        self.assertAlmostEqual(r.net_pnl, -0.40)

    def test_kalshi_no_win(self):
        """Buy NO at 0.30, event resolves NO → exit 0.00 on YES side.
        NO profit = entry - exit = 0.30. Fee = 0.30 * 0.07 = 0.021."""
        r = self.calc.compute("kalshi", "NO", 0.30, 0.00, contracts=1)
        self.assertAlmostEqual(r.gross_pnl, 0.30)
        self.assertAlmostEqual(r.fees, 0.021)
        self.assertAlmostEqual(r.net_pnl, 0.279)

    def test_kalshi_multiple_contracts(self):
        """5 contracts, YES at 0.40 → 1.00."""
        r = self.calc.compute("kalshi", "YES", 0.40, 1.00, contracts=5)
        self.assertAlmostEqual(r.gross_pnl, 3.00)
        self.assertAlmostEqual(r.fees, 0.21)
        self.assertAlmostEqual(r.net_pnl, 2.79)

    # ── Polymarket (0% fees) ──────────────────────────────────────

    def test_polymarket_yes_win(self):
        r = self.calc.compute("polymarket", "YES", 0.35, 1.00, contracts=10)
        self.assertAlmostEqual(r.gross_pnl, 6.50)
        self.assertAlmostEqual(r.fees, 0.0)
        self.assertAlmostEqual(r.net_pnl, 6.50)

    def test_polymarket_yes_loss(self):
        r = self.calc.compute("polymarket", "YES", 0.35, 0.00, contracts=10)
        self.assertAlmostEqual(r.gross_pnl, -3.50)
        self.assertAlmostEqual(r.net_pnl, -3.50)

    # ── ForecastEx (0% commission + APY coupon) ───────────────────

    def test_forecastex_with_coupon(self):
        """Hold for 30 days, collateral = 0.50 * 10 = 5.00.
        APY credit = 5.00 * 0.0314 * (30/365) ≈ 0.0129."""
        r = self.calc.compute(
            "forecastex", "YES", 0.50, 1.00, contracts=10, holding_days=30,
        )
        self.assertAlmostEqual(r.gross_pnl, 5.00)
        self.assertAlmostEqual(r.fees, 0.0)
        self.assertGreater(r.collateral_credit, 0)
        self.assertAlmostEqual(r.collateral_credit, 0.0129, places=3)
        self.assertAlmostEqual(r.total_pnl, r.net_pnl + r.collateral_credit)

    def test_forecastex_no_holding(self):
        """Zero holding days → no coupon."""
        r = self.calc.compute("forecastex", "YES", 0.50, 1.00, contracts=1)
        self.assertAlmostEqual(r.collateral_credit, 0.0)

    # ── Unknown venue (zero fees) ─────────────────────────────────

    def test_unknown_venue_zero_fees(self):
        r = self.calc.compute("some_new_venue", "YES", 0.50, 1.00, contracts=1)
        self.assertAlmostEqual(r.gross_pnl, 0.50)
        self.assertAlmostEqual(r.fees, 0.0)

    # ── Custom fee override ───────────────────────────────────────

    def test_custom_fees(self):
        custom = PnLCalculator(venue_fees={
            "kalshi": VenueFees(name="kalshi", profit_fee_pct=0.10),
        })
        r = custom.compute("kalshi", "YES", 0.40, 1.00, contracts=1)
        self.assertAlmostEqual(r.fees, 0.06)  # 10% of 0.60 profit

    # ── Batch ─────────────────────────────────────────────────────

    def test_batch(self):
        trades = [
            {"venue": "kalshi", "side": "YES", "entry_price": 0.40, "exit_price": 1.00},
            {"venue": "polymarket", "side": "NO", "entry_price": 0.30, "exit_price": 0.00},
        ]
        results = self.calc.compute_batch(trades)
        self.assertEqual(len(results), 2)
        self.assertAlmostEqual(results[0].gross_pnl, 0.60)
        self.assertAlmostEqual(results[1].gross_pnl, 0.30)

    def test_batch_skips_invalid(self):
        trades = [
            {"venue": "kalshi"},  # Missing required fields
            {"venue": "kalshi", "side": "YES", "entry_price": 0.40, "exit_price": 1.00},
        ]
        results = self.calc.compute_batch(trades)
        self.assertEqual(len(results), 1)

    # ── Edge cases ────────────────────────────────────────────────

    def test_zero_contracts(self):
        r = self.calc.compute("kalshi", "YES", 0.40, 1.00, contracts=0)
        self.assertAlmostEqual(r.gross_pnl, 0.0)

    def test_break_even(self):
        r = self.calc.compute("kalshi", "YES", 0.50, 0.50, contracts=1)
        self.assertAlmostEqual(r.gross_pnl, 0.0)
        self.assertAlmostEqual(r.fees, 0.0)


if __name__ == "__main__":
    unittest.main()
