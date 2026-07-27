"""
manager/throughput.py
─────────────────────
**Observation throughput** per gate: how fast is each forward test actually
accumulating the thing it is counting, and when does it therefore resolve?

Why this exists
───────────────
In this project's history no forward gate has ever PASSED. Every pod that
reached a verdict got there by being killed, and on 2026-07-27 all five live
gates simultaneously read zero. The hypothesis this instrument tests is that
**the binding constraint is observation throughput, not hypothesis supply** —
in which case a 33rd candidate is worthless and the work belongs on making
existing gates resolvable.

The audit that motivated it found three gates whose rate is zero for reasons
that have nothing to do with market cadence, and none of them was visible
anywhere. That is the generalisation of the P-022 silence problem: a gate
producing nothing looks exactly like a gate patiently waiting.

Design rules, inherited from the rest of `manager/`
──────────────────────────────────────────────────
* **Derived, never hand-typed.** Counts come from the trade logs; verdicts and
  progress come from the sanctioned checkpoint readers.
* **A reader that fails returns None, not a stale number.** `rate_per_week`
  is None when it cannot be measured, and a None never becomes a projection.
* **Counting the gate's own unit.** P-017 and P-022 count *tournaments*, not
  positions; P-001 counts *admissible* rows, not settlements. Counting the
  easy thing instead of the gate's thing is how P-017 came to report `1` on
  the day it entered its first tournament.

This module is read-only.
"""
from __future__ import annotations

import collections
import glob
import gzip
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

UTC = timezone.utc

# How long a gate may legitimately produce NOTHING before silence stops being
# explainable by event cadence. These are cadence tolerances, not thresholds —
# they change no gate and gate no decision; they only decide when to speak up.
#
# P-015 is the wide one on purpose: tennis qualifying is genuinely lumpy, and
# the registry's own milestones put the next volume spike at the US Open
# (Aug 17-21). Alarming at 7d would page through every legitimate gap.
STALL_DAYS: Dict[str, float] = {
    "P-001": 3.0,     # MLB settles 5-8/day in season
    "P-014": 14.0,    # intermittent by design
    "P-015": 45.0,    # qualifying weeks are lumpy; see registry milestones
    "P-017": 21.0,    # ~1 golf event/week, settling in two waves
    "P-022": 14.0,    # 4-5 golf tournaments/week across 13 series
}


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


def _trade_log_paths(root: Path) -> List[str]:
    """Every trade log, live and archived.

    Archives matter: rotation truncates the live file, and a counter that reads
    only `trade_log.jsonl` under-reports by however much has rotated — the same
    shape of bug as the log-rotation incident that orphaned 177 positions.
    """
    pats = [
        "data/trade_logs/trade_log*.jsonl",
        "data/trade_logs/trade_log*.jsonl.gz",
        "data/trade_logs/archive/*.jsonl",
        "data/trade_logs/archive/*.jsonl.gz",
    ]
    out: List[str] = []
    for pat in pats:
        out.extend(glob.glob(str(root / pat)))
    return sorted(set(out))


def _day(ts: Any) -> Optional[str]:
    if isinstance(ts, str) and len(ts) >= 10:
        return ts[:10]
    return None


def observations_by_day(root: Path) -> Dict[str, Dict[str, int]]:
    """pod_id -> {YYYY-MM-DD: settled observations}, deduplicated.

    The unit here is a settled POSITION. Gates that count something coarser
    (P-017 and P-022 count tournaments) are converted by `_gate_series`, which
    is why this is not the number reported for them.
    """
    out: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    seen: set = set()
    for rec in _rows(_trade_log_paths(root)):
        if (rec.get("outcome") or "").upper() not in ("WIN", "LOSS", "VOID"):
            continue
        pod = rec.get("pod_id") or rec.get("pod")
        if not pod:
            continue
        ts = rec.get("settled_at_utc") or rec.get("settled_at") or rec.get("iso")
        day = _day(ts)
        if not day:
            continue
        key = (pod, rec.get("market_id") or rec.get("market_ticker"), ts)
        if key in seen:
            continue
        seen.add(key)
        out[pod][day] += 1
    return {k: dict(v) for k, v in out.items()}


