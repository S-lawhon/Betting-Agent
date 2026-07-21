"""Tests for src/momentum_detector.py — MomentumDetector."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta
from src.momentum_detector import (
    MomentumDetector,
    MomentumSignal,
    PriceSnapshot,
    MAX_SNAPSHOTS_PER_GAME,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _ts(seconds_offset=0):
    """Generate ISO timestamp offset from a base time."""
    base = datetime(2026, 3, 28, 21, 0, 0, tzinfo=timezone.utc)
    dt = base + timedelta(seconds=seconds_offset)
    return dt.isoformat()


def _make_detector(**kwargs):
    defaults = dict(
        overreaction_threshold=0.03,
        divergence_threshold=0.05,
        lookback_seconds=120.0,
        min_snapshots=3,
    )
    defaults.update(kwargs)
    return MomentumDetector(**defaults)


def _record_snapshots(detector, game_id, snapshots):
    """Record a list of (ts_offset, kalshi, consensus, hscore, ascore) tuples."""
    for ts_offset, kalshi, consensus, hs, aws in snapshots:
        detector.record_snapshot(
            game_id=game_id,
            kalshi_home_prob=kalshi,
            consensus_home_prob=consensus,
            timestamp=_ts(ts_offset),
            home_score=hs,
            away_score=aws,
        )


# ── PriceSnapshot ────────────────────────────────────────────────────

class TestPriceSnapshot:
    def test_creation(self):
        snap = PriceSnapshot(
            timestamp=_ts(0),
            kalshi_home_prob=0.60,
            consensus_home_prob=0.58,
            home_score=45,
            away_score=40,
        )
        assert snap.kalshi_home_prob == 0.60
        assert snap.consensus_home_prob == 0.58


# ── Basic detection ──────────────────────────────────────────────────

class TestBasicDetection:
    def test_no_signal_with_few_snapshots(self):
        detector = _make_detector(min_snapshots=3)
        detector.record_snapshot("g1", 0.60, 0.58, _ts(0))
        detector.record_snapshot("g1", 0.61, 0.58, _ts(30))
        signals = detector.detect("g1")
        assert signals == []

    def test_no_signal_when_game_not_tracked(self):
        detector = _make_detector()
        assert detector.detect("nonexistent") == []

    def test_no_signal_when_aligned(self):
        """No signal when Kalshi and consensus move together."""
        detector = _make_detector()
        _record_snapshots(detector, "g1", [
            (0, 0.55, 0.55, 50, 48),
            (30, 0.56, 0.56, 52, 48),
            (60, 0.57, 0.57, 54, 50),
            (90, 0.58, 0.58, 56, 50),
        ])
        signals = detector.detect("g1")
        # Should have no overreaction (they moved together)
        overreactions = [s for s in signals if s.signal_type == "OVERREACTION"]
        assert len(overreactions) == 0


# ── Overreaction detection ───────────────────────────────────────────

class TestOverreaction:
    def test_detects_home_overreaction(self):
        """Kalshi moves toward home much more than consensus → buy_away signal."""
        detector = _make_detector(overreaction_threshold=0.03, lookback_seconds=120)
        _record_snapshots(detector, "g1", [
            (0, 0.50, 0.50, 50, 50),
            (30, 0.52, 0.50, 52, 50),
            (60, 0.55, 0.51, 54, 50),
            (120, 0.60, 0.52, 56, 50),  # Kalshi +10%, consensus +2% → overreaction 8%
        ])
        signals = detector.detect("g1")
        overreactions = [s for s in signals if s.signal_type == "OVERREACTION"]
        assert len(overreactions) == 1
        assert overreactions[0].direction == "buy_away"
        assert overreactions[0].overreaction_score > 0.03

    def test_detects_away_overreaction(self):
        """Kalshi drops too much (overvalues away) → buy_home signal."""
        detector = _make_detector(overreaction_threshold=0.03, lookback_seconds=120)
        _record_snapshots(detector, "g1", [
            (0, 0.60, 0.60, 50, 50),
            (30, 0.58, 0.60, 50, 52),
            (60, 0.55, 0.59, 50, 54),
            (120, 0.50, 0.58, 50, 56),  # Kalshi -10%, consensus -2% → overreaction 8% toward away
        ])
        signals = detector.detect("g1")
        overreactions = [s for s in signals if s.signal_type == "OVERREACTION"]
        assert len(overreactions) == 1
        assert overreactions[0].direction == "buy_home"

    def test_below_threshold_no_signal(self):
        detector = _make_detector(overreaction_threshold=0.10)
        _record_snapshots(detector, "g1", [
            (0, 0.50, 0.50, 50, 50),
            (30, 0.51, 0.50, 51, 50),
            (60, 0.52, 0.50, 52, 50),
            (120, 0.54, 0.51, 54, 50),  # Only ~3% overreaction, threshold is 10%
        ])
        signals = detector.detect("g1")
        overreactions = [s for s in signals if s.signal_type == "OVERREACTION"]
        assert len(overreactions) == 0

    def test_overreaction_includes_details(self):
        detector = _make_detector(overreaction_threshold=0.03)
        _record_snapshots(detector, "g1", [
            (0, 0.50, 0.50, 50, 50),
            (60, 0.52, 0.50, 52, 50),
            (120, 0.60, 0.52, 56, 50),
        ])
        signals = detector.detect("g1")
        overreactions = [s for s in signals if s.signal_type == "OVERREACTION"]
        if overreactions:
            sig = overreactions[0]
            assert "kalshi_delta" in sig.details
            assert "consensus_delta" in sig.details
            assert sig.game_id == "g1"


# ── Divergence detection ─────────────────────────────────────────────

class TestDivergence:
    def test_detects_large_divergence(self):
        """Kalshi consistently higher than consensus → buy_away signal."""
        detector = _make_detector(divergence_threshold=0.05)
        _record_snapshots(detector, "g1", [
            (0, 0.65, 0.55, 50, 50),
            (30, 0.66, 0.56, 52, 50),
            (60, 0.67, 0.57, 54, 50),
            (90, 0.68, 0.58, 56, 50),
            (120, 0.68, 0.58, 58, 50),
        ])
        signals = detector.detect("g1")
        divergences = [s for s in signals if s.signal_type == "DIVERGENCE"]
        assert len(divergences) == 1
        assert divergences[0].direction == "buy_away"
        assert abs(divergences[0].divergence) >= 0.05

    def test_detects_negative_divergence(self):
        """Kalshi consistently lower than consensus → buy_home signal."""
        detector = _make_detector(divergence_threshold=0.05)
        _record_snapshots(detector, "g1", [
            (0, 0.45, 0.55, 50, 50),
            (30, 0.44, 0.54, 50, 52),
            (60, 0.43, 0.53, 50, 54),
            (90, 0.42, 0.52, 50, 56),
            (120, 0.42, 0.52, 50, 58),
        ])
        signals = detector.detect("g1")
        divergences = [s for s in signals if s.signal_type == "DIVERGENCE"]
        assert len(divergences) == 1
        assert divergences[0].direction == "buy_home"

    def test_no_divergence_when_close(self):
        detector = _make_detector(divergence_threshold=0.05)
        _record_snapshots(detector, "g1", [
            (0, 0.55, 0.54, 50, 50),
            (30, 0.56, 0.55, 52, 50),
            (60, 0.57, 0.56, 54, 50),
            (90, 0.58, 0.57, 56, 50),
        ])
        signals = detector.detect("g1")
        divergences = [s for s in signals if s.signal_type == "DIVERGENCE"]
        assert len(divergences) == 0

    def test_transient_divergence_filtered(self):
        """A single divergent snapshot shouldn't trigger if avg is low."""
        detector = _make_detector(divergence_threshold=0.05)
        _record_snapshots(detector, "g1", [
            (0, 0.55, 0.55, 50, 50),
            (30, 0.55, 0.55, 52, 50),
            (60, 0.55, 0.55, 54, 50),
            (90, 0.55, 0.55, 56, 50),
            (120, 0.65, 0.55, 58, 50),  # single spike
        ])
        signals = detector.detect("g1")
        divergences = [s for s in signals if s.signal_type == "DIVERGENCE"]
        # Average divergence over last 5 is only ~2%, well below threshold * 0.5
        assert len(divergences) == 0


