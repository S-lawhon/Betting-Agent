"""S7 -- Exchange-wide open-market census.

Tests the prior session's claim that ~362,671 of ~425,682 OPEN markets are
KXMVE* with only 0.1% two-sided.  Keeps only what the claim needs (series
prefix + quote presence + top-of-book notional) so the whole exchange fits
in RAM.

`status=open` is passed as the FILTER; returned rows read `active` or `open`
-- both are counted, and the raw distribution of returned status strings is
printed so the mapping is visible rather than assumed.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter

from kalshi_api import Kalshi, STATUS_LOG, fnum, save

SERIES_RE = re.compile(r"^([A-Z0-9]+?)-")


def main():
    k = Kalshi()
    params = {"status": "open", "limit": 1000}
    n = 0
    page = 0
    stat = Counter()
    pref = Counter()
    mve_quote = Counter()
    all_quote = Counter()
    mve_top = []
    t0 = time.time()
    while True:
        d = k.get("/markets", params)
        if d is None:
            print(f"!! aborted at page {page}")
            break
        rows = d.get("markets") or []
        for m in rows:
            n += 1
            stat[m.get("status")] += 1
            t = m.get("ticker") or ""
            mm = SERIES_RE.match(t)
            p = mm.group(1) if mm else t
            is_mve = p.startswith("KXMVE")
            pref[p if is_mve else ("_other:" + p[:14])] += 1
            b, a = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
            bs = fnum(m.get("yes_bid_size_fp")) or 0.0
            asz = fnum(m.get("yes_ask_size_fp")) or 0.0
            hb = b is not None and b > 0
            ha = a is not None and a < 1.0
            key = ("bid" if hb else "") + ("ask" if ha else "") or "none"
            (mve_quote if is_mve else all_quote)[key] += 1
            if is_mve and hb and ha:
                mve_top.append(min(bs * b, asz * (1 - a)))
        page += 1
        if page % 100 == 0:
            print(f"  page {page} n={n:,} ({time.time()-t0:.0f}s)", flush=True)
        cur = d.get("cursor")
        if not cur or not rows:
            break
        params["cursor"] = cur

    print(f"\nTOTAL OPEN markets exchange-wide: {n:,} ({page} pages)")
    print("returned status values:", dict(stat))
    mve_n = sum(v for kk, v in pref.items() if kk.startswith("KXMVE"))
    print(f"KXMVE* open markets: {mve_n:,} ({100*mve_n/max(n,1):.1f}% of open)")
    print("\nKXMVE by series:",
          {kk: v for kk, v in pref.most_common() if kk.startswith("KXMVE")})
    print("\nTop 25 non-MVE prefixes:",
          dict([(kk, v) for kk, v in pref.most_common()
                if not kk.startswith("KXMVE")][:25]))
    print("\nquote presence KXMVE:", dict(mve_quote))
    if mve_n:
        both = mve_quote.get("bidask", 0)
        print(f"  two-sided KXMVE: {both:,} ({100*both/mve_n:.3f}%)")
    tot_other = sum(all_quote.values())
    print("quote presence non-MVE:", dict(all_quote))
    if tot_other:
        print(f"  two-sided non-MVE: {all_quote.get('bidask',0):,} "
              f"({100*all_quote.get('bidask',0)/tot_other:.2f}%)")
    if mve_top:
        mve_top.sort()
        for thr in (10, 100, 1000):
            c = sum(1 for x in mve_top if x >= thr)
            print(f"  KXMVE two-sided with >=${thr} both sides: {c:,}")
    save("s7_exchange_wide.json", {
        "total_open": n, "status_values": dict(stat),
        "prefix_counts": dict(pref), "mve_quote": dict(mve_quote),
        "nonmve_quote": dict(all_quote),
    })
    if STATUS_LOG:
        save("s7_status_log.json", STATUS_LOG)


if __name__ == "__main__":
    main()
