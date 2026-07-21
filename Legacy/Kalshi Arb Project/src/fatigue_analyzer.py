"""
src/fatigue_analyzer.py
───────────────────────
Schedule-adjusted fatigue analysis for NBA and NCAAB basketball markets.

Core thesis
───────────
Sportsbooks adjust lines for fatigue (back-to-backs, heavy schedules)
quickly because sharp bettors hammer stale lines.  Kalshi's thinner,
retail-heavy order books lack this corrective mechanism.  When the
sportsbook consensus already reflects a fatigue adjustment but Kalshi
hasn't caught up, the edge is more likely to be real.

This module does NOT change fair probabilities — the consensus already
prices in fatigue.  Instead it provides an **edge confidence multiplier**
that lowers the ``min_edge_pct`` and ``min_ev`` thresholds for
fatigue-flagged games: borderline edges (e.g. 2.5% vs a 3% threshold)
get through when fatigue gives us higher conviction the gap exists.

Schedule cache
──────────────
The Odds API only returns upcoming events — completed games disappear.
To detect back-to-backs we need to know when a team *last* played.
The ``schedule_cache.jsonl`` file persists every OddsEvent we observe.
On each scan cycle we append newly seen events; on startup we reload
the cache to build the historical lookback window.

Architecture
────────────
  FatigueContext     — per-team fatigue snapshot (frozen dataclass).
  MatchupFatigue     — home vs away fatigue comparison (frozen dataclass).
  FatigueAnalyzer    — stateful: owns the schedule cache and computes
                       fatigue metrics.  Injected into Scanner.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from logger_setup import get_logger

log = get_logger(__name__)


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FatigueContext:
    """Per-team fatigue snapshot for one upcoming game."""
    team: str
    rest_days: float                # days since last game (99.0 if unknown)
    games_in_last_4_days: int       # includes the game being evaluated
    games_in_last_7_days: int
    is_back_to_back: bool           # any game within b2b_threshold_hours
    is_second_of_b2b: bool          # this game IS the second leg
    fatigue_score: float            # 0.0 (fully rested) → 1.0 (maximum fatigue)


@dataclass(frozen=True)
class MatchupFatigue:
    """Comparison of home vs away fatigue for one game."""
    home_fatigue: FatigueContext
    away_fatigue: FatigueContext
    rest_differential: float        # home_rest - away_rest (positive = home fresher)
    edge_multiplier: float          # 1.0 = no signal, >1.0 = fatigue boosts conviction


# ── Fatigue score computation ─────────────────────────────────────────────────

def _compute_fatigue_score(ctx_fields: dict) -> float:
    """
    Composite fatigue score: 0.0 (fully rested) → 1.0 (maximum fatigue).

    Factors and approximate weights based on NBA rest-day research:
      - Back-to-back second leg:  biggest single factor (~3.5 pts ATS)
      - 3-in-4 nights:            moderate fatigue
      - 4-in-7 nights:            cumulative load
      - Extra rest (3+ days):     slight freshness bonus
    """
    score = 0.0

    if ctx_fields["is_second_of_b2b"]:
        score += 0.50
    if ctx_fields["games_in_last_4_days"] >= 3:
        score += 0.20
    if ctx_fields["games_in_last_7_days"] >= 4:
        score += 0.15
    if ctx_fields["rest_days"] >= 3:
        score -= 0.10

    return max(0.0, min(1.0, score))


# ── FatigueAnalyzer ──────────────────────────────────────────────────────────

class FatigueAnalyzer:
    """
    Schedule-aware fatigue analysis with JSONL schedule cache.

    Parameters
    ----------
    sports : list of str
        Sport keys this analyzer applies to (e.g. basketball_nba).
    schedule_cache_path : Path
        Where to persist observed OddsEvents as JSONL.
    b2b_multiplier : float
        Edge threshold multiplier when a back-to-back is detected.
    rest_diff_multiplier : float
        Edge threshold multiplier when rest differential ≥ min_rest_diff.
    min_rest_diff : float
        Minimum rest-day difference to trigger rest_diff_multiplier.
    b2b_threshold_hours : float
        Games within this many hours of each other count as back-to-back.
    _now_fn : callable, optional
        Returns current UTC datetime — injectable for tests.
    """

    def __init__(
        self,
        sports: List[str],
        schedule_cache_path: Path,
        b2b_multiplier: float = 1.5,
        rest_diff_multiplier: float = 1.3,
        min_rest_diff: float = 2.0,
        b2b_threshold_hours: float = 28.0,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.sports = frozenset(sports)
        self._cache_path = schedule_cache_path
        self._b2b_mult = b2b_multiplier
        self._rest_diff_mult = rest_diff_multiplier
        self._min_rest_diff = min_rest_diff
        self._b2b_hours = b2b_threshold_hours
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        self._seen_ids: Set[str] = set()
        self._cached_events: List[dict] = []
        self._load_cache()

    @classmethod
    def from_config(cls, config: dict, **kwargs) -> "FatigueAnalyzer":
        """Construct from a loaded config.yaml dict."""
        cfg = config.get("fatigue", {})
        return cls(
            sports=cfg.get("sports", ["basketball_nba", "basketball_ncaab"]),
            schedule_cache_path=Path(
                cfg.get("schedule_cache_path", "data/schedule_cache.jsonl")
            ),
            b2b_multiplier=cfg.get("b2b_multiplier", 1.5),
            rest_diff_multiplier=cfg.get("rest_diff_multiplier", 1.3),
            min_rest_diff=cfg.get("min_rest_diff", 2.0),
            b2b_threshold_hours=cfg.get("b2b_threshold_hours", 28.0),
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def update_cache(self, events: List[Any], sport: str) -> int:
        """
        Persist newly observed OddsEvents to the schedule cache.

        Parameters
        ----------
        events : list of OddsEvent
            Events returned by OddsClient.get_events().
        sport : str
            Sport key (used for logging only; the event already has sport_key).

        Returns
        -------
        Number of new events appended.
        """
        new_records: List[dict] = []
        for ev in events:
            eid = getattr(ev, "event_id", None)
            if not eid or eid in self._seen_ids:
                continue
            record = {
                "event_id": eid,
                "sport_key": getattr(ev, "sport_key", sport),
                "home_team": getattr(ev, "home_team", ""),
                "away_team": getattr(ev, "away_team", ""),
                "commence_time": getattr(ev, "commence_time", self._now()).isoformat(),
            }
            new_records.append(record)
            self._seen_ids.add(eid)
            self._cached_events.append(record)

        if new_records:
            self._append_to_cache(new_records)
            log.debug(
                "fatigue_cache_updated",
                sport=sport,
                new_events=len(new_records),
                total_cached=len(self._cached_events),
            )
        return len(new_records)

    def analyze_matchup(
        self,
        home_team: str,
        away_team: str,
        game_time: datetime,
        current_events: Optional[List[Any]] = None,
    ) -> MatchupFatigue:
        """
        Compute fatigue context for a home-vs-away matchup.

        Combines cached schedule history with any additional current_events
        to build the most complete picture of each team's recent workload.

        Parameters
        ----------
        home_team, away_team : str
            Team names as they appear in OddsEvent.
        game_time : datetime
            Scheduled start time of the game being evaluated.
        current_events : list of OddsEvent, optional
            Additional events from the current scan cycle (may overlap
            with cache — duplicates are handled).

        Returns
        -------
        MatchupFatigue with edge_multiplier.
        """
        # Merge cached + current events into a unified team→games map
        team_games = self._build_team_games(game_time, current_events)

        home_ctx = self._team_context(home_team, game_time, team_games)
        away_ctx = self._team_context(away_team, game_time, team_games)

        rest_diff = home_ctx.rest_days - away_ctx.rest_days
        multiplier = self._compute_multiplier(home_ctx, away_ctx)

        matchup = MatchupFatigue(
            home_fatigue=home_ctx,
            away_fatigue=away_ctx,
            rest_differential=round(rest_diff, 2),
            edge_multiplier=round(multiplier, 2),
        )

        if multiplier > 1.0:
            log.info(
                "fatigue_signal_detected",
                home=home_team,
                away=away_team,
                home_rest=round(home_ctx.rest_days, 1),
                away_rest=round(away_ctx.rest_days, 1),
                home_b2b=home_ctx.is_second_of_b2b,
                away_b2b=away_ctx.is_second_of_b2b,
                multiplier=round(multiplier, 2),
            )

        return matchup

    # ── Internal: schedule cache I/O ─────────────────────────────────────────

    def _load_cache(self) -> None:
        """Load existing schedule cache from JSONL file."""
        if not self._cache_path.exists():
            log.debug("fatigue_cache_empty", path=str(self._cache_path))
            return

        loaded = 0
        try:
            for line in self._cache_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("fatigue_cache_corrupt_line", line=line[:80])
                    continue
                eid = record.get("event_id")
                if eid and eid not in self._seen_ids:
                    self._seen_ids.add(eid)
                    self._cached_events.append(record)
                    loaded += 1
        except OSError as exc:
            log.warning("fatigue_cache_load_error", path=str(self._cache_path),
                        error=str(exc))
            return

        if loaded:
            log.info("fatigue_cache_loaded", events=loaded,
                     path=str(self._cache_path))

    def _append_to_cache(self, records: List[dict]) -> None:
        """Append new records to the schedule cache JSONL file."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._cache_path.open("a", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec) + "\n")
        except OSError as exc:
            log.error("fatigue_cache_write_error", path=str(self._cache_path),
                      error=str(exc))

    # ── Internal: team schedule building ─────────────────────────────────────

    def _build_team_games(
        self,
        reference_time: datetime,
        current_events: Optional[List[Any]] = None,
    ) -> Dict[str, List[datetime]]:
        """
        Build a mapping of team → sorted list of game times.

        Combines cached events (from JSONL) with current_events (from this
        scan cycle).  Only includes games within 14 days of reference_time
        to keep the dataset relevant.
        """
        cutoff = reference_time - timedelta(days=14)
        team_games: Dict[str, List[datetime]] = defaultdict(list)

        # Process cached events
        for rec in self._cached_events:
            ct_str = rec.get("commence_time", "")
            try:
                ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if ct < cutoff:
                continue
            home = rec.get("home_team", "")
            away = rec.get("away_team", "")
            if home:
                team_games[home].append(ct)
            if away:
                team_games[away].append(ct)

        # Process current events (may overlap with cache — that's fine,
        # duplicates in the time list don't affect rest-day calculations
        # because we take min/max/count)
        seen_times: Dict[str, Set[str]] = defaultdict(set)
        for ev in (current_events or []):
            ct = getattr(ev, "commence_time", None)
            if ct is None or ct < cutoff:
                continue
            home = getattr(ev, "home_team", "")
            away = getattr(ev, "away_team", "")
            ct_key = ct.isoformat()
            if home and ct_key not in seen_times.get(home, set()):
                team_games[home].append(ct)
                seen_times[home].add(ct_key)
            if away and ct_key not in seen_times.get(away, set()):
                team_games[away].append(ct)
                seen_times[away].add(ct_key)

        # Sort each team's games chronologically
        for team in team_games:
            team_games[team] = sorted(set(team_games[team]))

        return dict(team_games)

    def _team_context(
        self,
        team: str,
        game_time: datetime,
        team_games: Dict[str, List[datetime]],
    ) -> FatigueContext:
        """
        Compute FatigueContext for one team relative to a specific game time.

        If the team has no schedule data, returns a neutral context
        (rest_days=99, no fatigue flags).
        """
        games = team_games.get(team, [])

        if not games:
            return FatigueContext(
                team=team,
                rest_days=99.0,
                games_in_last_4_days=0,
                games_in_last_7_days=0,
                is_back_to_back=False,
                is_second_of_b2b=False,
                fatigue_score=0.0,
            )

        # Games before this game_time (past or same-day earlier)
        prior_games = [g for g in games if g < game_time]
        # Games within windows (counting relative to game_time)
        four_days_ago = game_time - timedelta(days=4)
        seven_days_ago = game_time - timedelta(days=7)
        b2b_cutoff = game_time - timedelta(hours=self._b2b_hours)

        games_in_4 = sum(1 for g in games if four_days_ago <= g < game_time)
        games_in_7 = sum(1 for g in games if seven_days_ago <= g < game_time)

        if prior_games:
            last_game = prior_games[-1]
            rest_hours = (game_time - last_game).total_seconds() / 3600.0
            rest_days = rest_hours / 24.0
            is_b2b = rest_hours < self._b2b_hours
            is_second_of_b2b = is_b2b
        else:
            rest_days = 99.0
            is_b2b = False
            is_second_of_b2b = False

        fields = {
            "is_second_of_b2b": is_second_of_b2b,
            "games_in_last_4_days": games_in_4,
            "games_in_last_7_days": games_in_7,
            "rest_days": rest_days,
        }
        fatigue_score = _compute_fatigue_score(fields)

        return FatigueContext(
            team=team,
            rest_days=round(rest_days, 2),
            games_in_last_4_days=games_in_4,
            games_in_last_7_days=games_in_7,
            is_back_to_back=is_b2b,
            is_second_of_b2b=is_second_of_b2b,
            fatigue_score=round(fatigue_score, 3),
        )

    # ── Internal: multiplier computation ─────────────────────────────────────

    def _compute_multiplier(
        self,
        home_ctx: FatigueContext,
        away_ctx: FatigueContext,
    ) -> float:
        """
        Compute edge confidence multiplier based on fatigue signals.

        The multiplier is used to lower min_edge_pct and min_ev thresholds
        for fatigue-flagged games, NOT to adjust fair probabilities.

        Returns
        -------
        float ≥ 1.0.
          1.0  — no fatigue signal
          b2b_multiplier — back-to-back detected (either team)
          rest_diff_multiplier — significant rest differential
        """
        # B2B is the strongest signal — check first
        if home_ctx.is_second_of_b2b or away_ctx.is_second_of_b2b:
            return self._b2b_mult

        # Rest differential: meaningful when one team is much fresher
        rest_diff = abs(home_ctx.rest_days - away_ctx.rest_days)
        if rest_diff >= self._min_rest_diff:
            return self._rest_diff_mult

        return 1.0
