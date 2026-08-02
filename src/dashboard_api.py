"""
src/dashboard_api.py
────────────────────
``build_v2(sources) -> dict`` — the dashboard's payload builder.

**This module does no I/O.** Its only input is the dict returned by
``src.dashboard_sources.load_all()``, and its only output is a JSON-ready dict.
That is deliberate: it makes every degradation path (source missing, field null,
older collector schema) a pure-function test with no filesystem, no clock and no
HTTP.

What it is allowed to do
────────────────────────
Reshape, join, label and window. That is all.

What it must never do
─────────────────────
Evaluate a gate. ``docs/GATE_INSTRUMENTATION_STANDARD.md`` §1 is explicit that a
dashboard tile is not a verdict, and §7 requires every consumer of a gate to
resolve the gate's declared ``source``. So:

* Gate numbers come from ``manager/state/status.json`` — written by the
  sanctioned checkpoint readers via ``manager/collect.py``. Never recomputed.
* The reader's own verdict string is passed through verbatim as
  ``reader_verdict``. The separate ``progress_state`` field is a **UI grouping
  label only**, derived from fields already present in the snapshot, and is
  named differently precisely so it can never be mistaken for the verdict.
* Findings come from ``manager.checks.run_checks``. No check is reimplemented
  here — one producer, many readers.
* ``progress`` may be ``None``. When it is, ``progress_available`` is ``False``
  and ``progress_reason`` says why. It is never coerced to ``0``: a zero that
  means "no reader" and a zero that means "measured zero" are different facts,
  and conflating them is how four of five gates spent 28 days measuring nothing
  while looking fine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

UTC = timezone.utc

SCHEMA = 2

#: The P-022 window funnel, in monotone order. Emitted as an ordered array of
#: pairs rather than an object because JSON key order is not a contract and the
#: stage sequence is the entire point of the panel.
FUNNEL_STAGES: Tuple[str, ...] = (
    "markets_listed",
    "close_resolved",
    "inside_window",
    "priced",
    "in_band",
    "passes_every_screen",
    "quoted_since_window_open",
    "candidates_without_quote",
)

RESEARCH_FUNNEL_STAGES: Tuple[str, ...] = (
    "assignments",
    "dispatched",
    "dispatched_reviewed",
    "dispatched_advanced",
)

#: snapshot key -> pod id, for the sanctioned checkpoint readers.
CHECKPOINT_KEYS = {
    "p001": "P-001",
    "p014": "P-014",
    "p015": "P-015",
    "p017": "P-017",
    "p022": "P-022",
    "maker": "P-016",
}

#: Stages at which a gate stops asking to be measured.
TERMINAL_STAGES = {"killed", "shelved", "retired"}

#: Max points in the equity series sent to the browser.
EQUITY_MAX_POINTS = 180

#: Rolling window for the skip-reason panel. Lifetime totals sit beside it —
#: a lifetime histogram alone is dominated by whatever config was live months
#: ago, which is complete but not actionable.
SKIP_WINDOW_DAYS = 30

#: Verbatim caveats that must travel with specific numbers. A CLV figure for
#: P-001 without this note is exactly the "tile as verdict" the Gate Standard
#: forbids — the gate's own registry entry records that its rows do not test
#: the hypothesis they appear to test.
POD_CAVEATS = {
    "P-001": ("P-001's CLV rows include wrong-day matches; clv_log's own "
              "`commence` agrees with the ticker on every row, so the defect is "
              "invisible from clv_log alone. Read the number only alongside "
              "scripts/p001_checkpoint.py's admissibility filter."),
}


# ── helpers ───────────────────────────────────────────────────────────


def _d(obj: Any) -> Dict[str, Any]:
    """Coerce to a dict. Older collector schemas simply omit whole blocks."""
    return obj if isinstance(obj, dict) else {}


def _l(obj: Any) -> List[Any]:
    return obj if isinstance(obj, list) else []


def _num(value: Any) -> Optional[float]:
    """Numeric or None. Never 0-as-a-fallback."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _available(sources: Dict[str, Any], name: str) -> bool:
    return bool(_d(_d(sources).get(name)).get("available"))


def _reason(sources: Dict[str, Any], name: str) -> Optional[str]:
    return _d(_d(sources).get(name)).get("reason")


def _downsample(points: List[List[Any]], limit: int) -> Tuple[List[List[Any]], int]:
    """Even stride, first and last always kept."""
    n = len(points)
    if n <= limit:
        return points, n
    step = n / float(limit - 1)
    idx = sorted({int(i * step) for i in range(limit - 1)} | {n - 1})
    return [points[i] for i in idx], n


# ── mode ──────────────────────────────────────────────────────────────


