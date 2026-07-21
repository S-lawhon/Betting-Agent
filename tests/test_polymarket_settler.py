"""Tests for src/polymarket_settler.py — scores-based + Gamma fallback."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys, os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.polymarket_settler import PolymarketSettler, _calc_outcome_pnl, _fuzzy_team_match


# ── Fixtures ──────────────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 3, 31, 12, 0, 0, tzinfo=timezone.utc)


def _make_placed_entry(**overrides):
    """Build a P-006 PLACED entry like polymarket_consensus.py writes."""
    base = {
        "action": "PLACED",
        "pod_id": "P-006",
        "venue": "polymarket",
        "market_id": "0xabc123",
        "market_type": "sports_basketball_nba",
        "event": "Boston Celtics vs Miami Heat",
        "side": "YES",
        "position_size_usd": 10.0,
        "venue_prob": 0.60,
        "timestamp_utc": "2026-03-30T20:00:00+00:00",
        "extra": {
            "home_team": "Boston Celtics",
            "away_team": "Miami Heat",
            "yes_team": "Boston Celtics",
            "game_time": "2026-03-31T00:00:00+00:00",
            "market_slug": "nba-celtics-heat-2026-03-31",
        },
    }
    base.update(overrides)
    return base


def _write_log(path: Path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_settler(log_path, odds_key="test-key", elo_model=None):
    client = MagicMock()
    return PolymarketSettler(
        polymarket_client=client,
        log_path=log_path,
        odds_api_key=odds_key,
        elo_model=elo_model,
        _now_fn=lambda: FIXED_NOW,
    )


# ── P&L tests ────────────────────────────────────────────────────────────

class TestCalcOutcomePnl:
    def test_win_yes(self):
        outcome, pnl = _calc_outcome_pnl("YES", "yes", 10.0, 0.60)
        assert outcome == "WIN"
        assert abs(pnl - 6.67) < 0.01  # 10 * 0.4 / 0.6

    def test_win_no(self):
        outcome, pnl = _calc_outcome_pnl("NO", "no", 10.0, 0.60)
        assert outcome == "WIN"
        assert abs(pnl - 15.0) < 0.01  # 10 * 0.6 / 0.4

    def test_loss(self):
        outcome, pnl = _calc_outcome_pnl("YES", "no", 10.0, 0.60)
        assert outcome == "LOSS"
        assert pnl == -10.0

    def test_void(self):
        outcome, pnl = _calc_outcome_pnl("YES", "void", 10.0, 0.60)
        assert outcome == "VOID"
        assert pnl == 0.0


# ── Fuzzy match tests ────────────────────────────────────────────────────

class TestFuzzyMatch:
    def test_exact(self):
        assert _fuzzy_team_match("Boston Celtics", "Boston Celtics") > 0.9

    def test_close(self):
        assert _fuzzy_team_match("LA Clippers", "Los Angeles Clippers") > 0.6

    def test_different(self):
        assert _fuzzy_team_match("Boston Celtics", "Miami Heat") < 0.5

    def test_empty(self):
        assert _fuzzy_team_match("", "Miami Heat") == 0.0


# ── Scores-based settlement tests ────────────────────────────────────────

class TestScoresResolution:
    def _mock_scores_response(self, settler, sport, games):
        """Inject fake scores into the cache."""
        settler._scores_cache[sport] = games

    def test_scores_resolve_home_win(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        _write_log(log_path, [entry])

        settler = _make_settler(log_path)
        self._mock_scores_response(settler, "basketball_nba", [
            {
                "completed": True,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "commence_time": "2026-03-31T00:00:00Z",
                "scores": [
                    {"name": "Boston Celtics", "score": "110"},
                    {"name": "Miami Heat", "score": "98"},
                ],
            }
        ])

        result = settler._check_scores(entry)
        assert result == "yes"  # yes_team=Boston Celtics won

    def test_scores_resolve_away_win(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        _write_log(log_path, [entry])

        settler = _make_settler(log_path)
        self._mock_scores_response(settler, "basketball_nba", [
            {
                "completed": True,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "commence_time": "2026-03-31T00:00:00Z",
                "scores": [
                    {"name": "Boston Celtics", "score": "90"},
                    {"name": "Miami Heat", "score": "105"},
                ],
            }
        ])

        result = settler._check_scores(entry)
        assert result == "no"  # yes_team=Boston Celtics lost

    def test_scores_not_completed(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        settler = _make_settler(log_path)
        self._mock_scores_response(settler, "basketball_nba", [
            {
                "completed": False,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "scores": None,
            }
        ])

        result = settler._check_scores(entry)
        assert result is None

    def test_scores_tie_returns_none(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        settler = _make_settler(log_path)
        self._mock_scores_response(settler, "basketball_nba", [
            {
                "completed": True,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "commence_time": "2026-03-31T00:00:00Z",
                "scores": [
                    {"name": "Boston Celtics", "score": "100"},
                    {"name": "Miami Heat", "score": "100"},
                ],
            }
        ])

        result = settler._check_scores(entry)
        assert result is None

    def test_scores_no_api_key(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        settler = _make_settler(log_path, odds_key="")
        result = settler._check_scores(entry)
        assert result is None

    def test_scores_wrong_game_time_rejected(self, tmp_path):
        """Game with matching teams but wrong time should not match."""
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        settler = _make_settler(log_path)
        self._mock_scores_response(settler, "basketball_nba", [
            {
                "completed": True,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                # This game started 5 hours before our trade's game_time
                "commence_time": "2026-03-30T19:00:00Z",
                "scores": [
                    {"name": "Boston Celtics", "score": "110"},
                    {"name": "Miami Heat", "score": "98"},
                ],
            }
        ])

        result = settler._check_scores(entry)
        assert result is None


# ── Full settle cycle tests ──────────────────────────────────────────────

class TestSettleCycle:
    def test_scores_settles_before_gamma(self, tmp_path):
        """Odds API scores should resolve trade without calling Gamma."""
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        _write_log(log_path, [entry])

        settler = _make_settler(log_path)
        scores_data = [
            {
                "completed": True,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "commence_time": "2026-03-31T00:00:00Z",
                "scores": [
                    {"name": "Boston Celtics", "score": "110"},
                    {"name": "Miami Heat", "score": "98"},
                ],
            }
        ]
        # Patch _fetch_scores to return our data without HTTP call
        settler._fetch_scores = lambda sport: scores_data

        settled = settler.settle_cycle()
        assert len(settled) == 1
        assert settled[0]["outcome"] == "WIN"
        assert settled[0]["resolution_source"] == "odds_api_scores"
        # Gamma API should NOT have been called
        settler._client.get_market_resolution.assert_not_called()

    def test_gamma_fallback_when_no_scores(self, tmp_path):
        """If no scores available, fall back to Gamma API."""
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        _write_log(log_path, [entry])

        settler = _make_settler(log_path, odds_key="")  # No API key

        # Mock Gamma API returning resolved
        settler._client.get_market_resolution.return_value = {
            "closed": True,
            "resolved": True,
            "result": "yes",
            "outcome_prices": [0.99, 0.01],
        }

        settled = settler.settle_cycle()
        assert len(settled) == 1
        assert settled[0]["outcome"] == "WIN"
        assert settled[0]["resolution_source"] == "gamma_api"

    def test_auto_void_stale_game(self, tmp_path):
        """Trades with games >8h ago that are unresolved should auto-void."""
        log_path = tmp_path / "trade_log.jsonl"
        # Game was 10 hours ago
        entry = _make_placed_entry(extra={
            "home_team": "Boston Celtics",
            "away_team": "Miami Heat",
            "yes_team": "Boston Celtics",
            "game_time": "2026-03-31T01:00:00+00:00",  # 11h before FIXED_NOW
            "market_slug": "nba-celtics-heat",
        })
        _write_log(log_path, [entry])

        settler = _make_settler(log_path, odds_key="")
        # Gamma says not resolved
        settler._client.get_market_resolution.return_value = {
            "closed": False, "resolved": False, "result": None,
            "outcome_prices": ["0", "0"],
        }

        settled = settler.settle_cycle()
        assert len(settled) == 1
        assert settled[0]["outcome"] == "VOID"

    def test_no_double_settle(self, tmp_path):
        """Already-settled trades should not be re-settled."""
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        settled_entry = {**entry, "action": "WIN", "outcome": "WIN", "result": "yes", "pnl_usd": 6.67}
        _write_log(log_path, [entry, settled_entry])

        settler = _make_settler(log_path)
        settled = settler.settle_cycle()
        assert len(settled) == 0


# ── Elo auto-update tests ───────────────────────────────────────────────

class TestEloAutoUpdate:
    def test_elo_updated_on_settlement(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry()
        _write_log(log_path, [entry])

        elo = MagicMock()
        elo.update.return_value = (1510.0, 1490.0)

        settler = _make_settler(log_path, elo_model=elo)
        scores_data = [
            {
                "completed": True,
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "commence_time": "2026-03-31T00:00:00Z",
                "scores": [
                    {"name": "Boston Celtics", "score": "110"},
                    {"name": "Miami Heat", "score": "98"},
                ],
            }
        ]
        settler._fetch_scores = lambda sport: scores_data

        settled = settler.settle_cycle()
        assert len(settled) == 1

        # Elo should have been updated: Celtics won at home
        elo.update.assert_called_once_with(
            home_team="Boston Celtics",
            away_team="Miami Heat",
            home_won=True,
            sport="basketball_nba",
        )
        elo.save.assert_called_once()

    def test_elo_not_updated_on_void(self, tmp_path):
        log_path = tmp_path / "trade_log.jsonl"
        entry = _make_placed_entry(extra={
            "home_team": "Boston Celtics",
            "away_team": "Miami Heat",
            "yes_team": "Boston Celtics",
            "game_time": "2026-03-31T01:00:00+00:00",
            "market_slug": "nba-celtics-heat",
        })
        _write_log(log_path, [entry])

        elo = MagicMock()
        settler = _make_settler(log_path, odds_key="", elo_model=elo)
        settler._client.get_market_resolution.return_value = {
            "closed": False, "resolved": False, "result": None,
            "outcome_prices": ["0", "0"],
        }

        settled = settler.settle_cycle()
        # Should auto-void (game 11h ago), but NOT update Elo
        assert len(settled) == 1
        assert settled[0]["outcome"] == "VOID"
        elo.update.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
