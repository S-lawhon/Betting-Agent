"""
tests/test_dashboard_api_v2.py
──────────────────────────────
Tests for ``src/dashboard_api.build_v2`` — a pure function of dicts.

No filesystem, no clock, no HTTP. That is the point: every degradation path
(source missing, field null, older collector schema) is a plain unit test.

The theme of this file is **null is not zero**. ``docs/GATE_INSTRUMENTATION_
STANDARD.md`` §3 exists because four of five gates spent 28 days measuring
nothing while looking fine. A progress of ``0`` from a working reader and a
progress of ``None`` from a reader that could not run demand opposite responses —
wait for observations, versus go fix the instrumentation — so the payload must
never conflate them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import dashboard_api as api  # noqa: E402


# ── source builders ───────────────────────────────────────────────────


def meta(available=True, path="p", age=10.0, stale=False, reason=None, **kw):
    m = {"available": available, "path": path, "age_seconds": age,
         "stale": stale, "reason": reason, "mtime_utc": None}
    m.update(kw)
    return m


def sources(**over):
    base = {name: meta(available=False, path="/x/" + name,
                       age=None, stale=True, reason="file does not exist")
            for name in ("engine_state", "open_positions", "manager_status",
                        "rollup", "p022_window", "research_metrics",
                        "crossvenue_metrics", "clv")}
    base.update(over)
    return base


def status(**over):
    s = {
        "collected_at": "2026-07-30T11:45:00+00:00",
        "host": "droplet",
        "invariants": {"all_paper": True, "pod_modes": {"P-001": "paper"},
                       "non_paper_pods": []},
        "services": [{"id": "betting-pod-shop", "active": "active",
                      "since": "2026-07-28T09:11:02Z", "restarts": 0,
                      "severity": "critical", "heartbeat": {"age_minutes": 2.1}}],
        "jobs": [],
        "faults": [],
        "errors": {},
        "workstreams": [],
        "throughput": {"available": True, "n_gates": 1, "n_stalled": 0,
                       "n_zero_28d": 1, "n_unprojectable": 1, "records": []},
    }
    s.update(over)
    return s


def src(**over):
    base = {
        "root": "/opt/betting-pod-shop",
        "generated_at_utc": "2026-07-30T12:00:00+00:00",
        "engine_state": None, "open_positions": None, "manager_status": None,
        "rollup": None, "p022_window": None, "kill_switches": [],
        "research_metrics": None, "crossvenue_metrics": None,
        "sources": sources(),
    }
    base.update(over)
    return base


def ws(pod="P-022", **over):
    w = {"id": pod, "name": "Round-Leader Dead-Heat Fade", "stage": "paper",
         "blocked_on": "time", "summary": "s",
         "gate": {"metric": "settled_tournaments", "threshold": 24,
                  "reader": "scripts/p022_checkpoint.py"}}
    w.update(over)
    return w


def rec(pod="P-022", **over):
    r = {"id": pod, "metric": "settled_tournaments", "threshold": 24,
         "progress": 0, "settled_positions_7d": 0, "settled_positions_28d": 0,
         "last_settlement_utc": None, "stall_days_tolerated": 21.0,
         "days_since_last_settlement": None, "stalled": None,
         "rate_per_week": None, "weeks_to_threshold": None,
         "projected_resolution_utc": None, "resolvable_within_12m": None}
    r.update(over)
    return r


# ── shape ─────────────────────────────────────────────────────────────


def test_empty_input_produces_a_complete_payload():
    p = api.build_v2(src())
    for key in ("schema", "generated_at_utc", "sources", "mode", "engine",
                "alarms", "gates", "gates_summary", "pnl", "pipeline", "ops"):
        assert key in p, key
    assert p["schema"] == 2
    assert p["gates"] == []


def test_build_v2_never_mutates_its_input():
    s = src()
    snapshot = repr(s)
    api.build_v2(s)
    assert repr(s) == snapshot


def test_sections_filter_trims_the_payload():
    p = api.build_v2(src(), sections=["pnl"])
    assert "pnl" in p and "gates" not in p and "pipeline" not in p
    # the ribbon and freshness chip render on every view, so these always ship
    for always in ("mode", "engine", "sources", "alarms"):
        assert always in p


def test_sections_matches_the_handlers_allowlist():
    from src.web_dashboard import _V2_SECTIONS
    assert tuple(api.SECTIONS) == _V2_SECTIONS


def test_funnel_stage_order_is_canonical_and_monotone():
    assert api.FUNNEL_STAGES[0] == "markets_listed"
    assert api.FUNNEL_STAGES[-1] == "candidates_without_quote"
    assert len(api.FUNNEL_STAGES) == 8
    f = api.build_v2(src())["pipeline"]["p022_window"]["funnel"]
    assert [s[0] for s in f] == list(api.FUNNEL_STAGES)
    assert all(s[1] is None for s in f), "absent source must be null, not 0"
    rf = api.build_v2(src())["pipeline"]["research"]["funnel"]
    assert [stage[0] for stage in rf] == list(api.RESEARCH_FUNNEL_STAGES)
    assert all(stage[1] is None for stage in rf)


# ── mode ribbon ───────────────────────────────────────────────────────


def test_mode_missing_is_unknown_not_paper():
    p = api.build_v2(src())
    assert p["mode"]["available"] is False
    assert p["mode"]["all_paper"] is None
    assert p["mode"]["label"] == "MODE UNKNOWN"
    assert p["mode"]["reason"]


def test_mode_paper():
    p = api.build_v2(src(manager_status=status(),
                        sources=sources(manager_status=meta())))
    assert p["mode"]["all_paper"] is True
    assert "PAPER" in p["mode"]["label"]


def test_mode_live_is_loud():
    st = status(invariants={"all_paper": False, "pod_modes": {"P-001": "live"},
                            "non_paper_pods": ["P-001"]})
    p = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))
    assert p["mode"]["all_paper"] is False
    assert p["mode"]["label"] == "LIVE CAPITAL"
    assert p["mode"]["non_paper_pods"] == ["P-001"]


def test_invariants_present_but_all_paper_absent_is_unknown():
    p = api.build_v2(src(manager_status=status(invariants={"pod_modes": {}}),
                        sources=sources(manager_status=meta())))
    assert p["mode"]["available"] is False


# ── engine liveness ───────────────────────────────────────────────────


def test_liveness_live():
    p = api.build_v2(src(
        engine_state={"engine_status": "running",
                      "_meta": {"interval_seconds": 300}},
        manager_status=status(),
        sources=sources(engine_state=meta(age=42.0,
                                          expected_max_age_seconds=900.0),
                        manager_status=meta())))
    assert p["engine"]["liveness"] == "live"
    assert p["engine"]["liveness_reason"] is None


def test_liveness_stale_when_snapshot_is_old():
    p = api.build_v2(src(
        engine_state={"engine_status": "running", "_meta": {"interval_seconds": 300}},
        manager_status=status(),
        sources=sources(engine_state=meta(age=4000.0, stale=True,
                                          expected_max_age_seconds=900.0),
                        manager_status=meta())))
    assert p["engine"]["liveness"] == "stale"
    assert "expected every" in p["engine"]["liveness_reason"]


def test_liveness_down_when_systemd_says_so():
    st = status(services=[{"id": "betting-pod-shop", "active": "failed",
                           "restarts": 12}])
    p = api.build_v2(src(engine_state={"engine_status": "running", "_meta": {}},
                        manager_status=st,
                        sources=sources(engine_state=meta(),
                                        manager_status=meta())))
    assert p["engine"]["liveness"] == "down"
    assert "failed" in p["engine"]["liveness_reason"]


def test_systemd_na_is_no_view_not_down():
    """collect.py records active: "n/a" off a systemd host (the Mac snapshot).

    Reading that as "stopped" reported a healthy engine as down every time the
    snapshot happened to be collected from the laptop.
    """
    st = status(services=[{"id": "betting-pod-shop", "active": "n/a"}])
    p = api.build_v2(src(
        engine_state={"engine_status": "running", "_meta": {"interval_seconds": 300}},
        manager_status=st,
        sources=sources(engine_state=meta(age=10.0), manager_status=meta())))
    assert p["engine"]["liveness"] == "live"
    assert p["engine"]["systemd"]["view_available"] is False


def test_liveness_unknown_with_no_snapshot_and_no_systemd_view():
    p = api.build_v2(src())
    assert p["engine"]["liveness"] == "unknown"
    assert "genuinely unknown, not idle" in p["engine"]["liveness_reason"]


def test_clock_skew_makes_liveness_unknown():
    p = api.build_v2(src(
        engine_state={"engine_status": "running", "_meta": {}},
        manager_status=status(),
        sources=sources(engine_state=meta(age=-9000.0, stale=True,
                                          clock_skew=True,
                                          reason="timestamp is in the FUTURE"),
                        manager_status=meta())))
    assert p["engine"]["liveness"] == "unknown"
    assert "FUTURE" in p["engine"]["liveness_reason"]


# ── gates: null is not zero ───────────────────────────────────────────


def test_null_progress_is_unmeasurable_never_zero():
    st = status(workstreams=[ws()],
                p022={"id": "P-022", "reader": "scripts/p022_checkpoint.py",
                      "available": False, "error": "ERROR: no clv log"},
                throughput={"available": True, "records": [rec(progress=None)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress"] is None
    assert g["progress_available"] is False
    assert g["progress_pct"] is None
    assert g["progress_state"] == "unmeasurable"
    assert "sanctioned reader could not run" in g["progress_reason"]


def test_measured_zero_is_no_observations_not_unmeasurable():
    """The distinction that the whole gate board exists to make."""
    st = status(workstreams=[ws()],
                throughput={"available": True, "records": [rec(progress=0)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress"] == 0
    assert g["progress_available"] is True
    assert g["progress_state"] == "no observations"


def test_historical_progress_with_a_dry_month_is_dormant_not_accumulating():
    """3 observations and nothing for 28 days is not "on its way to 24"."""
    st = status(workstreams=[ws()],
                throughput={"available": True,
                            "records": [rec(progress=3, settled_positions_28d=0,
                                            stalled=None)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["stalled"] is None
    assert g["progress_state"] == "dormant"


def test_unknown_28d_activity_is_explicitly_unknown_not_dormant():
    st = status(workstreams=[ws()],
                throughput={"available": True,
                            "records": [rec(progress=3, stalled=False,
                                            settled_positions_28d=None)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress_state"] == "activity unknown"


def test_recent_checkpoint_tournament_activity_is_accumulating():
    st = status(workstreams=[ws()],
                throughput={"available": True,
                            "records": [rec(progress=2,
                                            settled_positions_28d=2,
                                            observation_unit="tournaments",
                                            stalled=False)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress_state"] == "accumulating"
    assert g["observation_unit"] == "tournaments"


def test_stalled_true_is_stalled():
    st = status(workstreams=[ws()],
                throughput={"available": True,
                            "records": [rec(progress=3, stalled=True)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress_state"] == "stalled"


def test_progress_at_threshold_is_reached():
    st = status(workstreams=[ws()],
                throughput={"available": True,
                            "records": [rec(progress=24, stalled=False)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress_state"] == "reached"
    assert g["progress_pct"] == 100.0


def test_accumulating_requires_real_observations():
    st = status(workstreams=[ws()],
                throughput={"available": True,
                            "records": [rec(progress=6, stalled=False,
                                            settled_positions_28d=4)]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress_state"] == "accumulating"
    assert g["progress_pct"] == 25.0


@pytest.mark.parametrize("stage", ["killed", "shelved", "retired"])
def test_terminal_stages_stop_asking_to_be_measured(stage):
    st = status(workstreams=[ws(stage=stage)], throughput={"records": []})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress_state"] == "terminal"


def test_projection_null_is_not_false():
    """"we did not compute a projection" and "it is unprojectable" differ."""
    st = status(workstreams=[ws()],
                throughput={"available": True, "records": [rec()]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["projected_resolution_utc"] is None
    assert g["projection_available"] is False
    assert g["resolvable_within_12m"] is None


def test_reader_verdict_is_passed_through_verbatim():
    """Gate Standard §1: the reader's verdict is the only verdict."""
    st = status(
        workstreams=[ws()],
        p022={"id": "P-022", "reader": "scripts/p022_checkpoint.py",
              "available": True,
              "checkpoint": {"progress": 0, "threshold": 24,
                             "verdict": "NO DECISION",
                             "reason": "no settled P-022 trades yet"}},
        throughput={"available": True, "records": [rec()]})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["reader_verdict"] == "NO DECISION"
    assert g["reader_verdict_reason"] == "no settled P-022 trades yet"
    # the UI label is a different field, so it can never be mistaken for a verdict
    assert g["progress_state"] != g["reader_verdict"]


