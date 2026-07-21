"""
src/main.py
───────────
Entry point and orchestration for the Betting Agent.

Run (paper mode — default):
    python -m src.main

The agent will:
  1. Load config.yaml and .env credentials.
  2. Initialise all components (Kalshi client, Odds client, Risk manager,
     Matcher, Edge calculator, Learner).
  3. Run a continuous scan-and-decide loop every ``scan_interval_seconds``
     (default 300 s / 5 min), logging every edge and paper-mode decision to
     data/trade_logs/trade_log.jsonl.
  4. Shut down cleanly on Ctrl+C or a fatal error.

Live trading requires BOTH:
  - ``environment: live`` in config.yaml
  - ``I_UNDERSTAND_LIVE_TRADING=true`` in .env
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from logger_setup import configure_logging, get_logger

# ── Constants ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH  = PROJECT_ROOT / "config.yaml"
ENV_PATH     = PROJECT_ROOT / ".env"

log = get_logger(__name__)

_DEFAULT_SCAN_INTERVAL = 300   # seconds between scan cycles
_DEFAULT_BANKROLL      = 10_000.0


# ── Config loading ────────────────────────────────────────────────────────────

def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """
    Load and return the YAML config.

    Environment variables shadow config values where keys overlap
    (e.g. LOG_LEVEL overrides config.yaml log_level).

    Raises
    ------
    FileNotFoundError
        If config.yaml is missing.
    yaml.YAMLError
        If the file is malformed.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open() as fh:
        config = yaml.safe_load(fh)

    # Allow env vars to override top-level scalar values
    if env_log_level := os.environ.get("LOG_LEVEL"):
        config["log_level"] = env_log_level
    if env_env := os.environ.get("ENVIRONMENT"):
        config["environment"] = env_env

    return config


# ── Component initialisation ─────────────────────────────────────────────────

def _build_components(config: dict) -> dict:
    """
    Instantiate all runtime components and return them in a dict.

    Reads KALSHI_EMAIL, KALSHI_PASSWORD, and ODDS_API_KEY from the
    process environment (loaded from .env by main()).

    Returns keys: ledger, risk, edge, kalshi, odds, matcher, learner,
                  scanner, executor.
    """
    from consistency_checker import ConsistencyChecker
    from edge_calculator import EdgeCalculator
    from executor import Executor
    from fatigue_analyzer import FatigueAnalyzer
    from kalshi_client import KalshiClient
    from learner import Learner
    from matcher import Matcher
    from odds_client import OddsClient
    from risk_manager import Ledger, RiskManager
    from scanner import Scanner
    from settler import Settler

    # ── Credentials ───────────────────────────────────────────────────────────
    # Kalshi: prefer RSA key auth (current API); fall back to email/password
    api_key_id  = os.environ.get("KALSHI_API_KEY_ID",  "")
    private_key = os.environ.get("KALSHI_PRIVATE_KEY", "")
    email       = os.environ.get("KALSHI_EMAIL",       "")
    password    = os.environ.get("KALSHI_PASSWORD",    "")
    odds_key    = os.environ.get("ODDS_API_KEY",       "")

    has_kalshi = bool(api_key_id and private_key) or bool(email and password)
    missing = []
    if not has_kalshi:
        missing.append("KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY (or KALSHI_EMAIL + KALSHI_PASSWORD)")
    if not odds_key:
        missing.append("ODDS_API_KEY")
    if missing:
        log.warning("missing_credentials", keys=missing,
                    hint="Add them to your .env file")

    # ── Core accounting ────────────────────────────────────────────────────────
    initial_bankroll = float(
        config.get("risk", {}).get("initial_bankroll", _DEFAULT_BANKROLL)
    )
    ledger = Ledger(initial_bankroll=initial_bankroll)
    risk   = RiskManager.from_config(config, ledger)

    # ── Edge / EV calculator ───────────────────────────────────────────────────
    edge = EdgeCalculator.from_config(config)

    # ── API clients ────────────────────────────────────────────────────────────
    kalshi = KalshiClient.from_config(
        config,
        email=email,
        password=password,
        api_key_id=api_key_id,
        private_key=private_key,
    )
    odds   = OddsClient.from_config(config, api_key=odds_key)

    # ── Market matcher ─────────────────────────────────────────────────────────
    matcher = Matcher.from_config(config)

    # ── Probability calibration learner ───────────────────────────────────────
    learner = Learner.from_config(config)

    # ── Schedule-adjusted fatigue analysis (Strategy 1) ─────────────────────────
    fatigue = None
    if config.get("fatigue", {}).get("enabled", False):
        fatigue = FatigueAnalyzer.from_config(config)
        log.info("fatigue_analyzer_enabled", sports=list(fatigue.sports))

    # ── Cross-tier consistency arbitrage (Strategy 2) ──────────────────────────
    consistency = None
    if config.get("consistency", {}).get("enabled", False):
        consistency = ConsistencyChecker.from_config(config)
        log.info("consistency_checker_enabled",
                 sports=config.get("consistency", {}).get("sports", []))

    # ── Scanner (find edges) and Executor (act on them) ───────────────────────
    scanner  = Scanner.from_config(config, kalshi, odds, matcher, edge, risk,
                                   fatigue_analyzer=fatigue)
    executor = Executor.from_config(config, scanner, kalshi, risk)

    # ── Settlement engine (resolve open positions) ─────────────────────────────
    settler = Settler.from_config(config, kalshi, ledger)

    return {
        "ledger":      ledger,
        "risk":        risk,
        "edge":        edge,
        "kalshi":      kalshi,
        "odds":        odds,
        "matcher":     matcher,
        "learner":     learner,
        "fatigue":     fatigue,
        "consistency": consistency,
        "scanner":     scanner,
        "executor":    executor,
        "settler":     settler,
    }


