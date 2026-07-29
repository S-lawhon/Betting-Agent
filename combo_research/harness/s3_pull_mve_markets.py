"""S3 -- Pull the entire instantiated KXMVE* market population (STREAMING).

/markets takes no ticker prefix, so we iterate the 12 KXMVE* series found in
GET /series.  NO status filter is passed -- one bare sweep gets everything.
(`status=settled` is the valid FILTER; rows come back reading `finalized`;
passing `status=finalized` is a 400 "invalid status filter".)

The cross-category series alone is >600k markets, so rows are streamed to
gzipped JSONL (one compact record per line) instead of being held in RAM --
an earlier list-in-memory version hit 3.2 GB.

Output: cache/mve_markets_<SERIES>.jsonl.gz  (+ a .done sentinel so reruns
skip finished series).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from collections import Counter

from kalshi_api import Kalshi, STATUS_LOG, CACHE, save

MVE_SERIES = [
    "KXMVECBCHAMPIONSHIP", "KXMVECROSSCATEGORY", "KXMVEGRAMMYS",
    "KXMVEMENTIONSINGLE", "KXMVEMENTIONSSINGLE", "KXMVENBAMULTIGAMEEXTENDED",
    "KXMVENBASINGLEGAME", "KXMVENFLMULTIGAME", "KXMVENFLMULTIGAMEEXTENDED",
    "KXMVENFLSINGLEGAME", "KXMVEOSCARS", "KXMVESPORTSMULTIGAMEEXTENDED",
]

# compact keys keep the gz small; expanded by s4/s5 loaders
KEEP = {
    "ticker": "t", "event_ticker": "ev", "mve_collection_ticker": "col",
    "status": "st", "result": "res", "title": "ti",
    "yes_bid_dollars": "yb", "yes_ask_dollars": "ya",
    "yes_bid_size_fp": "ybs", "yes_ask_size_fp": "yas",
    "last_price_dollars": "lp", "volume_fp": "vol",
    "open_interest_fp": "oi", "liquidity_dollars": "liq",
    "settlement_value_dollars": "sv", "notional_value_dollars": "nv",
    "open_time": "ot", "close_time": "ct", "settlement_ts": "sts",
    "created_time": "crt", "price_level_structure": "pls",
}


def slim(m: dict) -> dict:
    d = {v: m.get(k) for k, v in KEEP.items()}
    d["lg"] = [[l.get("market_ticker"), l.get("side")]
               for l in (m.get("mve_selected_legs") or [])]
    return d


def pull_series(k: Kalshi, series: str, force=False):
    path = os.path.join(CACHE, f"mve_markets_{series}.jsonl.gz")
    done = path + ".done"
    if os.path.exists(done) and not force:
        print(f"  {series}: cached ({open(done).read().strip()})", flush=True)
        return
    params, page, n = {"series_ticker": series, "limit": 1000}, 0, 0
    t0 = time.time()
    stat = Counter()
    with gzip.open(path, "wt") as fh:
        while True:
            d = k.get("/markets", params)
            if d is None:
                print(f"  !! {series} aborted at page {page} (see status log)",
                      flush=True)
                break
            rows = d.get("markets") or []
            for m in rows:
                fh.write(json.dumps(slim(m), separators=(",", ":")) + "\n")
                stat[m.get("status")] += 1
            n += len(rows)
            page += 1
            if page % 100 == 0:
                print(f"    {series} page {page} n={n:,} "
                      f"({time.time()-t0:.0f}s)", flush=True)
            cur = d.get("cursor")
            if not cur or not rows:
                break
            params["cursor"] = cur
    with open(done, "w") as f:
        f.write(f"{n} markets, {page} pages, {dict(stat)}")
    print(f"  {series}: {n:,} markets / {page} pages "
          f"({time.time()-t0:.0f}s) {dict(stat)}", flush=True)


def main():
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    k = Kalshi()
    for s in (only or MVE_SERIES):
        pull_series(k, s, force)
    if STATUS_LOG:
        save("s3_status_log.json", STATUS_LOG)
        print(f"non-200s: {len(STATUS_LOG)}")


if __name__ == "__main__":
    main()
