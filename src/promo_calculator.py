"""
src/promo_calculator.py
──────────────────────
Shared promo math for T1 pods.

Three promo types supported:

FREE BET
  You receive $F "free bet" credit at a sportsbook.  If the bet wins you
  receive profit only (no stake returned).  Hedge on the opposite side at
  Kalshi/Polymarket to lock in a guaranteed cash amount regardless of outcome.

  Key formula:
    hedge_size = free_bet × (sportsbook_decimal_odds − 1) × no_price_hedge
    guaranteed_profit = hedge_size × (1 / no_price_hedge − 1)
    conversion_rate = guaranteed_profit / free_bet

RISK-FREE BET
  You place a regular $F bet; if it loses the sportsbook refunds F as a
  free bet.  EV = probability_win × (d_sb − 1) × F + probability_lose × conv_rate × F
  where conv_rate is the expected free bet conversion rate for this market.

ODDS BOOST
  The sportsbook increases the payout on a specific market from d_original
  to d_boosted on a max stake of $S.
  EV = S × (d_boosted − 1) × fair_yes_prob − S
     = S × (d_boosted × fair_yes_prob − 1)
  Optional hedge: place bet at boosted odds, sell at current market price to
  lock in guaranteed edge.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ── Odds conversion ───────────────────────────────────────────────────

def american_to_decimal(american_odds: float) -> float:
    """Convert American (moneyline) odds to decimal odds.

    Examples:
        american_to_decimal(+150) → 2.50
        american_to_decimal(-110) → 1.909...
        american_to_decimal(+100) → 2.00
    """
    if american_odds >= 100:
        return 1.0 + american_odds / 100.0
    else:
        return 1.0 + 100.0 / abs(american_odds)


def decimal_to_american(decimal_odds: float) -> int:
    """Convert decimal odds to American (moneyline) odds (nearest integer).

    Examples:
        decimal_to_american(2.50) → +150
        decimal_to_american(1.91) → -110
    """
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1.0) * 100)
    else:
        return round(-100.0 / (decimal_odds - 1.0))


def decimal_to_implied_prob(decimal_odds: float) -> float:
    """Implied probability from decimal odds (includes vig).

    Args:
        decimal_odds: e.g. 2.0 for even money, 1.5 for -200.

    Returns:
        Implied probability in [0, 1].
    """
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


# ── Free bet calculations ─────────────────────────────────────────────

def free_bet_hedge_size(
    free_bet_amount: float,
    decimal_odds_sb: float,
    no_price_hedge: float,
) -> float:
    """Calculate the optimal hedge size for a free bet.

    Strategy:
        Place free bet on YES at sportsbook (decimal odds d_sb).
        Hedge on NO at Kalshi/Polymarket (NO price p_no).

        If YES wins: free bet yields F × (d_sb − 1) profit; hedge loses H.
        If NO wins:  free bet yields $0; hedge yields H × (1/p_no − 1).

        Equalise:   F×(d_sb−1) − H = H×(1/p_no − 1)
                    F×(d_sb−1) = H × (1/p_no)
                    H = F × (d_sb−1) × p_no

    Args:
        free_bet_amount:  Face value of the free bet in USD.
        decimal_odds_sb:  Sportsbook decimal odds for the YES side.
        no_price_hedge:   Price (0–1) to buy NO at Kalshi/Polymarket.

    Returns:
        Optimal hedge amount in USD (rounded to 2 decimal places).
    """
    free_bet_profit = free_bet_amount * (decimal_odds_sb - 1.0)
    hedge = free_bet_profit * no_price_hedge
    return round(hedge, 2)


def free_bet_guaranteed_profit(
    free_bet_amount: float,
    decimal_odds_sb: float,
    no_price_hedge: float,
) -> float:
    """Calculate the guaranteed cash locked in from a free bet hedge.

    This is the profit collected from the NO side winning:
        profit = hedge_size × (1/p_no − 1)

    Args:
        free_bet_amount:  Face value of the free bet in USD.
        decimal_odds_sb:  Sportsbook decimal odds for the YES side.
        no_price_hedge:   Price (0–1) to buy NO at Kalshi/Polymarket.

    Returns:
        Guaranteed profit in USD (rounded to 2 decimal places).
    """
    if no_price_hedge <= 0 or no_price_hedge >= 1.0:
        return 0.0
    h = free_bet_hedge_size(free_bet_amount, decimal_odds_sb, no_price_hedge)
    profit = h * (1.0 / no_price_hedge - 1.0)
    return round(profit, 2)


def free_bet_conversion_rate(
    decimal_odds_sb: float,
    no_price_hedge: float,
) -> float:
    """What fraction of the free bet face value is locked in as cash.

    conversion_rate = guaranteed_profit / free_bet_amount
                    = (d_sb − 1) × (1 − p_no)

    Typical values: 55–75%.  Higher odds at sportsbook → higher conversion.
    Lower NO price at hedge venue → higher conversion.

    Args:
        decimal_odds_sb:  Sportsbook decimal odds for the YES side.
        no_price_hedge:   Price (0–1) to buy NO at Kalshi/Polymarket.

    Returns:
        Conversion rate in [0, 1].
    """
    if no_price_hedge <= 0 or no_price_hedge >= 1.0:
        return 0.0
    return round((decimal_odds_sb - 1.0) * (1.0 - no_price_hedge), 4)


# ── Risk-free bet calculations ─────────────────────────────────────────

def risk_free_ev(
    risk_free_amount: float,
    decimal_odds_sb: float,
    fair_yes_prob: float,
    free_bet_conversion_pct: float = 0.65,
) -> float:
    """Expected value of a risk-free bet.

    If YES wins: you keep full sportsbook profit.
    If NO wins:  you get the stake back as a free bet worth
                 risk_free_amount × free_bet_conversion_pct.

    EV = p_yes × F×(d_sb−1) + (1−p_yes) × F × conv_pct − F
       = F × [p_yes×(d_sb−1) + (1−p_yes)×conv_pct − 1]

    Args:
        risk_free_amount:         Bet size in USD.
        decimal_odds_sb:          Sportsbook decimal odds.
        fair_yes_prob:            True probability of YES outcome.
        free_bet_conversion_pct:  Expected conversion rate of returned free bet.

    Returns:
        Expected value in USD.
    """
    p_no = 1.0 - fair_yes_prob
    ev = risk_free_amount * (
        fair_yes_prob * (decimal_odds_sb - 1.0)
        + p_no * free_bet_conversion_pct
        - 1.0
    )
    return round(ev, 2)


# ── Odds boost calculations ───────────────────────────────────────────

def odds_boost_ev(
    stake: float,
    boosted_decimal_odds: float,
    fair_yes_prob: float,
) -> float:
    """Expected value of an odds boost.

    EV = stake × (boosted_decimal_odds − 1) × fair_yes_prob − stake × (1 − fair_yes_prob)
       = stake × [boosted_decimal_odds × fair_yes_prob − 1]

    A boost is +EV when boosted_decimal_odds > 1/fair_yes_prob.

    Args:
        stake:                 Amount to wager in USD.
        boosted_decimal_odds:  Sportsbook's boosted decimal odds.
        fair_yes_prob:         True probability of YES outcome.

    Returns:
        Expected value in USD.
    """
    ev = stake * (boosted_decimal_odds * fair_yes_prob - 1.0)
    return round(ev, 2)


def odds_boost_ev_pct(
    boosted_decimal_odds: float,
    fair_yes_prob: float,
) -> float:
    """EV as a fraction of stake.

    Returns:
        Edge percentage (e.g. 0.08 = 8% EV per dollar wagered).
    """
    return round(boosted_decimal_odds * fair_yes_prob - 1.0, 4)


def odds_boost_hedge_size(
    stake: float,
    boosted_decimal_odds: float,
    no_price_hedge: float,
) -> float:
    """Optimal hedge to lock in guaranteed profit from a boost.

    If boost side wins: stake × (d_boosted − 1) − H
    If other side wins: H × (1/p_no − 1) − stake (regular bet, stake is lost)

    Equalise:
        stake × (d_boosted − 1) − H = H × (1/p_no − 1) − stake
        stake × d_boosted − H × (1/p_no) = 0     [simplified]
        H = stake × d_boosted × p_no

    Args:
        stake:                Amount wagered on boosted side.
        boosted_decimal_odds: Sportsbook's boosted decimal odds.
        no_price_hedge:       Price (0–1) to buy NO at Kalshi/Polymarket.

    Returns:
        Optimal hedge amount in USD.
    """
    h = stake * boosted_decimal_odds * no_price_hedge
    return round(h, 2)


def odds_boost_locked_profit(
    stake: float,
    boosted_decimal_odds: float,
    no_price_hedge: float,
) -> float:
    """Guaranteed profit after hedging a boost.

    profit = H × (1/p_no − 1) − stake
           = stake × d_boosted × p_no × (1−p_no)/p_no − stake
           = stake × (d_boosted × (1−p_no) − 1)

    Only positive when d_boosted × (1 − p_no) > 1.

    Returns:
        Guaranteed locked profit in USD.
    """
    locked = stake * (boosted_decimal_odds * (1.0 - no_price_hedge) - 1.0)
    return round(locked, 2)


# ── PromoRecord dataclass ─────────────────────────────────────────────

@dataclass
class PromoRecord:
    """A single sportsbook promotional offer.

    Covers all three promo types:
        free_bet, risk_free, odds_boost.
    """

    promo_id: str              # Unique ID, e.g. "FD-FB-2026-01-15"
    sportsbook: str            # "fanduel", "draftkings", "caesars", etc.
    promo_type: str            # "free_bet" | "risk_free" | "odds_boost"
    amount: float              # USD face value / max stake

    # For free_bet and risk_free
    boosted_odds: Optional[float] = None       # American odds at sportsbook
    fair_yes_prob: Optional[float] = None      # True probability (from edge calc)
    hedge_venue: str = "kalshi"                # Where to hedge
    hedge_market_id: Optional[str] = None     # e.g. Kalshi ticker
    hedge_no_price: Optional[float] = None    # Current NO price at hedge venue

    # For odds_boost specifically
    original_odds: Optional[float] = None     # Pre-boost American odds
    max_stake: Optional[float] = None         # Sportsbook max stake cap

    # Lifecycle
    expiry: Optional[str] = None              # ISO date "2026-01-16"
    status: str = "pending"                   # "pending" | "used" | "expired"

    notes: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_active(self) -> bool:
        """Return True if status is pending (not yet used or expired)."""
        return self.status == "pending"

    def effective_stake(self) -> float:
        """Return the applicable stake, respecting boost max_stake cap."""
        if self.promo_type == "odds_boost" and self.max_stake is not None:
            return min(self.amount, self.max_stake)
        return self.amount


# ── Convenience: build PromoRecord from config dict ───────────────────

def promo_from_dict(d: Dict[str, Any]) -> PromoRecord:
    """Build a PromoRecord from a config dictionary.

    Accepts both snake_case and camelCase keys.  Unknown keys go into extra.
    """
    known = {f.name for f in PromoRecord.__dataclass_fields__.values()}
    extra = {k: v for k, v in d.items() if k not in known}
    filtered = {k: v for k, v in d.items() if k in known}
    return PromoRecord(**filtered, extra={**extra, **filtered.get("extra", {})})
