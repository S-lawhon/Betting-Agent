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
from datetime import datetime, timedelta, timezone
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


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


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
        age = hb.get("age_minutes")
        limit = hb.get("max_stale_minutes")
        if age is not None and limit and age > limit:
            # Some heartbeats are only meaningful in a time window (P-016 only
            # quotes during live MLB games; silence at 4am is correct).
            if hb.get("only_during") == "mlb_games_window" and not _in_games_window():
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


def _in_games_window(at: Optional[datetime] = None) -> bool:
    """Rough MLB slate window, 16:00-06:00 UTC (noon-2am ET)."""
    hour = (at or datetime.now(UTC)).hour
    return hour >= 16 or hour < 6


def check_jobs(snap: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    for job in snap.get("jobs", []):
        jid = job.get("id", "?")
        if not job.get("measurable"):
            # Output lives on a host we can't see from here. Silent by design —
            # the Mac-side jobs are checked when the brief runs on the Mac.
            continue
        if job.get("stale"):
            sev = {"critical": "critical", "warn": "warn"}.get(
                job.get("severity", "info"), "warn")
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
        out.append(Finding(
            key="invariant.kill_switch",
            severity="warn",
            title="P-016 kill switch is engaged",
            detail="data/KILL_MAKER exists, so the live maker is halted. "
                   "Expected if you killed it deliberately; alarming if not.",
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


def check_gates(snap: Dict[str, Any], registry: Dict[str, Any]) -> List[Finding]:
    """Evaluate pre-registered gates. These rules are locked — read, don't reinterpret."""
    out: List[Finding] = []

    # ---- P-016 maker gate ----
    maker = snap.get("maker", {}) or {}
    if maker.get("available"):
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


def check_workstreams(snap: Dict[str, Any]) -> List[Finding]:
    """The 'what needs me' list. Driven entirely by registry blocked_on."""
    out: List[Finding] = []
    for ws in snap.get("workstreams", []):
        wid, blocked = ws.get("id"), ws.get("blocked_on")

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
    registry = registry or {}
    findings: List[Finding] = []
    findings += check_staleness(snap)
    findings += check_services(snap)
    findings += check_invariants(snap)
    findings += check_config_drift(snap, local_config_fp)
    findings += check_jobs(snap)
    findings += check_gates(snap, registry)
    findings += check_errors(snap)
    findings += check_faults(snap)
    findings += check_workstreams(snap)
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.key))
    return findings


def load_status(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or (HERE / "state" / "status.json")
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys
    snap = load_status(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    for f in run_checks(snap):
        print("[{:8}] {}".format(f.severity, f.title))
