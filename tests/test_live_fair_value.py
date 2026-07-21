"""Tests for src/live_fair_value.py — LiveFairValueEngine."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.live_fair_value import LiveFairValueEngine, LiveFairValueResult
from src.live_odds_poller import LiveOddsSnapshot, BookmakerLine, american_to_prob
from src.game_state import GameState, GamePhase


# ── Helpers ──────────────────────────────────────────────────────────

FIXED_TS = "2026-03-28T21:00:00+00:00"


def _make_line(key, name, home, away, last_update=""):
    return BookmakerLine(key, name, home, away, last_update or FIXED_TS)


def _make_snapshot(lines=None, poll_ts=None):
    if lines is None:
        lines = [
            _make_line("pinnacle", "Pinnacle", -150, 130),
            _make_line("fanduel", "FanDuel", -155, 135),
            _make_line("draftkings", "DraftKings", -148, 128),
        ]
    return LiveOddsSnapshot(
        game_id="g1",
        sport="basketball_nba",
        home_team="Lakers",
        away_team="Celtics",
        commence_time="2026-03-28T19:00:00Z",
        is_live=True,
        bookmaker_lines=lines,
        poll_timestamp=poll_ts or FIXED_TS,
    )


def _make_game_state(
    home_score=85, away_score=78, period=3, phase=GamePhase.LIVE,
):
    return GameState(
        game_id="g1",
        sport="basketball_nba",
        home_team="Lakers",
        away_team="Celtics",
        home_score=home_score,
        away_score=away_score,
        phase=phase,
        period=period,
    )


# ── Basic estimation ─────────────────────────────────────────────────

class TestEstimate:
    def test_returns_result_with_multiple_books(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert result is not None
        assert 0.0 < result.home_prob < 1.0
        assert result.home_prob + result.away_prob == pytest.approx(1.0)
        assert result.num_books == 3

    def test_returns_none_with_no_lines(self):
        engine = LiveFairValueEngine()
        snap = _make_snapshot(lines=[])
        assert engine.estimate(snap) is None

    def test_returns_none_below_min_books(self):
        engine = LiveFairValueEngine(min_books=3)
        snap = _make_snapshot(lines=[
            _make_line("fanduel", "FanDuel", -150, 130),
        ])
        assert engine.estimate(snap) is None

    def test_home_prob_bounded(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert 0.001 <= result.home_prob <= 0.999


# ── Method selection ─────────────────────────────────────────────────

class TestMethodSelection:
    def test_ensemble_when_pinnacle_plus_others(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert result.method == "live_ensemble"

    def test_pinnacle_only_when_no_others(self):
        engine = LiveFairValueEngine(min_books=1)
        snap = _make_snapshot(lines=[
            _make_line("pinnacle", "Pinnacle", -150, 130),
        ])
        result = engine.estimate(snap)
        assert result.method == "live_pinnacle"

    def test_consensus_only_when_no_pinnacle(self):
        engine = LiveFairValueEngine()
        snap = _make_snapshot(lines=[
            _make_line("fanduel", "FanDuel", -150, 130),
            _make_line("draftkings", "DraftKings", -148, 128),
            _make_line("betmgm", "BetMGM", -152, 132),
        ])
        result = engine.estimate(snap)
        assert result.method == "live_consensus"

    def test_ensemble_blends_pinnacle_and_consensus(self):
        """Ensemble should be between Pinnacle-only and consensus-only."""
        engine = LiveFairValueEngine()
        snap_all = _make_snapshot()
        result_all = engine.estimate(snap_all)

        # Pinnacle only
        engine2 = LiveFairValueEngine(min_books=1)
        snap_pin = _make_snapshot(lines=[
            _make_line("pinnacle", "Pinnacle", -150, 130),
        ])
        result_pin = engine2.estimate(snap_pin)

        # Consensus only
        snap_cons = _make_snapshot(lines=[
            _make_line("fanduel", "FanDuel", -155, 135),
            _make_line("draftkings", "DraftKings", -148, 128),
        ])
        result_cons = engine.estimate(snap_cons)

        # Ensemble should be somewhere between
        if result_pin and result_cons:
            low = min(result_pin.home_prob, result_cons.home_prob)
            high = max(result_pin.home_prob, result_cons.home_prob)
            assert low - 0.01 <= result_all.home_prob <= high + 0.01


# ── Stale line handling ──────────────────────────────────────────────

class TestStaleLines:
    def test_stale_lines_excluded(self):
        """Lines older than threshold should be excluded."""
        engine = LiveFairValueEngine(stale_threshold_seconds=60.0, min_books=2)
        snap = _make_snapshot(
            lines=[
                _make_line("pinnacle", "Pinnacle", -150, 130, last_update="2026-03-28T20:59:30+00:00"),
                _make_line("fanduel", "FanDuel", -200, 180, last_update="2026-03-28T20:50:00+00:00"),  # stale (10 min old)
                _make_line("draftkings", "DraftKings", -148, 128, last_update="2026-03-28T20:59:50+00:00"),
            ],
            poll_ts="2026-03-28T21:00:00+00:00",
        )
        result = engine.estimate(snap)
        assert result is not None
        assert result.stale_books == 1
        assert result.num_books == 2  # fanduel excluded

    def test_all_stale_returns_none(self):
        engine = LiveFairValueEngine(stale_threshold_seconds=60.0, min_books=2)
        snap = _make_snapshot(
            lines=[
                _make_line("pinnacle", "Pinnacle", -150, 130, last_update="2026-03-28T20:50:00+00:00"),
                _make_line("fanduel", "FanDuel", -155, 135, last_update="2026-03-28T20:50:00+00:00"),
            ],
            poll_ts="2026-03-28T21:00:00+00:00",
        )
        result = engine.estimate(snap)
        assert result is None


# ── Pinnacle weighting ───────────────────────────────────────────────

class TestPinnacleWeight:
    def test_pinnacle_gets_higher_weight_in_consensus(self):
        """Pinnacle's line should pull the consensus toward it."""
        engine = LiveFairValueEngine(pinnacle_weight=5.0)
        # Pinnacle: -150/130 (~60% home). Others: -200/180 (~53% home)
        snap = _make_snapshot(lines=[
            _make_line("pinnacle", "Pinnacle", -150, 130),
            _make_line("fanduel", "FanDuel", -200, 180),
            _make_line("draftkings", "DraftKings", -200, 180),
        ])
        result = engine.estimate(snap)
        pin_home = american_to_prob(-150) / (american_to_prob(-150) + american_to_prob(130))
        # Result should be closer to Pinnacle than to others
        assert abs(result.home_prob - pin_home) < 0.05


