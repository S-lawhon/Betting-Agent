"""
tests/test_fatigue_analyzer.py
──────────────────────────────
Unit tests for the schedule-adjusted fatigue analysis module.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from src.fatigue_analyzer import (
    FatigueAnalyzer,
    FatigueContext,
    MatchupFatigue,
    _compute_fatigue_score,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 2, 20, 20, 0, 0, tzinfo=timezone.utc)  # Friday 8 PM UTC


class _FakeEvent:
    """Minimal stand-in for OddsEvent with just the fields FatigueAnalyzer reads."""

    def __init__(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        commence_time: datetime,
        sport_key: str = "basketball_nba",
    ):
        self.event_id = event_id
        self.home_team = home_team
        self.away_team = away_team
        self.commence_time = commence_time
        self.sport_key = sport_key


def _make_analyzer(
    cache_path: Optional[Path] = None,
    b2b_mult: float = 1.5,
    rest_diff_mult: float = 1.3,
    min_rest_diff: float = 2.0,
    b2b_hours: float = 28.0,
    now: datetime = _NOW,
) -> FatigueAnalyzer:
    """Build analyzer with injectable clock and temp cache path."""
    return FatigueAnalyzer(
        sports=["basketball_nba", "basketball_ncaab"],
        schedule_cache_path=cache_path or Path(tempfile.mktemp(suffix=".jsonl")),
        b2b_multiplier=b2b_mult,
        rest_diff_multiplier=rest_diff_mult,
        min_rest_diff=min_rest_diff,
        b2b_threshold_hours=b2b_hours,
        _now_fn=lambda: now,
    )


# ── Back-to-back detection ───────────────────────────────────────────────────

class TestBackToBackDetection(unittest.TestCase):
    """Verify B2B flag logic with various game spacings."""

    def test_games_24h_apart_is_b2b(self):
        """Games 24 hours apart should be flagged as back-to-back."""
        game_time = _NOW
        yesterday = _NOW - timedelta(hours=24)

        events = [
            _FakeEvent("g1", "Lakers", "Celtics", yesterday),
            _FakeEvent("g2", "Lakers", "Knicks", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Knicks", game_time, events)
        assert matchup.home_fatigue.is_back_to_back is True
        assert matchup.home_fatigue.is_second_of_b2b is True

    def test_games_30h_apart_not_b2b(self):
        """Games 30 hours apart exceed the 28h threshold — not B2B."""
        game_time = _NOW
        prev = _NOW - timedelta(hours=30)

        events = [
            _FakeEvent("g1", "Lakers", "Celtics", prev),
            _FakeEvent("g2", "Lakers", "Knicks", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Knicks", game_time, events)
        assert matchup.home_fatigue.is_back_to_back is False
        assert matchup.home_fatigue.is_second_of_b2b is False

    def test_games_exactly_at_threshold(self):
        """Games exactly 28h apart — should NOT be B2B (< threshold, not <=)."""
        game_time = _NOW
        prev = _NOW - timedelta(hours=28)

        events = [
            _FakeEvent("g1", "Lakers", "Celtics", prev),
            _FakeEvent("g2", "Lakers", "Knicks", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Knicks", game_time, events)
        # Exactly 28h → rest_hours == 28.0, which is NOT < 28.0
        assert matchup.home_fatigue.is_back_to_back is False

    def test_games_27_9h_apart_is_b2b(self):
        """Games 27.9h apart — just under threshold, should be B2B."""
        game_time = _NOW
        prev = _NOW - timedelta(hours=27, minutes=54)

        events = [
            _FakeEvent("g1", "Lakers", "Celtics", prev),
            _FakeEvent("g2", "Lakers", "Knicks", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Knicks", game_time, events)
        assert matchup.home_fatigue.is_back_to_back is True

    def test_away_team_b2b(self):
        """Away team can also be flagged as B2B."""
        game_time = _NOW
        yesterday = _NOW - timedelta(hours=22)

        events = [
            _FakeEvent("g1", "Celtics", "Knicks", yesterday),  # Knicks played away
            _FakeEvent("g2", "Lakers", "Knicks", game_time),   # Knicks away again
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Knicks", game_time, events)
        assert matchup.away_fatigue.is_second_of_b2b is True
        assert matchup.home_fatigue.is_second_of_b2b is False


# ── Games-in-window detection ────────────────────────────────────────────────

class TestGamesInWindow(unittest.TestCase):
    """Verify counting of recent games within 4-day and 7-day windows."""

    def test_three_in_four_days(self):
        """Three games in 4 days should be detected."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "Celtics", _NOW - timedelta(days=3)),
            _FakeEvent("g2", "Lakers", "Knicks", _NOW - timedelta(days=2)),
            _FakeEvent("g3", "Lakers", "Heat", _NOW - timedelta(days=1)),
            _FakeEvent("g4", "Lakers", "Nets", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Nets", game_time, events)
        assert matchup.home_fatigue.games_in_last_4_days == 3

    def test_four_in_seven_days(self):
        """Four games in 7 days should be counted."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "A", _NOW - timedelta(days=6)),
            _FakeEvent("g2", "Lakers", "B", _NOW - timedelta(days=4)),
            _FakeEvent("g3", "Lakers", "C", _NOW - timedelta(days=2)),
            _FakeEvent("g4", "Lakers", "D", _NOW - timedelta(days=1)),
            _FakeEvent("g5", "Lakers", "E", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "E", game_time, events)
        assert matchup.home_fatigue.games_in_last_7_days == 4

    def test_no_recent_games(self):
        """Team with no games in recent history gets zero counts."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "Nets", game_time),
        ]
        analyzer = _make_analyzer()

        matchup = analyzer.analyze_matchup("Lakers", "Nets", game_time, events)
        assert matchup.home_fatigue.games_in_last_4_days == 0
        assert matchup.home_fatigue.games_in_last_7_days == 0


