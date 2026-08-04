#!/usr/bin/env python3
"""Evaluate checks and dispatch alerts. Deterministic — no LLM in this path.

Runs on the droplet every 15 minutes, right after the collector. Only
`critical` findings page you. `warn` findings page only if they persist past a
confirmation window, so a single transient blip stays quiet.

Cooldown and dedupe are the difference between an alert channel you trust and
one you mute. State lives in state/alerts.json:

    - A finding pages once, then goes quiet for its cooldown.
    - Re-firing after resolution is a NEW alert (you want to know it came back).
    - Resolution sends a short all-clear so you are never left wondering.

Usage:
    python3 manager/alert.py              # evaluate + dispatch
    python3 manager/alert.py --dry-run    # print what WOULD be sent
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import notify  # noqa: E402

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
ALERT_STATE = STATE / "alerts.json"
UTC = timezone.utc

DASHBOARD_URL = "https://dashboard.htxtrades.org/manager"

# How long to stay quiet after paging for the same finding.
COOLDOWN = {"critical": timedelta(hours=2), "warn": timedelta(hours=12)}
# A warn must be seen this many consecutive runs before it pages. Criticals
# page immediately — a dead service should not wait for confirmation.
WARN_CONFIRMATIONS = 2
# A snapshot older than one collect cycle must not be judged: the 2026-08-04
# false "0 rows today" page came from an alerter re-reading the PREVIOUS
# cycle's snapshot while the current collect was still writing, which counted
# one transient warn twice. Skipping is safe only briefly — past DEAD the
# collector itself is the emergency, and that pages as critical.
SNAPSHOT_SKIP_AFTER = timedelta(minutes=12)
SNAPSHOT_DEAD_AFTER = timedelta(minutes=60)


def now() -> datetime:
    return datetime.now(UTC)


def parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def load_alert_state() -> Dict[str, Any]:
    if ALERT_STATE.exists():
        try:
            return json.loads(ALERT_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"active": {}}


def save_alert_state(state: Dict[str, Any]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / ".alerts.json.tmp"
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(ALERT_STATE)


def local_config_fingerprint() -> Optional[Dict[str, Any]]:
    """Fingerprint the config next to this checkout, for drift detection.

    On the droplet this is the live config and drift checking is a no-op
    (it compares to itself). On the Mac it is the working copy, which is
    where drift actually shows up.
    """
    try:
        sys.path.insert(0, str(HERE))
        from collect import Collector  # noqa: PLC0415
        return Collector.config_fingerprint(HERE.parent / "config_multi_pod.yaml")
    except Exception:  # noqa: BLE001
        return None


def format_alert(findings: List[checks.Finding]) -> Dict[str, str]:
    crit = [f for f in findings if f.severity == "critical"]
    warn = [f for f in findings if f.severity == "warn"]
    head = crit[0] if crit else (warn[0] if warn else None)
    if head is None:
        return {}

    if len(findings) == 1:
        subject = head.title
    else:
        subject = "{} (+{} more)".format(head.title, len(findings) - 1)
    prefix = "[BETTING-FUND CRITICAL]" if crit else "[betting-fund]"

    lines = []
    for f in findings:
        lines.append("[{}] {}".format(f.severity.upper(), f.title))
        if f.detail:
            lines.extend("    " + l for l in f.detail.splitlines())
        if f.fix:
            lines.append("    FIX: " + f.fix)
        lines.append("")
    lines.append("Dashboard: " + DASHBOARD_URL)
    lines.append("Checked at: " + now().isoformat())

    return {
        "subject": "{} {}".format(prefix, subject),
        "body": "\n".join(lines),
        "push_title": subject[:120],
        "push_body": (head.detail or head.title)[:600],
        "severity": "critical" if crit else "warn",
    }


def run(dry_run: bool = False, status_path: Optional[Path] = None) -> int:
    notify.load_env()
    try:
        snap = checks.load_status(status_path)
    except (OSError, json.JSONDecodeError) as exc:
        # If we can't even read the snapshot, that IS the emergency — page on it
        # rather than exiting quietly, which would look like "all clear".
        msg = "Manager cannot read status.json: {}".format(exc)
        print("[alert] " + msg, file=sys.stderr)
        if not dry_run:
            res = notify.DeliveryResult()
            notify.send_push("Manager collector unreadable", msg, "critical", result=res)
            notify.send_email("[BETTING-FUND CRITICAL] collector unreadable", msg,
                              result=res)
            print("[alert] " + res.summary())
        return 1

    collected = parse(snap.get("collected_at"))
    age = (now() - collected) if collected else None
    if age is not None and age >= SNAPSHOT_DEAD_AFTER:
        # Do not evaluate hours-old data as if it were current — but a
        # collector that has stopped producing snapshots is itself a page.
        findings: List[checks.Finding] = [checks.Finding(
            key="manager.snapshot.stale",
            severity="critical",
            title="Manager snapshot is {:.0f}h old — collector is not "
                  "producing".format(age.total_seconds() / 3600),
            detail="state/status.json was collected at {} and nothing newer "
                   "has appeared. Every downstream check is blind until the "
                   "collector runs again.".format(snap.get("collected_at")),
            fix="ssh root@129.212.176.202 'systemctl status "
                "manager-collect.timer manager-collect.service'")]
    elif age is not None and age >= SNAPSHOT_SKIP_AFTER:
        print("[alert] snapshot is {:.1f} min old (>{:.0f} min) — a fresher "
              "collect is due or in flight; skipping this evaluation rather "
              "than judging on stale data".format(
                  age.total_seconds() / 60,
                  SNAPSHOT_SKIP_AFTER.total_seconds() / 60))
        return 0
    else:
        findings = checks.run_checks(snap,
                                     local_config_fp=local_config_fingerprint())
    pageable = [f for f in findings if f.severity in ("critical", "warn")]

    state = load_alert_state()
    active: Dict[str, Any] = state.get("active", {})
    ts = now()
    to_send: List[checks.Finding] = []
    seen_keys = set()

    for f in pageable:
        seen_keys.add(f.key)
        rec = active.get(f.key)
        if rec is None:
            rec = {"first_seen": ts.isoformat(), "count": 1,
                   "last_paged": None, "severity": f.severity}
            active[f.key] = rec
        else:
            rec["count"] = int(rec.get("count", 0)) + 1
            rec["severity"] = f.severity

        # Criticals page immediately; warns must persist to avoid blip noise.
        if f.severity == "warn" and rec["count"] < WARN_CONFIRMATIONS:
            continue

        last = parse(rec.get("last_paged"))
        cooldown = COOLDOWN.get(f.severity, timedelta(hours=6))
        if last and (ts - last) < cooldown:
            continue

        to_send.append(f)
        rec["last_paged"] = ts.isoformat()

    # Anything that was active and is now gone has resolved.
    resolved = [k for k in list(active) if k not in seen_keys]
    for key in resolved:
        active.pop(key, None)

    result_summary = "nothing to send"
    if to_send:
        payload = format_alert(to_send)
        if dry_run:
            print("--- WOULD SEND ---")
            print("Subject:", payload["subject"])
            print(payload["body"])
        else:
            res = notify.DeliveryResult()
            notify.send_email(payload["subject"], payload["body"], result=res)
            if payload["severity"] == "critical" or len(to_send) > 2:
                notify.send_push(payload["push_title"], payload["push_body"],
                                 payload["severity"], DASHBOARD_URL, result=res)
            result_summary = res.summary()
            # A failed delivery must be loud locally, or a broken alert path
            # looks exactly like a healthy system.
            if res.failed:
                print("[alert] DELIVERY FAILURE: {}".format(res.failed),
                      file=sys.stderr)

    if resolved and not dry_run:
        recovered = [k for k in resolved
                     if (state.get("active", {}).get(k) or {}).get("last_paged")]
        if recovered:
            notify.send_email(
                "[betting-fund] resolved: " + ", ".join(recovered[:3]),
                "These previously-alerted conditions are no longer firing:\n\n"
                + "\n".join("  - " + k for k in recovered)
                + "\n\nDashboard: " + DASHBOARD_URL)

    state["active"] = active
    state["last_run"] = ts.isoformat()
    if not dry_run:
        save_alert_state(state)

    print("[alert] findings={} pageable={} sent={} resolved={} | {}".format(
        len(findings), len(pageable), len(to_send), len(resolved), result_summary))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate and dispatch fund alerts")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", default=None)
    args = ap.parse_args()
    return run(args.dry_run, Path(args.status) if args.status else None)


if __name__ == "__main__":
    sys.exit(main())
