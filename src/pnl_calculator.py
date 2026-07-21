"""
src/pnl_calculator.py
─────────────────────
Shared P&L calculator with venue-specific fee parameters.

Centralises the profit/loss computation that was previously
scattered across pods and the settlement bridge.  Each venue
has different fee structures:

  Kalshi:      7 % fee on profit (losses = full loss)
  Polymarket:  0 % fees
  ForecastEx:  0 % commission + 3.14 % APY coupon on collateral

Usage:
    from src.pnl_calculator import PnLCalculator, VenueFees

    calc = PnLCalculator()
    pnl = calc.compute(
        venue="kalshi",
        side="YES",
        entry_price=0.40,
        exit_price=1.00,       # 1.0 for win, 0.0 for loss
        contracts=5,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VenueFees:
    """Fee structure for a single venue.

    Attributes:
        name:              Venue identifier (e.g. "kalshi").
        profit_fee_pct:    Fee taken on gross profit (0-1).  Kalshi = 0.07.
        commission_pct:    Fixed commission per contract (0-1).  All current = 0.
        collateral_apy:    Annual yield on locked collateral (0-1).  ForecastEx = 0.0314.
        min_fee_usd:       Floor fee per trade in dollars.
    """
    name: str
    profit_fee_pct: float = 0.0
    commission_pct: float = 0.0
    collateral_apy: float = 0.0
    min_fee_usd: float = 0.0


# ── Default venue fee schedules ──────────────────────────────────────

VENUE_FEES: Dict[str, VenueFees] = {
    "kalshi": VenueFees(
        name="kalshi",
        profit_fee_pct=0.07,
    ),
    "polymarket": VenueFees(
        name="polymarket",
        profit_fee_pct=0.0,
    ),
    "forecastex": VenueFees(
        name="forecastex",
        profit_fee_pct=0.0,
        collateral_apy=0.0314,
    ),
}


@dataclass(frozen=True)
class PnLResult:
    """Result of a P&L computation."""
    gross_pnl: float        # Before fees
    fees: float             # Total fees deducted
    net_pnl: float          # gross_pnl - fees
    collateral_credit: float  # Coupon earned on locked collateral
    total_pnl: float        # net_pnl + collateral_credit
    venue: str
    contracts: int
    entry_price: float
    exit_price: float


class PnLCalculator:
    """Compute trade P&L with venue-specific fee deductions.

    Handles:
      - Kalshi's 7 % profit fee (only charged on winning trades)
      - Polymarket's 0 % fee
      - ForecastEx's 3.14 % APY collateral coupon
      - Configurable per-venue overrides via constructor
    """

    def __init__(
        self,
        venue_fees: Optional[Dict[str, VenueFees]] = None,
    ):
        self._fees: Dict[str, VenueFees] = dict(VENUE_FEES)
        if venue_fees:
            self._fees.update(venue_fees)

    def get_fees(self, venue: str) -> VenueFees:
        """Look up fee schedule for a venue.  Falls back to zero-fee."""
        return self._fees.get(
            venue,
            VenueFees(name=venue),  # Default: zero fees
        )

    def compute(
        self,
        venue: str,
        side: str,
        entry_price: float,
        exit_price: float,
        contracts: int = 1,
        holding_days: float = 0.0,
    ) -> PnLResult:
        """Compute P&L for a single trade.

        Args:
            venue:        Venue name (e.g. "kalshi", "polymarket", "forecastex").
            side:         "YES" or "NO".
            entry_price:  Price paid per contract (0-1 scale).
            exit_price:   Settlement price (1.0 for YES-win, 0.0 for YES-loss).
            contracts:    Number of contracts.
            holding_days: Days the position was held (for collateral APY).

        Returns:
            PnLResult with gross, fees, net, and total P&L.
        """
        fees_cfg = self.get_fees(venue)

        # Gross P&L per contract
        if side.upper() == "YES":
            gross_per_contract = exit_price - entry_price
        else:
            # NO side: profit when exit_price < entry_price
            gross_per_contract = entry_price - exit_price

        gross_pnl = gross_per_contract * contracts

        # Commission (flat per-contract fee)
        commission = fees_cfg.commission_pct * entry_price * contracts

        # Profit fee (only on profit, not on losses)
        profit_fee = 0.0
        if gross_pnl > 0:
            profit_fee = gross_pnl * fees_cfg.profit_fee_pct

        # Minimum fee
        total_fees = max(commission + profit_fee, fees_cfg.min_fee_usd)

        net_pnl = gross_pnl - total_fees

        # Collateral credit (ForecastEx coupon)
        collateral_credit = 0.0
        if fees_cfg.collateral_apy > 0 and holding_days > 0:
            # Collateral locked = entry_price * contracts
            collateral = entry_price * contracts
            collateral_credit = collateral * fees_cfg.collateral_apy * (holding_days / 365.0)

        return PnLResult(
            gross_pnl=round(gross_pnl, 4),
            fees=round(total_fees, 4),
            net_pnl=round(net_pnl, 4),
            collateral_credit=round(collateral_credit, 4),
            total_pnl=round(net_pnl + collateral_credit, 4),
            venue=venue,
            contracts=contracts,
            entry_price=entry_price,
            exit_price=exit_price,
        )

    def compute_batch(
        self,
        trades: list[dict],
    ) -> list[PnLResult]:
        """Compute P&L for a batch of trades.

        Each trade dict must have: venue, side, entry_price, exit_price.
        Optional: contracts (default 1), holding_days (default 0).
        """
        results = []
        for t in trades:
            try:
                result = self.compute(
                    venue=t["venue"],
                    side=t["side"],
                    entry_price=t["entry_price"],
                    exit_price=t["exit_price"],
                    contracts=t.get("contracts", 1),
                    holding_days=t.get("holding_days", 0.0),
                )
                results.append(result)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("pnl_calculator: skipping invalid trade: %s", exc)
        return results
