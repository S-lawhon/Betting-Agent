"""
satellites_research/pull_rt_markets.py
──────────────────────────────────────
Study B — Rotten Tomatoes family census.  READ-ONLY.

Pulls every market for every RT / RTTV / RTCOMPARISON series (and METACRITIC as
a control family), any status, cached per series.

What we are looking for:
  * events where EVERY strike settled No  -> the "no data a week later" fallback
    fired and the ladder was not a partition
  * events where exactly one strike settled Yes -> ladder behaved as a partition
  * the strike ladder shape (above/below/between) per event
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MKTS = os.path.join(DATA, "rt_markets")
os.makedirs(MKTS, exist_ok=True)

TEMPLATES = ["RT", "RTTV", "RTCOMPARISON", "METACRITIC"]


def all_markets(k: KalshiPublic, series: str) -> list[dict]:
    out, params = [], {"series_ticker": series, "limit": 200}
    for _ in range(20):
        d = k.get("/markets", params)
        if not d:
            break
        out.extend(d.get("markets") or [])
        cur = d.get("cursor")
        if not cur:
            break
        params["cursor"] = cur
    return out


def main() -> None:
    templates = json.load(open(os.path.join(DATA, "templates.json")))
    k = KalshiPublic(min_interval=0.7)
    for tpl in TEMPLATES:
        for s in templates.get(tpl, {}).get("tickers", []):
            p = os.path.join(MKTS, f"{tpl}__{s}.json")
            if os.path.exists(p):
                continue
            m = all_markets(k, s)
            with open(p, "w") as fh:
                json.dump(m, fh)
            print(f"  {tpl}/{s}: {len(m)}", flush=True)


if __name__ == "__main__":
    main()
