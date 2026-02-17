"""Edge calculation: computes edge, EV, Kelly, and confidence for trade opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.logger_setup import get_logger
from src.utils import (
    MatchedMarket,
    TradeOpportunity,
    calculate_overround,
    kelly_fraction,
)

logger = get_logger("edge_calculator")

# Kalshi fee structure (approximate)
# Taker fee on profit: typically 7-10% (we use 7% as a conservative estimate)
DEFAULT_KALSHI_FEE_RATE = 0.07


@dataclass
class EdgeResult:
    """Detailed edge calculation result."""
    edge_pct: float  # Edge as a percentage (positive = profitable)
    expected_value: float  # EV per dollar wagered
    kelly_fraction: float  # Optimal Kelly bet fraction
    net_edge_pct: float  # Edge after fees
    net_ev: float  # EV after fees
    kalshi_overround: float  # Kalshi market overround %
    confidence_score: float  # Overall confidence 0-1
    side: str  # Recommended side: "yes" or "no"


def calculate_edge(
    kalshi_yes_price: float,
    sharp_probability: float,
    side: str = "yes",
    fee_rate: float = DEFAULT_KALSHI_FEE_RATE,
) -> EdgeResult:
    """Calculate the edge between a Kalshi price and sharp sportsbook probability.

    Args:
        kalshi_yes_price: Current Kalshi YES contract price (0.01 to 0.99).
        sharp_probability: Consensus probability from sharp books (0.0 to 1.0).
        side: "yes" or "no" — which side to evaluate buying.
        fee_rate: Kalshi's fee rate on profits (default 7%).

    Returns:
        EdgeResult with all computed metrics.
    """
    # Clamp inputs
    kalshi_yes_price = max(0.01, min(0.99, kalshi_yes_price))
    sharp_probability = max(0.001, min(0.999, sharp_probability))

    kalshi_no_price = 1.0 - kalshi_yes_price

    if side == "yes":
        cost = kalshi_yes_price
        win_prob = sharp_probability
        payout_on_win = 1.0 - cost  # Profit if YES wins
    else:
        cost = kalshi_no_price
        win_prob = 1.0 - sharp_probability
        payout_on_win = 1.0 - cost  # Profit if NO wins

    # Raw edge (before fees)
    raw_edge = win_prob - cost
    raw_edge_pct = raw_edge * 100.0

    # EV per dollar wagered (before fees)
    raw_ev = (win_prob * payout_on_win) - ((1.0 - win_prob) * cost)

    # After fees: fee is charged on profit only
    net_payout_on_win = payout_on_win * (1.0 - fee_rate)
    net_ev = (win_prob * net_payout_on_win) - ((1.0 - win_prob) * cost)
    net_edge = net_ev  # Per dollar
    net_edge_pct = net_edge * 100.0

    # Kelly fraction (using net payouts)
    payout_multiplier = net_payout_on_win / cost if cost > 0 else 0
    kelly = kelly_fraction(win_prob, payout_multiplier)

    # Kalshi overround
    overround = calculate_overround([kalshi_yes_price, kalshi_no_price])

    return EdgeResult(
        edge_pct=raw_edge_pct,
        expected_value=raw_ev,
        kelly_fraction=kelly,
        net_edge_pct=net_edge_pct,
        net_ev=net_ev,
        kalshi_overround=overround,
        confidence_score=0.0,  # Set by evaluate_opportunity
        side=side,
    )


def evaluate_opportunity(
    matched_market: MatchedMarket,
    min_edge_pct: float = 3.0,
    max_edge_pct: float = 25.0,
    fee_rate: float = DEFAULT_KALSHI_FEE_RATE,
    min_books: int = 3,
) -> Optional[TradeOpportunity]:
    """Evaluate a matched market for trading opportunity.

    Calculates edge for both YES and NO sides, picks the better one,
    and applies filters.

    Args:
        matched_market: A matched Kalshi/sportsbook market pair.
        min_edge_pct: Minimum net edge % required.
        max_edge_pct: Maximum edge % (skip if exceeded — likely bad data).
        fee_rate: Kalshi fee rate.
        min_books: Minimum sportsbooks required for consensus.

    Returns:
        TradeOpportunity if edge meets thresholds, else None.
    """
    km = matched_market.kalshi_market
    sharp_prob = matched_market.sharp_probability

    if sharp_prob <= 0 or sharp_prob >= 1:
        return None

    # Calculate edge for both sides
    yes_edge = calculate_edge(km.yes_price, sharp_prob, "yes", fee_rate)
    no_edge = calculate_edge(km.yes_price, sharp_prob, "no", fee_rate)

    # Pick the better side
    if yes_edge.net_edge_pct >= no_edge.net_edge_pct:
        best = yes_edge
        side = "yes"
    else:
        best = no_edge
        side = "no"

    # Filter checks
    if best.net_edge_pct < min_edge_pct:
        logger.debug(
            f"Skip {km.ticker}: net edge {best.net_edge_pct:.2f}% < min {min_edge_pct}%",
            extra={"ticker": km.ticker, "edge": best.net_edge_pct, "reason": "below_min_edge"},
        )
        return None

    if best.net_edge_pct > max_edge_pct:
        logger.warning(
            f"Skip {km.ticker}: net edge {best.net_edge_pct:.2f}% > max {max_edge_pct}% "
            f"(likely stale data)",
            extra={"ticker": km.ticker, "edge": best.net_edge_pct, "reason": "above_max_edge"},
        )
        return None

    if matched_market.num_books_agreeing < min_books:
        logger.debug(
            f"Skip {km.ticker}: only {matched_market.num_books_agreeing} books "
            f"(need {min_books})",
            extra={"ticker": km.ticker, "reason": "insufficient_books"},
        )
        return None

    # Calculate confidence score
    confidence = _compute_confidence(matched_market, best)
    best_with_confidence = EdgeResult(
        edge_pct=best.edge_pct,
        expected_value=best.expected_value,
        kelly_fraction=best.kelly_fraction,
        net_edge_pct=best.net_edge_pct,
        net_ev=best.net_ev,
        kalshi_overround=best.kalshi_overround,
        confidence_score=confidence,
        side=side,
    )

    return TradeOpportunity(
        matched_market=matched_market,
        side=side,
        edge_pct=best_with_confidence.net_edge_pct,
        expected_value=best_with_confidence.net_ev,
        kelly_fraction=best_with_confidence.kelly_fraction,
        confidence_score=confidence,
    )


def _compute_confidence(
    matched_market: MatchedMarket, edge_result: EdgeResult
) -> float:
    """Compute a confidence score (0-1) for a trade opportunity.

    Factors:
    - Number of bookmakers agreeing (more = better)
    - Match quality (fuzzy match confidence)
    - Market volume (higher = more reliable)
    - Time to event (not too close, not too far)
    - Edge magnitude (moderate is better than extreme)
    """
    score = 0.0

    # Books agreeing: 3=0.3, 5=0.5, 7+=0.7 (max 0.25)
    books_score = min(matched_market.num_books_agreeing / 10.0, 0.25)
    score += books_score

    # Match confidence (max 0.25)
    match_score = matched_market.match_confidence * 0.25
    score += match_score

    # Volume factor (max 0.2)
    volume = matched_market.kalshi_market.volume
    if volume >= 500000:
        vol_score = 0.2
    elif volume >= 100000:
        vol_score = 0.15
    elif volume >= 50000:
        vol_score = 0.1
    else:
        vol_score = 0.05
    score += vol_score

    # Time to event (max 0.15) — 12-72 hours is ideal
    if matched_market.kalshi_market.close_time:
        now = datetime.now(timezone.utc)
        hours_to_event = max(
            0,
            (matched_market.kalshi_market.close_time - now).total_seconds() / 3600,
        )
        if 12 <= hours_to_event <= 72:
            time_score = 0.15
        elif 4 <= hours_to_event <= 168:
            time_score = 0.10
        else:
            time_score = 0.05
    else:
        time_score = 0.08
    score += time_score

    # Edge magnitude (max 0.15) — moderate edge is more trustworthy
    edge = abs(edge_result.net_edge_pct)
    if 3 <= edge <= 8:
        edge_score = 0.15
    elif 2 <= edge <= 12:
        edge_score = 0.10
    else:
        edge_score = 0.05
    score += edge_score

    return min(score, 1.0)


def compute_edge_for_logging(
    kalshi_yes_price: float,
    sharp_probability: float,
    fee_rate: float = DEFAULT_KALSHI_FEE_RATE,
) -> dict:
    """Compute edge metrics for both sides, returning a dict for logging."""
    yes = calculate_edge(kalshi_yes_price, sharp_probability, "yes", fee_rate)
    no = calculate_edge(kalshi_yes_price, sharp_probability, "no", fee_rate)

    best_side = "yes" if yes.net_edge_pct >= no.net_edge_pct else "no"
    best = yes if best_side == "yes" else no

    return {
        "kalshi_yes_price": kalshi_yes_price,
        "sharp_probability": sharp_probability,
        "best_side": best_side,
        "raw_edge_pct": best.edge_pct,
        "net_edge_pct": best.net_edge_pct,
        "ev_per_dollar": best.net_ev,
        "kelly_fraction": best.kelly_fraction,
        "kalshi_overround_pct": best.kalshi_overround,
        "yes_raw_edge_pct": yes.edge_pct,
        "no_raw_edge_pct": no.edge_pct,
    }
