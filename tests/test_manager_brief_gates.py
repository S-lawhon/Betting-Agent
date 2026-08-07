"""A CLOSED gate must render as resolved, never as a progress bar.

The bug these cover: P-016 was killed by its own pre-registered gate on
2026-07-21, but `collect.maker_gate` keyed the card on "maker_fills.jsonl
exists" — and the fill log outlives the verdict on purpose (nothing gets
deleted). The frozen final sample rendered on the dashboard as
"fills toward gate [####] 691/500 (100%)" for a pod that is retired, which is
the checker defect from 2026-07-28 (`0_closed_gate_is_substantiated`)
reproduced one layer up, in the renderer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

MANAGER = Path(__file__).resolve().parent.parent / "manager"
sys.path.insert(0, str(MANAGER))

collect = pytest.importorskip("collect")
brief = pytest.importorskip("brief")


# --------------------------------------------------------------------------
# collector: the snapshot must carry the closure
# --------------------------------------------------------------------------

def write_registry(tmp_path: Path, root: Path, gate_extra: str) -> Path:
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "meta:\n"
        "  version: test\n"
        "  project_root: {}\n"
        "services: []\n"
        "jobs: []\n"
        "workstreams:\n"
        "  - id: P-016\n"
        "    name: Live Maker\n"
        "    gate:\n"
        "      threshold: 500\n"
        "      exclude_before: \"2026-07-20T01:26:00Z\"\n"
        "{}".format(root, gate_extra),
        encoding="utf-8",
    )
    return reg


def write_fills(root: Path) -> None:
    log = root / "data" / "trade_logs"
    log.mkdir(parents=True)
    rows = [
        # Before the contamination boundary — must not count.
        {"type": "FILL", "iso": "2026-07-19T00:00:00Z", "side": "YES"},
        # Clean fills.
        {"type": "FILL", "iso": "2026-07-21T00:00:00Z", "side": "YES"},
        {"type": "FILL", "iso": "2026-07-21T01:00:00Z", "side": "NO"},
        # Shadow fills measure guardrail cost — never gate progress.
        {"type": "FILL", "iso": "2026-07-21T02:00:00Z", "shadow": True},
        {"type": "MARKOUT", "iso": "2026-07-21T03:00:00Z",
         "horizon_s": 300, "markout_per_contract": -0.0137},
    ]
    (log / "maker_fills.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_collector_surfaces_a_closed_gate(tmp_path):
    root = tmp_path / "proj"
    write_fills(root)
    reg = write_registry(
        tmp_path, root,
        "      status: CLOSED\n"
        "      resolved_on: 2026-07-21\n"
        "      verdict: KILL\n")
    col = collect.Collector(reg, root=root)
    m = col.maker_gate()

    assert m["gate_status"] == "CLOSED"
    # YAML parses the bare date as datetime.date; the snapshot is JSON.
    assert m["resolved_on"] == "2026-07-21"
    assert m["gate_verdict"] == "KILL"
    # The frozen evidence is still counted — it feeds the final-sample note.
    assert m["fills_clean"] == 2
    assert m["shadow_fills"] == 1


def test_collector_leaves_a_live_gate_alone(tmp_path):
    root = tmp_path / "proj"
    write_fills(root)
    reg = write_registry(tmp_path, root, "")
    m = collect.Collector(reg, root=root).maker_gate()

    assert "gate_status" not in m
    assert m["fills_clean"] == 2


# --------------------------------------------------------------------------
# brief: closed renders as a verdict, live renders as a bar
# --------------------------------------------------------------------------

def closed_snap() -> dict:
    return {"maker": {
        "id": "P-016", "available": True, "gate_status": "CLOSED",
        "resolved_on": "2026-07-21", "gate_verdict": "KILL",
        "fills_clean": 691, "threshold": 500,
        "markout_mean": -0.0137, "markout_mean_ex_best_day": -0.0278,
    }}


def live_snap() -> dict:
    return {"maker": {
        "id": "P-016", "available": True,
        "fills_clean": 282, "threshold": 500,
        "markout_mean": -0.0132, "markout_mean_ex_best_day": -0.02,
    }}


def test_closed_gate_card_is_resolved():
    g = brief.build(closed_snap(), [])["gates"][0]
    assert g["resolved"] is True
    assert g["label"] == "Live Maker — gate CLOSED"
    assert g["verdict"] == "KILL"
    assert g["note"].startswith("final sample:")


def test_closed_gate_renders_verdict_not_progress():
    b = brief.build(closed_snap(), [])
    for text in (brief.render_markdown(b), brief.render_html(b)):
        assert "CLOSED 2026-07-21 — verdict KILL" in text
        assert "691/500 final sample" in text
        assert "toward gate" not in text
        assert "#" * 5 not in text, "no progress bar for a resolved gate"


def test_live_gate_still_renders_a_bar():
    b = brief.build(live_snap(), [])
    g = b["gates"][0]
    assert g["resolved"] is False
    md = brief.render_markdown(b)
    assert "fills toward gate" in md
    assert "282/500" in md
    assert "CLOSED" not in md


def test_p029_blind_checkpoint_renders_zero_progress():
    snap = {
        "p029": {"checkpoint": {
            "progress": None,
            "threshold": 500,
            "verdict": "NO DECISION",
            "reason": "reader remains blind until 2026-08-23",
        }},
    }

    b = brief.build(snap, [])
    assert b["gates"][0]["current"] == 0
    for text in (brief.render_markdown(b), brief.render_html(b)):
        assert "0/500" in text
        assert "None/500" not in text


def research_snap() -> dict:
    return {"research_operations": {
        "available": True,
        "funnel": {"assignments": 200, "dispatched": 10,
                   "dispatched_reviewed": 2, "dispatched_advanced": 1},
        "operations": {
            "semantics": {"agent_invocation_tracked": False,
                          "started_tracking_available": True},
            "window_hours": 24, "overdue_after_hours": 48,
            "activity_24h": {"dispatched": 10, "started": 3, "reviewed": 2,
                             "advance": 1, "reject": 1, "defer": 0,
                             "research_minutes": 35},
            "queue": {"pending": 8, "in_progress": 2, "overdue": 1,
                      "oldest_pending_age_hours": 52.0},
            "agents": {"strategy-scout": {
                "pending": 8, "overdue": 1,
                "in_progress": 2, "started_24h": 3,
                "oldest_pending_age_hours": 52.0,
                "reviewed_24h": 2, "advance_24h": 1,
                "research_minutes_24h": 35,
            }},
        },
        "x_pilot": {"month": "2026-08", "estimated_cost_usd": 1.065,
                    "assignments": 10, "reviewed": 2, "advanced": 1},
        "crossvenue_pilot": {
            "status": "healthy", "generated_at": "2026-08-02T12:00:00Z",
            "terms_equivalence": "unverified",
            "latest": {"matched_events": 3,
                       "hypothetical_positive_paths": 1,
                       "actionable_paths": 0},
            "last_24h": {"snapshots": 288, "matched_events": 24},
            "analytics": {
                "quote_completeness": .875,
                "qualifying_path_observations": 5,
                "episodes": {"persistent_count": 2,
                             "median_duration_seconds": 300},
                "net_edge_usd": {"p95": .041},
            },
            "research_signals": [],
            "venue_pipeline": {
                "summary": {"collecting": 0, "blocked": 1, "excluded": 2},
                "venues": [{
                    "id": "prophetx",
                    "collection_state": "blocked_production_credentials",
                    "validation": {
                        "sandbox": {"status": "technical_ready",
                                    "technical_ready": True,
                                    "ready_sports": ["mlb"],
                                    "blocked_sports": ["tennis"],
                                    "counts": {"quote_coverage_by_sport": {
                                        "mlb": {"coverage": 1.0,
                                                "executable_rows": 16,
                                                "eligible_rows": 16,
                                                "missing_prophetx_ask": 0},
                                        "tennis": {"coverage": 0.0,
                                                   "executable_rows": 0,
                                                   "eligible_rows": 12,
                                                   "missing_prophetx_ask": 12},
                                    }}},
                        "production": {"status": "blocked",
                                       "rollout_ready": False,
                                       "blockers": ["tax_gate0"]},
                    },
                }],
            },
            "research_cases": {
                "lifetime_cases": 4, "cases_last_24h": 1,
                "cases_last_7d": 3, "active_cases": 1,
                "single_snapshot_share": .5,
                "market_phase": {
                    "pre_scheduled_start": 1,
                    "after_scheduled_start": 2, "unknown": 1,
                },
                "settlement_basis": {
                    "risk_dimensions": ["postponement", "shortened_game"],
                },
                "outcome_evidence": {
                    "schedule_coverage": .75,
                    "resolved_schedule_cases": 2,
                    "observed_exception_cases": 1,
                    "observed_exception_rate_lower_bound": .5,
                },
                "top_last_24h": [{
                    "case_id": "CV-example", "max_net_edge_usd": .04,
                    "duration_seconds_lower_bound": 300,
                    "observations": 2, "status": "active",
                    "market_phase": "pre_scheduled_start",
                    "schedule_alignment_status": "aligned",
                }],
            },
        },
        "collector_health": {
            "status": "degraded", "academic_feed_items_raw": 0,
            "zero_academic_feeds": ["feed:arxiv_qfin_trading"],
            "expected_empty_academic_feeds": [],
            "collector_error_count": 1,
        },
        "quality_control": {
            "intake_rejected": 4, "triage_blocked": 2,
            "legacy_dispatches_quarantined": 1,
        },
    }}


def test_daily_brief_renders_research_operations_without_implying_agent_start():
    b = brief.build(research_snap(), [])
    for text in (brief.render_markdown(b), brief.render_html(b)):
        assert "Research operations" in text
        assert "200 assignments" in text
        assert "10 dispatched" in text
        assert "8 pending" in text
        assert "Gemini/Kalshi research: healthy" in text
        assert "ProphetX readiness: sandbox technical_ready" in text
        assert "production blocked; rollout blocked" in text
        assert "Blockers: tax_gate0" in text
        assert "Sports ready: mlb; blocked: tennis" in text
        assert "mlb 100.0% (16/16 executable; 0 missing PX asks)" in text
        assert "tennis 0.0% (0/12 executable; 12 missing PX asks)" in text
        assert "settlement terms unverified" in text
        assert "87.5% quote completeness" in text
        assert "2 persistent episodes" in text
        assert "0 research alerts" in text
        assert "0 collecting, 1 blocked, 2 excluded" in text
        assert "Case ledger: 4 lifetime, 1 in 24h, 3 in 7d, 1 active" in text
        assert "single-snapshot share 50.0%" in text
        assert "postponement, shortened_game" in text
        assert "Top 24h case: CV-example" in text
        assert "phase pre/after/unknown 1/2/1" in text
        assert "phase pre_scheduled_start, schedule aligned" in text
        assert "Outcome evidence: 75.0% schedule coverage, 2 resolved" in text
        assert "observed settlement exception lower bound 50.0% (1 cases)" in text
        assert "strategy-scout" in text
        assert "worker claims/start state are tracked" in text.lower()
        assert "model invocation is not tracked" in text.lower()
        assert "3 started" in text
        assert "2 in progress" in text
        assert "35 research minutes" in text
        assert "$1.065" in text
        assert "Source health: degraded" in text
        assert "arxiv_qfin_trading" in text
        assert "4 rejected at intake" in text
        assert "1 legacy packets quarantined" in text


def test_daily_brief_surfaces_dry_run_worker_and_zero_model_spend():
    snap = research_snap()
    operations = snap["research_operations"]["operations"]
    operations["semantics"]["agent_invocation_tracked"] = True
    operations["activity_24h"]["invoked"] = 0
    operations["worker"] = {
        "mode": "dry_run", "status": "dry_run",
        "provider_configured": False, "assignment_id": "a-next",
        "daily_usage": {"attempts": 0, "cost_usd": 0,
                        "hard_cost_limit_usd": 10},
    }

    b = brief.build(snap, [])
    for text in (brief.render_markdown(b), brief.render_html(b)):
        assert "model invocation is tracked" in text.lower()
        assert "dry_run mode, status dry_run" in text
        assert "provider not configured" in text
        assert "next a-next" in text
        assert "0 model-invoked" in text
        assert "$0 of $10 hard limit" in text


def test_daily_brief_does_not_confuse_default_dry_run_with_provider_pilot():
    snap = research_snap()
    operations = snap["research_operations"]["operations"]
    operations["activity_24h"].update({"invoked": 1, "reviewed": 1})
    operations["worker"] = {
        "mode": "dry_run", "status": "dry_run",
        "provider_configured": False, "assignment_id": "next",
        "daily_usage": {"attempts": 0, "cost_usd": 0,
                        "hard_cost_limit_usd": 10},
    }

    b = brief.build(snap, [])
    for text in (brief.render_markdown(b), brief.render_html(b)):
        assert "Default research planner" in text
        assert "Provider-backed pilot" in text
        assert "1 tracked model invocation" in text
        assert "1 durable review" in text


def test_daily_brief_reports_missing_research_operations_as_unknown():
    b = brief.build({"research_operations": {
        "available": False, "reason": "metrics missing",
    }}, [])
    md = brief.render_markdown(b)
    assert "Research operations unavailable" in md
    assert "metrics missing" in md


def test_daily_brief_labels_subscription_usage_without_implying_api_spend():
    snap = research_snap()
    worker = snap["research_operations"]["operations"]["worker"] = {
        "mode": "execute", "status": "completed",
        "provider_configured": True,
        "billing_mode": "chatgpt_subscription", "assignment_id": "a1",
        "daily_usage": {"attempts": 1, "hard_attempt_limit": 1,
                        "input_tokens": 1200, "output_tokens": 250,
                        "cost_usd": 0},
    }
    assert worker["billing_mode"] == "chatgpt_subscription"

    b = brief.build(snap, [])
    for text in (brief.render_markdown(b), brief.render_html(b)):
        assert "configured (chatgpt_subscription)" in text
        assert "1 of 1 daily attempts" in text
        assert "$0 incremental API cost" in text
