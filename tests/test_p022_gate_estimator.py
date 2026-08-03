"""
tests/test_p022_gate_estimator.py
─────────────────────────────────
Pins the P-022 gate statistic in `golf_quirks_research/quirks_common.py`.

`P022_DECISION_RULE.md` §2/§3 define it as:

    x_t  = contract-weighted mean pnl_per_contract WITHIN tournament t
    edge = mean(x_t)                    # EQUAL weight per tournament
    se   = stdev(x_t) / sqrt(T)

Until 2026-08-02 the harness resampled tournaments correctly for the CI but
returned `sum(p*w)/total_w` — POOLED across every filled contract — as the
point estimate, so a large-field tournament dominated a figure the rule says
must count it once. The two agree until field sizes are imbalanced, which is
exactly when it matters: published 364-market sample +3.41 pooled vs +3.80
rule (both z>3), widened 404-market sample +2.57 pooled vs **+1.45 rule,
z=0.65 — not significant**. A pooled +2.57 reached Amendment 1's power
calculation as though it were the rule's effect size.

These tests are synthetic — no data cache, no network — so the estimator is
pinned even where the tick cache is unavailable.
`golf_quirks_research/repro_estimator_check.py` is the on-data counterpart.
"""
import math
import statistics
import sys
from pathlib import Path

import pytest

_QR = Path(__file__).resolve().parents[1] / "golf_quirks_research"
sys.path.insert(0, str(_QR))

qc = pytest.importorskip("quirks_common")


# A big tournament that won and a small one that lost. Pooled is dragged to
# the big one; the rule counts them equally. Chosen so the two estimators
# disagree in SIGN, the failure mode that flips a gate.
IMBALANCED = [
    ("BIG", 0.10, 900.0),
    ("SMALL", -0.06, 40.0),
]


# ── the gate statistic ───────────────────────────────────────────────────

def test_gate_weights_each_tournament_equally():
    mean, _, _ = qc.bootstrap_gate(IMBALANCED)
    assert mean == pytest.approx((0.10 + -0.06) / 2)


def test_pooled_is_dominated_by_the_large_tournament():
    """The old behaviour, kept only for reproducing published figures."""
    mean, _, _ = qc.bootstrap_pooled(IMBALANCED)
    expected = (0.10 * 900 + -0.06 * 40) / 940
    assert mean == pytest.approx(expected)
    # And it is materially different from the gate's answer.
    assert abs(mean - qc.bootstrap_gate(IMBALANCED)[0]) > 0.05


def test_gate_and_pooled_disagree_in_sign_when_sizes_are_imbalanced():
    """The reason this matters: pooled says +9.5c, the rule says +2.0c."""
    entries = [("BIG", 0.10, 900.0), ("SMALL", -0.30, 40.0)]
    assert qc.bootstrap_pooled(entries)[0] > 0
    assert qc.bootstrap_gate(entries)[0] < 0


def test_contracts_still_weight_WITHIN_a_tournament():
    """§2: equal weight ACROSS tournaments, contract-weighted WITHIN one.

    Both names sit in one tournament, so the big fill must dominate.
    """
    entries = [("ONE", 0.10, 900.0), ("ONE", -0.06, 40.0)]
    mean, _, _ = qc.bootstrap_gate(entries)
    assert mean == pytest.approx((0.10 * 900 + -0.06 * 40) / 940)


def test_agrees_with_pooled_when_tournaments_are_the_same_size():
    entries = [("A", 0.10, 100.0), ("B", -0.02, 100.0)]
    assert qc.bootstrap_gate(entries)[0] == pytest.approx(
        qc.bootstrap_pooled(entries)[0])


# ── the interval describes the same estimator ────────────────────────────

def test_ci_brackets_the_gate_point_estimate():
    """A CI computed on a different statistic than the headline is how the
    widened sample showed [+0.08, +4.51] (excludes zero) for an effect the
    rule scores at z=0.65."""
    entries = [(f"T{i}", v, 100.0 + 50 * i)
               for i, v in enumerate([0.02, -0.01, 0.05, 0.03, -0.02, 0.04])]
    mean, lo, hi = qc.bootstrap_gate(entries)
    assert lo <= mean <= hi


