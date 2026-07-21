#!/usr/bin/env python3
"""
scripts/run_golf_maker.py
─────────────────────────
Standalone runner for P-017M (Golf Fade Maker) — paper only. Mirrors
scripts/run_live_maker.py (P-016): a fast loop that discovers open Kalshi
top-N golf markets and cycles quote/fill/markout/settle. Logs to
data/trade_logs/golf_maker_{quotes,fills}.jsonl.

The engine only quotes inside each market's 36h->6h-before-close window,
so it is safe to run continuously; outside the window it is idle.

Usage:
    python3 scripts/run_golf_maker.py [--interval 20] [--rediscover 900]

Kill switch: create data/KILL_GOLF_MAKER to pull all quotes immediately.
"""
from __future__ import annotations

import argparse
import logging
import time

from src.config_loader import load_config  # type: ignore
from src.golf_fade_maker import GolfFadeMakerPod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_golf_maker")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=20.0,
                    help="seconds between cycles")
    ap.add_argument("--rediscover", type=float, default=900.0,
                    help="seconds between market re-discovery passes")
    args = ap.parse_args()

    try:
        config = load_config()
    except Exception:
        config = {}
    engine = GolfFadeMakerPod.from_config(config)

    logger.info("P-017M golf fade-maker starting (paper). Ctrl-C to stop.")
    last_discover = 0.0
    try:
        while True:
            now = time.time()
            if now - last_discover >= args.rediscover:
                n = engine.discover()
                if n:
                    logger.info("P-017M: discovered %d new golf markets", n)
                last_discover = now
            engine.cycle()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("P-017M: stopped by user")


if __name__ == "__main__":
    main()
