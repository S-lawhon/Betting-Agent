"""
tests/test_p022_checkpoint.py
─────────────────────────────
Tests for the P-022 gate reader (scripts/p022_checkpoint.py).

The reader is the ONLY sanctioned verdict on P-022, and until 2026-07-27 it
could not see P-022's output at all: it read four log paths the pod does not
write, and kept only rows carrying `outcome`/`contracts` fields the pod does
not emit. Pointed straight at a file of genuinely settled rows it reported
`0 tournaments`.

So the regression under test is not "does it compute the statistic" — §2/§3
were always right — but **does the loader actually reach the pod's rows**, and
does it carry them into the rule without distorting the two things the rule is
most particular about: rounds pooling into one tournament, and scalar
settlements counting at their realised value.
"""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SPEC = importlib.util.spec_from_file_location(
    "p022_checkpoint",
    Path(__file__).resolve().parent.parent / "scripts" / "p022_checkpoint.py")
cp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cp)


def _settle(fill_id, ticker, event, result, payout, price, qty):
    """A row in the exact shape src/round_leader_fade_maker.py writes."""
    pnl = (price - payout) * qty          # maker fee is 0 on *LEAD series
    return {
        "type": "SETTLE", "fill_id": fill_id, "pod_id": "P-022",
        "ticker": ticker, "event": event, "result": result, "payout": payout,
        "fill_price": price, "qty": qty, "maker_fee_per_contract": 0.0,
        "pnl_usd": pnl, "ts": 1.0e9, "iso": "2026-07-27T00:00:00+00:00",
    }


def _write(tmp_path, rows, name="round_leader_fade_fills.jsonl"):
    p = tmp_path / name
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return str(p)


# ── the regression ───────────────────────────────────────────────────

def test_reader_sees_the_engines_own_settle_rows(tmp_path):
    """Before the loader fix this returned 0 rows and T=0."""
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-COPC26-TCLE", "COPC26", "scalar", 0.5, 0.06, 5),
        _settle("f2", "KXLPGAR1LEAD-ISPH26-LAUCOU", "ISPH26", "scalar", 0.5, 0.06, 5),
    ])
    trades = cp.load_settled([path])
    assert len(trades) == 2
    r = cp.evaluate(trades)
    assert r["T"] == 2
    assert r["n_contracts"] == 10
    assert abs(r["edge_cents"] - (-44.0)) < 1e-6
    assert abs(r["pnl_usd"] - (-4.40)) < 1e-9


def test_non_settle_rows_are_ignored(tmp_path):
    """QUOTE / PULL / FILL / MARKOUT share the file and must not count."""
    path = _write(tmp_path, [
        {"type": "QUOTE", "pod_id": "P-022", "ticker": "T", "ask": 0.08},
        {"type": "PULL", "pod_id": "P-022", "ticker": "T", "reason": "kill"},
        {"type": "FILL", "pod_id": "P-022", "ticker": "T", "qty": 5,
         "price": 0.08},
        {"type": "MARKOUT", "pod_id": "P-022", "ticker": "T", "horizon_s": 300},
        _settle("f1", "KXPGAR1LEAD-A26-X", "A26", "no", 0.0, 0.08, 4),
    ])
    trades = cp.load_settled([path])
    assert len(trades) == 1
    assert cp.evaluate(trades)["T"] == 1


def test_other_pods_rows_are_ignored(tmp_path):
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-A26-X", "A26", "no", 0.0, 0.08, 4),
        dict(_settle("f2", "KXPGATOP10-A26-Y", "A26", "no", 0.0, 0.08, 4),
             pod_id="P-017"),
    ])
    assert len(cp.load_settled([path])) == 1


# ── §2: one tournament = one observation ─────────────────────────────

