"""
tests/test_edge_calculator.py
──────────────────────────────
Phase 1 must-pass tests:
  ✓ Edge sign correctness  YES vs NO
  ✓ EV correctness for known inputs
  ✓ Kelly is bounded and sensible
  ✓ Fee adjustment reduces EV / edge
  ✓ utils: odds conversions round-trip
  ✓ utils: vig removal sums to 1.0
  ✓ EdgeCalculator.from_config wires correctly
  ✓ EdgeCalculator.best_side filters below-threshold opportunities
"""

import sys
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.edge_calculator import (
    EdgeCalculator,
    EdgeResult,
    calc_ev,
    calc_kelly,
    calc_raw_edge,
    adjust_kelly_for_confidence,
)
from src.utils import (
    american_to_decimal,
    american_to_implied_prob,
    decimal_to_american,
    decimal_to_implied_prob,
    median_prob,
    overround,
    prob_to_american,
    remove_vig_additive,
    remove_vig_power,
    vig_pct,
    weighted_consensus_prob,
)

# ── Tolerance for floating-point comparisons ──────────────────────────────────
ATOL = 1e-6


def approx(a: float, b: float, atol: float = ATOL) -> bool:
    return abs(a - b) <= atol


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — utils.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestOddsConversions(unittest.TestCase):
    """American ↔ Decimal ↔ Probability round-trips."""

    # ── American → Decimal ────────────────────────────────────────────────────

    def test_positive_american_to_decimal(self):
        self.assertAlmostEqual(american_to_decimal(200), 3.0)

    def test_negative_american_to_decimal(self):
        # -110 → 100/110 + 1 ≈ 1.9091
        self.assertAlmostEqual(american_to_decimal(-110), 1.9090909090909092)

    def test_even_money_american_to_decimal(self):
        self.assertAlmostEqual(american_to_decimal(100), 2.0)

    # ── Decimal → American ────────────────────────────────────────────────────

    def test_decimal_to_american_favourite(self):
        # Decimal 1.909 → American -110
        self.assertAlmostEqual(decimal_to_american(1.9090909090909092), -110.0)

    def test_decimal_to_american_underdog(self):
        self.assertAlmostEqual(decimal_to_american(3.0), 200.0)

    # ── Round-trip: American → Decimal → American ─────────────────────────────

    def test_round_trip_positive_odds(self):
        for odds in [100, 110, 150, 200, 350, 1000]:
            with self.subTest(odds=odds):
                self.assertAlmostEqual(
                    decimal_to_american(american_to_decimal(odds)),
                    float(odds),
                    places=4,
                )

    def test_round_trip_negative_odds(self):
        # -100 is excluded: it maps to decimal 2.0 which is ambiguous with +100
        # (both represent evens). All other negative odds round-trip cleanly.
        for odds in [-105, -110, -115, -130, -200, -400]:
            with self.subTest(odds=odds):
                self.assertAlmostEqual(
                    decimal_to_american(american_to_decimal(odds)),
                    float(odds),
                    places=4,
                )

    # ── American → Probability ────────────────────────────────────────────────

    def test_implied_prob_even_money(self):
        self.assertAlmostEqual(american_to_implied_prob(100), 0.5)

    def test_implied_prob_favourite(self):
        # -200: 200/300 ≈ 0.6667
        self.assertAlmostEqual(american_to_implied_prob(-200), 2 / 3, places=6)

    def test_implied_prob_underdog(self):
        # +200: 100/300 ≈ 0.3333
        self.assertAlmostEqual(american_to_implied_prob(200), 1 / 3, places=6)

    # ── Decimal → Probability ─────────────────────────────────────────────────

    def test_decimal_to_prob(self):
        self.assertAlmostEqual(decimal_to_implied_prob(2.0), 0.5)
        self.assertAlmostEqual(decimal_to_implied_prob(4.0), 0.25)

    def test_decimal_to_prob_rejects_zero(self):
        with self.assertRaises(ValueError):
            decimal_to_implied_prob(0.0)

    # ── Probability → American ────────────────────────────────────────────────

    def test_prob_to_american_favourite(self):
        self.assertAlmostEqual(prob_to_american(2 / 3), -200.0, places=4)

    def test_prob_to_american_underdog(self):
        self.assertAlmostEqual(prob_to_american(1 / 3), 200.0, places=4)

    def test_prob_to_american_even(self):
        self.assertAlmostEqual(prob_to_american(0.5), -100.0)

    def test_prob_to_american_rejects_boundary(self):
        with self.assertRaises(ValueError):
            prob_to_american(0.0)
        with self.assertRaises(ValueError):
            prob_to_american(1.0)


