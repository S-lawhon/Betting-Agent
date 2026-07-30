#!/usr/bin/env python3
"""What does `min_top_size` actually select for?

The size screen (`297ce2b`) tests `top_ask_qty`. A resting sell-YES quote fills
only when a YES-taker print goes strictly THROUGH the quote price
(`_check_fills`), so the fill-relevant quantity is the TOTAL size at every price
strictly below the quote — `size_ahead_of_quote` in the census, not
`top_ask_qty`.

This prints the partition of the in-band population by both quantities, so the
claim in `research/REPORT_P022_Size_Screen_Section8_2026-07-30.md` §3 is
reproducible rather than quoted.

    python3 golf_quirks_research/screen_vs_size_ahead.py [--threshold 100]

Input is the census already committed at
`golf_quirks_research/live_book_census_aigwo26.json` (146 markets, AIGWO26 R1,
captured 2026-07-28 by `live_book_census.py`). No network, no API budget.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS = os.path.join(HERE, "live_book_census_aigwo26.json")


def passes(row: dict, threshold: float) -> bool:
    """Mirror of the pod's screen: the ask on a one-sided book, BOTH sides on a
    two-sided one. Missing quantities count as thin, same as the pod."""
    ask_q = row.get("top_ask_qty") or 0.0
    bid_q = row.get("top_bid_qty") or 0.0
    if row.get("class") == "one_sided_ask":
        return ask_q >= threshold
    return min(ask_q, bid_q) >= threshold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=100.0,
                    help="min_top_size to evaluate (pod default/config: 100)")
    ap.add_argument("--census", default=CENSUS)
    args = ap.parse_args()

    with open(args.census) as fh:
        rows = json.load(fh)
    band = [r for r in rows if r.get("in_band")]

    print(f"census: {len(rows)} markets, {len(band)} in band")
    print(f"classes in band: {dict(Counter(r['class'] for r in band))}")
    print(f"screen threshold: {args.threshold:g}\n")

    kept = [r for r in band if passes(r, args.threshold)]
    cut = [r for r in band if not passes(r, args.threshold)]

    hdr = f"{'ticker':38} {'class':15} {'top_ask':>8} {'ahead':>8} {'kept':>5}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(band, key=lambda r: -(r.get("top_ask_qty") or 0.0)):
        print(f"{r['ticker'][-38:]:38} {r['class']:15} "
              f"{(r.get('top_ask_qty') or 0):8.0f} "
              f"{r.get('size_ahead_of_quote', 0):8.0f} "
              f"{('yes' if passes(r, args.threshold) else 'no'):>5}")

    def spread(rs: list) -> str:
        if not rs:
            return "n/a"
        v = sorted(r.get("size_ahead_of_quote", 0) for r in rs)
        return (f"n={len(v)} min={v[0]:.0f} median={st.median(v):.0f} "
                f"max={v[-1]:.0f}")

    print(f"\nsize_ahead_of_quote, KEPT  : {spread(kept)}")
    print(f"size_ahead_of_quote, CUT   : {spread(cut)}")

    if kept and cut:
        lo_kept = min(r.get("size_ahead_of_quote", 0) for r in kept)
        hi_cut = max(r.get("size_ahead_of_quote", 0) for r in cut)
        if lo_kept > hi_cut:
            print(f"\nDISJOINT: every kept book has more size ahead ({lo_kept:.0f}+) "
                  f"than every cut book ({hi_cut:.0f}-).")
            print(f"Ratio of the two modes: {lo_kept / max(hi_cut, 1e-9):.0f}x.")
            print("The screen is not trimming a continuum; it keeps only the far mode,")
            print("which is the mode a resting quote is hardest to get filled in.")
        else:
            print("\nOverlapping: the two quantities do not partition cleanly here.")

    # How divergent are the two measures on the cut books? This is the reason
    # a screen on top-of-book size is not a screen on fill difficulty.
    div = [(r.get("size_ahead_of_quote", 0), r.get("top_ask_qty") or 0.0)
           for r in cut if (r.get("top_ask_qty") or 0.0) > 0]
    if div:
        ratios = [a / t for a, t in div]
        print(f"\nOn CUT books, size_ahead / top_ask_qty: "
              f"median {st.median(ratios):.1f}x (n={len(ratios)}) — top-of-book "
              f"understates what must be swept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
