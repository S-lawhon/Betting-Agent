"""
crossvenue_research/build_pairs.py
──────────────────────────────────
P-020 Phase 1, stage 2: turn the harvested universes into matched
Kalshi <-> Polymarket settled pairs.

Stage A  Poly event  -> Kalshi candidate series (Kalshi /v1/search/series)
Stage B  candidate series -> settled Kalshi markets in the window
Stage C  market-level match + match-confidence + settlement-definition checks

Everything is cached under crossvenue_research/data/.  READ-ONLY on both APIs.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crossvenue_research.harvest import (  # noqa: E402
    DATA, WIN_END, WIN_START, cache, kget, _session, _throttle, _k_last,
    K_INTERVAL,
)

KSEARCH = "https://api.elections.kalshi.com/v1/search/series"

POLY_EVENT_MIN_VOL = 25_000.0   # thesis requires Poly to be the deep venue


def ksearch(query: str) -> List[dict]:
    for attempt in range(4):
        _throttle(_k_last, K_INTERVAL)
        try:
            r = _session.get(KSEARCH, params={"query": query, "limit": 20},
                             timeout=25)
        except Exception:
            continue
        if r.status_code == 200:
            try:
                return r.json().get("current_page", []) or []
            except ValueError:
                return []
        if r.status_code == 429:
            import time
            time.sleep(8 * (attempt + 1))
            continue
        return []
    return []


# ── Stage A ──────────────────────────────────────────────────────────

def poly_events() -> Dict[str, dict]:
    poly = json.load(open(os.path.join(DATA, "poly_closed.json")))
    ev: Dict[str, dict] = {}
    for m in poly:
        e = (m.get("events") or [{}])[0]
        slug = e.get("slug") or m.get("slug")
        d = ev.setdefault(slug, {"slug": slug, "title": e.get("title") or m.get("question"),
                                 "markets": [], "vol": 0.0})
        d["markets"].append(m)
        d["vol"] += float(m.get("volumeNum") or 0.0)
    return ev


def stage_a() -> Dict[str, List[str]]:
    ev = poly_events()
    big = {k: v for k, v in ev.items() if v["vol"] >= POLY_EVENT_MIN_VOL}
    out: Dict[str, List[str]] = {}
    for i, (slug, v) in enumerate(sorted(big.items(), key=lambda kv: -kv[1]["vol"])):
        q = re.sub(r"[^\w\s]", " ", v["title"] or "")[:80].strip()
        hits = ksearch(q)
        out[slug] = sorted({h.get("series_ticker") for h in hits
                            if h.get("series_ticker")})
        sys.stderr.write(f"\r  stage A {i+1}/{len(big)}  cand={sum(len(x) for x in out.values())}   ")
        sys.stderr.flush()
    sys.stderr.write("\n")
    return out


# ── Stage B ──────────────────────────────────────────────────────────

def stage_b(series: List[str]) -> List[dict]:
    mn = int(WIN_START.timestamp())
    mx = int(WIN_END.timestamp())
    out: List[dict] = []
    for i, st in enumerate(series):
        cursor = None
        for _ in range(6):
            params = {"series_ticker": st, "status": "settled",
                      "min_close_ts": mn, "max_close_ts": mx, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            d = kget("/markets", params)
            if not d:
                break
            for m in d.get("markets", []):
                m["_series"] = st
                out.append(m)
            cursor = d.get("cursor")
            if not cursor:
                break
        sys.stderr.write(f"\r  stage B {i+1}/{len(series)} markets={len(out)}   ")
        sys.stderr.flush()
    sys.stderr.write("\n")
    return out


if __name__ == "__main__":
    print("A. kalshi series candidates per poly event ...")
    cand = cache("stage_a_candidates.json", stage_a)
    allser = sorted({s for v in cand.values() for s in v})
    print(f"   {len(cand)} poly events -> {len(allser)} candidate kalshi series")

    print("B. settled kalshi markets for candidate series ...")
    km = cache("kalshi_settled.json", lambda: stage_b(allser))
    print(f"   {len(km)} settled kalshi markets in window")
