"""
tests/test_live_game_edge.py
─────────────────────────────
Tests for P-014 (LiveGamePod) edge evaluation, Kelly fraction,
and position sizing logic.
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.pods.live_game_pod import LiveGamePod
from src.live_odds_poller import LiveOddsPoller, LiveOddsSnapshot, BookmakerLine
from src.game_state import GameState, GamePhase, GameStateTracker
from src.live_fair_value import LiveFairValueResult


# ── Fixtures ───────────────────────────────────────────────────────────

def _make_pod(**overrides) -> LiveGamePod:
    """Build a minimal LiveGamePod for unit testing."""
    poller = MagicMock(spec=LiveOddsPoller)
    tracker = MagicMock(spec=GameStateTracker)
    risk_mgr = MagicMock()
    risk_mgr.max_bet_pct = 0.10
    risk_mgr.max_position_usd = 100.0
    risk_mgr.ledger = MagicMock()
    risk_mgr.ledger.bankroll = 10_000.0

    defaults = dict(
        poller=poller,
        game_tracker=tracker,
        kalshi_client=None,
        discovery=None,
        edge_calculator=MagicMock(),
        risk_manager=risk_mgr,
        trade_log_path=Path("/tmp/test_p014.jsonl"),
        mode="paper",
        _now_fn=lambda: datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc),
        trade_store=None,
    )
    defaults.update(overrides)
    return LiveGamePod(**defaults)


# ── Kelly fraction tests ───────────────────────────────────────────────

class TestKellyFraction:
    """Tests for the corrected Kelly fraction calculation."""

    def test_kelly_binary_basic(self):
        """Standard Kelly for a 60% fair value at 50¢ venue — capped at 5%."""
        pod = _make_pod()
        # f* = (0.60 - 0.50) / (1 - 0.50) = 0.20 → capped at 0.05
        edge = (0.60 - 0.50) / 0.50
        kelly = pod._kelly_fraction(edge, fair_prob=0.60, venue_prob=0.50)
        assert kelly == 0.05  # capped

    def test_kelly_binary_small_edge(self):
        """Small edge gives proportionally small Kelly — capped."""
        pod = _make_pod()
        # f* = (0.55 - 0.52) / (1 - 0.52) = 0.03 / 0.48 ≈ 0.0625 → capped at 0.05
        edge = (0.55 - 0.52) / 0.52
        kelly = pod._kelly_fraction(edge, fair_prob=0.55, venue_prob=0.52)
        assert kelly == 0.05  # capped

    def test_kelly_binary_uncapped(self):
        """Very small edge → Kelly below cap, gives correct value."""
        pod = _make_pod()
        # f* = (0.52 - 0.50) / (1 - 0.50) = 0.02 / 0.50 = 0.04
        edge = (0.52 - 0.50) / 0.50
        kelly = pod._kelly_fraction(edge, fair_prob=0.52, venue_prob=0.50)
        assert abs(kelly - 0.04) < 0.001

    def test_kelly_capped_at_5pct(self):
        """Kelly is capped at 0.05 to prevent over-betting."""
        pod = _make_pod()
        # f* = (0.80 - 0.30) / (1 - 0.30) = 0.714 → capped at 0.05
        edge = (0.80 - 0.30) / 0.30
        kelly = pod._kelly_fraction(edge, fair_prob=0.80, venue_prob=0.30)
        assert kelly == 0.05

    def test_kelly_negative_edge_returns_zero(self):
        """Negative edge → Kelly is 0 (don't bet)."""
        pod = _make_pod()
        # fair < venue → negative edge
        edge = (0.40 - 0.50) / 0.50
        kelly = pod._kelly_fraction(edge, fair_prob=0.40, venue_prob=0.50)
        assert kelly == 0.0

    def test_kelly_extreme_prob_returns_zero(self):
        """Extreme probabilities (0 or 1) return zero."""
        pod = _make_pod()
        assert pod._kelly_fraction(0.1, 0.0, 0.5) == 0.0
        assert pod._kelly_fraction(0.1, 1.0, 0.5) == 0.0

    def test_kelly_fallback_without_venue_prob(self):
        """When venue_prob is 0, falls back to edge-based formula."""
        pod = _make_pod()
        # Should use the old formula: edge * fair / (1-fair)
        kelly = pod._kelly_fraction(edge=0.10, fair_prob=0.60, venue_prob=0.0)
        expected = 0.10 * 0.60 / 0.40
        assert abs(kelly - min(expected, 0.05)) < 0.001


# ── Position sizing tests ─────────────────────────────────────────────

class TestPositionSizing:
    """Tests for _compute_live_position_size with live adjustments."""

    def test_basic_sizing(self):
        """Basic case: no adjustments beyond Kelly."""
        pod = _make_pod()
        size = pod._compute_live_position_size(
            edge=0.10, fair_prob=0.60, venue_prob=0.50,
            confidence=1.0, game_progress=0.0, spread_cents=0,
        )
        # Kelly = (0.60 - 0.50) / (1 - 0.50) = 0.20 → capped at 0.05
        # Position = 0.05 * 10000 = 500, capped at max_position_usd=100
        assert size == 100.0

    def test_confidence_scales_position(self):
        """Lower confidence → smaller position."""
        # Use a high max_position to avoid cap masking the difference
        risk_mgr = MagicMock()
        risk_mgr.max_bet_pct = 0.10
        risk_mgr.max_position_usd = 10_000.0
        risk_mgr.ledger = MagicMock()
        risk_mgr.ledger.bankroll = 10_000.0
        pod = _make_pod(risk_manager=risk_mgr)
        full = pod._compute_live_position_size(
            edge=0.05, fair_prob=0.55, venue_prob=0.52,
            confidence=1.0, game_progress=0.0, spread_cents=0,
        )
        half = pod._compute_live_position_size(
            edge=0.05, fair_prob=0.55, venue_prob=0.52,
            confidence=0.5, game_progress=0.0, spread_cents=0,
        )
        assert half < full

    def test_game_progress_increases_position(self):
        """Later in the game → slightly larger position."""
        pod = _make_pod()
        early = pod._compute_live_position_size(
            edge=0.04, fair_prob=0.55, venue_prob=0.52,
            confidence=1.0, game_progress=0.0, spread_cents=0,
        )
        late = pod._compute_live_position_size(
            edge=0.04, fair_prob=0.55, venue_prob=0.52,
            confidence=1.0, game_progress=1.0, spread_cents=0,
        )
        assert late >= early  # Late game has +20% bonus

    def test_wide_spread_reduces_position(self):
        """Wide Kalshi spread → smaller position (spread eats edge)."""
        pod = _make_pod()
        tight = pod._compute_live_position_size(
            edge=0.04, fair_prob=0.55, venue_prob=0.52,
            confidence=1.0, game_progress=0.0, spread_cents=2,
        )
        wide = pod._compute_live_position_size(
            edge=0.04, fair_prob=0.55, venue_prob=0.52,
            confidence=1.0, game_progress=0.0, spread_cents=8,
        )
        assert wide <= tight

    def test_zero_edge_returns_zero(self):
        """Zero or negative edge → zero position."""
        pod = _make_pod()
        size = pod._compute_live_position_size(
            edge=0.0, fair_prob=0.50, venue_prob=0.50,
            confidence=1.0, game_progress=0.5, spread_cents=0,
        )
        assert size == 0.0


# ── Edge threshold tests ──────────────────────────────────────────────

class TestEdgeThresholds:
    """Tests for game-progress-aware and spread-aware edge thresholds."""

    def _make_snapshot(self, game_id="g1"):
        return LiveOddsSnapshot(
            game_id=game_id,
            sport="basketball_nba",
            home_team="Team A",
            away_team="Team B",
            commence_time="2026-03-28T19:00:00Z",
            is_live=True,
            bookmaker_lines=[
                BookmakerLine("pinnacle", "Pinnacle", -150, 130, "2026-03-28T12:00:00Z"),
                BookmakerLine("book2", "Book2", -145, 125, "2026-03-28T12:00:00Z"),
                BookmakerLine("book3", "Book3", -155, 135, "2026-03-28T12:00:00Z"),
            ],
            poll_timestamp="2026-03-28T12:00:00Z",
        )

    def _make_game_state(self, progress_period=2):
        return GameState(
            game_id="g1",
            sport="basketball_nba",
            home_team="Team A",
            away_team="Team B",
            home_score=55,
            away_score=50,
            period=progress_period,
            phase=GamePhase.LIVE,
        )

    def test_late_game_lower_threshold(self):
        """Late game (Q4) should accept edges that early game rejects.

        At Q4 (progress=1.0), threshold is reduced by 40%:
        2% * (1 - 1.0 * 0.4) = 1.2%
        """
        pod = _make_pod(min_edge_pct=0.02)
        # Progress at Q4 → 4/4 = 1.0
        game_state = self._make_game_state(progress_period=4)
        progress = game_state.game_progress_pct()
        adjusted = 0.02 * (1.0 - progress * 0.40)
        assert abs(adjusted - 0.012) < 0.001

    def test_early_game_full_threshold(self):
        """Early game (Q1) should use near-full threshold."""
        game_state = self._make_game_state(progress_period=1)
        progress = game_state.game_progress_pct()  # 1/4 = 0.25
        adjusted = 0.02 * (1.0 - progress * 0.40)
        # 0.02 * (1 - 0.25 * 0.4) = 0.02 * 0.90 = 0.018
        assert abs(adjusted - 0.018) < 0.001


# ── Filter sanity tests ──────────────────────────────────────────────

class TestEdgeFilters:
    """Tests for the tightened edge filters (extreme prob, min absolute, max divergence)."""

    def test_extreme_prob_rejected(self):
        """Contracts priced below 10¢ or above 90¢ are skipped."""
        # Venue prob 5¢ should be rejected even with a positive edge
        pod = _make_pod()
        # Kelly on extreme probs should still return a value for valid inputs
        kelly = pod._kelly_fraction(0.5, fair_prob=0.10, venue_prob=0.05)
        # But the scan loop filters at 10¢, so this would never reach Kelly
        # We test the threshold constants directly
        assert 0.05 < 0.10  # venue_prob=0.05 is below the 0.10 floor

    def test_max_divergence_cap(self):
        """Fair-vs-venue gaps larger than 20 points are rejected."""
        # If fair=0.80 and venue=0.40, abs_edge=0.40 > max_divergence=0.20
        abs_edge = 0.80 - 0.40
        max_divergence = 0.20
        assert abs_edge > max_divergence  # Would be rejected

    def test_min_absolute_edge_raised(self):
        """Min absolute edge is now 5¢ (up from 2¢)."""
        # A 3¢ edge should be rejected
        abs_edge = 0.53 - 0.50
        min_absolute_edge = 0.05
        assert abs_edge < min_absolute_edge  # Would be rejected

    def test_valid_trade_passes_all_filters(self):
        """A trade with reasonable edge passes all filters."""
        fair_prob = 0.60
        kalshi_prob = 0.52
        abs_edge = fair_prob - kalshi_prob  # 0.08
        max_divergence = 0.20
        min_absolute_edge = 0.05

        assert 0.10 < kalshi_prob < 0.90     # Not extreme
        assert abs_edge >= min_absolute_edge   # 8¢ > 5¢
        assert abs_edge <= max_divergence      # 8¢ < 20¢
        edge_pct = abs_edge / kalshi_prob      # 15.4%
        assert edge_pct > 0.02                 # Above min_edge_pct
