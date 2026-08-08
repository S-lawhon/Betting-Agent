#!/usr/bin/env python3
"""Render the daily brief from collected facts.

This is the deterministic layer: it assembles what is true. The fund-manager
agent reads this output and adds judgment on top — what it means, what to do
first, what looks off. Keeping assembly separate from judgment means the brief
still renders correctly when no model is in the loop.

Structure is deliberate and ordered by what you can act on:

    1. NEEDS YOU          - blocked_on: human. The reason this tool exists.
    2. PROBLEMS           - critical/warn findings.
    3. LIVE PRODUCTION    - services, pods, 24h activity.
    4. GATES              - progress toward each pre-registered decision point.
    5. ACCUMULATING       - blocked_on: time. Deliberately terse.

Workstreams waiting on calendar time are summarised in a single line rather
than repeated in full. P-015 needs ~6 months to reach n=120; printing its full
status every morning would train you to skim past the whole document.

Usage:
    python3 manager/brief.py                 # markdown to stdout
    python3 manager/brief.py --html out.html
    python3 manager/brief.py --email         # render and send
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import notify  # noqa: E402

HERE = Path(__file__).resolve().parent
UTC = timezone.utc
DASHBOARD_URL = "https://dashboard.htxtrades.org/manager"


def now() -> datetime:
    return datetime.now(UTC)


def _fmt_age(hours: Optional[float]) -> str:
    if hours is None:
        return "unknown"
    if hours < 1:
        return "{:.0f}m ago".format(hours * 60)
    if hours < 48:
        return "{:.0f}h ago".format(hours)
    return "{:.0f}d ago".format(hours / 24)


def _pct_bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, int(round(pct / 100.0 * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _metric(value: Any) -> str:
    """Render a measured count without turning missing into zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    return str(int(value)) if float(value).is_integer() else "{:.1f}".format(value)


def _seconds(value: Any) -> str:
    return "{}s".format(_metric(value)) if isinstance(value, (int, float)) else "unknown"


def _usd(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    normalized = 0.0 if abs(float(value)) < 0.0005 else float(value)
    return "${:.3f}".format(normalized)


def build(snap: Dict[str, Any], findings: List[checks.Finding]) -> Dict[str, Any]:
    """Assemble the brief into structured sections."""
    by_sev: Dict[str, List[checks.Finding]] = {}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)

    crit = by_sev.get("critical", [])
    warn = by_sev.get("warn", [])
    actions = by_sev.get("action", [])
    infos = by_sev.get("info", [])

    if crit:
        headline = "{} CRITICAL issue{} — live production needs attention".format(
            len(crit), "s" if len(crit) > 1 else "")
        status = "critical"
    elif warn:
        headline = "{} issue{} to look at; nothing on fire".format(
            len(warn), "s" if len(warn) > 1 else "")
        status = "warn"
    elif actions:
        headline = "All systems nominal — {} thing{} waiting on you".format(
            len(actions), "s" if len(actions) > 1 else "")
        status = "ok"
    else:
        headline = "All systems nominal, nothing blocked on you"
        status = "ok"

    # Gate progress lines.
    gates: List[Dict[str, Any]] = []
    maker = snap.get("maker") or {}
    if maker.get("available"):
        fills = maker.get("fills_clean") or 0
        thresh = maker.get("threshold") or 500
        closed = maker.get("gate_status") == "CLOSED"
        gates.append({
            "id": "P-016",
            "label": ("Live Maker — gate CLOSED" if closed
                      else "Live Maker — fills toward gate"),
            "resolved": closed,
            "resolved_on": maker.get("resolved_on"),
            "verdict": maker.get("gate_verdict"),
            "current": fills, "threshold": thresh,
            "pct": min(100.0, 100.0 * fills / max(1, thresh)),
            "note": "{}markout mean {} | ex-best-day {}".format(
                "final sample: " if closed else "",
                maker.get("markout_mean"), maker.get("markout_mean_ex_best_day")),
        })
    cp = (snap.get("p015") or {}).get("checkpoint") or {}
    if cp:
        n = cp.get("n") or cp.get("settled") or 0
        gates.append({
            "id": "P-015",
            "label": "Tennis Qualifier — settled trades toward first checkpoint",
            "current": n, "threshold": 120,
            "pct": min(100.0, 100.0 * n / 120.0),
            "note": "NO DECISION until n=120 (~Jan 2027). "
                    "US Open quals Aug 17-21 is the first volume spike.",
        })
    p029_cp = (snap.get("p029") or {}).get("checkpoint") or {}
    if p029_cp:
        progress = p029_cp.get("progress")
        current = progress if isinstance(progress, (int, float)) else 0
        threshold = p029_cp.get("threshold") or 500
        gates.append({
            "id": "P-029",
            "label": "Combo maker - frozen forward Gate 0c",
            "current": current,
            "threshold": threshold,
            "pct": min(100.0, 100.0 * current / max(1, threshold)),
            "verdict": p029_cp.get("verdict"),
            "note": "{} Read no earlier than 2026-08-23; one extension only."
                    .format(p029_cp.get("reason") or ""),
        })
    mlb_cp = (snap.get("mlb_props") or {}).get("checkpoint") or {}
    if mlb_cp:
        progress = mlb_cp.get("progress")
        current = progress if isinstance(progress, (int, float)) else 0
        threshold = mlb_cp.get("threshold") or 27
        gates.append({
            "id": "R-MLB-PROPS",
            "label": "MLB props - clean execution game-days",
            "current": current,
            "threshold": threshold,
            "pct": min(100.0, 100.0 * current / max(1, threshold)),
            "verdict": mlb_cp.get("verdict"),
            "note": "{} Outcomes stay blind until 2026-08-18 00:30 ET and 27 clean days."
                    .format(mlb_cp.get("reason") or ""),
        })

    # Accumulating (blocked on time) — one line each, no detail.
    accumulating = [
        {"id": ws.get("id"), "name": ws.get("name"),
         "summary": (ws.get("summary") or "").strip().split("\n")[0][:140]}
        for ws in snap.get("workstreams", [])
        if ws.get("blocked_on") == "time"
    ]

    # Where the work actually happened, by directory mtime.
    active_dirs = sorted(
        [(ws.get("id"), ws.get("name"), ws.get("owner_dir_age_hours"))
         for ws in snap.get("workstreams", [])
         if ws.get("owner_dir_age_hours") is not None],
        key=lambda r: r[2])[:5]

    return {
        "status": status, "headline": headline,
        "collected_at": snap.get("collected_at"),
        "host": snap.get("host"),
        "critical": crit, "warn": warn, "actions": actions, "info": infos,
        "services": snap.get("services", []),
        "trade": snap.get("trade", {}),
        "work": snap.get("work_today", {}),
        "research": snap.get("research_operations", {}),
        "gates": gates,
        "accumulating": accumulating,
        "active_dirs": active_dirs,
        "faults": snap.get("faults", []),
    }


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------

