"""
src/cli.py
──────────
CLI entry point and argument parsing for the Betting Pod Shop engine.

Wires together config_loader, engine, and all runtime components into
a single invocable command.

Usage
-----
    # Single scan cycle (paper mode):
    python -m src.main --once

    # Continuous loop every 5 minutes, specific pods:
    python -m src.main --loop --interval 300 --pods P-001,P-006

    # Live mode, custom config:
    python -m src.main --loop --mode live --config /path/to/config.yaml

    # Debug with verbose per-trade logging:
    python -m src.main --once --log-level DEBUG -v
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from src.aggregate_risk import AggregateRiskGuard
from src.capital_allocator import CapitalAllocator
from src.config_loader import (
    DEFAULT_MULTI_CONFIG,
    enrich_sports_with_active_tennis,
    filter_pods,
    load_config,
)
from src.dashboard import DashboardRenderer
from src.engine import (
    _run_guarded_loop,
    build_shared_deps,
    build_venue_clients,
    make_cycle_callback,
)
from src.env_loader import load as _load_env
from src.pod_runner import PodRunner
from src.trade_store import TradeStore
from src.web_dashboard import WebDashboardServer

logger = logging.getLogger(__name__)


# ── Logging setup ────────────────────────────────────────────────────

def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure root logger with console (and optionally file) handler."""
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
        force=True,
    )


# ── Argument parser ──────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.main",
        description="Betting Pod Shop — Multi-Pod Scan Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument(
        "--config", default=DEFAULT_MULTI_CONFIG,
        help="Path to multi-pod config YAML (default: %(default)s)",
    )
    p.add_argument(
        "--base-config", default=None, dest="base_config",
        help="Path to base config.yaml (merged under multi-pod config)",
    )
    p.add_argument(
        "--mode", choices=["paper", "live"], default=None,
        help="Override execution mode for all venue clients",
    )
    p.add_argument(
        "--pods", default=None,
        help="Comma-separated pod IDs to run, e.g. P-001,P-006",
    )

    run_group = p.add_mutually_exclusive_group()
    run_group.add_argument(
        "--once", action="store_true",
        help="Run a single scan cycle then exit (default if neither flag given)",
    )
    run_group.add_argument(
        "--loop", action="store_true",
        help="Run continuously until KeyboardInterrupt",
    )

    p.add_argument(
        "--interval", type=float, default=60.0,
        help="Seconds between scan cycles in --loop mode (default: %(default)s)",
    )
    p.add_argument(
        "--max-cycles", type=int, default=None, dest="max_cycles",
        help="Maximum cycles to run in --loop mode",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        dest="log_level",
        help="Logging verbosity (default: %(default)s)",
    )
    p.add_argument(
        "--log-file", default=None, dest="log_file",
        help="Append logs to this file in addition to stdout",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Log per-trade details at DEBUG level",
    )
    p.add_argument(
        "-d", "--dashboard", action="store_true",
        help="Print a live terminal dashboard after every scan cycle",
    )
    p.add_argument(
        "-w", "--web", action="store_true",
        help="Serve a live web dashboard (default: http://localhost:8080)",
    )
    p.add_argument(
        "--web-port", type=int, default=8080, dest="web_port",
        help="Port for the web dashboard (default: %(default)s)",
    )
    p.add_argument(
        "--no-browser", action="store_true", dest="no_browser",
        help="Don't auto-open the browser when --web is set",
    )

    # ── Health check ──────────────────────────────────────────────
    p.add_argument(
        "--health-check", default=None, dest="health_check", metavar="POD_ID",
        help=(
            "Run a structured health check for a pod (e.g. P-001) or 'all'. "
            "Queries the live dashboard API and prints a diagnostic report."
        ),
    )
    p.add_argument(
        "--dashboard-url", default="http://129.212.176.202:8080",
        dest="dashboard_url",
        help="Dashboard URL for health checks (default: %(default)s)",
    )
    p.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output health check results as JSON",
    )

    return p


