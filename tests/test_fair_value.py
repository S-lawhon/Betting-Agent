"""Tests for src/fair_value.py — FairValueEstimator."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Legacy", "Kalshi Arb Project", "src"))

import pytest
from fair_value import FairValueEstimator, FairValueResult
from elo_model import EloModel


# ── Fixtures ──────────────────────────────────────────────────────────────

class _MockConsensus:
    def __init__(self, hp, ap):
        self.home_prob = hp
        self.away_prob = ap


class _MockOutcome:
    def __init__(self, name, price):
        self.name = name
        self.price = price


class _MockBookmaker:
    def __init__(self, key, outcomes):
        self.bookmaker_key = key
        self.outcomes = outcomes


class _MockEvent:
    def __init__(self, home, away, bookmaker_odds=None):
        self.home_team = home
        self.away_team = away
        self.bookmaker_odds = bookmaker_odds or []


class _MockOddsClient:
    def __init__(self, home_prob=0.60):
        self._home_prob = home_prob

    def get_consensus(self, event):
        return _MockConsensus(self._home_prob, 1.0 - self._home_prob)


def _make_pinnacle_event(home, away, home_odds, away_odds):
    """Create an event with Pinnacle odds."""
    return _MockEvent(home, away, [
        _MockBookmaker("pinnacle", [
            _MockOutcome(home, home_odds),
            _MockOutcome(away, away_odds),
        ])
    ])


def _make_elo_model(n_teams=12):
    """Build an Elo model with enough teams to pass the maturity check."""
    model = EloModel()
    teams = [
        ("Team A", "Team B"), ("Team C", "Team D"),
        ("Team E", "Team F"), ("Team G", "Team H"),
        ("Team I", "Team J"), ("Team K", "Team L"),
    ]
    for h, a in teams:
        model.update(h, a, home_won=True, sport="basketball_nba")
    return model


# ── Test: consensus method ───────────────────────────────────────────────

class TestConsensusMethod:
    def test_returns_consensus_prob(self):
        est = FairValueEstimator(_MockOddsClient(0.55), method="consensus")
        event = _MockEvent("Home", "Away")
        r = est.estimate(event, sport="basketball_nba")
        assert r.method == "consensus"
        assert abs(r.home_prob - 0.55) < 0.001
        assert abs(r.away_prob - 0.45) < 0.001

    def test_confidence_is_1(self):
        est = FairValueEstimator(_MockOddsClient(), method="consensus")
        r = est.estimate(_MockEvent("H", "A"), sport="basketball_nba")
        assert r.confidence == 1.0


# ── Test: pinnacle_only method ──────────────────────────────────────────

class TestPinnacleOnly:
    def test_uses_pinnacle_line(self):
        est = FairValueEstimator(_MockOddsClient(), method="pinnacle_only")
        # Celtics -200 / Heat +170 → implied ~66.7%/37.0% → devigged ~64.3%
        event = _make_pinnacle_event("Celtics", "Heat", -200, 170)
        r = est.estimate(event, sport="basketball_nba")
        assert r.method == "pinnacle_only"
        assert 0.63 < r.home_prob < 0.66
        assert r.pinnacle_home is not None
        assert r.confidence == 1.0

    def test_falls_back_to_consensus_when_no_pinnacle(self):
        est = FairValueEstimator(_MockOddsClient(0.55), method="pinnacle_only")
        # Event with no Pinnacle odds
        event = _MockEvent("Home", "Away", [
            _MockBookmaker("fanduel", [_MockOutcome("Home", -150), _MockOutcome("Away", 130)])
        ])
        r = est.estimate(event, sport="basketball_nba")
        assert r.method == "consensus_fallback"
        assert abs(r.home_prob - 0.55) < 0.001
        assert r.confidence < 1.0  # Reduced confidence on fallback

    def test_pinnacle_home_is_populated(self):
        est = FairValueEstimator(_MockOddsClient(), method="pinnacle_only")
        event = _make_pinnacle_event("A", "B", -110, -110)
        r = est.estimate(event, sport="basketball_nba")
        assert r.pinnacle_home is not None
        assert 0.49 < r.pinnacle_home < 0.51  # Even odds


# ── Test: ensemble method ───────────────────────────────────────────────

class TestEnsemble:
    def test_blends_pinnacle_and_consensus(self):
        """Without Elo, should blend Pinnacle + consensus."""
        est = FairValueEstimator(
            _MockOddsClient(0.55),
            method="ensemble",
            pinnacle_weight=0.60,
            elo_weight=0.20,
            consensus_weight=0.20,
        )
        event = _make_pinnacle_event("H", "A", -200, 170)
        r = est.estimate(event, sport="basketball_nba")
        assert "pinnacle" in r.method
        assert "consensus" in r.method
        # Without Elo, weights renormalize: pinnacle 0.75, consensus 0.25
        # pinnacle ~0.643, consensus 0.55 → blend ~0.62
        assert 0.60 < r.home_prob < 0.65

    def test_includes_elo_when_mature(self):
        model = _make_elo_model()
        # Add the specific teams we're querying
        for _ in range(3):
            model.update("Home", "Away", True, "basketball_nba")

        est = FairValueEstimator(
            _MockOddsClient(0.55),
            elo_model=model,
            method="ensemble",
            pinnacle_weight=0.60,
            elo_weight=0.20,
            consensus_weight=0.20,
        )
        event = _make_pinnacle_event("Home", "Away", -200, 170)
        r = est.estimate(event, sport="basketball_nba")
        assert "elo" in r.method
        assert r.elo_home is not None
        assert r.confidence == 1.0

    def test_excludes_immature_elo(self):
        """Elo with <10 teams should be excluded from ensemble."""
        model = EloModel()
        model.update("A", "B", True, "basketball_nba")  # Only 2 teams

        est = FairValueEstimator(
            _MockOddsClient(0.55),
            elo_model=model,
            method="ensemble",
        )
        event = _make_pinnacle_event("A", "B", -110, -110)
        r = est.estimate(event, sport="basketball_nba")
        assert "elo" not in r.method

    def test_weight_normalization(self):
        """Weights that don't sum to 1 should be normalized."""
        est = FairValueEstimator(
            _MockOddsClient(0.50),
            method="ensemble",
            pinnacle_weight=0.30,
            elo_weight=0.10,
            consensus_weight=0.10,
        )
        # Should normalize to 0.6, 0.2, 0.2
        assert abs(est._pinnacle_w + est._elo_w + est._consensus_w - 1.0) < 0.01

    def test_returns_none_when_no_consensus(self):
        class _NoneOddsClient:
            def get_consensus(self, event):
                return None

        est = FairValueEstimator(_NoneOddsClient(), method="ensemble")
        r = est.estimate(_MockEvent("H", "A"), sport="nba")
        assert r is None


# ── Test: from_config ───────────────────────────────────────────────────

class TestFromConfig:
    def test_loads_ensemble_config(self):
        config = {
            "pods": {
                "P-006": {
                    "fair_value": {
                        "method": "ensemble",
                        "pinnacle_weight": 0.70,
                        "elo_weight": 0.15,
                        "consensus_weight": 0.15,
                    }
                }
            }
        }
        est = FairValueEstimator.from_config(config, _MockOddsClient())
        assert est._method == "ensemble"
        assert est._pinnacle_w == 0.70

    def test_defaults_when_no_config(self):
        est = FairValueEstimator.from_config({}, _MockOddsClient())
        assert est._method == "consensus"
