from pathlib import Path

from manager import checks
from src.crossvenue_research import evaluate_pair_signals, load_venue_pipeline


def _metrics(**analytics):
    return {
        "status": "healthy", "terms_equivalence": "not_equivalent",
        "generated_at": "2026-08-02T12:10:00Z",
        "terms_policy": {"registry_status": "healthy"},
        "analytics": {
            "priced_path_observations": 100,
            "quote_completeness": 1.0,
            "qualifying_path_observations": 0,
            "episodes": {"persistent_count": 0},
            **analytics,
        },
    }


def test_project_venue_pipeline_explains_expansion_state():
    pipeline = load_venue_pipeline(Path("config/research_venues.yaml"))

    assert pipeline["status"] == "healthy"
    assert pipeline["summary"] == {
        "tracked": 3, "automatable": 1, "collecting": 0,
        "blocked": 1, "excluded": 2,
    }
    rows = {row["id"]: row for row in pipeline["venues"]}
    assert rows["prophetx"]["collection_state"] == "blocked_api_credentials"
    assert rows["prophetx"]["adapter"] == "scripts.collect_crossvenue_basis"
    assert rows["novig"]["automated_collection_allowed"] is False
    assert rows["underdog_predict"]["execution_allowed"] is False


def test_healthy_pair_without_dislocation_has_no_signal():
    assert evaluate_pair_signals("pair", _metrics()) == []


def test_persistent_dislocation_is_research_only_warning():
    signals = evaluate_pair_signals("pair", _metrics(
        qualifying_path_observations=4,
        episodes={"persistent_count": 2, "recent": [{
            "persistent": True, "last_seen": "2026-08-02T12:05:00Z",
        }, {
            "persistent": True, "last_seen": "2026-08-02T12:06:00Z",
        }]},
    ))

    signal = next(row for row in signals
                  if row["kind"] == "research_opportunity")
    assert signal["severity"] == "warn"
    assert signal["trade_allowed"] is False
    assert "not_equivalent" in signal["detail"]


def test_old_persistent_episode_does_not_keep_alerting():
    signals = evaluate_pair_signals("pair", _metrics(
        qualifying_path_observations=4,
        episodes={"persistent_count": 1, "recent": [{
            "persistent": True, "last_seen": "2026-08-02T11:00:00Z",
        }]},
    ))

    assert not any(row["kind"] == "research_opportunity" for row in signals)


def test_data_and_registry_health_signals_are_pair_neutral():
    metrics = _metrics(quote_completeness=.75)
    metrics["status"] = "degraded"
    metrics["terms_policy"]["registry_status"] = "changed"

    kinds = {row["kind"] for row in evaluate_pair_signals("px_kalshi", metrics)}

    assert kinds == {"data_health", "settlement_health", "data_quality"}


def test_manager_surfaces_research_signal_without_execution_implication():
    snap = {"research_operations": {"crossvenue_pilot": {
        "research_signals": [{
            "id": "pair.persistent_dislocation", "severity": "warn",
            "kind": "research_opportunity", "title": "Investigate basis",
            "detail": "Two episodes survived.", "trade_allowed": False,
        }],
    }}}

    findings = checks.check_crossvenue_research(snap)

    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "not execution approval" in findings[0].detail
