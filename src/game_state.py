"""
src/game_state.py
─────────────────
Tracks live game state (scores, periods, time remaining) for P-014.

Polls Odds API /scores endpoint and maintains an in-memory model of
each active game.  Emits "game events" when state changes (score change,
period change, game start/end) that the MomentumDetector can consume.

Usage:
    tracker = GameStateTracker.from_config(config)
    games = tracker.update("basketball_nba")  # poll + return active games
    events = tracker.get_pending_events()      # consume state-change events
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import requests

from src.constants import HTTP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"


class GamePhase(str, Enum):
    """Lifecycle phase of a game."""
    PRE_GAME = "pre_game"
    LIVE = "live"
    HALFTIME = "halftime"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class GameEventType(str, Enum):
    """Types of game state changes."""
    GAME_START = "game_start"
    SCORE_CHANGE = "score_change"
    PERIOD_CHANGE = "period_change"
    HALFTIME = "halftime"
    GAME_END = "game_end"


@dataclass
class GameState:
    """Current state of one live game."""
    game_id: str
    sport: str
    home_team: str
    away_team: str
    home_score: int = 0
    away_score: int = 0
    period: Optional[int] = None           # quarter (NBA) or inning (MLB)
    period_label: str = ""                 # "Q1", "Q2", "Top 5th", etc.
    phase: GamePhase = GamePhase.UNKNOWN
    commence_time: str = ""
    last_update: str = ""
    completed: bool = False

    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score

    @property
    def score_diff(self) -> int:
        """Positive = home leading, negative = away leading."""
        return self.home_score - self.away_score

    @property
    def is_close_game(self) -> bool:
        """Is the game within 1 score?  NBA: <8 pts, MLB: <3 runs."""
        if "nba" in self.sport:
            return abs(self.score_diff) <= 8
        elif "mlb" in self.sport:
            return abs(self.score_diff) <= 2
        return abs(self.score_diff) <= 5

    def game_progress_pct(self, _now: Optional[datetime] = None) -> float:
        """Estimated game progress as 0-1 float.

        Primary: period-based (NBA quarter, MLB inning).
        Fallback: elapsed-time-based when period data is unavailable.
        Typical durations: NBA ~2.5h, MLB ~3h, NHL ~2.5h.
        """
        # Period-based (preferred)
        if self.period is not None and self.period >= 1:
            if "nba" in self.sport:
                return min(1.0, self.period / 4.0)
            elif "mlb" in self.sport:
                return min(1.0, self.period / 9.0)
            return min(1.0, self.period / 4.0)

        # Elapsed-time fallback: estimate from commence_time
        if self.commence_time and self.phase == GamePhase.LIVE:
            try:
                ct = datetime.fromisoformat(
                    self.commence_time.replace("Z", "+00:00")
                )
                now = _now or datetime.now(timezone.utc)
                elapsed_minutes = (now - ct).total_seconds() / 60.0
                if elapsed_minutes < 0:
                    return 0.0
                # Typical game durations in minutes
                if "nba" in self.sport:
                    typical_duration = 150.0   # 2.5 hours
                elif "mlb" in self.sport:
                    typical_duration = 180.0   # 3 hours
                elif "nhl" in self.sport:
                    typical_duration = 150.0   # 2.5 hours
                else:
                    typical_duration = 150.0   # conservative default
                return min(0.95, elapsed_minutes / typical_duration)
            except (ValueError, TypeError):
                pass

        return 0.0


@dataclass
class GameEvent:
    """A state-change event emitted by the tracker."""
    event_type: GameEventType
    game_id: str
    sport: str
    home_team: str
    away_team: str
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)


class GameStateTracker:
    """Tracks live game state by polling Odds API /scores.

    Maintains a dict of GameState per active game and emits GameEvent
    objects when state changes are detected.
    """

    def __init__(
        self,
        api_key: str,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        _session: Optional[requests.Session] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._session = _session or requests.Session()
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))

        # game_id → GameState
        self._games: Dict[str, GameState] = {}

        # Pending events (consumed by get_pending_events)
        self._pending_events: List[GameEvent] = []

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "GameStateTracker":
        """Build from config_multi_pod.yaml."""
        api_key = overrides.get("api_key") or os.environ.get("ODDS_API_KEY", "")
        if not api_key:
            raise ValueError("ODDS_API_KEY required for GameStateTracker")

        p014_cfg = config.get("pods", {}).get("P-014", {})
        scan_cfg = p014_cfg.get("scan", {})

        return cls(
            api_key=api_key,
            timeout=scan_cfg.get("timeout", HTTP_TIMEOUT_SECONDS),
            _session=overrides.get("_session"),
            _now_fn=overrides.get("_now_fn"),
        )

    def update(self, sport: str) -> List[GameState]:
        """Poll scores for a sport and return all active (live) games.

        Side effect: emits GameEvents for any state changes detected.
        """
        raw_scores = self._fetch_scores(sport)
        if raw_scores is None:
            return list(self._live_games(sport))

        now_str = self._now_fn().isoformat()

        for event in raw_scores:
            game_id = event.get("id", "")
            if not game_id:
                continue

            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            completed = event.get("completed", False)
            scores = event.get("scores")
            commence_time = event.get("commence_time", "")

            # Parse scores
            home_score = 0
            away_score = 0
            if scores and isinstance(scores, list):
                for s in scores:
                    name = s.get("name", "")
                    score_val = s.get("score")
                    if score_val is None:
                        continue
                    try:
                        score_int = int(score_val)
                    except (ValueError, TypeError):
                        continue
                    if name == home_team:
                        home_score = score_int
                    elif name == away_team:
                        away_score = score_int

            # Determine phase
            if completed:
                phase = GamePhase.COMPLETED
            elif scores is not None:
                phase = GamePhase.LIVE
            else:
                # No scores yet → check if commenced
                try:
                    ct = datetime.fromisoformat(
                        commence_time.replace("Z", "+00:00")
                    )
                    if ct <= self._now_fn():
                        phase = GamePhase.LIVE  # started but no scores yet
                    else:
                        phase = GamePhase.PRE_GAME
                except (ValueError, TypeError):
                    phase = GamePhase.UNKNOWN

            # Build new state
            new_state = GameState(
                game_id=game_id,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                phase=phase,
                commence_time=commence_time,
                last_update=now_str,
                completed=completed,
            )

            # Detect events by comparing to previous state
            old_state = self._games.get(game_id)
            self._detect_events(old_state, new_state, now_str)

            # Update cache
            self._games[game_id] = new_state

        return list(self._live_games(sport))

    def get_game(self, game_id: str) -> Optional[GameState]:
        """Get current state for a specific game."""
        return self._games.get(game_id)

    def get_live_games(self, sport: Optional[str] = None) -> List[GameState]:
        """Get all currently live games, optionally filtered by sport."""
        return list(self._live_games(sport))

    def get_pending_events(self) -> List[GameEvent]:
        """Consume and return all pending game events.

        Events are cleared after retrieval (consume-once pattern).
        """
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def is_game_live(self, game_id: str) -> bool:
        """Check if a game is currently in-play."""
        state = self._games.get(game_id)
        if state is None:
            return False
        return state.phase == GamePhase.LIVE

    # ── Internal helpers ───────────────────────────────────────────────

    def _live_games(self, sport: Optional[str] = None):
        """Generator yielding live GameState objects."""
        for gs in self._games.values():
            if gs.phase != GamePhase.LIVE:
                continue
            if sport and gs.sport != sport:
                continue
            yield gs

    def _detect_events(
        self,
        old: Optional[GameState],
        new: GameState,
        timestamp: str,
    ) -> None:
        """Compare old and new state, emit GameEvents for changes."""
        base_kwargs = {
            "game_id": new.game_id,
            "sport": new.sport,
            "home_team": new.home_team,
            "away_team": new.away_team,
            "timestamp": timestamp,
        }

        if old is None:
            # New game appearing as live
            if new.phase == GamePhase.LIVE:
                self._pending_events.append(GameEvent(
                    event_type=GameEventType.GAME_START,
                    details={
                        "home_score": new.home_score,
                        "away_score": new.away_score,
                    },
                    **base_kwargs,
                ))
            return

        # Game just ended
        if old.phase == GamePhase.LIVE and new.phase == GamePhase.COMPLETED:
            self._pending_events.append(GameEvent(
                event_type=GameEventType.GAME_END,
                details={
                    "final_home_score": new.home_score,
                    "final_away_score": new.away_score,
                    "winner": new.home_team if new.home_score > new.away_score else new.away_team,
                },
                **base_kwargs,
            ))
            return

        # Score change
        if (new.home_score != old.home_score or
                new.away_score != old.away_score):
            home_delta = new.home_score - old.home_score
            away_delta = new.away_score - old.away_score
            self._pending_events.append(GameEvent(
                event_type=GameEventType.SCORE_CHANGE,
                details={
                    "home_score": new.home_score,
                    "away_score": new.away_score,
                    "home_delta": home_delta,
                    "away_delta": away_delta,
                    "old_home_score": old.home_score,
                    "old_away_score": old.away_score,
                    "scoring_team": (
                        new.home_team if home_delta > away_delta
                        else new.away_team
                    ),
                },
                **base_kwargs,
            ))

    def _fetch_scores(self, sport: str) -> Optional[list]:
        """Fetch live scores from Odds API."""
        url = f"{ODDS_API_BASE}/sports/{sport}/scores"
        params = {
            "apiKey": self._api_key,
            "daysFrom": 1,
        }

        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
            if resp.status_code != 200:
                logger.warning(
                    "GameStateTracker: HTTP %d for %s scores",
                    resp.status_code, sport,
                )
                return None
            data = resp.json()
            if not isinstance(data, list):
                return None
            return data
        except requests.RequestException as exc:
            logger.error(
                "GameStateTracker: scores request failed for %s: %s",
                sport, exc,
            )
            return None
