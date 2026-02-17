"""Tests for risk_manager module."""

import time
import pytest

from src.risk_manager import RiskManager, RiskState
from src.utils import (
    KalshiMarket,
    MatchedMarket,
    SportsbookEvent,
    TradeOpportunity,
    TradeRecord,
)
from datetime import datetime, timezone


def make_opportunity(
    ticker: str = "TEST-001",
    size: float = 100.0,
    edge: float = 5.0,
    kelly: float = 0.05,
    confidence: float = 0.7,
    home_team: str = "Team A",
    away_team: str = "Team B",
) -> TradeOpportunity:
    km = KalshiMarket(
        ticker=ticker,
        title="Test Market",
        subtitle="",
        yes_price=0.50,
        no_price=0.50,
        volume=200000,
        open_interest=1000,
    )
    event = SportsbookEvent(
        event_id="test-1",
        sport="basketball_nba",
        home_team=home_team,
        away_team=away_team,
        commence_time=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    mm = MatchedMarket(
        kalshi_market=km,
        sportsbook_event=event,
        match_type="moneyline",
        match_confidence=0.9,
        kalshi_side="yes",
        sharp_probability=0.55,
        num_books_agreeing=5,
    )
    return TradeOpportunity(
        matched_market=mm,
        side="yes",
        edge_pct=edge,
        expected_value=0.05,
        kelly_fraction=kelly,
        confidence_score=confidence,
        recommended_size_usd=size,
    )


def make_trade_record(
    ticker: str = "TEST-001",
    pnl: float = -50.0,
    outcome: str = "loss",
) -> TradeRecord:
    return TradeRecord(
        trade_id="t-001",
        timestamp=datetime.now(timezone.utc).isoformat(),
        ticker=ticker,
        sport="basketball_nba",
        market_type="moneyline",
        teams="Team A vs Team B",
        event_time=datetime(2025, 6, 1, tzinfo=timezone.utc).isoformat(),
        side="yes",
        kalshi_price_at_entry=0.50,
        sharp_prob_at_entry=0.55,
        edge_at_entry=5.0,
        position_size_usd=100.0,
        kalshi_volume=200000,
        num_books=5,
        time_to_event_hours=24.0,
        line_movement=0.0,
        order_type="paper",
        mode="paper",
        outcome=outcome,
        pnl=pnl,
    )


class TestCanTrade:
    """Tests for the can_trade gate."""

    def test_allows_trade_within_limits(self):
        rm = RiskManager()
        opp = make_opportunity()
        can, reason = rm.can_trade(opp)
        assert can is True
        assert "passed" in reason.lower()

    def test_blocks_at_max_positions(self):
        rm = RiskManager(max_positions=2)
        rm._state.open_position_count = 2
        rm._state.open_tickers = ["A", "B"]

        opp = make_opportunity(ticker="C")
        can, reason = rm.can_trade(opp)
        assert can is False
        assert "Max positions" in reason

    def test_blocks_duplicate_ticker(self):
        rm = RiskManager()
        rm._state.open_tickers = ["TEST-001"]
        rm._state.open_position_count = 1

        opp = make_opportunity(ticker="TEST-001")
        can, reason = rm.can_trade(opp)
        assert can is False
        assert "Already have position" in reason


class TestDailyLoss:
    """Tests for daily loss limit enforcement."""

    @staticmethod
    def _set_today(rm: RiskManager) -> None:
        """Mark today's date so _check_daily_reset doesn't clear state."""
        rm._daily_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def test_allows_below_limit(self):
        rm = RiskManager(max_daily_loss_usd=500)
        self._set_today(rm)
        rm._state.daily_pnl = -100
        can, _ = rm.check_daily_loss()
        assert can is True

    def test_blocks_at_limit(self):
        rm = RiskManager(max_daily_loss_usd=500)
        self._set_today(rm)
        rm._state.daily_pnl = -500
        can, reason = rm.check_daily_loss()
        assert can is False
        assert "Daily loss limit" in reason

    def test_blocks_above_limit(self):
        rm = RiskManager(max_daily_loss_usd=500)
        self._set_today(rm)
        rm._state.daily_pnl = -600
        can, _ = rm.check_daily_loss()
        assert can is False

    def test_record_updates_daily_pnl(self):
        rm = RiskManager()
        self._set_today(rm)
        trade = make_trade_record(pnl=-50)
        rm.record_trade_result(trade)
        assert rm._state.daily_pnl == -50

        trade2 = make_trade_record(pnl=-100)
        rm.record_trade_result(trade2)
        assert rm._state.daily_pnl == -150


class TestCooldown:
    """Tests for cooldown after loss."""

    def test_no_cooldown_without_loss(self):
        rm = RiskManager(cooldown_after_loss_minutes=30)
        can, _ = rm.check_cooldown()
        assert can is True

    def test_cooldown_after_loss(self):
        rm = RiskManager(cooldown_after_loss_minutes=30)
        rm._state.last_loss_time = time.time()  # Just lost
        can, reason = rm.check_cooldown()
        assert can is False
        assert "Cooldown" in reason

    def test_cooldown_expires(self):
        rm = RiskManager(cooldown_after_loss_minutes=1)
        rm._state.last_loss_time = time.time() - 120  # Lost 2 min ago
        can, _ = rm.check_cooldown()
        assert can is True


class TestTotalExposure:
    """Tests for total exposure limit."""

    def test_allows_within_limit(self):
        rm = RiskManager(max_total_exposure_usd=5000)
        rm._state.total_exposure_usd = 1000
        can, _ = rm.check_total_exposure(500)
        assert can is True

    def test_blocks_exceeding_limit(self):
        rm = RiskManager(max_total_exposure_usd=5000)
        rm._state.total_exposure_usd = 4800
        can, reason = rm.check_total_exposure(500)
        assert can is False
        assert "exposure" in reason.lower()


class TestPositionSizing:
    """Tests for position size calculation."""

    def test_basic_sizing(self):
        rm = RiskManager(max_position_size_usd=500, fractional_kelly=0.25)
        opp = make_opportunity(kelly=0.10, confidence=0.8)
        size = rm.calculate_position_size(opp, bankroll=10000)

        # Expected: 10000 * 0.10 * 0.25 * 0.8 = 200
        assert 0 < size <= 500

    def test_capped_at_max(self):
        rm = RiskManager(max_position_size_usd=100)
        opp = make_opportunity(kelly=0.50, confidence=1.0)
        size = rm.calculate_position_size(opp, bankroll=100000)
        assert size <= 100

    def test_minimum_size_enforced(self):
        rm = RiskManager()
        opp = make_opportunity(kelly=0.001, confidence=0.1)
        size = rm.calculate_position_size(opp, bankroll=100)
        # Very small kelly * confidence -> below $5 minimum
        assert size == 0

    def test_drawdown_reduces_size(self):
        rm = RiskManager(
            max_position_size_usd=500,
            drawdown_reduction_threshold=0.10,
            drawdown_size_multiplier=0.50,
        )
        rm._state.drawdown_pct = 0.15  # 15% drawdown

        opp = make_opportunity(kelly=0.10, confidence=0.8)
        size = rm.calculate_position_size(opp, bankroll=10000)

        # With drawdown multiplier 0.5: 10000 * 0.10 * 0.25 * 0.8 * 0.5 = 100
        assert size < 200


class TestRiskSummary:
    """Tests for the risk summary report."""

    def test_summary_has_required_keys(self):
        rm = RiskManager()
        summary = rm.get_risk_summary()

        assert "total_exposure_usd" in summary
        assert "max_exposure_usd" in summary
        assert "open_positions" in summary
        assert "daily_pnl" in summary
        assert "is_cooldown" in summary

    def test_exposure_utilization(self):
        rm = RiskManager(max_total_exposure_usd=5000)
        rm._state.total_exposure_usd = 2500
        summary = rm.get_risk_summary()
        assert summary["exposure_utilization_pct"] == 50.0


class TestCorrelation:
    """Tests for correlation checking."""

    def test_warns_on_same_team(self):
        rm = RiskManager()
        rm._state.open_tickers = ["NBA-LAKERS-WIN"]
        rm._state.open_position_count = 1

        opp = make_opportunity(
            ticker="NBA-LAKERS-SPREAD",
            home_team="Los Angeles Lakers",
            away_team="Boston Celtics",
        )
        can, _ = rm.check_correlation(opp)
        # Should still allow but log warning (only blocks at 2+ correlations)
        assert can is True

    def test_allows_different_teams(self):
        rm = RiskManager()
        rm._state.open_tickers = ["NBA-WARRIORS-WIN"]
        rm._state.open_position_count = 1

        opp = make_opportunity(
            ticker="NBA-CELTICS-WIN",
            home_team="Boston Celtics",
            away_team="Miami Heat",
        )
        can, _ = rm.check_correlation(opp)
        assert can is True
