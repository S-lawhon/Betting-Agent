from datetime import datetime, timezone

from src.dislocation_cases import (
    CaseLedger, build_cases, calibrate_thresholds, settlement_basis,
)


UTC = timezone.utc
POLICY = {
    "id": "pair_policy", "status": "not_equivalent",
    "dimensions": {
        "underlying": "equivalent", "sources": "compatible",
        "postponement": "different", "review": "different",
    },
}


def _row(ts, edge, *, match="game", direction="home", scheduled_start=None):
    row = {
        "captured_at": ts, "collection_id": ts, "match_key": match,
        "quote_skew_seconds": 1.25,
        "paths": [{
            "direction": direction, "net_edge_usd": edge,
            "gross_edge_usd": edge + .01,
        }],
    }
    if scheduled_start:
        row["gemini"] = {"scheduled_start_at": scheduled_start}
        row["kalshi"] = {"scheduled_start_at": scheduled_start}
    return row


def test_case_identity_is_stable_and_active_case_accumulates():
    rows = [
        _row("2026-08-02T12:00:00Z", .04),
        _row("2026-08-02T12:05:00Z", .05),
    ]
    now = datetime(2026, 8, 2, 12, 6, tzinfo=UTC)

    first = build_cases(rows, pair_id="pair", policy=POLICY, now=now)
    second = build_cases(rows, pair_id="pair", policy=POLICY, now=now)

    assert first == second
    assert len(first) == 1
    case = first[0]
    assert case["case_id"].startswith("CV-")
    assert case["status"] == "active"
    assert case["persistent"] is True
    assert case["duration_seconds_lower_bound"] == 300
    assert case["max_net_edge_usd"] == .05


def test_below_threshold_observation_closes_case_with_observed_edge():
    rows = [
        _row("2026-08-02T12:00:00Z", .04),
        _row("2026-08-02T12:05:00Z", .05),
        _row("2026-08-02T12:10:00Z", .01),
    ]

    case = build_cases(
        rows, pair_id="pair", policy=POLICY,
        now=datetime(2026, 8, 2, 12, 11, tzinfo=UTC))[0]

    assert case["status"] == "closed"
    assert case["closure_reason"] == "edge_below_threshold"
    assert case["closed_at"] == "2026-08-02T12:10:00Z"
    assert case["post_case_edge_usd"] == .01


def test_observation_gap_splits_cases_without_inventing_survival_time():
    rows = [
        _row("2026-08-02T12:00:00Z", .04),
        _row("2026-08-02T12:10:00Z", .05),
    ]

    cases = build_cases(
        rows, pair_id="pair", policy=POLICY,
        now=datetime(2026, 8, 2, 12, 11, tzinfo=UTC))

    assert len(cases) == 2
    assert cases[0]["status"] == "closed"
    assert cases[0]["closure_reason"] == "observation_gap"
    assert cases[0]["duration_seconds_lower_bound"] == 0
    assert cases[1]["status"] == "active"


def test_slippage_breakpoint_and_survival_are_explicit():
    case = build_cases(
        [_row("2026-08-02T12:00:00Z", .04)], pair_id="pair",
        policy=POLICY, now=datetime(2026, 8, 2, 12, 1, tzinfo=UTC))[0]

    assert case["max_per_leg_slippage_usd"] == .02
    assert case["slippage_survival"]["0.010"]["survives"] is True
    assert case["slippage_survival"]["0.020"]["survives"] is False
    assert case["trade_allowed"] is False


def test_case_market_phase_distinguishes_pregame_from_after_start():
    pregame = build_cases([
        _row("2026-08-02T11:00:00Z", .04,
             scheduled_start="2026-08-02T12:00:00Z")],
        pair_id="pair", policy=POLICY,
        now=datetime(2026, 8, 2, 11, 1, tzinfo=UTC))[0]
    after = build_cases([
        _row("2026-08-02T13:00:00Z", .04,
             scheduled_start="2026-08-02T12:00:00Z")],
        pair_id="pair", policy=POLICY,
        now=datetime(2026, 8, 2, 13, 1, tzinfo=UTC))[0]

    assert pregame["market_phase"] == "pre_scheduled_start"
    assert pregame["first_seen_vs_scheduled_start_seconds"] == -3600
    assert after["market_phase"] == "after_scheduled_start"
    assert after["first_seen_vs_scheduled_start_seconds"] == 3600


def test_schedule_mismatch_is_explicit_and_uses_earliest_start():
    row = _row("2026-08-02T13:00:00Z", .04)
    row["gemini"] = {"scheduled_start_at": "2026-08-02T14:00:00Z"}
    row["kalshi"] = {"scheduled_start_at": "2026-08-02T12:00:00Z"}

    case = build_cases(
        [row], pair_id="pair", policy=POLICY,
        now=datetime(2026, 8, 2, 13, 1, tzinfo=UTC))[0]

    assert case["scheduled_start_at"] == "2026-08-02T12:00:00Z"
    assert case["market_phase"] == "after_scheduled_start"
    assert case["schedule_alignment_status"] == "mismatched"
    assert case["venue_start_difference_seconds"] == 7200


def test_settlement_basis_classifies_risk_without_claiming_incidence():
    basis = settlement_basis(POLICY)

    assert basis["classification"] == "contract_non_equivalence"
    assert basis["risk_dimensions"] == ["postponement", "review"]
    assert basis["aligned_dimensions"] == ["sources", "underlying"]
    assert basis["historical_incidence_status"] == "unmeasured"
    assert basis["trade_allowed"] is False


def test_threshold_calibration_replays_same_tape():
    rows = [
        _row("2026-08-02T12:00:00Z", .025),
        _row("2026-08-02T12:05:00Z", .025),
        _row("2026-08-02T12:10:00Z", .045),
    ]

    calibration = calibrate_thresholds(
        rows, pair_id="pair", policy=POLICY,
        now=datetime(2026, 8, 2, 12, 11, tzinfo=UTC),
        thresholds=(.02, .03, .05))

    indexed = {row["threshold_usd"]: row for row in calibration}
    assert indexed[.02]["case_count"] == 1
    assert indexed[.02]["persistent_count"] == 1
    assert indexed[.03]["case_count"] == 1
    assert indexed[.03]["single_snapshot_count"] == 1
    assert indexed[.05]["case_count"] == 0
    assert indexed[.02]["pre_scheduled_start_count"] == 0
    assert indexed[.02]["after_scheduled_start_count"] == 0
    assert indexed[.02]["schedule_mismatch_count"] == 0


def test_sqlite_ledger_is_idempotent_and_preserves_lifetime_cases(tmp_path):
    now = datetime(2026, 8, 2, 12, 1, tzinfo=UTC)
    cases = build_cases([_row("2026-08-02T12:00:00Z", .04)],
                        pair_id="pair", policy=POLICY, now=now)
    ledger = CaseLedger(tmp_path / "cases.sqlite3")
    try:
        ledger.upsert(cases, updated_at=now)
        ledger.upsert(cases, updated_at=now)
        summary = ledger.summary("pair", now=now)
    finally:
        ledger.close()

    assert summary["lifetime_cases"] == 1
    assert summary["cases_last_24h"] == 1
    assert summary["active_cases"] == 1
    assert summary["single_snapshot_share"] == 1.0
    assert summary["top_last_24h"][0]["case_id"] == cases[0]["case_id"]
