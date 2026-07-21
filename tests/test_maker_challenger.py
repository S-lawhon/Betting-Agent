"""Tests for Tier-2 champion/challenger evaluation (src/maker_challenger.py).

The first three sections pin the three constraints the design depends on:
  1. the champion is frozen and untouchable,
  2. proposals only — no config/param/pod write path exists,
  3. only conservative arms (the doc's direction rule) are permitted.

The rest cover the replay arithmetic and inference.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import pytest

from src.kalshi_fees import fee_per_contract
from src.maker_challenger import (
    CHAMPION_ARM, DEFAULT_ARMS, GATE_HORIZON_S, ArmSpec, NotConservativeError,
    arms_from_config, build_tape, cluster_bootstrap_delta,
    detectable_effect_c, evaluate_arm, intracluster_correlation, verify_subset,
)

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


# ── Fixtures ─────────────────────────────────────────────────────────

def fill(fill_id, game_pk, side, price, trade_price, markout,
         ssc=100.0, qty=10.0, shadow=False):
    """A FILL + its matching +5m MARKOUT, as the pod writes them."""
    return [
        {"type": "FILL", "fill_id": fill_id, "game_pk": game_pk,
         "side": side, "price": price, "trade_price": trade_price,
         "qty": qty, "shadow": shadow, "secs_since_state_change": ssc},
        {"type": "MARKOUT", "fill_id": fill_id, "horizon_s": GATE_HORIZON_S,
         "markout_per_contract": markout, "shadow": shadow},
    ]


def tape_of(*groups):
    recs = []
    for g in groups:
        recs.extend(g)
    return build_tape(recs)


# ═════════════════════════════════════════════════════════════════════
# CONSTRAINT 1 — the champion is frozen
# ═════════════════════════════════════════════════════════════════════

def test_challenger_module_does_not_import_the_champion_pod():
    """Structural guarantee: Tier 2 cannot execute champion code because
    it never imports it. Checked on source text so the guarantee survives
    refactors that would otherwise pass a mock-based test."""
    text = (SRC / "maker_challenger.py").read_text()
    assert "live_maker_pod" not in text
    assert "LiveMakerEngine" not in text


def test_champion_pod_does_not_import_the_challenger():
    """The reverse direction matters more: if the pod imported Tier 2,
    challenger code would run inside the champion's quoting loop."""
    text = (SRC / "pods" / "live_maker_pod.py").read_text()
    assert "maker_challenger" not in text


def test_champion_pod_source_is_unmodified_by_tier2():
    """Tier 2 must not have edited the champion. Pin the quoting
    parameters and the fill predicate that define its behaviour."""
    text = (SRC / "pods" / "live_maker_pod.py").read_text()
    for sig in (
        "base_half_width: float = 0.025",
        "sens_mult: float = 0.15",
        "max_half_width: float = 0.06",
        "flb_skew_coef: float = 0.04",
        "inv_skew_coef: float = 0.5",
        "quote_size: float = 20.0",
        "max_net_contracts: float = 100.0",
        "quote_max_inning: int = 8",
        "max_tilt: float = 0.03",
        "max_divergence: float = 0.15",
    ):
        assert sig in text, f"champion parameter changed: {sig}"


def test_champion_arm_is_a_noop():
    """The champion arm must reproduce the recorded tape exactly — same
    fills, same prices. If it did not, the comparison baseline would be a
    different strategy than the one the gate is judging."""
    t = tape_of(
        fill("a", 1, "buy", 0.50, 0.48, +0.02),
        fill("b", 1, "sell", 0.60, 0.63, -0.01),
        fill("c", 2, "buy", 0.30, 0.28, +0.03),
    )
    champ = evaluate_arm(t, CHAMPION_ARM)
    assert champ.n == 3
    assert champ.dropped == []
    assert champ.retention == 1.0
    for f in champ.fills:
        assert f.arm_price == f.champ_price