def test_rounds_of_one_event_pool_into_a_single_observation(tmp_path):
    """R1/R2/R3 of one golf event share a leaderboard — §2 says they are ONE
    observation, pooled, not three."""
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-ROC26-A", "ROC26", "no", 0.0, 0.06, 5),
        _settle("f2", "KXPGAR2LEAD-ROC26-B", "ROC26", "no", 0.0, 0.06, 5),
        _settle("f3", "KXPGAR3LEAD-ROC26-C", "ROC26", "no", 0.0, 0.06, 5),
        _settle("f4", "KXPGAR1LEAD-AIG26-D", "AIG26", "no", 0.0, 0.06, 5),
    ])
    r = cp.evaluate(cp.load_settled([path]))
    assert r["T"] == 2
    assert set(r["tournaments"]) == {"ROC26", "AIG26"}
    assert {o["event"] for o in r["tournament_observations"]
            if o["eligible"]} == {"ROC26", "AIG26"}


def test_tournament_observation_uses_latest_round_settlement(tmp_path):
    first = _settle("f1", "KXPGAR1LEAD-ROC26-A", "ROC26", "no", 0.0, 0.06, 5)
    second = _settle("f2", "KXPGAR2LEAD-ROC26-B", "ROC26", "no", 0.0, 0.06, 5)
    first["iso"] = "2026-07-27T00:00:00Z"
    second["iso"] = "2026-07-29T12:00:00Z"
    r = cp.evaluate(cp.load_settled([_write(tmp_path, [first, second])]))
    assert r["tournament_observations"] == [{
        "event": "ROC26", "settled_at_utc": "2026-07-29T12:00:00Z",
        "eligible": True,
    }]


def test_within_tournament_is_contract_weighted(tmp_path):
    """§2: contract-weighted within a tournament, equal weight across."""
    path = _write(tmp_path, [
        # ROC26: 90 contracts at +6c, 10 at -44c -> (90*6 + 10*-44)/100 = +1.0c
        _settle("f1", "KXPGAR1LEAD-ROC26-A", "ROC26", "no", 0.0, 0.06, 90),
        _settle("f2", "KXPGAR1LEAD-ROC26-B", "ROC26", "scalar", 0.5, 0.06, 10),
        # AIG26: a single small fill still counts equally across tournaments
        _settle("f3", "KXLPGAR1LEAD-AIG26-C", "AIG26", "no", 0.0, 0.05, 1),
    ])
    r = cp.evaluate(cp.load_settled([path]))
    assert abs(r["tournaments"]["ROC26"] - 1.0) < 1e-6
    assert abs(r["tournaments"]["AIG26"] - 5.0) < 1e-6
    assert abs(r["edge_cents"] - 3.0) < 1e-6      # equal weight, not 1.06


# ── §3 / §8.3: scalar counts, at its realised value ──────────────────

def test_scalar_settlement_counts_as_a_loss_not_a_void(tmp_path):
    """The whole mechanism under test. A 6c fade hit by a two-way tie owes
    44c; excluding it (or booking $0) would leave only the premium."""
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-A26-X", "A26", "scalar", 0.5, 0.06, 10),
    ])
    trades = cp.load_settled([path])
    assert len(trades) == 1
    assert trades[0]["result"] == "scalar"
    r = cp.evaluate(trades)
    assert abs(r["edge_cents"] - (-44.0)) < 1e-6
    assert r["tournaments_positive"] == 0


def test_a_three_way_tie_books_its_realised_value(tmp_path):
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-A26-X", "A26", "scalar", 0.33, 0.06, 10),
    ])
    r = cp.evaluate(cp.load_settled([path]))
    assert abs(r["edge_cents"] - (-27.0)) < 1e-6


def test_a_zero_pnl_settlement_is_still_an_observation(tmp_path):
    """Cannot arise under the locked parameters, but the reader must not
    depend on that: a WIN/LOSS sign test would silently drop it."""
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-A26-X", "A26", "scalar", 0.06, 0.06, 10),
    ])
    trades = cp.load_settled([path])
    assert len(trades) == 1
    assert cp.evaluate(trades)["T"] == 1


# ── dedup ────────────────────────────────────────────────────────────

