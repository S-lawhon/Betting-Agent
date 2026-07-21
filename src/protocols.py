"""
src/protocols.py
────────────────
Structural (Protocol-based) type hints for duck-typed dependencies.

These protocols define the minimum interface that the core hot-path
modules require from external dependencies (edge_calculator,
risk_manager, venue clients, etc.).  They enable mypy checking
without coupling the codebase to any concrete implementation.

Usage:
    from src.protocols import EdgeCalculatorProtocol, RiskManagerProtocol
    def build_pod(edge: EdgeCalculatorProtocol, risk: RiskManagerProtocol): ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ── Edge calculator ──────────────────────────────────────────────────

@dataclass(frozen=True)
class EdgeResult:
    """Minimum expected shape of an edge-calculation result."""
    side: str               # "YES" or "NO"
    raw_edge: float         # (fair - venue) / venue
    ev: float               # Expected value in dollars
    kelly_fractional: float # Kelly-optimal bankroll fraction


@runtime_checkable
class EdgeCalculatorProtocol(Protocol):
    """Interface required by all pods for edge calculation."""

    def best_side(
        self,
        fair_yes_prob: float,
        kalshi_yes_price: float,
    ) -> Optional[EdgeResult]:
        """Return the best side to trade, or None if no edge."""
        ...

    @classmethod
    def from_config(cls, config: dict) -> "EdgeCalculatorProtocol":
        ...


# ── Risk manager ─────────────────────────────────────────────────────

@runtime_checkable
class LedgerProtocol(Protocol):
    """Minimum interface for a risk-manager ledger."""
    bankroll: float


@runtime_checkable
class RiskManagerProtocol(Protocol):
    """Interface required by pods and executor for risk checks."""
    max_bet_pct: float
    max_position_usd: float

    @property
    def ledger(self) -> LedgerProtocol:
        ...


# ── Venue clients ────────────────────────────────────────────────────

@runtime_checkable
class KalshiClientProtocol(Protocol):
    """Minimum interface for the Kalshi venue client."""

    def get_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        ...

    def get_markets(self) -> List[Dict[str, Any]]:
        ...

    def place_order(
        self, *, ticker: str, side: str,
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        count: int = 1,
    ) -> Any:
        ...


@runtime_checkable
class PolymarketClientProtocol(Protocol):
    """Minimum interface for the Polymarket venue client."""

    def get_midpoint(self, token_id: str) -> Optional[float]:
        ...

    def get_markets(self, next_cursor: Optional[str] = None) -> Dict[str, Any]:
        ...

    def place_order(
        self, *, token_id: str, side: str,
        price: float, size: int,
    ) -> Any:
        ...


@runtime_checkable
class ForecastExClientProtocol(Protocol):
    """Minimum interface for the ForecastEx venue client."""

    def get_price(self, symbol: str, right: str = "CALL") -> Optional[float]:
        ...

    def place_order(
        self, *, symbol: str, right: str,
        quantity: int, limit_price: float,
    ) -> Any:
        ...


# ── Aggregate risk ───────────────────────────────────────────────────

@runtime_checkable
class AggregateRiskProtocol(Protocol):
    """Interface used by MultiExecutor and pods for portfolio-level checks."""

    def check_trade(
        self, pod_id: str, venue: str, position_size_usd: float,
    ) -> bool:
        ...

    def check_pre_cycle(self) -> bool:
        ...


# ── Trade store ──────────────────────────────────────────────────────

@runtime_checkable
class TradeStoreProtocol(Protocol):
    """Interface for the centralised trade log store."""

    def append(self, entry: Dict[str, Any]) -> None:
        ...

    def is_fingerprint_seen(self, fingerprint: str) -> bool:
        ...

    def is_position_open(self, market_id: str) -> bool:
        ...

    def get_open_market_ids(self) -> set:
        ...

    @property
    def seen_fingerprints(self) -> set:
        ...