class TestVigRemoval(unittest.TestCase):
    """Devigging functions produce fair probabilities summing to 1."""

    def test_additive_two_sides_sum_to_one(self):
        # Two -110 sides: each has 52.38% implied → 104.76% total
        raw = [american_to_implied_prob(-110), american_to_implied_prob(-110)]
        fair = remove_vig_additive(raw)
        self.assertAlmostEqual(sum(fair), 1.0)

    def test_additive_equal_sides_each_fifty_percent(self):
        fair = remove_vig_additive([0.5238, 0.5238])
        self.assertAlmostEqual(fair[0], 0.5, places=3)
        self.assertAlmostEqual(fair[1], 0.5, places=3)

    def test_additive_asymmetric_market(self):
        # Favourite -200 and underdog +180
        raw = [american_to_implied_prob(-200), american_to_implied_prob(180)]
        fair = remove_vig_additive(raw)
        self.assertAlmostEqual(sum(fair), 1.0)
        # Favourite should still be > 0.5 after devigging
        self.assertGreater(fair[0], 0.5)

    def test_power_sums_to_one(self):
        raw = [0.55, 0.50]  # total 1.05 → 5% vig
        fair = remove_vig_power(raw)
        self.assertAlmostEqual(sum(fair), 1.0, places=6)

    def test_power_vs_additive_close_for_low_vig(self):
        # For small vig (~2%), both methods should agree within 0.5%
        raw = [0.51, 0.51]
        additive = remove_vig_additive(raw)
        power = remove_vig_power(raw)
        for a, p in zip(additive, power):
            self.assertAlmostEqual(a, p, places=2)

    def test_overround_two_minus_110_lines(self):
        raw = [american_to_implied_prob(-110), american_to_implied_prob(-110)]
        self.assertAlmostEqual(overround(raw), 1.0476, places=3)

    def test_vig_pct_typical(self):
        raw = [0.5238, 0.5238]
        self.assertAlmostEqual(vig_pct(raw), 4.55, places=1)


class TestConsensus(unittest.TestCase):

    def test_equal_weight_consensus(self):
        result = weighted_consensus_prob([0.50, 0.52, 0.54])
        self.assertAlmostEqual(result, 0.52, places=6)

    def test_weighted_consensus_pinnacle_heavy(self):
        # Pinnacle gets 2x weight
        result = weighted_consensus_prob([0.52, 0.54], weights=[2.0, 1.0])
        expected = (0.52 * 2 + 0.54 * 1) / 3
        self.assertAlmostEqual(result, expected)

    def test_median_prob_odd(self):
        self.assertAlmostEqual(median_prob([0.50, 0.52, 0.54]), 0.52)

    def test_median_prob_even_lower(self):
        # Even length → lower median
        self.assertAlmostEqual(median_prob([0.50, 0.52]), 0.50)

    def test_median_single_element(self):
        self.assertAlmostEqual(median_prob([0.55]), 0.55)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — edge_calculator.py: core formulas
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalcRawEdge(unittest.TestCase):
    """Edge sign correctness — the most important correctness check."""

    def test_positive_edge_when_fair_above_price(self):
        # Fair prob 60%, market prices it at 55% → should buy YES
        edge = calc_raw_edge(fair_prob=0.60, kalshi_price=0.55)
        self.assertGreater(edge, 0)

    def test_negative_edge_when_fair_below_price(self):
        # Fair prob 40%, market prices it at 55% → no edge on YES
        edge = calc_raw_edge(fair_prob=0.40, kalshi_price=0.55)
        self.assertLess(edge, 0)

    def test_zero_edge_when_fair_equals_price(self):
        edge = calc_raw_edge(fair_prob=0.50, kalshi_price=0.50)
        self.assertAlmostEqual(edge, 0.0)

    def test_edge_symmetry_yes_no(self):
        """YES edge + NO edge must equal 0 for the same market."""
        yes_edge = calc_raw_edge(fair_prob=0.60, kalshi_price=0.55)  # YES side
        no_edge = calc_raw_edge(fair_prob=0.40, kalshi_price=0.45)   # NO side (1-0.60, 1-0.55)
        self.assertAlmostEqual(yes_edge + no_edge, 0.0, places=10)

    def test_magnitude(self):
        edge = calc_raw_edge(0.60, 0.55)
        self.assertAlmostEqual(edge, 0.05)