def _mode(status: Dict[str, Any], sources: Dict[str, Any]) -> Dict[str, Any]:
    """Paper vs live. Renders as a ribbon on every view, in all three states.

    A missing invariant is amber "MODE UNKNOWN", never a silent absence — the
    ribbon disappearing is indistinguishable from the ribbon saying "paper",
    and only one of those is safe.
    """
    inv = _d(status.get("invariants"))
    if not _available(sources, "manager_status") or "all_paper" not in inv:
        return {
            "available": False,
            "all_paper": None,
            "pod_modes": {},
            "label": "MODE UNKNOWN",
            "reason": (_reason(sources, "manager_status")
                       or "manager/state/status.json carries no invariants "
                          "block — cannot confirm paper mode"),
            "source": "manager/state/status.json:invariants",
        }
    all_paper = bool(inv.get("all_paper"))
    return {
        "available": True,
        "all_paper": all_paper,
        "pod_modes": _d(inv.get("pod_modes")),
        "non_paper_pods": _l(inv.get("non_paper_pods")),
        "label": ("PAPER — NO REAL CAPITAL AT RISK" if all_paper
                  else "LIVE CAPITAL"),
        "reason": None,
        "source": "manager/state/status.json:invariants",
    }


# ── engine ────────────────────────────────────────────────────────────


def _engine(src: Dict[str, Any]) -> Dict[str, Any]:
    """Engine liveness, from the snapshot file AND systemd's own view.

    ``liveness`` is four-valued on purpose. The v1 ``/api/status`` contract
    forces ``engine_status: "starting"`` when no snapshot exists (56 tests plus
    ``src/health_check.py`` depend on that vocabulary), which means a
    long-dead engine reads as "starting" on v1. This field is the honest one,
    and the UI renders only this.
    """
    sources = _d(src.get("sources"))
    state = _d(src.get("engine_state"))
    status = _d(src.get("manager_status"))
    meta = _d(sources.get("engine_state"))

    systemd: Dict[str, Any] = {"available": False,
                               "source": "manager/state/status.json:services"}
    for svc in _l(status.get("services")):
        if _d(svc).get("id") == "betting-pod-shop":
            systemd = {
                "available": True,
                "active": svc.get("active"),
                "since": svc.get("since"),
                "restarts": svc.get("restarts"),
                "severity": svc.get("severity"),
                "heartbeat": _d(svc.get("heartbeat")) or None,
                "source": "manager/state/status.json:services",
            }
            break

    age = _num(meta.get("age_seconds"))
    expected = _num(meta.get("expected_max_age_seconds")) or 900.0
    active = systemd.get("active")

    # "n/a" is what manager/collect.py records off a systemd host (the Mac
    # snapshot), and it means "we could not look" — NOT "the unit is stopped".
    # Reading it as stopped would report a healthy engine as down every time
    # the snapshot happened to be collected from the laptop.
    if active is None or str(active).lower() in ("n/a", "", "unknown", "none"):
        systemd_says_down = False
        systemd_view = False
    else:
        systemd_view = True
        systemd_says_down = str(active) not in ("active", "activating")
    systemd["view_available"] = systemd_view

    if not meta.get("available"):
        # No snapshot. systemd may still know the truth.
        if systemd_says_down:
            liveness, why = "down", "systemd reports the unit is {}".format(active)
        elif not systemd_view:
            liveness, why = "unknown", (
                "no engine snapshot at {} ({}), and manager/state/status.json "
                "carries no systemd view of betting-pod-shop — the engine's "
                "state is genuinely unknown, not idle".format(
                    _d(sources.get("engine_state")).get("path"),
                    meta.get("reason") or "unreadable"))
        else:
            liveness, why = "unknown", (
                "systemd says active but the engine has not written "
                + str(_d(sources.get("engine_state")).get("path"))
                + " — it may be starting, or writing may be failing")
    elif systemd_says_down:
        liveness, why = "down", "systemd reports the unit is {}".format(active)
    elif meta.get("clock_skew"):
        liveness, why = "unknown", meta.get("reason")
    elif age is not None and age > expected:
        liveness, why = "stale", (
            "last cycle wrote {:.1f} min ago; expected every {:.0f} s"
            .format(age / 60.0, expected))
    else:
        liveness, why = "live", None

    risk = _d(state.get("risk"))
    return {
        "liveness": liveness,
        "liveness_reason": why,
        # Verbatim from the engine's own serializer; vocabulary frozen by the
        # v1 contract. Present for continuity, not for judgement.
        "status": state.get("engine_status"),
        "age_seconds": age,
        "expected_max_age_seconds": expected,
        "written_at_utc": _d(state.get("_meta")).get("written_at_utc"),
        "interval_seconds": _num(_d(state.get("_meta")).get("interval_seconds")),
        "pid": _d(state.get("_meta")).get("pid"),
        "systemd": systemd,
        "halt": {
            "halted": risk.get("halted"),
            "reason": risk.get("halt_reason"),
        } if risk else {"halted": None, "reason": None},
        "kill_switches": [k for k in _l(src.get("kill_switches"))],
        "cycle": _d(state.get("cycle")) or None,
        "source": "data/dashboard/engine_state.json + "
                  "manager/state/status.json:services",
    }


# ── gates ─────────────────────────────────────────────────────────────


