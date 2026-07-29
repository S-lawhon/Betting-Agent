"""
tests/test_fit_correlation.py
─────────────────────────────
Tests for combo_research/fit_correlation.py — the tetrachoric ρ estimator that
replaces the copula's priors with a measurement.

What is worth testing here is the *estimator*, not the API plumbing:

  * **It inverts the bivariate normal correctly.** Given marginals and a joint
    generated at a known ρ, it must recover that ρ. This is the whole method.
  * **Independence returns ρ ≈ 0**, and the sign is right in both directions.
  * **Unidentifiable inputs return None, not 0.** A joint outside the Fréchet
    bounds means "cannot be determined"; returning zero would silently claim
    independence and reinstate exactly the bias the copula exists to remove.
  * **A single-slate fit is marked unidentified.** Every leg pair from one day
    shares that day's outcome rate, so ρ is confounded. The estimator must
    refuse to present it as a result — the live 2026-07-29 sample hit this.
  * **Partially-settled combos are excluded.** A settled combo can have unsettled
    legs, because it resolves NO the moment one leg fails. Keeping only resolved
    legs would select the legs that decided the outcome.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "combo_research"))

np = pytest.importorskip("numpy")

from fit_correlation import _bvn_cdf, day_of, fit, tetrachoric  # noqa: E402


# ── the estimator inverts Φ₂ ──────────────────────────────────────────
@pytest.mark.parametrize("p1,p2,rho", [
    (0.50, 0.50, 0.00),
    (0.50, 0.50, 0.30),
    (0.40, 0.60, 0.50),
    (0.30, 0.30, 0.20),
    (0.70, 0.45, -0.25),
])
def test_recovers_a_known_correlation(p1, p2, rho):
    """Generate a joint at a known ρ, then recover it."""
    from statistics import NormalDist
    z1, z2 = NormalDist().inv_cdf(p1), NormalDist().inv_cdf(p2)
    p11 = _bvn_cdf(z1, z2, rho)
    assert tetrachoric(p1, p2, p11) == pytest.approx(rho, abs=0.02)


def test_independence_gives_zero():
    assert tetrachoric(0.5, 0.4, 0.5 * 0.4) == pytest.approx(0.0, abs=0.02)


def test_positive_dependence_gives_positive_rho():
    assert tetrachoric(0.5, 0.5, 0.35) > 0.2       # joint above 0.25


def test_negative_dependence_gives_negative_rho():
    assert tetrachoric(0.5, 0.5, 0.15) < -0.2      # joint below 0.25


def test_joint_outside_frechet_bounds_is_unidentifiable_not_zero():
    """Returning 0 here would silently assert independence."""
    assert tetrachoric(0.3, 0.3, 0.35) is None     # above min(p1,p2)
    assert tetrachoric(0.8, 0.8, 0.50) is None     # below p1+p2-1


def test_degenerate_marginals_do_not_raise():
    for args in ((0.0, 0.5, 0.0), (1.0, 0.5, 0.5), (0.5, 1.0, 0.5)):
        tetrachoric(*args)          # must not raise


# ── day parsing drives the clustering ─────────────────────────────────
def test_day_of_extracts_the_slate():
    assert day_of("KXMLBGAME-26JUL281940KCMIN-KC") == "26JUL28"
    assert day_of("KXNBAPTS-26JUN13NYKSAS-SASJCHAMPAGNIE30-10") == "26JUN13"
    assert day_of("GARBAGE") == "unknown"


# ── the fit refuses to over-claim ─────────────────────────────────────
def _one_day_pairs(n):
    """n leg pairs, all on one slate."""
    return {("KXMLBGAME-26JUL28G%03d-A" % i, "KXMLBGAME-26JUL28G%03d-B" % i)
            for i in range(n)}


def _multi_day_pairs(days, per_day):
    out = set()
    for d in range(days):
        for i in range(per_day):
            out.add(("KXMLBGAME-26JUL%02dG%03d-A" % (10 + d, i),
                     "KXMLBGAME-26JUL%02dG%03d-B" % (10 + d, i)))
    return out


def test_single_slate_is_flagged_unidentified():
    """The live 2026-07-29 sample was one slate; ρ was confounded with that
    day's outcome rate and must not be presented as a result."""
    pairs = _one_day_pairs(60)
    outcomes = {}
    for i, (a, b) in enumerate(sorted(pairs)):
        outcomes[a] = i % 2
        outcomes[b] = i % 2
    out = fit({"same_day": pairs}, outcomes)["same_day"]
    assert out["n_days"] == 1
    assert out["identified"] is False
    assert "SINGLE SLATE" in (out["note"] or "")
    assert out["ci95"] is None


def test_multi_day_sample_is_identified_and_gets_a_ci():
    pairs = _multi_day_pairs(days=6, per_day=20)
    outcomes = {}
    for i, (a, b) in enumerate(sorted(pairs)):
        outcomes[a] = i % 2
        outcomes[b] = i % 2
    out = fit({"same_day": pairs}, outcomes)["same_day"]
    assert out["n_days"] == 6
    assert out["identified"] is True
    assert out["ci95"] is not None and len(out["ci95"]) == 2


def test_block_under_the_pair_floor_is_not_fitted():
    pairs = _one_day_pairs(5)
    outcomes = {t: 1 for p in pairs for t in p}
    out = fit({"same_player": pairs}, outcomes)["same_player"]
    assert out["rho"] is None
    assert "floor" in out["note"]


def test_pairs_with_unresolved_legs_are_excluded():
    pairs = _multi_day_pairs(days=4, per_day=15)
    outcomes = {}
    for i, (a, b) in enumerate(sorted(pairs)):
        outcomes[a] = i % 2
        if i % 3:                       # leave a third of the b-legs unresolved
            outcomes[b] = i % 2
    out = fit({"same_day": pairs}, outcomes)["same_day"]
    assert out["n_pairs"] < len(pairs)


def test_perfectly_correlated_outcomes_recover_high_rho():
    pairs = _multi_day_pairs(days=5, per_day=20)
    outcomes = {}
    for i, (a, b) in enumerate(sorted(pairs)):
        v = i % 2
        outcomes[a] = outcomes[b] = v   # identical outcomes
    out = fit({"same_day": pairs}, outcomes)["same_day"]
    assert out["rho"] is not None and out["rho"] > 0.9
