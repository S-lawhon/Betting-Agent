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
import fcntl
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
    ap.add_argument("--lock-file", default="data/strategy_agents/worker.lock")
    ap.add_argument("--poll-interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    worker_lock = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(worker_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error("another strategy-agent worker holds %s", lock_path)
        return 2

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
    fcntl.flock(worker_lock.fileno(), fcntl.LOCK_UN)
    worker_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
