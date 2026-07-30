"""
src/dashboard_server.py
───────────────────────
The standalone dashboard: a file-reading HTTP server that holds no reference to
the engine.

    python3 -m src.dashboard_server                       # 127.0.0.1:8081
    python3 -m src.dashboard_server --port 8081 --host 127.0.0.1

Why it is a separate process
───────────────────────────
The embedded dashboard (``src.main --web``) runs on a daemon thread *inside* the
engine process. An OOM kill, ``Restart=on-failure`` with ``RestartSec=30``, or a
wedged scan loop takes the page down with the engine — which is exactly the
moment the page is worth having. It also means the highest-priority workload on
a 2 GB box was serving HTTP and holding an unbounded trade list in the same
address space.

This process reads files and nothing else:

    data/dashboard/engine_state.json      <- engine, every cycle
    data/dashboard/open_positions.json    <- engine, every cycle
    data/dashboard/rollup.json            <- scripts/build_dashboard_rollup.py
    manager/state/status.json             <- manager/collect.py cron
    data/p022_window_check/status.jsonl   <- scripts/p022_window_check.py
    data/KILL_*                           <- humans

So when the engine is down, the page still renders — and says so, sourcing
liveness from systemd's own view in ``status.json`` rather than from silence.

Rejected alternative: ``Restart=always`` on the embedded server. A wedged engine
keeps the socket bound and serves stale JSON, which is indistinguishable from
health — the failure class ``scripts/p022_window_check.py`` exists to catch.

Ports and auth
──────────────
Binds ``127.0.0.1:8081`` by default, behind Caddy (see
``scripts/Caddyfile.dashboard.template``). It does **not** take 8080: the engine
keeps that during the side-by-side phase so ``scripts/deploy.sh``'s health gate
is untouched. Auth, the 401 realm, the no-credentials 503 and the open
``/health`` all come from ``_DashboardHandler`` unchanged — one handler, one
auth path, two states.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web_dashboard import (  # noqa: E402
    _DashboardHandler,
    FileBackedState,
    _load_dashboard_credentials,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8081
DEFAULT_HOST = "127.0.0.1"
DEFAULT_POLL_SECS = 15


def build_server(root: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 state_dir: Optional[Path] = None,
                 poll_secs: int = DEFAULT_POLL_SECS) -> ThreadingHTTPServer:
    """Construct (but do not serve) the standalone dashboard server."""
    state = FileBackedState(root, state_dir)

    class _Handler(_DashboardHandler):
        pass

    _Handler.state = state          # type: ignore[assignment]
    _Handler.poll_ms = poll_secs * 1_000
    return ThreadingHTTPServer((host, port), _Handler)


def request_shutdown(server: ThreadingHTTPServer) -> threading.Thread:
    """Stop ``server`` without blocking the calling thread.

    ``shutdown()`` waits for ``serve_forever()``'s poll loop to notice the
    stop flag — but a signal handler runs on the main thread, which is the
    thread *inside* ``serve_forever()``.  Calling ``shutdown()`` inline there
    deadlocks: the handler waits on a loop that cannot advance until the
    handler returns (F2 — systemctl stop hung 90s to SIGKILL, 2026-07-30).
    Spawning it on a helper thread lets the handler return immediately, the
    poll loop resumes, sees the flag, and exits.
    """
    t = threading.Thread(target=server.shutdown, daemon=True)
    t.start()
    return t


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Standalone Betting Pod Shop dashboard (file-backed).")
    ap.add_argument("--root", default=None,
                    help="project root (default: the repo this file lives in)")
    ap.add_argument("--state-dir", default=None,
                    help="dashboard state dir (default: <root>/data/dashboard)")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="bind address (default 127.0.0.1 — sits behind Caddy)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="port (default 8081; the engine keeps 8080)")
    ap.add_argument("--poll-secs", type=int, default=DEFAULT_POLL_SECS,
                    help="browser poll interval baked into the page")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")

    root = Path(args.root).resolve() if args.root \
        else Path(__file__).resolve().parent.parent
    state_dir = Path(args.state_dir).resolve() if args.state_dir else None

    if not _load_dashboard_credentials():
        # Not fatal: the handler already answers 503 with an explanation on
        # every authenticated route, and /health and /healthz stay open so the
        # deploy gate still works. Saying it once at boot beats discovering it
        # from a browser.
        logger.warning(
            "no dashboard credentials configured — authenticated routes will "
            "return 503. Set DASHBOARD_USER/DASHBOARD_PASSWORD or add a "
            "user:password line to /opt/betting-pod-shop/.dashboard_auth")

    server = build_server(root, args.host, args.port, state_dir, args.poll_secs)

    def _shutdown(signum, _frame):  # noqa: ANN001
        logger.info("received signal %s — shutting down", signum)
        request_shutdown(server)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):      # not the main thread
            pass

    logger.info("standalone dashboard on http://%s:%d (root=%s)",
                args.host, args.port, root)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