# ── Rest differential ────────────────────────────────────────────────────────

class TestRestDifferential(unittest.TestCase):
    """Verify rest_differential = home_rest - away_rest."""

    def test_home_fresher(self):
        """Home team has 3 rest days, away has 0 → positive differential."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(days=3)),  # Lakers: 3d rest
            _FakeEvent("g2", "Celtics", "Y", _NOW - timedelta(hours=20)),  # Celtics: ~0.8d
            _FakeEvent("g3", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.rest_differential > 2.0

    def test_away_fresher(self):
        """Away team has more rest → negative differential."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(hours=22)),
            _FakeEvent("g2", "Celtics", "Y", _NOW - timedelta(days=4)),
            _FakeEvent("g3", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.rest_differential < -2.0

    def test_equal_rest(self):
        """Both teams same rest → differential near zero."""
        game_time = _NOW
        prev = _NOW - timedelta(days=2)
        events = [
            _FakeEvent("g1", "Lakers", "X", prev),
            _FakeEvent("g2", "Celtics", "Y", prev),
            _FakeEvent("g3", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert abs(matchup.rest_differential) < 0.1


# ── Edge multiplier values ───────────────────────────────────────────────────

class TestEdgeMultiplier(unittest.TestCase):
    """Verify multiplier values for different fatigue scenarios."""

    def test_b2b_returns_b2b_multiplier(self):
        """Back-to-back → b2b_multiplier (1.5 default)."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(hours=24)),
            _FakeEvent("g2", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer(b2b_mult=1.5)
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.edge_multiplier == 1.5

    def test_rest_diff_returns_rest_multiplier(self):
        """Rest differential ≥ min_rest_diff → rest_diff_multiplier (1.3 default)."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(days=4)),
            _FakeEvent("g2", "Celtics", "Y", _NOW - timedelta(days=1, hours=12)),  # ~36h ago, outside B2B
            _FakeEvent("g3", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer(rest_diff_mult=1.3, min_rest_diff=2.0)
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.edge_multiplier == 1.3

    def test_no_signal_returns_one(self):
        """No fatigue signal → multiplier = 1.0."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(days=2)),
            _FakeEvent("g2", "Celtics", "Y", _NOW - timedelta(days=2)),
            _FakeEvent("g3", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.edge_multiplier == 1.0

    def test_b2b_takes_priority_over_rest_diff(self):
        """When both B2B and rest-diff apply, B2B multiplier wins (it's checked first)."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(hours=24)),
            _FakeEvent("g2", "Celtics", "Y", _NOW - timedelta(days=5)),
            _FakeEvent("g3", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer(b2b_mult=1.5, rest_diff_mult=1.3)
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.edge_multiplier == 1.5

    def test_custom_multiplier_values(self):
        """Custom multiplier config is respected."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW - timedelta(hours=20)),
            _FakeEvent("g2", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer(b2b_mult=2.0)
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.edge_multiplier == 2.0


# ── Fatigue score computation ────────────────────────────────────────────────

class TestFatigueScore(unittest.TestCase):
    """Verify the composite fatigue score formula."""

    def test_b2b_gives_high_score(self):
        score = _compute_fatigue_score({
            "is_second_of_b2b": True,
            "games_in_last_4_days": 1,
            "games_in_last_7_days": 2,
            "rest_days": 0.9,
        })
        assert score == 0.50

    def test_three_in_four_adds_penalty(self):
        score = _compute_fatigue_score({
            "is_second_of_b2b": True,
            "games_in_last_4_days": 3,
            "games_in_last_7_days": 3,
            "rest_days": 0.9,
        })
        assert score == 0.70  # 0.50 + 0.20

    def test_heavy_week_adds_penalty(self):
        score = _compute_fatigue_score({
            "is_second_of_b2b": True,
            "games_in_last_4_days": 3,
            "games_in_last_7_days": 4,
            "rest_days": 0.9,
        })
        assert score == 0.85  # 0.50 + 0.20 + 0.15

    def test_well_rested_gets_bonus(self):
        score = _compute_fatigue_score({
            "is_second_of_b2b": False,
            "games_in_last_4_days": 1,
            "games_in_last_7_days": 2,
            "rest_days": 4.0,
        })
        assert score == 0.0  # -0.10 clamped to 0.0

    def test_score_clamped_at_one(self):
        score = _compute_fatigue_score({
            "is_second_of_b2b": True,
            "games_in_last_4_days": 3,
            "games_in_last_7_days": 5,
            "rest_days": 0.5,
        })
        assert score <= 1.0

    def test_no_fatigue_gives_zero(self):
        score = _compute_fatigue_score({
            "is_second_of_b2b": False,
            "games_in_last_4_days": 1,
            "games_in_last_7_days": 2,
            "rest_days": 2.0,
        })
        assert score == 0.0


# ── Schedule cache operations ────────────────────────────────────────────────

class TestScheduleCache(unittest.TestCase):
    """Verify JSONL cache load/append/dedup."""

    def test_load_missing_file(self):
        """Missing cache file → empty state, no error."""
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        assert not path.exists()
        analyzer = _make_analyzer(cache_path=path)
        assert len(analyzer._cached_events) == 0

    def test_load_existing_cache(self):
        """Pre-populated cache file is loaded at init."""
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        records = [
            {"event_id": f"ev{i}", "sport_key": "basketball_nba",
             "home_team": "Lakers", "away_team": "Celtics",
             "commence_time": (_NOW - timedelta(days=i)).isoformat()}
            for i in range(5)
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        analyzer = _make_analyzer(cache_path=path)
        assert len(analyzer._cached_events) == 5

    def test_append_deduplicates(self):
        """Same event_id should not be appended twice."""
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        analyzer = _make_analyzer(cache_path=path)

        ev = _FakeEvent("ev1", "Lakers", "Celtics", _NOW)
        assert analyzer.update_cache([ev], "basketball_nba") == 1
        assert analyzer.update_cache([ev], "basketball_nba") == 0  # dup
        assert len(analyzer._cached_events) == 1

    def test_cache_persists_to_disk(self):
        """Events written to cache file can be loaded by a new analyzer."""
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        a1 = _make_analyzer(cache_path=path)
        a1.update_cache([
            _FakeEvent("ev1", "Lakers", "Celtics", _NOW),
            _FakeEvent("ev2", "Lakers", "Knicks", _NOW - timedelta(hours=24)),
        ], "basketball_nba")

        # New analyzer reads from same file
        a2 = _make_analyzer(cache_path=path)
        assert len(a2._cached_events) == 2

    def test_corrupt_line_skipped(self):
        """Invalid JSON lines are skipped gracefully."""
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        valid = json.dumps({
            "event_id": "ev1", "sport_key": "basketball_nba",
            "home_team": "Lakers", "away_team": "Celtics",
            "commence_time": _NOW.isoformat(),
        })
        path.write_text(f"{valid}\nNOT_VALID_JSON\n{valid.replace('ev1', 'ev2')}\n")

        analyzer = _make_analyzer(cache_path=path)
        assert len(analyzer._cached_events) == 2  # skipped the bad line


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):
    """Boundary and degenerate input scenarios."""

    def test_unknown_team_neutral(self):
        """Team with no schedule data → rest_days=99, multiplier=1.0."""
        analyzer = _make_analyzer()
        matchup = analyzer.analyze_matchup("Unknown FC", "Mystery SC", _NOW, [])
        assert matchup.home_fatigue.rest_days == 99.0
        assert matchup.away_fatigue.rest_days == 99.0
        assert matchup.edge_multiplier == 1.0

    def test_empty_events_list(self):
        """Empty current_events → relies on cache (which is also empty)."""
        analyzer = _make_analyzer()
        matchup = analyzer.analyze_matchup("Lakers", "Celtics", _NOW, [])
        assert matchup.edge_multiplier == 1.0

    def test_future_games_not_counted_as_prior(self):
        """Games scheduled after game_time should not count as prior games."""
        game_time = _NOW
        events = [
            _FakeEvent("g1", "Lakers", "X", _NOW + timedelta(days=2)),  # future
            _FakeEvent("g2", "Lakers", "Celtics", game_time),
        ]
        analyzer = _make_analyzer()
        analyzer.update_cache(events, "basketball_nba")

        matchup = analyzer.analyze_matchup("Lakers", "Celtics", game_time, events)
        assert matchup.home_fatigue.rest_days == 99.0  # no prior games
        assert matchup.home_fatigue.is_back_to_back is False

    def test_from_config_defaults(self):
        """from_config with empty fatigue section uses sane defaults."""
        analyzer = FatigueAnalyzer.from_config({})
        assert "basketball_nba" in analyzer.sports
        assert "basketball_ncaab" in analyzer.sports


# ── from_config ──────────────────────────────────────────────────────────────

class TestFromConfig(unittest.TestCase):
    """Verify config-driven construction."""

    def test_custom_config_values(self):
        """All config values are respected."""
        config = {
            "fatigue": {
                "sports": ["basketball_nba"],
                "schedule_cache_path": "/tmp/test_cache.jsonl",
                "b2b_multiplier": 2.0,
                "rest_diff_multiplier": 1.5,
                "min_rest_diff": 3.0,
                "b2b_threshold_hours": 30.0,
            }
        }
        analyzer = FatigueAnalyzer.from_config(config)
        assert analyzer.sports == frozenset(["basketball_nba"])
        assert analyzer._b2b_mult == 2.0
        assert analyzer._rest_diff_mult == 1.5
        assert analyzer._min_rest_diff == 3.0
        assert analyzer._b2b_hours == 30.0
