"""
satellites_research/pull_award_markets.py
─────────────────────────────────────────
Study A — award tie-regime census.  READ-ONLY.

For every series under an award contract-terms template, pull ALL markets
(any status) and cache them per series.  The census questions are:
  * does the event actually LIST a "tie" strike?  (the rule names a tie strike;
    if none is listed a tie pays nobody at all)
  * what did tie strikes settle at, and what did they trade at?
  * what is the sum of YES prices across an event's strikes?

Cached per series -> re-runs are free and an interrupted run resumes.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MKTS = os.path.join(DATA, "award_markets")
os.makedirs(MKTS, exist_ok=True)

# Regime groups, established by reading the certified terms verbatim
# (see satellites_research/contract_terms/*.txt).
GROUP1 = ["GLOBES", "CRITICS", "AMAS", "TONYS", "BILLBOARDAWARDS"]        # tie strike Yes, shared winners No
GROUP2 = ["AWARDS", "SAGAWARDS", "BETAWARDS", "AWARDSCMA", "LATINGRAMMY",
          "LATINGRAMMYS", "IHEARTRADIOMUSICAWARDS", "PRODUCERSGUILDAWARDS",
          "FILMINDEPENDENTSPIRITAWARDS"]                                   # unified AWARDS rulebook, same effect
GROUP3 = ["OSCARS", "EMMYS", "GRAMMY", "ACMA", "BAFTA", "GAMEAWARDS"]      # silent on ties -> discretion


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
    for group in (GROUP1, GROUP2, GROUP3):
        for tpl in group:
            for s in templates.get(tpl, {}).get("tickers", []):
                p = os.path.join(MKTS, f"{tpl}__{s}.json")
                if os.path.exists(p):
                    continue
                m = all_markets(k, s)
                with open(p, "w") as fh:
                    json.dump(m, fh)
                print(f"  {tpl}/{s}: {len(m)}")


if __name__ == "__main__":
    main()
