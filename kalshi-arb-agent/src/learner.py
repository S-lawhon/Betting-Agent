"""Learning system: Bayesian calibration, rolling win rates, and ML-based trade scoring."""

from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

from src.logger_setup import get_logger
from src.utils import TradeRecord

logger = get_logger("learner")

MODEL_STATE_PATH = os.path.join("data", "model_state.json")
ML_MODEL_PATH = os.path.join("data", "ml_model.pkl")
EDGE_HISTORY_PATH = os.path.join("data", "edge_history.csv")

# Maximum edge adjustment per recalibration (in percentage points)
MAX_ADJUSTMENT = 3.0


@dataclass
class BayesianPrior:
    """Beta distribution prior for a (sport, market_type) category."""
    alpha: float = 1.0  # Successes + prior
    beta: float = 1.0   # Failures + prior
    total_trades: int = 0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def win_rate(self) -> float:
        return self.mean

    def update(self, won: bool) -> None:
        if won:
            self.alpha += 1.0
        else:
            self.beta += 1.0
        self.total_trades += 1

    def to_dict(self) -> dict:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "total_trades": self.total_trades,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BayesianPrior:
        return cls(
            alpha=d.get("alpha", 1.0),
            beta=d.get("beta", 1.0),
            total_trades=d.get("total_trades", 0),
        )


@dataclass
class RollingStats:
    """Rolling win rate tracker for a category."""
    wins: int = 0
    losses: int = 0
    recent_outcomes: list[bool] = field(default_factory=list)
    window_size: int = 50

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        if self.total == 0:
            return 0.5
        return self.wins / self.total

    @property
    def recent_win_rate(self) -> float:
        recent = self.recent_outcomes[-self.window_size:]
        if not recent:
            return 0.5
        return sum(recent) / len(recent)

    def update(self, won: bool) -> None:
        if won:
            self.wins += 1
        else:
            self.losses += 1
        self.recent_outcomes.append(won)
        # Keep only last N*2 outcomes to prevent unbounded growth
        if len(self.recent_outcomes) > self.window_size * 2:
            self.recent_outcomes = self.recent_outcomes[-self.window_size:]

    def to_dict(self) -> dict:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "recent_outcomes": self.recent_outcomes[-self.window_size:],
            "window_size": self.window_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RollingStats:
        obj = cls(
            wins=d.get("wins", 0),
            losses=d.get("losses", 0),
            window_size=d.get("window_size", 50),
        )
        obj.recent_outcomes = d.get("recent_outcomes", [])
        return obj


