#!/usr/bin/env python3
"""Fund manager collector — gathers facts, judges nothing.

Runs on the droplet under cron. Reads the registry, measures the real system,
and writes a status snapshot. No LLM, no network calls to anything but the
local box, and no writes anywhere near the trading system's data.

Design rules this file follows:

1. NEVER crash the whole run because one probe failed. Every collector is
   wrapped; a failure becomes an "unknown" fact with the error attached. A
   monitor that dies at 3am tells you nothing, which is strictly worse than a
   monitor that says "I couldn't read the maker log."

2. NEVER read a whole log file. trade_log.jsonl is 16MB and the unmatched
   files are 39MB. Everything uses bounded reverse reads.

3. Python 3.9 compatible (droplet runs 3.12, but the Mac runs 3.9 and this
   must be testable locally — the project already has one test file that
   fails on the Mac for exactly this reason).

Usage:
    python3 manager/collect.py                 # collect, write state/status.json
    python3 manager/collect.py --print         # collect and dump to stdout
    python3 manager/collect.py --root /opt/betting-pod-shop
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# This file is invoked as a SCRIPT (`python manager/collect.py`), so sys.path[0]
# is `manager/`, not the repo root, and `import manager.x` raises
# ModuleNotFoundError. That is not hypothetical: the throughput probe shipped
# 2026-07-27 importing `manager.throughput`, failed on every collector run, and
# was swallowed by @safe into a fault nobody was reading — the instrument built
# to make silent failure visible was itself silently failing. Put the repo root
# on the path before any first-party import.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML required: pip install pyyaml\n")
    raise

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"
UTC = timezone.utc

# The droplet is not a git repo (deploy excludes .git), so the collector reads
# development history from a dedicated read-only clone of the public repo. See
# manager/README.md § git mirror. Overridable via MANAGER_GIT_REPO.
MIRROR_PATH = Path("/opt/betting-agent-mirror")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def jsonable(obj: Any) -> str:
    """Fallback encoder — YAML turns bare `2026-11-01` into a date object."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def parse_ts(value: Any) -> Optional[datetime]:
    """Best-effort timestamp parse across the formats used in these logs."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: anything past ~2001 in seconds, else milliseconds.
        try:
            return datetime.fromtimestamp(
                value / 1000.0 if value > 1e11 else value, UTC)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


# Ordered by preference. These are the ACTUAL keys used by this project's logs,
# verified against live data rather than assumed:
#   trade_log.jsonl   -> timestamp_utc
#   maker_fills.jsonl -> iso (human) and ts (epoch)
#   clv_log.jsonl     -> settled_at
_TS_KEYS = ("timestamp_utc", "iso", "timestamp", "ts", "time", "settled_at",
            "created_at", "captured_utc", "observed_at", "quote_ts", "fill_ts")

# Rows that represent a real bet, as opposed to a scan/skip/telemetry row.
# ~99% of trade_log rows are SKIPPED_EDGE noise, and DATA_COLLECTION is
# telemetry — counting either as activity would make a dead pod look busy.
_REAL_ACTIONS = {"PLACED", "PAPER_PLACED", "WIN", "WON", "LOSS", "LOST", "VOID"}


def row_ts(row: Dict[str, Any]) -> Optional[datetime]:
    for key in _TS_KEYS:
        if key in row:
            dt = parse_ts(row[key])
            if dt:
                return dt
    return None


def tail_lines(path: Path, count: int = 50, max_bytes: int = 512_000) -> List[str]:
    """Read the last `count` lines without loading the file.

    Bounded by max_bytes so a pathological single-line file can't blow memory.
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return []
        with path.open("rb") as fh:
            block = min(size, max_bytes)
            fh.seek(size - block)
            data = fh.read(block)
        text = data.decode("utf-8", errors="replace")
        if block < size:
            # First line is probably truncated mid-record; drop it.
            text = text.split("\n", 1)[-1]
        return [ln for ln in text.splitlines() if ln.strip()][-count:]
    except (OSError, ValueError):
        return []


