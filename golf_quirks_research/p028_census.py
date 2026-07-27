"""
golf_quirks_research/p028_census.py
───────────────────────────────────
P-028 Phase 1, step 2: how much SETTLED data does each untested golf template
actually have, and what is its result distribution?

This runs before any rule reading or tie-rate estimation on purpose. A family
with no settled markets cannot be studied at all, and that is a finding — it is
also the cheapest possible screen.

`status=settled` is the FILTER; returned rows read `status="finalized"`.
`result="scalar"` is a PARTIAL PAYOUT (the $1/N dead-heat split), never a void,
and `settlement_value_dollars` carries the realised payout.

Run:  python3 -m golf_quirks_research.p028_census
"""
from __future__ import annotations

import collections
import json
import os
from typing import Any, Dict, List

from src.kalshi_public import KalshiPublic

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "p028_census.json")

# Grouped by the mechanic the prompt names, not by tour.
FAMILIES: Dict[str, List[str]] = {
    "category_leader_1overN": ["KXGOLFCAT", "KXPGACAT", "KXPGAPLAYERCAT",
                               "KXPGAWINNERWITHOUT"],
    "head_to_head_tie_zero": ["KXPGAH2H", "KXLIVH2H", "KXDPWTH2H", "KXGOLFH2H"],
    "margin": ["KXPGAWINMARGIN", "KXPGASTROKEMARGIN"],
    "round_stat_threshold": ["KXPGAROUNDBIRDIES", "KXPGAROUNDSCORE",
                             "KXPGAGOLFERSCORE", "KXPGALOWSCORE",
                             "KXPGAROUNDLOW", "KXPGABIRDIES"],
    "region_country": ["KXPGAREGION", "KXPGAWINNERREGION"],
    "misc_untested": ["KXPGARETURN", "KXGOLFNEXTWIN", "KXPGAUNDERPAR",
                      "KXPGAWINNINGSCORE", "KXPGACUTLINE", "KXPGAPLAYOFF",
                      "KXPGA1STTIMEWIN", "KXPGAAGECUT"],
    # Controls: already-tested families, to sanity-check the counting method
    # against numbers we know.
    "CONTROL_tested": ["KXPGAR1LEAD", "KXPGATOP10", "KXPGAMAKECUT"],
}


def settled(k: KalshiPublic, series: str, max_pages: int = 12) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {"series_ticker": series, "status": "settled",
                              "limit": 200}
    for _ in range(max_pages):
        d = k.get("/markets", params)
        if not d:
            break
        out.extend(d.get("markets", []))
        cur = d.get("cursor")
        if not cur:
            break
        params["cursor"] = cur
    return out


def main() -> None:
    k = KalshiPublic()
    report: Dict[str, Any] = {}
    for fam, series_list in FAMILIES.items():
        print(f"\n=== {fam} ===")
        for s in series_list:
            ms = settled(k, s)
            results = collections.Counter((m.get("result") or "?").lower()
                                          for m in ms)
            events = {m.get("event_ticker") for m in ms}
            closes = sorted(m.get("close_time") or "" for m in ms)
            scalars = [m for m in ms if (m.get("result") or "").lower() == "scalar"]
            report[s] = {
                "family": fam,
                "n_settled": len(ms),
                "n_events": len(events),
                "results": dict(results),
                "n_scalar": len(scalars),
                "first_close": closes[0][:10] if closes else None,
                "last_close": closes[-1][:10] if closes else None,
            }
            span = (f"{closes[0][:10]}..{closes[-1][:10]}" if closes else "—")
            print(f"  {s:22} settled={len(ms):5}  events={len(events):4}  "
                  f"scalar={len(scalars):4}  {span}  {dict(results)}")
    json.dump(report, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