def test_duplicate_rows_do_not_double_weight_a_tournament(tmp_path):
    """A rotated log, or two overlapping globs in DEFAULT_LOGS, must not
    make one settlement count twice."""
    row = _settle("f1", "KXPGAR1LEAD-A26-X", "A26", "no", 0.0, 0.06, 5)
    path = _write(tmp_path, [row, row])
    trades = cp.load_settled([path, path])
    assert len(trades) == 1
    assert cp.evaluate(trades)["n_contracts"] == 5


# ── the legacy shape still works ─────────────────────────────────────

def test_trade_store_shaped_rows_still_load_and_voids_still_excluded(tmp_path):
    """A settler-written P-022 row would land in the shared trade log in the
    canonical shape. §3: VOIDs are excluded — no risk was taken."""
    path = _write(tmp_path, [
        {"pod_id": "P-022", "market_id": "KXPGAR1LEAD-A26-X", "outcome": "WIN",
         "pnl_usd": 0.30, "contracts": 5, "settled_at_utc": "2026-07-27T00:00Z",
         "extra": {"event_code": "A26", "contracts": 5}},
        {"pod_id": "P-022", "market_id": "KXPGAR1LEAD-B26-Y", "outcome": "VOID",
         "pnl_usd": 0.0, "contracts": 5, "settled_at_utc": "2026-07-27T00:00Z",
         "extra": {"event_code": "B26", "contracts": 5}},
    ], name="trade_log.jsonl")
    trades = cp.load_settled([path])
    assert len(trades) == 1
    r = cp.evaluate(trades)
    assert r["T"] == 1
    assert abs(r["edge_cents"] - 6.0) < 1e-6


# ── the paths themselves ─────────────────────────────────────────────

def test_default_logs_cover_the_path_the_pod_actually_writes():
    """The defect in one line: the reader must point at the engine's log."""
    assert any("round_leader_fade_fills" in p for p in cp.DEFAULT_LOGS)


# ── §7: a tournament with a breached cap is EXCLUDED from T ──────────

def _breach(event, kind="per_name", observed=23.75, limit=5.0):
    return {"type": "CAP_BREACH", "pod_id": "P-022", "event": event,
            "kind": kind, "observed": observed, "limit": limit,
            "ts": 1.0e9, "iso": "2026-07-27T00:00:00+00:00"}


def test_breached_tournament_is_excluded_from_T(tmp_path):
    """§7 says so, and until 2026-07-28 the reader had no breach concept at
    all — no exclusion, no field read, nothing. A breach recorded nowhere and
    checked nowhere is not a gate condition."""
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-COPC26-TCLE", "COPC26", "no", 0.0, 0.06, 5),
        _settle("f2", "KXLPGAR1LEAD-ISPH26-LAU", "ISPH26", "no", 0.0, 0.06, 5),
        _breach("ISPH26"),
    ])
    r = cp.evaluate(cp.load_settled([path]), cp.load_breached_events([path]))
    assert r["T"] == 1
    assert list(r["tournaments"]) == ["COPC26"]
    assert r["excluded"] == {"ISPH26": ["per_name"]}
    excluded = [o for o in r["tournament_observations"] if not o["eligible"]]
    assert excluded == [{
        "event": "ISPH26", "settled_at_utc": "2026-07-27T00:00:00Z",
        "eligible": False, "exclusion_reasons": ["per_name"],
    }]


def test_breach_excludes_every_round_of_that_tournament(tmp_path):
    """Rounds pool into one observation, so the exclusion must too."""
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-ROC26-A", "ROC26", "no", 0.0, 0.06, 5),
        _settle("f2", "KXPGAR2LEAD-ROC26-B", "ROC26", "yes", 1.0, 0.06, 5),
        _settle("f3", "KXPGAR1LEAD-COPC26-C", "COPC26", "no", 0.0, 0.06, 5),
        _breach("ROC26", kind="per_tournament", observed=90.0, limit=50.0),
    ])
    r = cp.evaluate(cp.load_settled([path]), cp.load_breached_events([path]))
    assert r["T"] == 1 and "ROC26" not in r["tournaments"]
    assert r["n_contracts"] == 5, "excluded rows must not inflate the count"


