"""Tests for src/game_state.py — GameStateTracker."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.game_state import (
    GameStateTracker,
    GameState,
    GamePhase,
    GameEvent,
    GameEventType,
)


# ── Fixtures ──────────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 3, 28, 21, 0, 0, tzinfo=timezone.utc)


def _make_scores_response(games=None):
    """Build a mock Odds API /scores response."""
    if games is not None:
        return games
    return [
        {
            "id": "game_1",
            "home_team": "Lakers",
            "away_team": "Celtics",
            "commence_time": "2026-03-28T19:00:00Z",
            "completed": False,
            "scores": [
                {"name": "Lakers", "score": "85"},
                {"name": "Celtics", "score": "78"},
            ],
        },
        {
            "id": "game_2",
            "home_team": "Warriors",
            "away_team": "Suns",
            "commence_time": "2026-03-28T19:30:00Z",
            "completed": False,
            "scores": [
                {"name": "Warriors", "score": "62"},
                {"name": "Suns", "score": "65"},
            ],
        },
    ]


def _mock_session(response_data, status=200):
    """Create a mock requests.Session for scores endpoint."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = response_data
    session.get.return_value = resp
    return session


def _make_tracker(response_data=None, **kwargs):
    """Build a GameStateTracker with mocked session."""
    data = response_data if response_data is not None else _make_scores_response()
    session = _mock_session(data)
    return GameStateTracker(
        api_key="test-key",
        _session=session,
        _now_fn=lambda: FIXED_NOW,
        **kwargs,
    )


# ── GameState tests ───────────────────────────────────────────────────

class TestGameState:
    def test_total_score(self):
        gs = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            home_score=85, away_score=78,
        )
        assert gs.total_score == 163

    def test_score_diff_home_leading(self):
        gs = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            home_score=85, away_score=78,
        )
        assert gs.score_diff == 7

    def test_score_diff_away_leading(self):
        gs = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            home_score=70, away_score=85,
        )
        assert gs.score_diff == -15

    def test_is_close_game_nba(self):
        gs = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            home_score=85, away_score=80,
        )
        assert gs.is_close_game is True

        gs2 = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            home_score=100, away_score=80,
        )
        assert gs2.is_close_game is False

    def test_is_close_game_mlb(self):
        gs = GameState(
            game_id="g1", sport="baseball_mlb",
            home_team="A", away_team="B",
            home_score=3, away_score=2,
        )
        assert gs.is_close_game is True

        gs2 = GameState(
            game_id="g1", sport="baseball_mlb",
            home_team="A", away_team="B",
            home_score=8, away_score=2,
        )
        assert gs2.is_close_game is False

    def test_game_progress_nba(self):
        gs = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            period=2,
        )
        assert abs(gs.game_progress_pct() - 0.5) < 0.01

    def test_game_progress_mlb(self):
        gs = GameState(
            game_id="g1", sport="baseball_mlb",
            home_team="A", away_team="B",
            period=5,
        )
        assert abs(gs.game_progress_pct() - 5 / 9) < 0.01

    def test_game_progress_no_period(self):
        gs = GameState(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
        )
        assert gs.game_progress_pct() == 0.0


# ── GameStateTracker.update tests ─────────────────────────────────────

class TestTrackerUpdate:
    def test_returns_live_games(self):
        tracker = _make_tracker()
        games = tracker.update("basketball_nba")
        assert len(games) == 2
        assert games[0].home_team == "Lakers"
        assert games[0].home_score == 85

    def test_tracks_game_state(self):
        tracker = _make_tracker()
        tracker.update("basketball_nba")
        gs = tracker.get_game("game_1")
        assert gs is not None
        assert gs.home_score == 85
        assert gs.away_score == 78
        assert gs.phase == GamePhase.LIVE

    def test_detects_completed_games(self):
        data = [{
            "id": "game_done",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-03-28T17:00:00Z",
            "completed": True,
            "scores": [
                {"name": "A", "score": "110"},
                {"name": "B", "score": "105"},
            ],
        }]
        tracker = _make_tracker(response_data=data)
        games = tracker.update("basketball_nba")
        # Completed games should not be in the "live" list
        assert len(games) == 0
        gs = tracker.get_game("game_done")
        assert gs.phase == GamePhase.COMPLETED

    def test_handles_no_scores(self):
        """Games with no scores yet (just commenced) should still be tracked."""
        data = [{
            "id": "game_new",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-03-28T20:55:00Z",  # 5 min ago
            "completed": False,
            "scores": None,
        }]
        tracker = _make_tracker(response_data=data)
        games = tracker.update("basketball_nba")
        assert len(games) == 1
        assert games[0].home_score == 0

    def test_handles_http_error(self):
        session = _mock_session([], status=500)
        tracker = GameStateTracker(
            api_key="test-key",
            _session=session,
            _now_fn=lambda: FIXED_NOW,
        )
        games = tracker.update("basketball_nba")
        assert games == []