def test_evaluating_arms_does_not_mutate_the_tape():
    """Replay is read-only on its input; a mutated tape would corrupt the
    champion's own numbers on the next arm."""
    t = tape_of(fill("a", 1, "buy", 0.50, 0.48, +0.02, ssc=5.0))
    before = json.dumps(t, sort_keys=True)
    for spec in (CHAMPION_ARM,) + DEFAULT_ARMS:
        evaluate_arm(t, spec)
    assert json.dumps(t, sort_keys=True) == before


# ═════════════════════════════════════════════════════════════════════
# CONSTRAINT 2 — proposals only, no auto-apply
# ═════════════════════════════════════════════════════════════════════

def test_no_config_write_path_in_challenger_module():
    """No write/dump/save call of any kind in the evaluator."""
    text = (SRC / "maker_challenger.py").read_text()
    for forbidden in ("write_text(", "yaml.dump", "json.dump",
                      "open(", ".save(", "os.replace"):
        assert forbidden not in text, f"write path present: {forbidden}"


def test_report_script_writes_only_into_the_proposals_dir():
    """The report is the only writer in Tier 2, and it may write exactly
    one kind of artefact: a markdown proposal."""
    text = (SCRIPTS / "maker_challenger_report.py").read_text()
    assert text.count("write_text(") == 1
    assert 'DEFAULT_OUT_DIR = "live_agent_research/proposals"' in text
    # The single write must target a path derived from out_dir, and the
    # only mkdir must be out_dir's.
    assert "out = out_dir / f\"P016_tier2_challenger_" in text
    assert "out.write_text(text)" in text
    assert text.count("mkdir(") == 1 and "out_dir.mkdir(" in text
    # Must not touch the pod's own anchors, its config, or write YAML.
    for forbidden in ("maker_anchors", "config_multi_pod", "yaml.dump",
                      "maker_quotes", ".maker_diag_state"):
        assert forbidden not in text, f"forbidden target: {forbidden}"
    # The fills log is read-only: it appears only as a default path and
    # is opened via load_jsonl, never written.
    assert "fills_path.write" not in text
    assert "open(" not in text


def test_report_script_makes_no_api_calls():
    """Tier 2 must add zero load to the shared Kalshi rate budget."""
    text = (SCRIPTS / "maker_challenger_report.py").read_text()
    for forbidden in ("KalshiPublic", "requests.", "http", "urlopen"):
        assert forbidden not in text, f"network call present: {forbidden}"


def test_report_writes_a_proposal_and_nothing_else(tmp_path):
    from scripts.maker_challenger_report import main

    fills = tmp_path / "maker_fills.jsonl"
    recs = []
    for i in range(40):
        recs += fill(f"f{i}", i % 6, "buy" if i % 2 else "sell",
                     0.50, 0.47 if i % 2 else 0.53,
                     0.01 if i % 3 else -0.02, ssc=float(i))
    fills.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    out = tmp_path / "proposals"
    before = sorted(p.name for p in tmp_path.iterdir())
    rc = main(["--fills", str(fills), "--out-dir", str(out)])
    assert rc == 0

    written = list(out.iterdir())
    assert len(written) == 1
    assert written[0].suffix == ".md"
    body = written[0].read_text()
    assert "PROPOSAL" in body
    assert "Nothing has been applied" in body
    # The fills log must be byte-identical after the run.
    assert sorted(p.name for p in tmp_path.iterdir()) == \
        sorted(before + ["proposals"])
    assert fills.read_text() == "\n".join(json.dumps(r) for r in recs) + "\n"


def test_default_config_has_no_challenger_block():
    """Behaviour-preserving default: the live config is untouched, so
    arms come from code and the champion's from_config is unaffected."""
    import yaml
    root = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((root / "config_multi_pod.yaml").read_text())
    p016 = (cfg.get("pods") or {}).get("P-016") or {}
    assert "challenger" not in p016
    assert arms_from_config(cfg) == DEFAULT_ARMS


# ═════════════════════════════════════════════════════════════════════
# CONSTRAINT 3 — conservative arms only (the doc's direction rule)
# ═════════════════════════════════════════════════════════════════════

