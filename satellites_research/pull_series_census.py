"""
satellites_research/pull_series_census.py
─────────────────────────────────────────
Step 0 for all three satellite sub-studies.  READ-ONLY — cannot place orders.

Pulls the full Kalshi series catalogue (all categories), caches it, and groups
series by their *contract-terms template* (the basename of
`contract_terms_url`, e.g. GLOBES.pdf / RT.pdf / NFLWINS.pdf).  The template is
the unit that carries a settlement rule, so it is the unit of the census.

Kalshi is a shared ~2 req/s per-IP budget across concurrent agent sessions and
the live VPS services -> throttled, cached, resumable.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

CATEGORIES = [
    "Politics", "Sports", "Culture", "Crypto", "Climate", "Economics",
    "Companies", "Financials", "World", "Entertainment", "Science and Technology",
    "Health", "Transportation",
]


def cached(name, fn):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p) as fh:
            return json.load(fh)
    val = fn()
    with open(p, "w") as fh:
        json.dump(val, fh)
    return val


def main() -> None:
    k = KalshiPublic(min_interval=0.7)
    all_series: dict[str, dict] = {}
    for cat in CATEGORIES:
        data = cached(
            f"series_{cat.replace(' ', '_')}.json",
            lambda cat=cat: k.get("/series/", {"category": cat, "limit": 1000}) or {},
        )
        got = (data or {}).get("series") or []
        print(f"  {cat}: {len(got)}")
        for s in got:
            all_series[s["ticker"]] = s
    print(f"total distinct series: {len(all_series)}")

    by_template: dict[str, list[dict]] = defaultdict(list)
    for s in all_series.values():
        url = s.get("contract_terms_url") or ""
        tpl = url.rsplit("/", 1)[-1].replace(".pdf", "") if url else "(none)"
        by_template[tpl].append(s)

    with open(os.path.join(DATA, "all_series.json"), "w") as fh:
        json.dump(all_series, fh)
    summary = {
        tpl: {
            "n_series": len(v),
            "fee_types": sorted({x.get("fee_type", "") for x in v}),
            "categories": sorted({x.get("category", "") for x in v}),
            "tickers": sorted(x["ticker"] for x in v),
        }
        for tpl, v in by_template.items()
    }
    with open(os.path.join(DATA, "templates.json"), "w") as fh:
        json.dump(summary, fh, indent=1)

    print(f"distinct templates: {len(by_template)}")
    for tpl in sorted(by_template, key=lambda t: -len(by_template[t]))[:25]:
        print(f"  {tpl:32s} {len(by_template[tpl]):5d}")


if __name__ == "__main__":
    main()