def last_json_row(path: Path) -> Optional[Dict[str, Any]]:
    """Last parseable JSON object in a .jsonl file, scanning backwards."""
    for line in reversed(tail_lines(path, count=25)):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def count_lines_since(path: Path, since: datetime,
                      max_scan_bytes: int = 60_000_000) -> Tuple[int, bool]:
    """Count rows with a timestamp >= `since`, scanning backwards.

    Returns (count, complete). `complete` is False when the scan window was
    exhausted before reaching `since`, meaning the count is a lower bound —
    the caller must not present a truncated count as exact.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0, True
    if size == 0:
        return 0, True

    count = 0
    pos = size
    chunk = 4_000_000
    leftover = b""
    scanned = 0
    try:
        with path.open("rb") as fh:
            while pos > 0 and scanned < max_scan_bytes:
                step = min(chunk, pos)
                pos -= step
                scanned += step
                fh.seek(pos)
                data = fh.read(step) + leftover
                parts = data.split(b"\n")
                leftover = parts[0] if pos > 0 else b""
                for raw in reversed(parts[1:] if pos > 0 else parts):
                    if not raw.strip():
                        continue
                    try:
                        obj = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(obj, dict):
                        continue
                    dt = row_ts(obj)
                    if dt is None:
                        continue
                    if dt >= since:
                        count += 1
                    else:
                        # Logs are append-ordered; first older row ends the scan.
                        return count, True
    except OSError:
        return count, False
    return count, pos <= 0


def file_age(path: Path) -> Optional[float]:
    """Minutes since last modification, or None if absent."""
    try:
        return (now().timestamp() - path.stat().st_mtime) / 60.0
    except OSError:
        return None


def is_local_job(job: Dict[str, Any]) -> bool:
    """True for registry jobs whose output lives on the Mac, not the droplet.

    Two independent markers, either sufficient: `host: mac` (where it runs) and
    `output.root: local` (where its output is rooted). They agree today, but a
    job declared with only one of them must still be routed to the Mac.
    """
    if str(job.get("host", "")).lower() in ("mac", "local", "laptop"):
        return True
    return str((job.get("output") or {}).get("root", "")).lower() == "local"


# Hosts this project's two collectors can actually stat files on. Anything else
# named in a job's `host:` is a third box (P-029's dedicated VPS) whose paths
# exist on neither, so measuring it here would produce a confident "missing"
# for a job that may be running perfectly — the same false negative the Mac
# branch in job_record() exists to prevent.
MEASURABLE_HOSTS = ("droplet", "mac", "local", "laptop")


def job_host(job: Dict[str, Any]) -> str:
    return str(job.get("host", "droplet")).lower()


def safe(label: str):
    """Decorator: turn any collector exception into a recorded fault."""
    def wrap(fn):
        def inner(self, *a, **kw):
            try:
                return fn(self, *a, **kw)
            except Exception as exc:  # noqa: BLE001 - deliberate catch-all
                self.faults.append({
                    "probe": label,
                    "error": "{}: {}".format(type(exc).__name__, exc),
                    "trace": traceback.format_exc(limit=3),
                })
                return None
        return inner
    return wrap


# --------------------------------------------------------------------------
# collector
# --------------------------------------------------------------------------

class Collector:
    def __init__(self, registry_path: Path, root: Optional[Path] = None,
                 local_root: Optional[Path] = None):
        self.registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        meta = self.registry.get("meta", {})
        self.root = Path(root or meta.get("project_root", "/opt/betting-pod-shop"))
        self.local_root = Path(local_root or meta.get("local_root", HERE.parent))
        # On the Mac the "project root" IS the local root; don't chase /opt.
        if not self.root.exists() and self.local_root.exists():
            self.root = self.local_root
        self.faults: List[Dict[str, Any]] = []
        self.host = os.uname().nodename

    def resolve(self, spec: Any, root_kind: str = "project") -> Path:
        p = Path(str(spec))
        if p.is_absolute():
            return p
        base = self.local_root if root_kind == "local" else self.root
        return base / p

    # ---- services --------------------------------------------------------
    @safe("services")
    def services(self) -> List[Dict[str, Any]]:
        out = []
        for svc in self.registry.get("services", []):
            sid = svc["id"]
            rec: Dict[str, Any] = {
                "id": sid,
                "description": svc.get("description", ""),
                "severity": svc.get("severity", "warn"),
                "load": None,
                "active": None,
                "since": None,
                "restarts": None,
            }
            rec.update(self._systemd(sid))

            hb = svc.get("heartbeat") or {}
            if hb.get("file"):
                path = self.resolve(hb["file"])
                age = file_age(path)
                last = last_json_row(path) if path.exists() else None
                rec["heartbeat"] = {
                    "file": str(path),
                    "exists": path.exists(),
                    "age_minutes": round(age, 1) if age is not None else None,
                    "max_stale_minutes": hb.get("max_stale_minutes"),
                    "last_row_ts": iso(row_ts(last)) if last else None,
                    "only_during": hb.get("only_during"),
                }
                if hb.get("inspect_last_row"):
                    rec["heartbeat"]["last_row"] = last
                halt_cfg = hb.get("halt_signal")
                if halt_cfg:
                    rec["heartbeat"]["halt"] = self._halt_state(sid, halt_cfg)
            out.append(rec)
        return out

    def _halt_state(self, unit: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Is the unit currently in a deliberate trading halt?

        When the aggregate-risk guard trips the daily-loss limit it skips the
        whole scan cycle, so the heartbeat file (trade_log.jsonl) goes stale
        while the loop is perfectly alive. That state is invisible in the file
        but loud in the journal: a "guard halted" line every cycle. We read the
        recent journal for that line; its AGE is the signal — recent means the
        loop is spinning and halting on purpose, not wedged. Absent journal
        (e.g. the Mac) yields halted=None: unknown, never a false 'healthy'.
        """
        pattern = str(cfg.get("pattern", ""))
        limit = cfg.get("max_silent_journal_minutes")
        out: Dict[str, Any] = {
            "pattern": pattern,
            "max_silent_journal_minutes": limit,
            "halt_age_minutes": None,
            "halted": None,
            "reason": None,
        }
        if not pattern or not Path("/run/systemd/system").exists():
            return out
        try:
            res = subprocess.run(
                ["journalctl", "-u", unit, "--since", "30 min ago",
                 "--no-pager", "-o", "short-iso"],
                capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as exc:
            out["error"] = str(exc)
            return out
        newest_ts, newest_line = None, None
        for line in res.stdout.splitlines():
            if pattern in line:
                ts = parse_ts(line.split(" ", 1)[0])
                if ts:                       # journal is chronological; last wins
                    newest_ts, newest_line = ts, line
        if newest_ts is not None:
            age = round((now() - newest_ts).total_seconds() / 60.0, 1)
            out["halt_age_minutes"] = age
            out["reason"] = newest_line[-200:] if newest_line else None
            if limit is not None:
                out["halted"] = age <= limit
        else:
            out["halted"] = False           # journal read fine, no halt line
        return out

    def _systemd(self, unit: str) -> Dict[str, Any]:
        """Query systemd. Absent systemctl (i.e. the Mac) is not an error."""
        if not Path("/run/systemd/system").exists():
            return {"active": "n/a", "note": "not a systemd host"}
        try:
            res = subprocess.run(
                ["systemctl", "show", unit, "--no-page",
                 "--property=LoadState,ActiveState,SubState,NRestarts,"
                 "ExecMainStartTimestamp"],
                capture_output=True, text=True, timeout=15,
            )
            props = {}
            for line in res.stdout.splitlines():
                k, _, v = line.partition("=")
                props[k] = v
            started = parse_ts(props.get("ExecMainStartTimestamp", "").replace(" UTC", ""))
            uptime = None
            if started:
                uptime = round((now() - started).total_seconds() / 60.0, 1)
            return {
                # LoadState separates "the unit file is not on this host" from
                # "the service died". systemctl reports ActiveState=inactive for
                # BOTH, so without this a registry entry deployed ahead of its
                # unit install pages a CRITICAL that no restart can clear.
                "load": props.get("LoadState"),
                "active": props.get("ActiveState"),
                "sub": props.get("SubState"),
                "restarts": int(props.get("NRestarts") or 0),
                "since": props.get("ExecMainStartTimestamp") or None,
                "uptime_minutes": uptime,
            }
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            return {"active": "unknown", "error": str(exc)}

    # ---- scheduled jobs --------------------------------------------------
    @safe("jobs")
    def jobs(self) -> List[Dict[str, Any]]:
        return [self.job_record(job) for job in self.registry.get("jobs", [])]

    def job_record(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Measure one registry job on THIS machine.

        A Mac-hosted job measured on the droplet must never come back as
        `exists: False`. Its output path (/Users/samlawhon/...) cannot exist
        there, so a plain stat() reports "missing" with total confidence for a
        job that may be running perfectly. That false negative is the bug this
        branch exists to prevent: R-EV-MAP Build 2 has a 30-day evidence gate,
        and for as long as the droplet answered "does not exist" the brief was
        structurally incapable of seeing evidence accumulate. When we cannot
        look, we say so.
        """
        spec = job.get("output", {}) or {}
        root_kind = spec.get("root", "project")
        max_hours = spec.get("max_stale_hours")

        base_rec: Dict[str, Any] = {
            "id": job["id"],
            "host": job.get("host", "droplet"),
            "schedule": job.get("schedule"),
            "description": job.get("description", ""),
            "severity": job.get("severity", "info"),
            "max_stale_hours": max_hours,
            "note": job.get("note"),
        }

        if not self.can_check(job):
            if job_host(job) not in MEASURABLE_HOSTS:
                reason = ("job runs on '{}', a host neither collector can stat; "
                          "this collector is on '{}'. Measured by the "
                          "p029-daily-health-check scheduled task instead."
                          .format(base_rec["host"], self.host))
            else:
                reason = ("job runs on '{}' (paths under {}); this collector is "
                          "on '{}' where that root is not present"
                          .format(base_rec["host"], self.local_root, self.host))
            base_rec.update({
                "output": None,
                "exists": None,          # NOT False — we did not look
                "age_hours": None,
                "content_age_hours": None,
                "stale": None,
                "measurable": False,
                "state": "uncheckable",
                "uncheckable_reason": reason,
            })
            return base_rec

        age_min: Optional[float] = None
        content_age_min: Optional[float] = None
        exists = False
        target = None

        if spec.get("file"):
            target = self.resolve(spec["file"], root_kind)
            exists = target.exists()
            age_min = file_age(target)
            # mtime lies. clv_settlement's file was touched 31h ago but its
            # newest ROW is ~50h old: the cron ran, opened the file, and
            # appended nothing. For .jsonl outputs the last row timestamp is
            # the only honest measure of whether the job did any work.
            if exists and str(target).endswith(".jsonl"):
                last = last_json_row(target)
                dt = row_ts(last) if last else None
                if dt:
                    content_age_min = (now() - dt).total_seconds() / 60.0
                    age_min = max(age_min or 0.0, content_age_min)
        elif spec.get("glob"):
            base = self.resolve(spec["glob"], root_kind)
            matches = sorted(base.parent.glob(base.name),
                             key=lambda p: p.stat().st_mtime if p.exists() else 0)
            if matches:
                target, exists = matches[-1], True
                age_min = file_age(matches[-1])

        stale = (age_min is not None and max_hours is not None
                 and age_min > max_hours * 60)

        base_rec.update({
            "output": str(target) if target else None,
            "exists": exists,
            "age_hours": round(age_min / 60.0, 1) if age_min is not None else None,
            "content_age_hours": (round(content_age_min / 60.0, 1)
                                  if content_age_min is not None else None),
            "stale": stale,
            "measurable": exists or age_min is not None,
            "state": "measured",
            "checked_on": self.host,
        })
        return base_rec

    def can_check(self, job: Dict[str, Any]) -> bool:
        """Can this machine honestly measure this job's output?

        Local-rooted jobs are checkable only where local_root actually exists.
        On the Mac local_root IS the checkout, so they resolve; on the droplet
        it does not, so they do not.

        A job declared on a THIRD host is checkable from neither collector.
        Added 2026-07-29 for P-029, whose Phase 0 logger and archiver run on
        their own VPS: without this branch the droplet would stat
        /var/lib/p029/... , find nothing, and report a healthy collector as a
        missing output every 15 minutes.
        """
        host = job_host(job)
        if host not in MEASURABLE_HOSTS and host != self.host.lower():
            return False
        if not is_local_job(job):
            return True
        return self.local_root.exists()

    # ---- trade log / pod activity ---------------------------------------
    @safe("trade_log")
    def trade_activity(self) -> Dict[str, Any]:
        path = self.root / "data/trade_logs/trade_log.jsonl"
        if not path.exists():
            return {"available": False, "path": str(path)}

        cutoff = now() - timedelta(hours=24)
        per_pod: Dict[str, Dict[str, int]] = {}
        actions: Dict[str, int] = {}
        realized = 0.0
        scanned = 0
        complete = True

        try:
            size = path.stat().st_size
            pos, chunk, leftover = size, 4_000_000, b""
            with path.open("rb") as fh:
                while pos > 0 and scanned < 80_000_000:
                    step = min(chunk, pos)
                    pos -= step
                    scanned += step
                    fh.seek(pos)
                    data = fh.read(step) + leftover
                    parts = data.split(b"\n")
                    leftover = parts[0] if pos > 0 else b""
                    stop = False
                    for raw in reversed(parts[1:] if pos > 0 else parts):
                        if not raw.strip():
                            continue
                        try:
                            row = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if not isinstance(row, dict):
                            continue
                        dt = row_ts(row)
                        if dt is None:
                            continue
                        if dt < cutoff:
                            stop = True
                            break
                        action = str(row.get("action", "?")).upper()
                        actions[action] = actions.get(action, 0) + 1
                        if action not in _REAL_ACTIONS:
                            continue
                        pod = str(row.get("pod_id") or row.get("pod") or "unknown")
                        bucket = per_pod.setdefault(
                            pod, {"placed": 0, "settled": 0, "won": 0,
                                  "lost": 0, "void": 0})
                        if action in ("PLACED", "PAPER_PLACED"):
                            bucket["placed"] += 1
                        elif action in ("WIN", "WON"):
                            bucket["settled"] += 1
                            bucket["won"] += 1
                        elif action in ("LOSS", "LOST"):
                            bucket["settled"] += 1
                            bucket["lost"] += 1
                        elif action == "VOID":
                            bucket["void"] += 1
                        pnl = row.get("pnl_usd")
                        if pnl is None and isinstance(row.get("extra"), dict):
                            pnl = row["extra"].get("pnl_usd")
                        if isinstance(pnl, (int, float)):
                            realized += float(pnl)
                    if stop:
                        break
                else:
                    complete = pos <= 0
        except OSError as exc:
            return {"available": False, "error": str(exc)}

        return {
            "available": True,
            "window_hours": 24,
            "complete": complete,
            "actions": actions,
            "per_pod": per_pod,
            "realized_pnl_24h": round(realized, 2),
            "last_row_ts": iso(row_ts(last_json_row(path) or {})),
            "age_minutes": round(file_age(path) or 0, 1),
        }

    # ---- P-016 maker gate ------------------------------------------------
    @safe("maker_gate")
    def maker_gate(self) -> Dict[str, Any]:
        ws = self._workstream("P-016")
        gate = (ws or {}).get("gate", {})
        path = self.root / "data/trade_logs/maker_fills.jsonl"
        result: Dict[str, Any] = {
            "id": "P-016",
            "threshold": gate.get("threshold", 500),
            "available": path.exists(),
        }
        # A CLOSED gate is a resolved question, not a pending one. The fill
        # log outlives the verdict on purpose (nothing gets deleted), so
        # "the file exists" must not read as "the gate is still counting" —
        # that is exactly how a KILLed pod spent a week on the dashboard as
        # a 100% progress bar. Surface the closure; renderers branch on it.
        if str(gate.get("status") or "").upper() == "CLOSED":
            result["gate_status"] = "CLOSED"
            result["resolved_on"] = (
                str(gate.get("resolved_on")) if gate.get("resolved_on") else None)
            result["gate_verdict"] = gate.get("verdict")
        if not path.exists():
            return result

        # The contamination boundary is load-bearing. First-night data was
        # anchor-contaminated and produced 484 one-way fills in 45 minutes; if
        # the counter included it, the gate would read ~97% complete on garbage.
        exclude_before = parse_ts(gate.get("exclude_before")) or datetime(
            1970, 1, 1, tzinfo=UTC)
        result["excluding_before"] = iso(exclude_before)

        # Row schema (verified against live data):
        #   type: FILL | MARKOUT | SETTLE
        #   shadow: bool          -- shadow fills measure guardrail cost, they
        #                            are NOT real fills and must never count
        #                            toward the 500-fill gate
        #   markout_per_contract  -- paired with horizon_s (60 / 300 / 900)
        # The gate is specified on the +5m markout, so horizon_s == 300.
        fills = 0
        shadow_fills = 0
        markouts: List[float] = []
        by_day: Dict[str, List[float]] = {}
        sides: Dict[str, int] = {}
        settles = 0

        for line in tail_lines(path, count=200000, max_bytes=40_000_000):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            dt = row_ts(row)
            if dt is None or dt < exclude_before:
                continue

            is_shadow = bool(row.get("shadow"))
            kind = str(row.get("type") or "").upper()

            if kind == "FILL":
                if is_shadow:
                    shadow_fills += 1
                else:
                    fills += 1
                    side = str(row.get("side", "?")).upper()
                    sides[side] = sides.get(side, 0) + 1
            elif kind == "SETTLE" and not is_shadow:
                settles += 1
            elif kind == "MARKOUT" and not is_shadow:
                if row.get("horizon_s") in (300, 300.0):
                    mk = row.get("markout_per_contract")
                    if isinstance(mk, (int, float)):
                        markouts.append(float(mk))
                        by_day.setdefault(dt.date().isoformat(), []).append(float(mk))

        result.update({
            "fills_clean": fills,
            "shadow_fills": shadow_fills,
            "settles": settles,
            "progress_pct": round(100.0 * fills / max(1, result["threshold"]), 1),
            "markout_n": len(markouts),
            "markout_horizon_s": 300,
            "markout_mean": round(sum(markouts) / len(markouts), 4) if markouts else None,
            "sides": sides,
        })
        # One-sided fills are the anchor-contamination signature. Worth a number.
        if sides:
            top = max(sides.values())
            result["one_sided_ratio"] = round(top / max(1, sum(sides.values())), 3)

        # Gate robustness: does the result survive dropping the best day?
        if len(by_day) >= 2 and markouts:
            day_means = {d: sum(v) / len(v) for d, v in by_day.items()}
            best = max(day_means, key=lambda d: day_means[d])
            rest = [m for d, vals in by_day.items() if d != best for m in vals]
            result["best_day"] = best
            result["markout_mean_ex_best_day"] = (
                round(sum(rest) / len(rest), 4) if rest else None)

        result["gate_met"] = bool(
            fills >= result["threshold"]
            and (result.get("markout_mean") or 0) > 0
            and (result.get("markout_mean_ex_best_day") or 0) > 0
        )
        return result

    # ---- P-015 gate (delegates to the sanctioned reader) -----------------
    @safe("p015_gate")
    def p015_gate(self) -> Dict[str, Any]:
        """P-015 results come ONLY from scripts/p015_checkpoint.py.

        The decision rule names that script as the single sanctioned reader.
        Re-deriving n/edge/z here would create a second, subtly different
        number for a gate whose entire purpose is to be unambiguous — so this
        shells out and reports what the checkpoint says, or reports that it
        couldn't run. It does not compute a fallback.
        """
        script = self.root / "scripts/p015_checkpoint.py"
        out: Dict[str, Any] = {"id": "P-015", "reader": str(script)}
        if not script.exists():
            out["available"] = False
            return out

        py = self.root / "venv/bin/python"
        exe = str(py) if py.exists() else sys.executable
        try:
            res = subprocess.run([exe, str(script), "--json"],
                                 capture_output=True, text=True, timeout=120,
                                 cwd=str(self.root))
            raw = (res.stdout or "").strip()
            if raw:
                try:
                    out.update({"available": True, "checkpoint": json.loads(raw)})
                    return out
                except json.JSONDecodeError:
                    # Script may not support --json; keep the human text.
                    out.update({"available": True, "raw": raw[-2000:]})
                    return out
            out.update({"available": False,
                        "error": (res.stderr or "no output")[-500:]})
        except (subprocess.SubprocessError, OSError) as exc:
            out.update({"available": False, "error": str(exc)})
        return out

    def _checkpoint(self, script_rel: str, pod_id: str) -> Dict[str, Any]:
        """Shell out to a pod's sanctioned checkpoint reader and report it.

        Same contract as ``p015_gate``: the decision rule names one script as the
        single reader, so re-deriving n/edge/z here would create a second,
        subtly-different number for a gate whose whole purpose is to be
        unambiguous.  Reports what the script says, or that it could not run.
        It never computes a fallback.
        """
        script = self.root / script_rel
        out: Dict[str, Any] = {"id": pod_id, "reader": str(script)}
        if not script.exists():
            out["available"] = False
            return out
        py = self.root / "venv/bin/python"
        exe = str(py) if py.exists() else sys.executable
        try:
            res = subprocess.run([exe, str(script), "--json"],
                                 capture_output=True, text=True, timeout=120,
                                 cwd=str(self.root))
            raw = (res.stdout or "").strip()
            if raw:
                try:
                    out.update({"available": True, "checkpoint": json.loads(raw)})
                    return out
                except json.JSONDecodeError:
                    out.update({"available": True, "raw": raw[-2000:]})
                    return out
            out.update({"available": False,
                        "error": (res.stderr or "no output")[-500:]})
        except (subprocess.SubprocessError, OSError) as exc:
            out.update({"available": False, "error": str(exc)})
        return out

    @safe("p017_gate")
    def p017_gate(self) -> Dict[str, Any]:
        """P-017 progress is DERIVED, never hand-maintained.

        Until 2026-07-26 ``registry.yaml`` carried ``progress: 1`` set by hand on
        the day P-017 entered its first tournament, while 16 of that event's 38
        positions were still open.  A gate that counts entries rather than
        settlements can be satisfied without observing the thing it measures.
        """
        return self._checkpoint("scripts/p017_checkpoint.py", "P-017")

    @safe("p022_gate")
    def p022_gate(self) -> Dict[str, Any]:
        """P-022 progress against its pre-registered T=14 rule.

        T counts settled TOURNAMENTS from 2026-07-26 22:36 UTC, when the
        reconciled runner restarted. Before that the service had been up since
        2026-07-23 but could never quote — `_close_epoch` preferred a
        far-future placeholder field — so T was genuinely 0 and nothing was lost.
        """
        return self._checkpoint("scripts/p022_checkpoint.py", "P-022")

    @safe("p022_window")
    def p022_window(self) -> Dict[str, Any]:
        """P-022's quotable-window detector, surfaced to the alert path.

        `scripts/p022_window_check.py` runs */30 and appends one row per run,
        but nothing read it: its only consumer was a human opening
        status.jsonl. The one state that matters —
        WINDOW_OPEN_CANDIDATE_NO_QUOTE — is the pod being structurally unable
        to quote while everything else looks healthy, and it has to page.

        A missing or stale file is reported, never defaulted: the checker not
        running looks exactly like the checker saying "fine".
        """
        path = self.root / "data/p022_window_check/status.jsonl"
        if not path.exists():
            return {"available": False,
                    "error": "no status.jsonl — the */30 checker has never run"}
        row = last_json_row(path)
        if not row:
            return {"available": False,
                    "error": "status.jsonl has no parseable row"}
        ts = parse_ts(row.get("iso"))
        age_min = ((now() - ts).total_seconds() / 60.0) if ts else None
        return {
            "available": True,
            "state": row.get("state"),
            "alarm": bool(row.get("alarm")),
            "iso": row.get("iso"),
            "age_min": round(age_min, 1) if age_min is not None else None,
            "detail": row.get("detail"),
            "funnel": row.get("funnel") or {},
            "n_in_window_events": row.get("n_in_window_events"),
            "n_candidates": row.get("n_candidates"),
            "candidates_without_quote": (row.get("candidates_without_quote")
                                         or [])[:10],
            "events": [
                {k: e.get(k) for k in
                 ("event", "close_ref_iso", "close_source",
                  "hours_to_close_ref", "window_open_iso")}
                for e in (row.get("events") or [])[:8]
            ],
        }

    @safe("evmap_jobs")
    def evmap_jobs(self) -> Dict[str, Any]:
        """EV-Map collector outcomes, per job, from scripts/evmap_job.py.

        These ran 139 times on the Mac and failed 139 times without anyone
        noticing, because a failing collector and an idle collector produced
        the same observable: nothing. Six days of point-in-time weather quotes
        are permanently gone. This probe exists so that cannot recur silently.

        Reports the last outcome, its age, consecutive failures, and the rows
        the run actually WROTE — an exit-0 collector that writes nothing is
        the same outcome as one that crashes.
        """
        path = self.root / "kalshi-ev-map/data/job_status.jsonl"
        if not path.exists():
            return {"available": False,
                    "error": "no kalshi-ev-map/data/job_status.jsonl — no "
                             "EV-Map job has run on this host"}
        jobs: Dict[str, Any] = {}
        rows_today: Dict[str, int] = {}
        today = now().date().isoformat()
        for line in tail_lines(path, count=400):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            job = rec.get("job")
            if not job:
                continue
            if (rec.get("iso") or "")[:10] == today:
                rows_today[job] = rows_today.get(job, 0) + int(
                    rec.get("rows_added") or 0)
            jobs[job] = rec                       # last write wins
        out: Dict[str, Any] = {"available": True, "jobs": {}}
        for job, rec in sorted(jobs.items()):
            ts = parse_ts(rec.get("iso"))
            out["jobs"][job] = {
                "ok": bool(rec.get("ok")),
                "iso": rec.get("iso"),
                "age_min": (round((now() - ts).total_seconds() / 60.0, 1)
                            if ts else None),
                "exit_code": rec.get("exit_code"),
                "rows_added": rec.get("rows_added"),
                "rows_after": rec.get("rows_after"),
                "rows_today": rows_today.get(job, 0),
                "consecutive_failures": rec.get("consecutive_failures") or 0,
                "empty_reason": rec.get("empty_reason"),
                "stderr_tail": (rec.get("stderr_tail") or [])[-2:],
                "duration_s": rec.get("duration_s"),
            }
        return out

    @safe("p001_gate")
    def p001_gate(self) -> Dict[str, Any]:
        """P-001 progress under the re-scoped scenario-D gate.

        The old row count reached 650 of a 200 threshold on a population where
        86% of bets were priced off a different day's game.  This counts only
        admissible rows placed after the matcher fix went live.
        """
        return self._checkpoint("scripts/p001_checkpoint.py", "P-001")

    # ---- invariants ------------------------------------------------------
    @safe("invariants")
    def invariants(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        kill = self.root / "data/KILL_MAKER"
        res["kill_switch_present"] = kill.exists()

        # Assert every pod is still paper. This is the one thing that must
        # never silently change.
        cfg = self.root / "config_multi_pod.yaml"
        modes: Dict[str, str] = {}
        non_paper: List[str] = []
        if cfg.exists():
            try:
                data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
                pods = data.get("pods", {}) or {}
                for pid, body in pods.items():
                    if not isinstance(body, dict):
                        continue
                    mode = str(body.get("mode", data.get("mode", "paper"))).lower()
                    modes[pid] = mode
                    if mode not in ("paper", "demo"):
                        non_paper.append(pid)
                res["active_pods"] = pods.get("active") if isinstance(
                    pods.get("active"), list) else None
            except yaml.YAMLError as exc:
                res["config_error"] = str(exc)
        res["pod_modes"] = modes
        res["non_paper_pods"] = non_paper
        res["all_paper"] = not non_paper
        res["config_fingerprint"] = self.config_fingerprint(cfg)
        return res

    @staticmethod
    def config_fingerprint(cfg: Path) -> Dict[str, Any]:
        """Semantic fingerprint of the pod config, for drift detection.

        This exists because of a real, discovered divergence: on 2026-07-20 the
        Mac's config_multi_pod.yaml still held the pre-pivot pod set with
        P-013 enabled: true (the pod killed for a significant NEGATIVE edge,
        -$2,094) and no P-015/P-016 at all, while the droplet held the correct
        post-pivot set. Deploying the Mac copy would have silently resurrected
        a known-losing pod and dropped both pods currently under validation.

        The fingerprint is semantic, not a file hash — comments and key order
        must not produce false drift, or the check gets ignored.
        """
        out: Dict[str, Any] = {"exists": cfg.exists()}
        if not cfg.exists():
            return out
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            pods = data.get("pods", {}) or {}
            enabled = {k: bool(v.get("enabled"))
                       for k, v in pods.items()
                       if isinstance(v, dict) and "enabled" in v}
            active = pods.get("active") if isinstance(pods.get("active"), list) else []
            out.update({
                "active": sorted(str(a) for a in active),
                "enabled": dict(sorted(enabled.items())),
                "mtime": iso(datetime.fromtimestamp(cfg.stat().st_mtime, UTC)),
            })
        except (yaml.YAMLError, OSError) as exc:
            out["error"] = str(exc)
        return out

    # ---- log errors ------------------------------------------------------
    @safe("errors")
    def recent_errors(self) -> Dict[str, Any]:
        """Count journal errors, minus registry-declared benign noise."""
        suppress = self.registry.get("suppress", []) or []
        result: Dict[str, Any] = {"units": {}, "suppressed_total": 0}
        if not Path("/run/systemd/system").exists():
            result["note"] = "not a systemd host; journal unavailable"
            return result

        for unit in [s["id"] for s in self.registry.get("services", [])]:
            try:
                res = subprocess.run(
                    ["journalctl", "-u", unit, "--since", "24 hours ago",
                     "--no-pager", "-p", "err"],
                    capture_output=True, text=True, timeout=60)
                lines = [l for l in res.stdout.splitlines() if l.strip()]
            except (subprocess.SubprocessError, OSError) as exc:
                result["units"][unit] = {"error": str(exc)}
                continue

            kept, muted, samples = 0, 0, []
            for line in lines:
                if any(str(s.get("pattern", "")) in line
                       or (s.get("match") and str(s["match"]) in line)
                       for s in suppress):
                    muted += 1
                    continue
                kept += 1
                if len(samples) < 8:
                    samples.append(line[-300:])
            result["units"][unit] = {
                "errors_24h": kept,
                "suppressed_24h": muted,
                "samples": samples,
            }
            result["suppressed_total"] += muted
        return result

    # ---- workstream roll-up ---------------------------------------------
    def _workstream(self, wid: str) -> Optional[Dict[str, Any]]:
        for ws in self.registry.get("workstreams", []):
            if ws.get("id") == wid:
                return ws
        return None

    @safe("workstreams")
    def workstreams(self, trade: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        per_pod = (trade or {}).get("per_pod", {}) or {}
        for ws in self.registry.get("workstreams", []):
            rec = {
                "id": ws.get("id"),
                "name": ws.get("name"),
                "stage": ws.get("stage"),
                "blocked_on": ws.get("blocked_on", "nothing"),
                # YAML parses a bare 2026-09-10 into a date object; str() it so
                # the snapshot stays JSON-round-trippable either way.
                "recheck_after": (str(ws["recheck_after"])
                                  if ws.get("recheck_after") else None),
                "summary": (ws.get("summary") or "").strip(),
                "action_required": (ws.get("action_required") or "").strip() or None,
                "gate": ws.get("gate"),
                "open_questions": ws.get("open_questions", []),
                "activity_24h": per_pod.get(ws.get("id")),
            }
            # Freshness of the owning research dir tells us if it's being worked.
            owner = ws.get("owner_dir")
            if owner:
                path = self.resolve(owner, "local")
                rec["owner_dir"] = str(path)
                rec["owner_dir_age_hours"] = self._dir_age_hours(path)
            out.append(rec)
        return out

    def _dir_age_hours(self, path: Path) -> Optional[float]:
        """Hours since the most recent modification anywhere under `path`."""
        if not path.exists():
            return None
        newest = 0.0
        try:
            if path.is_file():
                newest = path.stat().st_mtime
            else:
                for dirpath, dirnames, filenames in os.walk(path):
                    dirnames[:] = [d for d in dirnames
                                   if d not in ("__pycache__", ".git", "data")]
                    for fn in filenames:
                        if fn.startswith("."):
                            continue
                        try:
                            newest = max(newest, (Path(dirpath) / fn).stat().st_mtime)
                        except OSError:
                            continue
        except OSError:
            return None
        if not newest:
            return None
        return round((now().timestamp() - newest) / 3600.0, 1)

    # ---- work completed (git history) -----------------------------------
    def _git_repo(self) -> Tuple[Optional[Path], bool]:
        """Resolve which git repo to read development history from.

        Returns ``(path, is_mirror)``. ``is_mirror`` is True for the droplet's
        dedicated read-only clone of the public repo — which we ``fetch`` before
        reading — and False for a live dev working tree (the Mac), which we must
        never mutate. The source of truth is the same commits either way; the
        droplet just can't see them directly because deploy excludes ``.git``.
        """
        env = os.environ.get("MANAGER_GIT_REPO")
        if env:
            p = Path(env)
            return (p, True) if (p / ".git").exists() else (None, False)
        # Mac dev path: the project root is itself a git checkout — read in place.
        for cand in (self.root, self.local_root):
            if (cand / ".git").exists():
                return cand, False
        # Droplet default: the manager's read-only mirror (see manager/README.md).
        if (MIRROR_PATH / ".git").exists():
            return MIRROR_PATH, True
        return None, False

    @safe("work_today")
    def work_today(self, window_hours: int = 24) -> Dict[str, Any]:
        """Summarise recent development work from git.

        What was actually done each day — research verdicts, code updates — lives
        in commit messages, not in the running system's logs. This reads them so
        the daily brief can report them. Follows the same rule as every other
        probe: a git failure becomes a recorded note, never a crash, and never a
        blank section masquerading as "no work".
        """
        repo, is_mirror = self._git_repo()
        result: Dict[str, Any] = {
            "available": False,
            "window_hours": window_hours,
            "repo": str(repo) if repo else None,
            "is_mirror": is_mirror,
        }
        if not repo:
            result["note"] = ("no git repo available — clone the mirror to "
                              "/opt/betting-agent-mirror or set MANAGER_GIT_REPO")
            return result

        def git(*a: str, timeout: int = 30) -> subprocess.CompletedProcess:
            return subprocess.run(["git", "-C", str(repo), *a],
                                  capture_output=True, text=True, timeout=timeout)

        # Refresh the mirror so the log reflects the latest pushed work. Only the
        # mirror is fetched — never the Mac's live working tree. Best-effort: a
        # fetch failure (offline, GitHub hiccup) still lets us log what's present.
        if is_mirror:
            try:
                fr = git("fetch", "--all", "--prune", "--quiet", timeout=60)
                result["fetched"] = fr.returncode == 0
                if fr.returncode != 0:
                    result["fetch_error"] = (fr.stderr or "").strip()[-200:]
            except (subprocess.SubprocessError, OSError) as exc:
                result["fetched"] = False
                result["fetch_error"] = "{}: {}".format(type(exc).__name__, exc)

        since = "{} hours ago".format(window_hours)
        sep = "\x1f"
        # --all spans every branch (main on GitHub lags; work lands on feature
        # branches); git dedupes a commit reachable from several refs by SHA.
        log = git("log", "--all", "--no-merges", "--since", since,
                  "--date=iso-strict",
                  "--pretty=format:%h{0}%cI{0}%s{0}%an".format(sep))
        commits: List[Dict[str, Any]] = []
        seen = set()
        for line in (log.stdout or "").splitlines():
            parts = line.split(sep)
            if len(parts) < 3 or parts[0] in seen:
                continue
            seen.add(parts[0])
            commits.append({
                "hash": parts[0], "iso": parts[1], "subject": parts[2],
                "author": parts[3] if len(parts) > 3 else None,
            })
        result["commits"] = commits
        result["commit_count"] = len(commits)

        # Research areas the committed work touched (authoritative on any host).
        names = git("log", "--all", "--no-merges", "--since", since,
                    "--name-only", "--pretty=format:")
        areas = set()
        for path_line in (names.stdout or "").splitlines():
            top = path_line.strip().split("/", 1)[0]
            if top.endswith("_research") or top == "research":
                areas.add(top)
        # Uncommitted research still in progress — only visible on the dev tree,
        # never on the mirror (a fresh clone has a clean status).
        uncommitted = 0
        if not is_mirror:
            st = git("status", "--porcelain")
            for row in (st.stdout or "").splitlines():
                top = row[3:].strip().split("/", 1)[0]
                if top.endswith("_research") or top == "research":
                    uncommitted += 1
                    areas.add(top)
        result["research_areas"] = sorted(areas)
        result["uncommitted_research_files"] = uncommitted
        result["available"] = True
        return result

    # ---- research operations --------------------------------------------
    @safe("research_operations")
    def research_operations(self) -> Dict[str, Any]:
        """Load the shared research funnel/queue contract for the daily brief.

        The collector copies measured facts only. In particular, a dispatch is
        not promoted into an "agent started" claim: metrics.json explicitly
        records that invocation tracking is unavailable.
        """
        path = self.root / "data" / "research_intake" / "metrics.json"
        if not path.exists():
            return {
                "available": False,
                "path": str(path),
                "reason": "research metrics file does not exist",
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "available": False,
                "path": str(path),
                "reason": "{}: {}".format(type(exc).__name__, exc),
            }
        operations = payload.get("operations")
        if not isinstance(operations, dict):
            return {
                "available": False,
                "path": str(path),
                "generated_at": payload.get("generated_at"),
                "reason": "research metrics have no operations block",
            }
        age_minutes = file_age(path)
        crossvenue = payload.get("crossvenue_pilot") or {}
        live_crossvenue_path = (
            self.root / "data" / "gemini_crossvenue" / "metrics.json")
        if live_crossvenue_path.exists():
            try:
                live_crossvenue = json.loads(
                    live_crossvenue_path.read_text(encoding="utf-8"))
                if isinstance(live_crossvenue, dict) and live_crossvenue:
                    crossvenue = live_crossvenue
            except (OSError, json.JSONDecodeError):
                # The aggregate remains usable and truthfully falls back to its
                # last embedded snapshot while the live source is being replaced.
                pass
        return {
            "available": True,
            "path": str(path),
            "generated_at": payload.get("generated_at"),
            "age_hours": (
                round(age_minutes / 60.0, 2) if age_minutes is not None else None),
            "funnel": payload.get("funnel") or {},
            "dispatch": payload.get("dispatch") or {},
            "operations": operations,
            "collector_health": payload.get("collector_health") or {},
            "quality_control": payload.get("quality_control") or {},
            "decisions": payload.get("decisions") or {},
            "top_rejection_reasons": payload.get("top_rejection_reasons") or {},
            "x_pilot": payload.get("x_pilot") or {},
            "crossvenue_pilot": crossvenue,
        }

    # ---- P-014 gate (delegates to the sanctioned reader) -----------------
    @safe("p014_gate")
    def p014_gate(self) -> Dict[str, Any]:
        """P-014 progress comes ONLY from scripts/p014_checkpoint.py.

        Same contract as p015_gate: shell out, report what the checkpoint says
        or report that it could not run, never compute a fallback. Note that
        this reader deliberately returns NO DECISION at every n — P-014 has no
        pre-registered rule, and inventing one in the collector would be worse
        than inventing it in the reader.
        """
        script = self.root / "scripts/p014_checkpoint.py"
        out: Dict[str, Any] = {"id": "P-014", "reader": str(script)}
        if not script.exists():
            out["available"] = False
            return out
        py = self.root / "venv/bin/python"
        exe = str(py) if py.exists() else sys.executable
        try:
            res = subprocess.run([exe, str(script), "--json"],
                                 capture_output=True, text=True, timeout=120,
                                 cwd=str(self.root))
            raw = (res.stdout or "").strip()
            if raw:
                try:
                    out.update({"available": True, "checkpoint": json.loads(raw)})
                    return out
                except json.JSONDecodeError:
                    out.update({"available": True, "raw": raw[-2000:]})
                    return out
            out.update({"available": False,
                        "error": (res.stderr or "no output")[-500:]})
        except (subprocess.SubprocessError, OSError) as exc:
            out.update({"available": False, "error": str(exc)})
        return out

    # ---- observation throughput ------------------------------------------
    @safe("throughput")
    def throughput(self, snapshot_so_far: Dict[str, Any]) -> Dict[str, Any]:
        """Realised observations-per-week per gate, and the resulting projection.

        Takes the partially-built snapshot because progress must come from the
        sanctioned checkpoint readers already collected above — re-deriving it
        here would create a second, subtly different gate number, which is the
        exact bug that made P-017 report `1` for a tournament it had merely
        entered.
        """
        from manager.throughput import gate_throughput, summarize
        recs = gate_throughput(self.root, snapshot_so_far, self.registry)
        out = summarize(recs)
        out["available"] = True
        return out

    # ---- orchestration ---------------------------------------------------
    def run(self) -> Dict[str, Any]:
        trade = self.trade_activity() or {}
        snapshot = {
            "collected_at": iso(now()),
            "host": self.host,
            "root": str(self.root),
            "registry_version": self.registry.get("meta", {}).get("version"),
            "services": self.services() or [],
            "jobs": self.jobs() or [],
            "trade": trade,
            "maker": self.maker_gate() or {},
            "p015": self.p015_gate() or {},
            "p017": self.p017_gate() or {},
            "p001": self.p001_gate() or {},
            "p022": self.p022_gate() or {},
            "p022_window": self.p022_window() or {},
            "evmap": self.evmap_jobs() or {},
            "p014": self.p014_gate() or {},
            "invariants": self.invariants() or {},
            "errors": self.recent_errors() or {},
            "workstreams": self.workstreams(trade) or [],
            "work_today": self.work_today() or {},
            "research_operations": self.research_operations() or {},
            "faults": self.faults,
        }
        # Last, and fed the snapshot: throughput reads the gate readers'
        # answers rather than recomputing them.
        snapshot["throughput"] = self.throughput(snapshot) or {}
        return snapshot


def collect_local_jobs(registry_path: Path = HERE / "registry.yaml",
                       local_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Measure only the Mac-hosted jobs, on the machine calling this.

    refresh.py runs the main collector over SSH on the droplet, which cannot
    see /Users/samlawhon/... — so the local jobs come back "uncheckable" from
    there and are filled in here, on the Mac, where their output actually is.
    """
    col = Collector(registry_path, local_root=local_root)
    return [col.job_record(job) for job in col.registry.get("jobs", [])
            if is_local_job(job)]


def merge_local_jobs(snapshot: Dict[str, Any],
                     registry_path: Path = HERE / "registry.yaml",
                     local_root: Optional[Path] = None) -> Dict[str, Any]:
    """Overlay locally-measured job records onto a droplet-collected snapshot.

    Only records that were actually measured here replace the remote ones; if
    the Mac cannot see them either, the remote "uncheckable" entry stands. We
    never downgrade a real measurement into a guess.
    """
    local = {rec["id"]: rec for rec in collect_local_jobs(registry_path, local_root)
             if rec.get("state") == "measured"}
    if not local:
        return snapshot
    merged = []
    for job in snapshot.get("jobs", []):
        rec = local.pop(job.get("id"), None)
        merged.append(rec if rec else job)
    merged.extend(local.values())  # local-only jobs absent from the remote run
    snapshot["jobs"] = merged
    snapshot["local_jobs_merged_at"] = iso(now())
    return snapshot


def write_state(snapshot: Dict[str, Any], state_dir: Path = STATE_DIR) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    status = state_dir / "status.json"
    tmp = state_dir / ".status.json.tmp"
    tmp.write_text(json.dumps(snapshot, indent=2, sort_keys=False, default=jsonable), encoding="utf-8")
    tmp.replace(status)  # atomic; a reader never sees a half-written file

    # Append a compact row to history for trend detection.
    hist = state_dir / "history.jsonl"
    row = {
        "t": snapshot["collected_at"],
        "services": {s["id"]: s.get("active") for s in snapshot.get("services", [])},
        "maker_fills": snapshot.get("maker", {}).get("fills_clean"),
        "pnl_24h": snapshot.get("trade", {}).get("realized_pnl_24h"),
        "errors": {u: v.get("errors_24h")
                   for u, v in snapshot.get("errors", {}).get("units", {}).items()},
        "stale_jobs": [j["id"] for j in snapshot.get("jobs", []) if j.get("stale")],
        "faults": len(snapshot.get("faults", [])),
    }
    with hist.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=jsonable) + "\n")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect fund manager status facts")
    ap.add_argument("--registry", default=str(HERE / "registry.yaml"))
    ap.add_argument("--root", default=None, help="project root (default: from registry)")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--state-dir", default=str(STATE_DIR))
    args = ap.parse_args()

    col = Collector(Path(args.registry), Path(args.root) if args.root else None)
    snap = col.run()
    path = write_state(snap, Path(args.state_dir))

    if args.do_print:
        print(json.dumps(snap, indent=2, default=jsonable))
    else:
        svc = ", ".join("{}={}".format(s["id"], s.get("active"))
                        for s in snap.get("services", []))
        print("[collect] {} -> {} | {} | faults={}".format(
            snap["collected_at"], path, svc or "no services", len(snap["faults"])))
    # Faults are reported, not fatal — cron should not see a failure because
    # one probe couldn't read one file.
    return 0


if __name__ == "__main__":
    sys.exit(main())