def test_progress_falls_back_to_the_checkpoint_when_throughput_lacks_it():
    st = status(
        workstreams=[ws()],
        p022={"id": "P-022", "available": True,
              "checkpoint": {"progress": 5, "threshold": 24}},
        throughput={"available": True, "records": []})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["progress"] == 5
    assert "checkpoint" in g["progress_source"]


def test_no_gate_declared_says_so():
    st = status(workstreams=[{"id": "R-X", "name": "n", "stage": "build",
                              "blocked_on": "nothing"}],
                throughput={"records": []})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert "no gate is declared" in g["progress_reason"]


def test_validating_is_derived_from_throughput_membership():
    """`tier` is in registry.yaml but collect.py does not copy it into the
    snapshot — every live workstream has tier: None. Membership in
    throughput.records is the real signal."""
    st = status(workstreams=[ws("P-022"), ws("P-006", stage="shelved")],
                throughput={"available": True, "records": [rec("P-022")]})
    gates = api.build_v2(src(manager_status=st,
                            sources=sources(manager_status=meta())))["gates"]
    by = {g["pod_id"]: g for g in gates}
    assert by["P-022"]["is_validating"] is True
    assert by["P-022"]["tier"] == "validating"
    assert by["P-006"]["is_validating"] is False
    assert gates[0]["pod_id"] == "P-022", "validating gates sort first"


