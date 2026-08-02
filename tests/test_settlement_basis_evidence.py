from src.mlb_statsapi import MlbGame
from src.settlement_basis_evidence import classify_game, summarize_evidence


def game(status="Final", detailed="Final", game_pk=1):
    return MlbGame(
        game_pk=game_pk, away_name="Washington Nationals",
        home_name="Atlanta Braves", away_abbr="WSH", home_abbr="ATL",
        start_utc="2026-08-02T17:35:00Z", status=status,
        detailed_state=detailed,
    )


def test_classify_game_records_only_observable_exception_dimensions():
    final = classify_game(game(), "2026-08-02")
    postponed = classify_game(game("Preview", "Postponed"), "2026-08-02")
    suspended = classify_game(game("Live", "Suspended"), "2026-08-02")
    canceled = classify_game(game("Preview", "Cancelled"), "2026-08-02")
    pending = classify_game(game("Live", "In Progress"), "2026-08-02")

    assert final["match_key"] == "2026-08-02|braves|nationals"
    assert final["category"] == "ordinary_final_unverified"
    assert final["terminal"] is True
    assert postponed["observed_risk_dimensions"] == ["postponement"]
    assert suspended["observed_risk_dimensions"] == ["suspended_game"]
    assert canceled["observed_risk_dimensions"] == ["cancellation"]
    assert pending["category"] == "pending"
    assert pending["terminal"] is False


def test_summary_reports_a_lower_bound_and_rejects_ambiguous_doubleheaders():
    key = "2026-08-02|braves|nationals"
    cases = [{"case_id": "CV-1", "match_key": key}]
    one = summarize_evidence(
        cases, [classify_game(game("Preview", "Postponed"), "2026-08-02")])
    assert one["schedule_coverage"] == 1.0
    assert one["observed_exception_rate_lower_bound"] == 1.0
    assert one["observed_risk_dimensions"] == {"postponement": 1}
    assert "Lower bound only" in one["interpretation"]

    ambiguous = summarize_evidence(cases, [
        classify_game(game(game_pk=1), "2026-08-02"),
        classify_game(game(game_pk=2), "2026-08-02"),
    ])
    assert ambiguous["cases_matched_to_schedule"] == 0
    assert ambiguous["ambiguous_schedule_keys"] == [key]
    assert ambiguous["by_case"][0]["evidence"] is None
