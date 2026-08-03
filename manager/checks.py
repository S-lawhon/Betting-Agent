#!/usr/bin/env python3
"""Turn collected facts into findings. Deterministic — no LLM anywhere here.

Severity ladder:
    critical -> live production is broken or a locked kill-rule fired.
                Pushes to phone immediately.
    warn     -> something needs attention today but nothing is on fire.
                Included in the daily brief; pushed only if it persists.
    action   -> waiting on YOU. The "what do I jump back into" list.
    info     -> context for the brief; never pushed.

The severity assignment is the whole game. Anything that fires daily without
being actionable trains you to ignore the channel, at which point the alerting
protects nothing. When in doubt, demote.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

UTC = timezone.utc
HERE = Path(__file__).resolve().parent

SEVERITY_ORDER = {"critical": 0, "warn": 1, "action": 2, "info": 3}


class Finding:
    def __init__(self, key: str, severity: str, title: str,
                 detail: str = "", workstream: Optional[str] = None,
                 fix: Optional[str] = None, value: Any = None):
        self.key = key                # stable id, used for alert dedupe
        self.severity = severity
        self.title = title
        self.detail = detail.strip()
        self.workstream = workstream
        self.fix = (fix or "").strip() or None
        self.value = value

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in {
            "key": self.key, "severity": self.severity, "title": self.title,
            "detail": self.detail, "workstream": self.workstream,
            "fix": self.fix, "value": self.value,
        }.items() if v not in (None, "")}

    def __repr__(self) -> str:
        return "<{} {}>".format(self.severity, self.key)


def _parse_date(value: Any) -> Optional[date]:
    """A bare YAML date, already stringified by the collector."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


# Stages at which a pod's fate is SETTLED. Once a pod is killed or shelved the
# decision is made and recorded in the registry's `retired:` block — so the
# checks that exist to PROMPT a decision (the gate) or to flag a possibly-
# accidental state (the kill switch) must stop firing daily. A killed pod
# re-failing its now-frozen gate tape every morning, and its deliberately-left
# kill file being re-flagged as "needs a look" every morning, is exactly the
# never-actionable daily noise the module docstring warns trains you to ignore
# the channel. Concrete case: P-016, killed 2026-07-21 per its pre-registered
# gate, kill file intentionally left in place per registry.yaml `retired:`.
#
# `parked` is deliberately NOT terminal — a parked pod may resume, so its gate
# prompts should survive.
_TERMINAL_STAGES = frozenset({"killed", "shelved"})


def _pod_stage(snap: Dict[str, Any], pod_id: str) -> Optional[str]:
    """The registry `stage` for a pod, as captured into the snapshot.

    Reads the collected `workstreams` list (collect.py stamps `stage` there),
    NOT the registry argument — the brief renders with an empty registry, so the
    snapshot is the only stage source that is always present. If the workstreams
    probe faulted the pod is simply unknown here, so callers fall back to their
    normal (louder) behaviour rather than wrongly going quiet on a live pod.
    """
    for ws in snap.get("workstreams", []) or []:
        if ws.get("id") == pod_id:
            return ws.get("stage")
    return None


def _is_terminal(snap: Dict[str, Any], pod_id: str) -> bool:
    return _pod_stage(snap, pod_id) in _TERMINAL_STAGES


# --------------------------------------------------------------------------
# individual check groups
# --------------------------------------------------------------------------

