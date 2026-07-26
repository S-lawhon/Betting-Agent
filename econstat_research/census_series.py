"""P-027 step 1 — enumerate Kalshi series whose contract terms use the ECONSTAT template.

Caches raw /series pulls under econstat_research/data/.  Re-runnable; resumes
from cache.  Read-only public endpoints; polite throttle (KalshiPublic default
0.25s -> 4 req/s cap, we set 0.6s to stay well under the shared 2 req/s budget).
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from src.kalshi_public import KalshiPublic  # noqa: E402

DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

CATEGORIES = [
    "Economics", "Financials", "Politics", "World", "Companies",
    "Climate", "Science and Technology", "Health", "Transportation",
]


def fetch_all_series(kp: KalshiPublic) -> list:
    cache = os.path.join(DATA, "series_all.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    out = []
    seen = set()
    for cat in CATEGORIES:
        params = {"category": cat, "limit": 200}
        data = kp.get("/series/", params)
        if not data:
            print(f"  !! no data for category={cat}")
            continue
        rows = data.get("series") or []
        print(f"  {cat}: {len(rows)}")
        for r in rows:
            t = r.get("ticker")
            if t and t not in seen:
                seen.add(t)
                out.append(r)
    json.dump(out, open(cache, "w"), indent=1)
    return out


def main():
    kp = KalshiPublic(min_interval=0.6)
    series = fetch_all_series(kp)
    print(f"total series pulled: {len(series)}")

    econstat = [s for s in series
                if (s.get("contract_terms_url") or "").upper().endswith(
                    ("ECONSTAT.PDF", "ECONSTATTE.PDF"))]
    print(f"ECONSTAT-template series: {len(econstat)}")

    by_fee = {}
    for s in econstat:
        by_fee.setdefault(s.get("fee_type"), []).append(s["ticker"])
    for k, v in by_fee.items():
        print(f"  fee_type={k}: {len(v)}")

    json.dump(econstat, open(os.path.join(DATA, "econstat_series.json"), "w"),
              indent=1)
    for s in sorted(econstat, key=lambda x: x["ticker"]):
        print(f"{s['ticker']:<26} {s.get('fee_type','?'):<26} "
              f"{(s.get('title') or '')[:70]}")


if __name__ == "__main__":
    main()
