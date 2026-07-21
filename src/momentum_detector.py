"""
src/momentum_detector.py
────────────────────────
Detects overreaction in Kalshi live markets relative to sharp consensus.

The core thesis: when a scoring event occurs (NBA run, MLB early lead),
retail-heavy Kalshi markets overreact — moving price further/faster than
the sportsbook consensus justifies.  This creates a contrarian signal.

Signals:
  - OVERREACTION: Kalshi price moved more than consensus after a game event
  - DIVERGENCE:   Kalshi price has drifted far from consensus over time

Usage:
    detector = MomentumDetector()
    detector.record_snapshot(game_id, kalshi_prob, consensus_prob, timestamp)
    signals = detector.detect(game_id)
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum snapshots to keep per game (prevents unbounded memory)
MAX_SNAPSHOTS_PER_GAME = 200


@dataclass
class PriceSnapshot:
    """One point-in-time observation of Kalshi vs consensus."""
    timestamp: str
    kalshi_home_prob: float
    consensus_home_prob: float
    home_score: int = 0
    away_score: int = 0


@dataclass
class MomentumSignal:
    """A detected overreaction or divergence signal."""
    signal_type: str          # "OVERREACTION" or "DIVERGENCE"
    game_id: str
    direction: str            # "buy_home" or "buy_away" — contrarian direction
    kalshi_home_prob: float   # Current Kalshi implied home probability
    consensus_home_prob: float  # Current consensus fair value
    divergence: float         # Kalshi - consensus (positive = Kalshi overvalues home)
    overreaction_score: float  # Magnitude of overreaction (higher = stronger signal)
    lookback_seconds: float   # How far back the comparison window extends
    details: Dict = field(default_factory=dict)


class MomentumDetector:
    """Detects when Kalshi's retail market overreacts to game events.

    Maintains a time-series of (Kalshi price, consensus price) snapshots
    per game and detects when Kalshi diverges from consensus more than
    expected.

    Two detection modes:
    1. OVERREACTION — compares rate of change: if Kalshi moved 5% in the
       last 60s but consensus only moved 2%, overreaction score = 3%.
    2. DIVERGENCE — compares absolute levels: if Kalshi says 70% but
       consensus says 60%, divergence = 10%.
    """

    def __init__(
        self,
        overreaction_threshold: float = 0.03,   # 3% differential
        divergence_threshold: float = 0.05,      # 5% absolute gap
        lookback_seconds: float = 120.0,         # 2-minute comparison window
        min_snapshots: int = 3,                  # Minimum data points needed
    ) -> None:
        self._overreaction_threshold = overreaction_threshold
        self._divergence_threshold = divergence_threshold
        self._lookback_seconds = lookback_seconds
        self._min_snapshots = min_snapshots

        # game_id → deque of PriceSnapshot (most recent last)
        self._history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=MAX_SNAPSHOTS_PER_GAME)
        )

    def record_snapshot(
        self,
        game_id: str,
        kalshi_home_prob: float,
        consensus_home_prob: float,
        timestamp: str,
        home_score: int = 0,
        away_score: int = 0,
    ) -> None:
        """Record a new price observation for a game."""
        self._history[game_id].append(PriceSnapshot(
            timestamp=timestamp,
            kalshi_home_prob=kalshi_home_prob,
            consensus_home_prob=consensus_home_prob,
            home_score=home_score,
            away_score=away_score,
        ))

    def detect(self, game_id: str) -> List[MomentumSignal]:
        """Run all detection strategies for a game.

        Returns list of signals (may be empty).
        """
        history = self._history.get(game_id)
        if not history or len(history) < self._min_snapshots:
            return []

        signals: List[MomentumSignal] = []

        overreaction = self._detect_overreaction(game_id, history)
        if overreaction:
            signals.append(overreaction)

        divergence = self._detect_divergence(game_id, history)
        if divergence:
            signals.append(divergence)

        return signals

    def detect_all(self) -> Dict[str, List[MomentumSignal]]:
        """Run detection across all tracked games."""
        results = {}
        for game_id in list(self._history.keys()):
            signals = self.detect(game_id)
            if signals:
                results[game_id] = signals
        return results

    def get_history(self, game_id: str) -> List[PriceSnapshot]:
        """Get recorded history for a game."""
        return list(self._history.get(game_id, []))

    def clear_game(self, game_id: str) -> None:
        """Remove history for a completed game."""
        self._history.pop(game_id, None)

    @property
    def tracked_games(self) -> int:
        return len(self._history)

    # ── Detection strategies ──────────────────────────────────────────

    def _detect_overreaction(
        self, game_id: str, history: deque
    ) -> Optional[MomentumSignal]:
        """Detect if Kalshi moved more than consensus in the lookback window.

        Compares the delta (change) in Kalshi price vs consensus price
        over the lookback window.  If Kalshi moved significantly more,
        the excess is the overreaction score.
        """
        current = history[-1]
        reference = self._find_reference_snapshot(history)
        if reference is None:
            return None

        # Compute deltas
        kalshi_delta = current.kalshi_home_prob - reference.kalshi_home_prob
        consensus_delta = current.consensus_home_prob - reference.consensus_home_prob

        # Overreaction = how much more Kalshi moved than consensus
        # Signed: positive means Kalshi overvalues home relative to consensus movement
        overreaction = kalshi_delta - consensus_delta

        if abs(overreaction) < self._overreaction_threshold:
            return None

        # Contrarian direction: bet against the overreaction
        if overreaction > 0:
            direction = "buy_away"  # Kalshi moved too much toward home → buy away
        else:
            direction = "buy_home"  # Kalshi moved too much toward away → buy home

        lookback = self._time_delta_seconds(reference.timestamp, current.timestamp)

        return MomentumSignal(
            signal_type="OVERREACTION",
            game_id=game_id,
            direction=direction,
            kalshi_home_prob=current.kalshi_home_prob,
            consensus_home_prob=current.consensus_home_prob,
            divergence=current.kalshi_home_prob - current.consensus_home_prob,
            overreaction_score=abs(overreaction),
            lookback_seconds=lookback,
            details={
                "kalshi_delta": kalshi_delta,
                "consensus_delta": consensus_delta,
                "ref_kalshi": reference.kalshi_home_prob,
                "ref_consensus": reference.consensus_home_prob,
                "score_change": (
                    current.home_score != reference.home_score or
                    current.away_score != reference.away_score
                ),
            },
        )

    def _detect_divergence(
        self, game_id: str, history: deque
    ) -> Optional[MomentumSignal]:
        """Detect if Kalshi has diverged far from consensus in absolute terms.

        This catches slower-building mispricings that don't show up as
        sudden overreactions — e.g. Kalshi gradually drifting 8% from
        consensus over 10 minutes.
        """
        current = history[-1]
        divergence = current.kalshi_home_prob - current.consensus_home_prob

        if abs(divergence) < self._divergence_threshold:
            return None

        if divergence > 0:
            direction = "buy_away"  # Kalshi overvalues home → buy away
        else:
            direction = "buy_home"  # Kalshi undervalues home → buy home

        # Check if divergence is persistent (not just a momentary spike)
        # Average divergence over last N snapshots
        recent = list(history)[-min(len(history), 5):]
        avg_div = sum(
            s.kalshi_home_prob - s.consensus_home_prob for s in recent
        ) / len(recent)

        # Only signal if average divergence is in the same direction
        if abs(avg_div) < self._divergence_threshold * 0.5:
            return None

        return MomentumSignal(
            signal_type="DIVERGENCE",
            game_id=game_id,
            direction=direction,
            kalshi_home_prob=current.kalshi_home_prob,
            consensus_home_prob=current.consensus_home_prob,
            divergence=divergence,
            overreaction_score=abs(divergence),
            lookback_seconds=0,
            details={
                "avg_divergence": avg_div,
                "snapshot_count": len(recent),
                "persistent": abs(avg_div) >= self._divergence_threshold * 0.5,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────

    def _find_reference_snapshot(
        self, history: deque
    ) -> Optional[PriceSnapshot]:
        """Find the snapshot approximately `lookback_seconds` ago."""
        if len(history) < 2:
            return None

        current = history[-1]
        target_delta = self._lookback_seconds

        best: Optional[PriceSnapshot] = None
        best_delta_diff = float("inf")

        for snap in history:
            if snap is current:
                continue
            delta = self._time_delta_seconds(snap.timestamp, current.timestamp)
            diff = abs(delta - target_delta)
            if diff < best_delta_diff:
                best_delta_diff = diff
                best = snap

        # Require the reference to be at least 10 seconds old
        if best:
            actual_delta = self._time_delta_seconds(best.timestamp, current.timestamp)
            if actual_delta < 10:
                return None

        return best

    def _time_delta_seconds(self, ts1: str, ts2: str) -> float:
        """Compute time delta in seconds between two ISO timestamps."""
        try:
            dt1 = datetime.fromisoformat(ts1.replace("Z", "+00:00"))
            dt2 = datetime.fromisoformat(ts2.replace("Z", "+00:00"))
            return abs((dt2 - dt1).total_seconds())
        except (ValueError, TypeError):
            return 0.0