def test_tightening_the_spread_is_refused():
    with pytest.raises(NotConservativeError):
        ArmSpec(name="tighter", description="", widen_cents=-0.01).validate()


def test_tightening_inside_the_event_window_is_refused():
    with pytest.raises(NotConservativeError):
        ArmSpec(name="t2", description="", event_window_s=10,
                event_action="widen", event_widen_cents=-0.01).validate()


def test_all_default_arms_are_conservative():
    for spec in DEFAULT_ARMS:
        spec.validate()
        assert spec.widen_cents >= 0
        assert spec.event_widen_cents >= 0


def test_every_default_arm_fills_a_strict_subset_of_the_champion():
    """The observability guarantee, checked against data rather than
    argued: an arm that filled outside the champion's tape would be
    measuring fills that were never recorded."""
    t = tape_of(
        *[fill(f"f{i}", i % 4, "buy" if i % 2 else "sell",
               0.50, 0.46 if i % 2 else 0.54, 0.01, ssc=float(i * 3))
          for i in range(30)]
    )
    champ = evaluate_arm(t, CHAMPION_ARM)
    champ_ids = {f.fill_id for f in champ.fills}
    for spec in DEFAULT_ARMS:
        r = evaluate_arm(t, spec)
        verify_subset(champ, r)
        assert {f.fill_id for f in r.fills} <= champ_ids
        assert r.n <= champ.n


def test_verify_subset_raises_on_a_fabricated_extra_fill():
    """verify_subset must actually fire, not just pass vacuously."""
    t = tape_of(fill("a", 1, "buy", 0.50, 0.48, +0.02))
    champ = evaluate_arm(t, CHAMPION_ARM)
    rogue = evaluate_arm(t, CHAMPION_ARM)
    rogue.fills.append(
        type(rogue.fills[0])(fill_id="ghost", game_pk=1, side="buy",
                             champ_price=0.5, arm_price=0.5, qty=1.0,
                             markout_c=0.0, secs_since_state_change=None))
    with pytest.raises(NotConservativeError):
        verify_subset(champ, rogue)


def test_config_arms_are_validated_for_direction():
    bad = {"pods": {"P-016": {"challenger": {"arms": [
        {"name": "sneaky", "widen_cents": -0.02}]}}}}
    with pytest.raises(NotConservativeError):
        arms_from_config(bad)


# ═════════════════════════════════════════════════════════════════════
# Replay arithmetic
# ═════════════════════════════════════════════════════════════════════

def test_widening_moves_the_bid_down_and_the_ask_up():
    t = tape_of(
        fill("bid", 1, "buy", 0.50, 0.40, +0.02),
        fill("ask", 1, "sell", 0.60, 0.70, -0.01),
    )
    r = evaluate_arm(t, ArmSpec("w", "", widen_cents=0.02))
    by = {f.fill_id: f for f in r.fills}
    assert by["bid"].arm_price == pytest.approx(0.48)
    assert by["ask"].arm_price == pytest.approx(0.62)


def test_widening_adds_exactly_the_width_to_markout_on_both_sides():
    """The closed-form result the replay depends on: a fill retained at a
    quote `w` further out earns `w` more per contract, bid or ask."""
    w = 0.02
    t = tape_of(
        fill("bid", 1, "buy", 0.50, 0.40, +0.02),
        fill("ask", 1, "sell", 0.50, 0.60, -0.02),
    )
    champ = {f.fill_id: f for f in evaluate_arm(t, CHAMPION_ARM).fills}
    arm = {f.fill_id: f for f in
           evaluate_arm(t, ArmSpec("w", "", widen_cents=w)).fills}
    for fid in ("bid", "ask"):
        # Re-add the fee difference: markout_c is fee-adjusted at each
        # arm's own price, and the fee is price-dependent.
        champ_fee = fee_per_contract(champ[fid].champ_price, maker=True) * 100
        arm_fee = fee_per_contract(arm[fid].arm_price, maker=True) * 100
        gross_delta = (arm[fid].markout_c + arm_fee) - \
                      (champ[fid].markout_c + champ_fee)
        assert gross_delta == pytest.approx(w * 100, abs=1e-6)


