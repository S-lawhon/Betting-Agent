"""
leader_research/settled_leader_check.py
───────────────────────────────────────
P-026 Step 3, run EARLY.  READ-ONLY.

The brief assumed the first-ever KXLEADER settlements land ~2026-10-15 with the
MLB season-leader events.  That is not quite true: several LEAGUELEADER-template
series cover seasons that have ALREADY ended as of 2026-07-26 (the 2025 NFL
season, the 2025-26 NBA/UCL seasons, the 2026 World Cup).  If any of those
settled with a tie, the $1/n split rule is confirmed or falsified TODAY at zero
cost, and the October wait is unnecessary.

For every LEAGUELEADER-template series this walks /markets?status=settled and
records `result` and `settlement_value_dollars`.

    result = "yes"     -> outright leader, $1.00
    result = "no"      -> $0.00
    result = "scalar"  -> DEAD-HEAT SPLIT, settlement_value_dollars = $1/n
                          rounded down.  This is NOT a void.  (P-022 settler
                          fix: KalshiGolfSettler treated scalar as
                          kalshi_withdrawn -- wrong for this family.)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
TERMS = "https://assets.kalshi.com/contract_terms/LEAGUELEADER.pdf"


def settled_markets(k: KalshiPublic, series: str, max_pages: int = 6):
    out, params = [], {"series_ticker": series, "status": "settled",
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


def main():
    k = KalshiPublic(min_interval=0.8)
    series_all = json.load(open(os.path.join(DATA, "series_sports.json")))
    targets = [s["ticker"] for s in series_all
               if s.get("contract_terms_url") == TERMS]
    print(f"{len(targets)} LEAGUELEADER-template series")

    all_rows = {}
    for s in sorted(targets):
        p = os.path.join(DATA, f"settled_{s}.json")
        if os.path.exists(p):
            mk = json.load(open(p))
        else:
            mk = settled_markets(k, s)
            json.dump(mk, open(p, "w"), indent=1)
        if not mk:
            continue
        res = {}
        for m in mk:
            res.setdefault(m.get("result") or "(empty)", []).append(m)
        all_rows[s] = mk
        summary = " ".join(f"{a}={len(b)}" for a, b in sorted(res.items()))
        print(f"{s:24s} settled={len(mk):4d}  {summary}")
        for m in mk:
            if (m.get("result") or "").lower() not in ("no", ""):
                print("     ", m.get("ticker"), m.get("result"),
                      m.get("settlement_value_dollars"),
                      m.get("yes_sub_title"))
    json.dump({s: len(v) for s, v in all_rows.items()},
              open(os.path.join(DATA, "settled_index.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
