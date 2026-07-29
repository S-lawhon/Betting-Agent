"""S1 -- Enumerate ALL multivariate event collections, fully paginated.

GET /multivariate_event_collections
Response key is `multivariate_contracts` (NOT `collections`).
Each row already carries `associated_events` inline, so the detail GET in S2
is only needed to check for extra fields.

Writes cache/collections_raw.json (full, incl. associated_events) and
cache/collections_slim.json (metadata + counts, no event lists).
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from kalshi_api import Kalshi, STATUS_LOG, save, load

FORCE = "--force" in sys.argv


def main():
    k = Kalshi()
    rows = None if FORCE else load("collections_raw.json")
    if rows is None:
        print("Paginating /multivariate_event_collections ...", flush=True)
        rows = k.paginate("/multivariate_event_collections",
                          {"limit": 100}, "multivariate_contracts",
                          progress_every=5)
        save("collections_raw.json", rows)
    print(f"collections: {len(rows)}")

    # dedupe check
    tickers = [r.get("collection_ticker") for r in rows]
    print(f"unique collection_tickers: {len(set(tickers))}")

    slim = []
    for r in rows:
        ae = r.get("associated_events") or []
        slim.append({
            "collection_ticker": r.get("collection_ticker"),
            "series_ticker": r.get("series_ticker"),
            "title": r.get("title"),
            "description": r.get("description"),
            "functional_description": r.get("functional_description"),
            "is_ordered": r.get("is_ordered"),
            "is_single_market_per_event": r.get("is_single_market_per_event"),
            "is_all_yes": r.get("is_all_yes"),
            "size_min": r.get("size_min"),
            "size_max": r.get("size_max"),
            "open_date": r.get("open_date"),
            "close_date": r.get("close_date"),
            "n_associated_events": len(ae),
            "n_associated_event_tickers": len(r.get("associated_event_tickers") or []),
        })
    save("collections_slim.json", slim)

    # field key census across all rows
    keys = Counter()
    for r in rows:
        keys.update(r.keys())
    print("\n== row-level keys (count/total) ==")
    for kk, v in keys.most_common():
        print(f"  {kk}: {v}/{len(rows)}")

    print("\n== distribution by series_ticker ==")
    c = Counter(r.get("series_ticker") for r in rows)
    for s, n in c.most_common():
        print(f"  {s}: {n}")

    print("\n== flags ==")
    for f in ("is_ordered", "is_single_market_per_event", "is_all_yes"):
        print(f"  {f}: {dict(Counter(r.get(f) for r in rows))}")
    print(f"  size_min: {dict(Counter(r.get('size_min') for r in rows))}")
    print(f"  size_max: {dict(Counter(r.get('size_max') for r in rows))}")

    if STATUS_LOG:
        save("s1_status_log.json", STATUS_LOG)
        print(f"\nnon-200s: {len(STATUS_LOG)}")


if __name__ == "__main__":
    main()
