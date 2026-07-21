"""
tests/test_promo_calculator.py
────────────────────────────────
Tests for src/promo_calculator.py — shared promo math.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.promo_calculator import (
    american_to_decimal,
    decimal_to_american,
    decimal_to_implied_prob,
    free_bet_hedge_size,
    free_bet_guaranteed_profit,
    free_bet_conversion_rate,
    risk_free_ev,
    odds_boost_ev,
    odds_boost_ev_pct,
    odds_boost_hedge_size,
    odds_boost_locked_profit,
    PromoRecord,
    promo_from_dict,
)


# ── TestOddsConversion ────────────────────────────────────────────────

class TestOddsConversion(unittest.TestCase):

    def test_american_positive_to_decimal(self):
        # +200 → 3.0
        self.assertAlmostEqual(american_to_decimal(200), 3.0)

    def test_american_plus_150(self):
        self.assertAlmostEqual(american_to_decimal(150), 2.5)

    def test_american_negative_to_decimal(self):
        # -110 → 1.909...
        self.assertAlmostEqual(american_to_decimal(-110), 100 / 110 + 1, places=4)

    def test_american_even_money(self):
        self.assertAlmostEqual(american_to_decimal(100), 2.0)

    def test_decimal_to_american_positive(self):
        # 3.0 → +200
        self.assertEqual(decimal_to_american(3.0), 200)

    def test_decimal_to_american_negative(self):
        # 1.5 → -200
        self.assertEqual(decimal_to_american(1.5), -200)

    def test_decimal_to_american_even(self):
        self.assertEqual(decimal_to_american(2.0), 100)

    def test_implied_prob(self):
        # 2.0 decimal → 50%
        self.assertAlmostEqual(decimal_to_implied_prob(2.0), 0.50)

    def test_implied_prob_favourite(self):
        # 1.5 decimal → 66.7%
        self.assertAlmostEqual(decimal_to_implied_prob(1.5), 2 / 3, places=4)


# ── TestFreeBetHedge ──────────────────────────────────────────────────

class TestFreeBetHedge(unittest.TestCase):

    def test_hedge_size_formula(self):
        # F=$100, d=3.0 (+200), p_no=0.60
        # H = 100 * (3-1) * 0.60 = 120
        h = free_bet_hedge_size(100.0, 3.0, 0.60)
        self.assertAlmostEqual(h, 120.0, places=2)

    def test_hedge_size_even_money(self):
        # F=$100, d=2.0 (+100), p_no=0.50
        # H = 100 * (2-1) * 0.50 = 50
        h = free_bet_hedge_size(100.0, 2.0, 0.50)
        self.assertAlmostEqual(h, 50.0, places=2)

    def test_guaranteed_profit_positive(self):
        # F=$100, d=3.0, p_no=0.60
        # H=120; profit = 120 * (1/0.60 - 1) = 120 * 0.667 = 80.0
        p = free_bet_guaranteed_profit(100.0, 3.0, 0.60)
        self.assertGreater(p, 0)
        self.assertAlmostEqual(p, 80.0, places=1)

    def test_outcome_equalised(self):
        """YES win and NO win should yield equal cash after hedging."""
        F, d, p_no = 100.0, 3.0, 0.60
        H = free_bet_hedge_size(F, d, p_no)
        yes_win_payout = F * (d - 1) - H
        no_win_payout = H * (1 / p_no - 1)
        self.assertAlmostEqual(yes_win_payout, no_win_payout, places=2)

    def test_conversion_rate_typical(self):
        # At +200 and p_no=0.60: rate = (3-1)*(1-0.60) = 0.80
        rate = free_bet_conversion_rate(3.0, 0.60)
        self.assertAlmostEqual(rate, 0.80, places=4)

    def test_conversion_rate_even_money(self):
        # At +100 and p_no=0.50: rate = (2-1)*(1-0.50) = 0.50
        rate = free_bet_conversion_rate(2.0, 0.50)
        self.assertAlmostEqual(rate, 0.50, places=4)

    def test_conversion_rate_zero_on_bad_inputs(self):
        # p_no = 0 or 1 → degenerate, return 0
        self.assertEqual(free_bet_conversion_rate(3.0, 0.0), 0.0)
        self.assertEqual(free_bet_conversion_rate(3.0, 1.0), 0.0)


# ── TestRiskFreeBet ───────────────────────────────────────────────────

class TestRiskFreeBet(unittest.TestCase):

    def test_positive_ev_at_fair_odds(self):
        # $100 RFB at +200 (d=3.0), fair_prob=0.40, conv_pct=0.65
        # EV = 100 * [0.40*(3-1) + 0.60*0.65 - 1]
        #     = 100 * [0.80 + 0.39 - 1] = 100 * 0.19 = 19.0
        ev = risk_free_ev(100.0, 3.0, 0.40, 0.65)
        self.assertAlmostEqual(ev, 19.0, places=1)

    def test_ev_with_unfavourable_odds(self):
        # At severe overdog: lower EV but still positive with free bet component
        ev = risk_free_ev(100.0, 1.5, 0.70, 0.65)
        # EV = 100 * [0.70*0.50 + 0.30*0.65 - 1] = 100 * [0.35 + 0.195 - 1] = 100 * (-0.455) < 0
        self.assertLess(ev, 0)

    def test_higher_conversion_better_ev(self):
        ev_low = risk_free_ev(100.0, 2.5, 0.45, 0.60)
        ev_high = risk_free_ev(100.0, 2.5, 0.45, 0.75)
        self.assertGreater(ev_high, ev_low)


# ── TestOddsBoostEV ───────────────────────────────────────────────────

class TestOddsBoostEV(unittest.TestCase):

    def test_positive_ev_on_boosted_odds(self):
        # Stake=$25, d_boost=4.5 (+350), fair_prob=0.38
        # EV = 25*(4.5*0.38 - 1) = 25*(1.71 - 1) = 25*0.71 = 17.75
        ev = odds_boost_ev(25.0, 4.5, 0.38)
        self.assertAlmostEqual(ev, 17.75, places=1)

    def test_negative_ev_on_bad_boost(self):
        # Stake=$25, d_boost=2.0 (+100), fair_prob=0.40 (terrible odds)
        # EV = 25*(2.0*0.40 - 1) = 25*(0.80 - 1) = 25*(-0.20) = -5
        ev = odds_boost_ev(25.0, 2.0, 0.40)
        self.assertAlmostEqual(ev, -5.0, places=1)

    def test_ev_pct_positive(self):
        pct = odds_boost_ev_pct(4.5, 0.38)
        self.assertGreater(pct, 0)
        self.assertAlmostEqual(pct, 4.5 * 0.38 - 1, places=4)

    def test_breakeven_probability(self):
        # At breakeven: d * p = 1 → p = 1/d
        d = 3.0
        fair_prob = 1.0 / d
        ev_pct = odds_boost_ev_pct(d, fair_prob)
        self.assertAlmostEqual(ev_pct, 0.0, places=6)


# ── TestOddsBoostHedge ────────────────────────────────────────────────

class TestOddsBoostHedge(unittest.TestCase):

    def test_hedge_size(self):
        # Stake=$25, d_boost=4.5, p_no=0.64
        # H = 25 * 4.5 * 0.64 = 72
        h = odds_boost_hedge_size(25.0, 4.5, 0.64)
        self.assertAlmostEqual(h, 72.0, places=1)

    def test_locked_profit_positive_when_viable(self):
        # locked = 25*(4.5*(1-0.64) - 1) = 25*(4.5*0.36 - 1) = 25*(1.62 - 1) = 25*0.62 = 15.5
        locked = odds_boost_locked_profit(25.0, 4.5, 0.64)
        self.assertGreater(locked, 0)
        self.assertAlmostEqual(locked, 15.5, places=1)

    def test_locked_negative_when_not_viable(self):
        # If odds boost is small and p_no high, can't lock profit
        locked = odds_boost_locked_profit(25.0, 1.5, 0.70)
        # 25*(1.5*0.30 - 1) = 25*(0.45 - 1) = -13.75
        self.assertLess(locked, 0)

    def test_outcomes_equalised(self):
        """YES win and NO win should yield equal cash after hedge."""
        stake, d_boost, p_no = 25.0, 4.5, 0.64
        H = odds_boost_hedge_size(stake, d_boost, p_no)
        yes_win = stake * (d_boost - 1) - H  # boosted profit - hedge stake
        no_win = H * (1 / p_no - 1) - stake   # hedge profit - original stake
        self.assertAlmostEqual(yes_win, no_win, places=1)


# ── TestPromoRecord ───────────────────────────────────────────────────

class TestPromoRecord(unittest.TestCase):

    def test_is_active_pending(self):
        p = PromoRecord(promo_id="x", sportsbook="fd", promo_type="free_bet",
                        amount=100, status="pending")
        self.assertTrue(p.is_active())

    def test_is_active_used(self):
        p = PromoRecord(promo_id="x", sportsbook="fd", promo_type="free_bet",
                        amount=100, status="used")
        self.assertFalse(p.is_active())

    def test_effective_stake_capped_by_max_stake(self):
        p = PromoRecord(promo_id="x", sportsbook="fd", promo_type="odds_boost",
                        amount=50, max_stake=25)
        self.assertEqual(p.effective_stake(), 25)

    def test_effective_stake_not_capped_when_below_max(self):
        p = PromoRecord(promo_id="x", sportsbook="fd", promo_type="odds_boost",
                        amount=20, max_stake=25)
        self.assertEqual(p.effective_stake(), 20)

    def test_effective_stake_free_bet_ignores_max_stake(self):
        p = PromoRecord(promo_id="x", sportsbook="fd", promo_type="free_bet",
                        amount=100, max_stake=25)
        self.assertEqual(p.effective_stake(), 100)

    def test_to_dict(self):
        p = PromoRecord(promo_id="x", sportsbook="fd", promo_type="free_bet",
                        amount=100)
        d = p.to_dict()
        self.assertEqual(d["promo_id"], "x")
        self.assertEqual(d["amount"], 100)


if __name__ == "__main__":
    unittest.main()
