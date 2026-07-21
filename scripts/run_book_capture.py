#!/usr/bin/env python3
"""
scripts/run_book_capture.py
───────────────────────────
Standalone runner for the high-cadence book capture daemon.

Deployed as its own systemd unit (betting-book-capture) so a capture bug can
never take down the 5-minute engine or the P-016 maker, and vice versa.

This is a DATA COLLECTOR.  It places no orders and reads no credentials —
it talks only to Kalshi's public unauthenticated market-data endpoints.

Kill switch:  touch data/KILL_CAPTURE   (idles within one cycle)

Usage:
    python3 -m scripts.run_book_capture [--config config_multi_pod.yaml]
                                        [--cadence 60] [--rate 4.0]
                                        [--series KXMLBGAME]
                                        [--once] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.book_capture import (  # noqa: E402
    BookCapture, CaptureConfig, max_markets_for_budget,
)

logger = logging.getLogger("run_book_capture")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    logger.info("received signal %s — finishing cycle and exiting", signum)
    _stop = True


def load_config(path: str) -> dict:
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("config %s not found — using defaults", path)
        return {}
    except Exception as exc:
        logger.warning("could not load config %s (%s) — using defaults",
                       path, exc)
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Kalshi book capture daemon")
    ap.add_argument("--config", default="config_multi_pod.yaml")
    ap.add_argument("--cadence", type=float, default=None,
                    help="seconds between snapshots (default 60)")
    ap.add_argument("--rate", type=float, default=None,
                    help="max requests/sec (default 4.0; ~7 is where the "
                         "API starts 429ing)")
    ap.add_argument("--series", action="append", default=None,
                    help="Kalshi series ticker; repeatable "
                         "(default KXMLBGAME)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--once", action="store_true",
                    help="run a single cycle and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="discover markets and print the capture plan, "
                         "write nothing")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = CaptureConfig.from_config(load_config(args.config))
    if args.cadence is not None:
        cfg.cadence_s = args.cadence
    if args.rate is not None:
        cfg.rate_per_s = args.rate
    if args.series:
        cfg.series = args.series
    if args.out_dir:
        cfg.out_dir = Path(args.out_dir)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if args.dry_run:
        cap = BookCapture(cfg)
        targets = cap.discover()
        print(f"\ncadence      : {cfg.cadence_s:.0f}s")
        print(f"rate budget  : {cfg.rate_per_s:.1f} req/s "
              f"(safety {cfg.safety:.2f})")
        print(f"market cap   : {cap.max_markets} "
              f"({'2' if cfg.capture_trades else '1'} req/market/cycle)")
        print(f"discovered   : {len(targets) + cap._dropped_last_discovery}")
        print(f"capturing    : {len(targets)}")
        print(f"dropped      : {cap._dropped_last_discovery}")
        est_mb = len(targets) * (86400 / max(1.0, cfg.cadence_s)) * 350 / 1e6
        print(f"est. volume  : ~{est_mb:.0f} MB/day raw "
              f"(~{est_mb / 8:.0f} MB gzipped)")
        for t in targets[:10]:
            print(f"  {t['ticker']:<40} vol24h={t['volume_24h']:.0f}")
        if len(targets) > 10:
            print(f"  ... and {len(targets) - 10} more")
        return 0

    cap = BookCapture(cfg)
    cap.run(stop=lambda: _stop, max_cycles=1 if args.once else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
