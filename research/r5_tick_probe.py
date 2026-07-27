"""
research/r5_tick_probe.py
─────────────────────────
R5 follow-up item 1: settle the tick-size question empirically.

Hunt E reported non-1c tick regimes ("linear_cent 77.3%, tapered_deci_cent
19.9%, deci_cent 2.8%") and could not reproduce them. Those strings appear
NOWHERE in the public payloads: `/series` (12,199 rows) has no tick field at
all, and `tick_size` is absent from `/markets` in both list and singular form —
the "None for all 200" in the hunt was `.get()` on a key that does not exist,
not a null value.

So the question is answered empirically instead: read real order books and
measure the actual price granularity, and where in the price range it occurs.

Run:  python3 -m research.r5_tick_probe
"""
from __future__ import annotations

import collections
import json
import os
from decimal import Decimal
from typing import Any, Dict, List

from src.kalshi_public import KalshiPublic

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "r5_tick_probe.json")

SERIES = [
    # deep-tail props (where P-017 / P-022 live)
    "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGATOP10", "KXPGATOP20",
    # liquid game markets
    "KXMLBGAME", "KXNFLGAME", "KXATPMATCH", "KXWTAMATCH", "KXMLBTOTAL",
    # non-sports
    "KXBTCD", "KXETHD", "KXFED", "KXCPI", "KXHIGHNY",
]


def decimals(price: str) -> int:
    d = Decimal(price).normalize()
    return max(0, -d.as_tuple().exponent)


def main() -> None:
    k = KalshiPublic()
    per_series: Dict[str, Dict[str, Any]] = {}
    subcent_rows: List[Dict[str, Any]] = []

    for s in SERIES:
        try:
            ms = k.open_markets(s, max_pages=2)
        except Exception:                                   # noqa: BLE001
            continue
        if not ms:
            continue
        gran = collections.Counter()
        n_books = 0
        n_subcent_books = 0
        for m in ms[:40]:
            ob = k.get(f"/markets/{m['ticker']}/orderbook")
            # The payload is {"orderbook_fp": {...}} at top level on this
            # endpoint — there is no "orderbook" wrapper. Assuming one silently
            # yields zero levels for every market, which is how the first run
            # of this probe reported "no sub-cent ticks anywhere".
            root = ob or {}
            fp = (root.get("orderbook_fp")
                  or (root.get("orderbook") or {}).get("orderbook_fp") or {})
            levels = (fp.get("yes_dollars") or []) + (fp.get("no_dollars") or [])
            if not levels:
                continue
            n_books += 1
            book_sub = False
            for price, size in levels:
                d = decimals(str(price))
                gran[d] += 1
                if d > 2:
                    book_sub = True
                    subcent_rows.append({
                        "series": s, "ticker": m["ticker"],
                        "price": str(price), "size": str(size),
                    })
            n_subcent_books += int(book_sub)
        per_series[s] = {
            "books_read": n_books,
            "books_with_subcent_level": n_subcent_books,
            "levels_by_decimals": dict(sorted(gran.items())),
        }
        print(f"  {s:14} books={n_books:3}  with sub-cent level={n_subcent_books:3}  "
              f"decimals={dict(sorted(gran.items()))}")

    # Where in the price range do sub-cent levels sit?
    bands = collections.Counter()
    size_at_subcent = 0.0
    for r in subcent_rows:
        p = float(r["price"])
        band = ("<=0.02" if p <= 0.02 else
                "0.02-0.05" if p <= 0.05 else
                "0.05-0.12" if p <= 0.12 else
                "0.12-0.90" if p <= 0.90 else ">0.90")
        bands[band] += 1
        size_at_subcent += float(r["size"])
    print(f"\nsub-cent levels found: {len(subcent_rows)}")
    print(f"  by price band: {dict(bands)}")
    print(f"  total contracts resting at sub-cent prices: {size_at_subcent:,.0f}")
    if subcent_rows:
        print("  examples:")
        for r in subcent_rows[:8]:
            print(f"    {r['ticker']:34} {r['price']:>8} x {r['size']}")

    json.dump({"per_series": per_series, "subcent": subcent_rows,
               "bands": dict(bands)}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
