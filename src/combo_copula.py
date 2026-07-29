"""
src/combo_copula.py
───────────────────
Correlation-aware joint pricing for Kalshi combos (KXMVE multi-leg parlays).

Why this exists
───────────────
A combo pays only if every leg resolves YES, so its fair value is the JOINT
probability — not the product of the legs. The product assumes independence, and
independence is wrong in the direction that matters: retail builds *narrative*
combos (team wins AND its quarterback throws over), whose legs are positively
correlated, so the true joint sits ABOVE the product.

The size of the error is not marginal. On a worked 3-leg case (56% / 48% / 52%)
independence gives 14.0% against a Gaussian-copula joint of 18.9% — a **35%
relative understatement**, which is larger than the entire measured combo edge.
A quoter pricing independence is therefore not shaving margin; it is trading at
negative expectancy against anyone holding a copula.

Two consumers:

1. **Gate 0** (`combo_research/shadow_public.py`). The gate is specified as
   "median margin ≥ +2.0¢ against a *correlation-adjusted* price". Measuring it
   against the independence product instead is biased UPWARD — part of that
   margin is real correlation, not profit. This module supplies the correct
   baseline.
2. **Gate 3 / P-029 pricing.** The same joint, used to quote.

Method
──────
Gaussian copula. Each leg's marginal ``p_i`` becomes a latent threshold
``z_i = Φ⁻¹(p_i)``; the legs are given a correlation matrix; the joint is the
orthant probability ``P(Z_1 < z_1, …, Z_n < z_n)`` under that correlation.

The orthant integral has no elementary closed form above n=2, so it is evaluated
by Monte Carlo with **common random numbers** — one standard-normal sample drawn
at construction and reused for every combo. That buys three things at once:
determinism (the same combo always prices the same), speed (one matrix product
per combo rather than a fresh sample), and *monotone* comparisons between
similar combos, which matters more than absolute precision when the output feeds
a margin distribution.

Dependencies: numpy only. ``Φ⁻¹`` comes from the standard library
(``statistics.NormalDist``), so the droplet does not need scipy.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_NORM = NormalDist()

# Default correlation block structure. These are PRIORS, deliberately
# conservative (wider than measured, so the joint is pushed up and our quote is
# pushed up, which is the safe direction for a seller). Replace with fitted
# values from `combo_research/fit_correlation.py` once a sample exists.
DEFAULT_RHO = {
    "same_player": 0.35,   # two props on one player in one game
    "same_game": 0.18,     # different players / markets, same game
    "same_day": 0.02,      # different games, same slate — weather, refs, pace
    "cross": 0.0,          # unrelated
}

# Ticker shapes seen in the 2026-07 census:
#   KXMLBGAME-26JUL281940KCMIN-KC
#   KXNBAPTS-26JUN13NYKSAS-SASJCHAMPAGNIE30-10
#   KXMLBTOTAL-26JUL281840AZPIT-10
# Segment 1 is the event key (date + matchup); a player, when present, is the
# leading alphabetic run of segment 2.
_PLAYER_RE = re.compile(r"^([A-Z]{2,}[A-Z]+?)(\d+)$")


def event_key(ticker: str) -> Optional[str]:
    """The game/event a leg belongs to, or None if the ticker is unparseable.

    Two legs share a game iff they share this key. Derived from the ticker's
    second segment — the only handle Kalshi gives, since `mve_selected_legs`
    carries no event grouping.
    """
    parts = (ticker or "").split("-")
    return parts[1] if len(parts) >= 2 and parts[1] else None


def player_key(ticker: str) -> Optional[str]:
    """The player a prop leg refers to, or None for non-player markets.

    ``KXNBAPTS-26JUN13NYKSAS-SASJCHAMPAGNIE30-10`` → ``SASJCHAMPAGNIE``.
    Same-player legs are the most correlated pairs on the board — points and
    three-pointers for one player move together almost mechanically — and the
    live census showed Kalshi accepts exactly that combination.
    """
    parts = (ticker or "").split("-")
    if len(parts) < 3:
        return None
    m = _PLAYER_RE.match(parts[2])
    return m.group(1) if m else None


def relation(a: str, b: str) -> str:
    """Classify a leg pair into a correlation block."""
    if a == b:
        return "same_player"          # identical leg: perfectly dependent
    pa, pb = player_key(a), player_key(b)
    if pa and pb and pa == pb:
        return "same_player"
    ea, eb = event_key(a), event_key(b)
    if ea and eb and ea == eb:
        return "same_game"
    # Same calendar date embedded in the event key (first 7 chars: 26JUL28)
    if ea and eb and ea[:7] == eb[:7]:
        return "same_day"
    return "cross"


def correlation_matrix(tickers: Sequence[str],
                       rho: Optional[Dict[str, float]] = None) -> np.ndarray:
    """Build the leg correlation matrix from the block structure.

    Args:
        tickers: Leg market tickers, in order.
        rho: Block → correlation. Defaults to :data:`DEFAULT_RHO`.

    Returns:
        An ``n × n`` positive-definite correlation matrix.
    """
    rho = {**DEFAULT_RHO, **(rho or {})}
    n = len(tickers)
    m = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            r = float(rho.get(relation(tickers[i], tickers[j]), 0.0))
            m[i, j] = m[j, i] = r
    return _nearest_psd(m)


def _nearest_psd(m: np.ndarray) -> np.ndarray:
    """Project onto the nearest positive-semidefinite correlation matrix.

    A block structure assembled pairwise is not guaranteed PSD — three legs at
    ρ=0.35 pairwise are fine, but mixed blocks can produce a matrix with a
    negative eigenvalue, and Cholesky would then fail at quote time. Clipping
    the spectrum and renormalising the diagonal keeps it a valid correlation
    matrix. Cheap insurance on a path that must not raise.
    """
    vals, vecs = np.linalg.eigh(m)
    if vals.min() > 1e-10:
        return m
    vals = np.clip(vals, 1e-10, None)
    out = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(out))
    out = out / np.outer(d, d)
    np.fill_diagonal(out, 1.0)
    return out


@dataclass
class ComboCopula:
    """Gaussian-copula joint pricer with common random numbers.

    Args:
        draws: Monte Carlo sample size. 50k gives a standard error near 0.15¢
            at p≈0.2 — well inside the precision Gate 0 needs, and the common
            random numbers mean errors are shared across combos rather than
            adding noise to comparisons between them.
        max_legs: Width of the pre-drawn sample. Combos above this fall back to
            a fresh draw (rare: the target zone is 2–4 legs).
        seed: Fixed, so a combo prices identically on every run and on every
            machine.
    """

    draws: int = 50_000
    max_legs: int = 12
    seed: int = 20260729
    rho: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RHO))
    _z: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        half = self.draws // 2
        base = rng.standard_normal((half, self.max_legs))
        # Antithetic variates: pairing each draw with its negation halves the
        # variance of a symmetric integrand at zero extra cost.
        self._z = np.vstack([base, -base])

    # -- core ----------------------------------------------------------
    def joint_yes(self, marginals: Sequence[float],
                  corr: Optional[np.ndarray] = None) -> float:
        """P(every leg resolves YES) under the copula.

        Args:
            marginals: Per-leg P(YES), each in (0, 1).
            corr: Correlation matrix; identity (independence) if omitted.

        Returns:
            Joint probability in [0, 1].
        """
        p = np.asarray(marginals, dtype=float)
        if p.size == 0:
            raise ValueError("no legs")
        if np.any((p <= 0.0) | (p >= 1.0)):
            # A leg at exactly 0 or 1 collapses the joint; handle explicitly
            # rather than letting Φ⁻¹ return ±inf.
            if np.any(p <= 0.0):
                return 0.0
            p = np.clip(p, 1e-9, 1 - 1e-9)
        n = p.size
        if corr is None:
            corr = np.eye(n)
        thresh = np.array([_NORM.inv_cdf(float(x)) for x in p])

        z = self._z[:, :n] if n <= self.max_legs else \
            np.random.default_rng(self.seed).standard_normal((self.draws, n))
        try:
            chol = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            chol = np.linalg.cholesky(_nearest_psd(np.asarray(corr, float)))
        draws = z @ chol.T
        return float(np.all(draws < thresh, axis=1).mean())

    def price_combo(self, legs: Sequence[Tuple[str, str]],
                    leg_yes_probs: Sequence[float]) -> Dict[str, float]:
        """Price one combo from its legs and their YES marginals.

        Args:
            legs: ``[(market_ticker, side), …]`` — side ``"yes"`` or ``"no"``.
            leg_yes_probs: P(YES) for each leg, in the same order. The side is
                applied here, so pass the raw market probability.

        Returns:
            ``{"independence", "copula", "correlation_premium", "n_legs",
            "max_pair_rho"}`` — all probabilities, premium in probability units.
        """
        if len(legs) != len(leg_yes_probs):
            raise ValueError("legs and marginals differ in length")
        tickers = [t for t, _ in legs]
        # Apply each leg's side: a NO leg contributes (1 - p).
        eff = [
            (1.0 - float(q)) if (side or "yes").lower() == "no" else float(q)
            for (_, side), q in zip(legs, leg_yes_probs)
        ]
        corr = correlation_matrix(tickers, self.rho)
        indep = float(np.prod(eff))
        joint = self.joint_yes(eff, corr)
        off = corr[~np.eye(len(tickers), dtype=bool)]
        return {
            "independence": indep,
            "copula": joint,
            "correlation_premium": joint - indep,
            "n_legs": float(len(tickers)),
            "max_pair_rho": float(off.max()) if off.size else 0.0,
        }


def load_rho(path: str, strict: bool = False) -> Dict[str, float]:
    """Load fitted correlations, falling back to the prior per block.

    A block that the fitter could not identify — too few pairs, or a joint
    outside the Frechet bounds — keeps its DEFAULT_RHO prior rather than
    silently becoming zero. Zero would understate the joint, which is the exact
    bias this module exists to remove.

    Args:
        path: JSON written by combo_research/fit_correlation.py.
        strict: Raise if the file is unreadable instead of returning priors.
    """
    out = dict(DEFAULT_RHO)
    try:
        blocks = (json.loads(Path(path).read_text()) or {}).get("blocks") or {}
    except Exception as exc:
        if strict:
            raise
        logger.warning("could not read fitted rho from %s (%s); using priors",
                       path, exc)
        return out
    for block, rec in blocks.items():
        rec = rec or {}
        rho = rec.get("rho")
        # `identified: false` means the fitter computed a number but refused to
        # stand behind it (fewer than 3 event days -> rho is confounded with
        # the slate's outcome rate). An unidentified rho must not be used any
        # more than a missing one — same_player ships exactly this shape.
        if rho is None or not rec.get("identified", True):
            logger.info("block %r unfitted/unidentified; keeping prior %.3f",
                        block, out.get(block, 0.0))
            continue
        out[block] = float(rho)
    return out


# Module-level default, so callers share one pre-drawn sample.
_DEFAULT: Optional[ComboCopula] = None


def default_copula() -> ComboCopula:
    """Shared pricer. Building the sample costs ~10 ms; do it once."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ComboCopula()
    return _DEFAULT
