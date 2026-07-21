"""De-vigging utilities: convert vig-inflated sharp book odds into fair (no-vig)
probabilities. Fair prob is the accepted truth-proxy for CLV benchmarking.

The MLB edge analysis (2026-07) confirmed the sharp fair_prob is well-calibrated
for MLB (~+1pp vs realised), so a proper de-vig is the reference line for CLV.

Two de-vig methods:
  • multiplicative — standard normalise; unbiased in the 35-65% band (MLB-style
    two-way lines).
  • power (Shin-style) — solves sum(p_i**k)=1; shrinks longshots more than
    favourites. Correct for longshot-heavy multi-outcome fields (golf outrights,
    top-N), where multiplicative de-vig systematically misprices the tail.
"""
from __future__ import annotations
from typing import List, Sequence


def american_to_prob(odds: float) -> float:
    """American odds -> raw implied probability (includes vig)."""
    if odds == 0:
        raise ValueError("american odds cannot be 0")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return (-odds) / ((-odds) + 100.0)


def decimal_to_prob(dec: float) -> float:
    """Decimal odds -> raw implied probability (includes vig)."""
    if dec <= 1.0:
        raise ValueError("decimal odds must be > 1.0")
    return 1.0 / dec


def devig_multiplicative(probs: Sequence[float]) -> List[float]:
    """Multiplicative (normalised) de-vig: divide each raw implied prob by their
    sum. Standard method; unbiased in the 35-65% band where our MLB edge lives.
    """
    s = sum(probs)
    if s <= 0:
        raise ValueError("sum of implied probabilities must be positive")
    return [p / s for p in probs]


def devig_power(probs: Sequence[float], tol: float = 1e-9,
                max_iter: int = 100) -> List[float]:
    """Power (Shin-style) de-vig: solve sum(p_i ** k) = 1 for k, return p_i**k.

    For an overround book (sum > 1), k > 1, which shrinks longshots more than
    favourites — the correct direction for longshot-heavy golf fields (outright
    winner, top-N). Falls back to multiplicative for <2 outcomes.
    """
    ps = [max(min(float(p), 1.0 - 1e-12), 1e-12) for p in probs]
    s = sum(ps)
    if s <= 0:
        raise ValueError("sum of implied probabilities must be positive")
    if len(ps) < 2:
        return [p / s for p in ps]

    lo, hi = 0.5, 5.0
    for _ in range(60):  # expand hi until sum(p**hi) <= 1
        if sum(p ** hi for p in ps) <= 1.0:
            break
        hi *= 1.5
    for _ in range(max_iter):
        k = (lo + hi) / 2.0
        val = sum(p ** k for p in ps)
        if abs(val - 1.0) < tol:
            break
        if val > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    return [p ** k for p in ps]


def devig_two_way(prob_side: float, prob_other: float) -> float:
    """Fair (no-vig) probability of `side` given both raw implied probs."""
    return devig_multiplicative([prob_side, prob_other])[0]


def fair_from_american(odds_side: float, odds_other: float) -> float:
    """Convenience: fair no-vig prob of `side` directly from both American odds."""
    return devig_two_way(american_to_prob(odds_side), american_to_prob(odds_other))
