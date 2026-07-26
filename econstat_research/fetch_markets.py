"""P-027 step 2 — pull ALL markets (settled + open) for the candidate ECONSTAT
series, cache to disk, and dump close/expiration/settlement fields.

Read-only public endpoints.  Cache-first: every series is written to
econstat_research/data/markets/<TICKER>.json and never refetched.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from src.kalshi_public import KalshiPublic  # noqa: E402

DATA = os.path.join(HERE, "data")
MKT = os.path.join(DATA, "markets")
os.makedirs(MKT, exist_ok=True)


def fetch_series_markets(kp: KalshiPublic, series: str,
                         max_pages: int = 30) -> list:
    cache = os.path.join(MKT, f"{series}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    out, params = [], {"series_ticker": series, "limit": 200}
    for _ in range(max_pages):
        data = kp.get("/markets", params)
        if not data:
            break
        rows = data.get("markets") or []
        out.extend(rows)
        cur = data.get("cursor")
        if not cur or not rows:
            break
        params["cursor"] = cur
    json.dump(out, open(cache, "w"), indent=1)
    return out


def main(tickers):
    kp = KalshiPublic(min_interval=0.6)
    for t in tickers:
        rows = fetch_series_markets(kp, t)
        settled = [m for m in rows if m.get("status") == "settled"]
        print(f"{t:<18} total={len(rows):<5} settled={len(settled)}")


if __name__ == "__main__":
    main(sys.argv[1:])