# ── Both signals at once ─────────────────────────────────────────────

class TestCombinedSignals:
    def test_can_return_both_overreaction_and_divergence(self):
        """A game with both sudden overreaction and persistent divergence."""
        detector = _make_detector(
            overreaction_threshold=0.03,
            divergence_threshold=0.05,
            lookback_seconds=120,
        )
        _record_snapshots(detector, "g1", [
            (0, 0.55, 0.50, 50, 50),   # already diverged
            (30, 0.57, 0.50, 52, 50),
            (60, 0.60, 0.50, 54, 50),
            (90, 0.63, 0.51, 56, 50),
            (120, 0.68, 0.52, 58, 50),  # big jump + persistent gap
        ])
        signals = detector.detect("g1")
        types = {s.signal_type for s in signals}
        # Should have both OVERREACTION and DIVERGENCE
        assert "OVERREACTION" in types
        assert "DIVERGENCE" in types


# ── History management ───────────────────────────────────────────────

class TestHistoryManagement:
    def test_get_history(self):
        detector = _make_detector()
        detector.record_snapshot("g1", 0.60, 0.58, _ts(0))
        detector.record_snapshot("g1", 0.61, 0.59, _ts(30))
        history = detector.get_history("g1")
        assert len(history) == 2
        assert history[0].kalshi_home_prob == 0.60

    def test_clear_game(self):
        detector = _make_detector()
        detector.record_snapshot("g1", 0.60, 0.58, _ts(0))
        detector.clear_game("g1")
        assert detector.get_history("g1") == []

    def test_clear_nonexistent_game_no_error(self):
        detector = _make_detector()
        detector.clear_game("nonexistent")  # should not raise

    def test_tracked_games_count(self):
        detector = _make_detector()
        detector.record_snapshot("g1", 0.60, 0.58, _ts(0))
        detector.record_snapshot("g2", 0.55, 0.53, _ts(0))
        assert detector.tracked_games == 2

    def test_max_snapshots_per_game(self):
        detector = _make_detector()
        for i in range(MAX_SNAPSHOTS_PER_GAME + 50):
            detector.record_snapshot("g1", 0.60, 0.58, _ts(i))
        history = detector.get_history("g1")
        assert len(history) == MAX_SNAPSHOTS_PER_GAME

    def test_detect_all(self):
        detector = _make_detector(divergence_threshold=0.05)
        # Game 1: has divergence
        _record_snapshots(detector, "g1", [
            (0, 0.65, 0.55, 50, 50),
            (30, 0.66, 0.55, 52, 50),
            (60, 0.67, 0.55, 54, 50),
            (90, 0.68, 0.55, 56, 50),
        ])
        # Game 2: no signal
        _record_snapshots(detector, "g2", [
            (0, 0.50, 0.50, 50, 50),
            (30, 0.50, 0.50, 50, 50),
            (60, 0.50, 0.50, 50, 50),
            (90, 0.50, 0.50, 50, 50),
        ])
        results = detector.detect_all()
        assert "g1" in results
        assert "g2" not in results


