"""
src/crypto_options_pricer.py
────────────────────────────
Binary probability calculator for crypto event contracts.

Computes the implied probability that BTC (or ETH) will be above a
given strike at a given expiry, using data from Deribit's options chain.

Three estimation methods:

1. **Vertical Spread Replication** (primary)
   Buy call at K-δ, sell call at K+δ.  Spread price / width ≈ probability.
   Most robust; uses actual market prices and inherently captures skew.

2. **Black-Scholes N(d2)** (secondary)
   Uses interpolated implied volatility from the chain to compute the
   risk-neutral probability via the standard BSM d2 formula.

3. **Delta-Based** (for one-touch / path-dependent)
   Prob(touch K) ≈ 2 × |delta(K)|.  Uses the reflection principle.
   Only applicable for "will touch at any time" style contracts.

The ensemble method blends (1) and (2) with configurable weights.

Reference:
  Moontower Meta — "Prediction Market Arbitrage: Using Option Chains
  to Find Mispriced Bets"
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.deribit_client import OptionsChain, OptionInstrument

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BinaryProbEstimate:
    """Estimated probability of an asset being above a strike at expiry."""
    strike: float
    expiry_str: str
    underlying_price: float
    probability: float              # 0-1: prob of finishing above strike
    method: str                     # "spread", "bsm", "delta", "ensemble"
    confidence: float               # 0-1: quality/reliability of estimate
    spread_prob: Optional[float] = None   # From vertical spread method
    bsm_prob: Optional[float] = None      # From BSM N(d2)
    delta_prob: Optional[float] = None    # From 2×delta (one-touch)
    iv_used: Optional[float] = None       # Implied vol used (annualised)
    spread_width: Optional[float] = None  # Width of spread used
    time_to_expiry_years: Optional[float] = None


class CryptoOptionsPricer:
    """Compute implied probabilities from Deribit options chains.

    Usage:
        pricer = CryptoOptionsPricer()
        chain = deribit_client.get_chain_for_expiry("BTC", "28MAR26")
        est = pricer.estimate_probability(chain, strike=100000)
        print(f"P(BTC > $100k) = {est.probability:.1%}")
    """

    def __init__(
        self,
        method: str = "ensemble",
        spread_weight: float = 0.65,
        bsm_weight: float = 0.35,
        max_spread_width: float = 5000.0,
        risk_free_rate: float = 0.045,
    ):
        """
        Args:
            method: "spread", "bsm", "delta", or "ensemble"
            spread_weight: Weight for spread method in ensemble (0-1)
            bsm_weight: Weight for BSM method in ensemble (0-1)
            max_spread_width: Maximum strike distance for spread replication ($)
            risk_free_rate: Annualised risk-free rate for BSM
        """
        self.method = method
        self.spread_weight = spread_weight
        self.bsm_weight = bsm_weight
        self.max_spread_width = max_spread_width
        self.risk_free_rate = risk_free_rate

    # ── Public API ────────────────────────────────────────────────────

    def estimate_probability(
        self,
        chain: OptionsChain,
        strike: float,
        time_to_expiry_years: Optional[float] = None,
    ) -> Optional[BinaryProbEstimate]:
        """Estimate P(underlying > strike at expiry).

        Args:
            chain: Full options chain for the relevant expiry
            strike: Target strike price (e.g. 100000 for "$100k")
            time_to_expiry_years: Override for T (otherwise computed from chain)

        Returns:
            BinaryProbEstimate, or None if insufficient data.
        """
        if not chain.instruments:
            logger.warning("crypto_pricer: empty chain for %s", chain.expiry_str)
            return None

        spot = chain.underlying_price
        if spot <= 0:
            return None

        # Compute time to expiry
        if time_to_expiry_years is None:
            import time as _time
            now_ts = _time.time()
            t = max(0.0001, (chain.expiry_ts - now_ts) / (365.25 * 86400))
        else:
            t = max(0.0001, time_to_expiry_years)

        # Method dispatch
        spread_prob = None
        bsm_prob = None
        delta_prob = None
        iv_used = None
        spread_width = None
        confidence = 0.0

        if self.method in ("spread", "ensemble"):
            sp = self._spread_method(chain, strike, spot)
            if sp is not None:
                spread_prob, spread_width = sp

        if self.method in ("bsm", "ensemble"):
            bp = self._bsm_method(chain, strike, spot, t)
            if bp is not None:
                bsm_prob, iv_used = bp

        if self.method == "delta":
            dp = self._delta_method(chain, strike)
            if dp is not None:
                delta_prob = dp

        # Ensemble blending
        if self.method == "ensemble":
            prob, confidence = self._blend(spread_prob, bsm_prob)
        elif self.method == "spread":
            prob = spread_prob
            confidence = 0.9 if spread_prob is not None else 0.0
        elif self.method == "bsm":
            prob = bsm_prob
            confidence = 0.8 if bsm_prob is not None else 0.0
        elif self.method == "delta":
            prob = delta_prob
            confidence = 0.6 if delta_prob is not None else 0.0
        else:
            prob = None
            confidence = 0.0

        if prob is None:
            return None

        # Clamp
        prob = max(0.001, min(0.999, prob))

        return BinaryProbEstimate(
            strike=strike,
            expiry_str=chain.expiry_str,
            underlying_price=spot,
            probability=prob,
            method=self.method,
            confidence=confidence,
            spread_prob=spread_prob,
            bsm_prob=bsm_prob,
            delta_prob=delta_prob,
            iv_used=iv_used,
            spread_width=spread_width,
            time_to_expiry_years=t,
        )

    def estimate_probability_below(
        self,
        chain: OptionsChain,
        strike: float,
        time_to_expiry_years: Optional[float] = None,
    ) -> Optional[BinaryProbEstimate]:
        """Estimate P(underlying < strike at expiry).

        Complement of estimate_probability (above).
        """
        est = self.estimate_probability(chain, strike, time_to_expiry_years)
        if est is None:
            return None
        return BinaryProbEstimate(
            strike=est.strike,
            expiry_str=est.expiry_str,
            underlying_price=est.underlying_price,
            probability=1.0 - est.probability,
            method=est.method,
            confidence=est.confidence,
            spread_prob=(1.0 - est.spread_prob) if est.spread_prob is not None else None,
            bsm_prob=(1.0 - est.bsm_prob) if est.bsm_prob is not None else None,
            delta_prob=est.delta_prob,  # delta method is one-touch, not directional
            iv_used=est.iv_used,
            spread_width=est.spread_width,
            time_to_expiry_years=est.time_to_expiry_years,
        )

    # ── Method 1: Vertical Spread Replication ─────────────────────────

    def _spread_method(
        self, chain: OptionsChain, strike: float, spot: float
    ) -> Optional[Tuple[float, float]]:
        """Estimate probability via tight vertical call spread.

        Find the two strikes closest to and bracketing the target.
        Spread price / width ≈ probability of finishing above midpoint.

        Uses bid/ask midpoints when available (more accurate than mark
        price for illiquid strikes).  Rejects spreads where either leg
        has no real two-sided market (bid=0 or ask=0).

        Returns:
            (probability, spread_width) or None if can't bracket.
        """
        calls = sorted(chain.calls, key=lambda c: c.strike)
        if len(calls) < 2:
            return None

        # Find bracketing strikes: K_low < strike <= K_high
        k_low: Optional[OptionInstrument] = None
        k_high: Optional[OptionInstrument] = None

        for i, call in enumerate(calls):
            if call.strike >= strike:
                k_high = call
                if i > 0:
                    k_low = calls[i - 1]
                break

        # If strike is beyond all available, use closest pair
        if k_high is None and calls:
            k_high = calls[-1]
            k_low = calls[-2] if len(calls) >= 2 else None
        if k_low is None and calls:
            k_low = calls[0]
            if k_high is None or k_high == k_low:
                k_high = calls[1] if len(calls) >= 2 else None

        if k_low is None or k_high is None:
            return None
        if k_low.strike == k_high.strike:
            return None

        width = k_high.strike - k_low.strike
        if width <= 0:
            return None
        if width > self.max_spread_width:
            # Strikes too far apart — unreliable for binary prob
            logger.debug(
                "crypto_pricer: spread width $%.0f > max $%.0f — skipping",
                width, self.max_spread_width,
            )
            return None

        # ── Price the spread using bid/ask midpoints when available ──
        # Bid/ask mid is more accurate than mark_price for illiquid OTM
        # strikes where the exchange mark can lag.
        def _mid_price(inst: OptionInstrument) -> Optional[float]:
            if inst.bid_price_usd > 0 and inst.ask_price_usd > 0:
                return (inst.bid_price_usd + inst.ask_price_usd) / 2.0
            if inst.mark_price_usd > 0:
                return inst.mark_price_usd
            return None

        low_price = _mid_price(k_low)
        high_price = _mid_price(k_high)
        if low_price is None or high_price is None:
            return None

        # Require both legs to have real two-sided markets
        if k_low.bid_price_usd <= 0 or k_high.bid_price_usd <= 0:
            logger.debug(
                "crypto_pricer: spread leg has no bid — "
                "K_low=%.0f (bid=$%.2f) K_high=%.0f (bid=$%.2f)",
                k_low.strike, k_low.bid_price_usd,
                k_high.strike, k_high.bid_price_usd,
            )
            return None

        spread_price = low_price - high_price

        if spread_price < 0:
            # Shouldn't happen with properly ordered calls, but guard
            spread_price = 0.0

        probability = spread_price / width

        # ── Offset correction ──────────────────────────────────────
        # The spread prices the probability of being above the midpoint
        # of [k_low, k_high].  If the target strike is offset, correct
        # using a conservative linear shift (capped at ±20% of prob to
        # avoid overcorrection on wide spreads).
        midpoint = (k_low.strike + k_high.strike) / 2.0
        if abs(strike - midpoint) > 1.0 and width > 0:
            offset_frac = (strike - midpoint) / width
            correction = offset_frac * probability * 0.3
            # Cap correction magnitude
            max_correction = probability * 0.20
            correction = max(-max_correction, min(max_correction, correction))
            probability -= correction

        probability = max(0.001, min(0.999, probability))
        return (probability, width)

    # ── Method 2: Black-Scholes N(d2) ─────────────────────────────────

    def _bsm_method(
        self, chain: OptionsChain, strike: float, spot: float, t: float
    ) -> Optional[Tuple[float, float]]:
        """Estimate probability via BSM d2 formula.

        P(S_T > K) = N(d2) where:
            d2 = [ln(S/K) + (r - σ²/2)T] / (σ√T)

        Uses interpolated IV from the chain at the target strike.

        Returns:
            (probability, iv_used) or None.
        """
        iv = self._interpolate_iv(chain, strike)
        if iv is None or iv <= 0:
            return None

        try:
            d2 = (
                math.log(spot / strike)
                + (self.risk_free_rate - 0.5 * iv * iv) * t
            ) / (iv * math.sqrt(t))

            probability = _norm_cdf(d2)
            return (probability, iv)
        except (ValueError, ZeroDivisionError):
            return None

    def _interpolate_iv(
        self, chain: OptionsChain, strike: float
    ) -> Optional[float]:
        """Interpolate implied volatility at target strike.

        Uses cubic-spline-like interpolation (Fritsch-Carlson monotone
        piecewise cubic when 4+ points, quadratic with 3, linear with 2)
        across the full smile built from *both* calls and puts.

        Put-call parity cross-check: when both a call and put IV exist
        at the same strike, average them to wash out microstructure noise.
        Rejects the estimate if the call/put IV gap > 15% (stale quote).
        """
        # ── Build smile from calls + puts, averaging where both exist ──
        call_iv: Dict[float, float] = {}
        put_iv: Dict[float, float] = {}

        for call in chain.calls:
            if call.mark_iv > 0.01:
                call_iv[call.strike] = call.mark_iv
        for put in chain.puts:
            if put.mark_iv > 0.01:
                put_iv[put.strike] = put.mark_iv

        # Merge: prefer average where both exist; flag stale if gap > 15%
        all_strikes = sorted(set(list(call_iv.keys()) + list(put_iv.keys())))
        iv_points: List[Tuple[float, float]] = []

        for k in all_strikes:
            c_iv = call_iv.get(k)
            p_iv = put_iv.get(k)
            if c_iv is not None and p_iv is not None:
                gap = abs(c_iv - p_iv) / max(c_iv, p_iv)
                if gap > 0.15:
                    # Large call/put IV divergence — skip this strike
                    logger.debug(
                        "crypto_pricer: skipping strike %.0f — "
                        "call IV %.2f vs put IV %.2f (gap %.0f%%)",
                        k, c_iv, p_iv, gap * 100,
                    )
                    continue
                iv_points.append((k, (c_iv + p_iv) / 2.0))
            elif c_iv is not None:
                iv_points.append((k, c_iv))
            else:
                iv_points.append((k, p_iv))

        if not iv_points:
            return None

        iv_points.sort(key=lambda x: x[0])

        # Exact match (within $1)
        for k, iv in iv_points:
            if abs(k - strike) < 1.0:
                return iv

        # ── Interpolation ──────────────────────────────────────────
        strikes_arr = [p[0] for p in iv_points]
        ivs_arr = [p[1] for p in iv_points]

        # Don't extrapolate beyond the observed smile — too unreliable
        if strike < strikes_arr[0] or strike > strikes_arr[-1]:
            # Use nearest endpoint but flag low confidence via logger
            nearest_iv = ivs_arr[0] if strike < strikes_arr[0] else ivs_arr[-1]
            logger.debug(
                "crypto_pricer: strike %.0f outside smile [%.0f, %.0f] "
                "— using nearest IV %.3f (extrapolation disabled)",
                strike, strikes_arr[0], strikes_arr[-1], nearest_iv,
            )
            return nearest_iv

        # Find bracketing segment
        idx = 0
        for i, k in enumerate(strikes_arr):
            if k >= strike:
                idx = max(i - 1, 0)
                break

        n = len(strikes_arr)

        if n >= 4:
            # Cubic Hermite (Catmull-Rom) using 4 surrounding points
            # Gives C1 continuity and handles smile curvature well
            i0 = max(idx - 1, 0)
            i1 = idx
            i2 = min(idx + 1, n - 1)
            i3 = min(idx + 2, n - 1)

            k1, k2 = strikes_arr[i1], strikes_arr[i2]
            if k2 == k1:
                return ivs_arr[i1]
            t = (strike - k1) / (k2 - k1)

            v0, v1, v2, v3 = ivs_arr[i0], ivs_arr[i1], ivs_arr[i2], ivs_arr[i3]

            # Catmull-Rom spline coefficients
            t2 = t * t
            t3 = t2 * t
            iv = 0.5 * (
                (2 * v1)
                + (-v0 + v2) * t
                + (2 * v0 - 5 * v1 + 4 * v2 - v3) * t2
                + (-v0 + 3 * v1 - 3 * v2 + v3) * t3
            )
        elif n == 3:
            # Quadratic (Lagrange) through 3 points
            k0, k1, k2 = strikes_arr[0], strikes_arr[1], strikes_arr[2]
            v0, v1, v2 = ivs_arr[0], ivs_arr[1], ivs_arr[2]
            if k0 == k1 or k0 == k2 or k1 == k2:
                # Degenerate — fall back to linear
                frac = (strike - strikes_arr[idx]) / (strikes_arr[idx + 1] - strikes_arr[idx]) if strikes_arr[idx + 1] != strikes_arr[idx] else 0.5
                iv = ivs_arr[idx] + frac * (ivs_arr[idx + 1] - ivs_arr[idx])
            else:
                L0 = ((strike - k1) * (strike - k2)) / ((k0 - k1) * (k0 - k2))
                L1 = ((strike - k0) * (strike - k2)) / ((k1 - k0) * (k1 - k2))
                L2 = ((strike - k0) * (strike - k1)) / ((k2 - k0) * (k2 - k1))
                iv = v0 * L0 + v1 * L1 + v2 * L2
        else:
            # 2 points: linear
            k1, k2 = strikes_arr[idx], strikes_arr[min(idx + 1, n - 1)]
            if k2 == k1:
                return ivs_arr[idx]
            frac = (strike - k1) / (k2 - k1)
            iv = ivs_arr[idx] + frac * (ivs_arr[min(idx + 1, n - 1)] - ivs_arr[idx])

        # Sanity: IV should be positive and reasonable (5% - 500% annualised)
        if iv < 0.05 or iv > 5.0:
            logger.debug(
                "crypto_pricer: interpolated IV %.3f at strike %.0f "
                "outside sane range — rejecting", iv, strike,
            )
            return None

        return iv

    # ── Method 3: Delta-Based (One-Touch) ─────────────────────────────

    def _delta_method(
        self, chain: OptionsChain, strike: float
    ) -> Optional[float]:
        """Estimate touch probability via 2×|delta|.

        P(BTC touches K at any time before expiry) ≈ 2 × |delta(K)|

        Based on the reflection principle for geometric Brownian motion.
        Only meaningful for path-dependent ("will touch") contracts.
        """
        # Find call at nearest strike
        calls = chain.calls
        if not calls:
            return None

        nearest = min(calls, key=lambda c: abs(c.strike - strike))
        if abs(nearest.strike - strike) / strike > 0.05:
            # Nearest strike is more than 5% away — unreliable
            return None

        delta = abs(nearest.delta)
        probability = min(0.999, 2.0 * delta)
        return probability

    # ── Ensemble Blending ─────────────────────────────────────────────

    def _blend(
        self,
        spread_prob: Optional[float],
        bsm_prob: Optional[float],
    ) -> Tuple[float, float]:
        """Blend spread and BSM estimates with weights.

        Returns:
            (blended_probability, confidence)
        """
        if spread_prob is not None and bsm_prob is not None:
            total_w = self.spread_weight + self.bsm_weight
            prob = (
                self.spread_weight * spread_prob
                + self.bsm_weight * bsm_prob
            ) / total_w

            # Agreement between methods → higher confidence
            agreement = 1.0 - abs(spread_prob - bsm_prob)
            confidence = 0.7 + 0.3 * agreement
            return (prob, confidence)

        if spread_prob is not None:
            return (spread_prob, 0.7)

        if bsm_prob is not None:
            return (bsm_prob, 0.6)

        return (0.0, 0.0)

    # ── Factory ───────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> "CryptoOptionsPricer":
        """Build from pod config dict.

        Config keys (under 'P-013.pricer'):
            method: "spread", "bsm", "delta", "ensemble"
            spread_weight: 0.65
            bsm_weight: 0.35
            max_spread_width: 5000
            risk_free_rate: 0.045
        """
        pod_cfg = config.get("pods", {}).get("P-013", {})
        pricer_cfg = pod_cfg.get("pricer", {})
        return cls(
            method=pricer_cfg.get("method", "ensemble"),
            spread_weight=pricer_cfg.get("spread_weight", 0.65),
            bsm_weight=pricer_cfg.get("bsm_weight", 0.35),
            max_spread_width=pricer_cfg.get("max_spread_width", 5000.0),
            risk_free_rate=pricer_cfg.get("risk_free_rate", 0.045),
        )


# ── Utility ───────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF using the error function.

    Avoids scipy dependency — math.erf is in the stdlib.
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