def test_a_print_that_no_longer_goes_through_is_dropped():
    """Print at 0.49 goes through a 0.50 bid but not a 0.48 one."""
    t = tape_of(fill("a", 1, "buy", 0.50, 0.49, +0.01))
    assert evaluate_arm(t, CHAMPION_ARM).n == 1
    r = evaluate_arm(t, ArmSpec("w", "", widen_cents=0.02))
    assert r.n == 0
    assert len(r.dropped) == 1


def test_suppression_drops_only_fills_inside_the_window():
    t = tape_of(
        fill("early", 1, "buy", 0.50, 0.40, +0.01, ssc=5.0),
        fill("edge", 1, "buy", 0.50, 0.40, +0.01, ssc=15.0),
        fill("late", 1, "buy", 0.50, 0.40, +0.01, ssc=25.0),
    )
    r = evaluate_arm(t, ArmSpec("s", "", event_window_s=15.0,
                                event_action="suppress"))
    assert {f.fill_id for f in r.fills} == {"late"}
    assert {f.fill_id for f in r.dropped} == {"early", "edge"}
    assert r.retention == pytest.approx(1 / 3)


def test_unknown_staleness_is_treated_as_outside_the_window():
    """A missing secs_since_state_change means the pod never observed a
    state change on that book. Suppressing on absent evidence would
    silently discard fills, so it must be treated as outside."""
    t = tape_of(fill("a", 1, "buy", 0.50, 0.40, +0.01, ssc=None))
    r = evaluate_arm(t, ArmSpec("s", "", event_window_s=60.0,
                                event_action="suppress"))
    assert r.n == 1


def test_event_widen_applies_only_inside_the_window():
    t = tape_of(
        fill("in", 1, "buy", 0.50, 0.40, +0.01, ssc=5.0),
        fill("out", 1, "buy", 0.50, 0.40, +0.01, ssc=90.0),
    )
    r = evaluate_arm(t, ArmSpec("ew", "", event_window_s=30.0,
                                event_action="widen",
                                event_widen_cents=0.03))
    by = {f.fill_id: f for f in r.fills}
    assert by["in"].arm_price == pytest.approx(0.47)
    assert by["out"].arm_price == pytest.approx(0.50)


def test_base_widen_and_event_widen_compose():
    t = tape_of(fill("in", 1, "buy", 0.50, 0.30, +0.01, ssc=5.0))
    r = evaluate_arm(t, ArmSpec("c", "", widen_cents=0.01,
                                event_window_s=30.0, event_action="widen",
                                event_widen_cents=0.02))
    assert r.fills[0].arm_price == pytest.approx(0.47)


def test_dropped_fills_retain_champion_pricing():
    """A dropped fill is reported at the champion's own economics — it is
    what the arm gave up, so repricing it onto the arm would misstate the
    cost of the arm."""
    t = tape_of(fill("a", 1, "buy", 0.50, 0.49, +0.01))
    r = evaluate_arm(t, ArmSpec("w", "", widen_cents=0.02))
    assert r.dropped[0].arm_price == pytest.approx(0.50)


# ═════════════════════════════════════════════════════════════════════
# Tape construction
# ═════════════════════════════════════════════════════════════════════

def test_shadow_fills_are_excluded_by_default():
    t = tape_of(
        fill("real", 1, "buy", 0.50, 0.48, +0.01),
        fill("shad", 1, "buy", 0.50, 0.48, +0.01, shadow=True),
    )
    assert [r["fill_id"] for r in t] == ["real"]


def test_fills_without_a_markout_are_skipped_not_errored():
    recs = [{"type": "FILL", "fill_id": "x", "game_pk": 1, "side": "buy",
             "price": 0.5, "trade_price": 0.4, "qty": 1.0, "shadow": False,
             "secs_since_state_change": 10.0}]
    assert build_tape(recs) == []


