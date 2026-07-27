#!/usr/bin/env python3
"""
scripts/void_unsettleable_positions.py
──────────────────────────────────────
Write explicit VOID terminal rows for P-002 / P-006 positions that can never
resolve.

Both pods are shelved for lack of Polymarket execution access, and **neither has
a settler that will ever drain them** — `AggregateRiskGuard`'s bootstrap skips
pods without a settler by design, so these rows sit forever as `PLACED` with no
terminal state. They distort every log-derived view (open-position lists,
portfolio P&L that nets unsettled rows, any reader that treats "no terminal
outcome" as "still live"). Measured 2026-07-28: **282 positions, $4,642.06**.

They consume no risk limit and cannot block a trade — this is bookkeeping, and
it was Sam's decision. `research/REPORT_Corrections_2026-07-27.md` §8.

Design: this **APPENDS** terminal rows; it never rewrites or deletes an existing
line. That is both how settlement normally works (a settler appends a terminal
row after the PLACED row) and the least destructive option available — the
original history stays byte-identical and the change is undone by deleting the
appended block.

Discipline, matching `apply_golf_scalar_correction.py`:

  * narrow allow-list — only `P-002` and `P-006`, no other pod can be touched;
  * `--expect N` is REQUIRED and it refuses unless exactly N positions are open,
    so it can never silently void more than was reviewed;
  * VOID is written to **both** `action` and `outcome`. Consumers disagree about
    which they read (`p017_checkpoint` keys off `action`, trade_store off
    `outcome`), and setting one and not the other is how the JDAY correction
    silently failed its first pass;
  * `pnl_usd = 0.0` — a void books no P&L, and every gate statistic in the repo
    already excludes VOIDs as "no risk taken";
  * backup first, then append; idempotent — a second run finds 0 open.

**Stop the service before running**, so nothing interleaves with the append.

    systemctl stop betting-pod-shop
    ./venv/bin/python -m scripts.void_unsettleable_positions --expect 282
    ./venv/bin/python -m scripts.void_unsettleable_positions --expect 282 --apply
    systemctl start betting-pod-shop
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

PODS = ("P-002", "P-006")          # the allow-list, all of it
TERMINAL = ("WIN", "LOSS", "VOID")
REASON = ("pod shelved without Polymarket execution access; no settler exists "
          "and this position can never resolve. Bookkeeping void, no risk was "
          "taken. See research/REPORT_Corrections_2026-07-27.md section 8.")

LIVE = Path("data/trade_logs/trade_log.jsonl")
GLOBS = [
    "data/trade_logs/trade_log*.jsonl",
    "data/trade_logs/trade_log*.jsonl.gz",
    "data/trade_logs/archive/*.jsonl",
    "data/trade_logs/archive/*.jsonl.gz",
]


def _rows(paths: List[str]):
    for p in paths:
        opener = gzip.open if p.endswith(".gz") else open
        try:
            with opener(p, "rt", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            continue


def find_open() -> List[Tuple[Tuple[str, str], Dict[str, Any]]]:
    """(pod, market) -> its latest PLACED row, where no terminal row exists."""
    files: List[str] = []
    for g in GLOBS:
        files.extend(glob.glob(g))
    placed: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
    settled: set = set()
    for rec in _rows(sorted(set(files))):
        pod = rec.get("pod_id")
        if pod not in PODS:
            continue
        mid = rec.get("market_id") or rec.get("market_ticker")
        if not mid:
            continue
        key = (pod, mid)
        ts = rec.get("timestamp_utc") or rec.get("settled_at_utc") or ""
        if (rec.get("outcome") or "").upper() in TERMINAL:
            settled.add(key)
        if (rec.get("action") or "").upper() == "PLACED":
            prev = placed.get(key)
            if prev is None or ts > prev[0]:
                placed[key] = (ts, rec)
    return [(k, v[1]) for k, v in sorted(placed.items()) if k not in settled]


def void_row(pod: str, market: str, src: Dict[str, Any],
             now_iso: str) -> Dict[str, Any]:
    return {
        "timestamp_utc": now_iso,
        "pod_id": pod,
        "pod_name": src.get("pod_name"),
        "mode": src.get("mode", "paper"),
        "venue": src.get("venue"),
        "market_id": market,
        "market_ticker": market,
        "event": src.get("event"),
        "side": src.get("side"),
        "position_size_usd": src.get("position_size_usd"),
        "fill_price": src.get("fill_price"),
        # BOTH fields. Consumers disagree about which one they read.
        "action": "VOID",
        "outcome": "VOID",
        "result": "void",
        "pnl_usd": 0.0,
        "settled_at_utc": now_iso,
        "resolution_source": "bookkeeping_void_unsettleable",
        "void_reason": REASON,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", type=int, required=True,
                    help="exact number of open positions expected; refuses on "
                         "any other count")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--log-path", type=Path, default=LIVE)
    args = ap.parse_args()

    openpos = find_open()
    by_pod: Dict[str, List[float]] = {}
    for (pod, _m), src in openpos:
        by_pod.setdefault(pod, []).append(float(src.get("position_size_usd") or 0))

    print(f"open (PLACED, no terminal row) across {PODS}:")
    for pod in sorted(by_pod):
        print(f"  {pod}: {len(by_pod[pod]):4} positions   "
              f"${sum(by_pod[pod]):>10,.2f}")
    total = sum(len(v) for v in by_pod.values())
    print(f"  TOTAL: {total} positions  "
          f"${sum(sum(v) for v in by_pod.values()):,.2f}")

    if total == 0:
        print("\n  nothing open — already voided (idempotent no-op)")
        return 0
    if total != args.expect:
        print(f"\nREFUSING: expected exactly {args.expect} open positions, "
              f"found {total}. Re-measure and re-run with the right --expect.",
              file=sys.stderr)
        return 3

    if not args.apply:
        print(f"\n[dry-run] would append {total} VOID rows to {args.log_path}")
        print("  sample:")
        for (pod, m), src in openpos[:3]:
            print(f"    {pod} {m}  size=${float(src.get('position_size_usd') or 0):.2f}")
        print("\n  Re-run with --apply to commit.")
        return 0

    log = args.log_path
    if not log.exists():
        print(f"ERROR: {log} not found", file=sys.stderr)
        return 2
    backup = log.with_suffix(
        f".jsonl.bak_void_unsettleable_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    shutil.copy2(log, backup)
    print(f"\nbackup         : {backup}")

    now_iso = datetime.now(timezone.utc).isoformat()
    with open(log, "a", encoding="utf-8") as fh:
        for (pod, m), src in openpos:
            fh.write(json.dumps(void_row(pod, m, src, now_iso), default=str) + "\n")
        fh.flush()
    print(f"appended       : {total} VOID rows (no existing line was modified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