# ── Event detection tests ─────────────────────────────────────────────

class TestEventDetection:
    def test_game_start_event(self):
        tracker = _make_tracker()
        tracker.update("basketball_nba")
        events = tracker.get_pending_events()
        # First update → should emit GAME_START for each live game
        start_events = [e for e in events if e.event_type == GameEventType.GAME_START]
        assert len(start_events) == 2

    def test_score_change_event(self):
        # First update
        data_initial = [{
            "id": "game_1",
            "home_team": "Lakers",
            "away_team": "Celtics",
            "commence_time": "2026-03-28T19:00:00Z",
            "completed": False,
            "scores": [
                {"name": "Lakers", "score": "80"},
                {"name": "Celtics", "score": "75"},
            ],
        }]
        session = MagicMock()
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = data_initial

        data_updated = [{
            "id": "game_1",
            "home_team": "Lakers",
            "away_team": "Celtics",
            "commence_time": "2026-03-28T19:00:00Z",
            "completed": False,
            "scores": [
                {"name": "Lakers", "score": "83"},
                {"name": "Celtics", "score": "75"},
            ],
        }]
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = data_updated

        session.get.side_effect = [resp1, resp2]

        tracker = GameStateTracker(
            api_key="test-key",
            _session=session,
            _now_fn=lambda: FIXED_NOW,
        )

        # First update — game start
        tracker.update("basketball_nba")
        tracker.get_pending_events()  # consume start events

        # Second update — score change
        tracker.update("basketball_nba")
        events = tracker.get_pending_events()
        score_events = [e for e in events if e.event_type == GameEventType.SCORE_CHANGE]
        assert len(score_events) == 1
        assert score_events[0].details["home_delta"] == 3
        assert score_events[0].details["away_delta"] == 0
        assert score_events[0].details["scoring_team"] == "Lakers"

    def test_game_end_event(self):
        data_live = [{
            "id": "game_1",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-03-28T19:00:00Z",
            "completed": False,
            "scores": [
                {"name": "A", "score": "100"},
                {"name": "B", "score": "95"},
            ],
        }]
        data_done = [{
            "id": "game_1",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-03-28T19:00:00Z",
            "completed": True,
            "scores": [
                {"name": "A", "score": "110"},
                {"name": "B", "score": "105"},
            ],
        }]

        session = MagicMock()
        r1 = MagicMock(status_code=200)
        r1.json.return_value = data_live
        r2 = MagicMock(status_code=200)
        r2.json.return_value = data_done
        session.get.side_effect = [r1, r2]

        tracker = GameStateTracker(
            api_key="test-key", _session=session, _now_fn=lambda: FIXED_NOW,
        )
        tracker.update("basketball_nba")
        tracker.get_pending_events()  # consume start

        tracker.update("basketball_nba")
        events = tracker.get_pending_events()
        end_events = [e for e in events if e.event_type == GameEventType.GAME_END]
        assert len(end_events) == 1
        assert end_events[0].details["winner"] == "A"

    def test_events_consumed_once(self):
        tracker = _make_tracker()
        tracker.update("basketball_nba")
        events1 = tracker.get_pending_events()
        assert len(events1) > 0
        events2 = tracker.get_pending_events()
        assert len(events2) == 0


# ── Helper method tests ───────────────────────────────────────────────

class TestHelpers:
    def test_get_live_games_filtered(self):
        tracker = _make_tracker()
        tracker.update("basketball_nba")
        live = tracker.get_live_games("basketball_nba")
        assert len(live) == 2
        # Filter by wrong sport
        live_mlb = tracker.get_live_games("baseball_mlb")
        assert len(live_mlb) == 0

    def test_is_game_live(self):
        tracker = _make_tracker()
        tracker.update("basketball_nba")
        assert tracker.is_game_live("game_1") is True
        assert tracker.is_game_live("nonexistent") is False


# ── from_config tests ─────────────────────────────────────────────────

class TestTrackerFromConfig:
    def test_builds_from_config(self):
        config = {"pods": {"P-014": {"scan": {"timeout": 10}}}}
        tracker = GameStateTracker.from_config(config, api_key="test-key")
        assert tracker._timeout == 10

    def test_raises_without_api_key(self):
        with pytest.raises(ValueError, match="ODDS_API_KEY"):
            GameStateTracker.from_config({}, api_key="")
