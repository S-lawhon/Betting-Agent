"""
src/web_dashboard.py
────────────────────
Live web dashboard for the Betting Pod Shop.

Serves a single-page HTML dashboard on a local HTTP server.  The page
polls /api/status every few seconds and updates in-place without a
full page reload — no WebSocket, no external dependencies.

Architecture
────────────
  DashboardState        — thread-safe store for the latest engine data
  _DashboardHandler     — BaseHTTPRequestHandler (GET / and GET /api/status)
  WebDashboardServer    — manages the HTTPServer + background thread

Usage::

    from src.web_dashboard import WebDashboardServer

    server = WebDashboardServer(port=8080)
    server.start()           # launches background thread, opens browser

    # after each scan cycle:
    server.update(
        snapshot=guard.snapshot(),
        report=last_cycle_report,
        performances=allocator.performances(),
        allocations=allocator.allocations(),
        settlement_summary=bridge.summary(),
    )

    server.stop()            # graceful shutdown (or just exit — it's a daemon)
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import base64
import hmac
import html as html_mod
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Valid ``?section=`` values on ``/api/v2/dashboard``. Mirrors
#: ``src.dashboard_api.SECTIONS``; kept as a literal here so importing this
#: module never depends on import order, and asserted equal in the tests.
_V2_SECTIONS = ("now", "gates", "pnl", "pipeline", "ops", "work")


# ── Dashboard state ───────────────────────────────────────────────────


class DashboardState:
    """Thread-safe container for the latest engine snapshot.

    ``update()`` is called from the scan-loop thread; ``to_json()`` is
    called from the HTTP-server thread.  A ``threading.Lock`` protects
    all reads and writes.
    """

    #: Hard cap on the trade rows held in memory and echoed into
    #: ``/api/status``.  Before this cap the list was unbounded, serialised
    #: verbatim into every response *and* iterated three times per request —
    #: inside the engine process, which has no ``MemoryMax`` and had already
    #: caused a five-week OOM crash loop in June 2026.  ``0`` disables the cap.
    DEFAULT_MAX_TRADES = 400

    def __init__(self, max_trades: int = DEFAULT_MAX_TRADES) -> None:
        self._lock          = threading.Lock()
        self._snapshot: Optional[Any] = None
        self._report: Optional[Any]   = None
        self._performances: Dict[str, Any] = {}
        self._allocations:  Dict[str, Any] = {}
        self._settlement: Optional[Dict[str, Any]] = None
        self._trades: list = []
        self._trades_truncated = False
        self._max_trades = int(max_trades) if max_trades and max_trades > 0 else 0
        self._engine_status = "starting"

    # ── Write side ───────────────────────────────────────────────────

    def update(
        self,
        snapshot: Optional[Any]           = None,
        report:   Optional[Any]           = None,
        performances: Optional[Dict]      = None,
        allocations:  Optional[Dict]      = None,
        settlement_summary: Optional[Dict]= None,
        trades: Optional[list]            = None,
    ) -> None:
        """Replace the stored state with fresh engine data (thread-safe)."""
        with self._lock:
            if snapshot is not None:
                self._snapshot = snapshot
                self._engine_status = "halted" if snapshot.halted else "running"
            if report is not None:
                self._report = report
            if performances is not None:
                self._performances = performances
            if allocations is not None:
                self._allocations = allocations
            if settlement_summary is not None:
                self._settlement = settlement_summary
            if trades is not None:
                # Newest-first from get_recent_placed_with_status(), so the cap
                # keeps the rows anyone actually looks at.
                if self._max_trades and len(trades) > self._max_trades:
                    self._trades = list(trades[:self._max_trades])
                    self._trades_truncated = True
                else:
                    self._trades = trades
                    self._trades_truncated = False

    # ── Read side ─────────────────────────────────────────────────────

    def to_json(self) -> str:
        """Serialise the current state to a JSON string (thread-safe)."""
        with self._lock:
            return json.dumps(self._serialize(), default=str)

    def _serialize(self) -> Dict[str, Any]:
        """Build the dict that becomes the /api/status JSON payload."""
        result: Dict[str, Any] = {"engine_status": self._engine_status}

        if self._snapshot is not None:
            s = self._snapshot
            # Count only LIVE open positions (paper-mode trades don't
            # represent real capital at risk).  The guard already excludes
            # paper trades on bootstrap, so the dashboard should match.
            open_from_log = sum(
                1 for t in self._trades
                if t.get("status") == "OPEN" and t.get("mode") != "paper"
            )
            exposure_from_log = sum(
                float(t.get("position_size_usd", 0) or 0)
                for t in self._trades
                if t.get("status") == "OPEN" and t.get("mode") != "paper"
            )
            total_exposure = max(s.total_exposure_usd, exposure_from_log)
            # Derive bankroll from snapshot to recompute the percentage
            if s.total_exposure_pct > 0 and s.total_exposure_usd > 0:
                bankroll = s.total_exposure_usd / s.total_exposure_pct
                exposure_pct = total_exposure / bankroll
            else:
                from src.constants import DEFAULT_BANKROLL
                bankroll = getattr(s, "_bankroll", DEFAULT_BANKROLL)
                exposure_pct = s.total_exposure_pct
            result["risk"] = {
                "bankroll":           round(bankroll, 2),
                "total_exposure_usd": round(total_exposure, 2),
                "total_exposure_pct": round(exposure_pct * 100, 2),
                "daily_pnl":          round(s.daily_pnl, 2),
                "daily_pnl_pct":      round(s.daily_pnl_pct * 100, 2),
                "open_positions":     max(s.open_positions, open_from_log),
                "halted":             s.halted,
                "halt_reason":        getattr(s, "halt_reason", None),
                "venue_exposure": {
                    k: round(v, 2)
                    for k, v in (getattr(s, "venue_exposure", None) or {}).items()
                },
            }

        if self._report is not None:
            r = self._report
            result["cycle"] = {
                "cycle_number":     r.cycle_number,
                "duration_seconds": round(r.duration_seconds, 3),
                "pods_scanned":     r.pods_scanned,
                "placed_count":     r.placed_count,
                "skipped_count":    r.skipped_count,
                "error_count":      r.error_count,
                "success_rate":     round(r.success_rate * 100, 1),
                "timestamp_utc":    getattr(r, "timestamp_utc", ""),
            }

        if self._performances:
            # Compute capital deployed per pod from actual OPEN trade positions
            pod_capital: Dict[str, float] = {}
            for trade in self._trades:
                if trade.get("status") == "OPEN":
                    pid = trade.get("pod_id", "P-001")
                    usd = float(trade.get("position_size_usd", 0) or 0)
                    pod_capital[pid] = pod_capital.get(pid, 0) + usd

            pods: Dict[str, Any] = {}
            for pod_id, perf in self._performances.items():
                alloc    = self._allocations.get(pod_id)
                resolved = perf.total_wins + perf.total_losses
                # Try several attribute names pods may expose for their display name
                pod_name = (
                    getattr(perf, "pod_name", None)
                    or getattr(perf, "name", None)
                    or pod_id
                )
                pods[pod_id] = {
                    "name":              pod_name,
                    "placed":            perf.total_placed,
                    "wins":              perf.total_wins,
                    "losses":            perf.total_losses,
                    "win_pct":           round(perf.total_wins / resolved * 100, 1)
                                         if resolved else 0.0,
                    "pnl":               round(perf.total_pnl, 2),
                    "alloc_pct":         round(alloc.allocation_pct * 100, 1)
                                         if alloc else 0.0,
                    "max_position_usd":  int(alloc.max_position_usd)
                                         if alloc else 0,
                    "capital_usd":       round(pod_capital.get(pod_id, 0), 2),
                }
            result["pods"] = pods

        if self._settlement is not None:
            se = self._settlement
            result["settlement"] = {
                "total_pnl":     round(se.get("total_pnl",     0.0), 2),
                "total_settled": se.get("total_settled", 0),
                "wins":          se.get("wins",          0),
                "losses":        se.get("losses",        0),
                "voids":         se.get("voids",         0),
                "win_rate":      round(se.get("win_rate", 0.0) * 100, 1),
            }

        result["trades"] = self._trades

        # If trades reference a pod_id that isn't in performances (e.g. P-001
        # failed to load this run but placed trades in a prior run), inject a
        # minimal stub so the pod tab still appears with its trade history.
        if self._trades:
            pods_out = result.setdefault("pods", {})
            seen: set = set()
            for trade in self._trades:
                pid = trade.get("pod_id")
                if pid and pid not in pods_out and pid not in seen:
                    seen.add(pid)
                    pods_out[pid] = {
                        "name":             trade.get("pod_name", pid),
                        "placed":           0,
                        "wins":             0,
                        "losses":           0,
                        "win_pct":          0.0,
                        "pnl":              0.0,
                        "alloc_pct":        0.0,
                        "max_position_usd": 0,
                        "capital_usd":      0,
                    }

        return result

    # ── Snapshot writing (producer for the standalone dashboard) ──────

    def write_snapshot(self, state_dir: Path) -> Optional[Path]:
        """Persist ``_serialize()``'s output to ``engine_state.json``, atomically.

        The standalone dashboard reads this file, which is why the payload is
        built by calling ``_serialize()`` rather than by a second serializer:
        two serializers drift, and only one of them would be covered by the 56
        tests that pin the v1 contract.

        ``trades`` is dropped (it goes to ``open_positions.json``) and a
        ``_meta`` block is attached so a reader can tell how old the snapshot is
        *and* what cadence to expect — mtime alone cannot distinguish "the
        engine is slow" from "the engine is dead".

        Returns the path written, or ``None`` on failure. Never raises: a
        dashboard write must not be able to break a scan cycle.
        """
        try:
            state_dir = Path(state_dir)
            state_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = self._serialize()
                payload.pop("trades", None)
                cycle = payload.get("cycle") or {}
                payload["_meta"] = {
                    "written_at_utc": datetime.now(timezone.utc).isoformat(),
                    "cycle_number": cycle.get("cycle_number"),
                    "pid": os.getpid(),
                    "interval_seconds": self._interval_seconds,
                    "max_trades": self._max_trades,
                }
            return _atomic_json(state_dir / "engine_state.json", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not write engine_state.json: %s", exc)
            return None

    def write_open_positions(self, state_dir: Path) -> Optional[Path]:
        """Persist the (already capped) trade rows to ``open_positions.json``."""
        try:
            state_dir = Path(state_dir)
            state_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {
                    "written_at_utc": datetime.now(timezone.utc).isoformat(),
                    "capped_at": self._max_trades or None,
                    "truncated": self._trades_truncated,
                    "rows": list(self._trades),
                }
            return _atomic_json(state_dir / "open_positions.json", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not write open_positions.json: %s", exc)
            return None

    #: Scan interval, set by the engine so readers can scale staleness.
    _interval_seconds: Optional[float] = None

    #: Project root, used only so embedded mode can serve the same v2 UI.
    _root: Optional[Path] = None
    _state_dir: Optional[Path] = None

    def set_interval_seconds(self, seconds: Optional[float]) -> None:
        self._interval_seconds = float(seconds) if seconds else None

    def set_paths(self, root: Path, state_dir: Optional[Path] = None) -> None:
        self._root = Path(root)
        self._state_dir = Path(state_dir) if state_dir else None

    # ── v2 (embedded mode) ────────────────────────────────────────────

    def v2(self, sections: Optional[list] = None) -> Dict[str, Any]:
        """Same v2 payload as the standalone server.

        Embedded mode reads the same files rather than shortcutting to its own
        in-memory objects. That keeps one code path — and it means a laptop run
        against a fresh checkout honestly reports "source missing at
        data/dashboard/rollup.json" instead of inventing numbers.
        """
        from src import dashboard_api as _api      # noqa: PLC0415
        from src import dashboard_sources as _src  # noqa: PLC0415

        root = self._root or Path(__file__).resolve().parent.parent
        return _api.build_v2(_src.load_all(root, self._state_dir),
                             sections=sections)

    def rollup(self) -> Dict[str, Any]:
        from src import dashboard_sources as _src  # noqa: PLC0415
        root = self._root or Path(__file__).resolve().parent.parent
        payload, meta = _src.load_rollup(root, self._state_dir)
        return {"rollup": payload, "meta": meta}

    def fingerprint(self) -> str:
        from src import dashboard_sources as _src  # noqa: PLC0415
        root = self._root or Path(__file__).resolve().parent.parent
        return _src.source_fingerprint(root, self._state_dir)

    def healthz(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "dashboard_version": "v2",
            "dashboard_mode": "embedded",
            "engine_liveness": ("halted" if self._engine_status == "halted"
                                else self._engine_status),
            "sources": {},
        }


def _atomic_json(path: Path, payload: Dict[str, Any]) -> Path:
    """Write JSON via tmp + ``os.replace``, preserving mode and ownership.

    Same discipline as ``scripts/rotate_active_log._atomic_write``: a reader
    must never observe a half-written file, and the file is written by the
    ``bettingbot`` engine but read by a separate ``bettingbot`` server, so mode
    has to survive.
    """
    import tempfile

    try:
        st = path.stat()
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode & 0o777
    except OSError:
        uid = gid = -1
        mode = 0o644

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dash_",
                              suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, default=str, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        if uid != -1:
            try:
                os.chown(tmp, uid, gid)
            except (PermissionError, OSError):
                pass
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


# ── File-backed state (standalone dashboard) ──────────────────────────


class FileBackedState:
    """A ``DashboardState`` look-alike that reads files instead of memory.

    Duck-types the only two things ``_DashboardHandler`` needs — ``to_json()``
    and ``v2()`` — so both the embedded and standalone servers share one
    handler, one auth path and one route table.

    This is the class that makes "the dashboard still works when the engine is
    down" true. The embedded server is a thread inside the engine process, so it
    dies with it; this one holds no engine reference at all.
    """

    def __init__(self, root: Path, state_dir: Optional[Path] = None) -> None:
        self._root = Path(root)
        self._state_dir = Path(state_dir) if state_dir else None

    # -- v1, byte-compatible with DashboardState.to_json() ------------

    def to_json(self) -> str:
        return json.dumps(self._v1(), default=str)

    def _v1(self) -> Dict[str, Any]:
        """The v1 payload, rehydrated from the engine's own snapshot.

        Because ``engine_state.json`` *is* ``_serialize()``'s output, the v1
        contract (``engine_status`` vocabulary, absent-until-populated
        ``risk``/``cycle``/``pods``/``settlement``, percentages rather than
        fractions) holds by construction rather than by a parallel
        implementation that could drift.
        """
        from src import dashboard_sources as _src  # noqa: PLC0415

        state, meta = _src.load_engine_state(self._root, self._state_dir)
        rows, rows_meta = _src.load_open_positions(self._root, self._state_dir)

        if state is None:
            # Matches a fresh DashboardState exactly. This is a KNOWN soft spot:
            # a long-dead engine reads as "starting" on v1, because the
            # vocabulary is frozen by the 56-test contract and by
            # src/health_check.py. The honest signal is the additive
            # engine_state_* keys below, and /api/v2/dashboard's four-valued
            # `liveness`. The UI renders only v2.
            out: Dict[str, Any] = {"engine_status": "starting"}
        else:
            out = {k: v for k, v in state.items() if k != "_meta"}

        out["trades"] = list((rows or {}).get("rows") or [])
        out["trades_truncated"] = bool((rows or {}).get("truncated"))
        out["engine_state_available"] = bool(meta.get("available"))
        out["engine_state_age_seconds"] = meta.get("age_seconds")
        out["engine_state_path"] = meta.get("path")
        out["engine_state_reason"] = meta.get("reason")
        out["open_positions_available"] = bool(rows_meta.get("available"))
        out["dashboard_version"] = "v2"
        out["dashboard_mode"] = "standalone"
        return out

    # -- v2 -----------------------------------------------------------

    def v2(self, sections: Optional[list] = None) -> Dict[str, Any]:
        from src import dashboard_api as _api      # noqa: PLC0415
        from src import dashboard_sources as _src  # noqa: PLC0415
        return _api.build_v2(_src.load_all(self._root, self._state_dir),
                             sections=sections)

    def rollup(self) -> Dict[str, Any]:
        from src import dashboard_sources as _src  # noqa: PLC0415
        payload, meta = _src.load_rollup(self._root, self._state_dir)
        return {"rollup": payload, "meta": meta}

    def fingerprint(self) -> str:
        from src import dashboard_sources as _src  # noqa: PLC0415
        return _src.source_fingerprint(self._root, self._state_dir)

    def healthz(self) -> Dict[str, Any]:
        payload = self.v2(sections=["now"])
        srcs = payload.get("sources") or {}
        return {
            "ok": bool(srcs.get("engine_state", {}).get("available")
                       or srcs.get("manager_status", {}).get("available")),
            "dashboard_version": "v2",
            "dashboard_mode": "standalone",
            "engine_liveness": (payload.get("engine") or {}).get("liveness"),
            "sources": {name: {"available": m.get("available"),
                               "age_seconds": m.get("age_seconds"),
                               "stale": m.get("stale"),
                               "reason": m.get("reason")}
                        for name, m in srcs.items()},
        }


# ── HTTP request handler ──────────────────────────────────────────────


def _load_dashboard_credentials():
    """Return {username: password} for every configured dashboard user, or {}
    if none. Sources: DASHBOARD_USER/DASHBOARD_PASSWORD env (one user), plus
    every 'user:password' line of /opt/betting-pod-shop/.dashboard_auth (one
    user per line; blank lines and #comments ignored). Read per request, so
    adding or removing a user takes effect immediately with no restart."""
    creds = {}
    user = os.environ.get("DASHBOARD_USER")
    pw = os.environ.get("DASHBOARD_PASSWORD")
    if user and pw:
        creds[user] = pw
    try:
        p = Path(os.environ.get("DASHBOARD_AUTH_FILE",
                                "/opt/betting-pod-shop/.dashboard_auth"))
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                u, sep, pwd = line.partition(":")
                if sep and u and pwd:
                    creds[u] = pwd
    except Exception:
        pass
    return creds


class _DashboardHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler.  ``state`` and ``poll_ms`` are set as class
    attributes by ``WebDashboardServer`` before the server binds."""

    state:   DashboardState
    poll_ms: int

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond(200, b"ok", "text/plain")
            return
        # /healthz is the JSON sibling: same open access (scripts/deploy.sh and
        # Caddy gate on it and have no credentials), but it reports per-source
        # freshness so "the process is up" and "the data is current" stop being
        # the same signal.  Deliberately 200 even when a source is stale — a
        # dashboard that refuses to load because the collector died is strictly
        # worse than one that tells you the collector died.
        if self.path.split("?", 1)[0] == "/healthz":
            self._serve_healthz()
            return
        if not self._authorized():
            return
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._serve_html()
        elif route == "/api/v2/dashboard":
            self._serve_v2()
        elif route == "/api/v2/rollup":
            self._serve_rollup()
        elif self.path == "/api/status":
            self._serve_json()
        elif self.path == "/manager":
            self._serve_manager_html()
        elif self.path == "/api/manager":
            self._serve_manager_json()
        else:
            self.send_error(404, "Not found")

    # -- fund manager views -------------------------------------------------
    # These serve the manager collector's output verbatim. The dashboard does
    # no collection and no evaluation of its own: if it computed its own view
    # of "healthy" it could disagree with the alerting path, and then neither
    # number would be trustworthy. One producer, many readers.

    def _manager_state(self) -> "Optional[dict]":
        try:
            base = Path(__file__).resolve().parent.parent
            path = base / "manager" / "state" / "status.json"
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _serve_manager_json(self) -> None:
        snap = self._manager_state()
        if snap is None:
            self._respond(503, json.dumps({
                "error": "manager status unavailable",
                "hint": "manager/state/status.json missing — is the collector cron running?",
            }).encode("utf-8"), "application/json")
            return
        self._respond(200, json.dumps(snap).encode("utf-8"), "application/json")

    def _serve_manager_html(self) -> None:
        snap = self._manager_state()
        if snap is None:
            self._respond(503,
                          b"<h1>Manager status unavailable</h1><p>manager/state/"
                          b"status.json is missing. Check the collector cron.</p>",
                          "text/html; charset=utf-8")
            return
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manager"))
            import brief as _brief   # noqa: PLC0415
            import checks as _checks  # noqa: PLC0415
            findings = _checks.run_checks(snap)
            body = _brief.render_html(_brief.build(snap, findings))
            self._respond(200, body.encode("utf-8"), "text/html; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            # Never let a rendering bug take down the page — fall back to raw
            # facts, which are more useful than a stack trace anyway.
            self._respond(
                200,
                ("<h1>Manager (raw)</h1><p>Renderer failed: {}</p><pre>{}</pre>"
                 .format(html_mod.escape(str(exc)),
                         html_mod.escape(json.dumps(snap, indent=2)[:200000]))
                 ).encode("utf-8"),
                "text/html; charset=utf-8")

    def _authorized(self) -> bool:  # noqa: N802
        creds = _load_dashboard_credentials()
        if not creds:
            self._respond(503, (b"Dashboard auth not configured. Set DASHBOARD_USER and "
                                b"DASHBOARD_PASSWORD env, or write one 'user:password' per "
                                b"line to /opt/betting-pod-shop/.dashboard_auth (chmod 600)."),
                          "text/plain")
            return False
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                dec = base64.b64decode(hdr[6:]).decode("utf-8")
                user, _, pw = dec.partition(":")
                expected = creds.get(user)
                if expected is not None and hmac.compare_digest(pw, expected):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Betting Pod Shop"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _serve_html(self) -> None:
        body = _HTML_TEMPLATE.replace("__POLL_MS__", str(self.poll_ms))
        self._respond(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_json(self) -> None:
        body = self.state.to_json().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- v2 -----------------------------------------------------------

    def _query(self) -> Dict[str, str]:
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    def _serve_v2(self) -> None:
        """The primary UI feed.

        ``?section=pnl,gates`` trims the payload for a phone that is only
        looking at one tab. An ETag over every source's ``(path, mtime, size)``
        turns a 15-second poll into a 304 for the ~14 minutes of each quarter
        hour when nothing has changed.
        """
        sections = None
        raw = self._query().get("section")
        if raw:
            wanted = [s.strip() for s in raw.split(",") if s.strip()]
            bad = [s for s in wanted if s not in _V2_SECTIONS]
            if bad:
                self._respond(400, json.dumps({
                    "error": "unknown section(s): {}".format(", ".join(bad)),
                    "valid": sorted(_V2_SECTIONS),
                }).encode("utf-8"), "application/json")
                return
            sections = wanted

        try:
            etag = '"{}-{}"'.format(self.state.fingerprint(),
                                    ",".join(sections) if sections else "all")
        except Exception:  # noqa: BLE001
            # Serving without an ETag just costs 304s, but do it loudly — a
            # silently absent header is indistinguishable from a client bug.
            logger.exception("source fingerprint failed; serving without ETag")
            etag = None

        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            payload = self.state.v2(sections=sections)
        except Exception as exc:  # noqa: BLE001
            # A rendering bug must not take the page down; the raw reason is
            # more useful than a stack trace in the browser console.
            logger.exception("v2 build failed")
            self._respond(500, json.dumps({
                "schema": 2,
                "error": "could not build the dashboard payload",
                "detail": "{}: {}".format(type(exc).__name__, exc),
            }).encode("utf-8"), "application/json")
            return

        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_rollup(self) -> None:
        try:
            payload = self.state.rollup()
        except Exception as exc:  # noqa: BLE001
            payload = {"error": "{}: {}".format(type(exc).__name__, exc)}
        self._respond(200, json.dumps(payload, default=str).encode("utf-8"),
                      "application/json")

    def _serve_healthz(self) -> None:
        try:
            payload = self.state.healthz()
            code = 200
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False,
                       "error": "{}: {}".format(type(exc).__name__, exc)}
            code = 503
        self._respond(code, json.dumps(payload, default=str).encode("utf-8"),
                      "application/json")

    def _respond(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
        # Route access log to Python logger at DEBUG level
        logger.debug("web_dashboard: " + fmt, *args)


# ── Web dashboard server ──────────────────────────────────────────────


class WebDashboardServer:
    """Live web dashboard — starts a background HTTP server and serves
    the single-page dashboard at ``http://host:port/``.

    Args:
        host:       Bind address (default ``'0.0.0.0'``).
        port:       Port to listen on (default ``8080``).
        auto_open:  Open the browser automatically on ``start()``
                    (default ``True``).
        poll_secs:  How often the browser polls for fresh data
                    (default ``5`` seconds).
    """

    def __init__(
        self,
        host:      str  = "0.0.0.0",
        port:      int  = 8080,
        auto_open: bool = True,
        poll_secs: int  = 5,
    ) -> None:
        self._host      = host
        self._port      = port
        self._auto_open = auto_open
        self._poll_ms   = poll_secs * 1_000
        self._state     = DashboardState()
        self._server:   Optional[HTTPServer]      = None
        self._thread:   Optional[threading.Thread] = None

    # ── Properties ───────────────────────────────────────────────────

    @property
    def url(self) -> str:
        """URL the dashboard is served at."""
        return f"http://{self._host}:{self._port}"

    @property
    def state(self) -> DashboardState:
        """The underlying ``DashboardState`` (for testing or direct access)."""
        return self._state

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the HTTP server on a background daemon thread.

        Optionally opens the browser to the dashboard URL.
        Safe to call multiple times (no-op if already running).
        """
        if self._server is not None:
            logger.debug("web_dashboard: already running at %s", self.url)
            return

        # Build a concrete handler class with state and poll_ms baked in
        state   = self._state
        poll_ms = self._poll_ms

        class _Handler(_DashboardHandler):
            pass

        _Handler.state   = state
        _Handler.poll_ms = poll_ms

        self._server = ThreadingHTTPServer((self._host, self._port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="WebDashboard",
        )
        self._thread.start()
        logger.info("Web dashboard running at %s", self.url)

        if self._auto_open:
            import time
            import webbrowser
            time.sleep(0.15)        # let the socket bind before the browser hits it
            webbrowser.open(self.url)

    def stop(self) -> None:
        """Shut down the HTTP server gracefully."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self._thread = None
            logger.info("Web dashboard stopped")

    # ── Data update ──────────────────────────────────────────────────

    def update(
        self,
        snapshot:           Optional[Any] = None,
        report:             Optional[Any] = None,
        performances:       Optional[Dict[str, Any]] = None,
        allocations:        Optional[Dict[str, Any]] = None,
        settlement_summary: Optional[Dict[str, Any]] = None,
        trades:             Optional[list] = None,
    ) -> None:
        """Push fresh engine data to the dashboard state.

        Can be called from any thread.  Only non-None arguments are
        applied, so partial updates are fine.
        """
        self._state.update(
            snapshot=snapshot,
            report=report,
            performances=performances,
            allocations=allocations,
            settlement_summary=settlement_summary,
            trades=trades,
        )


# ── HTML template ─────────────────────────────────────────────────────
# Loaded from src/templates/dashboard.html at import time.
# __POLL_MS__ is replaced at serve-time with the actual poll interval.

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _load_template() -> str:
    """Load the dashboard HTML template from disk.

    Prefers ``dashboard_v2.html`` and falls back to the original
    ``dashboard.html``, so a partial deploy (new code, old templates) still
    serves a working page rather than the placeholder. Falls back again to a
    minimal placeholder if neither is present.
    """
    for name in ("dashboard_v2.html", "dashboard.html"):
        path = _TEMPLATE_DIR / name
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue

    logger.warning("Dashboard template not found in %s", _TEMPLATE_DIR)
    return (
        "<!DOCTYPE html><html><body>"
        "<h1>BETTING POD SHOP</h1>"
        "<p>Dashboard template not found. Check "
        "src/templates/dashboard_v2.html</p>"
        "</body></html>"
    )


_HTML_TEMPLATE = _load_template()