def render_markdown(b: Dict[str, Any]) -> str:
    L: List[str] = []
    icon = {"critical": "[!]", "warn": "[~]", "ok": "[ok]"}[b["status"]]
    L.append("# Betting Fund — Daily Brief")
    L.append("")
    L.append("**{} {}**".format(icon, b["headline"]))
    L.append("")
    L.append("_Collected {} on {}_".format(b.get("collected_at"), b.get("host")))
    L.append("")

    if b["critical"]:
        L.append("## CRITICAL")
        L.append("")
        for f in b["critical"]:
            L.append("### {}".format(f.title))
            if f.detail:
                L.append("")
                L.append(f.detail)
            if f.fix:
                L.append("")
                L.append("```\n{}\n```".format(f.fix))
            L.append("")

    if b["actions"]:
        L.append("## Waiting on you")
        L.append("")
        for f in b["actions"]:
            L.append("**{}**".format(f.title))
            L.append("")
            if f.detail:
                L.append(f.detail)
            L.append("")

    if b["warn"]:
        L.append("## Needs a look")
        L.append("")
        for f in b["warn"]:
            L.append("- **{}**".format(f.title))
            if f.detail:
                first = f.detail.splitlines()[0]
                L.append("  {}".format(first))
            if f.fix:
                L.append("  `{}`".format(f.fix))
        L.append("")

    L.append("## Live production")
    L.append("")
    for s in b["services"]:
        state = s.get("active")
        mark = "ok" if state == "active" else ("n/a" if state == "n/a" else "DOWN")
        up = s.get("uptime_minutes")
        L.append("- **{}** — {}{}".format(
            s.get("id"), mark,
            " (up {:.0f}h)".format(up / 60.0) if up else ""))
    trade = b.get("trade") or {}
    if trade.get("available"):
        acts = trade.get("actions") or {}
        real = sum(v for k, v in acts.items() if not k.startswith("SKIP"))
        L.append("- 24h: {} real actions, {} scan rows, realized P&L ${:.2f}".format(
            real, sum(acts.values()), trade.get("realized_pnl_24h") or 0.0))
        for pod, st in sorted((trade.get("per_pod") or {}).items()):
            L.append("  - {}: placed {} / settled {} (W{} L{} V{})".format(
                pod, st.get("placed", 0), st.get("settled", 0),
                st.get("won", 0), st.get("lost", 0), st.get("void", 0)))
    L.append("")

    work = b.get("work") or {}
    win = work.get("window_hours", 24)
    L.append("## Work completed (last {}h)".format(win))
    L.append("")
    if not work.get("available"):
        L.append("_Work summary unavailable — {}._".format(
            work.get("note") or "no git source configured"))
    else:
        commits = work.get("commits") or []
        if commits:
            for c in commits:
                when = (c.get("iso") or "")[:16].replace("T", " ")
                L.append("- `{}` {} — {}".format(
                    c.get("hash"), when, c.get("subject")))
        elif work.get("visibility") == "pushed_refs_only":
            L.append("_No pushed commits are visible in the last {}h; "
                     "local unpushed work is outside the mirror's view._".format(win))
        else:
            L.append("_Nothing committed in the last {}h._".format(win))
        areas = work.get("research_areas") or []
        if areas:
            L.append("")
            L.append("Research areas touched: {}".format(", ".join(areas)))
        unc = work.get("uncommitted_research_files") or 0
        if unc:
            L.append("_+ {} uncommitted research file{} in progress._".format(
                unc, "s" if unc != 1 else ""))
        if work.get("fetched") is False:
            L.append("")
            L.append("_(git mirror fetch failed — list may be stale)_")
        elif work.get("visibility") == "pushed_refs_only" and commits:
            L.append("")
            L.append("_(git mirror view: pushed refs only)_")
    L.append("")

    research = b.get("research") or {}
    L.append("## Research operations")
    L.append("")
    if not research.get("available"):
        L.append("_Research operations unavailable — {}._".format(
            research.get("reason") or "no measured operations snapshot"))
    else:
        operations = research.get("operations") or {}
        semantics = operations.get("semantics") or {}
        activity = operations.get("activity_24h") or {}
        queue = operations.get("queue") or {}
        worker = operations.get("worker") or {}
        funnel = research.get("funnel") or {}
        L.append(
            "**Execution status:** task packets are created automatically; "
            "worker claims/start state are {}; model invocation is {}. Completion "
            "requires a durable research disposition.".format(
                "tracked" if semantics.get("started_tracking_available")
                else "not tracked",
                "tracked" if semantics.get("agent_invocation_tracked")
                else "not tracked"))
        if worker.get("status") and worker.get("status") != "unavailable":
            usage = worker.get("daily_usage") or {}
            provider = ("configured ({})".format(
                worker.get("billing_mode") or "unknown")
                if worker.get("provider_configured") else "not configured")
            if worker.get("billing_mode") == "chatgpt_subscription":
                usage_text = (
                    "{} of {} daily attempts, {} input / {} output tokens; "
                    "$0 incremental API cost".format(
                        _metric(usage.get("attempts")),
                        _metric(usage.get("hard_attempt_limit")),
                        _metric(usage.get("input_tokens")),
                        _metric(usage.get("output_tokens"))))
            else:
                usage_text = "{} attempts / ${} of ${} hard limit".format(
                    _metric(usage.get("attempts")),
                    _metric(usage.get("cost_usd")),
                    _metric(usage.get("hard_cost_limit_usd")))
            L.append(
                "**Default research planner:** {} mode, status {}; provider {}; next {}; "
                "daily model usage {}.".format(
                    worker.get("mode") or "unknown",
                    worker.get("status") or "unknown", provider,
                    worker.get("assignment_id") or "none", usage_text))
        if (activity.get("invoked") or 0) > 0:
            L.append(
                "**Provider-backed pilot:** {} tracked model invocation(s) and "
                "{} durable review(s) in the last 24h. The default planner "
                "status above is a separate, deliberately dry-run service."
                .format(_metric(activity.get("invoked")),
                        _metric(activity.get("reviewed"))))
        L.append("")
        L.append(
            "- Lifetime funnel: {} assignments → {} dispatched → {} reviewed → "
            "{} advanced".format(
                _metric(funnel.get("assignments")),
                _metric(funnel.get("dispatched")),
                _metric(funnel.get("dispatched_reviewed")),
                _metric(funnel.get("dispatched_advanced"))))
        L.append(
            "- Last 24h: {} dispatched, {} started, {} model-invoked, {} reviewed ({} advanced / "
            "{} rejected / {} deferred), {} research minutes".format(
                _metric(activity.get("dispatched")),
                _metric(activity.get("started")),
                _metric(activity.get("invoked")),
                _metric(activity.get("reviewed")),
                _metric(activity.get("advance")),
                _metric(activity.get("reject")),
                _metric(activity.get("defer")),
                _metric(activity.get("research_minutes"))))
        L.append(
            "- Queue: {} pending, {} in progress, {} older than {}h; oldest {}".format(
                _metric(queue.get("pending")), _metric(queue.get("in_progress")),
                _metric(queue.get("overdue")),
                _metric(operations.get("overdue_after_hours")),
                _fmt_age(queue.get("oldest_pending_age_hours"))))
        agents = operations.get("agents") or {}
        if agents:
            L.append("")
            L.append("Agent queues:")
            for agent, row in sorted(agents.items()):
                L.append(
                    "- **{}** — {} pending ({} in progress / {} overdue; oldest {}), {} reviewed, "
                    "{} advanced, and {} research minutes in 24h".format(
                        agent, _metric(row.get("pending")),
                        _metric(row.get("in_progress")),
                        _metric(row.get("overdue")),
                        _fmt_age(row.get("oldest_pending_age_hours")),
                        _metric(row.get("reviewed_24h")),
                        _metric(row.get("advance_24h")),
                        _metric(row.get("research_minutes_24h"))))
        x_pilot = research.get("x_pilot") or {}
        crossvenue = research.get("crossvenue_pilot") or {}
        collector_health = research.get("collector_health") or {}
        quality = research.get("quality_control") or {}
        zero_feeds = collector_health.get("zero_academic_feeds") or []
        expected_feeds = (collector_health.get("expected_empty_academic_feeds")
                          or [])
        L.append("")
        L.append("Source health: {} — {} raw academic items, {} collector errors{}"
                 .format(
                     collector_health.get("status") or "unknown",
                     _metric(collector_health.get("academic_feed_items_raw")),
                     _metric(collector_health.get("collector_error_count")),
                     ("; zero feeds{}: {}".format(
                         " (expected today)"
                         if len(expected_feeds) == len(zero_feeds) else "",
                         ", ".join(zero_feeds))
                      if zero_feeds else "")))
        L.append("Quality control: {} rejected at intake, {} blocked at triage, "
                 "{} legacy packets quarantined".format(
                     _metric(quality.get("intake_rejected")),
                     _metric(quality.get("triage_blocked")),
                     _metric(quality.get("legacy_dispatches_quarantined"))))
        if x_pilot.get("month"):
            cost = x_pilot.get("estimated_cost_usd")
            cost_text = ("${:.3f}".format(cost)
                         if isinstance(cost, (int, float)) else "unknown")
            L.append("")
            L.append("X pilot: {} estimated spend; {} assignments, {} reviewed, "
                     "{} advanced.".format(
                         cost_text, _metric(x_pilot.get("assignments")),
                         _metric(x_pilot.get("reviewed")),
                         _metric(x_pilot.get("advanced"))))
        if crossvenue.get("generated_at"):
            latest = crossvenue.get("latest") or {}
            day = crossvenue.get("last_24h") or {}
            analytics = crossvenue.get("analytics") or {}
            episodes = analytics.get("episodes") or {}
            edge = analytics.get("net_edge_usd") or {}
            signals = crossvenue.get("research_signals")
            signal_count = len(signals) if isinstance(signals, list) else None
            venue_summary = ((crossvenue.get("venue_pipeline") or {})
                             .get("summary") or {})
            venue_rows = ((crossvenue.get("venue_pipeline") or {})
                          .get("venues") or [])
            prophetx = next((row for row in venue_rows
                             if row.get("id") == "prophetx"), {})
            px_validation = prophetx.get("validation") or {}
            cases = crossvenue.get("research_cases") or {}
            basis = cases.get("settlement_basis") or {}
            evidence = cases.get("outcome_evidence") or {}
            completeness = analytics.get("quote_completeness")
            completeness_text = ("{:.1f}%".format(completeness * 100)
                                 if isinstance(completeness, (int, float))
                                 else "unknown")
            L.append("")
            L.append("Gemini/Kalshi research: {} — {} matched now, {} hypothetical "
                     "positive paths, {} actionable; {} snapshots and {} matched observations in "
                     "24h; settlement terms {}. 14d: {} quote completeness, "
                     "{} paths at least 3c, {} persistent episodes, median "
                     "duration {}, p95 edge {}; {} research alerts. Venue "
                     "expansion: {} collecting, {} blocked, {} excluded.".format(
                         crossvenue.get("status") or "unknown",
                         _metric(latest.get("matched_events")),
                         _metric(latest.get("hypothetical_positive_paths")),
                         _metric(latest.get("actionable_paths")),
                         _metric(day.get("snapshots")),
                         _metric(day.get("matched_events")),
                         crossvenue.get("terms_equivalence") or "unknown",
                         completeness_text,
                         _metric(analytics.get("qualifying_path_observations")),
                         _metric(episodes.get("persistent_count")),
                         _seconds(episodes.get("median_duration_seconds")),
                         _usd(edge.get("p95")), _metric(signal_count),
                         _metric(venue_summary.get("collecting")),
                         _metric(venue_summary.get("blocked")),
                         _metric(venue_summary.get("excluded"))))
            if prophetx:
                sandbox = px_validation.get("sandbox") or {}
                production = px_validation.get("production") or {}
                L.append("ProphetX readiness: sandbox {}; production {}; rollout {}. "
                         "Sports ready: {}; blocked: {}. Blockers: {}.".format(
                             sandbox.get("status") or "not run",
                             production.get("status") or "not run",
                             "ready" if production.get("rollout_ready") else "blocked",
                             ", ".join(sandbox.get("ready_sports") or []) or "none",
                             ", ".join(sandbox.get("blocked_sports") or []) or "none",
                             ", ".join(production.get("blockers") or [])
                             or prophetx.get("collection_state") or "unknown"))
                sport_quotes = ((sandbox.get("counts") or {})
                                .get("quote_coverage_by_sport") or {})
                if sport_quotes:
                    L.append("ProphetX sandbox quotes: {}.".format(
                        "; ".join("{} {} ({}/{} executable; {} missing PX asks)".format(
                            sport,
                            ("{:.1f}%".format(row["coverage"] * 100)
                             if isinstance(row.get("coverage"), (int, float))
                             else "unmeasured"),
                            _metric(row.get("executable_rows")),
                            _metric(row.get("eligible_rows")),
                            _metric(row.get("missing_prophetx_ask")))
                            for sport, row in sorted(sport_quotes.items()))))
            risk_dimensions = basis.get("risk_dimensions")
            risk_text = (", ".join(risk_dimensions)
                         if isinstance(risk_dimensions, list) else "unknown")
            L.append("Case ledger: {} lifetime, {} in 24h, {} in 7d, {} active; "
                     "single-snapshot share {}; settlement-basis risks: {}; "
                     "phase pre/after/unknown {}/{}/{}."
                     .format(
                         _metric(cases.get("lifetime_cases")),
                         _metric(cases.get("cases_last_24h")),
                         _metric(cases.get("cases_last_7d")),
                         _metric(cases.get("active_cases")),
                         ("{:.1f}%".format(cases["single_snapshot_share"] * 100)
                         if isinstance(cases.get("single_snapshot_share"),
                                        (int, float)) else "unknown"),
                         risk_text or "none",
                         _metric((cases.get("market_phase") or {}).get(
                             "pre_scheduled_start")),
                         _metric((cases.get("market_phase") or {}).get(
                             "after_scheduled_start")),
                         _metric((cases.get("market_phase") or {}).get("unknown"))))
            exception_rate = evidence.get("observed_exception_rate_lower_bound")
            exception_text = (
                "{:.1f}%".format(exception_rate * 100)
                if isinstance(exception_rate, (int, float)) else "unmeasured")
            L.append("Outcome evidence: {} schedule coverage, {} resolved; "
                     "observed settlement exception lower bound {} ({} cases)."
                     .format(
                         ("{:.1f}%".format(evidence["schedule_coverage"] * 100)
                          if isinstance(evidence.get("schedule_coverage"),
                                        (int, float)) else "unmeasured"),
                         _metric(evidence.get("resolved_schedule_cases")),
                         exception_text,
                         _metric(evidence.get("observed_exception_cases"))))
            top_cases = cases.get("top_last_24h") or []
            if top_cases:
                top = top_cases[0]
                L.append("Top 24h case: {} — max edge {}, duration {}, {} "
                         "observations, status {}, phase {}, schedule {} "
                         "(research only).".format(
                             top.get("case_id") or "unknown",
                             _usd(top.get("max_net_edge_usd")),
                             _seconds(top.get("duration_seconds_lower_bound")),
                             _metric(top.get("observations")),
                             top.get("status") or "unknown",
                             top.get("market_phase") or "unknown",
                             top.get("schedule_alignment_status") or "unknown"))
    L.append("")

    if b["gates"]:
        L.append("## Gate progress")
        L.append("")
        for g in b["gates"]:
            L.append("**{} — {}**".format(g["id"], g["label"]))
            L.append("")
            if g.get("resolved"):
                L.append("`CLOSED {} — verdict {}  ({}/{} final sample)`".format(
                    g.get("resolved_on") or "?", g.get("verdict") or "?",
                    g["current"], g["threshold"]))
            else:
                L.append("`{} {}/{}  ({:.0f}%)`".format(
                    _pct_bar(g["pct"]), g["current"], g["threshold"], g["pct"]))
            if g.get("note"):
                L.append("")
                L.append("_{}_".format(g["note"]))
            L.append("")

    if b["accumulating"]:
        L.append("## Accumulating (no action — waiting on sample, not on you)")
        L.append("")
        for a in b["accumulating"]:
            L.append("- **{}** {} — {}".format(a["id"], a["name"], a["summary"]))
        L.append("")

    if b["active_dirs"]:
        L.append("## Where work happened last")
        L.append("")
        for wid, name, age in b["active_dirs"]:
            L.append("- {} ({}) — {}".format(wid, name, _fmt_age(age)))
        L.append("")

    if b["faults"]:
        L.append("## Collector faults (these areas are UNMEASURED)")
        L.append("")
        for f in b["faults"]:
            L.append("- {}: {}".format(f.get("probe"), f.get("error")))
        L.append("")

    L.append("---")
    L.append("Dashboard: {}".format(DASHBOARD_URL))
    return "\n".join(L)


