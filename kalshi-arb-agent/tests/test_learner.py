"""Tests for learner module."""

import json
import os
import tempfile
import pytest

from src.learner import Learner, BayesianPrior, RollingStats
from src.utils import TradeRecord
from datetime import datetime, timezone


def make_trade(
    sport: str = "basketball_nba",
    market_type: str = "moneyline",
    outcome: str = "win",
    edge: float = 5.0,
    pnl: float = 50.0,
) -> TradeRecord:
    return TradeRecord(
        trade_id="t-001",
        timestamp=datetime.now(timezone.utc).isoformat(),
        ticker="TEST-001",
        sport=sport,
        market_type=market_type,
        teams="Team A vs Team B",
        event_time=datetime(2025, 6, 1, tzinfo=timezone.utc).isoformat(),
        side="yes",
        kalshi_price_at_entry=0.45,
        sharp_prob_at_entry=0.55,
        edge_at_entry=edge,
        position_size_usd=100.0,
        kalshi_volume=200000,
        num_books=5,
        time_to_event_hours=24.0,
        line_movement=0.0,
        order_type="paper",
        mode="paper",
        outcome=outcome,
        pnl=pnl,
        hour_of_day=14,
    )


class TestBayesianPrior:
    """Tests for BayesianPrior data class."""

    def test_initial_prior(self):
        bp = BayesianPrior()
        assert bp.alpha == 1.0
        assert bp.beta == 1.0
        assert bp.mean == 0.5

    def test_update_win(self):
        bp = BayesianPrior()
        bp.update(won=True)
        assert bp.alpha == 2.0
        assert bp.beta == 1.0
        assert bp.mean > 0.5

    def test_update_loss(self):
        bp = BayesianPrior()
        bp.update(won=False)
        assert bp.alpha == 1.0
        assert bp.beta == 2.0
        assert bp.mean < 0.5

    def test_serialization(self):
        bp = BayesianPrior(alpha=5.0, beta=3.0, total_trades=8)
        d = bp.to_dict()
        bp2 = BayesianPrior.from_dict(d)
        assert bp2.alpha == 5.0
        assert bp2.beta == 3.0
        assert bp2.total_trades == 8

    def test_win_rate_convergence(self):
        """With many wins, win rate should approach 1."""
        bp = BayesianPrior()
        for _ in range(100):
            bp.update(won=True)
        assert bp.win_rate > 0.95


class TestRollingStats:
    """Tests for RollingStats tracker."""

    def test_initial_state(self):
        rs = RollingStats()
        assert rs.total == 0
        assert rs.win_rate == 0.5  # Default when no data

    def test_update(self):
        rs = RollingStats()
        rs.update(won=True)
        rs.update(won=False)
        assert rs.wins == 1
        assert rs.losses == 1
        assert rs.win_rate == 0.5

    def test_recent_win_rate(self):
        rs = RollingStats(window_size=10)
        for _ in range(7):
            rs.update(won=True)
        for _ in range(3):
            rs.update(won=False)
        assert rs.recent_win_rate == 0.7

    def test_window_rolls(self):
        rs = RollingStats(window_size=5)
        # Add 5 losses
        for _ in range(5):
            rs.update(won=False)
        assert rs.recent_win_rate == 0.0

        # Add 5 wins — last 5 entries are all wins
        for _ in range(5):
            rs.update(won=True)
        assert rs.recent_win_rate == 1.0  # Last 5 are all wins

    def test_serialization(self):
        rs = RollingStats(window_size=20)
        rs.update(won=True)
        rs.update(won=False)

        d = rs.to_dict()
        rs2 = RollingStats.from_dict(d)
        assert rs2.wins == 1
        assert rs2.losses == 1
        assert rs2.window_size == 20


