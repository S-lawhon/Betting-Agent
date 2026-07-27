"""
golf_quirks_research/widen_probe.py
───────────────────────────────────
P-022 widening, step 2: are there qualifying tournaments BEFORE the study's
start, and are their trade prints still retrievable?

The pre-declaration defines the window as
    [measured per-series horizon, original study start)
and the original study starts 2026-05-18. So this asks two questions in order:

  1. Does Kalshi still LIST settled round-leader events closing before 05-18?
  2. For those it lists, does `/markets/{t}/trades` still RETURN prints?

(2) is the one that matters. A settled market whose tick history has rolled off
cannot enter a fill replay at all — the replay is driven by executed prints.

Run:  python3 -m golf_quirks_research.widen_probe
"""
from __future__ import annotations

import collections
import json
import os
from typing import Any, Dict, List

from src.kalshi_public import KalshiPublic

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "widen_probe.json")

SERIES = ("KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
          "KXLIVR1LEAD", "KXLIVR2LEAD", "KXLIVR3LEAD",
          "KXLPGAR1LEAD", "KXLPGAR2LEAD", "KXLPGAR3LEAD",
          "KXDPWORLDTOURR1LEAD", "KXDPWORLDTOURR2LEAD", "KXDPWORLDTOURR3LEAD",
          "KXCHAMPTOURR1LEAD")

STUDY_START = "2026-05-18"          # earliest close in the cached universe


def settled(k: KalshiPublic, series: str) -> List[Dict[str, Any]]:
    out, params = [], {"series_ticker": series, "status": "settled", "limit": 200}
    for _ in range(12):
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
    by_event: Dict[str, Dict[str, Any]] = {}
    for s in SERIES:
        for m in settled(k, s):
            ev = m.get("event_ticker") or ""
            ct = m.get("close_time") or ""
            if not ev or not ct:
                continue
            e = by_event.setdefault(ev, {"series": s, "close": ct, "tickers": [],
                                         "vol": 0.0})
            e["close"] = min(e["close"], ct)
            e["tickers"].append(m["ticker"])
            try:
                e["vol"] += float(m.get("volume_fp") or 0)
            except (TypeError, ValueError):
                pass

    older = {e: v for e, v in by_event.items() if v["close"][:10] < STUDY_START}
    print(f"settled leader events listed by Kalshi : {len(by_event)}")
    print(f"  ...closing BEFORE the study start ({STUDY_START}): {len(older)}")

    if not older:
        print("\n  Kalshi lists NO settled round-leader event older than the "
              "study's own start. The backward window is empty at the source.")

    tour = collections.Counter(e.split("-", 1)[1] if "-" in e else e
                               for e in older)
    for e, v in sorted(older.items(), key=lambda kv: kv[1]["close"]):
        print(f"    {e:34} close={v['close'][:10]} markets={len(v['tickers'])} "
              f"vol={v['vol']:,.0f}")

    # The decisive test: are prints still retrievable at/near the study's edge?
    print("\n── retrievability probe: oldest cached tournaments ──")
    print("   (a settled market whose ticks have rolled off cannot enter a "
          "fill replay)")
    probe_targets = []
    for e, v in sorted(by_event.items(), key=lambda kv: kv[1]["close"])[:14]:
        probe_targets.append((e, v))
    results = []
    for e, v in probe_targets:
        got = 0
        checked = 0
        for t in v["tickers"][:6]:
            checked += 1
            tr = k.trades_since(t, 0.0, max_pages=1)
            if tr:
                got += 1
        results.append({"event": e, "close": v["close"][:10],
                        "markets_probed": checked, "with_prints": got})
        flag = "OK" if got else "**NO PRINTS**"
        print(f"    {e:34} close={v['close'][:10]}  "
              f"{got}/{checked} markets return prints   {flag}")

    json.dump({"n_events": len(by_event), "older_than_study": list(older),
               "retrievability": results}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
