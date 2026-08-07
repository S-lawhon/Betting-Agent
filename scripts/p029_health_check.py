#!/usr/bin/env python3
"""Deterministic P-029 health check: verify on-box, attest with a heartbeat.

The P-029 Phase 0 jobs (shadow logger, settled-combo archiver) run on a
dedicated VPS that neither fund-manager collector can stat, so their registry
entries used to be reported ``uncheckable`` (INFO) forever. This script closes
that gap without pretending the collectors can see the box:

  1. SSH to the P-029 VPS and measure each job where it actually runs.
  2. For each job that PASSES its check, append one heartbeat row to
     ``data/p029_heartbeat/<job_id>.jsonl`` on this Mac.
  3. rsync the heartbeat dir to the droplet's project tree, where the
     fund-manager collector measures it like any other jsonl output.

A failing check appends NOTHING, so heartbeat staleness is the alarm: it means
the check failed, this script did not run, or the push broke — every one of
which deserves a look. The registry entries for p029_shadow / p029_archive
point at these files with max_stale_hours: 3.

For the Gate 0c forward window, the shadow check also verifies that systemd's
effective memory ceiling is exactly 768 MiB and that the in-zone resolver
backlog remains below the logger's existing 25,000-row sustained-backlog alarm.
Memory, swap, cgroup memory events, PID, and restart telemetry are preserved in
every passing heartbeat so the manager can distinguish pressure from liveness.

WHERE IT RUNS (changed 2026-08-01): the primary runner is now the DROPLET —
``p029-health-check.timer`` (scripts/systemd/), hourly, invoking this
script with ``--no-push`` so the heartbeats land directly in the project data
dir the collector reads. The original runner was a scheduled task on the Mac,
and on 2026-08-01 both heartbeats went 35h stale for no reason other than the
Mac being asleep at the scheduled hour — the exact failure mode that moved the
evmap jobs onto droplet timers (see scripts/systemd/evmap-weather-sheet.timer).
The droplet reaches the P-029 box with its own key; ``P029_HEALTH_SSH_KEY`` /
``P029_HEALTH_HOST`` override the Mac-centric defaults (the systemd unit sets
the key). One-time setup: ``bash scripts/setup_p029_health_timer.sh`` from the
Mac.

Still safe to run by hand from the Mac any time:

    python3 scripts/p029_health_check.py            # check + push to droplet
    python3 scripts/p029_health_check.py --no-push  # check, write local only

Exit 0 only when every check passed and the push (if any) succeeded.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Env overrides exist for the droplet timer, whose key is not the Mac's
# deploy key. An empty env value falls through to the default on purpose —
# systemd `Environment=` typos produce empty strings more often than absent.
P029_HOST = os.environ.get("P029_HEALTH_HOST") or "root@143.198.162.120"
P029_KEY = (os.environ.get("P029_HEALTH_SSH_KEY")
            or os.path.expanduser("~/.ssh/betting_deploy"))
DROPLET_HOST = "root@129.212.176.202"
DROPLET_DIR = "/opt/betting-pod-shop/data/p029_heartbeat/"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_DIR = PROJECT_ROOT / "data" / "p029_heartbeat"

# Thresholds mirror what the on-box units promise, not the heartbeat cadence.
# A shadow DB older than ~1h is already a stall (SQLite single-instance lock);
# 2h is the registry's original on-box threshold. Gate 0c additionally requires
# the effective 768 MiB ceiling and no sustained in-zone resolver backlog. The
# archiver is daily at 09:30 UTC and a good run takes ~3h, so 30h means
# "yesterday's run landed".
SHADOW_MAX_DB_AGE_S = 2 * 3600
ARCHIVE_MAX_AGE_S = 30 * 3600
SHADOW_MEMORY_MAX_BYTES = 768 * 1024 * 1024
SHADOW_BACKLOG_WARN = 25_000

# Runs ON the box, prints one JSON object. stat()-based so it works no matter
# what state systemd thinks the units are in. `is-active` exits non-zero for
# `activating`, so states are read via check_output(..).strip() and never
# through the exit code.
REMOTE_PROBE = r"""
import json, os, sqlite3, subprocess, time
from datetime import datetime, timedelta, timezone
def unit_state(u):
    try:
        return subprocess.run(["systemctl", "is-active", u],
                              capture_output=True, text=True).stdout.strip()
    except Exception as e:
        return "probe-error: {}".format(e)
def age_s(p):
    try:
        return time.time() - os.stat(p).st_mtime
    except OSError:
        return None
def unit_property(u, prop):
    try:
        raw = subprocess.run(
            ["systemctl", "show", u, "--property=" + prop, "--value"],
            capture_output=True, text=True, check=True).stdout.strip()
        return raw or None
    except Exception:
        return None
def unit_int(u, prop):
    try:
        return int(unit_property(u, prop))
    except (TypeError, ValueError):
        return None
def memory_events(u):
    try:
        group = unit_property(u, "ControlGroup")
        path = "/sys/fs/cgroup" + group + "/memory.events"
        with open(path, encoding="utf-8") as fh:
            return {k: int(v) for k, v in (line.split() for line in fh)}
    except Exception:
        return None
