"""Tests for src/live_odds_poller.py — LiveOddsPoller."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.live_odds_poller import (
    LiveOddsPoller,
    LiveOddsSnapshot,
    BookmakerLine,
    american_to_prob,
)


# ── Fixtures ──────────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 3, 28, 20, 0, 0, tzinfo=timezone.utc)


def _make_odds_api_response(n_games=2, live=True):
    """Build a mock Odds API /odds response."""
    games = []
    for i in range(n_games):
        # If live, commence_time is in the past
        commence = "2026-03-28T19:00:00Z" if live else "2026-03-29T19:00:00Z"
        games.append({
            "id": f"game_{i}",
            "sport_key": "basketball_nba",
            "home_team": f"Home Team {i}",
            "away_team": f"Away Team {i}",
            "commence_time": commence,
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "last_update": "2026-03-28T19:55:00Z",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": f"Home Team {i}", "price": -150},
                            {"name": f"Away Team {i}", "price": 130},
                        ],
                    }],
                },
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "last_update": "2026-03-28T19:55:00Z",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": f"Home Team {i}", "price": -155},
                            {"name": f"Away Team {i}", "price": 135},
                        ],
                    }],
                },
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-03-28T19:55:00Z",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": f"Home Team {i}", "price": -148},
                            {"name": f"Away Team {i}", "price": 128},
                        ],
                    }],
                },
            ],
        })
    return games


def _mock_session(response_data, status=200, headers=None):
    """Create a mock requests.Session."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = response_data
    resp.headers = headers or {
        "x-requests-remaining": "450",
        "x-requests-used": "50",
    }
    session.get.return_value = resp
    return session


def _make_poller(session=None, response_data=None, **kwargs):
    """Build a LiveOddsPoller with mocked session."""
    if session is None:
        session = _mock_session(response_data or _make_odds_api_response())
    return LiveOddsPoller(
        api_key="test-key",
        _session=session,
        _now_fn=lambda: FIXED_NOW,
        **kwargs,
    )


# ── american_to_prob tests ────────────────────────────────────────────

class TestAmericanToProb:
    def test_favorite(self):
        # -150 → 150/(150+100) = 0.6
        assert abs(american_to_prob(-150) - 0.6) < 0.001

    def test_underdog(self):
        # +130 → 100/(130+100) ≈ 0.4348
        assert abs(american_to_prob(130) - 0.4348) < 0.001

    def test_even(self):
        # +100 → 100/(100+100) = 0.5
        assert abs(american_to_prob(100) - 0.5) < 0.001

    def test_heavy_favorite(self):
        # -300 → 300/400 = 0.75
        assert abs(american_to_prob(-300) - 0.75) < 0.001

    def test_zero_returns_none(self):
        assert american_to_prob(0) is None


# ── LiveOddsPoller.poll tests ─────────────────────────────────────────

class TestLiveOddsPollerPoll:
    def test_returns_live_games_only(self):
        """Should filter out games that haven't started yet."""
        data = _make_odds_api_response(1, live=True)
        data.append({
            "id": "future_game",
            "home_team": "Future Home",
            "away_team": "Future Away",
            "commence_time": "2026-03-29T19:00:00Z",  # tomorrow
            "bookmakers": [],
        })
        poller = _make_poller(response_data=data)
        results = poller.poll("basketball_nba")
        assert len(results) == 1
        assert results[0].game_id == "game_0"

    def test_returns_multiple_live_games(self):
        poller = _make_poller(response_data=_make_odds_api_response(3))
        results = poller.poll("basketball_nba")
        assert len(results) == 3

    def test_empty_response(self):
        session = _mock_session([])
        poller = _make_poller(session=session)
        results = poller.poll("basketball_nba")
        assert results == []

    def test_http_error_returns_empty(self):
        session = _mock_session([], status=500)
        poller = _make_poller(session=session)
        results = poller.poll("basketball_nba")
        assert results == []

    def test_rate_limit_returns_empty(self):
        session = _mock_session([], status=429)
        poller = _make_poller(session=session)
        results = poller.poll("basketball_nba")
        assert results == []

    def test_auth_error_returns_empty(self):
        session = _mock_session([], status=401)
        poller = _make_poller(session=session)
        results = poller.poll("basketball_nba")
        assert results == []

    def test_caches_snapshots(self):
        poller = _make_poller(response_data=_make_odds_api_response(1))
        poller.poll("basketball_nba")
        cached = poller.get_cached("game_0")
        assert cached is not None
        assert cached.home_team == "Home Team 0"

    def test_credit_tracking(self):
        poller = _make_poller()
        poller.poll("basketball_nba")
        assert poller.credits_remaining == 450
        assert poller.credits_used == 50


# ── LiveOddsSnapshot tests ────────────────────────────────────────────

