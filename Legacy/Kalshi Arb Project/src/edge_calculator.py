"""
src/edge_calculator.py
──────────────────────
Core edge / EV / Kelly math for Kalshi prediction markets.

Kalshi fee model
────────────────
Kalshi charges ``fee_pct`` on *net winnings* only, not on the stake.
For a YES contract bought at price ``p``:

    Win:  profit = (1 - p)·(1 - fee_pct)
    Lose: loss   = p

This makes the effective payout odds slightly worse than the raw contract
price suggests, which is accounted for in every formula here.

Side conventions
────────────────
    YES  — you believe the event WILL happen.
           kalshi_price  = mid-orderbook YES price
           fair_prob     = consensus devigged probability of event happening

    NO   — you believe the event will NOT happen.
           kalshi_price  = (1 - kalshi_yes_price)  [price of buying NO]
           fair_prob     = (1 - consensus_yes_prob) [fair value of NO]

In practice, call ``calc_both_sides()`` and it returns results for both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EdgeResult:
    """
    Immutable result of a single edge calculation.

    Attributes
    ----------
    side : "YES" | "NO"
    fair_prob : float
        Consensus devigged probability that this side resolves correctly.
    kalshi_price : float
        Price you would pay on Kalshi for this side (0–1).
    raw_edge : float
        ``fair_prob - kalshi_price`` — positive means the market under-prices this side.
    ev : float
        Expected value per dollar staked, *after* Kalshi fee.
    kelly_full : float
        Full Kelly fraction (uncapped). Use ``kelly_fractional`` for actual sizing.
    kelly_fractional : float
        ``kelly_fraction * kelly_full`` — the fraction of bankroll to wager.
    fee_pct : float
        The fee rate used in this calculation (from config).
    kelly_fraction : float
        The multiplier applied to full Kelly (e.g. 0.25 = quarter Kelly).
    has_edge : bool
        True when ``raw_edge > 0`` and ``ev > 0``.
    """
    side: str
    fair_prob: float
    kalshi_price: float
    raw_edge: float
    ev: float
    kelly_full: float
    kelly_fractional: float
    fee_pct: float
    kelly_fraction: float
    bet_cap: float = 0.0         # dynamic bet cap applied (fraction of bankroll)
    has_edge: bool = field(init=False)

    def __post_init__(self) -> None:
        # frozen=True means we can't assign; use object.__setattr__
        object.__setattr__(self, "has_edge", self.raw_edge > 0 and self.ev > 0)

    def __str__(self) -> str:
        return (
            f"EdgeResult(side={self.side}, "
            f"fair={self.fair_prob:.3f}, "
            f"price={self.kalshi_price:.3f}, "
            f"edge={self.raw_edge:+.3f}, "
            f"ev={self.ev:+.4f}, "
            f"kelly={self.kelly_fractional:.4f}, "
            f"cap={self.bet_cap:.3f})"
        )


# ── Core formulas (module-level, usable without an EdgeCalculator) ────────────

def calc_ev(fair_prob: float, kalshi_price: float, fee_pct: float = 0.07) -> float:
    """
    Expected value per dollar staked on a YES (or NO) contract.

    Parameters
    ----------
    fair_prob : float
        True probability that this side resolves correctly.
    kalshi_price : float
        Price paid for the contract (0 < p < 1).
    fee_pct : float
        Kalshi fee on net winnings (default 0.07 = 7%).

    Returns
    -------
    EV as a decimal (e.g. 0.05 means 5¢ expected profit per $1 staked).

    Formula
    -------
    EV = fair_prob · (1 − price) · (1 − fee) − (1 − fair_prob) · price
    """
    _validate_prob(fair_prob, "fair_prob")
    _validate_price(kalshi_price, "kalshi_price")
    _validate_fee(fee_pct)

    net_win = (1.0 - kalshi_price) * (1.0 - fee_pct)
    return fair_prob * net_win - (1.0 - fair_prob) * kalshi_price


def calc_raw_edge(fair_prob: float, kalshi_price: float) -> float:
    """
    Raw edge: difference between fair probability and Kalshi price.

    A positive value means Kalshi is under-pricing this side.

    Parameters
    ----------
    fair_prob : float
        True probability of this side resolving correctly.
    kalshi_price : float
        Current mid price of this side on Kalshi (0 < p < 1).
    """
    _validate_prob(fair_prob, "fair_prob")
    _validate_price(kalshi_price, "kalshi_price")
    return fair_prob - kalshi_price


def calc_kelly(
    fair_prob: float,
    kalshi_price: float,
    fee_pct: float = 0.07,
) -> float:
    """
    Full Kelly fraction for a single Kalshi contract.

    Kelly formula: f* = (b·q − (1−q)) / b

    where:
      q = fair_prob
      b = net-odds = (1−price)·(1−fee) / price

    Returns 0.0 if edge is negative (never bet against yourself).

    Parameters
    ----------
    fair_prob : float
        True probability this side wins.
    kalshi_price : float
        Price paid for the contract (0 < p < 1).
    fee_pct : float
        Kalshi fee on net winnings.
    """
    _validate_prob(fair_prob, "fair_prob")
    _validate_price(kalshi_price, "kalshi_price")
    _validate_fee(fee_pct)

    net_win = (1.0 - kalshi_price) * (1.0 - fee_pct)
    b = net_win / kalshi_price  # net odds per dollar at risk

    if b <= 0:
        return 0.0

    kelly = (b * fair_prob - (1.0 - fair_prob)) / b
    return max(0.0, kelly)  # floor at zero


def adjust_kelly_for_confidence(
    kelly_full: float,
    kelly_fraction: float,
    confidence: float = 1.0,
    max_bet_pct: float = 0.03,
) -> float:
    """
    Apply fractional Kelly scaling + confidence dampening + position cap.

    Parameters
    ----------
    kelly_full : float
        Full Kelly fraction from ``calc_kelly()``.
    kelly_fraction : float
        Fractional Kelly multiplier (e.g. 0.25 = quarter Kelly).
    confidence : float
        Model confidence in [0, 1]; multiplied into the stake.
    max_bet_pct : float
        Hard cap: single bet cannot exceed this fraction of bankroll.

    Returns
    -------
    Final fraction of bankroll to wager.
    """
    staked = kelly_full * kelly_fraction * confidence
    return min(staked, max_bet_pct)


def _dynamic_bet_cap(
    raw_edge: float,
    base_bet_pct: float,
    max_bet_pct: float,
    high_edge_threshold: float,
    max_edge_threshold: float,
) -> float:
    """
    Scale the per-bet size cap between ``base_bet_pct`` and ``max_bet_pct``
    based on the raw edge of the opportunity.

    The cap is the *ceiling* on the Kelly fraction, not the actual bet size.
    Kelly's probability weighting still applies beneath it — a 5% YES with
    large edge will have a small Kelly fraction and therefore bet less than
    a 70% YES with moderate edge, even if both see the same cap.

    Tiers
    -----
    edge ≤ high_edge_threshold   →  cap = base_bet_pct   (e.g. 3%)
    edge ≥ max_edge_threshold    →  cap = max_bet_pct    (e.g. 6%)
    in between                   →  linear interpolation

    Parameters
    ----------
    raw_edge : float
        ``fair_prob - kalshi_price`` for the side being evaluated.
    base_bet_pct : float
        Bet cap for normal-edge opportunities.
    max_bet_pct : float
        Bet cap ceiling for very-high-edge opportunities.
    high_edge_threshold : float
        Edge level at which the cap begins scaling above ``base_bet_pct``.
    max_edge_threshold : float
        Edge level at which the cap reaches ``max_bet_pct``.

    Returns
    -------
    float — bet cap as a fraction of bankroll.
    """
    if raw_edge <= high_edge_threshold:
        return base_bet_pct
    if raw_edge >= max_edge_threshold:
        return max_bet_pct
    scale = (raw_edge - high_edge_threshold) / (max_edge_threshold - high_edge_threshold)
    return base_bet_pct + (max_bet_pct - base_bet_pct) * scale


# ── High-level calculator class ───────────────────────────────────────────────

class EdgeCalculator:
    """
    Stateless calculator that wraps config-driven defaults.

    Parameters
    ----------
    fee_pct : float
        Kalshi platform fee on winnings (from config.yaml ``edge.fee_pct``).
    kelly_fraction : float
        Fractional Kelly multiplier (from ``risk.kelly_fraction``).
    base_bet_pct : float
        Per-bet cap for normal-edge opportunities (from ``risk.base_bet_pct``).
        Kelly is capped at this fraction of bankroll when edge ≤ high_edge_threshold.
    max_bet_pct : float
        Per-bet cap ceiling for very-high-edge opportunities (``risk.max_bet_pct``).
        Kelly is capped at this fraction when edge ≥ max_edge_threshold.
    high_edge_threshold : float
        Edge at which the bet cap starts scaling above ``base_bet_pct``.
    max_edge_threshold : float
        Edge at which the bet cap reaches ``max_bet_pct``.
    min_edge_pct : float
        Minimum raw edge to be considered a valid opportunity.
    min_ev : float
        Minimum EV per dollar to be considered a valid opportunity.

    Sizing logic
    ------------
    The Kelly formula already normalises for win probability — a 5% YES at
    3¢ with large edge produces a much smaller Kelly fraction than a 70% YES
    at 60¢ with moderate edge.  The dynamic cap raises the *ceiling* for
    exceptional opportunities without overriding that probability weighting.

    Cap schedule (linear interpolation between thresholds):
        edge ≤ high_edge_threshold  →  cap = base_bet_pct  (e.g. 3%)
        edge ≥ max_edge_threshold   →  cap = max_bet_pct   (e.g. 6%)
        in between                  →  linear blend
    """

    def __init__(
        self,
        fee_pct: float = 0.07,
        kelly_fraction: float = 0.25,
        base_bet_pct: float = 0.03,
        max_bet_pct: float = 0.06,
        high_edge_threshold: float = 0.10,
        max_edge_threshold: float = 0.25,
        min_edge_pct: float = 0.03,
        min_ev: float = 0.01,
    ) -> None:
        self.fee_pct             = fee_pct
        self.kelly_fraction      = kelly_fraction
        self.base_bet_pct        = base_bet_pct
        self.max_bet_pct         = max_bet_pct
        self.high_edge_threshold = high_edge_threshold
        self.max_edge_threshold  = max_edge_threshold
        self.min_edge_pct        = min_edge_pct
        self.min_ev              = min_ev

    @classmethod
    def from_config(cls, config: dict) -> "EdgeCalculator":
        """
        Construct from the loaded config.yaml dict.

        Parameters
        ----------
        config : dict
            Top-level config dict (``load_config()`` output).
        """
        edge_cfg = config.get("edge", {})
        risk_cfg = config.get("risk", {})
        # base_bet_pct falls back to legacy max_bet_pct key for backward compat
        base_bet_pct = risk_cfg.get(
            "base_bet_pct", risk_cfg.get("max_bet_pct", 0.03)
        )
        return cls(
            fee_pct=edge_cfg.get("fee_pct", 0.07),
            kelly_fraction=risk_cfg.get("kelly_fraction", 0.25),
            base_bet_pct=base_bet_pct,
            max_bet_pct=risk_cfg.get("max_bet_pct", 0.06),
            high_edge_threshold=risk_cfg.get("high_edge_threshold", 0.10),
            max_edge_threshold=risk_cfg.get("max_edge_threshold", 0.25),
            min_edge_pct=edge_cfg.get("min_edge_pct", 0.03),
            min_ev=edge_cfg.get("min_ev", 0.01),
        )

    def evaluate(
        self,
        fair_prob: float,
        kalshi_yes_price: float,
        side: str = "YES",
        confidence: float = 1.0,
    ) -> EdgeResult:
        """
        Evaluate edge for a single side of a Kalshi market.

        Parameters
        ----------
        fair_prob : float
            Consensus devigged YES probability (before converting for NO).
        kalshi_yes_price : float
            Mid-orderbook price for YES (0 < p < 1).
        side : "YES" | "NO"
            Which side to evaluate.
        confidence : float
            Model confidence multiplier for Kelly sizing.

        Returns
        -------
        EdgeResult
        """
        if side not in ("YES", "NO"):
            raise ValueError(f"side must be 'YES' or 'NO', got {side!r}")

        if side == "YES":
            this_fair = fair_prob
            this_price = kalshi_yes_price
        else:
            this_fair = 1.0 - fair_prob
            this_price = 1.0 - kalshi_yes_price

        edge = calc_raw_edge(this_fair, this_price)
        ev = calc_ev(this_fair, this_price, self.fee_pct)
        kelly_full = calc_kelly(this_fair, this_price, self.fee_pct)

        # Dynamic cap: scales from base_bet_pct → max_bet_pct as edge grows.
        # Kelly still handles probability weighting beneath the cap — a 5% YES
        # produces a much smaller Kelly fraction than a 70% YES regardless of
        # which cap tier applies.
        cap = _dynamic_bet_cap(
            raw_edge=edge,
            base_bet_pct=self.base_bet_pct,
            max_bet_pct=self.max_bet_pct,
            high_edge_threshold=self.high_edge_threshold,
            max_edge_threshold=self.max_edge_threshold,
        )
        kelly_frac = adjust_kelly_for_confidence(
            kelly_full,
            self.kelly_fraction,
            confidence,
            cap,
        )

        return EdgeResult(
            side=side,
            fair_prob=this_fair,
            kalshi_price=this_price,
            raw_edge=edge,
            ev=ev,
            kelly_full=kelly_full,
            kelly_fractional=kelly_frac,
            fee_pct=self.fee_pct,
            kelly_fraction=self.kelly_fraction,
            bet_cap=cap,
        )

    def evaluate_both_sides(
        self,
        fair_yes_prob: float,
        kalshi_yes_price: float,
        confidence: float = 1.0,
    ) -> tuple[EdgeResult, EdgeResult]:
        """
        Evaluate both YES and NO sides in one call.

        Returns
        -------
        (yes_result, no_result)
        """
        yes = self.evaluate(fair_yes_prob, kalshi_yes_price, "YES", confidence)
        no = self.evaluate(fair_yes_prob, kalshi_yes_price, "NO", confidence)
        return yes, no

    def best_side(
        self,
        fair_yes_prob: float,
        kalshi_yes_price: float,
        confidence: float = 1.0,
    ) -> Optional[EdgeResult]:
        """
        Return the better-value side, or None if neither clears the filters.

        Applies ``min_edge_pct`` and ``min_ev`` filters before returning.
        """
        yes, no = self.evaluate_both_sides(fair_yes_prob, kalshi_yes_price, confidence)

        candidates = [
            r for r in (yes, no)
            if r.raw_edge >= self.min_edge_pct and r.ev >= self.min_ev
        ]
        if not candidates:
            return None
        # Return the side with higher EV
        return max(candidates, key=lambda r: r.ev)


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_prob(val: float, name: str) -> None:
    if not (0.0 <= val <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {val}")


def _validate_price(val: float, name: str) -> None:
    if not (0.0 < val < 1.0):
        raise ValueError(f"{name} must be in (0, 1), got {val}")


def _validate_fee(val: float) -> None:
    if not (0.0 <= val < 1.0):
        raise ValueError(f"fee_pct must be in [0, 1), got {val}")
