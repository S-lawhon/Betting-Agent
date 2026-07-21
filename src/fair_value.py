"""
src/fair_value.py
─────────────────
Fair value estimation for P-006 (Sportsbook-Polymarket Consensus).

Provides three methods of estimating "true" probability:

  1. **consensus** — Existing Pinnacle-weighted multi-book consensus
     (Pinnacle 2×, others 1×).  Good baseline but diluted by softer books.

  2. **pinnacle_only** — Uses only Pinnacle's devigged line.  Pinnacle is
     the sharpest retail sportsbook globally and historically the most
     predictive pre-game price.  Eliminates noise from softer books that
     may shade lines for liability management.

  3. **ensemble** — Weighted blend of (a) Pinnacle-only, (b) Elo model
     prediction, and (c) multi-book consensus.  The idea: Pinnacle is
     your primary signal, Elo provides an independent model-based estimate
     that can catch games where the market is slow to react (e.g. a team
     is on a 5-game slide and Elo has adjusted but the market hasn't fully
     priced it in), and consensus serves as a smoothing/anchoring term.

The ensemble weights are configurable in config_multi_pod.yaml under
``pods.P-006.fair_value``.

Usage:
    estimator = FairValueEstimator.from_config(config, odds_client, elo_model)
    result = estimator.estimate(event, sport="basketball_nba")
    fair_home = result.home_prob
    fair_away = result.away_prob
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FairValueResult:
    """Result of fair value estimation."""
    home_prob: float
    away_prob: float
    method: str            # "consensus", "pinnacle_only", or "ensemble"
    pinnacle_home: Optional[float] = None   # Pinnacle-only home prob (if available)
    elo_home: Optional[float] = None        # Elo model home prob (if available)
    consensus_home: Optional[float] = None  # Multi-book consensus home prob
    confidence: float = 1.0                 # Confidence multiplier (lower if degraded)


class FairValueEstimator:
    """
    Multi-method fair value estimator for sports betting.

    Wraps the OddsClient consensus, extracts Pinnacle-only lines,
    and blends with an Elo model to produce the best available
    probability estimate.
    """

    def __init__(
        self,
        odds_client,
        elo_model=None,
        method: str = "ensemble",
        pinnacle_weight: float = 0.60,
        elo_weight: float = 0.20,
        consensus_weight: float = 0.20,
    ) -> None:
        self._odds_client = odds_client
        self._elo_model = elo_model
        self._method = method
        self._pinnacle_w = pinnacle_weight
        self._elo_w = elo_weight
        self._consensus_w = consensus_weight

        # Validate weights sum to ~1
        total = self._pinnacle_w + self._elo_w + self._consensus_w
        if abs(total - 1.0) > 0.01:
            logger.warning(
                "FairValue: ensemble weights sum to %.3f (expected 1.0) — normalizing",
                total,
            )
            self._pinnacle_w /= total
            self._elo_w /= total
            self._consensus_w /= total

    @classmethod
    def from_config(cls, config: dict, odds_client, elo_model=None) -> "FairValueEstimator":
        """Construct from loaded config_multi_pod.yaml."""
        p006_cfg = config.get("pods", {}).get("P-006", {})
        fv_cfg = p006_cfg.get("fair_value", {})

        return cls(
            odds_client=odds_client,
            elo_model=elo_model,
            method=fv_cfg.get("method", "consensus"),
            pinnacle_weight=fv_cfg.get("pinnacle_weight", 0.60),
            elo_weight=fv_cfg.get("elo_weight", 0.20),
            consensus_weight=fv_cfg.get("consensus_weight", 0.20),
        )

    def estimate(self, event, sport: str = "") -> Optional[FairValueResult]:
        """
        Estimate fair home/away probabilities for an event.

        Parameters
        ----------
        event : OddsEvent
            Event object with bookmaker_odds attached.
        sport : str
            Sport key (for Elo model lookup).

        Returns
        -------
        FairValueResult or None if insufficient data.
        """
        # ── Step 1: Get multi-book consensus (always computed as baseline) ──
        consensus = self._odds_client.get_consensus(event)
        if consensus is None:
            return None

        consensus_home = consensus.home_prob

        # ── Step 2: Extract Pinnacle-only fair value ─────────────────────
        pinnacle_home = self._get_pinnacle_only(event)

        # ── Step 3: Get Elo prediction (if model available) ──────────────
        elo_home = None
        if self._elo_model is not None and sport:
            home_team = getattr(event, "home_team", "")
            away_team = getattr(event, "away_team", "")
            if home_team and away_team:
                elo_home = self._elo_model.predict(home_team, away_team, sport)

        # ── Step 4: Route to the configured method ───────────────────────
        if self._method == "pinnacle_only":
            return self._pinnacle_only_result(pinnacle_home, consensus_home, elo_home)
        elif self._method == "ensemble":
            return self._ensemble_result(pinnacle_home, consensus_home, elo_home, sport)
        else:
            # Default: legacy consensus
            return FairValueResult(
                home_prob=consensus_home,
                away_prob=1.0 - consensus_home,
                method="consensus",
                consensus_home=consensus_home,
                pinnacle_home=pinnacle_home,
                elo_home=elo_home,
                confidence=1.0,
            )

    def _get_pinnacle_only(self, event) -> Optional[float]:
        """
        Extract Pinnacle's devigged home probability from the event.

        Looks through the event's bookmaker_odds for Pinnacle specifically,
        devigs their line, and returns the fair home probability.
        """
        from utils import american_to_implied_prob, remove_vig_additive

        for bm in getattr(event, "bookmaker_odds", []):
            if getattr(bm, "bookmaker_key", "") != "pinnacle":
                continue
            outcomes = getattr(bm, "outcomes", [])
            if len(outcomes) != 2:
                continue

            o1, o2 = outcomes
            home_team = getattr(event, "home_team", "")

            home_outcome = o1 if getattr(o1, "name", "") == home_team else o2
            away_outcome = o2 if getattr(o1, "name", "") == home_team else o1

            home_implied = american_to_implied_prob(home_outcome.price)
            away_implied = american_to_implied_prob(away_outcome.price)

            fair_home, _ = remove_vig_additive([home_implied, away_implied])
            return max(0.001, min(0.999, fair_home))

        return None  # Pinnacle not available for this event

    def _pinnacle_only_result(
        self,
        pinnacle_home: Optional[float],
        consensus_home: float,
        elo_home: Optional[float],
    ) -> FairValueResult:
        """Return Pinnacle-only fair value, falling back to consensus."""
        if pinnacle_home is not None:
            return FairValueResult(
                home_prob=pinnacle_home,
                away_prob=1.0 - pinnacle_home,
                method="pinnacle_only",
                pinnacle_home=pinnacle_home,
                consensus_home=consensus_home,
                elo_home=elo_home,
                confidence=1.0,
            )
        # Fallback: Pinnacle not available → use consensus with reduced confidence
        logger.info("FairValue: Pinnacle unavailable, falling back to consensus")
        return FairValueResult(
            home_prob=consensus_home,
            away_prob=1.0 - consensus_home,
            method="consensus_fallback",
            pinnacle_home=None,
            consensus_home=consensus_home,
            elo_home=elo_home,
            confidence=0.85,  # Lower confidence when Pinnacle absent
        )

    def _ensemble_result(
        self,
        pinnacle_home: Optional[float],
        consensus_home: float,
        elo_home: Optional[float],
        sport: str,
    ) -> FairValueResult:
        """
        Blend available signals into an ensemble fair value.

        Gracefully degrades: if Pinnacle or Elo is unavailable, their
        weight is redistributed proportionally to the remaining signals.
        """
        signals = {}
        weights = {}

        # Always have consensus
        signals["consensus"] = consensus_home
        weights["consensus"] = self._consensus_w

        # Pinnacle (if available)
        if pinnacle_home is not None:
            signals["pinnacle"] = pinnacle_home
            weights["pinnacle"] = self._pinnacle_w

        # Elo (if available and model has ratings for this sport)
        if elo_home is not None and self._elo_model is not None:
            # Only include Elo if model has accumulated enough data
            # (at least 10 teams rated = ~1 week of games)
            if self._elo_model.has_ratings(sport) and self._elo_model.team_count(sport) >= 10:
                signals["elo"] = elo_home
                weights["elo"] = self._elo_w
            else:
                logger.debug(
                    "FairValue: Elo model too immature for %s (%d teams) — excluded from ensemble",
                    sport, self._elo_model.team_count(sport) if self._elo_model else 0,
                )

        # Normalize weights to sum to 1
        total_w = sum(weights.values())
        if total_w <= 0:
            # Should never happen (consensus is always present)
            return FairValueResult(
                home_prob=consensus_home,
                away_prob=1.0 - consensus_home,
                method="consensus",
                consensus_home=consensus_home,
                confidence=0.7,
            )

        # Compute weighted average
        blended = sum(
            signals[k] * (weights[k] / total_w) for k in signals
        )
        blended = max(0.001, min(0.999, blended))

        # Confidence: full if all 3 signals present, reduced if degraded
        confidence = 1.0
        if "pinnacle" not in signals:
            confidence *= 0.85
        if "elo" not in signals:
            confidence *= 0.95  # Elo is supplementary, less impact

        method_parts = sorted(signals.keys())
        method_name = "ensemble_" + "+".join(method_parts)

        logger.debug(
            "FairValue: %s home=%.4f signals=%s weights=%s confidence=%.2f",
            method_name, blended,
            {k: f"{v:.4f}" for k, v in signals.items()},
            {k: f"{v:.2f}" for k, v in weights.items()},
            confidence,
        )

        return FairValueResult(
            home_prob=blended,
            away_prob=1.0 - blended,
            method=method_name,
            pinnacle_home=pinnacle_home,
            elo_home=elo_home,
            consensus_home=consensus_home,
            confidence=confidence,
        )