def test_terminal_gates_sort_last():
    st = status(workstreams=[ws("P-006", stage="killed"), ws("P-014", stage="paper")],
                throughput={"records": []})
    gates = api.build_v2(src(manager_status=st,
                            sources=sources(manager_status=meta())))["gates"]
    assert [g["pod_id"] for g in gates] == ["P-014", "P-006"]


def test_p001_clv_caveat_travels_with_the_gate():
    st = status(workstreams=[ws("P-001")], throughput={"records": []})
    g = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))["gates"][0]
    assert g["caveat"] and "wrong-day" in g["caveat"]


def test_gates_summary_counts_unmeasurable():
    st = status(workstreams=[ws("P-001"), ws("P-014")],
                throughput={"available": True, "n_gates": 2,
                            "records": [rec("P-014", progress=0, stalled=False)]})
    p = api.build_v2(src(manager_status=st,
                        sources=sources(manager_status=meta())))
    assert p["gates_summary"]["n_unmeasurable"] == 1


# ── P&L ───────────────────────────────────────────────────────────────


ROLLUP = {
    "lifetime": {"realized_pnl": -23.48, "settled": 215, "placed": 181,
                 "skipped": 46388, "wins": 116, "losses": 90, "voids": 8,
                 "gross_wagered_usd": 48210.5,
                 "by_action": {"SKIPPED_EDGE": 44000, "PLACED": 181}},
    "daily": [["2026-07-28", 1.0, 2, 1, 100],
              ["2026-07-29", -2.0, 3, 1, 120],
              ["2026-07-30", 0.5, 1, 0, 90]],
    "by_pod": {"P-001": {"realized_pnl": -23.48, "settled": 215, "placed": 181,
                         "wins": 116, "losses": 90, "voids": 8,
                         "gross_wagered_usd": 48210.5, "skipped": 46388}},
    "weekly_by_pod": {"P-001": {"2026-W31": {"placed": 5, "skipped": 1400,
                                             "settled": 5}}},
    "skip_reasons": {"lifetime_by_pod": {"P-001": {"edge_below_threshold": 41230,
                                                   "no_sharp_price": 3810}},
                     "by_day": {"2026-07-29": {"edge_below_threshold": 120}},
                     "by_day_retention_days": 45},
    "clv": {"by_pod": {"P-001": {"n": 650, "clv_gross_sum": 903.5,
                                 "clv_net_maker_sum": 611.0, "n_settled": 611}}},
    "coverage": {"rows_total": 46784, "rows_with_skip_reason": 46388,
                 "rows_missing_pod_id": 630, "rows_unparseable": 2},
}


