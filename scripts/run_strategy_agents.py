#!/usr/bin/env python3
"""
scripts/run_strategy_agents.py
───────────────────────────────
Live worker for the recursive strategy registry.

This is a queue-based daemon. Other tools or humans drop JSON requests into
data/strategy_agents/queue/<role>/ and the worker applies them to the shared
registry, persists the result, and emits a heartbeat for monitoring.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_agent_runtime import StrategyAgentRuntime  # noqa: E402

logger = logging.getLogger("run_strategy_agents")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the recursive strategy agents")
    ap.add_argument("--registry", default="data/strategy_agents/registry.json")
    ap.add_argument("--queue-dir", default="data/strategy_agents/queue")
    ap.add_argument("--processed-dir", default=None)
    ap.add_argument("--heartbeat", default="data/strategy_agents/heartbeat.jsonl")
    ap.add_argument("--poll-interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runtime = StrategyAgentRuntime(
        registry_path=Path(args.registry),
        queue_dir=Path(args.queue_dir),
        processed_dir=Path(args.processed_dir) if args.processed_dir else None,
        heartbeat_path=Path(args.heartbeat),
        poll_interval_s=args.poll_interval,
    )

    stop = {"flag": False}

    def _sig(signum, _frame):
        logger.info("signal %s received — shutting down after current cycle", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    if args.once:
        summary = runtime.run_once()
        logger.info("run_once summary: %s", summary.to_dict())
        return 0

    runtime.run_forever(stop=lambda: stop["flag"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
