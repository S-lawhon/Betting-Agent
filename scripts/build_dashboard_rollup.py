#!/usr/bin/env python3
"""
scripts/build_dashboard_rollup.py
─────────────────────────────────
Build ``data/dashboard/rollup.json`` — the dashboard's lifetime aggregates.

    python3 -m scripts.build_dashboard_rollup            # normal run (hourly timer)
    python3 -m scripts.build_dashboard_rollup --full     # rebuild from scratch
    python3 -m scripts.build_dashboard_rollup --dry-run --json

Why this exists at all
──────────────────────
Lifetime numbers **cannot** be read out of the trade log on demand.
``scripts/rotate_active_log.py`` caps the active log at 50 MB and rewrites it to
contain only still-open PLACED rows, keeping 12 gzipped archives — roughly
1.5 days of live traffic. And the 15-minute alerting collector
(``manager/collect.py::trade_activity``) survives only because it reverse-scans
with a hard 24-hour early exit inside an 80 MB budget; removing that cutoff to
get lifetime figures would put an unbounded read on the critical path of the
thing that pages when production is down.

So: a separate job, on its own timer, with its own memory cap.

⚠  THIS FILE BECOMES THE SYSTEM OF RECORD FOR PRE-HORIZON HISTORY.  ⚠
Once an archive is pruned, the rows it held survive **only** in these counters
and are not derivable from anything on disk. ``--full`` can rebuild only from
surviving archives, so a full rebuild silently shortens history. Back this file
up. ``--backup`` writes ``rollup.json.bak.gz`` for exactly this reason.

How it stays correct — the archive/tail split
─────────────────────────────────────────────
``rotate_active_log.rotate()`` gzips the active log **verbatim** into
``trade_log.archive_<ts>.jsonl.gz`` and then rewrites the active log to hold
only the still-open PLACED rows (``open_placed_rows``, same fingerprints). Two
consequences drive the whole design:

1. **A row can live in an archive AND in the current active log.** Any
   long-open PLACED row is re-archived on every rotation and re-carried into
   every new active log.
2. **Byte offsets into the active log are not a safe checkpoint.** The first
   version of this script kept ``(dev, inode, offset)`` per source. It
   double-counted: rows consumed from the active log at offset O were counted a
   second time when the archive containing those same bytes appeared as a
   "new" immutable source. Measured on a fixture: placed 4 → 6, realized P&L
   5.25 → 10.50 after one rotation.

The fix is to stop being incremental about the mutable file:

* **Archives are immutable** — consumed exactly once, keyed by filename, then
  marked ``complete`` and never reopened. Their counters persist in
  ``_archived``.
* **The active log is re-read in full on every run.** It is capped at 50 MB by
  rotation, so this is bounded work (a few seconds at ``Nice=15`` +
  ``IOSchedulingClass=idle``), and it makes offset drift, truncation and
  rotation races structurally impossible rather than merely handled.
* **PLACED rows are deduplicated by fingerprint** against the set of
  still-open placements as of the last archive boundary
  (``_archived.open_placed_fingerprints``). That is what stops a carried row
  being counted once in the archive and again in the tail — and it mirrors both
  ``TradeStore._index_entry``'s dedupe key and ``open_placed_rows``'s own
  semantics, ``BANKROLL_RESET`` included.

The published payload is ``archived + tail``; ``_archived`` is the restore point
for the next run. ``tests/test_dashboard_rollup.py`` asserts no double-count
across a simulated rotation. That test must never be skipped.

What it emits
─────────────
Counters only — no verdicts, no thresholds, no gate evaluation. Gate numbers
come from the sanctioned checkpoint readers via ``manager/state/status.json``
(Gate Standard §1: a dashboard tile is not a verdict). This file answers
"how much, when, and why not", never "did it work".
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.throughput import _trade_log_paths  # noqa: E402
from scripts.rotate_active_log import _atomic_write  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

UTC = timezone.utc

SCHEMA = 2
DEFAULT_OUT = "data/dashboard/rollup.json"
ACTIVE_LOG_REL = "data/trade_logs/trade_log.jsonl"
CLV_LOG_REL = "data/trade_logs/clv_log.jsonl"

#: Trades the aggregate risk guard WOULD have blocked while running in
#: shadow mode (``aggregate_risk.enforce: false``).
#:
#: This has to be its own source. Under enforcement these showed up in the
#: trade log as skip_reason rows ("aggregate risk guard",
#: "aggregate_risk_limit"); in shadow mode the pod is approved and PLACES
#: the trade, so those rows stop being written entirely. Without reading
#: this file the dashboard's skip panel simply goes quiet and the
#: constraint looks like it disappeared — which is the opposite of what
#: shadow mode is for.
SHADOW_LOG_REL = "data/trade_logs/aggregate_risk_shadow.jsonl"

#: NOTHING ROTATES THE SHADOW LOG. It is recomputed from scratch each run
#: (like CLV), so it gets slower as it grows — roughly 2-3 MB/day at the
#: measured block rate. Warn rather than silently take longer; if this
#: fires, either enforcement has been off for a long time or the caps need
#: revisiting.
SHADOW_LOG_WARN_BYTES = 100 * 1024 * 1024

#: Read files in bounded chunks so a 50 MB log never lands in RAM whole.
CHUNK_BYTES = 4_000_000

#: Loud warning if the active log has grown past what rotation should allow.
#: ``rotate_active_log.py``'s own docstring says it is "NOT deployed", while
#: ``manager/registry.yaml`` lists it as a 06:00 cron — if the cron is in fact
#: absent, the file grows without bound and the tail re-read gets slower every
#: day. Better to say so than to quietly take longer.
ACTIVE_LOG_WARN_BYTES = 200 * 1024 * 1024

#: Retention for the per-day breakdowns that exist only to make short windows
#: derivable. Lifetime totals and the daily equity series are kept in full.
SKIP_DAILY_DAYS = 45
DAILY_BY_POD_DAYS = 120

SETTLE_OUTCOMES = ("WIN", "LOSS", "VOID")

#: ``TradeLogSchema._DEFAULTS`` assigns unlabelled rows to P-001. Honour that
#: default (630 legacy rows in the March log carry no pod_id) but count them, so
#: the attribution is auditable rather than silent.
DEFAULT_POD = "P-001"


# ── small helpers ─────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _day(ts: Any) -> Optional[str]:
    """``YYYY-MM-DD`` from an ISO-ish string, without a full parse."""
    if isinstance(ts, str) and len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        return ts[:10]
    return None


def _week(day: str) -> Optional[str]:
    """ISO year-week (``2026-W31``) from ``YYYY-MM-DD``."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None
    y, w, _ = d.isocalendar()
    return "{}-W{:02d}".format(y, w)