def check_services(snap: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    for svc in snap.get("services", []):
        sid = svc.get("id", "?")
        active = svc.get("active")

        if active == "n/a":
            continue  # not a systemd host; nothing to say

        # A unit that is not installed reports ActiveState=inactive, exactly
        # like one that crashed — so the down-check below cannot tell them
        # apart, and a registry entry that ships ahead of its unit install
        # pages CRITICAL for something no restart will fix. That happened to
        # betting-strategy-agents on 2026-08-01: the deploy carried the
        # registry entry, the unit was installed 13 minutes later, and the
        # alerter paged in between. LoadState is the discriminator.
        if svc.get("load") == "not-found":
            out.append(Finding(
                key="service.{}.not_installed".format(sid),
                severity="warn",
                title="{} is in registry.yaml but not installed on the host".format(sid),
                detail=("No unit file is loaded. This is a deploy that ran ahead "
                        "of its install step, not a service that died — nothing "
                        "is down that was ever up."),
                fix=("ssh root@129.212.176.202 'cp /opt/betting-pod-shop/scripts/"
                     "systemd/{0}.service /etc/systemd/system/ && systemctl "
                     "daemon-reload && systemctl enable --now {0}'".format(sid)),
                value="not-found"))
            continue

        if active not in ("active", None):
            out.append(Finding(
                key="service.{}.down".format(sid),
                severity="critical",
                title="{} is {}".format(sid, active),
                detail=svc.get("description", ""),
                fix="ssh root@129.212.176.202 'systemctl status {} --no-pager'".format(sid),
                value=active))
            continue

        # A unit can be "active" while its loop is wedged. The heartbeat file
        # is the real liveness signal — this is how a hung scan cycle shows up.
        hb = svc.get("heartbeat") or {}
        if sid == "betting-strategy-agents":
            agent = hb.get("last_row") or {}
            failures = int(agent.get("failed") or 0)
            consecutive = int(agent.get("consecutive_failed_passes") or 0)
            queue_age = agent.get("oldest_queue_age_minutes")
            if failures:
                out.append(Finding(
                    key="service.{}.request_failures".format(sid),
                    severity="warn",
                    title="{} failed {} request(s) in its latest pass".format(
                        sid, failures),
                    detail=("{} consecutive pass(es) have failures. Inspect the "
                            "failed archive before trusting registry progress."
                            .format(consecutive)),
                    value=failures))
            if isinstance(queue_age, (int, float)) and queue_age > 30:
                out.append(Finding(
                    key="service.{}.queue_stale".format(sid),
                    severity="warn",
                    title="{} has a queued request {:.0f} min old".format(
                        sid, queue_age),
                    detail="A fresh process heartbeat does not prove the queue is draining.",
                    value=queue_age))
        age = hb.get("age_minutes")
        limit = hb.get("max_stale_minutes")
        if age is not None and limit and age > limit:
            # A daily-loss halt silences the heartbeat file deliberately: the
            # guard skips the scan cycle so no telemetry rows are written, yet
            # the loop is alive and logging a "guard halted" line every cycle.
            # If that line is fresh in the journal, the silence is expected —
            # demote to an info note instead of paging a wedged-loop CRITICAL.
            # A genuine wedge stops the halt lines too, so this cannot mask one.
            halt = hb.get("halt") or {}
            if halt.get("halted"):
                out.append(Finding(
                    key="service.{}.halted".format(sid),
                    severity="info",
                    title="{} trading halted (daily loss) — loop alive".format(sid),
                    detail=("Aggregate-risk guard has halted trading, so the "
                            "engine is skipping scan cycles and the heartbeat "
                            "file {} is stale by {:.0f} min BY DESIGN. The loop "
                            "is confirmed alive: a halt line was logged {:.0f} "
                            "min ago (limit {}). Clears at the next daily P&L "
                            "reset (00:00 UTC)."
                            .format(hb.get("file"), age,
                                    halt.get("halt_age_minutes") or 0,
                                    halt.get("max_silent_journal_minutes"))),
                    value=halt.get("reason")))
                continue
            # Some heartbeats are only meaningful in a time window (P-016 only
            # quotes during live MLB games; silence at 4am is correct).
            if hb.get("only_during") == "mlb_games_window":
                if not _in_games_window():
                    continue
                # Inside the window is not sufficient. _in_games_window opens at
                # noon ET to cover possible day games, but a typical slate's
                # first pitch is 18:40 ET — so for ~7 hours the maker is
                # correctly silent and this check fired CRITICAL anyway
                # (observed 2026-07-21: "silent for 912 min").
                #
                # The check exists to catch a WEDGED LOOP. A loop that has not
                # started yet is not wedged. So: if the heartbeat is older than
                # the window itself, the maker simply has not begun quoting
                # today — say nothing. Once it HAS quoted inside the window and
                # then goes quiet past the limit, that is the real failure and
                # it still fires.
                #
                # Deliberately does not hardcode a later start hour: that would
                # just replace one guess with another, and day games do exist.
                if age > _minutes_since_window_open():
                    continue
            out.append(Finding(
                key="service.{}.stale".format(sid),
                severity="critical",
                title="{} active but silent for {:.0f} min".format(sid, age),
                detail=("Heartbeat file {} has not been written in {:.0f} minutes "
                        "(limit {}). systemd reports the unit as active, so the "
                        "process is alive but its loop may be wedged."
                        .format(hb.get("file"), age, limit)),
                fix="ssh root@129.212.176.202 'journalctl -u {} -n 100 --no-pager'".format(sid),
                value=age))

        restarts = svc.get("restarts")
        if isinstance(restarts, int) and restarts > 0:
            out.append(Finding(
                key="service.{}.restarts".format(sid),
                severity="warn",
                title="{} has auto-restarted {}x".format(sid, restarts),
                detail="systemd NRestarts > 0 means the process crashed and was "
                       "revived, not that you restarted it manually.",
                value=restarts))
    return out


# The MLB slate window is defined in ET, because that is the timezone MLB
# schedules games in — NOT in UTC.
#
# This was originally WINDOW_OPEN_HOUR_UTC = 16, described as "noon ET". That
# is only true under EDT. Once DST ends (2026-11-01) 16:00 UTC is 11:00 EST,
# so the window would silently open an hour early and every elapsed-time
# calculation would be 60 minutes too large — which, in the one place this
# feeds (the "has the maker started quoting yet" test in check_services), errs
# toward firing CRITICAL on a maker that is correctly still idle.
#
# Anchoring to America/New_York makes the boundary follow the actual clock the
# schedule uses, at DST and forever after.
WINDOW_OPEN_HOUR_ET = 12       # noon ET — earliest a day game could start
WINDOW_CLOSE_HOUR_ET = 2       # 2am ET — latest a west-coast game could run to

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:               # pragma: no cover - no tzdata on this host
    ET = None


def _to_et(at: Optional[datetime] = None) -> datetime:
    """Current (or supplied) time expressed in ET.

    Falls back to a fixed UTC-4 only if tzdata is missing, which reproduces
    the old EDT-only behaviour rather than crashing the monitor.
    """
    now = at or datetime.now(UTC)
    if now.tzinfo is None:      # defensive: treat naive input as UTC
        now = now.replace(tzinfo=UTC)
    return now.astimezone(ET if ET is not None else timezone(timedelta(hours=-4)))


def _in_games_window(at: Optional[datetime] = None) -> bool:
    """Rough MLB slate window, noon-2am ET."""
    hour = _to_et(at).hour
    return hour >= WINDOW_OPEN_HOUR_ET or hour < WINDOW_CLOSE_HOUR_ET


def _minutes_since_window_open(at: Optional[datetime] = None) -> float:
    """How long the games window has been open, in minutes.

    Used to distinguish "hasn't started quoting yet" from "started and
    stalled". Past midnight ET the window opened at noon ET the previous day,
    so the elapsed time carries across midnight.
    """
    et = _to_et(at)
    hours = et.hour - WINDOW_OPEN_HOUR_ET
    if hours < 0:                       # past midnight ET, window opened yesterday
        hours += 24
    return hours * 60 + et.minute


def check_jobs(snap: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    for job in snap.get("jobs", []):
        jid = job.get("id", "?")
        if job.get("state") == "uncheckable":
            # We did not look, so we know nothing. Say that out loud. Staying
            # silent here is what let the Mac-hosted weather jobs report a
            # confident "does not exist" from the droplet for weeks.
            out.append(Finding(
                key="job.{}.uncheckable".format(jid),
                severity="info",
                title="{} could not be checked (state unknown)".format(jid),
                detail=("{}\nHost: {}\nSchedule: {}\n\n{}\n\n"
                        "This is an admitted gap, NOT a healthy result and NOT "
                        "a missing output. Run manager/refresh.py from the Mac "
                        "so local jobs are measured where they actually live."
                        .format(job.get("description", ""), job.get("host"),
                                job.get("schedule"),
                                job.get("uncheckable_reason", ""))),
                value=None))
            continue
        if not job.get("measurable"):
            # Output path is visible from here but nothing is there to measure.
            continue
        if job.get("stale"):
            # Honour the registry's declared severity. The old map only listed
            # critical/warn, so a job declared `severity: info` (e.g.
            # rotate_active_log, which is size-triggered and benign when idle)
            # fell through to the "warn" default and was silently promoted into
            # "needs a look" every day. Pass through any valid severity; default
            # to info, matching the collector's own default for the field.
            declared = job.get("severity", "info")
            sev = declared if declared in SEVERITY_ORDER else "info"
            out.append(Finding(
                key="job.{}.stale".format(jid),
                severity=sev,
                title="{} output is {:.0f}h stale".format(jid, job.get("age_hours") or 0),
                detail=("{}\nSchedule: {}\nOutput: {}\nThreshold: {}h\n\n"
                        "Note: a cron job exiting 0 is not evidence it did anything. "
                        "Only fresh output is."
                        .format(job.get("description", ""), job.get("schedule"),
                                job.get("output"), job.get("max_stale_hours"))),
                fix=job.get("note") or None,
                value=job.get("age_hours")))
    return out


def check_invariants(snap: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    inv = snap.get("invariants", {}) or {}

    non_paper = inv.get("non_paper_pods") or []
    if non_paper:
        out.append(Finding(
            key="invariant.non_paper",
            severity="critical",
            title="POD NOT IN PAPER MODE: {}".format(", ".join(non_paper)),
            detail=("Real money has never been deployed on this system. A pod "
                    "reporting a non-paper mode is either a config mistake or an "
                    "unreviewed promotion. Verify before the next scan cycle."),
            fix="Check `mode:` for these pods in config_multi_pod.yaml on the droplet.",
            value=non_paper))

    if inv.get("kill_switch_present"):
        # For a killed/shelved pod the kill file is the intended end state — the
        # `retired:` block says to leave it in place until the unit is repurposed
        # or decommissioned. So it is quiet context (info: never pushed, no
        # "needs a look"), not a warning. It stays a warn for a NON-terminal pod,
        # where an unexplained kill file is genuinely alarming.
        killed = _is_terminal(snap, "P-016")
        out.append(Finding(
            key="invariant.kill_switch",
            severity="info" if killed else "warn",
            title=("P-016 kill switch engaged (expected — pod retired)"
                   if killed else "P-016 kill switch is engaged"),
            detail=("data/KILL_MAKER exists, so the live maker is halted. This "
                    "is the intended resting state for a retired pod — the kill "
                    "file is left in place until the unit is repurposed or "
                    "decommissioned."
                    if killed else
                    "data/KILL_MAKER exists, so the live maker is halted. "
                    "Expected if you killed it deliberately; alarming if not."),
            workstream="P-016"))

    # P-013 must stay dead. It is the reason P-015's rule is pre-registered.
    enabled = (inv.get("config_fingerprint") or {}).get("enabled") or {}
    if enabled.get("P-013"):
        out.append(Finding(
            key="invariant.p013_enabled",
            severity="critical",
            title="P-013 is ENABLED — it was killed for a significant negative edge",
            detail=("P-013 (Kalshi-Deribit crypto options) lost $2,094 with a "
                    "per-bet CI of [-51%, -11%] and backwards calibration. It was "
                    "disabled at the 2026-07-18 pivot. If it is enabled again, "
                    "something overwrote the config."),
            fix="Set pods.P-013.enabled: false and remove it from pods.active.",
            workstream="P-013"))
    return out


def check_systemd_units(snap: Dict[str, Any]) -> List[Finding]:
    """Alert when a versioned unit is missing or differs on the droplet."""
    block = snap.get("systemd_units") or {}
    if not block.get("available"):
        return []
    out: List[Finding] = []
    for rec in block.get("units") or []:
        state = rec.get("state")
        if state == "ok":
            continue
        unit = rec.get("unit") or rec.get("id") or "?"
        severity = rec.get("severity", "warn")
        if severity not in SEVERITY_ORDER:
            severity = "warn"
        if state == "not_installed":
            title = "systemd unit {} is not installed".format(unit)
            detail = "Repo unit exists at {} but no installed copy exists.".format(
                rec.get("source"))
        elif state == "differs":
            title = "systemd unit {} differs from repo".format(unit)
            detail = "Installed unit {} differs from {}.".format(
                rec.get("installed"), rec.get("source"))
        else:
            title = "systemd source missing for {}".format(unit)
            detail = "Configured source {} does not exist.".format(rec.get("source"))
        out.append(Finding(
            key="systemd.{}.{}".format(unit, state),
            severity=severity,
            title=title,
            detail=detail,
            value=state))
    return out


def check_config_drift(snap: Dict[str, Any],
                       local_fp: Optional[Dict[str, Any]]) -> List[Finding]:
    """Compare the live droplet config against the local working copy.

    Real incident this guards against (found 2026-07-20): the Mac config still
    held the pre-pivot pod set with P-013 enabled and no P-015/P-016, while the
    droplet held the correct set. Deploying the Mac copy would have resurrected
    a known-losing pod and dropped both pods under active validation.
    """
    remote_fp = (snap.get("invariants", {}) or {}).get("config_fingerprint") or {}
    if not local_fp or not local_fp.get("exists") or not remote_fp.get("exists"):
        return []
    if "active" not in local_fp or "active" not in remote_fp:
        return []

    r_active, l_active = set(remote_fp["active"]), set(local_fp["active"])
    r_en, l_en = remote_fp.get("enabled", {}), local_fp.get("enabled", {})

    only_live = sorted(r_active - l_active)
    only_local = sorted(l_active - r_active)
    flag_diff = sorted(
        k for k in set(r_en) | set(l_en) if r_en.get(k) != l_en.get(k))

    if not (only_live or only_local or flag_diff):
        return []

    lines = []
    if only_live:
        lines.append("Active on the DROPLET but missing locally: {}"
                     .format(", ".join(only_live)))
    if only_local:
        lines.append("Active LOCALLY but not on the droplet: {}"
                     .format(", ".join(only_local)))
    for k in flag_diff:
        lines.append("  {}: droplet enabled={}  local enabled={}"
                     .format(k, r_en.get(k), l_en.get(k)))

    # Deploying local over live would resurrect a killed pod — that's critical,
    # not cosmetic drift.
    dangerous = l_en.get("P-013") and not r_en.get("P-013")
    return [Finding(
        key="config.drift",
        severity="critical" if dangerous else "warn",
        title="config_multi_pod.yaml has diverged between Mac and droplet",
        detail=("\n".join(lines) + (
            "\n\nDANGER: the local copy enables P-013, which the droplet "
            "correctly has disabled. Deploying the local config would restart a "
            "pod that lost $2,094 with a significantly negative edge, and would "
            "drop P-015/P-016 from the running set."
            if dangerous else
            "\n\nThe droplet copy is authoritative — it is what is actually "
            "running. Reconcile toward it, not away from it.")),
        fix=("scp root@129.212.176.202:/opt/betting-pod-shop/config_multi_pod.yaml "
             "/tmp/live_config.yaml && diff /tmp/live_config.yaml config_multi_pod.yaml"),
        value={"only_live": only_live, "only_local": only_local, "flags": flag_diff})]


def _gate_progress(snap: Dict[str, Any], pod_id: str,
                   gate: Dict[str, Any]) -> Optional[float]:
    """Gate progress, preferring a DERIVED value over the registry's own number.

    When a gate names a ``source``, that source's checkpoint script is the
    authority and the YAML figure is only a fallback for gates that have no
    reader yet.  This ordering is the fix for a real integrity bug: P-017's
    ``progress: 1`` was typed by hand on the day it entered its first tournament
    and never moved, so the gate measured tournaments *entered* rather than
    *settled* — satisfiable without a single observation of the thing under test.

    A gate that declares a source but whose reader fails returns None rather than
    silently falling back, because a stale hand-maintained number is exactly what
    this is meant to stop being trusted.
    """
    source = gate.get("source")
    if not source:
        val = gate.get("progress")
        return val if isinstance(val, (int, float)) else None

    key = {"p017_checkpoint": "p017", "p001_checkpoint": "p001",
           "p015_checkpoint": "p015", "p022_checkpoint": "p022",
           "p014_checkpoint": "p014"}.get(source)
    if not key:
        return None
    block = snap.get(key) or {}
    if not block.get("available"):
        return None
    cp = block.get("checkpoint") or {}
    val = cp.get("progress")
    return val if isinstance(val, (int, float)) else None


def check_gates(snap: Dict[str, Any], registry: Dict[str, Any]) -> List[Finding]:
    """Evaluate pre-registered gates. These rules are locked — read, don't reinterpret."""
    out: List[Finding] = []

    # ---- P-016 maker gate ----
    # The gate is a DECISION PROMPT. Once P-016 is killed the decision is made
    # (registry `retired:` block), so re-evaluating its frozen post-kill fill
    # tape and re-announcing "FAILED its gate" every day is pure noise — skip
    # the whole block for a terminal pod.
    maker = snap.get("maker", {}) or {}
    if maker.get("available") and not _is_terminal(snap, "P-016"):
        fills = maker.get("fills_clean") or 0
        thresh = maker.get("threshold", 500)
        if maker.get("gate_met"):
            out.append(Finding(
                key="gate.P-016.met",
                severity="warn",
                title="P-016 GATE REACHED — {} fills, decision due".format(fills),
                detail=("Pre-registered gate: >=500 fills AND positive fee-adjusted "
                        "+5m markout AND robust to excluding the best day.\n"
                        "  markout mean:            {}\n"
                        "  markout ex-best-day:     {}\n"
                        "  best day:                {}\n\n"
                        "All three conditions are satisfied. This is a decision "
                        "point, not an auto-promotion — P-016 stays in paper until "
                        "you act."
                        .format(maker.get("markout_mean"),
                                maker.get("markout_mean_ex_best_day"),
                                maker.get("best_day"))),
                workstream="P-016", value=fills))
        elif fills >= thresh:
            out.append(Finding(
                key="gate.P-016.failed",
                severity="warn",
                title="P-016 reached {} fills but FAILED its gate".format(fills),
                detail=("Markout mean {} / ex-best-day {}. The pre-registered rule "
                        "says: else kill. Do not renegotiate it now — that is "
                        "exactly what happened with P-013."
                        .format(maker.get("markout_mean"),
                                maker.get("markout_mean_ex_best_day"))),
                workstream="P-016", value=fills))

        ratio = maker.get("one_sided_ratio")
        if ratio is not None and ratio > 0.85 and fills > 30:
            out.append(Finding(
                key="gate.P-016.one_sided",
                severity="warn",
                title="P-016 fills are {:.0%} one-sided".format(ratio),
                detail=("One-sided fills are the anchor-contamination signature "
                        "that produced 484 bad fills on the first night. It means "
                        "both quotes are landing on the same side of the book and "
                        "you are accumulating directional risk, not making markets."),
                fix="ssh root@129.212.176.202 '/opt/betting-pod-shop/venv/bin/python "
                    "-m scripts.maker_diagnostics'",
                workstream="P-016", value=ratio))

    # ---- P-015 tennis gate (locked rule) ----
    p015 = snap.get("p015", {}) or {}
    cp = p015.get("checkpoint") or {}
    if cp:
        n = cp.get("n") or cp.get("settled") or 0
        z = cp.get("z")
        edge = cp.get("edge")
        if isinstance(z, (int, float)) and z <= -2.0:
            out.append(Finding(
                key="gate.P-015.hard_kill",
                severity="critical",
                title="P-015 HARD KILL TRIGGERED (z={:.2f})".format(z),
                detail=("The locked decision rule fires a HARD KILL at z <= -2.0 at "
                        "ANY n. Current n={}, edge={}.\n\n"
                        "This rule was pre-registered on 2026-07-20 specifically so "
                        "it could not be renegotiated after a bad run. Disable the "
                        "pod."
                        .format(n, edge)),
                fix="Set pods.P-015.enabled: false in the droplet config and restart.",
                workstream="P-015", value=z))
        elif isinstance(n, int) and n >= 240 and edge and edge > 0 and \
                isinstance(z, (int, float)) and z >= 2.0:
            out.append(Finding(
                key="gate.P-015.promote",
                severity="warn",
                title="P-015 meets its PROMOTE criteria (n={}, z={:.2f})".format(n, z),
                detail="n>=240, edge>0, z>=2.0. This is the pre-registered "
                       "promotion checkpoint. Human decision required.",
                workstream="P-015", value=n))
        elif isinstance(n, int) and n >= 120 and edge is not None and edge <= 0:
            out.append(Finding(
                key="gate.P-015.kill",
                severity="warn",
                title="P-015 reached n={} with edge <= 0 — rule says KILL".format(n),
                workstream="P-015", value=n))
    return out


def check_registry_reconciliation(snap: Dict[str, Any],
                                  registry: Dict[str, Any]) -> List[Finding]:
    """Does what the registry SAYS is trading match what IS trading?

    Real incidents this guards against, all on 2026-07-20/21:
      - The registry said P-017 was "built but NOT deployed" while it was live
        and had placed 38 positions. Nobody noticed for about a day.
      - It said "nothing is scheduled" for the book capture hours after the
        capture daemon was deployed.
      - It said the MLB props collector had no timer while a timer was running.

    Three misses in two days, each one a confident statement that was false.
    Note the direction of the fix: runtime config stays AUTHORITATIVE and the
    registry is checked against it. Making the registry drive what loads would
    have turned that same staleness into an outage rather than a bad report —
    see research/SPEC_Pod_Tiers_2026-07-21.md section 3.
    """
    out: List[Finding] = []
    fp = (snap.get("invariants", {}) or {}).get("config_fingerprint") or {}
    if not fp.get("exists") or "active" not in fp:
        return out           # nothing measured; check_config_drift says so

    active = set(fp["active"] or [])
    pods = {w.get("id"): w for w in registry.get("workstreams", [])
            if str(w.get("id", "")).startswith("P-")}
    svc_state = {s.get("id"): s.get("active") for s in snap.get("services", [])}

    # (a) trading but unregistered — we cannot report on what we don't know
    for pid in sorted(active - set(pods)):
        out.append(Finding(
            key="registry.unregistered.{}".format(pid),
            severity="error",
            title="{} is in pods.active but has no registry entry".format(pid),
            detail=("The engine is loading this pod, so it can place trades, "
                    "but the manager has no gate, tier or owner recorded for "
                    "it. Nothing will report on it."),
            workstream=pid,
            fix="Add a workstream entry for {} to manager/registry.yaml".format(pid)))

    # (b) recorded as trading, isn't. This is the P-017 miss, inverted.
    for pid, w in sorted(pods.items()):
        tier = w.get("tier")
        if tier not in ("validating", "production"):
            continue
        if pid in active or w.get("service"):
            continue
        out.append(Finding(
            key="registry.not_running.{}".format(pid),
            severity="error",
            title="{} is tier={} but is not running".format(pid, tier),
            detail=("The registry records this pod as trading, but it is "
                    "absent from pods.active and declares no `service:`. "
                    "Either it should be running and isn't, or its tier is "
                    "stale — both are wrong and only one is obvious."),
            workstream=pid,
            fix="Either add {} to pods.active, or set tier: none".format(pid)))

    # (c) declares a service that isn't up
    for pid, w in sorted(pods.items()):
        svc = w.get("service")
        if not svc:
            continue
        state = svc_state.get(svc)
        if state is None or state in ("active", "n/a"):
            continue
        out.append(Finding(
            key="registry.service_down.{}".format(pid),
            severity="critical",
            title="{} declares service {} which is {}".format(pid, svc, state),
            workstream=pid,
            fix="ssh root@129.212.176.202 'systemctl status {} --no-pager'".format(svc)))

    # (d) gate cleared but still validating — promotion is a DECISION, and a
    #     decision nobody is prompted to make is a decision that never happens.
    for pid, w in sorted(pods.items()):
        if w.get("tier") != "validating":
            continue
        gate = w.get("gate") or {}
        threshold = gate.get("threshold")
        progress = _gate_progress(snap, pid, gate)
        if not isinstance(threshold, (int, float)):
            continue
        if not isinstance(progress, (int, float)) or progress < threshold:
            continue
        out.append(Finding(
            key="registry.promotion_due.{}".format(pid),
            severity="info",
            title="{} has reached its gate ({}/{}) and is still tier=validating"
                  .format(pid, progress, threshold),
            detail=gate.get("question", ""),
            workstream=pid,
            fix="Evaluate the gate and set tier: production, or record why not"))

    return out


def check_workstreams(snap: Dict[str, Any]) -> List[Finding]:
    """The 'what needs me' list. Driven entirely by registry blocked_on."""
    out: List[Finding] = []
    today = datetime.now(UTC).date()
    for ws in snap.get("workstreams", []):
        wid, blocked = ws.get("id"), ws.get("blocked_on")

        # `recheck_after` was documentation only until 2026-08-01: three
        # workstreams carried one and NO code read the field, so a date that
        # came due passed in silence. That is the same shape as a `monitor`
        # step with no monitor — the note is written, and nothing opens it.
        # Firing as `action` puts it on the "what needs me" list, which is
        # where a due recheck belongs; before the date it stays invisible.
        due = _parse_date(ws.get("recheck_after"))
        if due and due <= today:
            overdue = (today - due).days
            out.append(Finding(
                key="ws.{}.recheck_due".format(wid),
                severity="action",
                title="{} — recheck due {}{}".format(
                    wid, ws.get("recheck_after"),
                    " ({}d overdue)".format(overdue) if overdue else " (today)"),
                detail=(ws.get("action_required")
                        or "registry.yaml set recheck_after for this workstream; "
                           "the date has arrived. Re-read its gate and either act, "
                           "move the date, or change blocked_on."),
                workstream=wid,
                value=ws.get("recheck_after")))

        if blocked == "human" and ws.get("action_required"):
            out.append(Finding(
                key="ws.{}.action".format(wid),
                severity="action",
                title="{} — {}".format(wid, ws.get("name")),
                detail=ws["action_required"],
                workstream=wid))

        for oq in ws.get("open_questions") or []:
            if oq.get("blocked_on") == "human":
                out.append(Finding(
                    key="ws.{}.oq.{}".format(wid, oq.get("id")),
                    severity="action",
                    title="{} open question: {}".format(wid, oq.get("id")),
                    detail=(oq.get("text") or "").strip(),
                    workstream=wid))
            elif oq.get("blocked_on") == "external":
                out.append(Finding(
                    key="ws.{}.oq.{}".format(wid, oq.get("id")),
                    severity="info",
                    title="{} waiting on external: {}".format(wid, oq.get("id")),
                    detail=(oq.get("text") or "").strip(),
                    workstream=wid))
    return out


def check_throughput(snap: Dict[str, Any]) -> List[Finding]:
    """A gate producing NOTHING must not look like a gate patiently waiting.

    This is the generalisation of the P-022 silence problem. P-022 sat dead for
    four days and nothing noticed, because silence is also its correct
    behaviour between tournaments. The same shape held for P-001 (still
    pricing one day's game and betting another, so admissible rows accrue at
    zero) and P-015 (5 real settled trades that its gate reader cannot see).

    A stall is raised only once the gap exceeds what that gate's own event
    cadence can explain — `manager.throughput.STALL_DAYS`. Those tolerances
    gate nothing and change no rule; they only decide when to speak.
    """
    out: List[Finding] = []
    block = snap.get("throughput") or {}
    if not block.get("available"):
        return out
    for rec in block.get("records") or []:
        pod = rec.get("id")
        if rec.get("stalled"):
            out.append(Finding(
                key="throughput.{}.stalled".format(pod),
                severity="warn",
                title="{} gate has stalled".format(pod),
                detail=("no settled observation for {} days (cadence tolerates "
                        "{}). Last: {}. This is the P-022 failure mode — a gate "
                        "producing nothing looks identical to a gate waiting."
                        .format(rec.get("days_since_last_settlement"),
                                rec.get("stall_days_tolerated"),
                                rec.get("last_settlement_utc"))),
                workstream=pod))
        if rec.get("resolvable_within_12m") is False:
            out.append(Finding(
                key="throughput.{}.inert".format(pod),
                severity="warn",
                title="{} cannot resolve within 12 months".format(pod),
                detail=("at the realised rate of {}/week it needs {} more "
                        "weeks (projected {}). A gate that cannot resolve is "
                        "not conservative — it is inert, and it consumes "
                        "attention while producing nothing. NOTE the rate is "
                        "measured from {} observation(s) over the pod's last "
                        "{:.0f} live day(s); a thin or lumpy sample can move "
                        "this projection by a lot, so read it as 'not on "
                        "track at the CURRENT rate', not as a date."
                        .format(rec.get("rate_per_week"),
                                rec.get("weeks_to_threshold"),
                                rec.get("projected_resolution_utc"),
                                rec.get("settled_positions_28d"),
                                rec.get("rate_window_days") or 28.0)),
                workstream=pod))
    return out


P022_CRITICAL_STATES = {
    "WINDOW_OPEN_CANDIDATE_NO_QUOTE": (
        "P-022 window OPEN with a qualifying name and NO quote written"),
    "CLOSE_REF_PLACEHOLDER": (
        "P-022 close reference is a placeholder — it CANNOT quote"),
    "SCHEDULE_UNRESOLVED": (
        "P-022 cannot time any listed round — failing closed and mute"),
}


def check_p022_window(snap: Dict[str, Any]) -> List[Finding]:
    """Route P-022's quotable-window detector into the alert path.

    The detector has run */30 since 2026-07-28 and nothing consumed it, so
    the one state that means "the fund's only validated edge is structurally
    unable to trade" reached a log file and stopped there. P-022 has now been
    mute for five days across three fixes, two of which were declared verified
    and failed live; the whole point is that its failure and its correct
    behaviour are the same silence.
    """
    win = snap.get("p022_window") or {}
    if not win:
        return []
    if not win.get("available"):
        return [Finding(
            key="p022.window.unavailable",
            severity="warn",
            title="P-022 window detector is not reporting",
            detail="{}\n\nWhile this is unavailable P-022's ability to quote "
                   "is UNMEASURED, which is not the same as fine."
                   .format(win.get("error") or "no detail"),
            workstream="P-022",
            fix="ssh root@129.212.176.202 'cd /opt/betting-pod-shop && "
                "sudo -u bettingbot ./venv/bin/python -m "
                "scripts.p022_window_check'")]

    out: List[Finding] = []
    age = win.get("age_min")
    if isinstance(age, (int, float)) and age > 90:
        out.append(Finding(
            key="p022.window.stale",
            severity="warn",
            title="P-022 window detector last ran {:.0f} min ago".format(age),
            detail="It is crontabbed */30. A stale row means the checker is "
                   "dead, and its last state below is not current.",
            workstream="P-022",
            fix="ssh root@129.212.176.202 'crontab -l | grep p022'",
            value=round(age, 1)))

    state = win.get("state")
    funnel = win.get("funnel") or {}
    funnel_line = ("funnel: listed {markets_listed} -> resolved "
                   "{close_resolved} -> in window {inside_window} -> priced "
                   "{priced} -> in band {in_band} -> passes every screen "
                   "{passes_every_screen} -> quoted "
                   "{quoted_since_window_open}").format(**{
                       k: funnel.get(k, "?") for k in (
                           "markets_listed", "close_resolved", "inside_window",
                           "priced", "in_band", "passes_every_screen",
                           "quoted_since_window_open")}) if funnel else ""

    if state in P022_CRITICAL_STATES:
        out.append(Finding(
            key="p022.window.{}".format(state.lower()),
            severity="critical",
            title=P022_CRITICAL_STATES[state],
            detail="\n".join(x for x in [
                win.get("detail") or "",
                funnel_line,
                ("names: " + ", ".join(win.get("candidates_without_quote") or []))
                if win.get("candidates_without_quote") else "",
            ] if x),
            workstream="P-022",
            fix="ssh root@129.212.176.202 'journalctl -u "
                "betting-round-leader-fade -n 100 --no-pager'",
            value=state))
    elif state == "CHECK_FAILED":
        out.append(Finding(
            key="p022.window.check_failed",
            severity="warn",
            title="P-022 window detector could not measure",
            detail=(win.get("detail") or "")
                   + "\n\nTreat P-022's state as UNKNOWN, not healthy.",
            workstream="P-022",
            value=state))
    elif state:
        out.append(Finding(
            key="p022.window.state",
            severity="info",
            title="P-022 window: {}".format(state),
            detail="\n".join(x for x in [win.get("detail") or "",
                                         funnel_line] if x),
            workstream="P-022",
            value=state))
    return out


# How stale a job's last run may be before it is treated as not running, and
# how many quote intents the paper maker must produce in a day. Both are
# generous: the point is to catch a DEAD collector, not to page on jitter.
EVMAP_MAX_AGE_MIN = {
    "weather_sheet": 60 * 30,     # daily 06:53 local
    "weather_depth": 60 * 30,     # 09:23 and 12:23 local
    "paper_maker": 60 * 20,       # every 30 min inside a 13h local window
    "paper_eval": 60 * 30,        # daily 07:37 local
    "archive_settled": 60 * 24 * 9,   # weekly, plus two days of slack
}
# A day in which the maker collected nothing is a dead day, whatever the exit
# codes said. Set well below the ~1,200/day the Mac used to produce.
EVMAP_MIN_ROWS_TODAY = {"paper_maker": 1}


def check_evmap(snap: Dict[str, Any]) -> List[Finding]:
    """EV-Map collectors: alarm on failure, on silence, AND on empty success.

    The Mac ran these 139 times and failed 139 times unnoticed — macOS TCC
    denying cron read access under ~/Desktop, so they failed awake as well as
    asleep. Six days of point-in-time weather paper quotes (~1,200/day) are
    permanently gone: the public horizon is 90 days and the quotes cannot be
    re-pulled.

    Three distinct conditions, because in that failure all three were
    indistinguishable from health:

      1. the job FAILED (nonzero exit, or exit 0 having written nothing it
         was contracted to write);
      2. the job has not RUN inside its own cadence;
      3. the job ran and the DAY collected nothing.
    """
    ev = snap.get("evmap") or {}
    if not ev:
        return []
    if not ev.get("available"):
        return [Finding(
            key="evmap.unavailable",
            severity="warn",
            title="EV-Map collectors are not reporting",
            detail="{}\n\nWhile this is unavailable the weather Build-2 clock "
                   "is UNMEASURED, and every unmeasured day is a day of "
                   "point-in-time quotes that cannot be recovered."
                   .format(ev.get("error") or "no detail"),
            workstream="EV-Map",
            fix="ssh root@129.212.176.202 'systemctl list-timers evmap-*'")]

    out: List[Finding] = []
    for job, rec in sorted((ev.get("jobs") or {}).items()):
        fails = int(rec.get("consecutive_failures") or 0)
        if not rec.get("ok"):
            out.append(Finding(
                key="evmap.{}.failing".format(job),
                # One failure is a warn (alert.py needs two consecutive runs
                # to page); two in a row is the pattern that ran to 139.
                severity="critical" if fails >= 2 else "warn",
                title="EV-Map {} failed ({} consecutive)".format(job, fails),
                detail="exit={} rows_added={} {}\n{}".format(
                    rec.get("exit_code"), rec.get("rows_added"),
                    rec.get("empty_reason") or "",
                    "\n".join("    " + s for s in rec.get("stderr_tail") or [])),
                workstream="EV-Map",
                fix="ssh root@129.212.176.202 'journalctl -u evmap-* -n 50'",
                value=fails))
            continue

        age = rec.get("age_min")
        limit = EVMAP_MAX_AGE_MIN.get(job)
        if isinstance(age, (int, float)) and limit and age > limit:
            out.append(Finding(
                key="evmap.{}.stale".format(job),
                severity="critical",
                title="EV-Map {} has not run in {:.0f}h".format(job, age / 60),
                detail="Its timer should have fired inside {:.0f}h. A "
                       "collector that stops running looks exactly like one "
                       "with nothing to collect — that is how 139 runs were "
                       "lost.".format(limit / 60),
                workstream="EV-Map",
                fix="ssh root@129.212.176.202 'systemctl list-timers evmap-*'",
                value=round(age, 1)))
            continue

        need = EVMAP_MIN_ROWS_TODAY.get(job)
        if need and int(rec.get("rows_today") or 0) < need:
            out.append(Finding(
                key="evmap.{}.empty_day".format(job),
                severity="warn",
                title="EV-Map {} collected 0 rows today".format(job),
                detail="Every run exited 0 and the day produced nothing. That "
                       "is the same outcome as not running at all, and it is "
                       "the half of the failure an exit code cannot see.",
                workstream="EV-Map",
                value=0))
    return out


def check_faults(snap: Dict[str, Any]) -> List[Finding]:
    """The collector failing to measure something is itself a finding.

    Without this, a broken probe looks identical to a healthy system — the
    silent-failure mode this whole tool exists to prevent.
    """
    faults = snap.get("faults") or []
    if not faults:
        return []
    return [Finding(
        key="collector.faults",
        severity="warn",
        title="Collector had {} probe failure(s)".format(len(faults)),
        detail="\n".join("  {}: {}".format(f.get("probe"), f.get("error"))
                         for f in faults) +
               "\n\nThese areas are UNMEASURED right now — treat their status as "
               "unknown, not healthy.",
        value=len(faults))]


def check_errors(snap: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    for unit, data in (snap.get("errors", {}) or {}).get("units", {}).items():
        count = data.get("errors_24h")
        if not isinstance(count, int) or count == 0:
            continue
        out.append(Finding(
            key="errors.{}".format(unit),
            severity="warn" if count >= 10 else "info",
            title="{}: {} unsuppressed error(s) in 24h".format(unit, count),
            detail=("Suppressed as known-benign: {}\n\nSamples:\n{}"
                    .format(data.get("suppressed_24h", 0),
                            "\n".join("  " + s for s in data.get("samples", [])))),
            value=count))
    return out


def check_crossvenue_research(snap: Dict[str, Any]) -> List[Finding]:
    """Surface pair-neutral research signals without granting trade authority."""
    research = snap.get("research_operations") or {}
    crossvenue = research.get("crossvenue_pilot") or {}
    out: List[Finding] = []
    for signal in crossvenue.get("research_signals") or []:
        if not isinstance(signal, dict) or not signal.get("id"):
            continue
        severity = signal.get("severity")
        if severity not in ("warn", "info"):
            severity = "info"
        detail = str(signal.get("detail") or "")
        if signal.get("kind") == "research_opportunity":
            detail += "\n\nThis is a research alert, not execution approval."
        out.append(Finding(
            key="research.crossvenue.{}".format(signal["id"]),
            severity=severity,
            title=str(signal.get("title") or signal["id"]),
            detail=detail,
            workstream="crossvenue-research",
            value=signal.get("value")))
    return out


def check_staleness(snap: Dict[str, Any]) -> List[Finding]:
    """Is the snapshot itself fresh? A stale status.json means the collector
    cron is dead, which would otherwise present as a perfectly healthy report."""
    ts = _parse(snap.get("collected_at"))
    if not ts:
        return [Finding(key="collector.no_timestamp", severity="warn",
                        title="status.json has no valid collected_at")]
    age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
    if age_min > 90:
        return [Finding(
            key="collector.stale",
            severity="critical",
            title="Status snapshot is {:.0f} min old — collector may be dead".format(age_min),
            detail="Everything else in this report is that stale too. The "
                   "collector cron on the droplet is the first thing to check.",
            fix="ssh root@129.212.176.202 'crontab -l | grep manager'",
            value=round(age_min, 1))]
    return []


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def run_checks(snap: Dict[str, Any], registry: Optional[Dict[str, Any]] = None,
               local_config_fp: Optional[Dict[str, Any]] = None) -> List[Finding]:
    findings: List[Finding] = []
    # Every production caller (brief, alert, both dashboards) omits `registry`,
    # and the old `registry or {}` default made check_registry_reconciliation
    # compare pods.active against an EMPTY workstream map — so every active pod
    # fired "is in pods.active but has no registry entry" despite the entries
    # existing (the four standing dashboard alarms of 2026-07-30). Load the
    # real registry by default; a caller passing an explicit dict keeps it.
    reconcilable = True
    if registry is None:
        registry = load_registry()
        if registry is None:
            findings.append(Finding(
                key="registry.unreadable",
                severity="warn",
                title="manager/registry.yaml could not be loaded",
                detail="Registry reconciliation was SKIPPED this run — the "
                       "trading-vs-registered cross-checks had no registry to "
                       "compare against. This is not an all-clear: an active "
                       "pod could be unregistered right now and nothing would "
                       "say so until the file loads again.",
                fix="python3 -c \"import yaml; "
                    "yaml.safe_load(open('manager/registry.yaml'))\""))
            registry = {}
            # Reconciling against {} is what produced the false alarms above:
            # a registry we FAILED TO READ must not be reported as empty.
            reconcilable = False
    findings += check_staleness(snap)
    findings += check_services(snap)
    findings += check_invariants(snap)
    findings += check_config_drift(snap, local_config_fp)
    findings += check_systemd_units(snap)
    findings += check_jobs(snap)
    findings += check_gates(snap, registry)
    if reconcilable:
        findings += check_registry_reconciliation(snap, registry)
    findings += check_errors(snap)
    findings += check_crossvenue_research(snap)
    findings += check_throughput(snap)
    findings += check_p022_window(snap)
    findings += check_evmap(snap)
    findings += check_faults(snap)
    findings += check_workstreams(snap)
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.key))
    return findings


def load_status(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or (HERE / "state" / "status.json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The manager registry, or None if it cannot be read.

    None and {} are different answers on purpose: {} means "reconcile against
    an empty registry" (every active pod flags as unregistered), None means
    "there is no registry to reconcile against" — run_checks reports the
    latter as its own finding instead of fabricating per-pod errors.
    """
    path = path or (HERE / "registry.yaml")
    try:
        import yaml  # noqa: PLC0415 — optional here; only collect.py requires it
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing file, bad YAML, no pyyaml: all None
        return None
    return data if isinstance(data, dict) else None


if __name__ == "__main__":
    import sys
    snap = load_status(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    for f in run_checks(snap):
        print("[{:8}] {}".format(f.severity, f.title))
