"""
src/elo_model.py
────────────────
Simple Elo rating system for NBA and NHL.

Maintains per-team Elo ratings that update after each game result.
Can produce win probabilities for matchups, which serve as an
independent fair-value signal alongside sharp-book consensus.

Ratings persist to a JSON file between restarts so the model
accumulates knowledge over the season.

Usage:
    model = EloModel.from_config(config)
    prob_home = model.predict(home_team="Boston Celtics", away_team="Miami Heat", sport="basketball_nba")
    model.update(home_team="Boston Celtics", away_team="Miami Heat", home_won=True, sport="basketball_nba")
    model.save()
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Sport-specific home advantages (in Elo points).
# Sources: historical closing-line analysis.
# NBA home court ~100 pts; NHL home ice ~30 pts; NFL home field ~50 pts;
# College sports ~60 pts; Tennis N/A (no home advantage).
_SPORT_HOME_ADVANTAGE: Dict[str, float] = {
    "basketball_nba": 100.0,
    "icehockey_nhl": 33.0,
    "americanfootball_nfl": 48.0,
    "basketball_ncaab": 65.0,
    "basketball_ncaaw": 65.0,
    "baseball_mlb": 24.0,
    # Tennis: no home advantage concept — handled via default 0
}

# Sport-specific K-factors.  Higher = more reactive to recent results.
# NBA/NHL mid-season: K=20 is standard.  Tennis: K=32 (more volatile).
_SPORT_K_FACTOR: Dict[str, float] = {
    "basketball_nba": 20.0,
    "icehockey_nhl": 20.0,
    "americanfootball_nfl": 20.0,
    "basketball_ncaab": 24.0,
    "basketball_ncaaw": 24.0,
    "baseball_mlb": 12.0,  # Lower K for baseball (162 game season, less variance per game)
}

DEFAULT_RATING = 1500.0
DEFAULT_K_FACTOR = 20.0


class EloModel:
    """Simple Elo rating model for sports betting fair value estimation."""

    def __init__(
        self,
        ratings: Optional[Dict[str, Dict[str, float]]] = None,
        k_factor: float = DEFAULT_K_FACTOR,
        home_advantage: float = 60.0,
        default_rating: float = DEFAULT_RATING,
        ratings_file: Optional[Path] = None,
    ) -> None:
        # ratings: {sport_key: {team_name: rating}}
        self._ratings: Dict[str, Dict[str, float]] = ratings or {}
        self._k_factor = k_factor
        self._home_advantage = home_advantage
        self._default_rating = default_rating
        self._ratings_file = ratings_file
        self._dirty = False  # Track if ratings have changed since last save

    @classmethod
    def from_config(cls, config: dict) -> "EloModel":
        """Construct from loaded config_multi_pod.yaml dict."""
        p006_cfg = config.get("pods", {}).get("P-006", {})
        fv_cfg = p006_cfg.get("fair_value", {})
        elo_cfg = fv_cfg.get("elo", {})

        ratings_path = elo_cfg.get("ratings_file", "data/elo_ratings.json")
        ratings_file = Path(ratings_path)

        # Load existing ratings from disk
        ratings: Dict[str, Dict[str, float]] = {}
        if ratings_file.exists():
            try:
                with open(ratings_file) as f:
                    data = json.load(f)
                ratings = data.get("ratings", {})
                logger.info(
                    "Elo: loaded ratings from %s — %d sports, %d teams",
                    ratings_file,
                    len(ratings),
                    sum(len(v) for v in ratings.values()),
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Elo: failed to load %s: %s — starting fresh", ratings_file, e)

        return cls(
            ratings=ratings,
            k_factor=elo_cfg.get("k_factor", DEFAULT_K_FACTOR),
            home_advantage=elo_cfg.get("home_advantage", 60.0),
            default_rating=elo_cfg.get("default_rating", DEFAULT_RATING),
            ratings_file=ratings_file,
        )

    def _get_rating(self, team: str, sport: str) -> float:
        """Get team's current Elo rating, initializing if needed."""
        sport_ratings = self._ratings.setdefault(sport, {})
        return sport_ratings.get(team, self._default_rating)

    def _set_rating(self, team: str, sport: str, rating: float) -> None:
        """Update team's Elo rating."""
        self._ratings.setdefault(sport, {})[team] = round(rating, 1)
        self._dirty = True

    def predict(
        self,
        home_team: str,
        away_team: str,
        sport: str,
        neutral_site: bool = False,
    ) -> float:
        """
        Predict home team win probability.

        Parameters
        ----------
        home_team : str
            Home team name (must match Odds API naming).
        away_team : str
            Away team name.
        sport : str
            Sport key (e.g. "basketball_nba").
        neutral_site : bool
            If True, no home advantage applied.

        Returns
        -------
        float
            Home team win probability (0-1).
        """
        home_elo = self._get_rating(home_team, sport)
        away_elo = self._get_rating(away_team, sport)

        # Apply home advantage
        ha = 0.0 if neutral_site else _SPORT_HOME_ADVANTAGE.get(sport, self._home_advantage)

        # Standard Elo expected score formula
        diff = (home_elo + ha) - away_elo
        prob = 1.0 / (1.0 + math.pow(10, -diff / 400.0))

        return max(0.001, min(0.999, prob))

    def update(
        self,
        home_team: str,
        away_team: str,
        home_won: bool,
        sport: str,
        neutral_site: bool = False,
    ) -> Tuple[float, float]:
        """
        Update Elo ratings after a game result.

        Parameters
        ----------
        home_team, away_team : str
            Team names.
        home_won : bool
            True if home team won.
        sport : str
            Sport key.

        Returns
        -------
        Tuple of (new_home_rating, new_away_rating).
        """
        home_elo = self._get_rating(home_team, sport)
        away_elo = self._get_rating(away_team, sport)

        # Expected scores
        ha = 0.0 if neutral_site else _SPORT_HOME_ADVANTAGE.get(sport, self._home_advantage)
        expected_home = 1.0 / (1.0 + math.pow(10, -(home_elo + ha - away_elo) / 400.0))
        expected_away = 1.0 - expected_home

        # Actual scores
        actual_home = 1.0 if home_won else 0.0
        actual_away = 1.0 - actual_home

        # K-factor (sport-specific)
        k = _SPORT_K_FACTOR.get(sport, self._k_factor)

        # Update
        new_home = home_elo + k * (actual_home - expected_home)
        new_away = away_elo + k * (actual_away - expected_away)

        self._set_rating(home_team, sport, new_home)
        self._set_rating(away_team, sport, new_away)

        logger.debug(
            "Elo: %s %s(%.0f→%.0f) vs %s(%.0f→%.0f) winner=%s",
            sport, home_team, home_elo, new_home,
            away_team, away_elo, new_away,
            "home" if home_won else "away",
        )

        return new_home, new_away

    def has_ratings(self, sport: str) -> bool:
        """Check if we have any Elo ratings for a sport."""
        return sport in self._ratings and len(self._ratings[sport]) > 0

    def team_count(self, sport: str) -> int:
        """Number of teams with ratings in a sport."""
        return len(self._ratings.get(sport, {}))

    def save(self) -> None:
        """Persist ratings to JSON file."""
        if not self._dirty or not self._ratings_file:
            return

        self._ratings_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "ratings": self._ratings,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "total_teams": sum(len(v) for v in self._ratings.values()),
        }
        with open(self._ratings_file, "w") as f:
            json.dump(data, f, indent=2)

        self._dirty = False
        logger.info("Elo: saved ratings to %s", self._ratings_file)

    def get_all_ratings(self, sport: str) -> Dict[str, float]:
        """Get all team ratings for a sport (for display/debugging)."""
        return dict(self._ratings.get(sport, {}))