class Learner:
    """Adaptive learning system that improves edge thresholds over time.

    Three approaches:
    1. Bayesian: Beta distribution posteriors per category
    2. Rolling: Moving window win rate tracker
    3. ML: Gradient boosting classifier (after sufficient data)
    """

    def __init__(
        self,
        enabled: bool = True,
        method: str = "bayesian",
        min_trades_before_adjust: int = 50,
        recalibrate_interval_hours: int = 24,
        model_state_path: str = MODEL_STATE_PATH,
        ml_model_path: str = ML_MODEL_PATH,
    ):
        self.enabled = enabled
        self.method = method
        self.min_trades = min_trades_before_adjust
        self.recalibrate_hours = recalibrate_interval_hours
        self.model_state_path = model_state_path
        self.ml_model_path = ml_model_path

        # State
        self.bayesian_priors: dict[str, BayesianPrior] = {}
        self.rolling_stats: dict[str, RollingStats] = {}
        self.edge_adjustments: dict[str, float] = {}
        self.total_trades: int = 0
        self.last_recalibration: Optional[datetime] = None
        self.ml_model: Any = None
        self.ml_features: list[str] = [
            "edge_at_entry", "time_to_event_hours", "kalshi_volume",
            "num_books", "line_movement", "hour_of_day",
        ]

        # Load persisted state
        self._load_state()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def get_adjusted_min_edge(
        self, sport: str, market_type: str, base_min_edge: float = 3.0
    ) -> float:
        """Get the adjusted minimum edge for a (sport, market_type) category.

        Args:
            sport: Sport key.
            market_type: "moneyline", "spread", or "total".
            base_min_edge: The base minimum edge from config.

        Returns:
            Adjusted minimum edge percentage.
        """
        if not self.enabled:
            return base_min_edge

        category = f"{sport}:{market_type}"
        adjustment = self.edge_adjustments.get(category, 0.0)
        adjusted = base_min_edge + adjustment

        # Never go below 1% minimum edge
        return max(1.0, adjusted)

    def get_win_probability_adjustment(
        self, trade_features: dict[str, Any]
    ) -> float:
        """Use ML model to predict win probability (if available).

        Args:
            trade_features: Dict of feature values.

        Returns:
            Predicted win probability (0.5 if model unavailable).
        """
        if self.ml_model is None or self.total_trades < 200:
            return 0.5

        try:
            import pandas as pd

            feature_values = []
            for f in self.ml_features:
                val = trade_features.get(f, 0)
                # Encode sport as numeric
                if f == "sport":
                    val = hash(str(val)) % 100
                feature_values.append(float(val) if val is not None else 0.0)

            X = np.array([feature_values])
            prob = self.ml_model.predict_proba(X)[0][1]
            return float(prob)

        except Exception as exc:
            logger.debug(f"ML prediction failed: {exc}")
            return 0.5

    # ------------------------------------------------------------------
    # Recording outcomes
    # ------------------------------------------------------------------

    def record_outcome(self, trade: TradeRecord) -> None:
        """Record a trade outcome and update all learning systems."""
        if not self.enabled:
            return

        if trade.outcome is None or trade.outcome == "push":
            return

        won = trade.outcome == "win"
        category = f"{trade.sport}:{trade.market_type}"

        # Update Bayesian prior
        if category not in self.bayesian_priors:
            self.bayesian_priors[category] = BayesianPrior()
        self.bayesian_priors[category].update(won)

        # Update rolling stats
        if category not in self.rolling_stats:
            self.rolling_stats[category] = RollingStats()
        self.rolling_stats[category].update(won)

        self.total_trades += 1

        logger.info(
            f"Learner recorded: {category} — {'WIN' if won else 'LOSS'} "
            f"(total: {self.total_trades})",
            extra={"sport": trade.sport, "action": "learner_update"},
        )

        # Append to edge history CSV
        self._append_edge_history(trade)

    # ------------------------------------------------------------------
    # Recalibration
    # ------------------------------------------------------------------

    def should_recalibrate(self) -> bool:
        """Check if it's time to recalibrate."""
        if not self.enabled:
            return False
        if self.total_trades < self.min_trades:
            return False
        if self.last_recalibration is None:
            return True

        now = datetime.now(timezone.utc)
        hours_since = (now - self.last_recalibration).total_seconds() / 3600
        return hours_since >= self.recalibrate_hours

    def recalibrate(self) -> dict[str, float]:
        """Recalibrate edge adjustments based on accumulated data.

        Returns:
            Dict of category -> new edge adjustment.
        """
        if not self.enabled or self.total_trades < self.min_trades:
            return {}

        old_adjustments = dict(self.edge_adjustments)

        if self.method == "bayesian":
            self._recalibrate_bayesian()
        elif self.method == "rolling_avg":
            self._recalibrate_rolling()
        elif self.method == "none":
            pass

        # Train ML model if we have enough data
        if self.total_trades >= 200:
            self._train_ml_model()

        self.last_recalibration = datetime.now(timezone.utc)

        # Log changes
        for cat, adj in self.edge_adjustments.items():
            old = old_adjustments.get(cat, 0.0)
            if abs(adj - old) > 0.01:
                logger.info(
                    f"Edge adjustment changed: {cat}: {old:+.2f}% -> {adj:+.2f}%"
                )

        self._save_state()
        return dict(self.edge_adjustments)

    def _recalibrate_bayesian(self) -> None:
        """Recalibrate using Bayesian posterior means."""
        for category, prior in self.bayesian_priors.items():
            if prior.total_trades < 10:
                continue

            win_rate = prior.mean
            # If win rate is significantly above/below 50%, adjust
            # Target: if winning 60%+ at current edge, we can lower min_edge
            # If winning <45%, we should raise min_edge
            if win_rate > 0.55:
                # Performing well: slightly lower threshold
                new_adj = -(win_rate - 0.50) * 10  # e.g., 60% -> -1.0%
            elif win_rate < 0.45:
                # Underperforming: raise threshold
                new_adj = (0.50 - win_rate) * 20   # e.g., 40% -> +2.0%
            else:
                new_adj = 0.0

            # Cap adjustment
            new_adj = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, new_adj))

            # Smooth update: blend with existing
            old = self.edge_adjustments.get(category, 0.0)
            self.edge_adjustments[category] = old * 0.7 + new_adj * 0.3

    def _recalibrate_rolling(self) -> None:
        """Recalibrate using rolling window win rates."""
        for category, stats in self.rolling_stats.items():
            if stats.total < 10:
                continue

            rate = stats.recent_win_rate

            if rate < 0.50:
                # Underperforming: increase min_edge by 1%
                adj = self.edge_adjustments.get(category, 0.0) + 1.0
            elif rate > 0.60:
                # Outperforming: decrease min_edge by 0.5% (but floor at -2%)
                adj = self.edge_adjustments.get(category, 0.0) - 0.5
            else:
                adj = self.edge_adjustments.get(category, 0.0)

            # Cap
            adj = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, adj))
            self.edge_adjustments[category] = adj

    def _train_ml_model(self) -> None:
        """Train a gradient boosting classifier on trade history."""
        try:
            import pandas as pd
            from sklearn.ensemble import GradientBoostingClassifier
            from sklearn.model_selection import cross_val_score

            # Load trade history
            trades = self._load_trade_history()
            if len(trades) < 200:
                return

            df = pd.DataFrame(trades)
            df = df[df["outcome"].isin(["win", "loss"])].copy()

            if len(df) < 200:
                return

            # Prepare features
            feature_cols = [
                "edge_at_entry", "time_to_event_hours", "kalshi_volume",
                "num_books", "line_movement", "hour_of_day",
            ]

            for col in feature_cols:
                if col not in df.columns:
                    df[col] = 0
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            X = df[feature_cols].values
            y = (df["outcome"] == "win").astype(int).values

            # Train with regularization
            model = GradientBoostingClassifier(
                n_estimators=50,
                max_depth=3,
                learning_rate=0.1,
                min_samples_leaf=10,
                subsample=0.8,
                random_state=42,
            )

            # Cross-validate first
            scores = cross_val_score(model, X, y, cv=5, scoring="accuracy")
            mean_accuracy = scores.mean()

            logger.info(
                f"ML model cross-val accuracy: {mean_accuracy:.3f} "
                f"(±{scores.std():.3f}) on {len(X)} trades"
            )

            # Only use model if it's better than random
            if mean_accuracy > 0.52:
                model.fit(X, y)
                self.ml_model = model

                # Save model
                with open(self.ml_model_path, "wb") as f:
                    pickle.dump(model, f)

                logger.info(
                    f"ML model trained and saved. "
                    f"Feature importances: {dict(zip(feature_cols, model.feature_importances_))}"
                )
            else:
                logger.info(
                    f"ML model accuracy ({mean_accuracy:.3f}) not better than random. "
                    f"Skipping."
                )

        except Exception as exc:
            logger.error(f"ML model training failed: {exc}")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save learner state to disk."""
        state = {
            "bayesian_priors": {
                k: v.to_dict() for k, v in self.bayesian_priors.items()
            },
            "rolling_stats": {
                k: v.to_dict() for k, v in self.rolling_stats.items()
            },
            "edge_adjustments": self.edge_adjustments,
            "total_trades": self.total_trades,
            "last_recalibration": (
                self.last_recalibration.isoformat()
                if self.last_recalibration
                else None
            ),
            "ml_model_path": self.ml_model_path if self.ml_model else None,
            "version": 1,
        }

        os.makedirs(os.path.dirname(self.model_state_path) or ".", exist_ok=True)
        with open(self.model_state_path, "w") as f:
            json.dump(state, f, indent=2, default=str)

        logger.debug("Learner state saved")

    def _load_state(self) -> None:
        """Load learner state from disk."""
        if not os.path.exists(self.model_state_path):
            return

        try:
            with open(self.model_state_path, "r") as f:
                state = json.load(f)

            self.bayesian_priors = {
                k: BayesianPrior.from_dict(v)
                for k, v in state.get("bayesian_priors", {}).items()
            }
            self.rolling_stats = {
                k: RollingStats.from_dict(v)
                for k, v in state.get("rolling_stats", {}).items()
            }
            self.edge_adjustments = state.get("edge_adjustments", {})
            self.total_trades = state.get("total_trades", 0)

            recal = state.get("last_recalibration")
            if recal:
                self.last_recalibration = datetime.fromisoformat(recal)

            # Load ML model if available
            ml_path = state.get("ml_model_path")
            if ml_path and os.path.exists(ml_path):
                with open(ml_path, "rb") as f:
                    self.ml_model = pickle.load(f)
                logger.info("ML model loaded from disk")

            logger.info(
                f"Learner state loaded: {self.total_trades} trades, "
                f"{len(self.edge_adjustments)} adjustments"
            )

        except Exception as exc:
            logger.error(f"Failed to load learner state: {exc}")

    def _load_trade_history(self) -> list[dict]:
        """Load trade history from trade_log.json."""
        trade_log_path = os.path.join("data", "trade_log.json")
        if not os.path.exists(trade_log_path):
            return []
        try:
            with open(trade_log_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _append_edge_history(self, trade: TradeRecord) -> None:
        """Append a trade's edge data to the CSV history."""
        try:
            os.makedirs(os.path.dirname(EDGE_HISTORY_PATH) or ".", exist_ok=True)

            row = (
                f"{trade.timestamp},{trade.sport},{trade.market_type},"
                f"{trade.ticker},{trade.teams},{trade.event_time},"
                f"{trade.kalshi_price_at_entry},{1 - trade.kalshi_price_at_entry},"
                f"{trade.sharp_prob_at_entry},{trade.edge_at_entry},"
                f"{trade.side},{trade.kalshi_volume},{trade.num_books},"
                f"{trade.time_to_event_hours},{trade.line_movement}\n"
            )

            with open(EDGE_HISTORY_PATH, "a") as f:
                f.write(row)

        except Exception as exc:
            logger.debug(f"Failed to append edge history: {exc}")

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return learner statistics for dashboard display."""
        stats: dict[str, Any] = {
            "total_trades": self.total_trades,
            "method": self.method,
            "enabled": self.enabled,
            "last_recalibration": (
                self.last_recalibration.isoformat()
                if self.last_recalibration
                else "never"
            ),
            "ml_model_active": self.ml_model is not None,
            "categories": {},
        }

        for category in set(
            list(self.bayesian_priors.keys()) + list(self.rolling_stats.keys())
        ):
            cat_stats: dict[str, Any] = {}

            bp = self.bayesian_priors.get(category)
            if bp:
                cat_stats["bayesian_win_rate"] = round(bp.win_rate, 3)
                cat_stats["bayesian_trades"] = bp.total_trades

            rs = self.rolling_stats.get(category)
            if rs:
                cat_stats["rolling_win_rate"] = round(rs.recent_win_rate, 3)
                cat_stats["rolling_total"] = rs.total

            adj = self.edge_adjustments.get(category, 0.0)
            cat_stats["edge_adjustment"] = round(adj, 2)

            stats["categories"][category] = cat_stats

        return stats
