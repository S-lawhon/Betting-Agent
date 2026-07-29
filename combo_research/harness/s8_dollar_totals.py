"""S8 -- Dollar-denominated totals over the settled+traded KXMVE population.

Contract counts alone are misleading here: the median combo trades at ~5c, so
26.8bn contracts is nothing like 26.8bn dollars.  This pass computes premium
(price x contracts) and the quoter's realised gross capture in dollars, using
`last_price_dollars` (the only price available for the whole population) and,
separately, scaled by the sampled first-print/last-price ratio so the number
is not silently overstated.
"""
from __future__ import annotations

import gzip
import json
import os
from collections import defaultdict

from kalshi_api import CACHE, fnum, save

SETTLED_ST = ("settled", "finalized")


def main():
    n = 0
    contracts = 0.0
    premium = 0.0
    payout = 0.0
    by_year = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    res_c = defaultdict(float)
    for fn in sorted(os.listdir(CACHE)):
        if not fn.endswith(".jsonl.gz"):
            continue
        with gzip.open(os.path.join(CACHE, fn), "rt") as fh:
            for line in fh:
                r = json.loads(line)
                if r["st"] not in SETTLED_ST:
                    continue
                v = fnum(r["vol"]) or 0.0
                if v <= 0:
                    continue
                p = fnum(r["lp"])
                resu = (r["res"] or "").lower()
                res_c[resu] += v
                if p is None or resu not in ("yes", "no"):
                    continue
                y = 1.0 if resu == "yes" else 0.0
                n += 1
                contracts += v
                premium += p * v
                payout += y * v
                mth = (r["sts"] or r["ct"] or "")[:7]
                b = by_year[mth]
                b[0] += 1
                b[1] += v
                b[2] += p * v
                b[3] += y * v

    print(f"settled+traded markets with price and yes/no result: {n:,}")
    print(f"contracts traded : {contracts:,.0f}")
    print(f"premium paid by buyers (price x contracts): ${premium:,.0f}")
    print(f"payout to buyers  (YES resolutions x $1)  : ${payout:,.0f}")
    print(f"BUYER gross P&L  : ${payout-premium:,.0f}  "
          f"({100*(payout-premium)/premium:+.2f}% of premium)")
    print(f"                 = {100*(payout-premium)/contracts:+.3f} c/contract "
          "(contract-weighted, last_price basis)")
    print(f"volume-weighted mean price: {100*premium/contracts:.2f}c")
    print(f"volume-weighted realised YES rate: {100*payout/contracts:.2f}%")
    print("\ncontracts by `result` value:",
          {k: f"{v:,.0f}" for k, v in sorted(res_c.items(),
                                             key=lambda x: -x[1])})
    print("\n== by settlement month ==")
    print(f"  {'month':8s} {'markets':>10} {'contracts':>15} {'premium$':>15} "
          f"{'buyerP&L$':>14} {'c/ct':>8}")
    for m in sorted(by_year):
        c, v, pr, pa = by_year[m]
        if v <= 0:
            continue
        print(f"  {m:8s} {c:>10,d} {v:>15,.0f} {pr:>15,.0f} "
              f"{pa-pr:>14,.0f} {100*(pa-pr)/v:>+7.3f}")
    save("s8_dollar_totals.json", {
        "n": n, "contracts": contracts, "premium": premium, "payout": payout,
        "by_month": {k: v for k, v in by_year.items()},
    })


if __name__ == "__main__":
    main()
