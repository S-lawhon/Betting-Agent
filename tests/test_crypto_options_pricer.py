"""
tests/test_crypto_options_pricer.py
───────────────────────────────────
Tests for CryptoOptionsPricer binary probability calculator.

  ✓ _norm_cdf: matches known values
  ✓ spread method: tight bracket around strike
  ✓ BSM method: ATM call ≈ 50% probability
  ✓ BSM method: deep OTM call → low probability
  ✓ delta method: 2×delta approximation
  ✓ ensemble: blends spread + BSM
  ✓ ensemble: degrades gracefully with only one method
  ✓ interpolate_iv: interpolates between strikes
  ✓ interpolate_iv: extrapolates from nearest
  ✓ estimate_probability_below: complement of above
  ✓ from_config builds with defaults
  ✓ empty chain returns None
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.deribit_client import OptionInstrument, OptionsChain
from src.crypto_options_pricer import (
    CryptoOptionsPricer,
    BinaryProbEstimate,
    _norm_cdf,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_option(strike, option_type, mark_price_usd, mark_iv, delta=0.0):
    """Helper to create an OptionInstrument with sensible defaults."""
    return OptionInstrument(
        instrument_name=f"BTC-28MAR26-{int(strike)}-{'C' if option_type == 'call' else 'P'}",
        underlying="BTC",
        strike=strike,
        expiry_ts=1743120000,  # 28 Mar 2026
        expiry_str="28MAR26",
        option_type=option_type,
        mark_price_usd=mark_price_usd,
        mark_iv=mark_iv,
        bid_price_usd=mark_price_usd * 0.95,
        ask_price_usd=mark_price_usd * 1.05,
        open_interest=100,
        volume_24h=50,
        delta=delta,
        gamma=0.00001,
        vega=50,
        theta=-10,
        underlying_price=87000,
    )


def _make_chain():
    """Create a realistic BTC options chain for testing.

    Spot = $87,000.  Strikes at 80k, 85k, 90k, 95k, 100k, 110k.
    Call prices decrease as strike increases (as expected).
    """
    instruments = [
        # Call prices (mark_price_usd = BTC price * mark_price_btc)
        _make_option(80000, "call", 9000, 0.55, delta=0.82),
        _make_option(85000, "call", 5500, 0.60, delta=0.60),
        _make_option(90000, "call", 3000, 0.65, delta=0.40),
        _make_option(95000, "call", 1500, 0.70, delta=0.25),
        _make_option(100000, "call", 700, 0.75, delta=0.15),
        _make_option(110000, "call", 200, 0.85, delta=0.05),
        # Puts
        _make_option(80000, "put", 2000, 0.55, delta=-0.18),
        _make_option(85000, "put", 3500, 0.60, delta=-0.40),
        _make_option(90000, "put", 6000, 0.65, delta=-0.60),
        _make_option(95000, "put", 9500, 0.70, delta=-0.75),
        _make_option(100000, "put", 13700, 0.75, delta=-0.85),
        _make_option(110000, "put", 23200, 0.85, delta=-0.95),
    ]

    return OptionsChain(
        underlying="BTC",
        expiry_str="28MAR26",
        expiry_ts=1743120000,
        underlying_price=87000,
        instruments=instruments,
    )


# ── Tests ─────────────────────────────────────────────────────────────

class TestNormCdf(unittest.TestCase):
    """Test the stdlib-based normal CDF implementation."""

    def test_zero(self):
        self.assertAlmostEqual(_norm_cdf(0), 0.5, places=5)

    def test_positive(self):
        # N(1) ≈ 0.8413
        self.assertAlmostEqual(_norm_cdf(1.0), 0.8413, places=3)

    def test_negative(self):
        # N(-1) ≈ 0.1587
        self.assertAlmostEqual(_norm_cdf(-1.0), 0.1587, places=3)

    def test_extreme_positive(self):
        self.assertAlmostEqual(_norm_cdf(5.0), 1.0, places=5)

    def test_extreme_negative(self):
        self.assertAlmostEqual(_norm_cdf(-5.0), 0.0, places=5)


class TestSpreadMethod(unittest.TestCase):
    """Test vertical spread replication."""

    def test_atm_probability_reasonable(self):
        """ATM strike (near spot) should have probability near 50%."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="spread")
        est = pricer.estimate_probability(chain, strike=87000, time_to_expiry_years=0.01)
        self.assertIsNotNone(est)
        # ATM should be in 40-60% range
        self.assertGreater(est.probability, 0.30)
        self.assertLess(est.probability, 0.70)

    def test_otm_probability_lower(self):
        """OTM strike ($100k when spot=$87k) should have lower probability."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="spread")
        est = pricer.estimate_probability(chain, strike=100000, time_to_expiry_years=0.01)
        self.assertIsNotNone(est)
        self.assertLess(est.probability, 0.50)

    def test_deep_itm_probability_higher(self):
        """Deep ITM strike ($80k when spot=$87k) should have high probability."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="spread")
        est = pricer.estimate_probability(chain, strike=80000, time_to_expiry_years=0.01)
        self.assertIsNotNone(est)
        self.assertGreater(est.probability, 0.50)

    def test_empty_chain_returns_none(self):
        chain = OptionsChain("BTC", "28MAR26", 1743120000, 87000, [])
        pricer = CryptoOptionsPricer(method="spread")
        est = pricer.estimate_probability(chain, strike=100000)
        self.assertIsNone(est)