def due_in_zone():
    try:
        before = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
        with sqlite3.connect(
                "file:/var/lib/p029/shadow.sqlite?mode=ro", uri=True,
                timeout=30) as con:
            return con.execute(
                "SELECT COUNT(*) FROM combo WHERE resolved=0 AND seen_ts <= ?"
                " AND n_legs BETWEEN 2 AND 4"
                " AND COALESCE(copula_price, indep_price, -1)"
                " BETWEEN 0.07 AND 0.40", (before,)).fetchone()[0]
    except Exception:
        return None
newest, newest_age = None, None
try:
    entries = [os.path.join("/var/lib/p029/archive", f)
               for f in os.listdir("/var/lib/p029/archive")]
    files = [p for p in entries if os.path.isfile(p)]
    if files:
        newest = max(files, key=lambda p: os.stat(p).st_mtime)
        newest_age = age_s(newest)
except OSError:
    pass
disk = os.statvfs("/")
print(json.dumps({
    "shadow_unit": unit_state("p029-shadow.service"),
    "shadow_db_age_s": age_s("/var/lib/p029/shadow.sqlite"),
    "shadow_db_bytes": (os.stat("/var/lib/p029/shadow.sqlite").st_size
                        if os.path.exists("/var/lib/p029/shadow.sqlite") else None),
    "shadow_memory_current_bytes": unit_int("p029-shadow.service", "MemoryCurrent"),
    "shadow_memory_peak_bytes": unit_int("p029-shadow.service", "MemoryPeak"),
    "shadow_memory_swap_bytes": unit_int("p029-shadow.service", "MemorySwapCurrent"),
    "shadow_memory_max_bytes": unit_int("p029-shadow.service", "MemoryMax"),
    "shadow_memory_events": memory_events("p029-shadow.service"),
    "shadow_main_pid": unit_int("p029-shadow.service", "MainPID"),
    "shadow_nrestarts": unit_int("p029-shadow.service", "NRestarts"),
    "shadow_result": unit_property("p029-shadow.service", "Result"),
    "shadow_due_in_zone": due_in_zone(),
    "archive_unit": unit_state("p029-archive.service"),
    "archive_newest": os.path.basename(newest) if newest else None,
    "archive_newest_age_s": newest_age,
    "disk_used_pct": round(100.0 * (1 - disk.f_bavail / disk.f_blocks), 1),
}))
"""


def probe(host: str = P029_HOST, key: str = P029_KEY) -> dict:
    """One SSH round-trip; raises RuntimeError with the ssh error on failure."""
    cmd = ["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=15", host, "python3", "-"]
    res = subprocess.run(cmd, input=REMOTE_PROBE, capture_output=True,
                         text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError("ssh probe failed (rc={}): {}".format(
            res.returncode, (res.stderr or "").strip()[-500:]))
    return json.loads(res.stdout.strip().splitlines()[-1])


def evaluate(facts: dict) -> dict:
    """Pure pass/fail per job id. Returns {job_id: {"ok": bool, "why": str}}.

    `activating` on the archive unit is a run in progress, not a failure —
    TimeoutStartSec=6h on the unit turns a genuine wedge into `failed`, which
    IS a failure here. Freshness of the newest archive file is the real check.
    """
    out = {}

    db_age = facts.get("shadow_db_age_s")
    memory_current_bytes = facts.get("shadow_memory_current_bytes")
    memory_max_bytes = facts.get("shadow_memory_max_bytes")
    memory_swap_bytes = facts.get("shadow_memory_swap_bytes")
    main_pid = facts.get("shadow_main_pid")
    nrestarts = facts.get("shadow_nrestarts")
    memory_events_data = facts.get("shadow_memory_events")
    due = facts.get("shadow_due_in_zone")
    shadow_ok = (facts.get("shadow_unit") == "active"
                 and db_age is not None and db_age <= SHADOW_MAX_DB_AGE_S
                 and memory_current_bytes is not None
                 and memory_max_bytes == SHADOW_MEMORY_MAX_BYTES
                 and memory_swap_bytes is not None
                 and main_pid not in (None, 0)
                 and nrestarts is not None
                 and isinstance(memory_events_data, dict)
                 and due is not None and due <= SHADOW_BACKLOG_WARN)
    memory_pct = (100.0 * memory_current_bytes / memory_max_bytes
                  if memory_current_bytes is not None and memory_max_bytes else None)
    out["p029_shadow"] = {
        "ok": shadow_ok,
        "why": ("unit={} db_age={} memory={}/{}MiB ({}) swap={}MiB "
                "pid={} restarts={} oom_kill={} due_in_zone={}".format(
            facts.get("shadow_unit"),
            "{:.0f}m".format(db_age / 60) if db_age is not None else "missing",
            (round(memory_current_bytes / 1024 / 1024)
             if memory_current_bytes is not None else "missing"),
            (round(memory_max_bytes / 1024 / 1024)
             if memory_max_bytes is not None else "missing"),
            ("{:.1f}%".format(memory_pct) if memory_pct is not None else "missing"),
            (round(memory_swap_bytes / 1024 / 1024)
             if memory_swap_bytes is not None else "missing"),
            main_pid if main_pid is not None else "missing",
            nrestarts if nrestarts is not None else "missing",
            ((memory_events_data or {}).get("oom_kill")
             if isinstance(memory_events_data, dict) else "missing"),
            due if due is not None else "unavailable")),
    }

    ar_age = facts.get("archive_newest_age_s")
    archive_ok = (facts.get("archive_unit") != "failed"
                  and ar_age is not None and ar_age <= ARCHIVE_MAX_AGE_S)
    out["p029_archive"] = {
        "ok": archive_ok,
        "why": ("unit={} newest={} age={}".format(
            facts.get("archive_unit"), facts.get("archive_newest"),
            "{:.1f}h".format(ar_age / 3600) if ar_age is not None else "none")),
    }
    return out


def append_heartbeat(job_id: str, facts: dict, verdict: dict,
                     hb_dir: Path = HEARTBEAT_DIR) -> Path:
    """One row per passing check. `timestamp_utc` is the key the collector's
    row_ts() reads, so the row's own clock — not the file's mtime — is what
    the staleness check measures."""
    hb_dir.mkdir(parents=True, exist_ok=True)
    path = hb_dir / "{}.jsonl".format(job_id)
    previous = None
    if path.exists():
        try:
            for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                candidate = json.loads(line)
                if isinstance(candidate, dict):
                    previous = candidate
                    break
        except (OSError, json.JSONDecodeError):
            previous = None
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "job": job_id,
        "ok": True,
        "detail": verdict["why"],
        "disk_used_pct": facts.get("disk_used_pct"),
        "checked_from": os.uname().nodename,
    }
    if job_id == "p029_shadow":
        current_restarts = facts.get("shadow_nrestarts")
        current_pid = facts.get("shadow_main_pid")
        previous_restarts = (previous or {}).get("shadow_nrestarts")
        previous_pid = (previous or {}).get("shadow_main_pid")
        restart_delta = 0
        if isinstance(current_restarts, int) and isinstance(previous_restarts, int):
            restart_delta = max(0, current_restarts - previous_restarts)
        if previous_pid not in (None, current_pid):
            restart_delta = max(1, restart_delta)
        memory_current = facts.get("shadow_memory_current_bytes")
        memory_max = facts.get("shadow_memory_max_bytes")
        events = facts.get("shadow_memory_events") or {}
        row.update({
            "shadow_memory_current_bytes": memory_current,
            "shadow_memory_peak_bytes": facts.get("shadow_memory_peak_bytes"),
            "shadow_memory_swap_bytes": facts.get("shadow_memory_swap_bytes"),
            "shadow_memory_max_bytes": memory_max,
            "shadow_memory_utilization_pct": (
                round(100.0 * memory_current / memory_max, 1)
                if isinstance(memory_current, int) and memory_max else None),
            "shadow_memory_max_events": events.get("max"),
            "shadow_oom_events": events.get("oom"),
            "shadow_oom_kill_events": events.get("oom_kill"),
            "shadow_main_pid": current_pid,
            "shadow_nrestarts": current_restarts,
            "shadow_restart_delta": restart_delta,
            "shadow_result": facts.get("shadow_result"),
            "shadow_due_in_zone": facts.get("shadow_due_in_zone"),
        })
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return path


def push(droplet: str = DROPLET_HOST, dest: str = DROPLET_DIR,
         hb_dir: Path = HEARTBEAT_DIR) -> None:
    res = subprocess.run(
        ["rsync", "-az", str(hb_dir) + "/", "{}:{}".format(droplet, dest)],
        capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError("rsync to droplet failed (rc={}): {}".format(
            res.returncode, (res.stderr or "").strip()[-500:]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-push", action="store_true",
                    help="write local heartbeats only, skip the droplet rsync")
    args = ap.parse_args(argv)

    try:
        facts = probe()
    except (RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as e:
        # No probe, no heartbeat: staleness accrues and the manager warns.
        print("P-029 health check: PROBE FAILED — {}".format(e))
        print("No heartbeat written; the registry check will go stale, "
              "which is the intended alarm.")
        return 1

    verdicts = evaluate(facts)
    failed = [j for j, v in verdicts.items() if not v["ok"]]
    for job_id, v in sorted(verdicts.items()):
        if v["ok"]:
            append_heartbeat(job_id, facts, v)
        print("{}: {} ({})".format(job_id, "PASS" if v["ok"] else "FAIL",
                                   v["why"]))
    print("disk_used_pct={}".format(facts.get("disk_used_pct")))

    pushed_err = None
    if not args.no_push:
        try:
            push()
            print("heartbeats pushed to {}{}".format(DROPLET_HOST, DROPLET_DIR))
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            pushed_err = str(e)
            print("PUSH FAILED — {}".format(pushed_err))
            print("Droplet copy will go stale; local copy is current.")

    return 1 if (failed or pushed_err) else 0


if __name__ == "__main__":
    sys.exit(main())
