"""Tests for src/elo_model.py — Elo rating model."""

import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from pathlib import Path
from elo_model import EloModel


class TestPredict:
    def test_home_advantage(self):
        """Equal-rated teams: home should have >50% win probability."""
        model = EloModel()
        p = model.predict("Home", "Away", "basketball_nba")
        assert p > 0.50

    def test_neutral_site(self):
        """Equal-rated teams at neutral site should be ~50%."""
        model = EloModel()
        p = model.predict("Home", "Away", "basketball_nba", neutral_site=True)
        assert abs(p - 0.50) < 0.01

    def test_higher_rated_favored(self):
        """Team with higher Elo should be favored."""
        model = EloModel()
        # Give home a bunch of wins
        for _ in range(10):
            model.update("Strong", "Weak", True, "basketball_nba")
        p = model.predict("Strong", "Weak", "basketball_nba", neutral_site=True)
        assert p > 0.65

    def test_clamped_to_valid_range(self):
        """Probability should be clamped between 0.001 and 0.999."""
        model = EloModel()
        # Extreme rating difference
        model._set_rating("God", "basketball_nba", 3000)
        model._set_rating("Bot", "basketball_nba", 500)
        p = model.predict("God", "Bot", "basketball_nba")
        assert 0.001 <= p <= 0.999


class TestUpdate:
    def test_winner_rating_increases(self):
        model = EloModel()
        r_before = model._get_rating("Winner", "basketball_nba")
        model.update("Winner", "Loser", True, "basketball_nba")
        r_after = model._get_rating("Winner", "basketball_nba")
        assert r_after > r_before

    def test_loser_rating_decreases(self):
        model = EloModel()
        r_before = model._get_rating("Loser", "basketball_nba")
        model.update("Winner", "Loser", True, "basketball_nba")
        r_after = model._get_rating("Loser", "basketball_nba")
        assert r_after < r_before

    def test_ratings_conserved(self):
        """Total Elo in the system should be conserved (zero-sum)."""
        model = EloModel()
        model.update("A", "B", True, "basketball_nba")
        r_a = model._get_rating("A", "basketball_nba")
        r_b = model._get_rating("B", "basketball_nba")
        # Rounding may cause tiny drift, but should be close to 3000 (2 × 1500)
        assert abs((r_a + r_b) - 3000.0) < 1.0

    def test_upset_produces_larger_adjustment(self):
        """Unexpected result should move ratings more."""
        model = EloModel()
        # Make A strong
        for _ in range(10):
            model.update("Fav", "Dog", True, "basketball_nba")

        r_fav_before = model._get_rating("Fav", "basketball_nba")
        model.update("Fav", "Dog", False, "basketball_nba")  # Upset!
        r_fav_after = model._get_rating("Fav", "basketball_nba")
        drop = r_fav_before - r_fav_after
        assert drop > 10  # Should be a significant drop


class TestSportIsolation:
    def test_ratings_isolated_by_sport(self):
        model = EloModel()
        model.update("Team", "Opponent", True, "basketball_nba")
        model.update("Team", "Opponent", False, "icehockey_nhl")

        nba_rating = model._get_rating("Team", "basketball_nba")
        nhl_rating = model._get_rating("Team", "icehockey_nhl")
        assert nba_rating > 1500  # Won in NBA
        assert nhl_rating < 1500  # Lost in NHL


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        ratings_file = tmp_path / "elo.json"

        # Create and populate model
        model = EloModel(ratings_file=ratings_file)
        model.update("Celtics", "Heat", True, "basketball_nba")
        model.save()

        # Verify file exists
        assert ratings_file.exists()

        # Load into new model
        with open(ratings_file) as f:
            data = json.load(f)
        model2 = EloModel(ratings=data["ratings"], ratings_file=ratings_file)

        r1 = model._get_rating("Celtics", "basketball_nba")
        r2 = model2._get_rating("Celtics", "basketball_nba")
        assert abs(r1 - r2) < 0.01

    def test_no_save_when_not_dirty(self, tmp_path):
        ratings_file = tmp_path / "elo.json"
        model = EloModel(ratings_file=ratings_file)
        model.save()  # Nothing to save
        assert not ratings_file.exists()


class TestHelpers:
    def test_has_ratings(self):
        model = EloModel()
        assert not model.has_ratings("basketball_nba")
        model.update("A", "B", True, "basketball_nba")
        assert model.has_ratings("basketball_nba")

    def test_team_count(self):
        model = EloModel()
        assert model.team_count("basketball_nba") == 0
        model.update("A", "B", True, "basketball_nba")
        assert model.team_count("basketball_nba") == 2

    def test_get_all_ratings(self):
        model = EloModel()
        model.update("X", "Y", True, "icehockey_nhl")
        ratings = model.get_all_ratings("icehockey_nhl")
        assert "X" in ratings
        assert "Y" in ratings
        assert len(ratings) == 2