def test_pnl_all_null_when_the_rollup_is_absent():
    r = api.build_v2(src())["pnl"]["realized"]
    assert r["available"] is False
    for key in ("lifetime", "d30", "d7", "settled", "win_pct", "roi_pct"):
        assert r[key] is None, "%s was coerced to a value" % key
    assert r["reason"], "an absent rollup must be explained"


def test_pnl_open_is_unknown_not_zero_without_the_engine():
    o = api.build_v2(src(rollup=ROLLUP,
                         sources=sources(rollup=meta())))["pnl"]["open"]
    assert o["available"] is False
    assert o["positions"] is None and o["exposure_usd"] is None
    assert o["bankroll"] is None, "bankroll must never be invented"
    assert o["reason"], "absent exposure must be explained"


def test_pnl_open_reason_falls_back_when_the_source_gave_none():
    """With no reason from the loader, the payload still says why."""
    o = api.build_v2(src(sources=sources(
        engine_state=meta(available=False, reason=None))))["pnl"]["open"]
    assert "not zero" in o["reason"]


ENGINE_RISK_STATE = {
    "engine_status": "running",
    "_meta": {"written_at_utc": "2026-07-30T11:59:00+00:00"},
    "risk": {"open_positions": 3, "total_exposure_usd": 120.0,
             "total_exposure_pct": 12.0, "bankroll": 1000.0,
             "daily_pnl": -4.0, "venue_exposure": {"kalshi": 120.0}},
}