def _f(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rel(root: Path, path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return str(path)


def archive_sources(root: Path) -> List[str]:
    """Every archived trade log, oldest first; the live file excluded.

    Enumeration reuses ``manager.throughput._trade_log_paths`` so the dashboard
    and the throughput reader agree on which files exist — including both
    archive naming conventions (``trade_log.archive_*.jsonl.gz`` from
    ``rotate_active_log.py``'s rotation and ``archive/YYYY-MM.jsonl.gz`` from
    its consolidate-at-prune step; legacy monthly files predating the
    consolidation manifest are plain sources).

    Order is load-bearing: the PLACED dedupe depends on seeing an open
    placement before the copy of it that the next rotation carried forward.
    ``trade_log.archive_YYYYmmdd_HHMMSS`` and ``archive/YYYY-MM`` are both
    lexicographically chronological within their own convention, and the
    monthly archives are always older, so a plain sort is chronological — but
    it is asserted here rather than assumed.
    """
    active = str(root / ACTIVE_LOG_REL)
    monthly, rotated = [], []
    for p in _trade_log_paths(root):
        if p == active:
            continue
        (monthly if "/archive/" in p.replace("\\", "/") else rotated).append(p)
    return sorted(monthly) + sorted(rotated)


# ── the accumulator ───────────────────────────────────────────────────


class Rollup:
    """Streaming counters. Everything here is O(pods x days), never O(rows)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.by_action: collections.Counter = collections.Counter()
        self.rows = 0
        self.pod: Dict[str, Dict[str, Any]] = {}
        self.daily: Dict[str, Dict[str, float]] = {}
        self.daily_pod: Dict[str, Dict[str, Dict[str, float]]] = {}
        self.weekly_pod: Dict[str, Dict[str, Dict[str, float]]] = {}
        self.skip_lifetime: Dict[str, collections.Counter] = {}
        self.skip_daily: Dict[str, collections.Counter] = {}
        #: Still-open PLACED fingerprints. The rotation dedupe key.
        self.open_placed: set = set()
        # provenance / honesty counters
        self.rows_missing_pod = 0
        self.rows_missing_day = 0
        self.rows_bad_json = 0
        self.rows_with_skip_reason = 0
        self.skipped_carried_placed = 0
        self.clv: Dict[str, Dict[str, float]] = {}
        self.clv_week: Dict[str, Dict[str, Dict[str, float]]] = {}

    # -- bucket accessors ---------------------------------------------

    def _pod(self, pid: str) -> Dict[str, Any]:
        b = self.pod.get(pid)
        if b is None:
            b = self.pod[pid] = {
                "placed": 0, "settled": 0, "wins": 0, "losses": 0, "voids": 0,
                "skipped": 0, "realized_pnl": 0.0, "gross_wagered_usd": 0.0,
                "contracts": 0.0,
                "first_placed_utc": None, "last_placed_utc": None,
                "last_settled_utc": None,
            }
        return b

    def _daily(self, day: str) -> Dict[str, float]:
        b = self.daily.get(day)
        if b is None:
            b = self.daily[day] = {"realized_pnl": 0.0, "settled": 0,
                                   "placed": 0, "skipped": 0}
        return b

    def _daily_pod(self, pid: str, day: str) -> Dict[str, float]:
        pod = self.daily_pod.setdefault(pid, {})
        b = pod.get(day)
        if b is None:
            b = pod[day] = {"realized_pnl": 0.0, "settled": 0, "placed": 0}
        return b

    def _weekly_pod(self, pid: str, week: str) -> Dict[str, float]:
        pod = self.weekly_pod.setdefault(pid, {})
        b = pod.get(week)
        if b is None:
            b = pod[week] = {"placed": 0, "skipped": 0, "settled": 0,
                             "realized_pnl": 0.0}
        return b

    # -- the hot path -------------------------------------------------

    def add(self, rec: Dict[str, Any]) -> None:
        """Fold one trade-log row into the counters."""
        action = str(rec.get("action") or "").upper()
        outcome = str(rec.get("outcome") or "").upper()

        # The rotation dedupe runs FIRST, before `rows` and `by_action` are
        # touched. A row that rotation carried forward is not a new observation
        # in any sense, so it must not inflate the raw row count or the action
        # histogram either — only `carried_placed_rows_skipped`, where it is
        # visible as the guard working.
        if action in ("PLACED", "PAPER_PLACED"):
            fp_dedupe = rec.get("fingerprint")
            if fp_dedupe and fp_dedupe in self.open_placed:
                self.skipped_carried_placed += 1
                return

        self.rows += 1
        self.by_action[action or "(none)"] += 1

        if action == "BANKROLL_RESET":
            # Mirrors rotate_active_log.open_placed_rows: a reset means the
            # prior positions are no longer tracked, so they can no longer be
            # carried forward and must not suppress a future PLACED row.
            self.open_placed.clear()
            return

        pid_raw = rec.get("pod_id") or rec.get("pod")
        if not pid_raw:
            self.rows_missing_pod += 1
        pid = str(pid_raw or DEFAULT_POD)

        is_settlement = outcome in SETTLE_OUTCOMES or action in SETTLE_OUTCOMES
        ts = (rec.get("settled_at_utc") or rec.get("settled_at")) if is_settlement \
            else rec.get("timestamp_utc")
        day = _day(ts) or _day(rec.get("timestamp_utc"))
        if day is None:
            self.rows_missing_day += 1

        # ---- skip telemetry (~94% of all rows, previously invisible) ----
        if action.startswith("SKIPPED"):
            reason = rec.get("skip_reason") or action
            if rec.get("skip_reason"):
                self.rows_with_skip_reason += 1
            self._pod(pid)["skipped"] += 1
            self.skip_lifetime.setdefault(pid, collections.Counter())[reason] += 1
            if day:
                self._daily(day)["skipped"] += 1
                self.skip_daily.setdefault(day, collections.Counter())[reason] += 1
                wk = _week(day)
                if wk:
                    self._weekly_pod(pid, wk)["skipped"] += 1
            return

        # ---- settlements ----
        if is_settlement:
            pnl = _f(rec.get("pnl_usd"))
            if not pnl:
                extra = rec.get("extra")
                if isinstance(extra, dict):
                    pnl = _f(extra.get("pnl_usd"))
            resolved = outcome if outcome in SETTLE_OUTCOMES else action
            b = self._pod(pid)
            b["settled"] += 1
            b["realized_pnl"] += pnl
            b["last_settled_utc"] = ts or b["last_settled_utc"]
            if resolved == "WIN":
                b["wins"] += 1
            elif resolved == "LOSS":
                b["losses"] += 1
            else:
                b["voids"] += 1
            fp = rec.get("fingerprint")
            if fp:
                self.open_placed.discard(fp)
            if day:
                d = self._daily(day)
                d["settled"] += 1
                d["realized_pnl"] += pnl
                dp = self._daily_pod(pid, day)
                dp["settled"] += 1
                dp["realized_pnl"] += pnl
                wk = _week(day)
                if wk:
                    w = self._weekly_pod(pid, wk)
                    w["settled"] += 1
                    w["realized_pnl"] += pnl
            return

        # ---- placements ----
        if action in ("PLACED", "PAPER_PLACED"):
            # Carried duplicates were already filtered at the top of add().
            fp = rec.get("fingerprint")
            if fp:
                self.open_placed.add(fp)
            b = self._pod(pid)
            b["placed"] += 1
            b["gross_wagered_usd"] += _f(rec.get("position_size_usd"))
            b["contracts"] += _f(rec.get("contracts"))
            b["last_placed_utc"] = ts or b["last_placed_utc"]
            if b["first_placed_utc"] is None:
                b["first_placed_utc"] = ts
            if day:
                self._daily(day)["placed"] += 1
                self._daily_pod(pid, day)["placed"] += 1
                wk = _week(day)
                if wk:
                    self._weekly_pod(pid, wk)["placed"] += 1
            return

        # Anything else (telemetry such as DATA_COLLECTION) is counted in
        # by_action and deliberately nowhere else.

    # -- CLV ----------------------------------------------------------

    def add_clv(self, rec: Dict[str, Any]) -> None:
        pid = str(rec.get("pod_id") or DEFAULT_POD)
        # Field names come from clv_log.jsonl itself (pinn_fair_close /
        # clv_net_maker), NOT from src/clv.py's CLV_FIELDS — the two differ and
        # the log's names are the ones actually on disk.
        gross = rec.get("clv_gross")
        net = rec.get("clv_net_maker")
        if gross is None and net is None:
            return
        b = self.clv.get(pid)
        if b is None:
            b = self.clv[pid] = {"n": 0, "clv_gross_sum": 0.0,
                                 "clv_net_maker_sum": 0.0, "n_settled": 0}
        b["n"] += 1
        b["clv_gross_sum"] += _f(gross)
        b["clv_net_maker_sum"] += _f(net)
        if rec.get("outcome"):
            b["n_settled"] += 1
        day = _day(rec.get("settled_at")) or _day(rec.get("commence"))
        wk = _week(day) if day else None
        if wk:
            w = self.clv_week.setdefault(pid, {}).setdefault(
                wk, {"n": 0, "clv_gross_sum": 0.0, "clv_net_maker_sum": 0.0})
            w["n"] += 1
            w["clv_gross_sum"] += _f(gross)
            w["clv_net_maker_sum"] += _f(net)

    # -- serialisation ------------------------------------------------

    def _trim(self) -> None:
        """Drop per-day detail that exists only to make windows derivable.

        Safe because archives are consumed oldest-first and never re-read, and
        the tail only ever adds newer days — a trimmed day cannot come back.
        """
        if self.skip_daily:
            keepset = set(sorted(self.skip_daily)[-SKIP_DAILY_DAYS:])
            for d in [d for d in self.skip_daily if d not in keepset]:
                del self.skip_daily[d]
        for pid, days in list(self.daily_pod.items()):
            if len(days) > DAILY_BY_POD_DAYS:
                keepset = set(sorted(days)[-DAILY_BY_POD_DAYS:])
                self.daily_pod[pid] = {d: v for d, v in days.items()
                                       if d in keepset}

    def core(self) -> Dict[str, Any]:
        """The counter blocks. Round-trips through ``restore``."""
        self._trim()

        lifetime = {
            "rows": self.rows,
            "by_action": dict(sorted(self.by_action.items(),
                                     key=lambda kv: -kv[1])),
            "placed": sum(b["placed"] for b in self.pod.values()),
            "skipped": sum(b["skipped"] for b in self.pod.values()),
            "settled": sum(b["settled"] for b in self.pod.values()),
            "wins": sum(b["wins"] for b in self.pod.values()),
            "losses": sum(b["losses"] for b in self.pod.values()),
            "voids": sum(b["voids"] for b in self.pod.values()),
            "realized_pnl": round(sum(b["realized_pnl"]
                                      for b in self.pod.values()), 4),
            "gross_wagered_usd": round(sum(b["gross_wagered_usd"]
                                           for b in self.pod.values()), 2),
        }

        pods = {}
        for pid, b in sorted(self.pod.items()):
            out = dict(b)
            out["realized_pnl"] = round(b["realized_pnl"], 4)
            out["gross_wagered_usd"] = round(b["gross_wagered_usd"], 2)
            out["contracts"] = round(b["contracts"], 2)
            pods[pid] = out

        return {
            "lifetime": lifetime,
            "by_pod": pods,
            "daily": [
                [d, round(v["realized_pnl"], 4), int(v["settled"]),
                 int(v["placed"]), int(v["skipped"])]
                for d, v in sorted(self.daily.items())
            ],
            "daily_columns": ["day", "realized_pnl", "settled", "placed",
                              "skipped"],
            "daily_by_pod": {
                pid: {d: {"realized_pnl": round(v["realized_pnl"], 4),
                          "settled": int(v["settled"]),
                          "placed": int(v["placed"])}
                      for d, v in sorted(days.items())}
                for pid, days in sorted(self.daily_pod.items())
            },
            "weekly_by_pod": {
                pid: {w: {"placed": int(v["placed"]),
                          "skipped": int(v["skipped"]),
                          "settled": int(v["settled"]),
                          "realized_pnl": round(v["realized_pnl"], 4)}
                      for w, v in sorted(weeks.items())}
                for pid, weeks in sorted(self.weekly_pod.items())
            },
            "skip_reasons": {
                "lifetime_by_pod": {
                    pid: dict(sorted(c.items(), key=lambda kv: -kv[1]))
                    for pid, c in sorted(self.skip_lifetime.items())
                },
                "by_day": {d: dict(sorted(c.items(), key=lambda kv: -kv[1]))
                           for d, c in sorted(self.skip_daily.items())},
                "by_day_retention_days": SKIP_DAILY_DAYS,
            },
            "clv": {
                "by_pod": {pid: dict(b) for pid, b in sorted(self.clv.items())},
                "by_week": {pid: {w: dict(v) for w, v in sorted(weeks.items())}
                            for pid, weeks in sorted(self.clv_week.items())},
            },
            "coverage": {
                "rows_total": self.rows,
                "rows_with_skip_reason": self.rows_with_skip_reason,
                "rows_missing_pod_id": self.rows_missing_pod,
                "rows_missing_day": self.rows_missing_day,
                "rows_unparseable": self.rows_bad_json,
                "carried_placed_rows_skipped": self.skipped_carried_placed,
                "note": ("rows_missing_pod_id were attributed to " + DEFAULT_POD
                         + " per TradeLogSchema._DEFAULTS; "
                         "carried_placed_rows_skipped is the rotation "
                         "double-count guard doing its job, not an error"),
            },
            "open_placed_fingerprints": sorted(self.open_placed),
        }

    # -- restore ------------------------------------------------------

    @classmethod
    def restore(cls, root: Path, payload: Optional[Dict[str, Any]]) -> "Rollup":
        """Rehydrate counters from a ``core()`` blob."""
        r = cls(root)
        if not isinstance(payload, dict):
            return r
        lt = payload.get("lifetime") or {}
        r.rows = int(lt.get("rows") or 0)
        r.by_action = collections.Counter(
            {k: int(v) for k, v in (lt.get("by_action") or {}).items()})
        for pid, b in (payload.get("by_pod") or {}).items():
            bucket = r._pod(str(pid))
            for k in ("placed", "settled", "wins", "losses", "voids", "skipped"):
                bucket[k] = int(b.get(k) or 0)
            bucket["realized_pnl"] = _f(b.get("realized_pnl"))
            bucket["gross_wagered_usd"] = _f(b.get("gross_wagered_usd"))
            bucket["contracts"] = _f(b.get("contracts"))
            for k in ("first_placed_utc", "last_placed_utc", "last_settled_utc"):
                bucket[k] = b.get(k)
        for row in (payload.get("daily") or []):
            if not isinstance(row, list) or len(row) < 5:
                continue
            r.daily[str(row[0])] = {"realized_pnl": _f(row[1]),
                                    "settled": int(row[2] or 0),
                                    "placed": int(row[3] or 0),
                                    "skipped": int(row[4] or 0)}
        for pid, days in (payload.get("daily_by_pod") or {}).items():
            for d, v in days.items():
                b = r._daily_pod(str(pid), str(d))
                b["realized_pnl"] = _f(v.get("realized_pnl"))
                b["settled"] = int(v.get("settled") or 0)
                b["placed"] = int(v.get("placed") or 0)
        for pid, weeks in (payload.get("weekly_by_pod") or {}).items():
            for w, v in weeks.items():
                b = r._weekly_pod(str(pid), str(w))
                b["placed"] = int(v.get("placed") or 0)
                b["skipped"] = int(v.get("skipped") or 0)
                b["settled"] = int(v.get("settled") or 0)
                b["realized_pnl"] = _f(v.get("realized_pnl"))
        sr = payload.get("skip_reasons") or {}
        for pid, c in (sr.get("lifetime_by_pod") or {}).items():
            r.skip_lifetime[str(pid)] = collections.Counter(
                {k: int(v) for k, v in c.items()})
        for d, c in (sr.get("by_day") or {}).items():
            r.skip_daily[str(d)] = collections.Counter(
                {k: int(v) for k, v in c.items()})
        cov = payload.get("coverage") or {}
        r.rows_with_skip_reason = int(cov.get("rows_with_skip_reason") or 0)
        r.rows_missing_pod = int(cov.get("rows_missing_pod_id") or 0)
        r.rows_missing_day = int(cov.get("rows_missing_day") or 0)
        r.rows_bad_json = int(cov.get("rows_unparseable") or 0)
        r.skipped_carried_placed = int(cov.get("carried_placed_rows_skipped") or 0)
        r.open_placed = set(payload.get("open_placed_fingerprints") or [])
        # CLV is recomputed from scratch every run (the log is small and
        # unrotated), so it is deliberately NOT restored here.
        return r


# ── readers ───────────────────────────────────────────────────────────


def _iter_gz_or_plain(path: str) -> Iterable[str]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:  # type: ignore[operator]
        for line in fh:
            yield line


class _ByteRange:
    """A read-only window of *length* bytes over an already-seeked file."""

    def __init__(self, fh, length: int) -> None:
        self._fh = fh
        self._left = length

    def read(self, n: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        if n is None or n < 0:
            n = self._left
        data = self._fh.read(min(n, self._left))
        self._left -= len(data)
        return data


def _iter_monthly_member_lines(path: str, offset: int,
                               length: int) -> Iterable[str]:
    """Stream the lines of ONE consolidated member of a monthly archive.

    ``rotate_active_log`` appends each pruned rotated archive to
    ``archive/YYYY-MM.jsonl.gz`` verbatim, as an independent gzip member, and
    records its byte range in the consolidation manifest. Decompression is
    streamed — a member can be a 60 MB gzip holding hundreds of MB of rows,
    and this job runs under a memory cap.
    """
    import io
    with open(path, "rb") as fh:
        fh.seek(offset)
        gz = gzip.GzipFile(fileobj=_ByteRange(fh, length))  # type: ignore[arg-type]
        with io.TextIOWrapper(gz, encoding="utf-8", errors="replace") as txt:
            for line in txt:
                yield line


#: ``None`` (file unreadable) is different from ``{}`` (no manifest yet):
#: with no manifest, every monthly file is a legacy plain source; with an
#: unreadable one, unconsumed monthly files must be SKIPPED, because reading
#: one as a plain source would double-count every member the rotated-archive
#: ingestion already counted.
MANIFEST_REL = "data/trade_logs/archive/consolidated_manifest.json"


def _consolidation_manifest(root: Path) -> Optional[Dict[str, Any]]:
    path = root / MANIFEST_REL
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        monthly = data.get("monthly") if isinstance(data, dict) else None
        return monthly if isinstance(monthly, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("cannot read %s (%s) — unconsumed monthly archives "
                       "will be skipped until it is readable again",
                       MANIFEST_REL, exc)
        return None


def _consume_lines(roll: Rollup, lines: Iterable[str]) -> int:
    n = 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            roll.rows_bad_json += 1
            continue
        if not isinstance(rec, dict):
            roll.rows_bad_json += 1
            continue
        roll.add(rec)
        n += 1
    return n


def _iter_file_chunked(path: Path) -> Iterable[str]:
    """Yield complete lines from *path*, never holding more than CHUNK_BYTES."""
    leftover = b""
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_BYTES)
            if not chunk:
                break
            parts = (leftover + chunk).split(b"\n")
            leftover = parts.pop()
            for p in parts:
                yield p.decode("utf-8", errors="replace")
    if leftover.strip():
        yield leftover.decode("utf-8", errors="replace")


# ── the job ───────────────────────────────────────────────────────────


def _shadow_risk(root: Path) -> Dict[str, Any]:
    """Aggregate the aggregate-risk shadow log.

    Deliberately NOT part of the archive/tail checkpoint machinery: this
    file is append-only, is never rotated, and carries no fingerprints to
    deduplicate on. Recomputed in full each run, like CLV.

    ``available: False`` means the file is absent — which is the normal
    state when the guard is ENFORCING, since nothing writes it then.
    """
    path = root / SHADOW_LOG_REL
    out: Dict[str, Any] = {
        "available": False,
        "by_day_retention_days": SKIP_DAILY_DAYS,
        "source": SHADOW_LOG_REL,
        "note": (
            "Trades the aggregate risk guard would have blocked and let "
            "through (aggregate_risk.enforce: false). Verdicts are measured "
            "against the UNCONSTRAINED book, so they identify which trades "
            "touched a limit — not what a constrained portfolio would have "
            "held. Pod-level caps (e.g. P-017 max_open_positions) are part "
            "of each strategy's spec and still apply; they are not counted "
            "here."
        ),
    }
    if not path.exists():
        return out

    try:
        size = path.stat().st_size
        if size > SHADOW_LOG_WARN_BYTES:
            logger.warning(
                "aggregate-risk shadow log is %.0f MB and nothing rotates it "
                "— this rebuild re-reads it in full every run",
                size / 1e6)
    except OSError:
        size = 0

    by_kind: Dict[str, int] = {}
    by_pod: Dict[str, Dict[str, int]] = {}
    by_day: Dict[str, Dict[str, int]] = {}
    usd = 0.0
    rows = 0
    unparseable = 0
    last_utc: Optional[str] = None

    try:
        for raw in _iter_file_chunked(path):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                unparseable += 1
                continue
            if not isinstance(rec, dict):
                unparseable += 1
                continue

            rows += 1
            kind = str(rec.get("breach_kind") or "unknown")
            pid = str(rec.get("pod_id") or DEFAULT_POD)
            ts = rec.get("timestamp_utc")

            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_pod.setdefault(pid, {})
            by_pod[pid][kind] = by_pod[pid].get(kind, 0) + 1
            usd += _f(rec.get("position_size_usd"))

            day = _day(ts)
            if day:
                by_day.setdefault(day, {})
                by_day[day][kind] = by_day[day].get(kind, 0) + 1
            if isinstance(ts, str) and (last_utc is None or ts > last_utc):
                last_utc = ts
    except OSError as exc:
        logger.warning("cannot read %s: %s", SHADOW_LOG_REL, exc)
        return out

    # Same retention as skip_reasons.by_day — lifetime totals stay whole.
    if len(by_day) > SKIP_DAILY_DAYS:
        for stale in sorted(by_day)[:-SKIP_DAILY_DAYS]:
            by_day.pop(stale, None)

    out.update({
        "available": True,
        "lifetime": {
            "total": rows,
            "usd_passed_through": round(usd, 2),
            "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
            "by_pod": {p: dict(sorted(c.items(), key=lambda kv: -kv[1]))
                       for p, c in sorted(by_pod.items())},
        },
        "by_day": {d: dict(sorted(c.items(), key=lambda kv: -kv[1]))
                   for d, c in sorted(by_day.items())},
        "last_utc": last_utc,
        "rows_unparseable": unparseable,
        "bytes": size,
    })
    return out


def build(root: Path, out_path: Path,
          full: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the rollup. Returns ``(payload, summary)``; writes nothing."""
    root = Path(root).resolve()

    prior: Optional[Dict[str, Any]] = None
    if not full and out_path.exists():
        try:
            candidate = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and candidate.get("schema") == SCHEMA:
                prior = candidate
            else:
                logger.warning("existing rollup has schema %r, expected %d — "
                               "rebuilding from scratch",
                               (candidate or {}).get("schema"), SCHEMA)
        except (OSError, ValueError) as exc:
            logger.warning("cannot read %s (%s) — rebuilding from scratch",
                           out_path, exc)

    if prior is None:
        roll = Rollup(root)
        arch_sources: Dict[str, Any] = {}
        if full:
            logger.warning(
                "FULL REBUILD: pre-horizon history is NOT recoverable. Rows "
                "from archives already pruned by rotate_active_log are gone "
                "from these counters. This is why rollup.json must be backed up.")
    else:
        roll = Rollup.restore(root, prior.get("_archived"))
        arch_sources = dict((prior.get("_checkpoint") or {})
                            .get("archived_sources") or {})

    # ── 1. immutable archives, consumed exactly once ──────────────────
    #
    # Two source kinds since consolidation-at-prune landed:
    #
    # * plain sources (rotated archives, legacy monthly files): immutable,
    #   consumed once by filename — unchanged behaviour;
    # * manifest-managed monthly files (archive/YYYY-MM.jsonl.gz written by
    #   rotate_active_log's prune): these GROW, one gzip member per pruned
    #   rotated archive. They are consumed per MEMBER, and a member whose
    #   rotated original was already counted is marked done without reading —
    #   its rows are the same bytes. A member whose original was pruned
    #   before this job ever saw it is read from the monthly file, which is
    #   the outage-recovery path: those rows used to be lost.
    manifest_members = _consolidation_manifest(root)
    archives_added = 0
    members_counted = 0
    touched: List[str] = []
    for abs_path in archive_sources(root):
        rel = _rel(root, abs_path)
        name = Path(abs_path).name
        is_monthly = "/archive/" in rel.replace("\\", "/")
        entry = arch_sources.get(rel)

        managed = manifest_members.get(name) if (is_monthly and manifest_members) else None
        if is_monthly and manifest_members is None and not (
                entry and entry.get("state") in ("complete", "counted_pruned")):
            logger.warning("skipping monthly %s: consolidation manifest unreadable", rel)
            continue

        if managed is not None:
            if entry and entry.get("state") in ("complete", "counted_pruned"):
                # Consumed whole as a plain source before the manifest existed.
                # Terminal: its pre-manifest rows are counted; members added
                # since resolve through their rotated originals as usual.
                continue
            if not entry or entry.get("state") != "managed":
                entry = arch_sources[rel] = {"state": "managed", "members": {}}
            members_state = entry.setdefault("members", {})
            new_rows = 0
            for mem in managed.get("members") or []:
                aname = mem.get("archive")
                if not aname or aname in members_state:
                    continue
                if (root / "data" / "trade_logs" / aname).exists():
                    # The rotated original survived (crash between manifest
                    # write and unlink, or a --full rebuild racing a prune).
                    # Let the plain pass below count it; this member resolves
                    # via its state on a later run.
                    continue
                arch_rel = _rel(root, str(root / "data" / "trade_logs" / aname))
                src = arch_sources.get(arch_rel)
                if src and src.get("state") in ("complete", "counted_pruned"):
                    members_state[aname] = {"rows": src.get("rows"), "via": "rotated"}
                    members_counted += 1
                    continue
                off = int(mem.get("offset") or 0)
                ln = int(mem.get("length") or 0)
                try:
                    if off + ln > Path(abs_path).stat().st_size:
                        logger.warning(
                            "monthly %s: member %s claims bytes %d..%d beyond "
                            "EOF — manifest ahead of the file, retrying next run",
                            rel, aname, off, off + ln)
                        continue
                    n = _consume_lines(
                        roll, _iter_monthly_member_lines(abs_path, off, ln))
                except OSError as exc:
                    logger.warning("cannot read member %s of %s: %s", aname, rel, exc)
                    continue
                members_state[aname] = {"rows": n, "via": "monthly"}
                arch_sources[arch_rel] = {"state": "counted_pruned", "rows": n,
                                          "via_monthly": rel}
                members_counted += 1
                archives_added += n
                new_rows += n
            if new_rows:
                touched.append(rel)
            continue

        if entry and entry.get("state") in ("complete", "counted_pruned"):
            continue
        try:
            n = _consume_lines(roll, _iter_gz_or_plain(abs_path))
        except OSError as exc:
            logger.warning("cannot read archive %s: %s", rel, exc)
            continue
        arch_sources[rel] = {"state": "complete", "rows": n}
        archives_added += n
        touched.append(rel)

    # ── 2. archives that have since been pruned ───────────────────────
    pruned = 0
    for rel, entry in arch_sources.items():
        if entry.get("state") == "counted_pruned":
            pruned += 1
            continue
        if not (root / rel).exists():
            entry["state"] = "counted_pruned"
            pruned += 1
            logger.info("archive %s has been pruned; its %s rows stay in the "
                        "counters and can never be re-added",
                        rel, entry.get("rows"))

    # Snapshot the archived-only state. This is the restore point, and it is
    # taken BEFORE the tail is folded in — that ordering is the whole reason
    # the tail can be re-read every run without double-counting.
    archived_blob = roll.core()

    # ── 3. the live log, re-read in full every run ────────────────────
    active = root / ACTIVE_LOG_REL
    tail_rows = 0
    active_bytes = 0
    if active.exists():
        try:
            active_bytes = active.stat().st_size
            if active_bytes > ACTIVE_LOG_WARN_BYTES:
                logger.warning(
                    "active log is %.0f MB — rotation (rotate_active_log.py, "
                    "50 MB cap) may not be running on this host; the full "
                    "tail re-read will keep getting slower",
                    active_bytes / 1e6)
            tail_rows = _consume_lines(roll, _iter_file_chunked(active))
            touched.append(ACTIVE_LOG_REL)
        except OSError as exc:
            logger.warning("cannot read %s: %s", ACTIVE_LOG_REL, exc)

    # ── 4. CLV (small, unrotated — recomputed each run) ───────────────
    clv_path = root / CLV_LOG_REL
    clv_rows = 0
    if clv_path.exists():
        try:
            for raw in _iter_file_chunked(clv_path):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    roll.add_clv(rec)
                    clv_rows += 1
        except OSError as exc:
            logger.warning("cannot read %s: %s", CLV_LOG_REL, exc)

    checkpoint = {
        "schema": SCHEMA,
        "archived_sources": arch_sources,
        "archive_rows_added_this_run": archives_added,
        "archives_complete": sum(1 for e in arch_sources.values()
                                 if e.get("state") == "complete"),
        "monthly_members_counted_this_run": members_counted,
        "pruned_sources_counted": pruned,
        "sources_touched": touched,
        "active_log_rows_this_run": tail_rows,
        "active_log_bytes": active_bytes,
        "clv_rows_this_run": clv_rows,
        "strategy": ("archives are immutable and counted once; the active log "
                     "is re-read in full every run, and PLACED rows are "
                     "deduplicated by fingerprint against the open set at the "
                     "last archive boundary"),
        "horizon_note": (
            "Rows from sources marked counted_pruned survive ONLY in these "
            "counters. rollup.json is not derivable from the logs — back it up."
        ),
    }

    # ── 5. aggregate-risk shadow log (unrotated — recomputed each run) ─
    shadow = _shadow_risk(root)

    payload: Dict[str, Any] = {"schema": SCHEMA,
                              "built_at_utc": _now().isoformat()}
    payload.update(roll.core())
    payload["shadow_risk"] = shadow
    payload["_checkpoint"] = checkpoint
    payload["_archived"] = archived_blob

    lt = payload["lifetime"]
    summary = {
        "ok": True,
        "rows_total": lt["rows"],
        "archive_rows_added_this_run": archives_added,
        "active_log_rows_this_run": tail_rows,
        "clv_rows": clv_rows,
        "archives_complete": checkpoint["archives_complete"],
        "pruned_sources_counted": pruned,
        "carried_placed_rows_skipped":
            payload["coverage"]["carried_placed_rows_skipped"],
        "placed": lt["placed"],
        "settled": lt["settled"],
        "skipped": lt["skipped"],
        "realized_pnl": lt["realized_pnl"],
        "days": len(payload["daily"]),
        "pods": list(payload["by_pod"]),
        "shadow_risk_rows": (shadow.get("lifetime") or {}).get("total", 0),
    }
    return payload, summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the dashboard's lifetime rollup.")
    ap.add_argument("--root", default=".", help="project root")
    ap.add_argument("--out", default=None,
                    help="output path (default: <root>/" + DEFAULT_OUT + ")")
    ap.add_argument("--full", action="store_true",
                    help="rebuild from scratch — LOSES pruned-archive history")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute but do not write")
    ap.add_argument("--backup", action="store_true",
                    help="also write rollup.json.bak.gz")
    ap.add_argument("--json", action="store_true",
                    help="print a machine-readable summary")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out) if args.out else (root / DEFAULT_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)

    payload, summary = build(root, out, full=args.full)
    body = json.dumps(payload, separators=(",", ":"), sort_keys=False)
    summary.update({"out": str(out), "written": not args.dry_run,
                    "bytes": len(body)})

    if not args.dry_run:
        _atomic_write(out, body)
        if args.backup:
            try:
                bak = out.with_name(out.name + ".bak.gz")
                with gzip.open(bak, "wt", encoding="utf-8") as fh:
                    fh.write(body)
            except OSError as exc:
                logger.warning("backup failed: %s", exc)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        lt = payload["lifetime"]
        print("rollup {} ({:,} bytes)".format(
            "computed" if args.dry_run else "written", len(body)))
        print("  rows total    : {:,}".format(summary["rows_total"]))
        print("  this run      : {:,} archive + {:,} live rows".format(
            summary["archive_rows_added_this_run"],
            summary["active_log_rows_this_run"]))
        print("  placed/settled: {:,} / {:,}".format(lt["placed"], lt["settled"]))
        print("  skipped       : {:,}".format(lt["skipped"]))
        print("  realized pnl  : {:+.2f} (PAPER)".format(lt["realized_pnl"]))
        print("  days covered  : {}".format(summary["days"]))
        print("  pods          : {}".format(", ".join(summary["pods"]) or "none"))
        if summary["carried_placed_rows_skipped"]:
            print("  rotation guard: {:,} carried PLACED rows skipped".format(
                summary["carried_placed_rows_skipped"]))
        if summary["pruned_sources_counted"]:
            print("  pruned archives counted: {} — BACK UP THIS FILE".format(
                summary["pruned_sources_counted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
