#!/usr/bin/env python3
"""
scripts/evmap_job.py
────────────────────
Run one `kalshi-ev-map` collector job and make its outcome OBSERVABLE.

Why this wrapper exists
───────────────────────
EV-Map Build 2's collectors ran **139 times and failed 139 times** without
anyone noticing.  The cause was macOS TCC denying `cron` read access under
`~/Desktop` (`Operation not permitted`) — it failed awake, not only asleep —
but the cause is not the interesting part.  The interesting part is that a
failing collector and an idle collector produced the same observable: nothing.
Six days of point-in-time weather paper quotes (~1,200/day) are permanently
gone because the horizon is 90 days and they cannot be re-pulled.

So this wrapper records, per run:

  * exit code and wall time,
  * **rows written**, measured as the size of the job's own output BEFORE and
    AFTER, not inferred from stdout,
  * the last lines of stderr on failure,
  * consecutive-failure count, carried forward from the status log.

A collector that exits 0 and writes nothing is the same outcome as one that
crashes, and `manager/checks.py::check_evmap` alarms on both.

Usage
─────
    python3 -m scripts.evmap_job weather_sheet
    python3 -m scripts.evmap_job paper_maker --dry-run
    python3 -m scripts.evmap_job --list

Exit code is the job's own, so a systemd unit fails when the job fails.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
EVMAP = ROOT / "kalshi-ev-map"
DATA = EVMAP / "data"
STATUS = DATA / "job_status.jsonl"

# name -> (script, argv extras, output file used for the row count,
#          expected minimum rows ADDED by one successful run)
#
# `expect_rows` is the "a run that collects nothing is a failure" contract.
# It is 0 where a run legitimately writes nothing (the paper maker outside its
# own quoting hours, the archiver when no new market settled) and the window
# rule in checks.py handles those instead.
JOBS: Dict[str, Dict[str, Any]] = {
    "weather_sheet": {
        "script": "weather_pipeline.py",
        "args": [],
        # Written per target day, so the newest one is the run's output.
        "output_glob": "weather_fair_*.csv",
        "expect_rows": 1,
        "every": "daily 06:53 America/Chicago",
    },
    "weather_depth": {
        "script": "weather_depth.py",
        "args": [],
        "output": "weather_depth.parquet",
        "expect_rows": 1,
        "every": "09:23 and 12:23 America/Chicago",
    },
    "paper_maker": {
        "script": "weather_paper_maker.py",
        "args": [],
        "output": "paper_quotes.parquet",
        # 0 on purpose: the maker only quotes a day's market before 10:00
        # local, so late-window runs correctly add nothing. The alarm for
        # "the maker collected nothing all day" is a DAILY total in
        # checks.py, not a per-run minimum -- a per-run minimum here would
        # page every afternoon for correct behaviour.
        "expect_rows": 0,
        "every": ":13 and :43 past 07-19h America/Chicago",
    },
    "paper_eval": {
        "script": "weather_paper_eval.py",
        "args": ["--day=YESTERDAY"],
        "output_glob": "paper_eval_*.parquet",
        "expect_rows": 0,
        "every": "daily 07:37 America/Chicago",
    },
    "archive_settled": {
        "script": "archive_settled.py",
        "args": [],
        "output": "settled_archive.parquet",
        "expect_rows": 0,
        "every": "weekly Sun 03:17 America/Chicago",
    },
}


def _now() -> float:
    return time.time()


def _rows(spec: Dict[str, Any], since: Optional[float] = None) -> Optional[int]:
    """Row count of the job's output, or None if it cannot be measured.

    None is deliberate and is NEVER coerced to 0: "I could not count" and
    "there are none" are different facts, and collapsing them is how a broken
    probe reads as a healthy-but-idle one.

    Two output shapes, and they need different arithmetic:

    * ``output``      — one append-only file. Rows collected = after − before.
    * ``output_glob`` — one file PER DAY (``weather_fair_2026-07-28.csv``).
      An after-minus-before delta is meaningless there: yesterday's file and
      today's have similar row counts, so the delta is ~0 on a perfectly
      healthy run. This cost a false alarm on the first live run. With
      ``since`` set, only a file written by THIS run counts, and its own row
      count is what the run collected.
    """
    path = _output_path(spec, since=since)
    if path is None:
        return None
    if not path.exists():
        return 0
    try:
        if path.suffix == ".parquet":
            # Read the FOOTER, not the data. `pd.read_parquet(path)` loaded the
            # whole file just to call len() on it — and on 2026-07-28, the run
            # that first built `settled_archive.parquet` made that fatal: 340 MB
            # / 3.68 M rows expands past 700 MB in pandas, so this probe was
            # OOM-KILLED IN 1.4 SECONDS, before the archiver it wraps had done
            # anything at all. **The mechanism built to detect silent failure
            # became the failure**, and it would have done so every Sunday.
            # Parquet stores the row count in its metadata; this is O(1) and
            # reads no column data.
            import pyarrow.parquet as pq            # noqa: PLC0415
            return int(pq.ParquetFile(path).metadata.num_rows)
        if path.suffix == ".csv":
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return max(0, sum(1 for _ in fh) - 1)   # minus the header
    except Exception:                              # noqa: BLE001
        return None
    return None


def _output_path(spec: Dict[str, Any],
                 since: Optional[float] = None) -> Optional[Path]:
    if spec.get("output"):
        return DATA / spec["output"]
    if spec.get("output_glob"):
        hits = sorted(DATA.glob(spec["output_glob"]))
        if since is not None:
            # Only a file this run actually wrote. No slack: a slack window
            # lets YESTERDAY's file satisfy a run that wrote nothing, which is
            # the precise failure this wrapper exists to catch. Both st_mtime
            # and `since` are float seconds from the same clock, so `>=` is
            # safe without one.
            hits = [h for h in hits if h.stat().st_mtime >= since]
        # Newest by name: both globs are date-stamped, so lexical == temporal.
        return hits[-1] if hits else DATA / spec["output_glob"].replace("*", "none")
    return None


def _append(rec: Dict[str, Any]) -> None:
    """Append one ledger row and FLUSH IT TO DISK.

    `os.fsync` is not ceremony here: the whole point of the write-ahead row is
    to survive a SIGKILL, and a row sitting in the page cache when the process
    dies is a row that survives — but one sitting in Python's buffer is not.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    with open(STATUS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _prev_failures(job: str) -> int:
    """Consecutive failures immediately before this run.

    ONE RUN = ONE ``ts``. Since 2026-07-28 each run writes a write-ahead
    ``phase: "START"`` row and then its outcome row, both stamped with the
    same ``t0``, so the two must be collapsed or a completed failure would
    count twice. The outcome row always supersedes its own START.
    """
    try:
        with open(STATUS, "r", encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
    except (OSError, json.JSONDecodeError):
        return 0
    order: List[Any] = []
    latest: Dict[Any, Dict[str, Any]] = {}
    for i, rec in enumerate(rows):
        if rec.get("job") != job:
            continue
        # A row with no ts cannot be paired with anything; give it its own key.
        key = rec.get("ts") if rec.get("ts") is not None else f"_norun{i}"
        if key not in latest:
            order.append(key)
            latest[key] = rec
        elif rec.get("phase") != "START":
            latest[key] = rec            # outcome supersedes its own START
    n = 0
    for key in reversed(order):
        if latest[key].get("ok"):
            break
        n += 1
    return n


def run(job: str, dry_run: bool = False) -> int:
    spec = JOBS[job]
    script = EVMAP / "src" / spec["script"]
    args = list(spec["args"])
    if "--day=YESTERDAY" in args:
        from datetime import timedelta
        y = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        args[args.index("--day=YESTERDAY")] = f"--day={y}"

    per_day = bool(spec.get("output_glob"))
    t0 = _now()
    if dry_run:
        print(f"[evmap_job] would run: {sys.executable} {script} {' '.join(args)}")
        return 0

    # ── WRITE-AHEAD ROW: a killed wrapper must not be invisible ────────────
    #
    # 2026-07-28: this wrapper was itself OOM-KILLED, inside `_rows()` below,
    # 1.4 s after starting and before the archiver it wraps had done anything.
    # SIGKILL runs no handler, so NOTHING was written and `job_status.jsonl`
    # showed no `archive_settled` row at all — the run was INVISIBLE. The
    # ledger built so that a failing collector could not look like an idle one
    # had exactly that hole for the case where the collector is killed rather
    # than exiting non-zero.
    #
    # So the outcome is now recorded in two rows sharing one `ts`: this one,
    # written before any work, and the real one after. `ok: false` here is
    # deliberate and fail-closed — until an outcome row supersedes it, the
    # ledger's last word on this job is "started, never finished".
    #
    # `manager/collect.py` reads last-write-wins per job and needs no change:
    # a killed run leaves this row last, reports `ok: false`, and carries an
    # `empty_reason` that says what happened.
    prev_fail = _prev_failures(job)
    _append({
        "ts": t0,
        "iso": datetime.now(timezone.utc).isoformat(),
        "job": job,
        "phase": "START",
        "ok": False,
        "exit_code": None,
        "duration_s": None,
        "rows_before": None,
        "rows_after": None,
        "rows_added": None,
        "expect_rows": spec["expect_rows"],
        "empty_reason": ("run STARTED and never recorded an outcome — the "
                         "wrapper was killed (OOM / unit timeout / host died) "
                         "rather than exiting. This row is superseded the "
                         "moment the run completes either way."),
        "host": os.uname().nodename,
        "stdout_tail": [],
        "stderr_tail": [],
        "consecutive_failures": prev_fail + 1,
    })

    # A per-day job starts from zero by construction: only the file this run
    # writes counts (see `_rows`).
    before = 0 if per_day else _rows(spec)
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(EVMAP), capture_output=True, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"})
    dt = _now() - t0
    after = _rows(spec, since=t0 if per_day else None)

    delta = (after - before) if (after is not None and before is not None) else None
    ok = proc.returncode == 0
    # "Ran and collected nothing" is a failure when the job is contracted to
    # collect something. This is the second half of the 139-run lesson: exit 0
    # was never evidence of anything.
    if ok and spec["expect_rows"] > 0 and (delta is None or delta < spec["expect_rows"]):
        ok = False
        empty_reason = (f"exit 0 but rows added = {delta} < expected "
                        f"{spec['expect_rows']}")
    else:
        empty_reason = None

    rec = {
        "ts": t0,
        "iso": datetime.now(timezone.utc).isoformat(),
        "job": job,
        "phase": "END",
        "ok": ok,
        "exit_code": proc.returncode,
        "duration_s": round(dt, 2),
        "rows_before": before,
        "rows_after": after,
        "rows_added": delta,
        "expect_rows": spec["expect_rows"],
        "empty_reason": empty_reason,
        "host": os.uname().nodename,
        "stdout_tail": (proc.stdout or "").strip().splitlines()[-3:],
        "stderr_tail": (proc.stderr or "").strip().splitlines()[-6:],
        # `prev_fail` was captured before the START row was written, so
        # this cannot count that START as a prior failure of its own run.
        "consecutive_failures": (0 if ok else prev_fail + 1),
    }
    _append(rec)

    print(f"[evmap_job] {job} ok={ok} exit={proc.returncode} "
          f"rows {before} -> {after} (+{delta}) in {dt:.1f}s")
    if proc.stdout:
        print(proc.stdout.rstrip())
    if not ok:
        print(f"[evmap_job] FAILED: {empty_reason or 'nonzero exit'}",
              file=sys.stderr)
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        # A wrapper that swallows the failure recreates the bug it exists to
        # fix, so the unit must see a nonzero exit.
        return proc.returncode or 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument("job", nargs="?", choices=sorted(JOBS))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.list or not a.job:
        for name, spec in sorted(JOBS.items()):
            print(f"  {name:16s} {spec['script']:26s} every {spec['every']}")
        return 0
    return run(a.job, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