# ── Paper-mode scan loop ──────────────────────────────────────────────────────

def _run_paper_loop(
    config: dict,
    components: dict,
    max_cycles: int | None = None,
) -> None:
    """
    Run the continuous scan-and-decide loop.

    Each cycle:
      1. Calls executor.run_cycle() → scans all configured sports, scores
         edges, sizes positions via Kelly, and logs paper-mode decisions.
      2. Sleeps for scan_interval_seconds before the next cycle.

    Parameters
    ----------
    max_cycles : int | None
        Stop after this many cycles.  ``None`` (default) runs forever.
        Pass ``0`` to skip the loop entirely (used in tests).

    Exits cleanly on KeyboardInterrupt (Ctrl+C).
    """
    executor       = components["executor"]
    settler        = components["settler"]
    consistency    = components.get("consistency")
    scanner        = components.get("scanner")
    scan_interval  = int(config.get("scan_interval_seconds", _DEFAULT_SCAN_INTERVAL))
    environment    = config.get("environment", "demo")

    log.info("paper_loop_start",
             scan_interval_seconds=scan_interval,
             environment=environment,
             max_cycles=max_cycles)

    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        try:
            # ── 1. Settle any resolved positions first ─────────────────────────
            settlements = settler.settle_cycle()
            if settlements:
                n_win  = sum(1 for s in settlements if s.get("outcome") == "WIN")
                n_loss = sum(1 for s in settlements if s.get("outcome") == "LOSS")
                n_void = sum(1 for s in settlements if s.get("outcome") == "VOID")
                log.info("cycle_settlements",
                         cycle=cycle, total=len(settlements),
                         wins=n_win, losses=n_loss, voids=n_void)

            # ── 2. Scan for new edges and place bets ───────────────────────────
            results = executor.run_cycle()
            n_placed  = sum(1 for r in results if r.action == "PLACED")
            n_skipped = sum(1 for r in results if r.action.startswith("SKIPPED"))
            log.info("cycle_complete",
                     cycle=cycle,
                     n_results=len(results),
                     n_placed=n_placed,
                     n_skipped=n_skipped)

            # ── 3. Cross-tier consistency check (Strategy 2) ─────────────────
            if consistency and scanner:
                try:
                    kalshi_markets = scanner.last_kalshi_markets
                    if kalshi_markets:
                        signals = consistency.scan_cycle(kalshi_markets)
                        if signals:
                            log.info("consistency_signals",
                                     cycle=cycle,
                                     signals_found=len(signals),
                                     teams=[s.team for s in signals])
                except Exception as exc:
                    log.warning("consistency_check_error",
                                cycle=cycle, error=str(exc))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.error("cycle_error", cycle=cycle, error=str(exc))

        if max_cycles is None or cycle < max_cycles:
            log.debug("cycle_sleep", seconds=scan_interval)
            time.sleep(scan_interval)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(max_cycles: int | None = None) -> int:
    """
    Entry point.

    Parameters
    ----------
    max_cycles : int | None
        Passed through to ``_run_paper_loop``.  ``None`` (default) runs
        forever; ``0`` skips the loop (used in tests).

    Returns 0 on clean exit, 1 on config / startup error.
    """
    # Load .env before anything else (silently ok if missing)
    load_dotenv(ENV_PATH, override=False)

    try:
        config = load_config()
    except (FileNotFoundError, yaml.YAMLError) as exc:
        print(f"[FATAL] Could not load config: {exc}", file=sys.stderr)
        return 1

    configure_logging(config.get("log_level", "INFO"))
    log.info(
        "startup_ok",
        environment=config.get("environment", "demo"),
        log_level=config.get("log_level", "INFO"),
        config_path=str(CONFIG_PATH),
    )

    # ── Build all components ───────────────────────────────────────────────────
    try:
        components = _build_components(config)
    except Exception as exc:
        log.error("startup_failed", error=str(exc))
        return 1

    log.info("components_initialised",
             environment=config.get("environment", "demo"),
             learner_enabled=config.get("learner", {}).get("enabled", False))

    # ── Rebuild ledger from log (so risk limits are accurate after restart) ────
    try:
        n_rebuilt = components["settler"].rebuild_ledger_from_log()
        log.info("ledger_rebuilt_from_log", open_positions=n_rebuilt)
    except Exception as exc:
        log.warning("ledger_rebuild_failed", error=str(exc))

    # ── Run the scan loop ──────────────────────────────────────────────────────
    try:
        _run_paper_loop(config, components, max_cycles=max_cycles)
    except KeyboardInterrupt:
        log.info("shutdown_ok", reason="keyboard_interrupt")
    except Exception as exc:
        log.error("fatal_error", error=str(exc))
        return 1

    log.info("shutdown_ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
