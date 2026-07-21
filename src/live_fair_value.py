"""
src/live_fair_value.py
──────────────────────
Game-state-aware fair value estimation for P-014 (Live Game Agent).

Extends the consensus logic from LiveOddsPoller with:
  1. Game progress weighting — later in the game, consensus is more reliable
  2. Score-adjusted confidence — blowouts have higher-confidence fair values
  3. Pinnacle isolation — Pinnacle line gets extra weight when available
  4. Stale-line detection — flags books whose last_update is too old

Usage:
    engine = LiveFairValueEngine()
    result = engine.estimate(snapshot, game_state)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.game_state import GameState
from src.live_odds_poller import LiveOddsSnapshot, BookmakerLine, american_to_prob

logger = logging.getLogger(__name__)


@dataclass
class LiveFairValueResult:
    """Result of live fair value estimation."""
    home_prob: float
    away_prob: float
    method: str               # "live_consensus", "live_pinnacle", "live_ensemble"
    pinnacle_home: Optional[float] = None
    consensus_home: Optional[float] = None
    confidence: float = 1.0   # 0-1, higher = more reliable
    num_books: int = 0
    stale_books: int = 0      # Books with old last_update


class LiveFairValueEngine:
    """Computes live fair value from multi-book odds snapshots.

    Designed for in-game estimation where:
    - Speed matters (lightweight computation)
    - Game state affects confidence
    - Pinnacle is the strongest signal when available
    """

    def __init__(
        self,
        pinnacle_weight: float = 2.5,
        stale_threshold_seconds: float = 120.0,
        min_books: int = 2,
    ) -> None:
        self._pinnacle_weight = pinnacle_weight
        self._stale_threshold = stale_threshold_seconds
        self._min_books = min_books

    def estimate(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState] = None,
    ) -> Optional[LiveFairValueResult]:
        """Estimate fair home/away probabilities from live odds.

        Parameters
        ----------
        snapshot : LiveOddsSnapshot
            Current odds from all bookmakers
        game_state : GameState, optional
            Current game state for confidence adjustment

        Returns
        -------
        LiveFairValueResult or None if insufficient data
        """
        if not snapshot.bookmaker_lines:
            return None

        # Separate Pinnacle from the rest
        pinnacle_line: Optional[BookmakerLine] = None
        other_lines: list[BookmakerLine] = []
        stale_count = 0

        for bm in snapshot.bookmaker_lines:
            # Check staleness
            if self._is_stale(bm.last_update, snapshot.poll_timestamp):
                stale_count += 1
                continue  # Exclude stale lines from consensus

            if bm.bookmaker_key == "pinnacle":
                pinnacle_line = bm
            else:
                other_lines.append(bm)

        total_fresh = (1 if pinnacle_line else 0) + len(other_lines)
        if total_fresh < self._min_books:
            return None

        # Compute devigged probabilities for each book
        pinnacle_home = self._devig_home(pinnacle_line) if pinnacle_line else None
        consensus_home = self._weighted_consensus(pinnacle_line, other_lines)

        if consensus_home is None:
            return None

        # Choose method based on available data
        if pinnacle_home is not None and len(other_lines) >= 2:
            # Ensemble: blend Pinnacle (heavy weight) with consensus
            pin_w = 0.6
            con_w = 0.4
            home_prob = pinnacle_home * pin_w + consensus_home * con_w
            method = "live_ensemble"
        elif pinnacle_home is not None:
            home_prob = pinnacle_home
            method = "live_pinnacle"
        else:
            home_prob = consensus_home
            method = "live_consensus"

        home_prob = max(0.001, min(0.999, home_prob))

        # Confidence adjustment based on game state
        confidence = self._compute_confidence(
            num_books=total_fresh,
            has_pinnacle=(pinnacle_home is not None),
            game_state=game_state,
            stale_count=stale_count,
        )

        return LiveFairValueResult(
            home_prob=home_prob,
            away_prob=1.0 - home_prob,
            method=method,
            pinnacle_home=pinnacle_home,
            consensus_home=consensus_home,
            confidence=confidence,
            num_books=total_fresh,
            stale_books=stale_count,
        )

    def _weighted_consensus(
        self,
        pinnacle: Optional[BookmakerLine],
        others: list[BookmakerLine],
    ) -> Optional[float]:
        """Compute Pinnacle-weighted consensus home probability."""
        weighted_sum = 0.0
        total_weight = 0.0

        if pinnacle:
            home_p = self._devig_home(pinnacle)
            if home_p is not None:
                weighted_sum += home_p * self._pinnacle_weight
                total_weight += self._pinnacle_weight

        for bm in others:
            home_p = self._devig_home(bm)
            if home_p is not None:
                weighted_sum += home_p * 1.0
                total_weight += 1.0

        if total_weight <= 0:
            return None

        return weighted_sum / total_weight

    def _devig_home(self, line: BookmakerLine) -> Optional[float]:
        """Devig a bookmaker line to get fair home probability."""
        home_imp = american_to_prob(line.home_price)
        away_imp = american_to_prob(line.away_price)
        if home_imp is None or away_imp is None:
            return None
        total = home_imp + away_imp
        if total <= 0:
            return None
        return home_imp / total

    def _is_stale(self, last_update: str, poll_timestamp: str) -> bool:
        """Check if a bookmaker's line is too old."""
        if not last_update or not poll_timestamp:
            return False
        try:
            update_dt = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
            poll_dt = datetime.fromisoformat(poll_timestamp.replace("Z", "+00:00"))
            delta = (poll_dt - update_dt).total_seconds()
            return delta > self._stale_threshold
        except (ValueError, TypeError):
            return False

    def _compute_confidence(
        self,
        num_books: int,
        has_pinnacle: bool,
        game_state: Optional[GameState],
        stale_count: int,
    ) -> float:
        """Compute a confidence score (0-1) for the fair value estimate.

        Higher confidence when:
        - More books reporting (more information)
        - Pinnacle is available (sharpest line)
        - Game is further along (less variance)
        - Score differential is large (clearer outcome)
        - Fewer stale lines
        """
        confidence = 1.0

        # Book count: penalize when few books
        if num_books < 3:
            confidence *= 0.7
        elif num_books < 5:
            confidence *= 0.85

        # Pinnacle presence
        if not has_pinnacle:
            confidence *= 0.8

        # Stale lines reduce confidence
        if stale_count > 0:
            confidence *= max(0.7, 1.0 - stale_count * 0.05)

        # Game state adjustments
        if game_state:
            progress = game_state.game_progress_pct()
            # Later in the game → more confident (less variance remaining)
            # Progress 0.0 → no bonus, 1.0 → +15% confidence
            confidence *= (1.0 + progress * 0.15)

            # Blowouts are more predictable
            if not game_state.is_close_game:
                confidence *= 1.1

        return min(1.0, max(0.0, confidence))
