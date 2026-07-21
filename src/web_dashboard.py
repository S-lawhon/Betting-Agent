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
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── Dashboard state ───────────────────────────────────────────────────


class DashboardState:
    """Thread-safe container for the latest engine snapshot.

    ``update()`` is called from the scan-loop thread; ``to_json()`` is
    called from the HTTP-server thread.  A ``threading.Lock`` protects
    all reads and writes.
    """

    def __init__(self) -> None:
        self._lock          = threading.Lock()
        self._snapshot: Optional[Any] = None
        self._report: Optional[Any]   = None
        self._performances: Dict[str, Any] = {}
        self._allocations:  Dict[str, Any] = {}
        self._settlement: Optional[Dict[str, Any]] = None
        self._trades: list = []
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
                self._trades = trades

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
        if not self._authorized():
            return
        if self.path in ("/", "/index.html"):
            self._serve_html()
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

    Falls back to a minimal placeholder if the file is missing (e.g. during
    testing or packaging where templates weren't bundled).
    """
    path = _TEMPLATE_DIR / "dashboard.html"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Dashboard template not found at %s", path)
        return (
            "<!DOCTYPE html><html><body>"
            "<h1>Betting Pod Shop</h1>"
            "<p>Dashboard template not found. Check src/templates/dashboard.html</p>"
            "</body></html>"
        )


_HTML_TEMPLATE = _load_template()
