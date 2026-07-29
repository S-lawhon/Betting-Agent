"""
tests/test_combo_copula.py
──────────────────────────
Tests for src/combo_copula.py — the correlation-aware combo joint pricer.

The properties under test are the ones that decide whether Gate 0 measures the
right thing:

  * **Independence is recovered exactly** at ρ=0. If the copula does not
    reproduce the product when uncorrelated, every number downstream is wrong.
  * **Correlation raises the joint, monotonically.** Positive ρ on positively-
    specified legs must push the joint ABOVE the product — that is the whole
    reason the module exists, and the sign being backwards would make the Gate 0
    bias worse rather than better.
  * **It agrees with an exact bivariate calculation.** Monte Carlo is validated
    against scipy's closed-form multivariate normal CDF, not merely asserted to
    be plausible.
  * **It is deterministic.** Common random numbers mean the same combo prices
    identically every run; a wandering price would make the gate unreproducible.
  * **Leg relationships are classified correctly from tickers**, since the
    correlation block is chosen entirely from ticker shape.
  * **It never raises on the quote path** — a non-PSD matrix or a degenerate
    marginal must degrade, not crash.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

np = pytest.importorskip("numpy")

from src.combo_copula import (DEFAULT_RHO, ComboCopula, correlation_matrix,
                              default_copula, event_key, player_key, relation)

# Real tickers from the 2026-07 live census.
MLB_A = "KXMLBGAME-26JUL281940KCMIN-KC"
MLB_A2 = "KXMLBTOTAL-26JUL281940KCMIN-9"
MLB_B = "KXMLBGAME-26JUL282140BOSATH-BOS"
NBA_P1 = "KXNBAPTS-26JUN13NYKSAS-SASJCHAMPAGNIE30-10"
NBA_P2 = "KXNBA3PT-26JUN13NYKSAS-SASJCHAMPAGNIE30-3"
NBA_T = "KXNBATOTAL-26JUN13NYKSAS-217"


# ── ticker parsing ────────────────────────────────────────────────────
def test_event_key_extracts_the_game():
    assert event_key(MLB_A) == "26JUL281940KCMIN"
    assert event_key(NBA_P1) == "26JUN13NYKSAS"
    assert event_key("MALFORMED") is None
    assert event_key("") is None


def test_player_key_extracts_players_and_ignores_non_props():
    assert player_key(NBA_P1) == "SASJCHAMPAGNIE"
    assert player_key(NBA_P2) == "SASJCHAMPAGNIE"
    assert player_key(NBA_T) is None          # "217" is a strike, not a player
    assert player_key(MLB_A) is None


@pytest.mark.parametrize("a,b,expected", [
    (NBA_P1, NBA_P2, "same_player"),   # points + threes, one player
    (NBA_P1, NBA_T, "same_game"),      # player prop + game total
    (MLB_A, MLB_A2, "same_game"),
    (MLB_A, MLB_B, "same_day"),        # different games, same slate
    (MLB_A, NBA_P1, "cross"),          # different sports, different dates
])
def test_relation_classification(a, b, expected):
    assert relation(a, b) == expected


def test_identical_legs_are_maximally_related():
    assert relation(MLB_A, MLB_A) == "same_player"


# ── correlation matrix ────────────────────────────────────────────────
def test_matrix_is_symmetric_unit_diagonal_and_psd():
    m = correlation_matrix([NBA_P1, NBA_P2, NBA_T, MLB_A])
    assert np.allclose(m, m.T)
    assert np.allclose(np.diag(m), 1.0)
    assert np.linalg.eigvalsh(m).min() >= -1e-9


def test_matrix_uses_the_right_blocks():
    m = correlation_matrix([NBA_P1, NBA_P2, NBA_T])
    assert m[0, 1] == pytest.approx(DEFAULT_RHO["same_player"])
    assert m[0, 2] == pytest.approx(DEFAULT_RHO["same_game"])


def test_extreme_rho_is_repaired_to_psd_rather_than_failing():
    """Three legs at ρ=0.99 pairwise is near-singular; Cholesky must still work."""
    m = correlation_matrix([NBA_P1, NBA_P2, NBA_T],
                           rho={"same_player": 0.99, "same_game": 0.99})
    np.linalg.cholesky(m)          # raises if the repair failed


# ── the joint ─────────────────────────────────────────────────────────
def test_independence_recovers_the_product():
    c = ComboCopula(draws=200_000)
    p = [0.5, 0.4, 0.3]
    assert c.joint_yes(p) == pytest.approx(0.06, abs=0.004)


def test_correlation_raises_the_joint_above_the_product():
    """The sign that matters: positive ρ ⇒ joint > product."""
    c = ComboCopula(draws=200_000)
    p = [0.56, 0.48, 0.52]
    indep = c.joint_yes(p)
    corr = c.joint_yes(p, np.full((3, 3), 0.35) + np.eye(3) * 0.65)
    assert corr > indep
    assert corr - indep > 0.02          # materially, not marginally


def test_joint_is_monotone_in_correlation():
    c = ComboCopula(draws=200_000)
    p = [0.5, 0.5, 0.5]
    out = []
    for r in (0.0, 0.2, 0.4, 0.6):
        m = np.full((3, 3), r) + np.eye(3) * (1 - r)
        out.append(c.joint_yes(p, m))
    assert out == sorted(out)


def test_matches_scipy_exact_bivariate():
    """Monte Carlo validated against a closed form, not just asserted."""
    st = pytest.importorskip("scipy.stats")
    from statistics import NormalDist
    c = ComboCopula(draws=400_000)
    for p1, p2, r in [(0.5, 0.5, 0.3), (0.2, 0.7, 0.5), (0.35, 0.35, 0.15)]:
        z = [NormalDist().inv_cdf(p1), NormalDist().inv_cdf(p2)]
        cov = [[1.0, r], [r, 1.0]]
        exact = st.multivariate_normal(mean=[0, 0], cov=cov).cdf(z)
        got = c.joint_yes([p1, p2], np.array(cov))
        assert got == pytest.approx(exact, abs=0.004)


def test_perfect_correlation_approaches_the_minimum_marginal():
    c = ComboCopula(draws=200_000)
    m = np.full((3, 3), 0.999) + np.eye(3) * 0.001
    assert c.joint_yes([0.3, 0.5, 0.7], m) == pytest.approx(0.3, abs=0.02)


def test_joint_never_exceeds_the_smallest_marginal():
    c = ComboCopula(draws=100_000)
    p = [0.25, 0.6, 0.8]
    for r in (0.0, 0.3, 0.7):
        m = np.full((3, 3), r) + np.eye(3) * (1 - r)
        assert c.joint_yes(p, m) <= min(p) + 1e-9


# ── determinism ───────────────────────────────────────────────────────
def test_pricing_is_deterministic_across_instances():
    a = ComboCopula().joint_yes([0.3, 0.4], np.array([[1, 0.2], [0.2, 1]]))
    b = ComboCopula().joint_yes([0.3, 0.4], np.array([[1, 0.2], [0.2, 1]]))
    assert a == b


def test_repeated_calls_return_identical_values():
    c = default_copula()
    v = [c.joint_yes([0.2, 0.3, 0.4]) for _ in range(3)]
    assert len(set(v)) == 1


# ── the combo path ────────────────────────────────────────────────────
def test_price_combo_reports_a_positive_correlation_premium():
    c = default_copula()
    out = c.price_combo([(NBA_P1, "yes"), (NBA_P2, "yes"), (NBA_T, "yes")],
                        [0.55, 0.45, 0.50])
    assert out["copula"] > out["independence"]
    assert out["correlation_premium"] > 0
    assert out["n_legs"] == 3
    assert out["max_pair_rho"] == pytest.approx(DEFAULT_RHO["same_player"])


def test_cross_game_combo_has_almost_no_premium():
    """Unrelated legs must price near independence — otherwise the model would
    inflate the baseline everywhere and hide real edge."""
    c = default_copula()
    out = c.price_combo([(MLB_A, "yes"), ("KXNBAGAME-26JUN13NYKSAS-NYK", "yes")],
                        [0.5, 0.5])
    assert abs(out["correlation_premium"]) < 0.01


def test_no_side_legs_use_one_minus_p():
    c = default_copula()
    yes = c.price_combo([(MLB_A, "yes")], [0.30])["independence"]
    no = c.price_combo([(MLB_A, "no")], [0.30])["independence"]
    assert yes == pytest.approx(0.30)
    assert no == pytest.approx(0.70)


def test_price_combo_validates_length():
    with pytest.raises(ValueError, match="differ in length"):
        default_copula().price_combo([(MLB_A, "yes")], [0.3, 0.4])


# ── degenerate inputs must not crash the quote path ───────────────────
def test_zero_marginal_collapses_to_zero():
    assert default_copula().joint_yes([0.5, 0.0]) == 0.0


def test_certain_legs_do_not_produce_infinities():
    v = default_copula().joint_yes([1.0, 0.4])
    assert 0.0 <= v <= 1.0 and v == pytest.approx(0.4, abs=0.01)


def test_empty_legs_raises_clearly():
    with pytest.raises(ValueError, match="no legs"):
        default_copula().joint_yes([])


def test_more_legs_than_the_predrawn_sample_still_prices():
    c = ComboCopula(draws=20_000, max_legs=3)
    v = c.joint_yes([0.6] * 5)
    assert v == pytest.approx(0.6 ** 5, abs=0.02)


# ── load_rho against the COMMITTED fitted file ───────────────────────
# combo_research/fitted_rho.json is the 2026-07-29 fit off the 8-day VPS
# archive (103,019 combos, all legs settled). These tests pin the contract the
# Gate 0 pricer depends on: the file loads, identified blocks override their
# priors, and an UNIDENTIFIED block — same_player ships with a computed rho
# but identified:false (431 pairs on only 2 event days) — retains the prior
# rather than smuggling in a confounded number.

FITTED_PATH = str(Path(__file__).resolve().parent.parent
                  / "combo_research" / "fitted_rho.json")


def test_fitted_rho_file_loads():
    from src.combo_copula import load_rho
    rho = load_rho(FITTED_PATH, strict=True)
    # identified blocks override the priors
    assert rho["cross"] != DEFAULT_RHO["cross"]
    assert rho["same_day"] != DEFAULT_RHO["same_day"]
    assert 0.0 < rho["cross"] < 1.0
    assert 0.0 < rho["same_day"] < 1.0
    assert 0.0 < rho["same_game"] < 1.0


def test_unidentified_block_retains_prior():
    """same_player carries rho=0.3231 with identified:false — the fitter
    computed it but refused to stand behind it. load_rho must not use it."""
    import json
    from src.combo_copula import load_rho
    rec = json.load(open(FITTED_PATH))["blocks"]["same_player"]
    assert rec["identified"] is False and rec["rho"] is not None, \
        "fixture drifted: this test wants a computed-but-unidentified block"
    rho = load_rho(FITTED_PATH, strict=True)
    assert rho["same_player"] == DEFAULT_RHO["same_player"]


def test_unfitted_null_block_retains_prior(tmp_path):
    p = tmp_path / "rho.json"
    p.write_text('{"blocks": {"same_game": {"rho": null, "identified": false},'
                 ' "cross": {"rho": 0.10, "identified": true, "n_days": 9}}}')
    from src.combo_copula import load_rho
    rho = load_rho(str(p))
    assert rho["same_game"] == DEFAULT_RHO["same_game"]
    assert rho["cross"] == 0.10


def test_missing_file_falls_back_to_priors(tmp_path):
    from src.combo_copula import load_rho
    assert load_rho(str(tmp_path / "nope.json")) == DEFAULT_RHO
