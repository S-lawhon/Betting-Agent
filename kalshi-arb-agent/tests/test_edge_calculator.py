"""Tests for edge_calculator module."""

import pytest
from datetime import datetime, timezone

from src.edge_calculator import (
    calculate_edge,
    compute_edge_for_logging,
    evaluate_opportunity,
    _compute_confidence,
    EdgeResult,
)
from src.utils import (
    KalshiMarket,
    MatchedMarket,
    SportsbookEvent,
    SportsbookOdds,
)


class TestCalculateEdge:
    """Tests for the calculate_edge function."""

    def test_positive_edge_yes_side(self):
        """When sharp probability > Kalshi price, YES has positive edge."""
        result = calculate_edge(
            kalshi_yes_price=0.40,
            sharp_probability=0.50,
            side="yes",
            fee_rate=0.07,
        )
        assert result.edge_pct > 0
        assert result.net_ev > 0
        assert result.side == "yes"

    def test_positive_edge_no_side(self):
        """When sharp probability < Kalshi price, NO has positive edge."""
        result = calculate_edge(
            kalshi_yes_price=0.60,
            sharp_probability=0.40,
            side="no",
            fee_rate=0.07,
        )
        assert result.edge_pct > 0
        assert result.net_ev > 0
        assert result.side == "no"

    def test_no_edge_fair_price(self):
        """When sharp prob equals Kalshi price, edge is ~0 (negative after fees)."""
        result = calculate_edge(
            kalshi_yes_price=0.50,
            sharp_probability=0.50,
            side="yes",
            fee_rate=0.07,
        )
        # Raw edge should be approximately 0
        assert abs(result.edge_pct) < 0.1
        # Net EV should be negative due to fees
        assert result.net_ev < 0

    def test_negative_edge(self):
        """Buying YES when sharp prob is below price gives negative edge."""
        result = calculate_edge(
            kalshi_yes_price=0.60,
            sharp_probability=0.40,
            side="yes",
            fee_rate=0.07,
        )
        assert result.edge_pct < 0
        assert result.net_ev < 0

    def test_fees_reduce_ev(self):
        """Fees should reduce EV compared to fee-free calculation."""
        no_fee = calculate_edge(0.40, 0.55, "yes", fee_rate=0.0)
        with_fee = calculate_edge(0.40, 0.55, "yes", fee_rate=0.07)

        assert with_fee.net_ev < no_fee.net_ev
        assert with_fee.net_edge_pct < no_fee.net_edge_pct

    def test_kelly_positive_when_edge_positive(self):
        """Kelly fraction should be positive when there's positive net edge."""
        result = calculate_edge(0.30, 0.50, "yes", fee_rate=0.07)
        assert result.kelly_fraction > 0

    def test_kelly_zero_when_no_edge(self):
        """Kelly fraction should be 0 when edge is negative."""
        result = calculate_edge(0.60, 0.40, "yes", fee_rate=0.07)
        assert result.kelly_fraction == 0

    def test_extreme_prices_clamped(self):
        """Extreme prices should be clamped to valid range."""
        result = calculate_edge(0.001, 0.50, "yes")
        assert result.edge_pct is not None  # Should not crash

        result = calculate_edge(0.999, 0.50, "yes")
        assert result.edge_pct is not None

    def test_overround_calculation(self):
        """Kalshi overround should be calculated (yes + no - 1)."""
        result = calculate_edge(0.50, 0.55, "yes")
        # yes_price + no_price = 1.0, so overround = 0
        assert abs(result.kalshi_overround) < 0.1

    def test_symmetry_yes_no(self):
        """Edge for YES and NO should be complementary."""
        yes = calculate_edge(0.40, 0.50, "yes", fee_rate=0.0)
        no = calculate_edge(0.40, 0.50, "no", fee_rate=0.0)

        # One should be positive, the other negative (or both small)
        # YES edge = 0.50 - 0.40 = 10%
        # NO edge = 0.50 - 0.60 = -10%
        assert yes.edge_pct > 0
        assert no.edge_pct < 0