def test_pnl_open_degrades_when_systemd_says_the_engine_is_down():
    """F1: a readable snapshot from a stopped engine must not read as live.

    Fresh engine_state file, but systemd reports the unit inactive — the exact
    state reproduced 2026-07-30 ~14:01Z, when bankroll still returned 1000.0
    with available:true.
    """
    st = status(services=[{"id": "betting-pod-shop", "active": "inactive",
                           "since": None, "restarts": 0,
                           "severity": "critical", "heartbeat": None}])
    p = api.build_v2(src(engine_state=ENGINE_RISK_STATE, manager_status=st,
                         sources=sources(engine_state=meta(),
                                         manager_status=meta())))
    assert p["engine"]["liveness"] == "down"
    o = p["pnl"]["open"]
    assert o["available"] is False
    assert o["bankroll"] is None, "a stopped engine's bankroll is not current"
    assert o["positions"] is None and o["exposure_usd"] is None
    assert "down" in o["reason"]
    assert "2026-07-30T11:59:00+00:00" in o["reason"]
    assert o["last_known"]["bankroll"] == pytest.approx(1000.0)
    assert o["last_known"]["written_at_utc"] == "2026-07-30T11:59:00+00:00"


def test_pnl_open_degrades_when_the_snapshot_is_stale():
    p = api.build_v2(src(engine_state=ENGINE_RISK_STATE, manager_status=status(),
                         sources=sources(engine_state=meta(age=7200.0),
                                         manager_status=meta())))
    assert p["engine"]["liveness"] == "stale"
    o = p["pnl"]["open"]
    assert o["available"] is False and o["bankroll"] is None
    assert "stale" in o["reason"]
    assert o["last_known"]["positions"] == 3


def test_pnl_open_is_current_while_the_engine_is_live():
    p = api.build_v2(src(engine_state=ENGINE_RISK_STATE, manager_status=status(),
                         sources=sources(engine_state=meta(),
                                         manager_status=meta())))
    assert p["engine"]["liveness"] == "live"
    o = p["pnl"]["open"]
    assert o["available"] is True
    assert o["bankroll"] == pytest.approx(1000.0)
    assert o["last_known"] is None


def test_pnl_realized_windows_and_derived_rates():
    r = api.build_v2(src(rollup=ROLLUP,
                         sources=sources(rollup=meta())))["pnl"]["realized"]
    assert r["lifetime"] == pytest.approx(-23.48)
    assert r["d7"] == pytest.approx(-0.5)
    assert r["win_pct"] == pytest.approx(56.3, abs=0.1)
    assert r["roi_pct"] == pytest.approx(-0.049, abs=0.001)