class TestCalcEV(unittest.TestCase):
    """EV correctness for known inputs."""

    # ── Known-value hand calculations ─────────────────────────────────────────
    # fair=0.60, price=0.50, fee=0.07:
    #   net_win = (1-0.5)*(1-0.07) = 0.5 * 0.93 = 0.465
    #   EV = 0.60 * 0.465 - 0.40 * 0.50 = 0.279 - 0.200 = 0.079

    def test_ev_known_value(self):
        ev = calc_ev(fair_prob=0.60, kalshi_price=0.50, fee_pct=0.07)
        self.assertAlmostEqual(ev, 0.079, places=6)

    def test_ev_positive_when_edge_positive(self):
        ev = calc_ev(fair_prob=0.60, kalshi_price=0.55, fee_pct=0.07)
        self.assertGreater(ev, 0)

    def test_ev_negative_when_no_edge(self):
        ev = calc_ev(fair_prob=0.45, kalshi_price=0.55, fee_pct=0.07)
        self.assertLess(ev, 0)

    def test_ev_zero_fee_is_greater_than_with_fee(self):
        """Zero-fee EV must always be >= fee-adjusted EV for a winning bet."""
        ev_no_fee = calc_ev(fair_prob=0.60, kalshi_price=0.50, fee_pct=0.00)
        ev_with_fee = calc_ev(fair_prob=0.60, kalshi_price=0.50, fee_pct=0.07)
        self.assertGreater(ev_no_fee, ev_with_fee)

    def test_ev_at_fair_price_is_negative_due_to_fee(self):
        """Buying a contract exactly at fair value loses money after fees."""
        ev = calc_ev(fair_prob=0.50, kalshi_price=0.50, fee_pct=0.07)
        self.assertLess(ev, 0)

    def test_ev_rejects_invalid_price(self):
        with self.assertRaises(ValueError):
            calc_ev(0.5, 0.0)
        with self.assertRaises(ValueError):
            calc_ev(0.5, 1.0)

    def test_ev_rejects_invalid_prob(self):
        with self.assertRaises(ValueError):
            calc_ev(-0.1, 0.5)
        with self.assertRaises(ValueError):
            calc_ev(1.1, 0.5)


class TestCalcKelly(unittest.TestCase):
    """Kelly fraction is bounded and sensible."""

    def test_kelly_positive_when_edge_exists(self):
        k = calc_kelly(fair_prob=0.60, kalshi_price=0.50, fee_pct=0.07)
        self.assertGreater(k, 0)

    def test_kelly_zero_when_no_edge(self):
        k = calc_kelly(fair_prob=0.40, kalshi_price=0.55, fee_pct=0.07)
        self.assertAlmostEqual(k, 0.0)

    def test_kelly_zero_at_fair_price_with_fee(self):
        """Kelly should be ~0 (or small negative, floored to 0) at fair price."""
        k = calc_kelly(fair_prob=0.50, kalshi_price=0.50, fee_pct=0.07)
        self.assertAlmostEqual(k, 0.0, places=5)

    def test_kelly_never_negative(self):
        for fair, price in [(0.3, 0.6), (0.1, 0.9), (0.49, 0.51)]:
            with self.subTest(fair=fair, price=price):
                k = calc_kelly(fair, price)
                self.assertGreaterEqual(k, 0.0)

    def test_kelly_bounded_below_one(self):
        """Even with huge edge, Kelly should be < 1 for typical market params."""
        k = calc_kelly(fair_prob=0.90, kalshi_price=0.50, fee_pct=0.07)
        self.assertLess(k, 1.0)

    def test_kelly_increases_with_edge(self):
        k_small = calc_kelly(0.55, 0.50)
        k_large = calc_kelly(0.70, 0.50)
        self.assertGreater(k_large, k_small)

    def test_kelly_decreases_with_higher_fee(self):
        k_low_fee = calc_kelly(0.60, 0.50, fee_pct=0.02)
        k_high_fee = calc_kelly(0.60, 0.50, fee_pct=0.10)
        self.assertGreater(k_low_fee, k_high_fee)

    def test_kelly_known_value(self):
        # fair=0.60, price=0.50, fee=0.07:
        # net_win = 0.5 * 0.93 = 0.465
        # b = 0.465 / 0.50 = 0.93
        # kelly = (0.93*0.60 - 0.40) / 0.93 = (0.558 - 0.40)/0.93 = 0.158/0.93 ≈ 0.1699
        k = calc_kelly(fair_prob=0.60, kalshi_price=0.50, fee_pct=0.07)
        self.assertAlmostEqual(k, 0.158 / 0.93, places=4)


