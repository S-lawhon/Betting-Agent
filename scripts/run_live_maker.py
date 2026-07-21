#!/usr/bin/env python3
"""
scripts/run_live_maker.py
─────────────────────────
Standalone runner for P-016 (Live Maker, Kalshi MLB paper pilot).

Runs its own fast loop (default 12s while games are live) because the
main engine's 5-minute cycle cannot maintain resting quotes.  Deployed
as a separate systemd unit (betting-live-maker) so the existing
betting-pod-shop service is untouched.

Kill switch: `touch data/KILL_MAKER` pulls all quotes within one cycle.

Usage:
    python3 -m scripts.run_live_maker [--config config_multi_pod.yaml]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pods.live_maker_pod import LiveMakerEngine, LiveMakerPod  # noqa: E402

logger = logging.getLogger("run_live_maker")

LIVE_CYCLE_S = 12.0        # cycle period while any game is live
IDLE_CYCLE_S = 120.0       # cycle period while games upcoming today
DORMANT_CYCLE_S = 600.0    # no games / all done
DISCOVER_EVERY_S = 900.0   # re-run schedule+market discovery


def load_config(path: str) -> dict:
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("config load failed (%s) — using defaults", exc)
        return {}


def et_today() -> str:
    """MLB schedule date — use ET so late West-coast games stay on
    today's slate past midnight UTC."""
    et = datetime.now(timezone(timedelta(hours=-4)))
    return et.strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_multi_pod.yaml")
    ap.add_argument("--once", action="store_true",
                    help="single discovery+cycle pass (smoke test)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    engine: LiveMakerEngine = LiveMakerPod.from_config(config)

    stop = {"flag": False}

    def _sig(_signum, _frame):
        logger.info("signal received — shutting down after current cycle")
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    last_discover = 0.0
    games = []
    logger.info("P-016 Live Maker runner starting (paper). Kill switch: %s",
                engine.kill_file)

    while not stop["flag"]:
        now = time.time()
        if now - last_discover > DISCOVER_EVERY_S or not games:
            games = engine.statsapi.schedule(et_today())
            n_new = engine.discover(games)
            engine.mark_final_from_schedule(games)
            last_discover = now
            n_live = sum(1 for g in games if g.status == "Live")
            logger.info("discovery: %d games today, %d live, %d new books, "
                        "%d books total", len(games), n_live, n_new,
                        len(engine.books))

        statuses = {g.game_pk: g.status for g in games}
        any_live = any(
            statuses.get(pk) == "Live"
            for pk, b in engine.books.items() if not b.done
        )
        upcoming = any(
            statuses.get(pk) == "Preview"
            for pk, b in engine.books.items() if not b.done
        )

        if any_live:
            engine.cycle()
            sleep_s = LIVE_CYCLE_S
        elif upcoming:
            sleep_s = IDLE_CYCLE_S
        else:
            sleep_s = DORMANT_CYCLE_S

        if args.once:
            break

        # Sleep in 1s slices so SIGTERM stays responsive
        deadline = time.time() + sleep_s
        while time.time() < deadline and not stop["flag"]:
            time.sleep(1.0)

    logger.info("P-016 runner stopped.")


if __name__ == "__main__":
    main()