# ── Confidence scoring ───────────────────────────────────────────────

class TestConfidence:
    def test_baseline_confidence(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert 0.0 <= result.confidence <= 1.0

    def test_fewer_books_lower_confidence(self):
        engine = LiveFairValueEngine(min_books=1)
        snap_many = _make_snapshot()
        snap_few = _make_snapshot(lines=[
            _make_line("pinnacle", "Pinnacle", -150, 130),
            _make_line("fanduel", "FanDuel", -155, 135),
        ])
        result_many = engine.estimate(snap_many)
        result_few = engine.estimate(snap_few)
        assert result_many.confidence >= result_few.confidence

    def test_no_pinnacle_lower_confidence(self):
        engine = LiveFairValueEngine()
        snap_with_pin = _make_snapshot()
        snap_no_pin = _make_snapshot(lines=[
            _make_line("fanduel", "FanDuel", -150, 130),
            _make_line("draftkings", "DraftKings", -148, 128),
            _make_line("betmgm", "BetMGM", -152, 132),
        ])
        r1 = engine.estimate(snap_with_pin)
        r2 = engine.estimate(snap_no_pin)
        assert r1.confidence >= r2.confidence

    def test_game_progress_increases_confidence(self):
        engine = LiveFairValueEngine()
        snap = _make_snapshot()

        gs_early = _make_game_state(period=1)
        gs_late = _make_game_state(period=4)

        r_early = engine.estimate(snap, gs_early)
        r_late = engine.estimate(snap, gs_late)
        assert r_late.confidence >= r_early.confidence

    def test_blowout_increases_confidence(self):
        engine = LiveFairValueEngine()
        snap = _make_snapshot()

        gs_close = _make_game_state(home_score=85, away_score=83)  # close
        gs_blowout = _make_game_state(home_score=110, away_score=78)  # blowout

        r_close = engine.estimate(snap, gs_close)
        r_blowout = engine.estimate(snap, gs_blowout)
        assert r_blowout.confidence >= r_close.confidence

    def test_stale_lines_reduce_confidence(self):
        engine = LiveFairValueEngine(stale_threshold_seconds=60.0)
        snap_fresh = _make_snapshot(
            lines=[
                _make_line("pinnacle", "Pinnacle", -150, 130, "2026-03-28T20:59:50+00:00"),
                _make_line("fanduel", "FanDuel", -155, 135, "2026-03-28T20:59:50+00:00"),
                _make_line("draftkings", "DraftKings", -148, 128, "2026-03-28T20:59:50+00:00"),
            ],
            poll_ts="2026-03-28T21:00:00+00:00",
        )
        snap_stale = _make_snapshot(
            lines=[
                _make_line("pinnacle", "Pinnacle", -150, 130, "2026-03-28T20:59:50+00:00"),
                _make_line("fanduel", "FanDuel", -155, 135, "2026-03-28T20:50:00+00:00"),  # stale
                _make_line("draftkings", "DraftKings", -148, 128, "2026-03-28T20:59:50+00:00"),
            ],
            poll_ts="2026-03-28T21:00:00+00:00",
        )
        r_fresh = engine.estimate(snap_fresh)
        r_stale = engine.estimate(snap_stale)
        assert r_fresh.confidence >= r_stale.confidence


# ── Devig ────────────────────────────────────────────────────────────

class TestDevig:
    def test_devig_produces_valid_probability(self):
        engine = LiveFairValueEngine()
        line = _make_line("test", "Test", -150, 130)
        home_p = engine._devig_home(line)
        assert home_p is not None
        assert 0.0 < home_p < 1.0

    def test_devig_symmetric_odds(self):
        """Even money should devig to ~50%."""
        engine = LiveFairValueEngine()
        line = _make_line("test", "Test", -110, -110)
        home_p = engine._devig_home(line)
        assert home_p == pytest.approx(0.5, abs=0.01)

    def test_devig_heavy_favorite(self):
        engine = LiveFairValueEngine()
        line = _make_line("test", "Test", -300, 250)
        home_p = engine._devig_home(line)
        assert home_p > 0.7


# ── LiveFairValueResult ──────────────────────────────────────────────

class TestResult:
    def test_home_away_sum_to_one(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert result.home_prob + result.away_prob == pytest.approx(1.0)

    def test_pinnacle_home_populated(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert result.pinnacle_home is not None
        assert 0.0 < result.pinnacle_home < 1.0

    def test_consensus_home_populated(self):
        engine = LiveFairValueEngine()
        result = engine.estimate(_make_snapshot())
        assert result.consensus_home is not None
