from datetime import datetime, timedelta, timezone

import pytest

from inplay_research import mlb_wp_calibration as cal


UTC = timezone.utc
TICKER_HOME = "KXMLBGAME-26AUG081840BALBOS-BOS"
TICKER_AWAY = "KXMLBGAME-26AUG081840BALBOS-BAL"


def _identity(ticker=TICKER_HOME):
    parsed = cal.parse_ticker(ticker)
    assert parsed is not None
    return parsed


def _tape(points):
    return cal.EventTape(_identity(), points)


def test_ticker_orientation_uses_target_team_not_assumed_home():
    home = _identity(TICKER_HOME)
    away = _identity(TICKER_AWAY)

    assert (home.away_code, home.home_code, home.target_code) == (
        "BAL", "BOS", "BOS")
    assert cal.orient_home_mid(home, 0.61) == pytest.approx(0.61)
    assert cal.orient_home_mid(away, 0.39) == pytest.approx(0.61)
    assert cal.orient_home_mid(home, None) is None


def test_pregame_anchor_is_real_and_at_least_five_minutes_early():
    start = _identity().scheduled_start.timestamp()
    tape = _tape([
        cal.PricePoint(start - 7 * 3600, 0.51, True),
        cal.PricePoint(start - 30 * 60, 0.53, True),
        cal.PricePoint(start - 5 * 60, 0.54, False),
        cal.PricePoint(start - 4 * 60, 0.80, True),
    ])

    anchor = cal.pregame_anchor(tape)

    assert anchor is not None
    assert anchor.epoch == start - 5 * 60
    assert anchor.home_mid == pytest.approx(0.54)


def test_checkpoint_selection_prefers_home_market_and_requires_tolerance():
    start = _identity().scheduled_start.timestamp()
    target = start + cal.CHECKPOINTS_S[0]
    tape = _tape([
        cal.PricePoint(target + 1, 0.52, False),
        cal.PricePoint(target + 80, 0.55, True),
        cal.PricePoint(start + cal.CHECKPOINTS_S[1] + 91, 0.60, True),
    ])

    points = cal.checkpoint_prices(tape)

    assert len(points) == 1
    assert points[0][1].target_is_home is True
    assert points[0][1].home_mid == pytest.approx(0.55)


def test_summary_equal_weights_games_not_checkpoint_count():
    games = [
        {"delta": 0.10, "n_checkpoints": 4, "market_brier": 0.30,
         "model_brier": 0.20},
        {"delta": -0.10, "n_checkpoints": 2, "market_brier": 0.10,
         "model_brier": 0.20},
    ]

    summary = cal._summarize(games)

    assert summary["mean"] == pytest.approx(0.0)
    assert summary["market_brier"] == pytest.approx(0.20)
    assert summary["model_brier"] == pytest.approx(0.20)
    assert summary["n_checkpoints"] == 6


def test_cluster_bootstrap_is_seeded_and_resamples_games():
    values = [0.10, -0.05, 0.02]
    assert cal.cluster_bootstrap(values, reps=500) == cal.cluster_bootstrap(
        values, reps=500)
    assert cal.cluster_bootstrap(values, reps=500)["mean"] == pytest.approx(
        sum(values) / len(values))


def test_arm_is_immutable_and_manifest_hash_mismatch_fails(tmp_path):
    path = tmp_path / "manifest.json"
    armed = cal.arm(path, datetime(2026, 8, 8, tzinfo=UTC))
    assert armed["read_not_before_utc"] == "2026-08-15T00:00:00Z"
    with pytest.raises(cal.CalibrationError, match="immutable"):
        cal.arm(path, datetime(2026, 8, 9, tzinfo=UTC))

    armed["model_sha256"] = "wrong"
    with pytest.raises(cal.CalibrationError, match="model_sha256"):
        cal.verify_manifest(armed)


def test_evaluate_refuses_before_read_boundary_without_reading_data(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest = cal.arm(manifest_path, datetime(2026, 8, 8, tzinfo=UTC))

    with pytest.raises(cal.CalibrationError, match="still blind"):
        cal.evaluate(
            manifest, tmp_path / "missing", tmp_path / "result.json",
            now=datetime(2026, 8, 14, 23, 59, tzinfo=UTC),
        )


def test_status_is_explicitly_outcome_free(tmp_path):
    manifest = cal.arm(
        tmp_path / "manifest.json", datetime(2026, 8, 8, tzinfo=UTC))

    status = cal.status_payload(manifest, tmp_path / "missing")

    assert status["outcomes_read"] is False
    assert status["events_book_eligible"] == 0


@pytest.mark.parametrize(
    ("primary", "robust", "look", "expected"),
    [
        ((0.02, 0.01), (0.01, 0.001), 60, "PASS"),
        ((0.02, -0.01), (0.01, -0.01), 60, "NO_DECISION"),
        ((0.0, -0.01), (0.01, -0.01), 60, "KILL"),
        ((0.02, -0.01), (0.01, -0.01), 120, "KILL"),
    ],
)
def test_verdict_rule(primary, robust, look, expected):
    summaries = {
        "30": {"mean": primary[0], "lo": primary[1]},
        "60": {"mean": robust[0], "lo": robust[1]},
    }
    assert cal.verdict_for(summaries, look) == expected


def test_play_snapshot_advances_after_third_out():
    play = {
        "about": {"endTime": "2026-08-08T23:10:00Z", "inning": 3,
                  "halfInning": "bottom"},
        "result": {"homeScore": 2, "awayScore": 1},
        "count": {"outs": 3},
        "matchup": {"postOnFirst": {"id": 1}},
    }

    parsed = cal._play_snapshot(play)

    assert parsed is not None
    _, state = parsed
    assert state.inning == 4
    assert state.is_top is True
    assert state.outs == 0
    assert not state.on_first