def _window_count(by_day: Dict[str, int], now: datetime, days: int) -> int:
    lo = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    return sum(n for d, n in by_day.items() if d >= lo)


def _last_observation(by_day: Dict[str, int]) -> Optional[str]:
    return max(by_day) if by_day else None


def gate_throughput(root: Path, snap: Dict[str, Any],
                    registry: Dict[str, Any],
                    now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """One record per live gate: rate, projection, and whether it has stalled.

    `snap` is the collector's status snapshot — used ONLY for progress and
    threshold, both of which come from the sanctioned readers via
    `manager.checks._gate_progress`. Nothing here re-derives a gate verdict.
    """
    from manager.checks import _gate_progress          # local: avoid a cycle

    now = now or datetime.now(UTC)
    by_pod = observations_by_day(root)
    out: List[Dict[str, Any]] = []

    for ws in registry.get("workstreams", []) or []:
        pod = ws.get("id")
        gate = ws.get("gate") or {}
        if not pod or not gate or ws.get("tier") != "validating":
            continue
        threshold = gate.get("threshold")
        progress = _gate_progress(snap, pod, gate)
        by_day = by_pod.get(pod, {})

        rec: Dict[str, Any] = {
            "id": pod,
            "metric": gate.get("metric"),
            "threshold": threshold,
            # None, deliberately, when the sanctioned reader could not run.
            # A projection built on a stale progress number is worse than no
            # projection, because it looks like measurement.
            "progress": progress,
            "settled_positions_7d": _window_count(by_day, now, 7),
            "settled_positions_28d": _window_count(by_day, now, 28),
            "last_settlement_utc": _last_observation(by_day),
            "stall_days_tolerated": STALL_DAYS.get(pod),
        }

        last = rec["last_settlement_utc"]
        if last:
            try:
                gap = (now - datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=UTC)).days
            except ValueError:
                gap = None
            rec["days_since_last_settlement"] = gap
            tol = STALL_DAYS.get(pod)
            rec["stalled"] = bool(tol is not None and gap is not None and gap > tol)
        else:
            rec["days_since_last_settlement"] = None
            rec["stalled"] = None          # never produced anything: unknown

        # Realised rate in the gate's OWN unit, projected forward.
        #
        # For the position-counting gates (P-014, P-015) the settled-position
        # rate IS the gate's unit. For the tournament-counting gates (P-017,
        # P-022) and for P-001's admissibility filter it is NOT, so no
        # projection is offered rather than a wrong one — those need their
        # reader's own history, which does not exist yet.
        unit_is_positions = gate.get("metric") == "settled_trades"
        rate = None
        if unit_is_positions and rec["settled_positions_28d"] > 0:
            rate = rec["settled_positions_28d"] / 4.0
        rec["rate_per_week"] = rate

        if (rate and isinstance(progress, (int, float))
                and isinstance(threshold, (int, float)) and progress < threshold):
            weeks = (threshold - progress) / rate
            rec["weeks_to_threshold"] = round(weeks, 1)
            rec["projected_resolution_utc"] = (
                now + timedelta(weeks=weeks)).strftime("%Y-%m-%d")
            rec["resolvable_within_12m"] = weeks <= 52.0
        else:
            rec["weeks_to_threshold"] = None
            rec["projected_resolution_utc"] = None
            # Unmeasurable is NOT the same as unresolvable, and must not be
            # rendered as either "fine" or "dead".
            rec["resolvable_within_12m"] = None
        out.append(rec)
    return out


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    n_stalled = sum(1 for r in records if r.get("stalled"))
    n_zero = sum(1 for r in records if r.get("settled_positions_28d") == 0)
    return {
        "n_gates": len(records),
        "n_stalled": n_stalled,
        "n_zero_28d": n_zero,
        "n_unprojectable": sum(1 for r in records
                               if r.get("resolvable_within_12m") is None),
        "records": records,
    }
