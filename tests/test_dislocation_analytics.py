from datetime import datetime, timezone

from src.dislocation_analytics import analyze


UTC = timezone.utc


def _row(captured_at, edge, *, match_key="game", direction="left"):
    path = {
        "gemini_yes_team": direction,
        "kalshi_yes_team": "other",
        "gross_edge_usd": edge + .01 if edge is not None else None,
        "net_edge_usd": edge,
    }
    return {
        "captured_at": captured_at, "collection_id": captured_at,
        "match_key": match_key, "quote_skew_seconds": 1.5,
        "paths": [path],
    }


def test_quote_completeness_and_edge_distribution():
    rows = [
        _row("2026-08-02T12:00:00Z", .04),
        _row("2026-08-02T12:05:00Z", None, direction="right"),
    ]

    result = analyze(rows)

    assert result["path_observations"] == 2
    assert result["priced_path_observations"] == 1
    assert result["quote_completeness"] == .5
    assert result["qualifying_path_observations"] == 1
    assert result["net_edge_usd"]["p95"] == .04


def test_consecutive_qualifying_quotes_form_persistent_episode():
    rows = [
        _row("2026-08-02T12:00:00Z", .04),
        _row("2026-08-02T12:05:00Z", .05),
    ]

    episodes = analyze(rows)["episodes"]

    assert episodes["count"] == 1
    assert episodes["persistent_count"] == 1
    assert episodes["median_duration_seconds"] == 300


def test_large_gap_splits_episodes_and_singletons_are_not_persistent():
    rows = [
        _row("2026-08-02T12:00:00Z", .04),
        _row("2026-08-02T12:10:00Z", .05),
    ]

    episodes = analyze(rows, max_episode_gap_seconds=450)["episodes"]

    assert episodes["count"] == 2
    assert episodes["persistent_count"] == 0
    assert episodes["longest_duration_seconds"] == 0


def test_slippage_scenarios_reduce_positive_paths():
    result = analyze([_row("2026-08-02T12:00:00Z", .03)])
    scenarios = {row["per_leg_slippage_usd"]: row
                 for row in result["slippage_scenarios"]}

    assert scenarios[0.0]["positive_paths"] == 1
    assert scenarios[0.02]["positive_paths"] == 0
    assert result["depth_status"] == "unavailable"