def test_all_tournaments_breached_gives_no_decision(tmp_path):
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-ROC26-A", "ROC26", "no", 0.0, 0.06, 5),
        _breach("ROC26"),
    ])
    r = cp.evaluate(cp.load_settled([path]), cp.load_breached_events([path]))
    assert r["T"] == 0 and r["verdict"] == "NO DECISION"
    assert "cap breach" in r["reason"]


def test_no_breaches_leaves_the_verdict_untouched(tmp_path):
    path = _write(tmp_path, [
        _settle("f1", "KXPGAR1LEAD-ROC26-A", "ROC26", "no", 0.0, 0.06, 5),
        _settle("f2", "KXPGAR1LEAD-COPC26-C", "COPC26", "no", 0.0, 0.06, 5),
    ])
    rows = cp.load_settled([path])
    assert cp.load_breached_events([path]) == {}
    assert cp.evaluate(rows)["T"] == cp.evaluate(rows, {})["T"] == 2


# ── AMENDMENT 1 (2026-07-28): T = 14 -> 24 ───────────────────────────

def test_decision_threshold_is_24_and_matches_the_rules_own_arithmetic():
    """The threshold is not a chosen number — it is the solve of the rule's own
    criterion, and this test re-derives it rather than hard-coding trust.

    §5 fixes the criterion as "the smallest sample with 90% power against the
    effect actually measured" and gives the formula and sigma. T=14 was
    ceil(13.3) for the Phase-2 estimate of +3.4c/ct. The 2026-07-28 widening
    revised the pooled estimate to +2.57c/ct; the same solve gives 23.3 -> 24.
    """
    import math
    se19 = (5.1 - 1.7) / 2 / 1.95996          # the published T=19 CI
    sigma = se19 * math.sqrt(19)
    assert abs(sigma - 3.781) < 0.01, "sigma drifted from the rule's stated 3.781"

    def solve(effect_cents, z_power=1.2816):   # 1.2816 = 90% power
        return ((2.0 + z_power) * sigma / effect_cents) ** 2

    assert math.ceil(solve(3.40)) == 14, "the ORIGINAL threshold must still solve to 14"
    assert math.ceil(solve(2.57)) == cp.MIN_T_DECISION == 24


def test_the_amendment_only_moved_the_threshold():
    """HARD KILL and the PASS bar are untouched — the amendment raised the
    sample size, not the standard of proof."""
    assert cp.PASS_Z == 2.0
    assert cp.HARD_KILL_Z == -2.0


def test_extension_is_still_40_and_known_to_be_inconsistent():
    """Deliberately NOT amended. Its stated rationale is 80% power vs HALF the
    measured effect; half of +2.57c solves to ~70, and 40 now delivers ~53%.
    Sam has not ruled on it, so it stays 40 — this test exists so the
    inconsistency is visible rather than forgotten."""
    import math
    se19 = (5.1 - 1.7) / 2 / 1.95996
    sigma = se19 * math.sqrt(19)
    consistent = ((2.0 + 0.8416) * sigma / (2.57 / 2)) ** 2
    assert cp.MIN_T_EXTENSION == 40
    assert math.ceil(consistent) == 70, "the rationale-consistent extension"
    assert cp.MIN_T_EXTENSION < consistent, "40 is below its own rationale"


def test_a_sample_between_14_and_24_is_now_NO_DECISION(tmp_path):
    """The behavioural consequence: a T=19 sample that would have produced a
    verdict under the old threshold must now report NO DECISION."""
    rows = [_settle(f"f{i}", f"KXPGAR1LEAD-T{i}26-A", f"T{i}26", "no", 0.0, 0.06, 5)
            for i in range(19)]
    r = cp.evaluate(cp.load_settled([_write(tmp_path, rows)]))
    assert r["T"] == 19
    assert r["verdict"] == "NO DECISION"
    assert "24" in r["reason"]
