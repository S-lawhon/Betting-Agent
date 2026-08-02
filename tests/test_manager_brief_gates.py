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


def research_snap() -> dict:
    return {"research_operations": {
        "available": True,
        "funnel": {"assignments": 200, "dispatched": 10,
                   "dispatched_reviewed": 2, "dispatched_advanced": 1},
        "operations": {
            "semantics": {"agent_invocation_tracked": False},
            "window_hours": 24, "overdue_after_hours": 48,
            "activity_24h": {"dispatched": 10, "reviewed": 2,
                             "advance": 1, "reject": 1, "defer": 0,
                             "research_minutes": 35},
            "queue": {"pending": 8, "overdue": 1,
                      "oldest_pending_age_hours": 52.0},
            "agents": {"strategy-scout": {
                "pending": 8, "overdue": 1,
                "oldest_pending_age_hours": 52.0,
                "reviewed_24h": 2, "advance_24h": 1,
                "research_minutes_24h": 35,
            }},
        },
        "x_pilot": {"month": "2026-08", "estimated_cost_usd": 1.065,
                    "assignments": 10, "reviewed": 2, "advanced": 1},
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
        assert "strategy-scout" in text
        assert "agent invocation/started state is not tracked" in text.lower()
        assert "35 research minutes" in text
        assert "$1.065" in text
        assert "Source health: degraded" in text
        assert "arxiv_qfin_trading" in text
        assert "4 rejected at intake" in text
        assert "1 legacy packets quarantined" in text


def test_daily_brief_reports_missing_research_operations_as_unknown():
    b = brief.build({"research_operations": {
        "available": False, "reason": "metrics missing",
    }}, [])
    md = brief.render_markdown(b)
    assert "Research operations unavailable" in md
    assert "metrics missing" in md
