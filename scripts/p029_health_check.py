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
point at these files with max_stale_hours: 30.

Run daily by the ``p029-daily-health-check`` scheduled task; safe to run by
hand any time:

    python3 scripts/p029_health_check.py            # check + push
    python3 scripts/p029_health_check.py --no-push  # check, write local only

Exit 0 only when every check passed and the push succeeded.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

P029_HOST = "root@143.198.162.120"
P029_KEY = os.path.expanduser("~/.ssh/betting_deploy")
DROPLET_HOST = "root@129.212.176.202"
DROPLET_DIR = "/opt/betting-pod-shop/data/p029_heartbeat/"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_DIR = PROJECT_ROOT / "data" / "p029_heartbeat"

# Thresholds mirror what the on-box units promise, not the heartbeat cadence.
# A shadow DB older than ~1h is already a stall (SQLite single-instance lock);
# 2h is the registry's original on-box threshold. The archiver is daily at
# 09:30 UTC and a good run takes ~3h, so 30h means "yesterday's run landed".
SHADOW_MAX_DB_AGE_S = 2 * 3600
ARCHIVE_MAX_AGE_S = 30 * 3600

# Runs ON the box, prints one JSON object. stat()-based so it works no matter
# what state systemd thinks the units are in. `is-active` exits non-zero for
# `activating`, so states are read via check_output(..).strip() and never
# through the exit code.
REMOTE_PROBE = r"""
import json, os, subprocess, time
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
    shadow_ok = (facts.get("shadow_unit") == "active"
                 and db_age is not None and db_age <= SHADOW_MAX_DB_AGE_S)
    out["p029_shadow"] = {
        "ok": shadow_ok,
        "why": ("unit={} db_age={}".format(
            facts.get("shadow_unit"),
            "{:.0f}m".format(db_age / 60) if db_age is not None else "missing")),
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
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "job": job_id,
        "ok": True,
        "detail": verdict["why"],
        "disk_used_pct": facts.get("disk_used_pct"),
        "checked_from": os.uname().nodename,
    }
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
