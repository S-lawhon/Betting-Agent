import json

from scripts.run_gemini_crossvenue_research import collect


class _Gemini:
    def active_prediction_events(self):
        return [{
            "eventId": "gem-1", "title": "Pittsburgh at Cincinnati",
            "startTime": "2026-08-02T17:40:00-04:00",
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