class TestEvaluateOpportunity:
    """Tests for the evaluate_opportunity function."""

    def _make_matched_market(
        self,
        yes_price: float = 0.40,
        sharp_prob: float = 0.55,
        num_books: int = 5,
        volume: int = 200000,
    ) -> MatchedMarket:
        km = KalshiMarket(
            ticker="TEST-MARKET-YES",
            title="Test Team to Win",
            subtitle="NBA Game",
            yes_price=yes_price,
            no_price=1.0 - yes_price,
            volume=volume,
            open_interest=1000,
            close_time=datetime(2025, 6, 1, 20, 0, tzinfo=timezone.utc),
        )
        event = SportsbookEvent(
            event_id="test-event-1",
            sport="basketball_nba",
            home_team="Test Team",
            away_team="Other Team",
            commence_time=datetime(2025, 6, 1, 19, 0, tzinfo=timezone.utc),
        )
        return MatchedMarket(
            kalshi_market=km,
            sportsbook_event=event,
            match_type="moneyline",
            match_confidence=0.9,
            kalshi_side="yes",
            sharp_probability=sharp_prob,
            num_books_agreeing=num_books,
        )

    def test_opportunity_found_with_edge(self):
        """Should find opportunity when edge exceeds minimum."""
        mm = self._make_matched_market(yes_price=0.40, sharp_prob=0.55)
        opp = evaluate_opportunity(mm, min_edge_pct=3.0)
        assert opp is not None
        assert opp.edge_pct > 3.0

    def test_no_opportunity_below_min_edge(self):
        """Should not find opportunity when edge is below minimum."""
        mm = self._make_matched_market(yes_price=0.49, sharp_prob=0.50)
        opp = evaluate_opportunity(mm, min_edge_pct=5.0)
        assert opp is None

    def test_skip_above_max_edge(self):
        """Should skip when edge exceeds maximum (likely bad data)."""
        mm = self._make_matched_market(yes_price=0.10, sharp_prob=0.80)
        opp = evaluate_opportunity(mm, min_edge_pct=3.0, max_edge_pct=25.0)
        assert opp is None

    def test_skip_insufficient_books(self):
        """Should skip when not enough sportsbooks agree."""
        mm = self._make_matched_market(num_books=1)
        opp = evaluate_opportunity(mm, min_books=3)
        assert opp is None

    def test_picks_best_side(self):
        """Should pick the side with higher edge."""
        mm = self._make_matched_market(yes_price=0.40, sharp_prob=0.55)
        opp = evaluate_opportunity(mm, min_edge_pct=1.0)
        assert opp is not None
        # YES should be better: sharp_prob (0.55) > yes_price (0.40)
        assert opp.side == "yes"

    def test_confidence_score_set(self):
        """Opportunity should have a non-zero confidence score."""
        mm = self._make_matched_market()
        opp = evaluate_opportunity(mm, min_edge_pct=1.0)
        assert opp is not None
        assert 0 < opp.confidence_score <= 1.0

    def test_invalid_sharp_prob(self):
        """Should return None for invalid sharp probability."""
        mm = self._make_matched_market(sharp_prob=0.0)
        assert evaluate_opportunity(mm) is None

        mm = self._make_matched_market(sharp_prob=1.0)
        assert evaluate_opportunity(mm) is None


class TestComputeEdgeForLogging:
    """Tests for compute_edge_for_logging helper."""

    def test_returns_dict_with_required_keys(self):
        result = compute_edge_for_logging(0.45, 0.55)
        assert "best_side" in result
        assert "net_edge_pct" in result
        assert "kelly_fraction" in result
        assert "kalshi_overround_pct" in result
        assert "yes_raw_edge_pct" in result
        assert "no_raw_edge_pct" in result

    def test_picks_best_side(self):
        # Sharp prob 0.60 vs yes price 0.40 -> YES is better
        result = compute_edge_for_logging(0.40, 0.60)
        assert result["best_side"] == "yes"
        assert result["net_edge_pct"] > 0
