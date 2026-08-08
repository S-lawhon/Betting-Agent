#!/usr/bin/env python3
"""Refresh Gemini/Kalshi historical analytics without collecting new quotes."""

from __future__ import annotations

import argparse
import fcntl
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_gemini_crossvenue_research import (  # noqa: E402
    refresh_historical_analysis,
)
from src.settlement_registry import load_policy_state  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
PAIR_ID = "gemini_kalshi_mlb_moneyline"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue")
    parser.add_argument("--settlement-config", type=Path,
                        default=ROOT / "config" / "settlement_equivalence.yaml")
    parser.add_argument("--settlement-manifest", type=Path,
                        default=ROOT / "data" / "settlement_registry"
                        / "manifest.json")
    parser.add_argument("--window-days", type=int, default=14)
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.output_dir / ".collector.lock"
    with lock_path.open("w") as lock:
        # The hourly replay waits for a short quote capture to finish. The
        # five-minute collector remains non-blocking and will retry next tick
        # if a replay happens to cross its boundary.
        fcntl.flock(lock, fcntl.LOCK_EX)
        refreshed_at = datetime.now(UTC)
        policy = load_policy_state(
            args.settlement_config, args.settlement_manifest, PAIR_ID)
        analytics, cases = refresh_historical_analysis(
            args.output_dir, captured_at=refreshed_at,
            terms_policy=policy, window_days=args.window_days)

    print(
        "gemini cross-venue analysis: observations={} cases={} lifetime={}".format(
            analytics.get("observation_rows"), cases.get("cases_replayed"),
            cases.get("lifetime_cases")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
