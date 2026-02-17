"""Risk management: position sizing, exposure limits, drawdown protection."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from src.logger_setup import get_logger
from src.utils import TradeOpportunity, TradeRecord

logger = get_logger("risk_manager")


@dataclass
class RiskState:
    """Current risk state of the portfolio."""
    total_exposure_usd: float = 0.0
    open_position_count: int = 0
    daily_pnl: float = 0.0
    daily_trade_count: int = 0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    drawdown_pct: float = 0.0
    last_loss_time: Optional[float] = None
    is_cooldown: bool = False
    open_tickers: list[str] = field(default_factory=list)


class RiskManager:
    """Enforces risk limits before allowing trades.

    Tracks portfolio-level exposure, daily P&L, drawdown, and position
    correlation to prevent excessive risk.
    """

    def __init__(
        self,
        max_position_size_usd: float = 500.0,
        max_total_exposure_usd: float = 5000.0,
        max_positions: int = 20,
        max_daily_loss_usd: float = 500.0,
        cooldown_after_loss_minutes: int = 30,
        fractional_kelly: float = 0.25,
        drawdown_reduction_threshold: float = 0.10,
        drawdown_size_multiplier: float = 0.50,
    ):
        self.max_position_size_usd = max_position_size_usd
        self.max_total_exposure_usd = max_total_exposure_usd
        self.max_positions = max_positions
        self.max_daily_loss_usd = max_daily_loss_usd
        self.cooldown_minutes = cooldown_after_loss_minutes
        self.fractional_kelly = fractional_kelly
        self.drawdown_threshold = drawdown_reduction_threshold
        self.drawdown_multiplier = drawdown_size_multiplier

        self._state = RiskState()
        self._trade_history: list[TradeRecord] = []
        self._daily_reset_date: Optional[str] = None

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    @property
    def state(self) -> RiskState:
        """Current risk state (read-only view)."""
        self._check_daily_reset()
        return self._state

    def update_positions(
        self,
        open_positions: list[dict[str, Any]],
        account_balance: float = 0.0,
    ) -> None:
        """Update risk state from current portfolio positions.

        Args:
            open_positions: List of position dicts from Kalshi API.
            account_balance: Current account balance in USD.
        """
        self._state.open_position_count = len(open_positions)
        self._state.open_tickers = [
            p.get("ticker", "") for p in open_positions
        ]

        # Calculate total exposure
        total = 0.0
        for pos in open_positions:
            # Position size in dollars (contracts * price / 100)
            count = abs(pos.get("position", 0))
            avg_price = pos.get("average_price", 50) / 100.0
            total += count * avg_price
        self._state.total_exposure_usd = total

        # Update equity tracking
        if account_balance > 0:
            self._state.current_equity = account_balance
            if account_balance > self._state.peak_equity:
                self._state.peak_equity = account_balance
            if self._state.peak_equity > 0:
                self._state.drawdown_pct = (
                    1.0 - account_balance / self._state.peak_equity
                )

    def record_trade_result(self, trade: TradeRecord) -> None:
        """Record a trade outcome and update running P&L."""
        self._trade_history.append(trade)

        if trade.pnl is not None:
            self._state.daily_pnl += trade.pnl
            self._state.daily_trade_count += 1

            if trade.pnl < 0:
                self._state.last_loss_time = time.time()

        logger.info(
            f"Trade recorded: {trade.ticker} {trade.side} — "
            f"PnL: ${trade.pnl or 0:.2f}, Daily PnL: ${self._state.daily_pnl:.2f}",
            extra={"ticker": trade.ticker, "pnl": trade.pnl},
        )

    def _check_daily_reset(self) -> None:
        """Reset daily counters at midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_reset_date = today
            self._state.daily_pnl = 0.0
            self._state.daily_trade_count = 0
            self._state.is_cooldown = False

    # ------------------------------------------------------------------
    # Risk checks
    # ------------------------------------------------------------------

    def can_trade(self, opportunity: TradeOpportunity) -> tuple[bool, str]:
        """Run all risk checks. Returns (allowed, reason)."""
        self._check_daily_reset()
        ticker = opportunity.matched_market.kalshi_market.ticker

        # Check daily loss limit
        ok, reason = self.check_daily_loss()
        if not ok:
            return False, reason

        # Check cooldown after loss
        ok, reason = self.check_cooldown()
        if not ok:
            return False, reason

        # Check total exposure
        ok, reason = self.check_total_exposure(opportunity.recommended_size_usd)
        if not ok:
            return False, reason

        # Check position count
        if self._state.open_position_count >= self.max_positions:
            return False, (
                f"Max positions reached ({self.max_positions}). "
                f"Wait for existing positions to close."
            )

        # Check for duplicate position
        if ticker in self._state.open_tickers:
            return False, f"Already have position in {ticker}"

        # Check correlation
        ok, reason = self.check_correlation(opportunity)
        if not ok:
            return False, reason

        return True, "All risk checks passed"

    def check_daily_loss(self) -> tuple[bool, str]:
        """Check if daily loss limit has been hit."""
        self._check_daily_reset()
        if self._state.daily_pnl < 0 and abs(self._state.daily_pnl) >= self.max_daily_loss_usd:
            return False, (
                f"Daily loss limit hit: ${self._state.daily_pnl:.2f} "
                f"(max: -${self.max_daily_loss_usd:.2f}). Trading paused."
            )
        return True, "OK"

    def check_cooldown(self) -> tuple[bool, str]:
        """Check if in cooldown period after a loss."""
        if self._state.last_loss_time is not None:
            elapsed = time.time() - self._state.last_loss_time
            cooldown_sec = self.cooldown_minutes * 60
            if elapsed < cooldown_sec:
                remaining = int(cooldown_sec - elapsed)
                self._state.is_cooldown = True
                return False, (
                    f"Cooldown active: {remaining}s remaining after loss. "
                    f"(Cooldown: {self.cooldown_minutes} min)"
                )
        self._state.is_cooldown = False
        return True, "OK"

    def check_total_exposure(self, proposed_size: float) -> tuple[bool, str]:
        """Check if adding proposed_size would exceed total exposure limit."""
        new_total = self._state.total_exposure_usd + proposed_size
        if new_total > self.max_total_exposure_usd:
            return False, (
                f"Total exposure would be ${new_total:.2f} "
                f"(max: ${self.max_total_exposure_usd:.2f}). "
                f"Current: ${self._state.total_exposure_usd:.2f}"
            )
        return True, "OK"

    def check_correlation(
        self, opportunity: TradeOpportunity
    ) -> tuple[bool, str]:
        """Warn if multiple positions on correlated outcomes (same game)."""
        mm = opportunity.matched_market
        event = mm.sportsbook_event

        # Check if we have other positions on the same event
        event_teams = {event.home_team.lower(), event.away_team.lower()}

        correlated_tickers = []
        for ticker in self._state.open_tickers:
            # Simple heuristic: if ticker contains similar team identifiers
            ticker_lower = ticker.lower()
            for team in event_teams:
                short = team.split()[-1] if team else ""  # Last word (team name)
                if short and short in ticker_lower:
                    correlated_tickers.append(ticker)
                    break

        if len(correlated_tickers) >= 2:
            return False, (
                f"Correlated positions detected: {correlated_tickers}. "
                f"Already have exposure to this game."
            )

        if correlated_tickers:
            logger.warning(
                f"Possible correlation with existing position: {correlated_tickers}",
                extra={"ticker": mm.kalshi_market.ticker, "reason": "correlation_warning"},
            )

        return True, "OK"

    def check_drawdown(self) -> float:
        """Check drawdown and return position size multiplier.

        Returns:
            Multiplier for position sizes (1.0 = normal, 0.5 = reduced).
        """
        if self._state.drawdown_pct >= self.drawdown_threshold:
            logger.warning(
                f"Drawdown protection active: {self._state.drawdown_pct:.1%} "
                f"drawdown from peak. Reducing position sizes by "
                f"{(1 - self.drawdown_multiplier):.0%}."
            )
            return self.drawdown_multiplier
        return 1.0

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        opportunity: TradeOpportunity,
        bankroll: float = 0.0,
    ) -> float:
        """Calculate the position size in USD for a trade opportunity.

        Uses fractional Kelly criterion, capped by max_position_size_usd,
        and adjusted for drawdown.

        Args:
            opportunity: The trade opportunity.
            bankroll: Current bankroll / account balance.

        Returns:
            Recommended position size in USD.
        """
        kelly = opportunity.kelly_fraction
        confidence = opportunity.confidence_score

        # Fractional Kelly
        fraction = kelly * self.fractional_kelly

        # Scale by confidence (low confidence = smaller size)
        fraction *= confidence

        # Apply drawdown multiplier
        drawdown_mult = self.check_drawdown()
        fraction *= drawdown_mult

        # Calculate dollar size
        if bankroll > 0:
            size = bankroll * fraction
        else:
            # Fallback: use max position size as reference
            size = self.max_position_size_usd * fraction

        # Enforce max position size
        size = min(size, self.max_position_size_usd)

        # Enforce minimum meaningful size ($5)
        if size < 5.0:
            size = 0.0

        logger.debug(
            f"Position size: ${size:.2f} (kelly={kelly:.4f}, "
            f"fraction={self.fractional_kelly}, confidence={confidence:.2f}, "
            f"drawdown_mult={drawdown_mult})"
        )

        return round(size, 2)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_risk_summary(self) -> dict[str, Any]:
        """Return a summary of current risk state."""
        self._check_daily_reset()
        s = self._state
        return {
            "total_exposure_usd": s.total_exposure_usd,
            "max_exposure_usd": self.max_total_exposure_usd,
            "exposure_utilization_pct": (
                s.total_exposure_usd / self.max_total_exposure_usd * 100
                if self.max_total_exposure_usd > 0
                else 0
            ),
            "open_positions": s.open_position_count,
            "max_positions": self.max_positions,
            "daily_pnl": s.daily_pnl,
            "max_daily_loss": self.max_daily_loss_usd,
            "daily_loss_utilization_pct": (
                abs(s.daily_pnl) / self.max_daily_loss_usd * 100
                if self.max_daily_loss_usd > 0 and s.daily_pnl < 0
                else 0
            ),
            "drawdown_pct": s.drawdown_pct * 100,
            "is_cooldown": s.is_cooldown,
            "total_trades_today": s.daily_trade_count,
        }

    @property
    def trade_history(self) -> list[TradeRecord]:
        return list(self._trade_history)
