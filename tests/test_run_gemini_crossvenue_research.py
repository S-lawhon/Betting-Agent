import json
from datetime import datetime, timezone

import scripts.run_gemini_crossvenue_research as collector
from scripts.run_gemini_crossvenue_research import collect


UTC = timezone.utc


class _Gemini:
    def active_prediction_events(self):
        return [{
            "eventId": "gem-1", "title": "Pittsburgh at Cincinnati",
            "startTime": "2026-08-02T13:40:00-04:00",
            "sportsMarket": {"sport": "baseball", "type": "moneyline"},
            "contracts": [
                {"contractId": "pit", "title": "Pirates", "bid": .05, "ask": .06},
                {"contractId": "cin", "title": "Reds", "bid": .94, "ask": .95},
            ],
        }]


class _Kalshi:
    def open_events(self, series):
        assert series == "KXMLBGAME"
        ticker = "KXMLBGAME-26AUG021340PITCIN"
        return [{
            "event_ticker": ticker, "title": "Pittsburgh at Cincinnati Winner?",
            "markets": [
                {"ticker": ticker + "-PIT", "yes_sub_title": "Pittsburgh",
                 "yes_bid_dollars": ".05", "yes_ask_dollars": ".06"},
                {"ticker": ticker + "-CIN", "yes_sub_title": "Cincinnati",
                 "yes_bid_dollars": ".94", "yes_ask_dollars": ".95"},
            ],
        }]


def test_collect_persists_snapshot_observation_and_metrics(tmp_path):
    result = collect(_Gemini(), _Kalshi(), tmp_path)

    assert result["status"] == "healthy"
    assert result["matching"]["matched"] == 1
    latest = json.loads((tmp_path / "latest.json").read_text())
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    analytics = json.loads((tmp_path / "analytics.json").read_text())
    cases = json.loads((tmp_path / "research_cases.json").read_text())
    observations = list((tmp_path / "observations").glob("*.jsonl"))
    runs = list((tmp_path / "runs").glob("*.jsonl"))
    assert latest["mode"] == "read_only_research"
    assert metrics["terms_equivalence"] == "unverified"
    assert metrics["terms_policy"]["registry_status"] == "missing"
    assert metrics["latest"]["actionable_paths"] == 0
    assert len(observations) == 1
    assert len(observations[0].read_text().splitlines()) == 1
    assert len(runs) == 1
    assert metrics["last_24h"]["snapshots"] == 1
    assert metrics["last_24h"]["healthy_snapshots"] == 1
    assert analytics["path_observations"] == 2
    assert metrics["analytics"]["depth_status"] == "unavailable"
    assert [row["kind"] for row in metrics["research_signals"]] == [
        "settlement_health"]
    assert metrics["venue_pipeline"]["summary"]["blocked"] == 1
    assert metrics["venue_pipeline"]["summary"]["excluded"] == 2
    assert (tmp_path / "research_cases.sqlite3").exists()
    assert cases["lifetime_cases"] == 0
    assert cases["settlement_basis"]["status"] == "unverified"
    assert cases["outcome_evidence"]["status"] == "unavailable"
    assert len(cases["threshold_calibration"]) == 5
    assert metrics["research_cases"] == cases


def test_fast_collection_reuses_analysis_without_replaying_observations(
        tmp_path, monkeypatch):
    cached_analytics = {
        "generated_at": "2026-08-02T00:00:00Z",
        "priced_path_observations": 10,
        "quote_completeness": 1.0,
    }
    cached_cases = {
        "generated_at": "2026-08-02T00:00:00Z", "lifetime_cases": 2,
    }
    (tmp_path / "analytics.json").write_text(json.dumps(cached_analytics))
    (tmp_path / "research_cases.json").write_text(json.dumps(cached_cases))

    def fail_if_replayed(*args, **kwargs):
        raise AssertionError("the five-minute path must not replay observations")

    monkeypatch.setattr(collector, "iter_observations", fail_if_replayed)
    result = collect(
        _Gemini(), _Kalshi(), tmp_path, refresh_analysis=False)

    assert result["status"] == "healthy"
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["analytics"] == cached_analytics
    assert metrics["research_cases"] == cached_cases
    assert metrics["analysis_refresh"] == {
        "mode": "cached",
        "analytics_generated_at": "2026-08-02T00:00:00Z",
        "cases_generated_at": "2026-08-02T00:00:00Z",
    }
    assert metrics["last_24h"]["matched_events"] == 1
    assert metrics["last_24h"]["runs_with_matches"] == 1
    assert metrics["last_24h"]["best_net_edge_usd"] == -0.03


def test_legacy_run_rows_use_bounded_observation_history_fallback(tmp_path):
    captured = "2026-08-08T12:00:00Z"
    runs = tmp_path / "runs"
    observations = tmp_path / "observations"
    runs.mkdir()
    observations.mkdir()
    (runs / "2026-08-08.jsonl").write_text(json.dumps({
        "captured_at": captured, "status": "healthy", "matched_events": 1,
        "hypothetical_positive_paths": 1, "actionable_paths": 0,
    }) + "\n")
    (observations / "2026-08-08.jsonl").write_text(json.dumps({
        "captured_at": captured, "collection_id": "legacy",
        "hypothetical_positive_paths": 1, "actionable_paths": 0,
        "best_net_edge_usd": .22,
    }) + "\n")

    metrics = collector._history_24h(
        tmp_path, datetime(2026, 8, 8, 12, 5, tzinfo=UTC))

    assert metrics["snapshots"] == 1
    assert metrics["matched_events"] == 1
    assert metrics["runs_with_matches"] == 1
    assert metrics["best_net_edge_usd"] == .22
    assert "edge_history_complete" not in metrics
