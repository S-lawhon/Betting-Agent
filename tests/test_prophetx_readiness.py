import json
from datetime import datetime, timezone

from scripts.validate_prophetx_readiness import validate
from src.crossvenue_research import load_venue_pipeline
from src.prophetx_readiness import build_readiness_report


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _report(environment="sandbox", **overrides):
    values = {
        "environment": environment,
        "probe": {"has_credentials": True, "authenticated": True},
        "event_count": 10, "moneyline_count": 4,
        "pairs": [{"game_id": "g1"}], "unmatched_reasons": {},
        "snapshot_rows": [{
            "k_yes_ask": .49, "px_other_ask_prob": .48,
            "schedule_skew_seconds": 60, "px_environment": environment,
        }],
        "generated_at": NOW,
    }
    values.update(overrides)
    return build_readiness_report(**values)


def test_sandbox_can_be_technically_ready_but_never_rollout_ready():
    report = _report(tax_gate0_resolved=True,
                     production_collection_approved=True)

    assert report["status"] == "technical_ready"
    assert report["technical_ready"] is True
    assert report["rollout_ready"] is False
    assert report["blockers"] == ["production_environment"]
    assert report["safety"] == {
        "execution_enabled": False,
        "collector_enabled_by_validation": False,
        "contains_secrets": False,
    }


def test_production_report_fails_closed_on_policy_gates():
    report = _report(environment="production")

    assert report["technical_ready"] is True
    assert report["rollout_ready"] is False
    assert report["blockers"] == ["tax_gate0", "production_collection_approval"]


def test_bad_schedule_or_quote_coverage_blocks_technical_readiness():
    rows = [
        {"k_yes_ask": .49, "px_other_ask_prob": None,
         "schedule_skew_seconds": 901, "px_environment": "production"},
        {"k_yes_ask": .49, "px_other_ask_prob": .48,
         "schedule_skew_seconds": 60, "px_environment": "production"},
    ]
    report = _report(environment="production", snapshot_rows=rows)

    assert report["technical_ready"] is False
    assert "schedule_safe_matches" in report["blockers"]
    assert "executable_quote_coverage" in report["blockers"]


def test_post_start_rows_are_reported_but_not_quote_coverage_denominator():
    rows = [{
        "k_yes_ask": .49, "px_other_ask_prob": None,
        "schedule_skew_seconds": 60, "px_environment": "sandbox",
        "kalshi_scheduled_start_at": "2026-08-02T23:00:00Z",
    }, {
        "k_yes_ask": .49, "px_other_ask_prob": .48,
        "schedule_skew_seconds": 60, "px_environment": "sandbox",
        "kalshi_scheduled_start_at": "2026-08-03T01:00:00Z",
    }]
    report = _report(snapshot_rows=rows)

    assert report["technical_ready"] is True
    assert report["counts"]["post_start_rows_excluded"] == 1
    assert report["counts"]["eligible_quote_rows"] == 1
    assert report["counts"]["executable_quote_coverage"] == 1.0


def test_live_runner_contract_does_not_write_observations(monkeypatch):
    class Px:
        def probe(self):
            return {"has_credentials": True, "authenticated": True}

        def sport_events(self):
            return [{"event_id": "e1"}]

    monkeypatch.setattr("scripts.validate_prophetx_readiness.prophetx_moneylines",
                        lambda px: [{"event_id": "e1"}])
    monkeypatch.setattr("scripts.validate_prophetx_readiness.kalshi_games",
                        lambda kp: {"g1": {}})
    monkeypatch.setattr("scripts.validate_prophetx_readiness.match_pairs",
                        lambda games, markets: ([{"game_id": "g1"}], {}))
    monkeypatch.setattr("scripts.validate_prophetx_readiness.snapshot",
                        lambda kp, px, pair, tax: [{
                            "k_yes_ask": .49, "px_other_ask_prob": .48,
                            "schedule_skew_seconds": 0,
                            "px_environment": pair["px_environment"],
                        }])

    report = validate(environment="sandbox", client=Px(), kalshi=object())

    assert report["technical_ready"] is True
    assert report["counts"]["snapshot_rows"] == 1


def test_venue_pipeline_embeds_only_safe_readiness_summary(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = config_dir / "research_venues.yaml"
    config.write_text("""venues:
  - id: prophetx
    name: ProphetX
    research_allowed: true
    execution_allowed: false
    automated_collection_allowed: true
    collection_state: blocked_production_credentials
""")
    report_dir = tmp_path / "data" / "prophetx_readiness"
    report_dir.mkdir(parents=True)
    report_dir.joinpath("sandbox.json").write_text(json.dumps(_report()))

    pipeline = load_venue_pipeline(config, venue_ids=("prophetx",))
    validation = pipeline["venues"][0]["validation"]["sandbox"]

    assert validation["status"] == "technical_ready"
    assert validation["technical_ready"] is True
    assert "checks" not in validation
    assert "probe" not in validation