# --------------------------------------------------------------------------
# html
# --------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 820px; margin: 0 auto; padding: 24px; color: #1a1a1a; background: #fff; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
     color: #666; margin: 28px 0 10px; border-bottom: 1px solid #e3e3e3; padding-bottom: 6px; }
h3 { font-size: 16px; margin: 14px 0 6px; }
.headline { padding: 12px 14px; border-radius: 6px; font-weight: 600; margin: 12px 0 20px; }
.s-critical { background: #fdecea; color: #8b1a10; border-left: 4px solid #d93025; }
.s-warn { background: #fff6e0; color: #7a5400; border-left: 4px solid #f5a623; }
.s-ok { background: #eaf6ec; color: #1a6b2a; border-left: 4px solid #34a853; }
.card { border: 1px solid #e3e3e3; border-radius: 6px; padding: 12px 14px; margin: 10px 0; }
.card.critical { border-left: 4px solid #d93025; }
.card.action { border-left: 4px solid #1a73e8; }
.card.warn { border-left: 4px solid #f5a623; }
.meta { color: #777; font-size: 13px; }
pre, code { background: #f4f4f6; border-radius: 4px; font-size: 13px; }
pre { padding: 10px; overflow-x: auto; }
code { padding: 1px 5px; }
.bar { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
ul { padding-left: 20px; }
li { margin: 3px 0; }
.dim { color: #777; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  h2 { color: #9aa0a6; border-color: #303237; }
  .card { border-color: #303237; }
  pre, code { background: #22252a; }
  .s-critical { background: #3a1714; color: #ffb4a8; }
  .s-warn { background: #3a2e12; color: #ffd88a; }
  .s-ok { background: #14301c; color: #9fe0ae; }
  .dim, .meta { color: #9aa0a6; }
}
"""


def render_html(b: Dict[str, Any]) -> str:
    e = html.escape
    P: List[str] = []
    P.append("<!doctype html><html><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    P.append("<title>Betting Fund — Daily Brief</title>")
    P.append("<style>{}</style></head><body>".format(_CSS))
    P.append("<h1>Betting Fund — Daily Brief</h1>")
    P.append("<div class='meta'>Collected {} on {}</div>".format(
        e(str(b.get("collected_at"))), e(str(b.get("host")))))
    P.append("<div class='headline s-{}'>{}</div>".format(
        b["status"], e(b["headline"])))

    def cards(items, cls, heading):
        if not items:
            return
        P.append("<h2>{}</h2>".format(e(heading)))
        for f in items:
            P.append("<div class='card {}'>".format(cls))
            P.append("<h3>{}</h3>".format(e(f.title)))
            if f.detail:
                P.append("<div>{}</div>".format(
                    e(f.detail).replace("\n", "<br>")))
            if f.fix:
                P.append("<pre>{}</pre>".format(e(f.fix)))
            P.append("</div>")

    cards(b["critical"], "critical", "Critical")
    cards(b["actions"], "action", "Waiting on you")
    cards(b["warn"], "warn", "Needs a look")

    P.append("<h2>Live production</h2><ul>")
    for s in b["services"]:
        state = s.get("active")
        up = s.get("uptime_minutes")
        P.append("<li><b>{}</b> — {}{}</li>".format(
            e(str(s.get("id"))), e(str(state)),
            " <span class='dim'>(up {:.0f}h)</span>".format(up / 60.0) if up else ""))
    trade = b.get("trade") or {}
    if trade.get("available"):
        acts = trade.get("actions") or {}
        real = sum(v for k, v in acts.items() if not k.startswith("SKIP"))
        P.append("<li>24h: {} real actions, {} scan rows, realized P&amp;L ${:.2f}</li>"
                 .format(real, sum(acts.values()), trade.get("realized_pnl_24h") or 0.0))
    P.append("</ul>")

    work = b.get("work") or {}
    win = work.get("window_hours", 24)
    P.append("<h2>Work completed <span class='dim'>(last {}h)</span></h2>".format(win))
    if not work.get("available"):
        P.append("<div class='dim'>Work summary unavailable — {}.</div>".format(
            e(str(work.get("note") or "no git source configured"))))
    else:
        commits = work.get("commits") or []
        if commits:
            P.append("<ul>")
            for c in commits:
                when = (c.get("iso") or "")[:16].replace("T", " ")
                P.append("<li><code>{}</code> <span class='dim'>{}</span> — {}</li>"
                         .format(e(str(c.get("hash"))), e(when),
                                 e(str(c.get("subject")))))
            P.append("</ul>")
        elif work.get("visibility") == "pushed_refs_only":
            P.append("<div class='dim'>No pushed commits are visible in the last "
                     "{}h; local unpushed work is outside the mirror's view.</div>"
                     .format(win))
        else:
            P.append("<div class='dim'>Nothing committed in the last {}h.</div>"
                     .format(win))
        areas = work.get("research_areas") or []
        if areas:
            P.append("<div>Research areas touched: <b>{}</b></div>".format(
                e(", ".join(areas))))
        unc = work.get("uncommitted_research_files") or 0
        if unc:
            P.append("<div class='dim'>+ {} uncommitted research file{} in progress.</div>"
                     .format(unc, "s" if unc != 1 else ""))
        if work.get("fetched") is False:
            P.append("<div class='dim'>(git mirror fetch failed — list may be stale)</div>")
        elif work.get("visibility") == "pushed_refs_only" and commits:
            P.append("<div class='dim'>(git mirror view: pushed refs only)</div>")

    research = b.get("research") or {}
    P.append("<h2>Research operations</h2>")
    if not research.get("available"):
        P.append("<div class='dim'>Research operations unavailable — {}.</div>".format(
            e(str(research.get("reason") or "no measured operations snapshot"))))
    else:
        operations = research.get("operations") or {}
        semantics = operations.get("semantics") or {}
        activity = operations.get("activity_24h") or {}
        queue = operations.get("queue") or {}
        worker = operations.get("worker") or {}
        funnel = research.get("funnel") or {}
        started_tracking = ("tracked" if semantics.get("started_tracking_available")
                            else "not tracked")
        model_tracking = ("tracked" if semantics.get("agent_invocation_tracked")
                          else "not tracked")
        P.append("<div class='card'><b>Execution status</b><br>Task packets are "
                 "created automatically; worker claims/start state are {}; model "
                 "invocation is {}. Completion requires a durable research "
                 "disposition.</div>".format(
                     e(started_tracking), e(model_tracking)))
        if worker.get("status") and worker.get("status") != "unavailable":
            usage = worker.get("daily_usage") or {}
            provider = ("configured ({})".format(
                worker.get("billing_mode") or "unknown")
                if worker.get("provider_configured") else "not configured")
            if worker.get("billing_mode") == "chatgpt_subscription":
                usage_text = (
                    "{} of {} daily attempts, {} input / {} output tokens; "
                    "$0 incremental API cost".format(
                        _metric(usage.get("attempts")),
                        _metric(usage.get("hard_attempt_limit")),
                        _metric(usage.get("input_tokens")),
                        _metric(usage.get("output_tokens"))))
            else:
                usage_text = "{} attempts / ${} of ${} hard limit".format(
                    _metric(usage.get("attempts")),
                    _metric(usage.get("cost_usd")),
                    _metric(usage.get("hard_cost_limit_usd")))
            P.append("<div class='card'><b>Default research planner</b><br>{} mode, status {}; "
                     "provider {}; next {}; daily model usage {}.</div>".format(
                         e(str(worker.get("mode") or "unknown")),
                         e(str(worker.get("status") or "unknown")),
                         e(provider),
                         e(str(worker.get("assignment_id") or "none")),
                         e(usage_text)))
        if (activity.get("invoked") or 0) > 0:
            P.append(
                "<div class='card'><b>Provider-backed pilot</b><br>{} tracked "
                "model invocation(s) and {} durable review(s) in the last 24h. "
                "The default planner status above is a separate, deliberately "
                "dry-run service.</div>".format(
                    e(_metric(activity.get("invoked"))),
                    e(_metric(activity.get("reviewed")))))
        P.append("<ul>")
        P.append("<li>Lifetime funnel: {} assignments → {} dispatched → {} reviewed "
                 "→ {} advanced</li>".format(
                     e(_metric(funnel.get("assignments"))),
                     e(_metric(funnel.get("dispatched"))),
                     e(_metric(funnel.get("dispatched_reviewed"))),
                     e(_metric(funnel.get("dispatched_advanced")))))
        P.append("<li>Last 24h: {} dispatched, {} started, {} model-invoked, {} reviewed ({} advanced / "
                 "{} rejected / {} deferred), {} research minutes</li>".format(
                     e(_metric(activity.get("dispatched"))),
                     e(_metric(activity.get("started"))),
                     e(_metric(activity.get("invoked"))),
                     e(_metric(activity.get("reviewed"))),
                     e(_metric(activity.get("advance"))),
                     e(_metric(activity.get("reject"))),
                     e(_metric(activity.get("defer"))),
                     e(_metric(activity.get("research_minutes")))))
        P.append("<li>Queue: {} pending, {} in progress, {} older than {}h; oldest {}</li>".format(
            e(_metric(queue.get("pending"))), e(_metric(queue.get("in_progress"))),
            e(_metric(queue.get("overdue"))),
            e(_metric(operations.get("overdue_after_hours"))),
            e(_fmt_age(queue.get("oldest_pending_age_hours")))))
        P.append("</ul>")
        agents = operations.get("agents") or {}
        if agents:
            P.append("<div class='card'><b>Agent queues</b><ul>")
            for agent, row in sorted(agents.items()):
                P.append("<li><b>{}</b> — {} pending ({} in progress / {} overdue; oldest {}), {} "
                         "reviewed, {} advanced, and {} research minutes in 24h</li>".format(
                             e(str(agent)), e(_metric(row.get("pending"))),
                             e(_metric(row.get("in_progress"))),
                             e(_metric(row.get("overdue"))),
                             e(_fmt_age(row.get("oldest_pending_age_hours"))),
                             e(_metric(row.get("reviewed_24h"))),
                             e(_metric(row.get("advance_24h"))),
                             e(_metric(row.get("research_minutes_24h")))))
            P.append("</ul></div>")
        x_pilot = research.get("x_pilot") or {}
        crossvenue = research.get("crossvenue_pilot") or {}
        collector_health = research.get("collector_health") or {}
        quality = research.get("quality_control") or {}
        zero_feeds = collector_health.get("zero_academic_feeds") or []
        expected_feeds = (collector_health.get("expected_empty_academic_feeds")
                          or [])
        P.append("<div class='dim'>Source health: {} — {} raw academic items, {} "
                 "collector errors{}</div>".format(
                     e(str(collector_health.get("status") or "unknown")),
                     e(_metric(collector_health.get("academic_feed_items_raw"))),
                     e(_metric(collector_health.get("collector_error_count"))),
                     e("; zero feeds{}: {}".format(
                       " (expected today)"
                       if len(expected_feeds) == len(zero_feeds) else "",
                       ", ".join(zero_feeds))
                       if zero_feeds else "")))
        P.append("<div class='dim'>Quality control: {} rejected at intake, {} "
                 "blocked at triage, {} legacy packets quarantined</div>".format(
                     e(_metric(quality.get("intake_rejected"))),
                     e(_metric(quality.get("triage_blocked"))),
                     e(_metric(quality.get("legacy_dispatches_quarantined")))))
        if x_pilot.get("month"):
            cost = x_pilot.get("estimated_cost_usd")
            cost_text = ("${:.3f}".format(cost)
                         if isinstance(cost, (int, float)) else "unknown")
            P.append("<div class='dim'>X pilot: {} estimated spend; {} assignments, "
                     "{} reviewed, {} advanced.</div>".format(
                         e(cost_text), e(_metric(x_pilot.get("assignments"))),
                         e(_metric(x_pilot.get("reviewed"))),
                         e(_metric(x_pilot.get("advanced")))))
        if crossvenue.get("generated_at"):
            latest = crossvenue.get("latest") or {}
            day = crossvenue.get("last_24h") or {}
            analytics = crossvenue.get("analytics") or {}
            episodes = analytics.get("episodes") or {}
            edge = analytics.get("net_edge_usd") or {}
            signals = crossvenue.get("research_signals")
            signal_count = len(signals) if isinstance(signals, list) else None
            venue_summary = ((crossvenue.get("venue_pipeline") or {})
                             .get("summary") or {})
            venue_rows = ((crossvenue.get("venue_pipeline") or {})
                          .get("venues") or [])
            prophetx = next((row for row in venue_rows
                             if row.get("id") == "prophetx"), {})
            px_validation = prophetx.get("validation") or {}
            cases = crossvenue.get("research_cases") or {}
            basis = cases.get("settlement_basis") or {}
            evidence = cases.get("outcome_evidence") or {}
            completeness = analytics.get("quote_completeness")
            completeness_text = ("{:.1f}%".format(completeness * 100)
                                 if isinstance(completeness, (int, float))
                                 else "unknown")
            P.append("<div class='dim'>Gemini/Kalshi research: {} — {} matched now, "
                     "{} hypothetical positive paths, {} actionable; {} snapshots and {} matched "
                     "observations in 24h; settlement terms {}. 14d: {} quote "
                     "completeness, {} paths at least 3c, {} persistent episodes, "
                     "median duration {}, p95 edge {}; {} research alerts. "
                     "Venue expansion: {} collecting, {} blocked, {} "
                     "excluded.</div>".format(
                         e(str(crossvenue.get("status") or "unknown")),
                         e(_metric(latest.get("matched_events"))),
                         e(_metric(latest.get("hypothetical_positive_paths"))),
                         e(_metric(latest.get("actionable_paths"))),
                         e(_metric(day.get("snapshots"))),
                         e(_metric(day.get("matched_events"))),
                         e(str(crossvenue.get("terms_equivalence") or "unknown")),
                         e(completeness_text),
                         e(_metric(analytics.get("qualifying_path_observations"))),
                         e(_metric(episodes.get("persistent_count"))),
                         e(_seconds(episodes.get("median_duration_seconds"))),
                         e(_usd(edge.get("p95"))), e(_metric(signal_count)),
                         e(_metric(venue_summary.get("collecting"))),
                         e(_metric(venue_summary.get("blocked"))),
                         e(_metric(venue_summary.get("excluded")))))
            if prophetx:
                sandbox = px_validation.get("sandbox") or {}
                production = px_validation.get("production") or {}
                P.append("<div class='dim'>ProphetX readiness: sandbox {}; "
                         "production {}; rollout {}. Sports ready: {}; blocked: {}. "
                         "Blockers: {}.</div>".format(
                             e(str(sandbox.get("status") or "not run")),
                             e(str(production.get("status") or "not run")),
                             "ready" if production.get("rollout_ready") else "blocked",
                             e(", ".join(sandbox.get("ready_sports") or []) or "none"),
                             e(", ".join(sandbox.get("blocked_sports") or []) or "none"),
                             e(", ".join(production.get("blockers") or [])
                               or str(prophetx.get("collection_state") or "unknown"))))
                sport_quotes = ((sandbox.get("counts") or {})
                                .get("quote_coverage_by_sport") or {})
                if sport_quotes:
                    P.append("<div class='dim'>ProphetX sandbox quotes: {}.</div>".format(
                        e("; ".join("{} {} ({}/{} executable; {} missing PX asks)".format(
                            sport,
                            ("{:.1f}%".format(row["coverage"] * 100)
                             if isinstance(row.get("coverage"), (int, float))
                             else "unmeasured"),
                            _metric(row.get("executable_rows")),
                            _metric(row.get("eligible_rows")),
                            _metric(row.get("missing_prophetx_ask")))
                            for sport, row in sorted(sport_quotes.items())))))
            risk_dimensions = basis.get("risk_dimensions")
            risk_text = (", ".join(risk_dimensions)
                         if isinstance(risk_dimensions, list) else "unknown")
            singleton_text = (
                "{:.1f}%".format(cases["single_snapshot_share"] * 100)
                if isinstance(cases.get("single_snapshot_share"), (int, float))
                else "unknown")
            P.append("<div class='dim'>Case ledger: {} lifetime, {} in 24h, "
                     "{} in 7d, {} active; single-snapshot share {}; "
                     "settlement-basis risks: {}; phase pre/after/unknown "
                     "{}/{}/{}.</div>".format(
                         e(_metric(cases.get("lifetime_cases"))),
                         e(_metric(cases.get("cases_last_24h"))),
                         e(_metric(cases.get("cases_last_7d"))),
                         e(_metric(cases.get("active_cases"))),
                         e(singleton_text), e(risk_text or "none"),
                         e(_metric((cases.get("market_phase") or {}).get(
                             "pre_scheduled_start"))),
                         e(_metric((cases.get("market_phase") or {}).get(
                             "after_scheduled_start"))),
                         e(_metric((cases.get("market_phase") or {}).get("unknown")))))
            exception_rate = evidence.get("observed_exception_rate_lower_bound")
            exception_text = (
                "{:.1f}%".format(exception_rate * 100)
                if isinstance(exception_rate, (int, float)) else "unmeasured")
            coverage_text = (
                "{:.1f}%".format(evidence["schedule_coverage"] * 100)
                if isinstance(evidence.get("schedule_coverage"), (int, float))
                else "unmeasured")
            P.append("<div class='dim'>Outcome evidence: {} schedule coverage, "
                     "{} resolved; observed settlement exception lower bound "
                     "{} ({} cases).</div>".format(
                         e(coverage_text),
                         e(_metric(evidence.get("resolved_schedule_cases"))),
                         e(exception_text),
                         e(_metric(evidence.get("observed_exception_cases")))))
            top_cases = cases.get("top_last_24h") or []
            if top_cases:
                top = top_cases[0]
                P.append("<div class='dim'>Top 24h case: {} — max edge {}, "
                         "duration {}, {} observations, status {}, phase {}, "
                         "schedule {} (research only).</div>".format(
                             e(str(top.get("case_id") or "unknown")),
                             e(_usd(top.get("max_net_edge_usd"))),
                             e(_seconds(top.get("duration_seconds_lower_bound"))),
                             e(_metric(top.get("observations"))),
                             e(str(top.get("status") or "unknown")),
                             e(str(top.get("market_phase") or "unknown")),
                             e(str(top.get("schedule_alignment_status") or "unknown"))))

    if b["gates"]:
        P.append("<h2>Gate progress</h2>")
        for g in b["gates"]:
            P.append("<div class='card'><b>{} — {}</b><br>".format(
                e(g["id"]), e(g["label"])))
            if g.get("resolved"):
                P.append("<span class='bar'>CLOSED {} — verdict {} ({}/{} final sample)</span>".format(
                    e(str(g.get("resolved_on") or "?")), e(str(g.get("verdict") or "?")),
                    g["current"], g["threshold"]))
            else:
                P.append("<span class='bar'>{} {}/{} ({:.0f}%)</span>".format(
                    e(_pct_bar(g["pct"])), g["current"], g["threshold"], g["pct"]))
            if g.get("note"):
                P.append("<br><span class='dim'>{}</span>".format(e(str(g["note"]))))
            P.append("</div>")

    if b["accumulating"]:
        P.append("<h2>Accumulating <span class='dim'>(waiting on sample, not on you)</span></h2><ul>")
        for a in b["accumulating"]:
            P.append("<li><b>{}</b> {} — <span class='dim'>{}</span></li>".format(
                e(str(a["id"])), e(str(a["name"])), e(str(a["summary"]))))
        P.append("</ul>")

    if b["faults"]:
        P.append("<h2>Collector faults — these areas are UNMEASURED</h2><ul>")
        for f in b["faults"]:
            P.append("<li>{}: {}</li>".format(
                e(str(f.get("probe"))), e(str(f.get("error")))))
        P.append("</ul>")

    P.append("<hr><div class='meta'><a href='{0}'>{0}</a></div>".format(DASHBOARD_URL))
    P.append("</body></html>")
    return "\n".join(P)


def record_brief_run(status: str, headline: str, *, emailed: bool) -> None:
    """Append a durable heartbeat for the scheduled brief path."""
    state = HERE / "state"
    state.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp_utc": now().isoformat().replace("+00:00", "Z"),
        "status": status,
        "headline": headline,
        "emailed": emailed,
    }
    with (state / "brief_runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the daily fund brief")
    ap.add_argument("--status", default=None)
    ap.add_argument("--html", default=None, help="also write HTML to this path")
    ap.add_argument("--json", action="store_true", help="emit structured JSON")
    ap.add_argument("--email", action="store_true", help="send via configured SMTP")
    ap.add_argument("--out", default=None, help="write markdown to this path")
    args = ap.parse_args()

    notify.load_env()
    snap = checks.load_status(Path(args.status) if args.status else None)

    local_fp = None
    try:
        from collect import Collector  # noqa: PLC0415
        local_fp = Collector.config_fingerprint(HERE.parent / "config_multi_pod.yaml")
    except Exception:  # noqa: BLE001
        pass

    findings = checks.run_checks(snap, local_config_fp=local_fp)
    b = build(snap, findings)
    md = render_markdown(b)

    if args.json:
        print(json.dumps({**{k: v for k, v in b.items()
                             if k not in ("critical", "warn", "actions", "info")},
                          "findings": [f.to_dict() for f in findings]},
                         indent=2, default=str))
    else:
        print(md)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    if args.html:
        Path(args.html).write_text(render_html(b), encoding="utf-8")
    if args.email:
        res = notify.send_email(
            "[betting-fund] Daily brief — {}".format(b["headline"][:80]),
            md, render_html(b))
        print("\n[brief] " + res.summary(), file=sys.stderr)
        record_brief_run(
            "failed" if res.failed else "ok", b.get("headline", ""),
            emailed=bool(res.sent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