def test_only_the_gate_horizon_markout_is_used():
    recs = [
        {"type": "FILL", "fill_id": "x", "game_pk": 1, "side": "buy",
         "price": 0.5, "trade_price": 0.4, "qty": 1.0, "shadow": False,
         "secs_since_state_change": 10.0},
        {"type": "MARKOUT", "fill_id": "x", "horizon_s": 60,
         "markout_per_contract": 0.99, "shadow": False},
        {"type": "MARKOUT", "fill_id": "x", "horizon_s": GATE_HORIZON_S,
         "markout_per_contract": 0.01, "shadow": False},
    ]
    t = build_tape(recs)
    assert t[0]["markout_per_contract"] == pytest.approx(0.01)


# ═════════════════════════════════════════════════════════════════════
# Inference
# ═════════════════════════════════════════════════════════════════════

def test_bootstrap_of_champion_against_itself_is_centred_on_zero():
    t = tape_of(*[fill(f"f{i}", i % 5, "buy", 0.50, 0.40,
                       0.01 * ((i % 7) - 3)) for i in range(50)])
    champ = evaluate_arm(t, CHAMPION_ARM)
    bs = cluster_bootstrap_delta(champ, champ, reps=500)
    assert bs["delta_c"] == pytest.approx(0.0, abs=1e-9)
    assert bs["ci_lo_c"] == pytest.approx(0.0, abs=1e-9)
    assert bs["ci_hi_c"] == pytest.approx(0.0, abs=1e-9)


def test_bonferroni_widens_the_interval_with_more_arms():
    t = tape_of(*[fill(f"f{i}", i % 6, "buy", 0.50, 0.40,
                       0.01 * ((i % 9) - 4), ssc=float(i))
                  for i in range(60)])
    champ = evaluate_arm(t, CHAMPION_ARM)
    arm = evaluate_arm(t, ArmSpec("s", "", event_window_s=20.0,
                                  event_action="suppress"))
    one = cluster_bootstrap_delta(champ, arm, reps=4000, n_arms=1)
    four = cluster_bootstrap_delta(champ, arm, reps=4000, n_arms=4)
    assert four["alpha"] < one["alpha"]
    assert (four["ci_hi_c"] - four["ci_lo_c"]) >= \
           (one["ci_hi_c"] - one["ci_lo_c"])


def test_bootstrap_clusters_by_game_not_by_fill():
    """With all fills in ONE game, resampling games can only ever draw
    that game, so the interval must collapse to a point. A fill-level
    bootstrap would produce a spuriously narrow non-degenerate CI."""
    t = tape_of(*[fill(f"f{i}", 1, "buy", 0.50, 0.40, 0.01 * (i - 10))
                  for i in range(20)])
    champ = evaluate_arm(t, CHAMPION_ARM)
    arm = evaluate_arm(t, ArmSpec("w", "", widen_cents=0.01))
    bs = cluster_bootstrap_delta(champ, arm, reps=200)
    assert bs["n_games"] == 1
    assert bs["ci_lo_c"] == pytest.approx(bs["ci_hi_c"])


def test_detectable_effect_shrinks_with_sample_and_grows_with_arms():
    a = detectable_effect_c(500, 2.0, 12.0, n_arms=1)
    b = detectable_effect_c(2000, 2.0, 12.0, n_arms=1)
    c = detectable_effect_c(2000, 2.0, 12.0, n_arms=8)
    assert b < a
    assert c > b


def test_design_effect_is_at_least_one_for_positive_icc():
    t = tape_of(*[fill(f"f{i}", i % 4, "buy", 0.50, 0.40,
                       0.01 * (i % 4)) for i in range(40)])
    champ = evaluate_arm(t, CHAMPION_ARM)
    icc, deff = intracluster_correlation(champ)
    assert deff >= 1.0


def test_icc_is_nan_with_too_few_clusters():
    t = tape_of(fill("a", 1, "buy", 0.50, 0.40, 0.01))
    icc, deff = intracluster_correlation(evaluate_arm(t, CHAMPION_ARM))
    assert icc != icc and deff != deff        # NaN
