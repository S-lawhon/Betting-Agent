"""
src/risk_manager.py
───────────────────
Risk management and in-memory P&L ledger.

Architecture
────────────
Two cooperating classes:

  Ledger        — pure bookkeeping, no policy decisions.
                  Tracks open positions, realised P&L, exposure, drawdown.

  RiskManager   — policy layer on top of Ledger.
                  Applies config-driven rules to decide whether a trade
                  is allowed and how large it should be.

Clock injection
───────────────
Both classes accept an optional ``_now_fn`` callable (default:
``datetime.utcnow``) so tests can control time without patching globals.

Bankroll model
──────────────
Bankroll starts at ``initial_bankroll``.  When a position closes, the
net P&L (positive or negative) is added to the running bankroll.  Open
positions are *not* deducted from bankroll until they close — only
``exposure_usd`` (the sum of open stakes) is tracked separately.

This matches Kalshi's model: you fund stakes from your account balance,
and winnings/losses settle when the market resolves.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class Position:
    """An open Kalshi position held by the agent."""
    position_id: str
    market_ticker: str
    side: str               # "YES" | "NO"
    size_usd: float         # dollar amount staked
    kalshi_price: float     # price paid (0–1)
    opened_at: datetime     # UTC

    @classmethod
    def new(
        cls,
        market_ticker: str,
        side: str,
        size_usd: float,
        kalshi_price: float,
        opened_at: Optional[datetime] = None,
    ) -> "Position":
        return cls(
            position_id=str(uuid.uuid4()),
            market_ticker=market_ticker,
            side=side,
            size_usd=size_usd,
            kalshi_price=kalshi_price,
            opened_at=opened_at or datetime.now(tz=timezone.utc),
        )


@dataclass
class ClosedPosition:
    """A settled position with realised P&L."""
    position: Position
    pnl_usd: float          # positive = profit, negative = loss
    closed_at: datetime     # UTC


@dataclass(frozen=True)
class TradeDecision:
    """
    Result of ``RiskManager.can_trade()``.

    Attributes
    ----------
    allowed : bool
        True when all risk checks pass.
    block_code : str
        "OK" when allowed; one of the BLOCK_* constants otherwise.
    reason : str
        Human-readable explanation (logged in trade_log.jsonl).
    """
    allowed: bool
    block_code: str
    reason: str

    # Canonical block codes
    OK = "OK"
    BLOCK_MAX_POSITIONS = "MAX_POSITIONS"
    BLOCK_MAX_EXPOSURE = "MAX_EXPOSURE"
    BLOCK_DAILY_LOSS = "DAILY_LOSS"
    BLOCK_COOLDOWN = "COOLDOWN"
    BLOCK_MIN_CONFIDENCE = "MIN_CONFIDENCE"

    @classmethod
    def ok(cls) -> "TradeDecision":
        return cls(allowed=True, block_code=cls.OK, reason="All risk checks passed.")

    @classmethod
    def blocked(cls, code: str, reason: str) -> "TradeDecision":
        return cls(allowed=False, block_code=code, reason=reason)


@dataclass
class LedgerSnapshot:
    """Point-in-time summary of the ledger state."""
    bankroll: float
    open_positions: int
    exposure_usd: float
    exposure_pct: float         # exposure_usd / bankroll
    daily_pnl_usd: float
    daily_pnl_pct: float        # daily_pnl_usd / initial_bankroll
    peak_equity: float
    drawdown_usd: float         # peak_equity - bankroll
    drawdown_pct: float         # drawdown_usd / peak_equity
    in_cooldown: bool
    cooldown_until: Optional[datetime]
    as_of: datetime


# ── Ledger ────────────────────────────────────────────────────────────────────

class Ledger:
    """
    In-memory double-entry bookkeeper for agent positions.

    Parameters
    ----------
    initial_bankroll : float
        Starting account balance in USD.
    _now_fn : callable, optional
        Returns current UTC datetime.  Override in tests.
    """

    def __init__(
        self,
        initial_bankroll: float,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if initial_bankroll <= 0:
            raise ValueError("initial_bankroll must be positive.")
        self._initial_bankroll = initial_bankroll
        self._bankroll = initial_bankroll
        self._peak_equity = initial_bankroll
        self._open: Dict[str, Position] = {}
        self._closed: List[ClosedPosition] = []
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))

    # ── Open / close ──────────────────────────────────────────────────────────

    def open_position(self, position: Position) -> None:
        """Record a newly opened position."""
        if position.position_id in self._open:
            raise ValueError(f"Position {position.position_id} already open.")
        self._open[position.position_id] = position

    def close_by_ticker(
        self, market_ticker: str, pnl_usd: float
    ) -> Optional[ClosedPosition]:
        """
        Close the first open position matching ``market_ticker``.

        Used by the settlement engine which only knows the ticker, not the
        internal position_id.

        Parameters
        ----------
        market_ticker : str
            Kalshi ticker to search for in open positions.
        pnl_usd : float
            Realised P&L (profit for wins, -size for losses, 0 for voids).

        Returns
        -------
        ClosedPosition, or ``None`` if no matching position was found.
        """
        for pid, pos in list(self._open.items()):
            if pos.market_ticker == market_ticker:
                return self.close_position(pid, pnl_usd)
        return None

    def close_position(self, position_id: str, pnl_usd: float) -> ClosedPosition:
        """
        Settle an open position with a realised P&L amount.

        Updates bankroll and peak equity.

        Parameters
        ----------
        position_id : str
            ID of an open position.
        pnl_usd : float
            Realised profit (positive) or loss (negative) in USD.

        Returns
        -------
        ClosedPosition
        """
        if position_id not in self._open:
            raise KeyError(f"No open position with id {position_id!r}.")
        pos = self._open.pop(position_id)
        self._bankroll += pnl_usd
        if self._bankroll > self._peak_equity:
            self._peak_equity = self._bankroll
        closed = ClosedPosition(position=pos, pnl_usd=pnl_usd, closed_at=self._now())
        self._closed.append(closed)
        return closed

    # ── Snapshot properties ───────────────────────────────────────────────────

    @property
    def bankroll(self) -> float:
        return self._bankroll

    @bankroll.setter
    def bankroll(self, value: float) -> None:
        """Allow capital allocator to adjust bankroll for per-pod allocation."""
        self._bankroll = value
        if value > self._peak_equity:
            self._peak_equity = value

    @property
    def initial_bankroll(self) -> float:
        return self._initial_bankroll

    @property
    def open_count(self) -> int:
        return len(self._open)

    @property
    def open_positions(self) -> List[Position]:
        return list(self._open.values())

    @property
    def exposure_usd(self) -> float:
        """Total dollar value staked in open positions."""
        return sum(p.size_usd for p in self._open.values())

    @property
    def exposure_pct(self) -> float:
        """Exposure as a fraction of current bankroll."""
        if self._bankroll <= 0:
            return float("inf")
        return self.exposure_usd / self._bankroll

    def daily_pnl(self, date: Optional[datetime] = None) -> float:
        """
        Realised P&L for a given UTC calendar date (defaults to today).

        Parameters
        ----------
        date : datetime, optional
            Any datetime whose .date() defines the day to query.
        """
        target_date = (date or self._now()).date()
        return sum(
            cp.pnl_usd
            for cp in self._closed
            if cp.closed_at.date() == target_date
        )

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def drawdown_usd(self) -> float:
        return max(0.0, self._peak_equity - self._bankroll)

    @property
    def drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return self.drawdown_usd / self._peak_equity

    @property
    def closed_positions(self) -> List[ClosedPosition]:
        return list(self._closed)

    def snapshot(self, in_cooldown: bool = False, cooldown_until: Optional[datetime] = None) -> LedgerSnapshot:
        """Return a point-in-time snapshot of ledger state."""
        now = self._now()
        daily = self.daily_pnl(now)
        return LedgerSnapshot(
            bankroll=self._bankroll,
            open_positions=self.open_count,
            exposure_usd=self.exposure_usd,
            exposure_pct=self.exposure_pct,
            daily_pnl_usd=daily,
            daily_pnl_pct=daily / self._initial_bankroll if self._initial_bankroll else 0.0,
            peak_equity=self._peak_equity,
            drawdown_usd=self.drawdown_usd,
            drawdown_pct=self.drawdown_pct,
            in_cooldown=in_cooldown,
            cooldown_until=cooldown_until,
            as_of=now,
        )


# ── RiskManager ───────────────────────────────────────────────────────────────

class RiskManager:
    """
    Policy layer: decides whether a trade is allowed and how large it should be.

    Parameters
    ----------
    ledger : Ledger
        Shared ledger instance (injected so caller can inspect it).
    max_positions : int
        Maximum concurrent open positions.
    max_exposure_pct : float
        Maximum total exposure as a fraction of bankroll (e.g. 0.20 = 20%).
    max_daily_loss_pct : float
        Daily loss limit as a fraction of bankroll (e.g. 0.05 = 5%).
    cooldown_minutes : int
        Minutes to pause trading after max_daily_loss breach.
    kelly_fraction : float
        Fractional Kelly multiplier for position sizing.
    max_bet_pct : float
        Hard cap: single bet cannot exceed this fraction of bankroll.
    min_confidence : float
        Model confidence below which we skip the trade entirely.
    _now_fn : callable, optional
        Returns current UTC datetime. Override in tests.
    """

    def __init__(
        self,
        ledger: Ledger,
        max_positions: int = 10,
        max_exposure_pct: float = 0.20,
        max_daily_loss_pct: float = 0.05,
        cooldown_minutes: int = 60,
        kelly_fraction: float = 0.25,
        max_bet_pct: float = 0.03,
        min_confidence: float = 0.55,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.ledger = ledger
        self.max_positions = max_positions
        self.max_exposure_pct = max_exposure_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.cooldown_minutes = cooldown_minutes
        self.kelly_fraction = kelly_fraction
        self.max_bet_pct = max_bet_pct
        self.min_confidence = min_confidence
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))

        # Cooldown state
        self._cooldown_until: Optional[datetime] = None

    @classmethod
    def from_config(cls, config: dict, ledger: Ledger, **kwargs) -> "RiskManager":
        """
        Construct from the loaded config.yaml dict.

        Parameters
        ----------
        config : dict
            Top-level config dict (``load_config()`` output).
        ledger : Ledger
            Pre-constructed Ledger instance.
        **kwargs :
            Additional overrides (e.g. ``_now_fn`` for tests).
        """
        risk = config.get("risk", {})
        return cls(
            ledger=ledger,
            max_positions=risk.get("max_positions", 10),
            max_exposure_pct=risk.get("max_exposure_pct", 0.20),
            max_daily_loss_pct=risk.get("max_daily_loss_pct", 0.05),
            cooldown_minutes=risk.get("cooldown_minutes", 60),
            kelly_fraction=risk.get("kelly_fraction", 0.25),
            max_bet_pct=risk.get("max_bet_pct", 0.03),
            min_confidence=risk.get("min_confidence", 0.55),
            **kwargs,
        )

    # ── Core decision ─────────────────────────────────────────────────────────

    def can_trade(
        self,
        proposed_size_usd: float,
        confidence: float = 1.0,
    ) -> TradeDecision:
        """
        Check all risk rules in priority order.

        Rules are checked in this order so the most operationally important
        blocks surface first:

        1. Minimum confidence (model quality gate)
        2. Cooldown (trading pause after daily loss limit)
        3. Max daily loss (triggers cooldown if just breached)
        4. Max open positions
        5. Max exposure

        Parameters
        ----------
        proposed_size_usd : float
            Dollar amount we intend to stake.
        confidence : float
            Model confidence in [0, 1].

        Returns
        -------
        TradeDecision with ``allowed=True`` or a specific block code.
        """
        now = self._now()

        # 1. Min confidence
        if confidence < self.min_confidence:
            return TradeDecision.blocked(
                TradeDecision.BLOCK_MIN_CONFIDENCE,
                f"Confidence {confidence:.3f} below minimum {self.min_confidence:.3f}.",
            )

        # 2. Cooldown — still within pause window
        if self._cooldown_until is not None and now < self._cooldown_until:
            remaining = int((self._cooldown_until - now).total_seconds() / 60)
            return TradeDecision.blocked(
                TradeDecision.BLOCK_COOLDOWN,
                f"In cooldown until {self._cooldown_until.isoformat()}Z "
                f"({remaining} min remaining).",
            )
        elif self._cooldown_until is not None and now >= self._cooldown_until:
            # Cooldown expired — clear it
            self._cooldown_until = None

        # 3. Daily loss limit — check and trigger cooldown if needed
        daily_loss_pct = self.ledger.daily_pnl(now) / self.ledger.initial_bankroll
        if daily_loss_pct < -self.max_daily_loss_pct:
            self._cooldown_until = now + timedelta(minutes=self.cooldown_minutes)
            return TradeDecision.blocked(
                TradeDecision.BLOCK_DAILY_LOSS,
                f"Daily loss {daily_loss_pct:.2%} exceeds limit "
                f"-{self.max_daily_loss_pct:.2%}. "
                f"Cooldown until {self._cooldown_until.isoformat()}Z.",
            )

        # 4. Max open positions
        if self.ledger.open_count >= self.max_positions:
            return TradeDecision.blocked(
                TradeDecision.BLOCK_MAX_POSITIONS,
                f"Open positions ({self.ledger.open_count}) at maximum "
                f"({self.max_positions}).",
            )

        # 5. Max exposure
        projected_exposure_pct = (
            (self.ledger.exposure_usd + proposed_size_usd) / self.ledger.bankroll
        )
        if projected_exposure_pct > self.max_exposure_pct:
            return TradeDecision.blocked(
                TradeDecision.BLOCK_MAX_EXPOSURE,
                f"Projected exposure {projected_exposure_pct:.2%} would exceed "
                f"limit {self.max_exposure_pct:.2%}.",
            )

        return TradeDecision.ok()

    # ── Position sizing ───────────────────────────────────────────────────────

    def calculate_position_size(
        self,
        kelly_fractional: float,
    ) -> float:
        """
        Convert a fractional Kelly stake to a dollar amount.

        Parameters
        ----------
        kelly_fractional : float
            Output of ``EdgeCalculator.evaluate().kelly_fractional`` (already
            capped at ``max_bet_pct`` by the edge calculator).

        Returns
        -------
        Dollar amount to wager, floored at 0.  Capped at
        ``max_position_usd`` when set by the CapitalAllocator.
        """
        size = kelly_fractional * self.ledger.bankroll
        # Enforce per-pod position cap set by CapitalAllocator
        max_pos = getattr(self, "max_position_usd", None)
        if max_pos is not None and max_pos > 0:
            size = min(size, max_pos)
        return max(0.0, size)

    # ── Cooldown inspection ───────────────────────────────────────────────────

    @property
    def in_cooldown(self) -> bool:
        """True when a cooldown window is currently active."""
        if self._cooldown_until is None:
            return False
        return self._now() < self._cooldown_until

    @property
    def cooldown_until(self) -> Optional[datetime]:
        return self._cooldown_until

    # ── Convenience passthrough ───────────────────────────────────────────────

    def snapshot(self) -> LedgerSnapshot:
        """Delegate to Ledger.snapshot() enriched with cooldown state."""
        return self.ledger.snapshot(
            in_cooldown=self.in_cooldown,
            cooldown_until=self._cooldown_until,
        )