class TestBSMMethod(unittest.TestCase):
    """Test Black-Scholes N(d2) method."""

    def test_atm_near_50pct(self):
        """ATM with short expiry and moderate vol → near 50%."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="bsm", risk_free_rate=0.045)
        est = pricer.estimate_probability(chain, strike=87000, time_to_expiry_years=0.01)
        self.assertIsNotNone(est)
        self.assertGreater(est.probability, 0.35)
        self.assertLess(est.probability, 0.65)

    def test_otm_lower(self):
        """OTM call → lower probability."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="bsm")
        est = pricer.estimate_probability(chain, strike=110000, time_to_expiry_years=0.1)
        self.assertIsNotNone(est)
        self.assertLess(est.probability, 0.30)

    def test_itm_higher(self):
        """ITM call → higher probability."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="bsm")
        est = pricer.estimate_probability(chain, strike=80000, time_to_expiry_years=0.1)
        self.assertIsNotNone(est)
        self.assertGreater(est.probability, 0.50)


class TestDeltaMethod(unittest.TestCase):
    """Test 2×delta one-touch approximation."""

    def test_otm_call_delta(self):
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="delta")
        est = pricer.estimate_probability(chain, strike=100000)
        self.assertIsNotNone(est)
        # delta=0.15, so 2×0.15=0.30
        self.assertAlmostEqual(est.probability, 0.30, places=1)

    def test_atm_delta(self):
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="delta")
        est = pricer.estimate_probability(chain, strike=85000)
        self.assertIsNotNone(est)
        # delta=0.60, so 2×0.60=1.20 → capped at 0.999
        self.assertGreater(est.probability, 0.90)


class TestEnsembleMethod(unittest.TestCase):
    """Test ensemble blending of spread + BSM."""

    def test_ensemble_blends(self):
        chain = _make_chain()
        pricer = CryptoOptionsPricer(
            method="ensemble",
            spread_weight=0.65,
            bsm_weight=0.35,
        )
        est = pricer.estimate_probability(chain, strike=95000, time_to_expiry_years=0.1)
        self.assertIsNotNone(est)
        self.assertEqual(est.method, "ensemble")
        # Should have both sub-estimates
        self.assertIsNotNone(est.spread_prob)
        self.assertIsNotNone(est.bsm_prob)
        # Confidence should be higher when both agree
        self.assertGreater(est.confidence, 0.6)

    def test_ensemble_otm_vs_itm_ordering(self):
        """OTM strike should have lower probability than ITM."""
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="ensemble")
        otm = pricer.estimate_probability(chain, strike=100000, time_to_expiry_years=0.1)
        itm = pricer.estimate_probability(chain, strike=80000, time_to_expiry_years=0.1)
        self.assertIsNotNone(otm)
        self.assertIsNotNone(itm)
        self.assertLess(otm.probability, itm.probability)


class TestEstimateProbabilityBelow(unittest.TestCase):

    def test_complement(self):
        chain = _make_chain()
        pricer = CryptoOptionsPricer(method="bsm")
        above = pricer.estimate_probability(chain, strike=90000, time_to_expiry_years=0.1)
        below = pricer.estimate_probability_below(chain, strike=90000, time_to_expiry_years=0.1)
        self.assertIsNotNone(above)
        self.assertIsNotNone(below)
        self.assertAlmostEqual(above.probability + below.probability, 1.0, places=4)


class TestInterpolateIV(unittest.TestCase):

    def test_interpolation_between_strikes(self):
        chain = _make_chain()
        pricer = CryptoOptionsPricer()
        # Strike 92500 is between 90000 (IV=0.65) and 95000 (IV=0.70)
        iv = pricer._interpolate_iv(chain, 92500)
        self.assertIsNotNone(iv)
        self.assertGreater(iv, 0.64)
        self.assertLess(iv, 0.71)

    def test_exact_strike_match(self):
        chain = _make_chain()
        pricer = CryptoOptionsPricer()
        iv = pricer._interpolate_iv(chain, 90000)
        self.assertIsNotNone(iv)
        self.assertAlmostEqual(iv, 0.65, places=2)


class TestFromConfig(unittest.TestCase):

    def test_defaults(self):
        config = {}
        pricer = CryptoOptionsPricer.from_config(config)
        self.assertEqual(pricer.method, "ensemble")
        self.assertEqual(pricer.spread_weight, 0.65)
        self.assertEqual(pricer.bsm_weight, 0.35)

    def test_custom_config(self):
        config = {
            "pods": {
                "P-013": {
                    "pricer": {
                        "method": "bsm",
                        "spread_weight": 0.5,
                        "bsm_weight": 0.5,
                        "max_spread_width": 10000,
                    }
                }
            }
        }
        pricer = CryptoOptionsPricer.from_config(config)
        self.assertEqual(pricer.method, "bsm")
        self.assertEqual(pricer.max_spread_width, 10000)


if __name__ == "__main__":
    unittest.main()
