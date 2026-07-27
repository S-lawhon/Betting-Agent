#!/usr/bin/env python3
"""
scripts/check_fee_fixture.py
────────────────────────────
Fail loudly when Kalshi's maker-fee schedule moves away from the committed
fixture.  **This is the check the project keeps missing** — the fee table
drifted five times and every one was found by accident, downstream of a
verdict that had already been written.

    python3 -m scripts.check_fee_fixture          # exit 1 on drift
    python3 -m scripts.check_fee_fixture --json

What counts as drift (exit 1):

  1. A series that exists in BOTH snapshots changed its fee classification.
     This is the real event: a maker-free series starting to charge silently
     inflates every net edge computed against it.
  2. A NEW series appears that charges maker fees.  Unknown series are
     conservatively charged by the runtime, so this is not yet a costing
     error — but it is the fixture going stale in the direction that matters.
  3. Kalshi introduces a `fee_type` the model does not know.
  4. A `_PENDING_SERIES` pre-registration in `src/kalshi_fees.py` has been
     listed for real, so the hand-written entry must be deleted.

What does NOT count (reported, exit 0): new or delisted maker-free series.
Kalshi lists those constantly and alarming on them would train the alarm to
be ignored, which is the same failure as not having one.

Fix a drift by regenerating and committing the fixture:

    python3 -m scripts.generate_fee_fixture
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.generate_fee_fixture import (FIXTURE, MAKER_FEE_TYPE,  # noqa: E402
                                          build, fetch_series)
from src.kalshi_fees import _PENDING_SERIES                          # noqa: E402


def check() -> Dict[str, Any]:
    if not FIXTURE.exists():
        return {"ok": False, "drift": True,
                "reason": f"no committed fixture at {FIXTURE}"}
    old = json.loads(FIXTURE.read_text())
    fresh = build(fetch_series())

    old_all, new_all = set(old["all_series"]), set(fresh["all_series"])
    old_ch, new_ch = set(old["maker_fee_series"]), set(fresh["maker_fee_series"])
    both = old_all & new_all

    started_charging = sorted((new_ch - old_ch) & both)
    stopped_charging = sorted((old_ch - new_ch) & both)
    new_charging_series = sorted((new_ch - old_ch) - both)
    unknown_types = fresh["unknown_fee_types"]
    listed_pending = sorted(set(_PENDING_SERIES) & new_all)

    added_free = sorted((new_all - old_all) - new_ch)
    delisted = sorted(old_all - new_all)

    drift = bool(started_charging or stopped_charging or new_charging_series
                 or unknown_types or listed_pending)
    return {
        "ok": not drift, "drift": drift,
        "fixture_generated_at": old.get("generated_at_utc"),
        "n_series": {"fixture": old["n_series"], "live": fresh["n_series"]},
        "n_charging": {"fixture": len(old_ch), "live": len(new_ch)},
        # drift
        "started_charging": started_charging,
        "stopped_charging": stopped_charging,
        "new_series_that_charge": new_charging_series,
        "unknown_fee_types": unknown_types,
        "pending_now_listed": listed_pending,
        # informational
        "n_new_free_series": len(added_free),
        "n_delisted_series": len(delisted),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        r = check()
    except SystemExit:
        raise
    except Exception as exc:                            # noqa: BLE001
        # A network failure is NOT a pass. Silently skipping is precisely the
        # failure mode this script exists to replace.
        print(f"FEE FIXTURE CHECK COULD NOT RUN: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(r, indent=1))
        return 1 if r["drift"] else 0

    print("Kalshi maker-fee fixture check")
    print("=" * 58)
    print(f"  fixture generated : {r.get('fixture_generated_at')}")
    print(f"  series            : {r['n_series']['fixture']} fixture / "
          f"{r['n_series']['live']} live "
          f"(+{r['n_new_free_series']} new free, -{r['n_delisted_series']} delisted)")
    print(f"  charging makers   : {r['n_charging']['fixture']} fixture / "
          f"{r['n_charging']['live']} live")
    for key, label in (
            ("started_charging", "NOW CHARGING (was free)"),
            ("stopped_charging", "NOW FREE (was charging)"),
            ("new_series_that_charge", "NEW series that charge"),
            ("unknown_fee_types", "UNKNOWN fee_type values"),
            ("pending_now_listed", "_PENDING_SERIES now listed — delete them")):
        if r.get(key):
            print(f"    {label}: {r[key]}")
    print()
    if r["drift"]:
        print("  *** DRIFT — every net-edge number computed since the fixture "
              "date is suspect. ***")
        print("  Fix: python3 -m scripts.generate_fee_fixture   (then commit)")
        return 1
    print("  OK — the committed fixture matches live Kalshi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