# ── Entry point ──────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """Run the Betting Pod Shop engine.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code — 0 for success, 1 for error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Default to --once if neither flag set
    if not args.once and not args.loop:
        args.once = True

    setup_logging(args.log_level, args.log_file)

    # Load .env before any clients are constructed
    _load_env()

    logger.info("Betting Pod Shop starting")

    # ── Load config ──────────────────────────────────────────────────
    try:
        config = load_config(args.config, args.base_config)
    except (FileNotFoundError, RuntimeError) as exc:
        logger.error("Config error: %s", exc)
        return 1

    if args.pods:
        config = filter_pods(config, args.pods)

    config = enrich_sports_with_active_tennis(config)

    # ── Build all components ─────────────────────────────────────────
    clients = build_venue_clients(config, args.mode)
    shared = build_shared_deps(config, clients)

    allocator = CapitalAllocator.from_config(config)
    guard = AggregateRiskGuard.from_config(config)

    shared["aggregate_risk"] = guard

    trade_log_path = Path(
        config.get("paths", {}).get("trade_log", "data/trade_logs/trade_log.jsonl")
    )

    # ── TradeStore: single in-memory indexed store ──
    # Bound startup memory on large logs (see main.py for rationale).  Open
    # positions and the full dedup fingerprint set are always preserved.
    max_load_entries = int(
        config.get("trade_store", {}).get("max_load_entries", 50000) or 0
    )
    trade_store = TradeStore.from_log(
        trade_log_path, max_entries=max_load_entries or None
    )
    logger.info(
        "TradeStore loaded: %d entries, %d placed, %d open, %d settlements%s",
        trade_store.entry_count, trade_store.placed_count,
        trade_store.open_count, trade_store.settlement_count,
        f" (bounded, {trade_store.total_scanned} scanned)" if trade_store.bounded else "",
    )
    shared["trade_store"] = trade_store

    dash_renderer: Optional[DashboardRenderer] = None
    if getattr(args, "dashboard", False):
        dash_renderer = DashboardRenderer(use_color=True, width=80)
        logger.info("Dashboard mode enabled")

    web_server: Optional[WebDashboardServer] = None
    if getattr(args, "web", False):
        web_server = WebDashboardServer(
            port=args.web_port,
            auto_open=not getattr(args, "no_browser", False),
        )
        web_server.start()

    # Bootstrap from store (no disk I/O).  Paper positions from pods that
    # have a settler DO count — they get closed on settlement, so the
    # guard should start the process knowing about them.
    allocator.bootstrap_from_store(trade_store)
    guard.bootstrap_from_store(
        trade_store, settled_pod_ids=shared.get("settled_pod_ids"),
    )

    callback = make_cycle_callback(
        guard,
        verbose=args.verbose,
        renderer=dash_renderer,
        allocator=allocator,
        web_server=web_server,
        trade_log_path=trade_log_path,
        trade_store=trade_store,
    )

    try:
        runner = PodRunner.from_config(
            config,
            venue_clients=clients,
            shared_deps=shared,
            capital_allocator=allocator,
            cycle_callback=callback,
        )
    except Exception as exc:
        logger.error("Failed to build PodRunner: %s", exc)
        return 1

    if not runner.pods:
        logger.warning(
            "No pods loaded. Verify 'pods.active' in config "
            "and that pod dependencies are available."
        )
        return 1

    logger.info(
        "Engine ready: %d pods loaded [%s]",
        len(runner.pods),
        ", ".join(p.pod_id for p in runner.pods),
    )

    # ── Run ──────────────────────────────────────────────────────────
    try:
        if args.once:
            if not guard.check_pre_cycle():
                snap = guard.snapshot()
                logger.error("Risk guard blocked scan: %s", snap.halt_reason)
                return 1
            runner.run_once()
            logger.info("Single scan complete.")

        else:
            logger.info(
                "Starting loop — interval=%.0fs max_cycles=%s",
                args.interval, args.max_cycles,
            )
            # Signal systemd that startup is complete (Type=notify)
            try:
                from src.watchdog import notify_ready
                notify_ready()
            except Exception:
                pass
            _run_guarded_loop(
                runner, guard, args.interval, args.max_cycles,
                settler=shared.get("settler"),
                polymarket_settler=shared.get("polymarket_settler"),
                allocator=allocator,
            )

    except KeyboardInterrupt:
        snap = guard.snapshot()
        logger.info(
            "Interrupted. Final state: open_positions=%d daily_pnl=%.2f",
            snap.open_positions, snap.daily_pnl,
        )

    finally:
        if web_server is not None:
            web_server.stop()

    return 0