def test_ci_is_clustered_by_tournament_not_by_contract():
    """Per-contract resampling would understate the CI by an order of
    magnitude (the P-017M lesson). Splitting one tournament's rows into
    more rows must not shrink the interval."""
    coarse = [("A", 0.10, 400.0), ("B", -0.05, 400.0), ("C", 0.02, 400.0)]
    split = []
    for ev, p, w in coarse:
        for _ in range(8):
            split.append((ev, p, w / 8))
    _, lo_c, hi_c = qc.bootstrap_gate(coarse)
    _, lo_s, hi_s = qc.bootstrap_gate(split)
    assert (hi_s - lo_s) == pytest.approx(hi_c - lo_c, abs=1e-9)


def test_bootstrap_is_deterministic():
    a = qc.bootstrap_gate(IMBALANCED)
    b = qc.bootstrap_gate(IMBALANCED)
    assert a == b


# ── dispersion / significance ────────────────────────────────────────────

def test_gate_dispersion_matches_the_rule_formula():
    entries = [("A", 0.02, 10.0), ("B", 0.06, 900.0), ("C", -0.01, 50.0)]
    xs = [0.02, 0.06, -0.01]
    T, sd, se, z = qc.gate_dispersion(entries)
    assert T == 3
    assert sd == pytest.approx(statistics.stdev(xs))
    assert se == pytest.approx(statistics.stdev(xs) / math.sqrt(3))
    assert z == pytest.approx(statistics.mean(xs) / se)


def test_dispersion_uses_tournament_means_not_raw_rows():
    """sd must be over x_t, not over per-name rows — otherwise a wide
    within-tournament spread inflates T and shrinks se."""
    entries = [("A", 0.20, 50.0), ("A", -0.20, 50.0), ("B", 0.01, 50.0)]
    T, _, _, _ = qc.gate_dispersion(entries)
    assert T == 2


# ── leave-one-out must use the SAME statistic as the headline ────────────

def test_leave_one_out_uses_the_gate_estimator():
    """A robustness check on a different statistic than the headline says
    nothing about the headline's robustness."""
    entries = [("A", 0.10, 900.0), ("B", -0.06, 40.0), ("C", 0.02, 300.0)]
    loo = qc.leave_one_out(entries)
    # Dropping A leaves B and C, equally weighted.
    assert loo["A"] == pytest.approx((-0.06 + 0.02) / 2, abs=5e-5)
    assert loo["B"] == pytest.approx((0.10 + 0.02) / 2, abs=5e-5)


# ── degenerate inputs ────────────────────────────────────────────────────

def test_single_tournament_returns_nan_ci():
    mean, lo, hi = qc.bootstrap_gate([("A", 0.05, 10.0)])
    assert mean == pytest.approx(0.05)
    assert math.isnan(lo) and math.isnan(hi)


def test_empty_and_zero_weight_are_safe():
    assert qc.bootstrap_gate([]) == (0.0, pytest.approx(float("nan"), nan_ok=True),
                                     pytest.approx(float("nan"), nan_ok=True))
    mean, lo, hi = qc.bootstrap_gate([("A", 0.05, 0.0), ("B", 0.01, 0.0)])
    assert mean == 0.0
    T, _, _, _ = qc.gate_dispersion([("A", 0.05, 0.0)])
    assert T == 0


# ── the emitted artifact is self-describing ──────────────────────────────

def test_replay_output_labels_its_estimator_and_keeps_pooled_separate():
    """Artifacts written before the fix carry the POOLED figure under
    `net_per_contract` and have no `estimator` key. New ones must be
    distinguishable, or the two get compared as if they were the same."""
    import inspect
    src = inspect.getsource(qc.replay)
    assert '"estimator": "equal_weight_per_tournament"' in src
    assert '"net_per_contract_pooled"' in src
    assert "bootstrap_gate(entries)" in src
    assert "bootstrap_weighted" not in src, (
        "bootstrap_weighted was the pooled estimator; it must not be "
        "reintroduced into the headline path")
