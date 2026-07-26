#!/usr/bin/env python3
"""
scripts/run_round_leader_fade.py
────────────────────────────────
Standalone runner for P-022 (Round-Leader Dead-Heat Fade maker) — paper only.
Mirrors scripts/run_golf_maker.py (P-017M): a fast loop that discovers open
Kalshi round-leader markets and cycles quote/fill/markout/settle. Logs to
data/trade_logs/round_leader_fade_{quotes,fills}.jsonl.

The engine only PLACES new quotes inside each market's 24h->12h-before-close
window (and rests them through the round to close), so it is safe to run
continuously; outside that window it discovers/settles but places nothing.

Usage:
    python3 scripts/run_round_leader_fade.py [--interval 20] [--rediscover 900]

Kill switch: create data/KILL_ROUND_LEADER_FADE to pull all quotes immediately.
"""
from __future__ import annotations

import argparse
import logging
import time

from src.config_loader import load_config  # type: ignore
from src.round_leader_fade_maker import RoundLeaderFadeMakerPod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_round_leader_fade")


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
    engine = RoundLeaderFadeMakerPod.from_config(config)

    logger.info("P-022 round-leader fade-maker starting (paper). Ctrl-C to stop.")
    last_discover = 0.0
    try:
        while True:
            now = time.time()
            if now - last_discover >= args.rediscover:
                n = engine.discover()
                if n:
                    logger.info("P-022: discovered %d new round-leader markets", n)
                last_discover = now
            engine.cycle()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("P-022: stopped by user")


if __name__ == "__main__":
    main()