def _checkpoints(status: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """pod_id -> {reader, available, checkpoint|error} from the snapshot."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, pod in CHECKPOINT_KEYS.items():
        block = _d(status.get(key))
        if not block:
            continue
        out[pod] = block
    return out


def _progress_state(*, stage: str, progress: Optional[float],
                    threshold: Optional[float], stalled: Optional[bool],
                    settled_28d: Optional[float]) -> str:
    """A UI grouping label — NOT a verdict. See the module docstring.

    The ordering matters. ``unmeasurable`` is reserved strictly for "no reader
    resolved a number" — it must not swallow "the reader works and says zero",
    because those two need opposite responses: fix the instrumentation versus
    wait for observations. An earlier version returned ``unmeasurable`` whenever
    ``stalled`` was ``None``, which mislabelled every gate that had simply never
    settled anything.
    """
    if (stage or "").lower() in TERMINAL_STAGES:
        return "terminal"
    if progress is None:
        return "unmeasurable"
    if threshold is not None and progress >= threshold:
        return "reached"
    if stalled is True:
        return "stalled"
    if progress == 0:
        return "no observations"
    if settled_28d is None:
        return "activity unknown"
    if settled_28d == 0:
        # Historical progress but a genuinely measured dry month.
        return "dormant"
    return "accumulating"


def _gates(src: Dict[str, Any],
           findings: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]],
                                                    Dict[str, Any]]:
    status = _d(src.get("manager_status"))
    sources = _d(src.get("sources"))
    thr = _d(status.get("throughput"))
    records = {str(_d(r).get("id")): _d(r) for r in _l(thr.get("records"))}
    checkpoints = _checkpoints(status)

    # `tier` is in manager/registry.yaml but manager/collect.py does not copy it
    # into the snapshot's workstreams (verified against a live status.json: every
    # entry has tier: None). Membership in throughput.records IS the signal —
    # manager/throughput.py emits a record only for `tier: validating` gates —
    # so derive it rather than depending on a field that is not there.
    validating = set(records.keys())

    by_pod_findings: Dict[str, List[Dict[str, Any]]] = {}
    for f in findings:
        ws = f.get("workstream")
        if ws:
            by_pod_findings.setdefault(str(ws), []).append(f)

    gates: List[Dict[str, Any]] = []
    for ws in _l(status.get("workstreams")):
        ws = _d(ws)
        pod = str(ws.get("id") or "")
        if not pod:
            continue
        gate = _d(ws.get("gate"))
        rec = records.get(pod, {})
        cp_block = checkpoints.get(pod, {})
        cp = _d(cp_block.get("checkpoint"))

        # ---- progress: throughput first, then the reader's own block ----
        progress = _num(rec.get("progress"))
        progress_src = "manager/state/status.json:throughput.records"
        progress_reason = None
        if progress is None:
            progress = _num(cp.get("progress"))
            progress_src = "manager/state/status.json:{}.checkpoint".format(
                pod.lower().replace("-", ""))
        if progress is None:
            progress_src = None
            if cp_block and cp_block.get("available") is False:
                progress_reason = (
                    "the sanctioned reader could not run: {}".format(
                        cp_block.get("error") or "unknown error"))
            elif not _available(sources, "manager_status"):
                progress_reason = _reason(sources, "manager_status")
            elif not gate:
                progress_reason = ("no gate is declared for this workstream in "
                                   "manager/registry.yaml")
            else:
                progress_reason = (
                    "no reader resolved a progress value — checks.py's "
                    "_gate_progress could not map this gate's declared source")

        threshold = _num(rec.get("threshold")) or _num(gate.get("threshold")) \
            or _num(cp.get("threshold"))
        stalled = rec.get("stalled")
        settled_28d = _num(rec.get("settled_positions_28d"))

        pct = None
        if progress is not None and threshold:
            pct = round(min(100.0, 100.0 * progress / threshold), 1)

        gates.append({
            "pod_id": pod,
            "name": ws.get("name"),
            "stage": ws.get("stage"),
            "tier": ws.get("tier") or ("validating" if pod in validating else None),
            "is_validating": pod in validating,
            "blocked_on": ws.get("blocked_on"),
            "summary": ws.get("summary"),
            "action_required": ws.get("action_required"),
            "open_questions": _l(ws.get("open_questions")),
            "activity_24h": ws.get("activity_24h"),
            "owner_dir": ws.get("owner_dir"),
            "owner_dir_age_hours": _num(ws.get("owner_dir_age_hours")),

            "metric": rec.get("metric") or gate.get("metric"),
            "reader": gate.get("reader") or cp_block.get("reader"),
            "reader_available": cp_block.get("available"),
            "reader_error": cp_block.get("error"),
            # Verbatim from the sanctioned reader. The only verdict on the page.
            "reader_verdict": cp.get("verdict") or gate.get("verdict"),
            "reader_verdict_reason": cp.get("reason"),
            "decision_rule": gate.get("decision") or gate.get("rules"),

            "progress": progress,
            "progress_available": progress is not None,
            "progress_reason": progress_reason,
            "progress_source": progress_src,
            "threshold": threshold,
            "progress_pct": pct,
            # A UI grouping label, not a measurement. See module docstring.
            "progress_state": _progress_state(
                stage=str(ws.get("stage") or ""), progress=progress,
                threshold=threshold, stalled=stalled, settled_28d=settled_28d),

            "settled_positions_7d": _num(rec.get("settled_positions_7d")),
            "settled_positions_28d": settled_28d,
            "observation_unit": rec.get("observation_unit") or "positions",
            "activity_available": rec.get("activity_available"),
            "last_settlement_utc": rec.get("last_settlement_utc"),
            "days_since_last_settlement":
                _num(rec.get("days_since_last_settlement")),
            "stall_days_tolerated": _num(rec.get("stall_days_tolerated")),
            "stalled": stalled,
            "rate_per_week": _num(rec.get("rate_per_week")),
            "weeks_to_threshold": _num(rec.get("weeks_to_threshold")),
            "projected_resolution_utc": rec.get("projected_resolution_utc"),
            "resolvable_within_12m": rec.get("resolvable_within_12m"),
            # None is NOT False here: "we did not compute a projection" and
            # "we computed that it is unprojectable" are different statements.
            "projection_available":
                rec.get("projected_resolution_utc") is not None,

            "findings": by_pod_findings.get(pod, []),
            "caveat": POD_CAVEATS.get(pod),
            "source": "manager/state/status.json:workstreams + throughput "
                      "+ <pod>.checkpoint",
        })

    gates.sort(key=lambda g: (
        not g.get("is_validating"),
        str(g.get("stage") or "").lower() in TERMINAL_STAGES,
        str(g.get("pod_id")),
    ))

    summary = {
        "n_gates": thr.get("n_gates"),
        "n_stalled": thr.get("n_stalled"),
        "n_zero_28d": thr.get("n_zero_28d"),
        "n_unprojectable": thr.get("n_unprojectable"),
        "available": bool(thr.get("available")) if thr else False,
        "n_unmeasurable": sum(1 for g in gates
                              if g["progress_state"] == "unmeasurable"),
        "source": "manager/state/status.json:throughput",
    }
    return gates, summary


# ── P&L ───────────────────────────────────────────────────────────────


def _window_sum(daily: List[List[Any]], days: int,
                today: Optional[datetime]) -> Optional[float]:
    if today is None:
        return None
    cutoff = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    total = 0.0
    seen = False
    for row in daily:
        if not isinstance(row, list) or len(row) < 2:
            continue
        if str(row[0]) >= cutoff:
            total += _num(row[1]) or 0.0
            seen = True
    return round(total, 4) if seen else 0.0


def _pnl(src: Dict[str, Any],
         engine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sources = _d(src.get("sources"))
    rollup = _d(src.get("rollup"))
    state = _d(src.get("engine_state"))
    open_pos = _d(src.get("open_positions"))
    have_rollup = _available(sources, "rollup")
    now = _parse(src.get("generated_at_utc")) or datetime.now(UTC)

    lifetime = _d(rollup.get("lifetime"))
    daily = _l(rollup.get("daily"))

    if have_rollup:
        settled = _num(lifetime.get("settled")) or 0
        wins = _num(lifetime.get("wins")) or 0
        losses = _num(lifetime.get("losses")) or 0
        resolved = wins + losses
        realized = {
            "lifetime": _num(lifetime.get("realized_pnl")),
            "d30": _window_sum(daily, 30, now),
            "d7": _window_sum(daily, 7, now),
            "today": _window_sum(daily, 0, now),
            "settled": settled,
            "placed": _num(lifetime.get("placed")),
            "skipped": _num(lifetime.get("skipped")),
            "wins": wins, "losses": losses,
            "voids": _num(lifetime.get("voids")),
            "win_pct": round(100.0 * wins / resolved, 1) if resolved else None,
            "gross_wagered_usd": _num(lifetime.get("gross_wagered_usd")),
            "available": True, "reason": None,
        }
        roi = None
        gw = realized["gross_wagered_usd"]
        if gw:
            roi = round(100.0 * (realized["lifetime"] or 0.0) / gw, 3)
        realized["roi_pct"] = roi
    else:
        realized = {k: None for k in (
            "lifetime", "d30", "d7", "today", "settled", "placed", "skipped",
            "wins", "losses", "voids", "win_pct", "gross_wagered_usd",
            "roi_pct")}
        realized["available"] = False
        realized["reason"] = _reason(sources, "rollup")

    # ---- open exposure: engine only. Never back-derive a bankroll. ----
    # F1 (research/RUN_SUMMARY_2026-07-30): a readable snapshot is not a live
    # engine. With betting-pod-shop stopped, the last-written file still parses
    # and this block used to serve its bankroll as current truth. Exposure and
    # bankroll are only current while the engine is; when liveness says "down"
    # or "stale", the block degrades and the old numbers move to last_known.
    risk = _d(state.get("risk"))
    liveness = _d(engine if engine is not None else _engine(src)).get("liveness")
    if _available(sources, "engine_state") and risk:
        current = {
            "positions": _num(risk.get("open_positions")),
            "exposure_usd": _num(risk.get("total_exposure_usd")),
            "exposure_pct": _num(risk.get("total_exposure_pct")),
            "bankroll": _num(risk.get("bankroll")),
            "daily_pnl": _num(risk.get("daily_pnl")),
            "venue_exposure": _d(risk.get("venue_exposure")),
        }
        if liveness in ("down", "stale"):
            written = _d(state.get("_meta")).get("written_at_utc")
            open_block = {
                "available": False, "positions": None, "exposure_usd": None,
                "exposure_pct": None, "bankroll": None, "daily_pnl": None,
                "venue_exposure": {},
                "reason": "{} — last-known values as of {}".format(
                    "engine is down" if liveness == "down"
                    else "engine snapshot is stale",
                    written or "an unknown time"),
                "last_known": dict(current, written_at_utc=written),
            }
        else:
            open_block = dict(current, available=True, reason=None,
                              last_known=None)
    else:
        open_block = {
            "available": False, "positions": None, "exposure_usd": None,
            "exposure_pct": None, "bankroll": None, "daily_pnl": None,
            "venue_exposure": {},
            "reason": (_reason(sources, "engine_state")
                       or "no engine snapshot — open exposure is unknown, "
                          "not zero"),
            "last_known": None,
        }

    # ---- equity curve: realized only, and labelled as such ----
    cum, running = [], 0.0
    for row in daily:
        if not isinstance(row, list) or len(row) < 2:
            continue
        running += _num(row[1]) or 0.0
        cum.append([str(row[0]), round(running, 4)])
    points, total_points = _downsample(cum, EQUITY_MAX_POINTS)
    equity = {
        "available": have_rollup and bool(cum),
        "basis": "realized_only",
        "caption": ("Realized settlements only — open positions are excluded. "
                    "Marking them to market would need live prices, which a "
                    "file-reading dashboard deliberately does not fetch."),
        "points": points,
        "n_points": len(points),
        "downsampled_from": total_points,
        "reason": None if (have_rollup and cum) else (
            _reason(sources, "rollup") or "no settled days yet"),
    }

    # ---- per-pod attribution ----
    eng_pods = _d(state.get("pods"))
    open_rows = _l(open_pos.get("rows"))
    open_by_pod: Dict[str, Dict[str, float]] = {}
    for r in open_rows:
        r = _d(r)
        if str(r.get("status") or "").upper() != "OPEN":
            continue
        pid = str(r.get("pod_id") or "P-001")
        b = open_by_pod.setdefault(pid, {"positions": 0.0, "capital_usd": 0.0})
        b["positions"] += 1
        b["capital_usd"] += _num(r.get("position_size_usd")) or 0.0

    by_pod: List[Dict[str, Any]] = []
    for pid in sorted(set(list(_d(rollup.get("by_pod")).keys())
                          + list(eng_pods.keys())
                          + list(open_by_pod.keys()))):
        rb = _d(_d(rollup.get("by_pod")).get(pid))
        eb = _d(eng_pods.get(pid))
        ob = open_by_pod.get(pid) or {}
        wins = _num(rb.get("wins"))
        losses = _num(rb.get("losses"))
        resolved = (wins or 0) + (losses or 0)
        gw = _num(rb.get("gross_wagered_usd"))
        pnl = _num(rb.get("realized_pnl"))
        by_pod.append({
            "pod_id": pid,
            "name": eb.get("name") or pid,
            "realized_pnl": pnl,
            "placed": _num(rb.get("placed")),
            "settled": _num(rb.get("settled")),
            "skipped": _num(rb.get("skipped")),
            "wins": wins, "losses": losses, "voids": _num(rb.get("voids")),
            "win_pct": round(100.0 * wins / resolved, 1) if resolved else None,
            "gross_wagered_usd": gw,
            "roi_pct": (round(100.0 * pnl / gw, 3)
                        if (gw and pnl is not None) else None),
            "open_positions": ob.get("positions"),
            "capital_usd": ob.get("capital_usd"),
            "alloc_pct": _num(eb.get("alloc_pct")),
            "max_position_usd": _num(eb.get("max_position_usd")),
            "first_placed_utc": rb.get("first_placed_utc"),
            "last_placed_utc": rb.get("last_placed_utc"),
            "last_settled_utc": rb.get("last_settled_utc"),
            "caveat": POD_CAVEATS.get(pid),
        })
    by_pod.sort(key=lambda p: (p["realized_pnl"] is None,
                               -(p["realized_pnl"] or 0.0)))

    # ---- CLV ----
    clv_pods = []
    for pid, b in sorted(_d(_d(rollup.get("clv")).get("by_pod")).items()):
        b = _d(b)
        n = _num(b.get("n")) or 0
        clv_pods.append({
            "pod_id": pid,
            "n": int(n),
            "n_settled": int(_num(b.get("n_settled")) or 0),
            "clv_gross_mean_c": (round((_num(b.get("clv_gross_sum")) or 0) / n, 3)
                                 if n else None),
            "clv_net_maker_mean_c":
                (round((_num(b.get("clv_net_maker_sum")) or 0) / n, 3)
                 if n else None),
            "caveat": POD_CAVEATS.get(pid),
        })

    return {
        "unit": "paper_usd",
        "unit_prefix": "p$",
        "realized": realized,
        "open": open_block,
        "equity": equity,
        "by_pod": by_pod,
        "clv": {
            "available": _available(sources, "rollup") and bool(clv_pods),
            "by_pod": clv_pods,
            "log_freshness": _d(sources.get("clv")),
            "reason": None if clv_pods else (
                _reason(sources, "clv")
                or "no CLV rows in the rollup — scripts/clv_settlement.py may "
                   "not have run"),
            "source": "data/dashboard/rollup.json:clv "
                      "(fields pinn_fair_close / clv_net_maker, as written)",
        },
        "source": "data/dashboard/rollup.json + data/dashboard/engine_state.json",
    }


# ── pipeline ──────────────────────────────────────────────────────────


def _pipeline(src: Dict[str, Any]) -> Dict[str, Any]:
    sources = _d(src.get("sources"))
    rollup = _d(src.get("rollup"))
    win = _d(src.get("p022_window"))
    research_metrics = _d(src.get("research_metrics"))
    crossvenue_metrics = _d(src.get("crossvenue_metrics"))
    now = _parse(src.get("generated_at_utc")) or datetime.now(UTC)

    # ---- research discovery -> specialist review -> opportunity funnel ----
    if _available(sources, "research_metrics") and research_metrics:
        raw_research_funnel = _d(research_metrics.get("funnel"))
        research = {
            "available": True,
            "funnel": [[stage, _num(raw_research_funnel.get(stage))]
                       for stage in RESEARCH_FUNNEL_STAGES],
            "dispatch": _d(research_metrics.get("dispatch")),
            "by_source_type": _d(research_metrics.get("by_source_type")),
            "by_source_name": _d(research_metrics.get("by_source_name")),
            "by_lane": _d(research_metrics.get("by_lane")),
            "decisions": _d(research_metrics.get("decisions")),
            "operations": _d(research_metrics.get("operations")),
            "collector_health": _d(research_metrics.get("collector_health")),
            "quality_control": _d(research_metrics.get("quality_control")),
            "x_pilot": _d(research_metrics.get("x_pilot")),
            "crossvenue_pilot": (
                crossvenue_metrics
                if _available(sources, "crossvenue_metrics") and crossvenue_metrics
                else _d(research_metrics.get("crossvenue_pilot"))),
            "generated_at": research_metrics.get("generated_at"),
            "reason": None,
            "source": "data/research_intake/metrics.json",
        }
    else:
        research = {
            "available": False,
            "funnel": [[stage, None] for stage in RESEARCH_FUNNEL_STAGES],
            "dispatch": {}, "by_source_type": {}, "by_source_name": {},
            "by_lane": {}, "decisions": {}, "operations": {},
            "collector_health": {}, "quality_control": {}, "x_pilot": {},
            "crossvenue_pilot": {},
            "generated_at": None,
            "reason": (_reason(sources, "research_metrics")
                       or "research metrics are unavailable"),
            "source": "data/research_intake/metrics.json",
        }

    # ---- P-022 window funnel ----
    if _available(sources, "p022_window") and win:
        raw = _d(win.get("funnel"))
        funnel = [[stage, _num(raw.get(stage))] for stage in FUNNEL_STAGES]
        p022 = {
            "available": True,
            "state": win.get("state"),
            "legacy_state": win.get("legacy_state"),
            "alarm": bool(win.get("alarm")),
            "iso": win.get("iso"),
            # Verbatim. The checker writes a full sentence explaining the
            # silence; paraphrasing it here would lose the reason.
            "detail": win.get("detail"),
            "funnel": funnel,
            "screen_refusals": _d(win.get("screen_refusals")),
            "candidates_without_quote": _l(win.get("candidates_without_quote")),
            "discovery_errors": _d(win.get("discovery_errors")),
            "n_events": _num(win.get("n_events")),
            "n_markets": _num(win.get("n_markets")),
            "n_unresolved_events": _num(win.get("n_unresolved_events")),
            "reason": None,
            "source": "data/p022_window_check/status.jsonl (live tail)",
        }
    else:
        p022 = {
            "available": False,
            "state": None, "alarm": None, "detail": None,
            "funnel": [[stage, None] for stage in FUNNEL_STAGES],
            "screen_refusals": {}, "candidates_without_quote": [],
            "reason": (_reason(sources, "p022_window")
                       or "no window-check rows — the */30 checker has not run"),
            "source": "data/p022_window_check/status.jsonl (live tail)",
        }

    # ---- placement rate by week ----
    weekly = _d(rollup.get("weekly_by_pod"))
    weeks: Dict[str, Dict[str, float]] = {}
    per_pod_weeks: Dict[str, List[Dict[str, Any]]] = {}
    for pid, wks in weekly.items():
        rows = []
        for wk, v in sorted(_d(wks).items()):
            v = _d(v)
            placed = _num(v.get("placed")) or 0
            skipped = _num(v.get("skipped")) or 0
            looked = placed + skipped
            rows.append({
                "week": wk, "placed": placed, "skipped": skipped,
                "settled": _num(v.get("settled")) or 0,
                "placement_rate_pct": (round(100.0 * placed / looked, 4)
                                       if looked else None),
            })
            agg = weeks.setdefault(wk, {"placed": 0.0, "skipped": 0.0,
                                        "settled": 0.0})
            agg["placed"] += placed
            agg["skipped"] += skipped
            agg["settled"] += _num(v.get("settled")) or 0
        per_pod_weeks[pid] = rows

    overall_weeks = []
    for wk, v in sorted(weeks.items()):
        looked = v["placed"] + v["skipped"]
        overall_weeks.append({
            "week": wk, "placed": int(v["placed"]), "skipped": int(v["skipped"]),
            "settled": int(v["settled"]),
            "placement_rate_pct": (round(100.0 * v["placed"] / looked, 4)
                                   if looked else None),
        })

    lifetime = _d(rollup.get("lifetime"))
    lt_placed = _num(lifetime.get("placed"))
    lt_skipped = _num(lifetime.get("skipped"))
    lt_looked = (lt_placed or 0) + (lt_skipped or 0)

    placement = {
        "available": _available(sources, "rollup") and bool(overall_weeks),
        "by_week": overall_weeks,
        "by_pod_week": per_pod_weeks,
        "lifetime": {
            "placed": lt_placed, "skipped": lt_skipped,
            "by_action": _d(lifetime.get("by_action")),
            "placement_rate_pct": (round(100.0 * lt_placed / lt_looked, 4)
                                   if lt_looked else None),
        },
        "reason": None if overall_weeks else _reason(sources, "rollup"),
        "source": "data/dashboard/rollup.json:weekly_by_pod",
    }

    # ---- skip reasons ----
    sr = _d(rollup.get("skip_reasons"))
    by_day = _d(sr.get("by_day"))
    cutoff = (now - timedelta(days=SKIP_WINDOW_DAYS)).strftime("%Y-%m-%d")
    windowed: Dict[str, float] = {}
    days_in_window = 0
    for day, counts in by_day.items():
        if str(day) < cutoff:
            continue
        days_in_window += 1
        for reason, n in _d(counts).items():
            windowed[reason] = windowed.get(reason, 0) + (_num(n) or 0)

    lifetime_by_pod = _d(sr.get("lifetime_by_pod"))
    lifetime_all: Dict[str, float] = {}
    for pid, counts in lifetime_by_pod.items():
        for reason, n in _d(counts).items():
            lifetime_all[reason] = lifetime_all.get(reason, 0) + (_num(n) or 0)

    coverage = _d(rollup.get("coverage"))
    skip = {
        "available": _available(sources, "rollup") and bool(lifetime_all),
        "window_days": SKIP_WINDOW_DAYS,
        "days_in_window": days_in_window,
        "retention_days": _num(sr.get("by_day_retention_days")),
        "windowed": sorted(([r, int(n)] for r, n in windowed.items()),
                           key=lambda kv: -kv[1]),
        "lifetime": sorted(([r, int(n)] for r, n in lifetime_all.items()),
                           key=lambda kv: -kv[1]),
        "lifetime_by_pod": {
            pid: sorted(([r, int(_num(n) or 0)] for r, n in _d(c).items()),
                        key=lambda kv: -kv[1])
            for pid, c in sorted(lifetime_by_pod.items())
        },
        "coverage": {
            "rows_total": _num(coverage.get("rows_total")),
            "rows_with_skip_reason": _num(coverage.get("rows_with_skip_reason")),
            "rows_missing_pod_id": _num(coverage.get("rows_missing_pod_id")),
            "rows_unparseable": _num(coverage.get("rows_unparseable")),
        },
        "reason": None if lifetime_all else _reason(sources, "rollup"),
        "source": "data/dashboard/rollup.json:skip_reasons",
    }

    # ---- aggregate-risk shadow (enforce: false) ----
    # Sits alongside skip_reasons on purpose: while the guard is in shadow
    # mode these trades are MISSING from skip_reasons, because the pod was
    # approved and placed them. Reading the skip panel alone would say the
    # portfolio constraint had gone away.
    shr = _d(rollup.get("shadow_risk"))
    shr_life = _d(shr.get("lifetime"))
    shr_day = _d(shr.get("by_day"))
    shr_windowed: Dict[str, float] = {}
    shr_days_in_window = 0
    for day, counts in shr_day.items():
        if str(day) < cutoff:
            continue
        shr_days_in_window += 1
        for kind, n in _d(counts).items():
            shr_windowed[kind] = shr_windowed.get(kind, 0) + (_num(n) or 0)

    shadow_risk = {
        "available": bool(shr.get("available")),
        "enforcing": not bool(shr.get("available")),
        "window_days": SKIP_WINDOW_DAYS,
        "days_in_window": shr_days_in_window,
        "retention_days": _num(shr.get("by_day_retention_days")),
        "lifetime_total": _num(shr_life.get("total")),
        "usd_passed_through": _num(shr_life.get("usd_passed_through")),
        "windowed": sorted(([k, int(n)] for k, n in shr_windowed.items()),
                           key=lambda kv: -kv[1]),
        "lifetime": sorted(
            ([k, int(_num(n) or 0)] for k, n in _d(shr_life.get("by_kind")).items()),
            key=lambda kv: -kv[1]),
        "lifetime_by_pod": {
            pid: sorted(([k, int(_num(n) or 0)] for k, n in _d(c).items()),
                        key=lambda kv: -kv[1])
            for pid, c in sorted(_d(shr_life.get("by_pod")).items())
        },
        "last_utc": shr.get("last_utc"),
        "note": shr.get("note"),
        "reason": (None if shr.get("available")
                   else "no shadow log — the aggregate risk guard is "
                        "enforcing, or has not run in shadow mode yet"),
        "source": "data/dashboard/rollup.json:shadow_risk",
    }

    return {"research": research, "p022_window": p022,
            "placement": placement, "skip_reasons": skip,
            "shadow_risk": shadow_risk}


# ── ops / work ────────────────────────────────────────────────────────


def _ops(src: Dict[str, Any],
         findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    status = _d(src.get("manager_status"))
    sources = _d(src.get("sources"))
    return {
        "available": _available(sources, "manager_status"),
        "reason": _reason(sources, "manager_status"),
        "services": [_d(s) for s in _l(status.get("services"))],
        "jobs": [_d(j) for j in _l(status.get("jobs"))],
        "faults": _l(status.get("faults")),
        "errors": _d(status.get("errors")),
        "evmap": _d(status.get("evmap")) or None,
        "invariants": _d(status.get("invariants")),
        "findings": findings,
        "collected_at": status.get("collected_at"),
        "host": status.get("host"),
        "registry_version": status.get("registry_version"),
        "source": "manager/state/status.json + manager.checks.run_checks",
    }


def _work(src: Dict[str, Any]) -> Dict[str, Any]:
    status = _d(src.get("manager_status"))
    return {
        "work_today": _d(status.get("work_today")) or None,
        "accumulating": [
            {"id": _d(w).get("id"), "name": _d(w).get("name"),
             "summary": _d(w).get("summary")}
            for w in _l(status.get("workstreams"))
            if _d(w).get("blocked_on") == "time"
        ],
        "source": "manager/state/status.json:workstreams + work_today",
    }


# ── findings ──────────────────────────────────────────────────────────

#: ``manager.checks.SEVERITY_ORDER`` is the authority, but it lists only
#: critical/warn/action/info while ``run_checks`` also emits ``error`` (seen on a
#: live snapshot: "P-001 is in pods.active but has no registry entry"). An
#: unknown severity must sort NEAR THE TOP, not to the bottom — burying a
#: severity nobody enumerated is how it goes unnoticed.
_SEVERITY_ORDER = {"critical": 0, "error": 1, "warn": 2, "action": 3, "info": 4}
_UNKNOWN_SEVERITY_RANK = 1.5

#: Severities that belong on the front page's alarm panel.
LOUD_SEVERITIES = ("critical", "error", "warn")


def _severity_rank(severity: Any, order: Dict[str, int]) -> float:
    key = str(severity or "info").lower()
    if key in order:
        return float(order[key])
    if key in _SEVERITY_ORDER:
        return float(_SEVERITY_ORDER[key])
    return _UNKNOWN_SEVERITY_RANK


def _findings(src: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Delegate to ``manager.checks.run_checks``. Reimplement nothing.

    If checks cannot be imported or throws, return a single synthetic finding
    saying so — a silent empty list would read as "all clear", which is the one
    answer that must never be fabricated.
    """
    status = src.get("manager_status")
    if not isinstance(status, dict):
        return []
    try:
        from manager import checks as _checks  # noqa: PLC0415
        order = getattr(_checks, "SEVERITY_ORDER", _SEVERITY_ORDER)
        found = _checks.run_checks(status)
        out = [f.to_dict() for f in found]
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_checks failed: %s", exc)
        return [{
            "key": "dashboard.checks_failed",
            "severity": "warn",
            "title": "Health checks could not run",
            "detail": ("manager.checks.run_checks raised {}: {}. The findings "
                       "list below is EMPTY because it could not be computed — "
                       "not because nothing is wrong."
                       .format(type(exc).__name__, exc)),
            "fix": "Run `python3 manager/checks.py` to see the traceback.",
        }]
    out.sort(key=lambda f: (_severity_rank(f.get("severity"), order),
                            str(f.get("key"))))
    return out


# ── the builder ───────────────────────────────────────────────────────

SECTIONS = ("now", "gates", "pnl", "pipeline", "ops", "work")


def build_v2(src: Dict[str, Any],
             sections: Optional[List[str]] = None) -> Dict[str, Any]:
    """Assemble the v2 payload from ``dashboard_sources.load_all()``'s output.

    Pure: no I/O, no clock (``src["generated_at_utc"]`` is the reference time).
    ``sections`` limits the payload for partial mobile refetches; ``schema``,
    ``sources``, ``mode`` and ``engine`` are always included because the ribbon
    and freshness chip render on every view.
    """
    src = _d(src)
    want = set(sections) if sections else set(SECTIONS)

    findings = _findings(src)
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at_utc": src.get("generated_at_utc"),
        "root": src.get("root"),
        "sources": _d(src.get("sources")),
        "mode": _mode(_d(src.get("manager_status")), _d(src.get("sources"))),
        "engine": _engine(src),
        # Everything loud enough for the front page, already severity-sorted.
        # Unknown severities are included by construction (see _severity_rank) —
        # a severity nobody enumerated must not be silently dropped.
        "alarms": [f for f in findings
                   if str(f.get("severity", "")).lower() in LOUD_SEVERITIES
                   or str(f.get("severity", "")).lower() not in _SEVERITY_ORDER],
    }

    if "gates" in want or "now" in want:
        gates, gsummary = _gates(src, findings)
        payload["gates"] = gates
        payload["gates_summary"] = gsummary
    if "pnl" in want or "now" in want:
        payload["pnl"] = _pnl(src, payload["engine"])
    if "pipeline" in want or "now" in want:
        payload["pipeline"] = _pipeline(src)
    if "ops" in want or "now" in want:
        payload["ops"] = _ops(src, findings)
    if "work" in want:
        payload["work"] = _work(src)

    payload["sections"] = sorted(want)
    return payload
