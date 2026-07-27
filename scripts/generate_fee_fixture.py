#!/usr/bin/env python3
"""
scripts/generate_fee_fixture.py
───────────────────────────────
Regenerate `src/fixtures/kalshi_series_fees.json` from Kalshi's own
`GET /series` — the source of truth for which series charge maker fees.

Why this exists
───────────────
`_SERIES_MAKER_FEE` was a hand-maintained dict and it drifted **four times**,
three of those in a single day's run. A wrong maker fee does not produce an
obviously broken number: it produces a *plausible* one, shifted by a fraction
of a cent, in exactly the band where these hypotheses live and die. Several
committed verdicts sit within a cent of their kill line.

Generation and consumption are deliberately separate. The runtime
(`src/kalshi_fees.py`) only ever READS the fixture; this script is the only
thing that writes it, and `scripts/check_fee_fixture.py` is the only thing
that compares it against live Kalshi.

    python3 -m scripts.generate_fee_fixture           # rewrite the fixture
    python3 -m scripts.generate_fee_fixture --diff    # show the diff, write nothing

`/series` returns the whole inventory in ONE call (12,199 series on
2026-07-28), so this is cheap enough to run on every drift check.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_public import KalshiPublic          # noqa: E402

FIXTURE = (Path(__file__).resolve().parent.parent
           / "src" / "fixtures" / "kalshi_series_fees.json")

MAKER_FEE_TYPE = "quadratic_with_maker_fees"


def fetch_series() -> List[Dict[str, Any]]:
    """Every series Kalshi lists, with its `fee_type` and `fee_multiplier`."""
    k = KalshiPublic()
    data = k.get("/series/", {"limit": 5})
    series = (data or {}).get("series") or []
    if not series:
        raise SystemExit("could not fetch /series — refusing to write an "
                         "empty fixture")
    return series


def build(series: List[Dict[str, Any]]) -> Dict[str, Any]:
    tickers = sorted({str(s.get("ticker") or "") for s in series} - {""})
    maker = sorted({str(s["ticker"]) for s in series
                    if s.get("fee_type") == MAKER_FEE_TYPE and s.get("ticker")})
    zero_mult = sorted({str(s["ticker"]) for s in series
                        if s.get("fee_multiplier") == 0 and s.get("ticker")})
    other = sorted({str(s.get("fee_type")) for s in series} - {"quadratic",
                                                               MAKER_FEE_TYPE})
    return {
        "_comment": (
            "GENERATED — do not hand-edit. Rewrite with "
            "`python3 -m scripts.generate_fee_fixture`. Drift against live "
            "Kalshi is detected by `python3 -m scripts.check_fee_fixture`."),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "GET https://api.elections.kalshi.com/trade-api/v2/series/",
        "n_series": len(tickers),
        # Series charging the 0.0175 maker rate. Everything else in
        # `all_series` is `quadratic` -> maker fee ZERO.
        "maker_fee_series": maker,
        # fee_multiplier == 0: no fee at all, taker included.
        "zero_fee_multiplier_series": zero_mult,
        # Kept in full so the runtime can tell "known and maker-free" from
        # "never seen, be conservative". Without it a series listed AFTER the
        # fixture was generated that DOES charge would be billed as free —
        # the one direction that understates cost.
        "all_series": tickers,
        # Empty today; a new value here means Kalshi invented a fee regime
        # this model does not know about.
        "unknown_fee_types": other,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true",
                    help="print the diff against the committed fixture and "
                         "write nothing")
    args = ap.parse_args()

    fresh = build(fetch_series())
    if FIXTURE.exists():
        old = json.loads(FIXTURE.read_text())
        added = sorted(set(fresh["maker_fee_series"]) - set(old["maker_fee_series"]))
        removed = sorted(set(old["maker_fee_series"]) - set(fresh["maker_fee_series"]))
        new_series = len(set(fresh["all_series"]) - set(old["all_series"]))
        gone = len(set(old["all_series"]) - set(fresh["all_series"]))
        print(f"maker-fee series: {len(old['maker_fee_series'])} -> "
              f"{len(fresh['maker_fee_series'])}")
        if added:
            print(f"  NOW CHARGING ({len(added)}): {added}")
        if removed:
            print(f"  NO LONGER CHARGING ({len(removed)}): {removed}")
        print(f"inventory: {old['n_series']} -> {fresh['n_series']} "
              f"(+{new_series} new, -{gone} delisted)")
        if fresh["unknown_fee_types"]:
            print(f"  UNKNOWN fee_type values: {fresh['unknown_fee_types']}")
    if args.diff:
        return 0

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fresh, indent=1) + "\n")
    print(f"wrote {FIXTURE} ({fresh['n_series']} series, "
          f"{len(fresh['maker_fee_series'])} charging makers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
