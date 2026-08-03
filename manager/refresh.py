#!/usr/bin/env python3
"""Pull live status from the droplet to the Mac.

The collector runs ON the droplet (that is where the trading system lives and
where the box is always on). This script is an optional local helper: it runs
the remote collector over SSH and copies the resulting snapshot back for
ad-hoc Mac-side inspection.

If the droplet is unreachable this exits non-zero and leaves the previous
snapshot alone. Callers must treat a failure as "state unknown", never as
"nothing changed" — silently reusing a stale snapshot is how a dead production
system comes to look perfectly healthy.

Usage:
    python3 manager/refresh.py
    python3 manager/refresh.py --no-remote-run   # just fetch, don't re-collect
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402  (local module, needs the path above)

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
HOST = "root@129.212.176.202"
REMOTE_ROOT = "/opt/betting-pod-shop"
REMOTE_MANAGER = REMOTE_ROOT + "/manager"


def run(cmd, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh manager status from the droplet")
    ap.add_argument("--no-remote-run", action="store_true",
                    help="fetch the existing remote snapshot without re-collecting")
    ap.add_argument("--host", default=HOST)
    args = ap.parse_args()

    STATE.mkdir(parents=True, exist_ok=True)

    if not args.no_remote_run:
        remote_cmd = (
            "cd {root} && "
            "({root}/venv/bin/python manager/collect.py --root {root} "
            " || python3 manager/collect.py --root {root})"
        ).format(root=REMOTE_ROOT)
        res = run(["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes",
                   args.host, remote_cmd])
        if res.returncode != 0:
            sys.stderr.write(
                "[refresh] remote collect FAILED (rc={}):\n{}\n{}\n"
                .format(res.returncode, res.stdout.strip(), res.stderr.strip()))
            sys.stderr.write(
                "[refresh] The local snapshot was NOT updated. Treat current "
                "state as UNKNOWN, not healthy.\n")
            return 1
        print("[refresh] remote: " + (res.stdout.strip() or "ok"))

    dest = STATE / "status.json"
    staged = STATE / ".status.json.fetching"
    res = run(["scp", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes",
               "{}:{}/state/status.json".format(args.host, REMOTE_MANAGER),
               str(staged)])
    if res.returncode != 0:
        sys.stderr.write("[refresh] scp FAILED:\n{}\n".format(res.stderr.strip()))
        staged.unlink(missing_ok=True)
        return 1

    try:
        snap = json.loads(staged.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("[refresh] fetched snapshot is unreadable: {}\n".format(exc))
        staged.unlink(missing_ok=True)
        return 1

    # The remote collector cannot see /Users/samlawhon/... — Mac-hosted jobs
    # (weather_paper_maker, weather_archiver) come back marked "uncheckable".
    # Measure them here, where their output lives, and overlay the results.
    # Without this the brief was blind to R-EV-MAP Build 2's evidence gate.
    try:
        n_before = sum(1 for j in snap.get("jobs", [])
                       if j.get("state") == "uncheckable")
        snap = collect.merge_local_jobs(snap)
        n_after = sum(1 for j in snap.get("jobs", [])
                      if j.get("state") == "uncheckable")
        staged.write_text(json.dumps(snap, indent=2, default=collect.jsonable),
                          encoding="utf-8")
        print("[refresh] local jobs measured on this Mac: {} of {} filled in"
              .format(n_before - n_after, n_before))
        if n_after:
            sys.stderr.write(
                "[refresh] {} job(s) remain UNCHECKABLE from here — reported as "
                "unknown, not as healthy.\n".format(n_after))
    except Exception as exc:  # noqa: BLE001 - never fail the refresh over this
        sys.stderr.write(
            "[refresh] local job merge failed ({}: {}); Mac-hosted jobs stay "
            "UNCHECKABLE in this snapshot.\n".format(type(exc).__name__, exc))

    # The previous known-good snapshot remains intact until the fetched and
    # locally-augmented replacement has parsed successfully.
    os.replace(staged, dest)

    collected = snap.get("collected_at", "?")
    age = ""
    try:
        dt = datetime.fromisoformat(str(collected).replace("Z", "+00:00"))
        mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
        age = " ({:.0f} min old)".format(mins)
    except ValueError:
        pass

    svc = ", ".join("{}={}".format(s.get("id"), s.get("active"))
                    for s in snap.get("services", []))
    print("[refresh] snapshot {}{} | {} | faults={}".format(
        collected, age, svc, len(snap.get("faults", []))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
