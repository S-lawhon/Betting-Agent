"""
src/mlb_win_prob.py
───────────────────
Live MLB win-probability model for P-016 (Live Maker).

Approach: normal approximation over the remaining run differential,
anchored to a pre-game home win probability (team-strength prior) and
adjusted for the current base/out state via a run-expectancy table.

This is deliberately a v0 "state layer": the paper pilot measures maker
fill quality (markouts), not model alpha — the market itself is roughly
as accurate as a good model (Kalshi NBA study, arXiv 2606.07811), so the
model's job is a continuously-updating fair value, not out-forecasting.

Calibration constants:
  RUNS_PER_HALF_INNING — league-average scoring rate per half inning
  (~0.48 for 2023-2025 run environment).
  SIGMA_PER_HALF_INNING — std dev of runs scored in one half inning.
  RE24 — standard run-expectancy matrix (base state × outs), league
  average, used for the in-progress half inning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RUNS_PER_HALF_INNING = 0.48
SIGMA_PER_HALF_INNING = 1.05
EXTRA_INNINGS_HOME_WP = 0.52   # historical home win rate in extras

# Run expectancy by (bases_bitmask, outs).  Bitmask: 1=1st, 2=2nd, 4=3rd.
# League-average RE24, modern run environment.
RE24 = {
    (0, 0): 0.51, (0, 1): 0.27, (0, 2): 0.10,
    (1, 0): 0.90, (1, 1): 0.53, (1, 2): 0.22,
    (2, 0): 1.13, (2, 1): 0.69, (2, 2): 0.32,
    (3, 0): 1.49, (3, 1): 0.92, (3, 2): 0.44,
    (4, 0): 1.38, (4, 1): 0.96, (4, 2): 0.36,
    (5, 0): 1.79, (5, 1): 1.16, (5, 2): 0.51,
    (6, 0): 2.01, (6, 1): 1.42, (6, 2): 0.61,
    (7, 0): 2.29, (7, 1): 1.60, (7, 2): 0.77,
}


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """Inverse normal CDF (Acklam-style rational approximation)."""
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    # Beasley-Springer-Moro
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


@dataclass
class MlbGameSnapshot:
    """Minimal live game state for win-probability evaluation."""
    home_score: int
    away_score: int
    inning: int              # 1-based current inning
    is_top: bool             # True = away team batting
    outs: int                # 0-2 within the half inning
    on_first: bool = False
    on_second: bool = False
    on_third: bool = False
    is_final: bool = False


@dataclass
class WinProbResult:
    home_wp: float           # P(home wins)
    sensitivity: float       # |d(home_wp) / d(run)| — leverage proxy
    remaining_half_innings_home: float
    remaining_half_innings_away: float


def strength_drift_per_half_inning(pregame_home_prob: float) -> float:
    """Convert a pre-game home win prob into an expected per-half-inning
    run-differential drift (home minus away).

    Inverts the same normal approximation used in-game: over a full game
    (9 innings each side) the run differential ~ N(mu9, sigma9^2) and
    P(home) ≈ Phi(mu9 / sigma9) (ties → extras ≈ coin flip, absorbed).
    """
    p = min(max(pregame_home_prob, 0.05), 0.95)
    sigma9 = SIGMA_PER_HALF_INNING * math.sqrt(18.0)
    mu9 = _phi_inv(p) * sigma9
    return mu9 / 18.0


def _base_state_mask(snap: MlbGameSnapshot) -> int:
    return (1 if snap.on_first else 0) | \
           (2 if snap.on_second else 0) | \
           (4 if snap.on_third else 0)


def _remaining_half_innings(snap: MlbGameSnapshot,
                            regulation: int = 9) -> tuple:
    """(away_remaining, home_remaining) full-half-inning counts AFTER the
    current half inning completes.  The in-progress half is handled
    separately via RE24."""
    inn = max(snap.inning, 1)
    if snap.is_top:
        away_rem = max(regulation - inn, 0)          # future top halves
        home_rem = max(regulation - inn + 1, 0)      # this inning's bottom + future
    else:
        away_rem = max(regulation - inn, 0)
        home_rem = max(regulation - inn, 0)
    return away_rem, home_rem


def implied_pregame_prob(snap: MlbGameSnapshot, target_prob: float,
                         tol: float = 1e-4, max_iter: int = 40) -> float:
    """Invert the model: find the pregame prior that reproduces
    `target_prob` given the CURRENT game state.

    Used when a game is first seen already in progress, so we have no
    true pregame anchor.  Naively passing the live mid as the pregame
    prior double-counts the current state (the live price already
    reflects the score, and the model would then apply it again) — that
    bug produced 8-17pp systematic divergence on 2026-07-20.

    home_win_probability is monotonic increasing in the prior, so a
    bisection is safe.
    """
    lo, hi = 0.05, 0.95
    target = min(max(target_prob, 0.02), 0.98)
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        val = home_win_probability(snap, mid).home_wp
        if abs(val - target) < tol:
            return mid
        if val < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def home_win_probability(snap: MlbGameSnapshot,
                         pregame_home_prob: float = 0.54) -> WinProbResult:
    """Estimate P(home win) from the live game state.

    Normal approximation on the final run differential:
      diff_final = current_diff + RE(current half, batting team)
                   + drift*(home_rem - away_rem) + noise
      noise ~ N(0, SIGMA^2 * total_remaining_halves)
    Ties resolved by extra innings at EXTRA_INNINGS_HOME_WP, shaded by
    team strength.
    """
    if snap.is_final:
        wp = 1.0 if snap.home_score > snap.away_score else 0.0
        return WinProbResult(wp, 0.0, 0.0, 0.0)

    def wp_for_diff(current_diff: float) -> float:
        away_rem, home_rem = _remaining_half_innings(snap)

        # Walk-off boundary: bottom 9th (or later), home already ahead →
        # game is effectively over.
        if (not snap.is_top and snap.inning >= 9 and current_diff > 0):
            return 1.0
        # Top 9th or later with away batting: home_rem may be 0 if home leads.

        drift = strength_drift_per_half_inning(pregame_home_prob)

        # In-progress half inning expectation from base/out state.
        re_key = (_base_state_mask(snap), min(max(snap.outs, 0), 2))
        re_now = RE24.get(re_key, RUNS_PER_HALF_INNING)
        # Expected remaining runs:
        #   away = re_now (if top) + away_rem * base
        #   home = home_rem * base (+ re_now if bottom)
        base = RUNS_PER_HALF_INNING
        if snap.is_top:
            exp_away = re_now + away_rem * base
            exp_home = home_rem * base
            halves_var = 1 + away_rem + home_rem
        else:
            # Bottom 9+ with home behind/tied: home bats now.
            exp_away = away_rem * base
            exp_home = re_now + home_rem * base
            halves_var = 1 + away_rem + home_rem

        # Strength drift: mu9 accrues over 18 halves; scale by the
        # fraction of the game remaining (including the in-progress half).
        home_halves = home_rem + (0 if snap.is_top else 1)
        away_halves = away_rem + (1 if snap.is_top else 0)
        mu = current_diff + (exp_home - exp_away) + \
            drift * (home_halves + away_halves)

        sigma = SIGMA_PER_HALF_INNING * math.sqrt(max(halves_var, 0.25))

        # P(diff_final >= 1), P(tie) with continuity correction
        p_win_outright = 1.0 - _phi((0.5 - mu) / sigma)
        p_tie = _phi((0.5 - mu) / sigma) - _phi((-0.5 - mu) / sigma)

        extras_wp = EXTRA_INNINGS_HOME_WP + \
            0.3 * (pregame_home_prob - 0.5)
        extras_wp = min(max(extras_wp, 0.35), 0.65)

        return min(max(p_win_outright + p_tie * extras_wp, 0.001), 0.999)

    diff = float(snap.home_score - snap.away_score)
    wp = wp_for_diff(diff)
    # Leverage: symmetric one-run finite difference
    wp_up = wp_for_diff(diff + 1.0)
    wp_dn = wp_for_diff(diff - 1.0)
    sensitivity = abs(wp_up - wp_dn) / 2.0

    away_rem, home_rem = _remaining_half_innings(snap)
    return WinProbResult(
        home_wp=wp,
        sensitivity=sensitivity,
        remaining_half_innings_home=home_rem,
        remaining_half_innings_away=away_rem,
    )