class TestLiveOddsSnapshot:
    def test_get_pinnacle_line(self):
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="Lakers", away_team="Celtics",
            commence_time="2026-03-28T19:00:00Z", is_live=True,
            bookmaker_lines=[
                BookmakerLine("fanduel", "FanDuel", -150, 130, ""),
                BookmakerLine("pinnacle", "Pinnacle", -145, 125, ""),
            ],
        )
        pin = snap.get_pinnacle_line()
        assert pin is not None
        assert pin.home_price == -145

    def test_get_pinnacle_line_missing(self):
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="Lakers", away_team="Celtics",
            commence_time="2026-03-28T19:00:00Z", is_live=True,
            bookmaker_lines=[
                BookmakerLine("fanduel", "FanDuel", -150, 130, ""),
            ],
        )
        assert snap.get_pinnacle_line() is None

    def test_consensus_probs_weighted(self):
        """Pinnacle should get 2x weight in consensus."""
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="Lakers", away_team="Celtics",
            commence_time="2026-03-28T19:00:00Z", is_live=True,
            bookmaker_lines=[
                BookmakerLine("pinnacle", "Pinnacle", -150, 130, ""),
                BookmakerLine("fanduel", "FanDuel", -200, 170, ""),
            ],
        )
        probs = snap.get_consensus_probs()
        assert probs is not None
        home, away = probs
        # Pinnacle: -150 → 0.6 implied, +130 → 0.4348, devig ≈ 0.58
        # FanDuel: -200 → 0.667, +170 → 0.37, devig ≈ 0.643
        # Weighted: (0.58*2 + 0.643*1) / 3 ≈ 0.601
        assert 0.55 < home < 0.65
        assert abs(home + away - 1.0) < 0.01

    def test_consensus_probs_empty_lines(self):
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="Lakers", away_team="Celtics",
            commence_time="", is_live=True,
            bookmaker_lines=[],
        )
        assert snap.get_consensus_probs() is None

    def test_num_books(self):
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            commence_time="", is_live=True,
            bookmaker_lines=[
                BookmakerLine("a", "A", -150, 130, ""),
                BookmakerLine("b", "B", -150, 130, ""),
                BookmakerLine("c", "C", -150, 130, ""),
            ],
        )
        assert snap.num_books == 3


# ── LiveOddsPoller.poll_all tests ─────────────────────────────────────

class TestPollAll:
    def test_polls_multiple_sports(self):
        poller = _make_poller(response_data=_make_odds_api_response(1))
        results = poller.poll_all(["basketball_nba", "baseball_mlb"])
        assert "basketball_nba" in results
        assert "baseball_mlb" in results


# ── Line movement detection tests ──────────────────────────────────

class TestLineMovement:
    def test_detects_movement(self):
        poller = _make_poller()
        old_snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            commence_time="", is_live=True,
            bookmaker_lines=[
                BookmakerLine("pinnacle", "Pinnacle", -150, 130, ""),
            ],
        )
        poller._cache["g1"] = old_snap

        new_snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            commence_time="", is_live=True,
            bookmaker_lines=[
                BookmakerLine("pinnacle", "Pinnacle", -250, 210, ""),
            ],
        )
        movement = poller.detect_line_movement("g1", new_snap, threshold=0.02)
        assert movement is not None
        assert movement["direction"] == "home"

    def test_no_movement_below_threshold(self):
        poller = _make_poller()
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            commence_time="", is_live=True,
            bookmaker_lines=[
                BookmakerLine("pinnacle", "Pinnacle", -150, 130, ""),
            ],
        )
        poller._cache["g1"] = snap
        # Same snapshot → no movement
        result = poller.detect_line_movement("g1", snap, threshold=0.02)
        assert result is None

    def test_no_cache_returns_none(self):
        poller = _make_poller()
        snap = LiveOddsSnapshot(
            game_id="g1", sport="basketball_nba",
            home_team="A", away_team="B",
            commence_time="", is_live=True,
            bookmaker_lines=[
                BookmakerLine("pinnacle", "Pinnacle", -150, 130, ""),
            ],
        )
        result = poller.detect_line_movement("g1", snap)
        assert result is None


# ── from_config tests ─────────────────────────────────────────────────

class TestFromConfig:
    def test_builds_from_config(self):
        config = {
            "pods": {
                "P-014": {
                    "odds_api": {
                        "regions": ["us", "eu"],
                        "markets": ["h2h", "spreads"],
                    }
                }
            }
        }
        poller = LiveOddsPoller.from_config(config, api_key="test-key")
        assert poller._regions == ["us", "eu"]
        assert poller._markets == ["h2h", "spreads"]

    def test_raises_without_api_key(self):
        with pytest.raises(ValueError, match="ODDS_API_KEY"):
            LiveOddsPoller.from_config({}, api_key="")
