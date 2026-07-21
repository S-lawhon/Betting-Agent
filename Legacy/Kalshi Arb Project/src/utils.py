"""
src/utils.py
────────────
Odds-format conversions and probability utilities.

All functions are pure (no I/O, no side-effects) so they are trivially
testable and safe to call from anywhere in the pipeline.

Supported conversions
─────────────────────
    American  ←→  Decimal  ←→  Probability (implied)

Vig removal
───────────
    Two methods for devigging a two-outcome market (YES/NO or Home/Away):
      • additive     — subtract the overround proportionally (most common)
      • multiplicative — scale each side by 1/overround (identical for 2 outcomes)
      • power (Shin)  — iterative; more accurate when vig > 10%
"""

from __future__ import annotations

import math
from typing import Sequence


# ── American ↔ Decimal ────────────────────────────────────────────────────────

def american_to_decimal(american: float) -> float:
    """
    Convert American (moneyline) odds to decimal odds.

    Examples
    --------
    >>> american_to_decimal(200)   # +200 → 3.0
    3.0
    >>> american_to_decimal(-110)  # -110 → ~1.909
    1.9090909090909092
    """
    if american >= 0:
        return american / 100.0 + 1.0
    return 100.0 / abs(american) + 1.0


def decimal_to_american(decimal: float) -> float:
    """
    Convert decimal odds to American (moneyline) odds.

    Examples
    --------
    >>> decimal_to_american(3.0)    # → +200
    200.0
    >>> round(decimal_to_american(1.909), 1)   # → -110
    -110.0
    """
    if decimal >= 2.0:
        return (decimal - 1.0) * 100.0
    return -100.0 / (decimal - 1.0)


# ── Odds → Probability ────────────────────────────────────────────────────────

def american_to_implied_prob(american: float) -> float:
    """
    Convert American odds to *raw* implied probability (vig still included).

    Examples
    --------
    >>> round(american_to_implied_prob(-110), 4)
    0.5238
    >>> round(american_to_implied_prob(200), 4)
    0.3333
    """
    if american >= 0:
        return 100.0 / (american + 100.0)
    abs_odds = abs(american)
    return abs_odds / (abs_odds + 100.0)


def decimal_to_implied_prob(decimal: float) -> float:
    """
    Convert decimal odds to implied probability (vig still included).

    Examples
    --------
    >>> decimal_to_implied_prob(2.0)
    0.5
    """
    if decimal <= 0:
        raise ValueError(f"Decimal odds must be positive, got {decimal}")
    return 1.0 / decimal


def prob_to_american(prob: float) -> float:
    """
    Convert a fair probability to American odds (no vig).

    Examples
    --------
    >>> prob_to_american(0.5)
    -100.0
    >>> prob_to_american(0.333)
    200.30030030030033
    """
    if not (0 < prob < 1):
        raise ValueError(f"prob must be in (0, 1), got {prob}")
    if prob >= 0.5:
        return -100.0 * prob / (1.0 - prob)
    return 100.0 * (1.0 - prob) / prob


# ── Vig removal ───────────────────────────────────────────────────────────────

def remove_vig_additive(
    probs: Sequence[float],
) -> list[float]:
    """
    Remove bookmaker vig using the additive (proportional) method.

    Scales all implied probabilities so they sum to exactly 1.0.
    For a two-outcome market this is equivalent to the multiplicative method.

    Parameters
    ----------
    probs:
        Sequence of raw implied probabilities (must all be > 0).

    Returns
    -------
    list of fair probabilities summing to 1.0.

    Examples
    --------
    >>> remove_vig_additive([0.5238, 0.5238])   # two -110 sides
    [0.5, 0.5]
    """
    total = sum(probs)
    if total <= 0:
        raise ValueError("Sum of implied probabilities must be positive.")
    return [p / total for p in probs]


def remove_vig_power(
    probs: Sequence[float],
    max_iter: int = 1000,
    tol: float = 1e-9,
) -> list[float]:
    """
    Remove bookmaker vig using the power (Shin) method.

    More accurate than the additive method when the overround is large or
    probabilities are asymmetric. Solves for the exponent k such that
    ``sum(p_i ^ (1/k)) = 1`` via bisection.

    Parameters
    ----------
    probs:
        Sequence of raw implied probabilities.
    max_iter:
        Maximum bisection iterations.
    tol:
        Convergence tolerance on the sum.

    Returns
    -------
    list of fair probabilities summing to 1.0.
    """
    probs = list(probs)
    total = sum(probs)
    if abs(total - 1.0) < tol:
        return probs  # already fair

    # Bisect on k in [1, 10]; at k=1 we recover additive, higher k → more shrinkage
    lo, hi = 1.0, 10.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        s = sum(p ** (1.0 / mid) for p in probs)
        if s > 1.0:
            lo = mid
        else:
            hi = mid
        if abs(s - 1.0) < tol:
            break

    k = (lo + hi) / 2.0
    fair = [p ** (1.0 / k) for p in probs]
    # Normalise to remove any residual floating-point error
    fair_total = sum(fair)
    return [f / fair_total for f in fair]


# ── Consensus probability ─────────────────────────────────────────────────────

def weighted_consensus_prob(
    probs: Sequence[float],
    weights: Sequence[float] | None = None,
) -> float:
    """
    Compute a weighted consensus probability from multiple books.

    Parameters
    ----------
    probs:
        Sequence of fair (devigged) YES probabilities from each book.
    weights:
        Optional weights; if None, equal weighting is used.

    Returns
    -------
    Weighted mean probability in [0, 1].

    Examples
    --------
    >>> weighted_consensus_prob([0.52, 0.54, 0.53], [2.0, 1.0, 1.0])
    0.5275
    """
    probs = list(probs)
    if not probs:
        raise ValueError("probs must not be empty.")
    if weights is None:
        weights = [1.0] * len(probs)
    weights = list(weights)
    if len(weights) != len(probs):
        raise ValueError("probs and weights must have the same length.")

    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("Total weight must be positive.")

    return sum(p * w for p, w in zip(probs, weights)) / total_weight


def median_prob(probs: Sequence[float]) -> float:
    """
    Return the median of a sequence of probabilities.

    Uses the lower median for even-length sequences (conservative).

    Examples
    --------
    >>> median_prob([0.50, 0.52, 0.54])
    0.52
    """
    sorted_p = sorted(probs)
    n = len(sorted_p)
    if n == 0:
        raise ValueError("probs must not be empty.")
    mid = (n - 1) // 2
    return sorted_p[mid]


# ── Overround / vig inspection ────────────────────────────────────────────────

def overround(probs: Sequence[float]) -> float:
    """
    Return the bookmaker overround (sum of implied probabilities).

    A fair book has overround == 1.0; a typical sportsbook is 1.02–1.10.

    Examples
    --------
    >>> round(overround([0.5238, 0.5238]), 4)
    1.0476
    """
    return sum(probs)


def vig_pct(probs: Sequence[float]) -> float:
    """
    Return the vig as a percentage of the overround.

    vig_pct = (overround - 1) / overround * 100

    Examples
    --------
    >>> round(vig_pct([0.5238, 0.5238]), 2)
    4.55
    """
    o = overround(probs)
    if o <= 0:
        raise ValueError("Overround must be positive.")
    return (o - 1.0) / o * 100.0
