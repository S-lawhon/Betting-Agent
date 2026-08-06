"""Tests for the correlation side-sign fix in src/combo_copula.py.

The bug (pre-2026-08-05): correlation_matrix was built from tickers only, so a
YES/NO pair of positively correlated legs was priced with +rho instead of -rho,
inflating every mixed-side joint. See
combo_research/recalib/REPORT_P029_Recalibration_2026-08-05.md.
"""
import numpy as np
import pytest

from src.combo_copula import ComboCopula, correlation_matrix

# Two legs in the same game -> positive block rho under any rho table.
GAME_A = "KXMLBGAME-26JUL281940KCMIN-KC"
GAME_B = "KXMLBTOTAL-26JUL281940KCMIN-10"
RHO = {"same_game": 0.30, "same_day": 0.10, "same_player": 0.35, "cross": 0.0}


def test_no_side_flips_matrix_sign():
    unsigned = correlation_matrix([GAME_A, GAME_B], RHO)
    signed = correlation_matrix([GAME_A, GAME_B], RHO, sides=["yes", "no"])
    assert unsigned[0, 1] == pytest.approx(0.30)
    assert signed[0, 1] == pytest.approx(-0.30)


def test_two_no_legs_cancel_back_to_positive():
    signed = correlation_matrix([GAME_A, GAME_B], RHO, sides=["no", "no"])
    assert signed[0, 1] == pytest.approx(0.30)


def test_sides_none_reproduces_unsigned_matrix():
    # Historical reproduction path: omitting sides must keep the old matrix.
    a = correlation_matrix([GAME_A, GAME_B], RHO)
    b = correlation_matrix([GAME_A, GAME_B], RHO, sides=["yes", "yes"])
    assert np.allclose(a, b)


def test_sides_length_mismatch_raises():
    with pytest.raises(ValueError):
        correlation_matrix([GAME_A, GAME_B], RHO, sides=["yes"])


def test_mixed_side_joint_prices_below_independence():
    """P(A yes AND B no) with A,B positively correlated < p_a * (1 - p_b).

    This is the money assertion: the old pricer put mixed-side joints ABOVE
    independence, the exact wrong side.
    """
    cop = ComboCopula(rho=RHO)
    out = cop.price_combo([(GAME_A, "yes"), (GAME_B, "no")], [0.55, 0.45])
    assert out["copula"] < out["independence"]


def test_all_yes_joint_still_above_independence():
    cop = ComboCopula(rho=RHO)
    out = cop.price_combo([(GAME_A, "yes"), (GAME_B, "yes")], [0.55, 0.45])
    assert out["copula"] > out["independence"]


def test_yes_no_pair_symmetry():
    """P(A yes, B no) + P(A yes, B yes) must equal P(A yes) = p_a.

    Under the signed matrix the two joints partition A's YES mass; the unsigned
    matrix violates this identity on correlated pairs — the structural signature
    of the bug.
    """
    cop = ComboCopula(rho=RHO)
    p_a, p_b = 0.55, 0.45
    yes_no = cop.price_combo([(GAME_A, "yes"), (GAME_B, "no")], [p_a, p_b])
    yes_yes = cop.price_combo([(GAME_A, "yes"), (GAME_B, "yes")], [p_a, p_b])
    assert yes_no["copula"] + yes_yes["copula"] == pytest.approx(p_a, abs=0.01)


def test_signed_matrix_stays_psd_three_legs():
    m = correlation_matrix(
        [GAME_A, GAME_B, "KXMLBGAME-26JUL281840AZPIT-AZ"],
        RHO, sides=["yes", "no", "no"])
    assert np.linalg.eigvalsh(m).min() > -1e-9