def test_win_pct_is_null_when_nothing_resolved():
    ru = dict(ROLLUP)
    ru["lifetime"] = dict(ROLLUP["lifetime"], wins=0, losses=0)
    r = api.build_v2(src(rollup=ru, sources=sources(rollup=meta())))["pnl"]["realized"]
    assert r["win_pct"] is None


def test_equity_is_cumulative_realized_only_and_captioned():
    eq = api.build_v2(src(rollup=ROLLUP,
                          sources=sources(rollup=meta())))["pnl"]["equity"]
    assert eq["available"] is True
    assert eq["basis"] == "realized_only"
    assert "open positions are excluded" in eq["caption"]
    assert [p[1] for p in eq["points"]] == [1.0, -1.0, -0.5]


def test_equity_downsampling_keeps_the_ends():
    days = [["2026-01-%02d" % ((i % 28) + 1), 1.0, 1, 0, 0] for i in range(900)]
    ru = dict(ROLLUP, daily=days)
    eq = api.build_v2(src(rollup=ru, sources=sources(rollup=meta())))["pnl"]["equity"]
    assert eq["n_points"] <= api.EQUITY_MAX_POINTS
    assert eq["downsampled_from"] == 900
    assert eq["points"][-1][1] == pytest.approx(900.0)


def test_open_positions_roll_up_per_pod():
    op = {"rows": [{"pod_id": "P-017", "status": "OPEN", "position_size_usd": 50.0},
                   {"pod_id": "P-017", "status": "OPEN", "position_size_usd": 25.0},
                   {"pod_id": "P-017", "status": "WIN", "position_size_usd": 99.0}]}
    pods = api.build_v2(src(rollup=ROLLUP, open_positions=op,
                            sources=sources(rollup=meta(),
                                            open_positions=meta())))["pnl"]["by_pod"]
    by = {p["pod_id"]: p for p in pods}
    assert by["P-017"]["open_positions"] == 2, "settled rows must not count as open"
    assert by["P-017"]["capital_usd"] == pytest.approx(75.0)


def test_clv_means_and_the_inline_caveat():
    clv = api.build_v2(src(rollup=ROLLUP,
                           sources=sources(rollup=meta())))["pnl"]["clv"]
    row = clv["by_pod"][0]
    assert row["clv_gross_mean_c"] == pytest.approx(1.39, abs=0.01)
    assert row["clv_net_maker_mean_c"] == pytest.approx(0.94, abs=0.01)
    assert "wrong-day" in row["caveat"]
    assert "pinn_fair_close" in clv["source"], "name the fields as written on disk"


# ── pipeline ──────────────────────────────────────────────────────────


def test_research_pipeline_surfaces_dispatch_yield_and_x_cost():
    metrics = {
        "generated_at": "2026-08-02T14:00:00Z",
        "funnel": {
            "assignments": 50, "dispatched": 10,
            "dispatched_reviewed": 4, "dispatched_advanced": 1,
        },
        "dispatch": {"pending_review": 6, "review_rate": 0.4},
        "operations": {
            "semantics": {"agent_invocation_tracked": False},
            "window_hours": 24,
            "activity_24h": {"dispatched": 10, "reviewed": 4,
                             "advance": 1, "reject": 3, "defer": 0,
                             "research_minutes": 80},
            "queue": {"pending": 6, "overdue": 2,
                      "oldest_pending_age_hours": 51.0},
            "agents": {"social-scout": {
                "queue_state": "overdue", "pending": 6, "overdue": 2,
                "dispatched_24h": 10, "reviewed_24h": 4,
                "advance_24h": 1,
            }},
        },
        "by_source_type": {"social": {
            "assigned": 10, "advance": 1, "review_rate": 0.4,
            "advance_rate": 0.1,
        }},
        "x_pilot": {"month": "2026-08", "estimated_cost_usd": 1.065,
                    "assignments": 10, "reviewed": 4, "advanced": 1},
        "crossvenue_pilot": {
            "status": "healthy", "generated_at": "2026-08-02T12:00:00Z",
            "terms_equivalence": "unverified",
            "latest": {"matched_events": 3, "actionable_paths": 0},
        },
    }
    research = api.build_v2(src(
        research_metrics=metrics,
        sources=sources(research_metrics=meta())))["pipeline"]["research"]
    assert research["available"] is True
    assert research["funnel"][-1] == ["dispatched_advanced", 1.0]
    assert research["dispatch"]["pending_review"] == 6
    assert research["operations"]["queue"]["overdue"] == 2
    assert research["operations"]["semantics"]["agent_invocation_tracked"] is False
    assert research["x_pilot"]["estimated_cost_usd"] == 1.065
    assert research["crossvenue_pilot"]["latest"]["matched_events"] == 3


