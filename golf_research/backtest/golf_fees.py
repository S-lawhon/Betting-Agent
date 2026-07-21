"""
golf_research/backtest/golf_fees.py
───────────────────────────────────
Series-aware Kalshi fee model for golf, plus power/Shin de-vig.

WHY this exists separately from src/kalshi_fees.py:
The engine's kalshi_fees.fee_per_contract(price, maker=True) returns
0.0175*P*(1-P) — the *general* Kalshi maker rate. But golf's derivative
series (top-N, make-cut, H2H, 3-ball, round leaders) are fee_type
`quadratic`, which charges makers ZERO. Only the winner series
(KXPGATOUR, KXTHEOPEN, KXPGA, KXPGARYDER, KXPGASOLHEIM) are
`quadratic_with_maker_fees`. Confirmed live via /series metadata
2026-07-19. Using the general maker rate on a prop maker strategy would
understate net edge by ~0.4c/contract at 20c — enough to matter.

These functions are intended to be promoted into src/kalshi_fees.py
(series_maker_charges_fee) once the backtest validates the strategy.
"""
from __future__ import annotations

import math
from typing import List, Sequence

TAKER_COEF = 0.07
MAKER_COEF_GENERAL = 0.0175  # matches src/kalshi_fees.MAKER_COEF

# Prop/derivative series: fee_type == "quadratic" → makers pay 0.
# Winner series: fee_type == "quadratic_with_maker_fees" → makers pay.
# Series prefixes verified against live /series metadata (2026-07-19).
_MAKER_FEE_SERIES_PREFIXES = (
    "KXPGATOUR", "KXTHEOPEN", "KXPGA-", "KXPGARYDER", "KXPGASOLHEIM",
    "KXLPGATOUR", "KXLIVTOUR", "KXCHAMPTOUR",
)


def series_maker_charges_fee(series_ticker: str) -> bool:
    """True if this series charges maker fees (quadratic_with_maker_fees).

    Golf prop series (top-N, make-cut, H2H, 3-ball, round leaders) return
    False → maker fee is zero. Winner/outright series return True.
    """
    s = (series_ticker or "").upper()
    # KXPGA (PGA Championship winner) needs the trailing '-' to avoid
    # matching KXPGATOP*, KXPGAMAKECUT, etc.
    return any(s == p.rstrip("-") or s.startswith(p) for p in _MAKER_FEE_SERIES_PREFIXES)


def _roundup_cent(x: float) -> float:
    return math.ceil(x * 100.0 - 1e-9) / 100.0


def fee_per_contract(price: float, maker: bool = False,
                     series_ticker: str = "") -> float:
    """Marginal (un-rounded) fee for ONE contract, in dollars.

    Taker: 0.07*P*(1-P) always.
    Maker: 0 for `quadratic` series (all golf props); 0.0175*P*(1-P) for
           `quadratic_with_maker_fees` series (winner markets).
    Pass series_ticker to get the correct maker treatment; if omitted and
    maker=True, falls back to the conservative general maker rate.
    """
    if not maker:
        return TAKER_COEF * price * (1.0 - price)
    if series_ticker and not series_maker_charges_fee(series_ticker):
        return 0.0
    if series_ticker:  # known maker-fee series
        return MAKER_COEF_GENERAL * price * (1.0 - price)
    # Unknown series + maker: be conservative (charge the general rate).
    return MAKER_COEF_GENERAL * price * (1.0 - price)


def fee_total(contracts: float, price: float, maker: bool = False,
              series_ticker: str = "") -> float:
    """Order fee rounded up to the cent, in dollars."""
    per = fee_per_contract(price, maker, series_ticker)
    return _roundup_cent(per * contracts)


# ── De-vig: power method for longshot-heavy fields ───────────────────────
# Multiplicative de-vig (src/devig.py) is unbiased in the 35-65% band but
# systematically misprices longshots — exactly where golf top-N contracts
# live (10-40c). The power method finds k such that sum(p_i**k) = 1, which
# fits favorite-longshot structure far better for large fields.


def devig_power(raw_probs: Sequence[float], tol: float = 1e-9,
                max_iter: int = 100) -> List[float]:
    """Power de-vig: solve sum(p_i ** k) = 1 for k, return p_i ** k.

    For an overround book (sum > 1), k > 1, which shrinks longshots more
    than favorites — the correct direction for golf fields. Falls back to
    multiplicative if the solve fails (e.g. a single-outcome input).
    """
    probs = [max(min(float(p), 1.0 - 1e-12), 1e-12) for p in raw_probs]
    s = sum(probs)
    if s <= 0:
        raise ValueError("sum of implied probabilities must be positive")
    if len(probs) < 2:
        return [p / s for p in probs]

    lo, hi = 0.5, 5.0
    # Expand hi until sum(p**hi) <= 1 (monotone decreasing in k for p<1).
    for _ in range(60):
        if sum(p ** hi for p in probs) <= 1.0:
            break
        hi *= 1.5
    for _ in range(max_iter):
        k = (lo + hi) / 2.0
        val = sum(p ** k for p in probs)
        if abs(val - 1.0) < tol:
            break
        if val > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    return [p ** k for p in probs]


def devig_multiplicative(raw_probs: Sequence[float]) -> List[float]:
    """Standard normalised de-vig (mirror of src/devig.py for comparison)."""
    s = sum(raw_probs)
    if s <= 0:
        raise ValueError("sum of implied probabilities must be positive")
    return [p / s for p in raw_probs]
