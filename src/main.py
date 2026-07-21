"""
src/main.py
──────────
CLI entry point for the Betting Pod Shop engine.

This module serves as the backward-compatible entry point.  The heavy
lifting has been split into:
  - ``config_loader.py`` — config loading, merging, pod filtering
  - ``engine.py``        — component construction, cycle callback, run loop
  - ``cli.py``           — argument parser, logging setup

All public symbols are re-exported here so existing imports and test
patches (e.g. ``@patch("src.main.PodRunner")``) continue to work.

Usage
-----
    python -m src.main --once
    python -m src.main --loop --interval 300 --pods P-001,P-006
"""

import logging
import sys
from pathlib import Path
from typing import List, Optional

# ── Core imports (needed for test patching via src.main.X) ───────────
from src.aggregate_risk import AggregateRiskGuard       # noqa: F401
from src.capital_allocator import CapitalAllocator       # noqa: F401
from src.dashboard import DashboardRenderer              # noqa: F401
from src.env_loader import load as _load_env             # noqa: F401
from src.pod_runner import CycleReport, PodRunner        # noqa: F401
from src.trade_store import TradeStore                   # noqa: F401
from src.web_dashboard import WebDashboardServer         # noqa: F401

# ── Re-export from config_loader ─────────────────────────────────────
from src.config_loader import (                          # noqa: F401
    DEFAULT_MULTI_CONFIG,
    _deep_merge,
    _enrich_sports_with_active_tennis,
    enrich_sports_with_active_tennis,
    filter_pods,
    load_config,
)

# ── Re-export from engine ────────────────────────────────────────────
from src.engine import (                                 # noqa: F401
    _compute_settlement_summary,
    _read_recent_placed_trades,
    _resolve_bet_team,
    _run_guarded_loop,
    build_shared_deps,
    build_venue_clients,
    make_cycle_callback,
)

# ── Re-export from cli ───────────────────────────────────────────────
from src.cli import (                                    # noqa: F401
    _build_parser,
    setup_logging,
)

# ── Health check ───────────────────────────────────────────────────
from src.health_check import (                           # noqa: F401
    health_check_main,
    run_health_check,
)

logger = logging.getLogger(__name__)


# ── Entry point (kept here for backward compat with test patches) ────

def main(argv: Optional[List[str]] = None) -> int:
    """Run the Betting Pod Shop engine.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code — 0 for success, 1 for error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── Health check (fast path — no engine boot required) ────────
    if getattr(args, "health_check", None):
        setup_logging(args.log_level, args.log_file)
        return health_check_main(
            pod_id=args.health_check,
            dashboard_url=getattr(args, "dashboard_url", "http://129.212.176.202:8080"),
            config_path=args.config,
            json_output=getattr(args, "json_output", False),
        )

    # Default to --once if neither flag set
    if not args.once and not args.loop:
        args.once = True

    setup_logging(args.log_level, args.log_file)
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

    # ── TradeStore ───────────────────────────────────────────────────
    # Cap how many recent entries are held in memory at load time so a large
    # trade log can't OOM the process on startup.  Open positions and the full
    # dedup fingerprint set are always preserved regardless of this cap.
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

    # These settlers read open positions from the store, not from
    # data/pods/<pod>.jsonl — with a TradeStore attached, BasePod.write_log
    # delegates to store.append() and the per-pod files are never written.
    # The store is built after build_shared_deps(), so attach it here.
    for _settler_key in ("golf_settler", "tennis_settler"):
        _s = shared.get(_settler_key)
        if _s is not None:
            _s.trade_store = trade_store

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
            try:
                from src.watchdog import notify_ready
                notify_ready()
            except Exception:
                pass
            _run_guarded_loop(
                runner, guard, args.interval, args.max_cycles,
                settler=shared.get("settler"),
                polymarket_settler=shared.get("polymarket_settler"),
                tennis_settler=shared.get("tennis_settler"),
                golf_settler=shared.get("golf_settler"),
                allocator=allocator,
                trade_store=trade_store,
                settlement_interval_cycles=int(
                    (config.get("settlement") or {}).get("interval_cycles", 1)
                ),
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


if __name__ == "__main__":
    sys.exit(main())
