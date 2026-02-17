"""Utility helpers: odds conversion, probability math, rate limiting."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Odds / probability conversion
# ---------------------------------------------------------------------------


def american_to_implied_prob(american_odds: int) -> float:
    """Convert American odds to implied probability (0-1).

    Examples:
        +150 → 0.4   (risk 100 to win 150)
        -200 → 0.6667 (risk 200 to win 100)
    """
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    else:
        return abs(american_odds) / (abs(american_odds) + 100.0)


def decimal_to_implied_prob(decimal_odds: float) -> float:
    """Convert decimal (European) odds to implied probability."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


def implied_prob_to_american(prob: float) -> int:
    """Convert an implied probability to American odds."""
    if prob <= 0 or prob >= 1:
        raise ValueError(f"Probability must be in (0,1), got {prob}")
    if prob >= 0.5:
        return int(-100.0 * prob / (1.0 - prob))
    else:
        return int(100.0 * (1.0 - prob) / prob)


def remove_vig(probs: list[float]) -> list[float]:
    """Remove the overround (vig) from a set of implied probabilities.

    Scales probabilities so they sum to 1.0.
    """
    total = sum(probs)
    if total == 0:
        return probs
    return [p / total for p in probs]


def calculate_overround(probs: list[float]) -> float:
    """Calculate the overround/vig as a percentage.

    A fair market sums to 1.0 (0% overround).
    """
    return (sum(probs) - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Kelly criterion
# ---------------------------------------------------------------------------


def kelly_fraction(win_prob: float, payout_multiplier: float) -> float:
    """Calculate the Kelly criterion fraction for a binary bet.

    Args:
        win_prob: Estimated probability of winning (0-1).
        payout_multiplier: Net payout per unit wagered on a win.
            For a Kalshi YES contract at price p, payout = (1 - p) / p.

    Returns:
        Optimal fraction of bankroll to wager. Negative means don't bet.
    """
    if payout_multiplier <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    q = 1.0 - win_prob
    f = (win_prob * payout_multiplier - q) / payout_multiplier
    return max(f, 0.0)


# ---------------------------------------------------------------------------
# Token-bucket rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(self, max_calls: int, period_seconds: float = 60.0):
        self.max_calls = max_calls
        self.period = period_seconds
        self.tokens = float(max_calls)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.max_calls,
                self.tokens + elapsed * (self.max_calls / self.period),
            )
            self.last_refill = now

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) * (self.period / self.max_calls)
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1.0

    @property
    def tokens_remaining(self) -> float:
        now = time.monotonic()
        elapsed = now - self.last_refill
        return min(
            self.max_calls,
            self.tokens + elapsed * (self.max_calls / self.period),
        )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class KalshiMarket:
    """Represents a single Kalshi market."""
    ticker: str
    title: str
    subtitle: str
    yes_price: float  # 0.01 - 0.99
    no_price: float
    volume: int  # in cents
    open_interest: int
    close_time: Optional[datetime] = None
    status: str = "open"
    category: str = ""
    series_ticker: str = ""
    result: Optional[str] = None  # "yes", "no", or None if unsettled
    yes_ask: Optional[float] = None
    yes_bid: Optional[float] = None
    no_ask: Optional[float] = None
    no_bid: Optional[float] = None


@dataclass
class SportsbookOdds:
    """Odds from a single sportsbook for an event outcome."""
    bookmaker: str
    market_type: str  # "h2h", "spreads", "totals"
    outcome_name: str  # Team name or "Over"/"Under"
    price: float  # Decimal odds
    point: Optional[float] = None  # Spread or total line
    implied_prob: float = 0.0
    last_update: Optional[datetime] = None


@dataclass
class SportsbookEvent:
    """A sporting event with odds from multiple bookmakers."""
    event_id: str
    sport: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmaker_odds: list[SportsbookOdds] = field(default_factory=list)


@dataclass
class MatchedMarket:
    """A Kalshi market matched to a sportsbook event."""
    kalshi_market: KalshiMarket
    sportsbook_event: SportsbookEvent
    match_type: str  # "moneyline", "spread", "total"
    match_confidence: float  # 0-1 fuzzy match quality
    kalshi_side: str  # Which Kalshi side maps to the sportsbook outcome
    sharp_probability: float  # Consensus sharp probability
    num_books_agreeing: int
    line_movement: float = 0.0  # Positive = moving toward Kalshi side


@dataclass
class TradeOpportunity:
    """A potential trade with computed edge."""
    matched_market: MatchedMarket
    side: str  # "yes" or "no"
    edge_pct: float
    expected_value: float
    kelly_fraction: float
    confidence_score: float
    recommended_size_usd: float = 0.0


@dataclass
class TradeRecord:
    """A completed (or paper) trade for logging."""
    trade_id: str
    timestamp: str
    ticker: str
    sport: str
    market_type: str
    teams: str
    event_time: str
    side: str  # "yes" or "no"
    kalshi_price_at_entry: float
    sharp_prob_at_entry: float
    edge_at_entry: float
    position_size_usd: float
    kalshi_volume: int
    num_books: int
    time_to_event_hours: float
    line_movement: float
    order_type: str
    mode: str  # "paper" or "live"
    outcome: Optional[str] = None  # "win", "loss", "push", None
    pnl: Optional[float] = None
    order_id: Optional[str] = None
    hour_of_day: int = 0


def utcnow() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)