class TestLearner:
    """Tests for the Learner class."""

    def test_disabled_learner(self):
        learner = Learner(enabled=False)
        adj = learner.get_adjusted_min_edge("basketball_nba", "moneyline", 3.0)
        assert adj == 3.0  # No adjustment when disabled

    def test_record_outcome(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")
            learner = Learner(
                enabled=True,
                method="bayesian",
                model_state_path=state_path,
            )

            trade = make_trade(outcome="win")
            learner.record_outcome(trade)
            assert learner.total_trades == 1

            category = "basketball_nba:moneyline"
            assert category in learner.bayesian_priors
            assert learner.bayesian_priors[category].alpha == 2.0

    def test_record_loss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")
            learner = Learner(enabled=True, model_state_path=state_path)

            trade = make_trade(outcome="loss", pnl=-100)
            learner.record_outcome(trade)

            category = "basketball_nba:moneyline"
            assert learner.bayesian_priors[category].beta == 2.0

    def test_skip_push_outcome(self):
        learner = Learner(enabled=True)
        trade = make_trade(outcome="push", pnl=0)
        learner.record_outcome(trade)
        assert learner.total_trades == 0  # Pushes don't count

    def test_skip_none_outcome(self):
        learner = Learner(enabled=True)
        trade = make_trade(outcome=None, pnl=None)
        # outcome=None should be skipped since make_trade sets it
        # but the record_outcome method checks for None
        trade.outcome = None
        learner.record_outcome(trade)
        assert learner.total_trades == 0

    def test_adjusted_edge_after_learning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")
            learner = Learner(
                enabled=True,
                method="bayesian",
                min_trades_before_adjust=5,
                model_state_path=state_path,
            )

            # Record many wins for NBA
            for _ in range(20):
                learner.record_outcome(make_trade(outcome="win"))

            # Recalibrate
            learner.recalibrate()

            # Should have lowered the edge threshold for NBA moneyline
            adj = learner.get_adjusted_min_edge("basketball_nba", "moneyline", 3.0)
            assert adj < 3.0  # Should be lower due to good performance

    def test_adjusted_edge_after_losses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")
            learner = Learner(
                enabled=True,
                method="bayesian",
                min_trades_before_adjust=5,
                model_state_path=state_path,
            )

            # Record many losses
            for _ in range(20):
                learner.record_outcome(make_trade(outcome="loss", pnl=-100))

            learner.recalibrate()

            adj = learner.get_adjusted_min_edge("basketball_nba", "moneyline", 3.0)
            assert adj > 3.0  # Should be higher due to poor performance

    def test_should_not_recalibrate_too_few_trades(self):
        learner = Learner(enabled=True, min_trades_before_adjust=50)
        learner.total_trades = 10
        assert learner.should_recalibrate() is False

    def test_state_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")

            # Create and populate learner
            learner1 = Learner(enabled=True, model_state_path=state_path)
            for _ in range(5):
                learner1.record_outcome(make_trade(outcome="win"))
            learner1._save_state()

            # Load in new learner
            learner2 = Learner(enabled=True, model_state_path=state_path)
            assert learner2.total_trades == 5
            assert "basketball_nba:moneyline" in learner2.bayesian_priors

    def test_rolling_avg_recalibration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")
            learner = Learner(
                enabled=True,
                method="rolling_avg",
                min_trades_before_adjust=5,
                model_state_path=state_path,
            )

            # Record trades with < 50% win rate
            for _ in range(8):
                learner.record_outcome(make_trade(outcome="loss", pnl=-100))
            for _ in range(2):
                learner.record_outcome(make_trade(outcome="win"))

            learner.recalibrate()

            adj = learner.get_adjusted_min_edge("basketball_nba", "moneyline", 3.0)
            assert adj > 3.0  # Should increase due to low win rate

    def test_max_adjustment_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "model_state.json")
            learner = Learner(
                enabled=True,
                method="bayesian",
                min_trades_before_adjust=5,
                model_state_path=state_path,
            )

            # Record extreme losses
            for _ in range(100):
                learner.record_outcome(make_trade(outcome="loss", pnl=-100))

            learner.recalibrate()

            category = "basketball_nba:moneyline"
            adj = learner.edge_adjustments.get(category, 0)
            assert abs(adj) <= 3.0  # Capped at MAX_ADJUSTMENT

    def test_get_stats(self):
        learner = Learner(enabled=True)
        for _ in range(3):
            learner.record_outcome(make_trade(outcome="win"))
        learner.record_outcome(make_trade(outcome="loss", pnl=-100))

        stats = learner.get_stats()
        assert stats["total_trades"] == 4
        assert stats["enabled"] is True
        assert "basketball_nba:moneyline" in stats["categories"]
