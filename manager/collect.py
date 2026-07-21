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

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML required: pip install pyyaml\n")
    raise

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"
UTC = timezone.utc


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
            out.append(rec)
        return out

    def _systemd(self, unit: str) -> Dict[str, Any]:
        """Query systemd. Absent systemctl (i.e. the Mac) is not an error."""
        if not Path("/run/systemd/system").exists():
            return {"active": "n/a", "note": "not a systemd host"}
        try:
            res = subprocess.run(
                ["systemctl", "show", unit, "--no-page",
                 "--property=ActiveState,SubState,NRestarts,ExecMainStartTimestamp"],
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
        out = []
        for job in self.registry.get("jobs", []):
            spec = job.get("output", {})
            root_kind = spec.get("root", "project")
            age_min: Optional[float] = None
            exists = False
            target = None

            content_age_min: Optional[float] = None
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

            max_hours = spec.get("max_stale_hours")
            stale = (age_min is not None and max_hours is not None
                     and age_min > max_hours * 60)

            out.append({
                "id": job["id"],
                "host": job.get("host", "droplet"),
                "schedule": job.get("schedule"),
                "description": job.get("description", ""),
                "severity": job.get("severity", "info"),
                "output": str(target) if target else None,
                "exists": exists,
                "age_hours": round(age_min / 60.0, 1) if age_min is not None else None,
                "content_age_hours": (round(content_age_min / 60.0, 1)
                                      if content_age_min is not None else None),
                "max_stale_hours": max_hours,
                "stale": stale,
                # A job whose host we're not running on can't be measured if its
                # output lives on the other machine — flag rather than fake it.
                "measurable": exists or age_min is not None,
                "note": job.get("note"),
            })
        return out

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
            "invariants": self.invariants() or {},
            "errors": self.recent_errors() or {},
            "workstreams": self.workstreams(trade) or [],
            "faults": self.faults,
        }
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
