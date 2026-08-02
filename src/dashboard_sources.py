"""
src/dashboard_sources.py
────────────────────────
The ONLY filesystem reader in the dashboard.

Every fact the dashboard renders enters through a loader in this module, and
every loader returns ``(payload, meta)`` where *meta* is a plain dict::

    {"available": bool,        # did we actually read it?
     "path":      str,         # where we looked (always populated)
     "mtime_utc": str | None,
     "age_seconds": float | None,
     "stale":     bool,        # older than its expected refresh interval
     "reason":    str | None}  # WHY it is unavailable or stale, in words

Why this module exists
──────────────────────
``docs/GATE_INSTRUMENTATION_STANDARD.md`` §3 says a reader must return ``None``
or an *explained* zero — never a stale value and never a fabricated default.
The old dashboard broke that rule in JavaScript: ``dashboard.html:879`` fell
back to ``bankroll = 10000`` whenever the risk snapshot was missing, so ROI,
Sharpe and drawdown all rendered against a number that did not exist.

Centralising I/O here is what makes the rule testable rather than aspirational.
Nothing else in the dashboard touches the disk, so "does every panel degrade to
an explicit unknown?" is a property of one ~250-line file.

Design rules
────────────
* **No exceptions escape.** A missing, empty, malformed, over-large or
  permission-denied file is a ``meta`` with ``available: False`` and a reason.
  A dashboard that 500s because one JSON file is truncated is worse than one
  that says which file is truncated.
* **Size-guarded.** Whole-file reads refuse anything over ``_MAX_JSON_BYTES``.
  The box is 2 GB with six other workloads and has already had one OOM crash
  loop (2026-06-28); an unbounded ``read()`` in a request handler is how that
  happens again.
* **Clock injected.** Every loader takes ``clock`` so staleness is testable
  without sleeping.
* **Reuses ``manager.collect``** for ``tail_lines`` / ``parse_ts``. Those are
  the functions the alerting path uses; re-implementing them here would let the
  dashboard and the pager disagree about what "fresh" means.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

UTC = timezone.utc

# ── Reused helpers ────────────────────────────────────────────────────
# manager/collect.py owns bounded tailing and timestamp parsing for this
# project's log formats.  Import it rather than copying: the dashboard must
# agree with the alerting path about what a timestamp means.
try:  # pragma: no cover - exercised implicitly by every loader
    from manager import collect as _collect  # type: ignore
    tail_lines = _collect.tail_lines
    parse_ts = _collect.parse_ts
except Exception:  # pragma: no cover - only when run outside the repo root
    _collect = None  # type: ignore

    def tail_lines(path: Path, count: int = 50,  # type: ignore[misc]
                   max_bytes: int = 512_000) -> List[str]:
        raise RuntimeError(
            "manager.collect unavailable — run from the project root")

    def parse_ts(value: Any) -> Optional[datetime]:  # type: ignore[misc]
        return None


Clock = Callable[[], datetime]

# ── Tunables ──────────────────────────────────────────────────────────

#: Refuse to load a JSON file larger than this.  ``manager/state/status.json``
#: is ~46 KB and ``rollup.json`` is bounded well under 1 MB by construction;
#: 32 MB is four orders of magnitude of headroom and still cannot OOM the box.
_MAX_JSON_BYTES = 32 * 1024 * 1024

#: Default state directory, relative to the project root.
DEFAULT_STATE_DIR = "data/dashboard"

#: Expected refresh intervals, in seconds.  A source older than this is
#: reported ``stale: True`` — it is still rendered, but labelled.
ENGINE_STATE_MAX_AGE_S = 900.0        # 3 x the 300s scan interval
MANAGER_STATUS_MAX_AGE_S = 2700.0     # 3 x the 15-minute collector cron
ROLLUP_MAX_AGE_S = 10800.0            # 3 x the hourly rollup timer
P022_WINDOW_MAX_AGE_S = 10800.0       # registry says max_stale_hours: 3
CLV_MAX_AGE_S = 172800.0             # registry says max_stale_hours: 48
RESEARCH_METRICS_MAX_AGE_S = 108000.0  # daily intake; allow 30 hours

#: Kill-switch sentinel files.  Existence — not content — means "killed".
KILL_SWITCH_NAMES = (
    "KILL_MAKER",
    "KILL_ROUND_LEADER_FADE",
    "KILL_GOLF_MAKER",
    "KILL_CAPTURE",
)

#: Relative paths, so a caller can name a source without importing constants.
PATHS = {
    "engine_state":   DEFAULT_STATE_DIR + "/engine_state.json",
    "open_positions": DEFAULT_STATE_DIR + "/open_positions.json",
    "rollup":         DEFAULT_STATE_DIR + "/rollup.json",
    "manager_status": "manager/state/status.json",
    "p022_window":    "data/p022_window_check/status.jsonl",
    "clv":            "data/trade_logs/clv_log.jsonl",
    "research_metrics": "data/research_intake/metrics.json",
}


def _now(clock: Optional[Clock] = None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


# ── Meta construction ─────────────────────────────────────────────────


def _meta(
    path: Path,
    *,
    available: bool,
    reason: Optional[str] = None,
    mtime: Optional[float] = None,
    max_age_s: Optional[float] = None,
    clock: Optional[Clock] = None,
    content_ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a source-meta dict.

    ``content_ts`` — when the payload carries its own "written at" stamp — wins
    over mtime for the age calculation.  This is the ``clv_settlement`` lesson
    recorded in ``manager/collect.py``: a file whose mtime was 31 h old held a
    newest row 50 h old, because the job rewrote the file without adding
    anything.  mtime answers "was it touched"; the content stamp answers "is
    the information current", and the second is the one that matters.
    """
    m: Dict[str, Any] = {
        "available":   bool(available),
        "path":        str(path),
        "mtime_utc":   None,
        "age_seconds": None,
        "stale":       False,
        "reason":      reason,
    }

    ts: Optional[datetime] = None
    if content_ts:
        ts = parse_ts(content_ts)
        if ts is not None:
            m["content_ts_utc"] = ts.isoformat()
    if mtime is not None:
        m["mtime_utc"] = datetime.fromtimestamp(mtime, UTC).isoformat()
        if ts is None:
            ts = datetime.fromtimestamp(mtime, UTC)

    if ts is not None:
        age = (_now(clock) - ts).total_seconds()
        m["age_seconds"] = round(age, 1)
        if age < -60.0:
            # A source stamped in the future means the writer's clock and the
            # reader's disagree. Left unflagged this reads as "very fresh",
            # which is the wrong direction to be wrong in: freshness is the
            # thing every other panel trusts.
            m["clock_skew"] = True
            m["stale"] = True
            m["reason"] = (
                "timestamp is {:.0f} min in the FUTURE — clock skew between "
                "the writer and this process; treat every age on this page as "
                "unreliable".format(abs(age) / 60.0))
        elif max_age_s is not None and age > max_age_s:
            m["stale"] = True
            if not m["reason"]:
                m["reason"] = (
                    "last updated {:.0f} min ago; expected every {:.0f} min"
                    .format(age / 60.0, max_age_s / 60.0)
                )

    if not available:
        m["stale"] = True
    return m