def test_live_crossvenue_metrics_override_daily_embedded_copy():
    daily = {
        "generated_at": "2026-08-02T04:00:00Z",
        "funnel": {}, "operations": {},
        "crossvenue_pilot": {"latest": {"matched_events": 3}},
    }
    live = {
        "generated_at": "2026-08-02T12:05:00Z", "status": "healthy",
        "latest": {"matched_events": 9},
    }
    research = api.build_v2(src(
        research_metrics=daily, crossvenue_metrics=live,
        sources=sources(research_metrics=meta(), crossvenue_metrics=meta()),
    ))["pipeline"]["research"]

    assert research["crossvenue_pilot"]["latest"]["matched_events"] == 9


def test_missing_research_metrics_is_unknown_not_zero():
    research = api.build_v2(src())["pipeline"]["research"]
    assert research["available"] is False
    assert research["reason"]
    assert all(value is None for _, value in research["funnel"])


WINDOW = {
    "state": "CLOSE_REF_PLACEHOLDER", "legacy_state": "WAITING", "alarm": True,
    "iso": "2026-07-30T11:49:00+00:00",
    "detail": "3 of 3 events carry a placeholder close reference.",
    "funnel": {"markets_listed": 68, "close_resolved": 68, "inside_window": 24,
               "priced": 19, "in_band": 6, "passes_every_screen": 2,
               "quoted_since_window_open": 0, "candidates_without_quote": 2},
    "screen_refusals": {"size_below_min": 14},
    "candidates_without_quote": ["KX-A", "KX-B"],
    "n_events": 3, "n_markets": 68,
}


def test_funnel_is_an_ordered_array_of_pairs():
    w = api.build_v2(src(p022_window=WINDOW,
                         sources=sources(p022_window=meta())))["pipeline"]["p022_window"]
    assert w["funnel"] == [["markets_listed", 68.0], ["close_resolved", 68.0],
                           ["inside_window", 24.0], ["priced", 19.0],
                           ["in_band", 6.0], ["passes_every_screen", 2.0],
                           ["quoted_since_window_open", 0.0],
                           ["candidates_without_quote", 2.0]]
    assert w["alarm"] is True


def test_window_detail_is_verbatim():
    w = api.build_v2(src(p022_window=WINDOW,
                         sources=sources(p022_window=meta())))["pipeline"]["p022_window"]
    assert w["detail"] == WINDOW["detail"]


def test_missing_window_source_from_an_older_collector():
    p = api.build_v2(src(manager_status=status(),
                        sources=sources(manager_status=meta())))
    w = p["pipeline"]["p022_window"]
    assert w["available"] is False
    assert w["alarm"] is None, "unknown alarm state must not read as 'no alarm'"
    assert w["reason"]


def test_missing_evmap_block_does_not_raise():
    p = api.build_v2(src(manager_status=status(),
                        sources=sources(manager_status=meta())))
    assert p["ops"]["evmap"] is None


def test_placement_rate_by_week():
    pl = api.build_v2(src(rollup=ROLLUP,
                          sources=sources(rollup=meta())))["pipeline"]["placement"]
    wk = pl["by_week"][0]
    assert wk["week"] == "2026-W31"
    assert wk["placement_rate_pct"] == pytest.approx(100 * 5 / 1405, abs=1e-4)
    assert pl["lifetime"]["placed"] == 181


def test_skip_reasons_window_and_lifetime():
    sk = api.build_v2(src(rollup=ROLLUP,
                          sources=sources(rollup=meta())))["pipeline"]["skip_reasons"]
    assert sk["lifetime"][0] == ["edge_below_threshold", 41230]
    assert sk["windowed"] == [["edge_below_threshold", 120]]
    assert sk["window_days"] == api.SKIP_WINDOW_DAYS
    assert sk["coverage"]["rows_missing_pod_id"] == 630