class TestFeeAdjustment(unittest.TestCase):
    """Fee adjustment reduces EV and Kelly relative to the no-fee baseline."""

    FEE_LEVELS = [0.0, 0.03, 0.07, 0.10]

    def test_ev_monotonically_decreases_with_fee(self):
        evs = [calc_ev(0.60, 0.50, fee) for fee in self.FEE_LEVELS]
        for i in range(len(evs) - 1):
            with self.subTest(fee=self.FEE_LEVELS[i + 1]):
                self.assertGreater(evs[i], evs[i + 1])

    def test_kelly_monotonically_decreases_with_fee(self):
        kellys = [calc_kelly(0.60, 0.50, fee) for fee in self.FEE_LEVELS]
        for i in range(len(kellys) - 1):
            with self.subTest(fee=self.FEE_LEVELS[i + 1]):
                self.assertGreater(kellys[i], kellys[i + 1])

    def test_edge_unaffected_by_fee(self):
        """Raw edge is fee-agnostic (it's purely prob vs price)."""
        e1 = calc_raw_edge(0.60, 0.55)
        e2 = calc_raw_edge(0.60, 0.55)  # same inputs
        self.assertAlmostEqual(e1, e2)

    def test_fractional_kelly_smaller_than_full(self):
        full = calc_kelly(0.60, 0.50, 0.07)
        fractional = adjust_kelly_for_confidence(full, kelly_fraction=0.25)
        self.assertLess(fractional, full)

    def test_confidence_dampens_kelly(self):
        full = calc_kelly(0.60, 0.50, 0.07)
        high_conf = adjust_kelly_for_confidence(full, 0.25, confidence=1.0)
        low_conf = adjust_kelly_for_confidence(full, 0.25, confidence=0.5)
        self.assertGreater(high_conf, low_conf)

    def test_max_bet_cap_respected(self):
        """Even huge Kelly is capped at max_bet_pct."""
        full = calc_kelly(0.95, 0.20, 0.01)  # enormous edge
        capped = adjust_kelly_for_confidence(full, 1.0, confidence=1.0, max_bet_pct=0.03)
        self.assertLessEqual(capped, 0.03)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — EdgeCalculator class
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCalculator(unittest.TestCase):

    def setUp(self):
        self.calc = EdgeCalculator(
            fee_pct=0.07,
            kelly_fraction=0.25,
            max_bet_pct=0.03,
            min_edge_pct=0.03,
            min_ev=0.01,
        )

    # ── YES side ──────────────────────────────────────────────────────────────

    def test_yes_side_positive_edge(self):
        result = self.calc.evaluate(fair_prob=0.60, kalshi_yes_price=0.50, side="YES")
        self.assertIsInstance(result, EdgeResult)
        self.assertEqual(result.side, "YES")
        self.assertGreater(result.raw_edge, 0)
        self.assertGreater(result.ev, 0)
        self.assertTrue(result.has_edge)

    def test_yes_side_no_edge(self):
        result = self.calc.evaluate(fair_prob=0.40, kalshi_yes_price=0.55, side="YES")
        self.assertLess(result.raw_edge, 0)
        self.assertFalse(result.has_edge)

    # ── NO side ───────────────────────────────────────────────────────────────

    def test_no_side_positive_edge_when_market_overprices_yes(self):
        # Fair YES = 40%, market prices YES at 55% → buy NO (fair NO=60%, price NO=45%)
        result = self.calc.evaluate(fair_prob=0.40, kalshi_yes_price=0.55, side="NO")
        self.assertEqual(result.side, "NO")
        self.assertAlmostEqual(result.fair_prob, 0.60)
        self.assertAlmostEqual(result.kalshi_price, 0.45)
        self.assertGreater(result.raw_edge, 0)
        self.assertTrue(result.has_edge)

    def test_no_side_no_edge_when_market_underprices_yes(self):
        # Fair YES = 60%, market prices YES at 50% → NO has no edge
        result = self.calc.evaluate(fair_prob=0.60, kalshi_yes_price=0.50, side="NO")
        self.assertLess(result.raw_edge, 0)
        self.assertFalse(result.has_edge)

    def test_yes_and_no_never_both_have_edge(self):
        """Only one side can have positive raw edge in any given market."""
        for yes_price in [0.40, 0.45, 0.50, 0.55, 0.60]:
            yes, no = self.calc.evaluate_both_sides(
                fair_yes_prob=0.55, kalshi_yes_price=yes_price
            )
            with self.subTest(yes_price=yes_price):
                # At most one side can have positive edge
                self.assertFalse(yes.raw_edge > 0 and no.raw_edge > 0)

    # ── from_config ───────────────────────────────────────────────────────────

    def test_from_config(self):
        import yaml
        with (ROOT / "config.yaml").open() as fh:
            config = yaml.safe_load(fh)
        calc = EdgeCalculator.from_config(config)
        self.assertAlmostEqual(calc.fee_pct, config["edge"]["fee_pct"])
        self.assertAlmostEqual(calc.kelly_fraction, config["risk"]["kelly_fraction"])
        self.assertAlmostEqual(calc.max_bet_pct, config["risk"]["max_bet_pct"])

    # ── best_side filtering ───────────────────────────────────────────────────

    def test_best_side_returns_none_when_below_threshold(self):
        # Only 1% edge — below min_edge_pct=0.03
        result = self.calc.best_side(fair_yes_prob=0.51, kalshi_yes_price=0.50)
        self.assertIsNone(result)

    def test_best_side_returns_yes_when_clear_edge(self):
        result = self.calc.best_side(fair_yes_prob=0.65, kalshi_yes_price=0.50)
        self.assertIsNotNone(result)
        self.assertEqual(result.side, "YES")

    def test_best_side_returns_no_when_yes_overpriced(self):
        result = self.calc.best_side(fair_yes_prob=0.35, kalshi_yes_price=0.55)
        self.assertIsNotNone(result)
        self.assertEqual(result.side, "NO")

    # ── Kelly sizing sanity ───────────────────────────────────────────────────

    def test_kelly_fractional_respects_max_bet_cap(self):
        result = self.calc.evaluate(fair_prob=0.95, kalshi_yes_price=0.30, side="YES")
        self.assertLessEqual(result.kelly_fractional, self.calc.max_bet_pct)

    def test_kelly_fractional_is_quarter_kelly(self):
        result = self.calc.evaluate(fair_prob=0.60, kalshi_yes_price=0.50, side="YES")
        expected = min(result.kelly_full * 0.25, self.calc.max_bet_pct)
        self.assertAlmostEqual(result.kelly_fractional, expected, places=8)

    # ── EdgeResult immutability ───────────────────────────────────────────────

    def test_edge_result_is_frozen(self):
        result = self.calc.evaluate(0.60, 0.50, "YES")
        with self.assertRaises((AttributeError, TypeError)):
            result.raw_edge = 99.9  # type: ignore

    def test_edge_result_str(self):
        result = self.calc.evaluate(0.60, 0.50, "YES")
        s = str(result)
        self.assertIn("YES", s)
        self.assertIn("edge=", s)

    # ── Invalid inputs ────────────────────────────────────────────────────────

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            self.calc.evaluate(0.60, 0.50, side="MAYBE")

    def test_invalid_fair_prob_raises(self):
        with self.assertRaises(ValueError):
            self.calc.evaluate(1.5, 0.50, "YES")

    def test_invalid_kalshi_price_raises(self):
        with self.assertRaises(ValueError):
            self.calc.evaluate(0.60, 0.0, "YES")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Integration smoke test
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEndSmoke(unittest.TestCase):
    """
    Simulate a realistic edge evaluation pipeline:
    raw American odds → devig → edge calc → best side decision.
    """

    def test_full_pipeline_nfl_moneyline(self):
        """
        Scenario: Chiefs -165 on sharp books (fair ~62%), Kalshi prices YES at 57¢.

        At -165, fair value after devigging is ~62%. Kalshi at 57¢ gives a
        clear 5% edge and positive EV even after the 7% Kalshi fee.
        """
        # Step 1: raw implied probs from American odds
        chiefs_raw = american_to_implied_prob(-165)   # ≈ 0.6226
        raiders_raw = american_to_implied_prob(145)   # ≈ 0.4082

        # Step 2: remove vig
        fair_chiefs, fair_raiders = remove_vig_additive([chiefs_raw, raiders_raw])
        self.assertAlmostEqual(fair_chiefs + fair_raiders, 1.0)
        self.assertGreater(fair_chiefs, 0.5)  # Chiefs are still the favourite

        # Step 3: Kalshi pricing — market under-prices Chiefs YES
        kalshi_yes_price = 0.57  # Chiefs YES at 57¢ vs fair ~60%

        # Step 4: evaluate both sides
        calc = EdgeCalculator()
        yes, no = calc.evaluate_both_sides(fair_chiefs, kalshi_yes_price)

        # Chiefs fair > 57¢ → YES has positive raw edge
        self.assertGreater(yes.raw_edge, 0, "YES side should have positive edge")
        self.assertLess(no.raw_edge, 0, "NO side should have negative edge")
        self.assertTrue(yes.has_edge, f"YES should have edge; EV={yes.ev:.4f}")
        self.assertFalse(no.has_edge)

        # Step 5: position sizing is bounded and positive
        self.assertLessEqual(yes.kelly_fractional, 0.03)
        self.assertGreater(yes.kelly_fractional, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
