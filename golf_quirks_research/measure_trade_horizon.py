"""
golf_quirks_research/measure_trade_horizon.py
─────────────────────────────────────────────
Task 1 of the P-022 widening: **measure** how far back Kalshi's trade history
actually reaches, per series — do not trust "~1 month" and do not trust
"2026-05-20" either. Both are claims about an API.

Method: for each of the 13 round-leader series, walk the settled events oldest
first and ask `/markets/{ticker}/trades` for the earliest print it will return.
The earliest print that comes back across a series IS that series' horizon.

Run:  python3 -m golf_quirks_research.measure_trade_horizon
"""
from __future__ import annotations

import collections
import json
import os
from datetime import datetime, timezone

from src.kalshi_public import KalshiPublic

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "data", "settled_meta.jsonl")
OUT = os.path.join(HERE, "trade_horizon.json")

SERIES = (
    "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
    "KXLIVR1LEAD", "KXLIVR2LEAD", "KXLIVR3LEAD",
    "KXLPGAR1LEAD", "KXLPGAR2LEAD", "KXLPGAR3LEAD",
    "KXDPWORLDTOURR1LEAD", "KXDPWORLDTOURR2LEAD", "KXDPWORLDTOURR3LEAD",
    "KXCHAMPTOURR1LEAD",
)


def main() -> None:
    # Oldest-first candidate tickers per series, from the settled cache.
    by_series = collections.defaultdict(list)
    with open(META) as fh:
        for line in fh:
            r = json.loads(line)
            s = r.get("series", "")
            if s in SERIES and r.get("close_time") and float(r.get("volume_fp") or 0) > 0:
                by_series[s].append((r["close_time"], r["ticker"]))

    k = KalshiPublic()
    out = {}
    for s in SERIES:
        cands = sorted(by_series.get(s, []))
        earliest = None
        probed = 0
        # Walk oldest-first; the first ticker that returns any print gives the
        # horizon. Bounded so one dead series cannot burn the API budget.
        for close_iso, ticker in cands[:40]:
            probed += 1
            trades = k.trades_since(ticker, 0.0, max_pages=1)
            if not trades:
                continue
            ep = min(t["epoch"] for t in trades)
            iso = datetime.fromtimestamp(ep, timezone.utc).isoformat()
            if earliest is None or iso < earliest:
                earliest = iso
            break
        out[s] = {
            "earliest_print_utc": earliest,
            "n_settled_markets_cached": len(cands),
            "oldest_cached_close": cands[0][0] if cands else None,
            "tickers_probed": probed,
        }
        print(f"  {s:22} earliest_print={earliest}  "
              f"(cached settled markets: {len(cands)}, probed {probed})")

    json.dump(out, open(OUT, "w"), indent=1)
    found = [v["earliest_print_utc"] for v in out.values() if v["earliest_print_utc"]]
    print(f"\nseries with retrievable history: {len(found)}/{len(SERIES)}")
    if found:
        print(f"overall earliest print: {min(found)}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