# ── aggregate-risk shadow ─────────────────────────────────────────────


def test_shadow_risk_absent_reads_as_enforcing_not_as_zero():
    """No shadow section means the guard is ENFORCING. The panel must say
    so rather than render an empty shadow card."""
    sh = api.build_v2(src(rollup=ROLLUP,
                          sources=sources(rollup=meta())))["pipeline"]["shadow_risk"]
    assert sh["available"] is False
    assert sh["enforcing"] is True
    assert sh["reason"]


def test_shadow_risk_window_and_lifetime():
    import copy
    from datetime import datetime, timedelta, timezone

    ru = copy.deepcopy(ROLLUP)
    # Anchor to the fixture's pinned generated_at_utc — that is what the API
    # windows against. Wall-clock dates rot: on 2026-08-04 "now - 35d" landed
    # exactly ON the cutoff and this test broke a year after it was written.
    anchor = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    recent = anchor.strftime("%Y-%m-%d")
    old = (anchor
           - timedelta(days=api.SKIP_WINDOW_DAYS + 5)).strftime("%Y-%m-%d")
    ru["shadow_risk"] = {
        "available": True,
        "by_day_retention_days": 45,
        "lifetime": {
            "total": 140, "usd_passed_through": 3500.0,
            "by_kind": {"venue_exposure": 100, "total_exposure": 40},
            "by_pod": {"P-017": {"venue_exposure": 100}},
        },
        "by_day": {recent: {"venue_exposure": 12},
                   old: {"total_exposure": 40}},
        "last_utc": recent + "T12:00:00+00:00",
        "note": "n",
    }
    sh = api.build_v2(src(rollup=ru,
                          sources=sources(rollup=meta())))["pipeline"]["shadow_risk"]

    assert sh["available"] is True
    assert sh["enforcing"] is False
    assert sh["lifetime_total"] == 140
    assert sh["usd_passed_through"] == 3500.0
    assert sh["lifetime"][0] == ["venue_exposure", 100]
    # The stale day falls outside the window; the recent one does not.
    assert sh["windowed"] == [["venue_exposure", 12]]
    assert sh["lifetime_by_pod"]["P-017"] == [["venue_exposure", 100]]


# ── findings ──────────────────────────────────────────────────────────


def test_findings_are_severity_sorted_and_unknown_severity_ranks_high():
    """`run_checks` emits `error`, which SEVERITY_ORDER does not list.

    An unknown severity must sort near the top; burying a severity nobody
    enumerated is exactly how it goes unnoticed.
    """
    order = api._SEVERITY_ORDER
    assert order["critical"] < order["error"] < order["warn"] < order["info"]
    assert order["critical"] < api._UNKNOWN_SEVERITY_RANK < order["warn"]
    assert api._severity_rank("something_new", order) == api._UNKNOWN_SEVERITY_RANK


def test_alarms_include_error_and_unknown_severities():
    st = status()
    p = api.build_v2(src(manager_status=st, sources=sources(manager_status=meta())))
    # real run_checks output on this snapshot; just assert the filter's shape
    for f in p["alarms"]:
        sev = str(f.get("severity", "")).lower()
        assert sev in api.LOUD_SEVERITIES or sev not in api._SEVERITY_ORDER


def test_checks_failure_becomes_a_visible_finding(monkeypatch):
    """An empty findings list must never mean "could not compute"."""
    import manager.checks as checks

    def boom(_snap, registry=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(checks, "run_checks", boom)
    p = api.build_v2(src(manager_status=status(),
                        sources=sources(manager_split=meta(),
                                        manager_status=meta())))
    assert len(p["ops"]["findings"]) == 1
    f = p["ops"]["findings"][0]
    assert f["key"] == "dashboard.checks_failed"
    assert "kaboom" in f["detail"]
    assert "not because nothing is wrong" in f["detail"]


def test_no_status_json_means_no_findings_not_a_crash():
    p = api.build_v2(src())
    assert p["ops"]["findings"] == []
    assert p["ops"]["available"] is False