# ── Generic JSON loader ───────────────────────────────────────────────


def load_json_file(
    path: Path,
    *,
    max_age_s: Optional[float] = None,
    max_bytes: int = _MAX_JSON_BYTES,
    clock: Optional[Clock] = None,
    content_ts_key: Optional[str] = None,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Load one JSON file. Never raises.

    Returns ``(None, meta)`` with a spoken reason for: missing, empty,
    over-size, unreadable, malformed, or not-a-JSON-object.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return None, _meta(path, available=False,
                           reason="file does not exist", clock=clock)
    except OSError as exc:
        return None, _meta(path, available=False,
                           reason="cannot stat: {}".format(exc), clock=clock)

    if st.st_size == 0:
        return None, _meta(path, available=False, reason="file is empty",
                           mtime=st.st_mtime, clock=clock)

    if st.st_size > max_bytes:
        return None, _meta(
            path, available=False, mtime=st.st_mtime, clock=clock,
            reason="file is {:.1f} MB, over the {:.0f} MB read limit — "
                   "refusing to load it into memory".format(
                       st.st_size / 1e6, max_bytes / 1e6))

    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        return None, _meta(path, available=False, mtime=st.st_mtime,
                           clock=clock,
                           reason="unreadable or malformed JSON: {}".format(exc))

    content_ts = None
    if content_ts_key and isinstance(payload, dict):
        content_ts = payload.get(content_ts_key)
        if content_ts is None:
            meta_block = payload.get("_meta")
            if isinstance(meta_block, dict):
                content_ts = meta_block.get(content_ts_key)

    meta = _meta(path, available=True, mtime=st.st_mtime,
                 max_age_s=max_age_s, clock=clock, content_ts=content_ts)
    meta["bytes"] = st.st_size
    return payload, meta


# ── Per-source loaders ────────────────────────────────────────────────


def load_engine_state(
    root: Path,
    state_dir: Optional[Path] = None,
    clock: Optional[Clock] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The engine's per-cycle snapshot.

    This is ``DashboardState._serialize()``'s own output, written atomically by
    the engine each cycle — not a re-derivation.  Its ``_meta.interval_seconds``
    is used to scale the staleness threshold, so a slower scan interval does not
    read as a dead engine.
    """
    path = (state_dir or (root / DEFAULT_STATE_DIR)) / "engine_state.json"
    payload, meta = load_json_file(path, clock=clock,
                                  content_ts_key="written_at_utc")
    if payload is None:
        return None, meta
    if not isinstance(payload, dict):
        return None, _meta(path, available=False, clock=clock,
                           reason="expected a JSON object, got {}".format(
                               type(payload).__name__))

    interval = None
    block = payload.get("_meta")
    if isinstance(block, dict):
        try:
            interval = float(block.get("interval_seconds") or 0) or None
        except (TypeError, ValueError):
            interval = None

    max_age = (interval * 3.0) if interval else ENGINE_STATE_MAX_AGE_S
    age = meta.get("age_seconds")
    if age is not None and age > max_age:
        meta["stale"] = True
        meta["reason"] = (
            "engine last wrote {:.1f} min ago; it should write every "
            "{:.0f} s".format(age / 60.0, interval or 300.0))
    meta["expected_max_age_seconds"] = round(max_age, 1)
    return payload, meta


def load_open_positions(
    root: Path,
    state_dir: Optional[Path] = None,
    clock: Optional[Clock] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Currently-open placements, capped by the engine when it writes them."""
    path = (state_dir or (root / DEFAULT_STATE_DIR)) / "open_positions.json"
    payload, meta = load_json_file(path, max_age_s=ENGINE_STATE_MAX_AGE_S,
                                   clock=clock,
                                   content_ts_key="written_at_utc")
    if payload is None:
        return None, meta
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return None, _meta(path, available=False, clock=clock,
                           reason="expected {'rows': [...]}, got a different shape")
    meta["truncated"] = bool(payload.get("truncated"))
    meta["rows"] = len(payload["rows"])
    return payload, meta


def load_manager_status(
    root: Path,
    clock: Optional[Clock] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """``manager/state/status.json`` — gates, throughput, services, jobs.

    Written by ``manager/collect.py`` on a 15-minute cron.  This is the
    authoritative source for every gate number the dashboard shows; the
    dashboard must not recompute any of them (Gate Standard §1, §7).
    """
    path = root / "manager" / "state" / "status.json"
    payload, meta = load_json_file(path, max_age_s=MANAGER_STATUS_MAX_AGE_S,
                                   clock=clock, content_ts_key="collected_at")
    if payload is None:
        if meta.get("reason") == "file does not exist":
            meta["reason"] = (
                "manager/state/status.json missing — is the collector cron "
                "running? Every gate and service number comes from this file.")
        return None, meta
    if not isinstance(payload, dict):
        return None, _meta(path, available=False, clock=clock,
                           reason="expected a JSON object")
    meta["collected_at"] = payload.get("collected_at")
    meta["host"] = payload.get("host")
    return payload, meta


def load_rollup(
    root: Path,
    state_dir: Optional[Path] = None,
    clock: Optional[Clock] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Lifetime aggregates from ``scripts/build_dashboard_rollup.py``.

    Cannot be computed on demand: ``scripts/rotate_active_log.py`` rewrites the
    active log to hold only still-open PLACED rows and keeps 12 gzipped
    archives, so lifetime history exists only in this file plus the surviving
    archives.
    """
    path = (state_dir or (root / DEFAULT_STATE_DIR)) / "rollup.json"
    payload, meta = load_json_file(path, max_age_s=ROLLUP_MAX_AGE_S,
                                   clock=clock, content_ts_key="built_at_utc")
    if payload is None:
        if meta.get("reason") == "file does not exist":
            meta["reason"] = (
                "no rollup yet — run: python3 -m scripts.build_dashboard_rollup "
                "--full")
        return None, meta
    if not isinstance(payload, dict):
        return None, _meta(path, available=False, clock=clock,
                           reason="expected a JSON object")
    cp = payload.get("_checkpoint")
    if isinstance(cp, dict):
        meta["rows_total"] = cp.get("rows_total")
        meta["pruned_sources_counted"] = cp.get("pruned_sources_counted")
    return payload, meta


def load_research_metrics(
    root: Path,
    state_dir: Optional[Path] = None,
    clock: Optional[Clock] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Research assignment, dispatch, review, advancement, and X-cost funnel."""
    del state_dir  # signature matches the other JSON loaders for callers/tests
    path = root / "data" / "research_intake" / "metrics.json"
    payload, meta = load_json_file(
        path, max_age_s=RESEARCH_METRICS_MAX_AGE_S, clock=clock,
        content_ts_key="generated_at")
    if payload is None:
        if meta.get("reason") == "file does not exist":
            meta["reason"] = (
                "no research metrics — run scripts.run_research_triage then "
                "scripts.build_research_metrics")
        return None, meta
    if not isinstance(payload, dict):
        return None, _meta(path, available=False, clock=clock,
                           reason="expected a JSON object")
    return payload, meta


def tail_p022_window(
    root: Path,
    clock: Optional[Clock] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The newest row of ``data/p022_window_check/status.jsonl``.

    Read live rather than via the collector so the funnel is current between
    15-minute collections.  This is the only panel that answers "the window was
    open and nothing was quoted — at which stage did it stop?", which is the
    failure class the static gate checker explicitly cannot catch.
    """
    path = root / "data" / "p022_window_check" / "status.jsonl"
    try:
        st = path.stat()
    except FileNotFoundError:
        return None, _meta(
            path, available=False, clock=clock,
            reason="no status.jsonl — scripts/p022_window_check.py has not run")
    except OSError as exc:
        return None, _meta(path, available=False,
                           reason="cannot stat: {}".format(exc), clock=clock)

    rows = tail_lines(path, count=25, max_bytes=256_000)
    for raw in reversed(rows):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            meta = _meta(path, available=True, mtime=st.st_mtime,
                         max_age_s=P022_WINDOW_MAX_AGE_S, clock=clock,
                         content_ts=obj.get("iso") or obj.get("ts"))
            return obj, meta

    return None, _meta(path, available=False, mtime=st.st_mtime, clock=clock,
                       reason="no parseable JSON row in the last 25 lines")


def clv_freshness(
    root: Path,
    clock: Optional[Clock] = None,
) -> Tuple[None, Dict[str, Any]]:
    """Freshness only for ``clv_log.jsonl``; the numbers come from the rollup.

    Kept separate because a CLV *number* with no indication of whether the
    capture job is still running is the "dashboard tile as verdict" the Gate
    Standard forbids.
    """
    path = root / "data" / "trade_logs" / "clv_log.jsonl"
    try:
        st = path.stat()
    except OSError:
        return None, _meta(
            path, available=False, clock=clock,
            reason="no clv_log.jsonl — scripts/clv_settlement.py has not run")

    last = None
    for raw in reversed(tail_lines(path, count=25, max_bytes=256_000)):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            last = obj
            break

    meta = _meta(path, available=True, mtime=st.st_mtime,
                 max_age_s=CLV_MAX_AGE_S, clock=clock,
                 content_ts=(last or {}).get("settled_at"))
    return None, meta


def kill_switches(root: Path) -> List[Dict[str, Any]]:
    """Sentinel files whose mere existence disables a strategy.

    Cheap (``stat`` only) and high-value: ``data/KILL_MAKER`` has been present
    and intentional since 2026-07-22, and a dashboard that does not say so
    invites the question "why is P-016 quiet?" every single day.
    """
    out: List[Dict[str, Any]] = []
    for name in KILL_SWITCH_NAMES:
        p = root / "data" / name
        try:
            st = p.stat()
        except OSError:
            out.append({"name": name, "present": False, "mtime_utc": None})
            continue
        out.append({
            "name": name,
            "present": True,
            "mtime_utc": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
        })
    return out


# ── Aggregate ─────────────────────────────────────────────────────────


def load_all(
    root: Path,
    state_dir: Optional[Path] = None,
    clock: Optional[Clock] = None,
) -> Dict[str, Any]:
    """Read every source once. Never raises.

    Returns ``{"root", "state_dir", "kill_switches", "<name>": payload,
    "sources": {"<name>": meta}}`` — the single input to
    ``src.dashboard_api.build_v2``, which is a pure function of this dict.
    """
    root = Path(root)
    sd = Path(state_dir) if state_dir else (root / DEFAULT_STATE_DIR)

    engine_state, m_engine = load_engine_state(root, sd, clock)
    open_positions, m_open = load_open_positions(root, sd, clock)
    manager_status, m_mgr = load_manager_status(root, clock)
    rollup, m_rollup = load_rollup(root, sd, clock)
    research_metrics, m_research = load_research_metrics(root, sd, clock)
    p022_window, m_p022 = tail_p022_window(root, clock)
    _, m_clv = clv_freshness(root, clock)

    return {
        "root":           str(root),
        "state_dir":      str(sd),
        "generated_at_utc": _now(clock).isoformat(),
        "engine_state":   engine_state,
        "open_positions": open_positions,
        "manager_status": manager_status,
        "rollup":         rollup,
        "research_metrics": research_metrics,
        "p022_window":    p022_window,
        "kill_switches":  kill_switches(root),
        "sources": {
            "engine_state":   m_engine,
            "open_positions": m_open,
            "manager_status": m_mgr,
            "rollup":         m_rollup,
            "research_metrics": m_research,
            "p022_window":    m_p022,
            "clv":            m_clv,
        },
    }


def source_fingerprint(root: Path, state_dir: Optional[Path] = None) -> str:
    """Cheap ETag input: ``(path, mtime_ns, size)`` over every source.

    Lets a phone poll every 15 s and get a 304 whenever nothing changed, which
    is most of the time — the collector runs every 15 min and the engine every
    5.
    """
    import hashlib

    root = Path(root)
    sd = Path(state_dir) if state_dir else (root / DEFAULT_STATE_DIR)
    h = hashlib.sha1()
    for rel in ("engine_state.json", "open_positions.json", "rollup.json"):
        p = sd / rel
        try:
            st = p.stat()
            h.update("{}:{}:{}".format(rel, st.st_mtime_ns, st.st_size).encode())
        except OSError:
            h.update("{}:absent".format(rel).encode())
    for p in (root / "manager" / "state" / "status.json",
              root / "data" / "p022_window_check" / "status.jsonl",
              root / "data" / "research_intake" / "metrics.json"):
        try:
            st = p.stat()
            h.update("{}:{}:{}".format(p.name, st.st_mtime_ns, st.st_size).encode())
        except OSError:
            h.update("{}:absent".format(p.name).encode())
    return h.hexdigest()[:16]
