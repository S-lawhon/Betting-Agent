#!/usr/bin/env python3
"""
research/p001_placement_probe.py
────────────────────────────────
P-001's placement rate is falling — 66.2 → 53.5 → 36.0 per week — and since the
2026-07-29 re-measurement made P-001 a LIVE gate, that rate is the gate's clock.

This establishes the FACT before anything explains it:

  * the daily series, with the window boundaries printed, so a trailing partial
    week cannot masquerade as a decline (it does in every dataset ever built);
  * the FUNNEL, so the question "did opportunities fall or did conversion
    fall?" is answered by the stage that moved rather than by a story;
  * the sport mix, because MLB is the bulk of the book and a schedule is not a
    defect;
  * the matcher fix's own contribution, since correctly rejecting 152 wrong-day
    matches is a FIX and a placement rate falling because of it is the pod
    becoming honest.

Read-only. Run on the droplet, where the logs are:

    python3 -m research.p001_placement_probe --json p001_probe.json
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import json
import os
import sys
from typing import Any, Dict

LOG_GLOB = "data/trade_logs/trade_log*.jsonl*"

# THE TRAP, and it is worth stating because it inverted the answer once already.
# `trade_log*.jsonl*` also matches the `.bak_*` snapshots the 2026-07-26/27
# correction scripts left behind — `bak_scalar_correction`, `bak_spurious_voids`,
# `bak_void_unsettleable`, four of them. Every placement from 07-22 onward is
# therefore present up to FIVE times, while 07-28's rows (written after the last
# backup) are present once. Counted raw, that makes the most recent week look
# like a RISE (210 -> 84 -> 86 -> 196) when deduplicated it is a fall
# (131 -> 60 -> 60 -> 31). Same family as the log-rotation orphan bug: a glob
# over a directory that also holds copies of itself.
#
# `.archive_*` is NOT in this list and must not be: those are genuine log
# ROTATIONS holding rows that exist nowhere else. Dropping them would replace
# a double-count with a data loss, which is the mistake in the other direction
# and just as wrong. Only true copies are excluded here; the fingerprint dedup
# below is the backstop for any residual overlap between a rotation and the
# live log.
BAK_MARKERS = (".bak_", "contaminated")
# The matcher fix went live at this instant (research/REPORT_P001_Admissibility_2026-07-29.md:
# the fixed process started 2026-07-26T21:31:32Z; the last exactly-24.00h
# tie-break row was placed 21:26:45Z, 4m47s earlier).
FIX_UTC = "2026-07-26T21:31:32"


def _open(path: str):
    return (gzip.open(path, "rt", errors="ignore") if path.endswith(".gz")
            else open(path, errors="ignore"))


def scan(root: str) -> Dict[str, Any]:
    funnel: Dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    sport_placed: Dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    sport_scanned: Dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    seen_fp: set = set()
    dupes = 0
    files = [f for f in sorted(glob.glob(os.path.join(root, LOG_GLOB)))
             if not any(m in os.path.basename(f) for m in BAK_MARKERS)]
    skipped = [os.path.basename(f) for f in sorted(glob.glob(os.path.join(root, LOG_GLOB)))
               if any(m in os.path.basename(f) for m in BAK_MARKERS)]
    for f in files:
        with _open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:                      # noqa: BLE001
                    continue
                if r.get("pod_id") != "P-001":
                    continue
                fp = r.get("fingerprint")
                if fp:
                    if fp in seen_fp:
                        dupes += 1
                        continue
                    seen_fp.add(fp)
                ts = r.get("timestamp_utc") or ""
                day = ts[:10]
                if not day:
                    continue
                act = r.get("action") or "?"
                funnel[day][act] += 1
                sp = r.get("sport") or "?"
                sport_scanned[day][sp] += 1
                if act == "PLACED":
                    sport_placed[day][sp] += 1
    return {"files_read": [os.path.basename(f) for f in files],
            "files_skipped_as_backups": skipped,
            "funnel": {d: dict(c) for d, c in funnel.items()},
            "sport_placed": {d: dict(c) for d, c in sport_placed.items()},
            "sport_scanned": {d: dict(c) for d, c in sport_scanned.items()},
            "duplicate_rows_skipped": dupes}


ACTIONS = ("PLACED", "SKIPPED_EDGE", "SKIPPED_DUPLICATE", "SKIPPED_RISK",
           "SKIPPED_CLV_GATE")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--since", default="2026-06-20")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    res = scan(a.root)
    funnel = res["funnel"]
    days = sorted(d for d in funnel if d >= a.since)
    if not days:
        print("no P-001 rows in range", file=sys.stderr)
        return 1

    print(f"read {len(res['files_read'])} log file(s); SKIPPED "
          f"{len(res['files_skipped_as_backups'])} backup/archive copies: "
          f"{res['files_skipped_as_backups']}")
    print(f"P-001 daily funnel   window {days[0]} .. {days[-1]}   "
          f"({len(days)} days with rows; duplicate fingerprints skipped: "
          f"{res['duplicate_rows_skipped']})")
    print(f"{'day':11s} {'PLACED':>7} {'SKIP_EDGE':>10} {'SKIP_DUP':>9} "
          f"{'SKIP_RISK':>10} {'SKIP_CLV':>9} {'SCANNED':>10} {'conv%':>7}")
    for d in days:
        f = funnel[d]
        tot = sum(f.values())
        pl = f.get("PLACED", 0)
        print(f"{d:11s} {pl:7d} {f.get('SKIPPED_EDGE',0):10d} "
              f"{f.get('SKIPPED_DUPLICATE',0):9d} {f.get('SKIPPED_RISK',0):10d} "
              f"{f.get('SKIPPED_CLV_GATE',0):9d} {tot:10d} "
              f"{(100*pl/tot if tot else 0):7.3f}")

    # Weekly aggregation, boundaries printed. A trailing PARTIAL week is
    # labelled as such rather than plotted as a decline.
    print("\nweekly (7-day blocks counted BACK from the last day with rows)")
    print(f"{'window':25s} {'days':>5} {'PLACED':>7} {'SCANNED':>10} "
          f"{'conv%':>7} {'/week':>8}")
    last = days[-1]
    from datetime import datetime, timedelta
    end = datetime.strptime(last, "%Y-%m-%d")
    blocks = []
    for k in range(6):
        hi = end - timedelta(days=7 * k)
        lo = hi - timedelta(days=6)
        sel = [d for d in days
               if lo.strftime("%Y-%m-%d") <= d <= hi.strftime("%Y-%m-%d")]
        if not sel:
            continue
        pl = sum(funnel[d].get("PLACED", 0) for d in sel)
        tot = sum(sum(funnel[d].values()) for d in sel)
        blocks.append((lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"),
                       len(sel), pl, tot))
    for lo, hi, n, pl, tot in blocks:
        note = "  <- PARTIAL" if n < 7 else ""
        print(f"{lo}..{hi} {n:5d} {pl:7d} {tot:10d} "
              f"{(100*pl/tot if tot else 0):7.3f} {pl*7/max(n,1):8.1f}{note}")

    print("\nPLACED by sport, last 14 days with rows")
    for d in days[-14:]:
        sp = res["sport_placed"].get(d, {})
        top = sorted(sp.items(), key=lambda kv: -kv[1])[:5]
        print(f"  {d}  {dict(top)}")

    print("\nSCANNED (all actions) by sport, last 14 days with rows")
    for d in days[-14:]:
        sp = res["sport_scanned"].get(d, {})
        top = sorted(sp.items(), key=lambda kv: -kv[1])[:5]
        print(f"  {d}  {dict(top)}")

    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1)
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