# ── Time helpers ─────────────────────────────────────────────────────

class TestTimeHelpers:
    def test_time_delta_seconds(self):
        detector = _make_detector()
        ts1 = "2026-03-28T21:00:00+00:00"
        ts2 = "2026-03-28T21:02:00+00:00"
        assert detector._time_delta_seconds(ts1, ts2) == pytest.approx(120.0)

    def test_time_delta_invalid_timestamp(self):
        detector = _make_detector()
        assert detector._time_delta_seconds("bad", "also_bad") == 0.0

    def test_time_delta_with_z_suffix(self):
        detector = _make_detector()
        ts1 = "2026-03-28T21:00:00Z"
        ts2 = "2026-03-28T21:01:00Z"
        assert detector._time_delta_seconds(ts1, ts2) == pytest.approx(60.0)

    def test_reference_snapshot_requires_min_age(self):
        """Reference snapshot must be at least 10 seconds old."""
        detector = _make_detector()
        detector.record_snapshot("g1", 0.60, 0.58, _ts(0))
        detector.record_snapshot("g1", 0.61, 0.58, _ts(1))  # only 1s apart
        detector.record_snapshot("g1", 0.62, 0.58, _ts(2))  # only 2s apart
        # All snapshots are within 2s of each other — reference should need >10s
        history = detector._history["g1"]
        ref = detector._find_reference_snapshot(history)
        # The best match for 120s lookback is 0s offset — but that's too close
        # So ref should be None since none is >10s old
        assert ref is None


# ── MomentumSignal ───────────────────────────────────────────────────

class TestMomentumSignal:
    def test_signal_fields(self):
        sig = MomentumSignal(
            signal_type="OVERREACTION",
            game_id="g1",
            direction="buy_away",
            kalshi_home_prob=0.65,
            consensus_home_prob=0.55,
            divergence=0.10,
            overreaction_score=0.08,
            lookback_seconds=120.0,
            details={"kalshi_delta": 0.10, "consensus_delta": 0.02},
        )
        assert sig.signal_type == "OVERREACTION"
        assert sig.direction == "buy_away"
        assert sig.overreaction_score == 0.08
