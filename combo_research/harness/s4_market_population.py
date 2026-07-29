"""S4 -- The instantiated KXMVE market population (Q3).

Counts, leg-count distribution, price distribution (with the project's fee
parabola evaluated on it), quote presence, top-of-book size, volume/OI.

Leg counts come from each market's OWN `mve_selected_legs` array, so they are
EXACT, not inferred -- the MVE ticker suffix is an opaque hash and carries no
leg information, so any attempt to parse leg count out of the ticker would be
the kind of unverified field assumption this project has been burned by.

The population is ~9M rows, so everything here is a STREAMING aggregation
(counters + reservoir samples for quantiles); nothing is materialised.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

from kalshi_api import CACHE, fnum, save

# Kalshi returns "active" for live markets and "finalized" for settled ones.
# `status=open` / `status=settled` are the QUERY filters; these are the VALUES.
# There is no row anywhere in this pull whose status string is literally "open".
OPEN_ST = ("open", "active")
SETTLED_ST = ("settled", "finalized")

TAKER_COEF = 0.07
random.seed(20260728)
RES_N = 200_000            # reservoir size per quantile stream


class Reservoir:
    def __init__(self, n=RES_N):
        self.n, self.k, self.buf = n, 0, []

    def add(self, x):
        self.k += 1
        if len(self.buf) < self.n:
            self.buf.append(x)
        else:
            j = random.randrange(self.k)
            if j < self.n:
                self.buf[j] = x

    def q(self, p):
        if not self.buf:
            return float("nan")
        b = sorted(self.buf)
        return b[min(len(b) - 1, max(0, int(round(p / 100 * (len(b) - 1)))))]

    def mean(self):
        return sum(self.buf) / len(self.buf) if self.buf else float("nan")

    def __len__(self):
        return self.k


def iter_rows():
    for fn in sorted(os.listdir(CACHE)):
        if not fn.endswith(".jsonl.gz"):
            continue
        series = fn[len("mve_markets_"):-len(".jsonl.gz")]
        with gzip.open(os.path.join(CACHE, fn), "rt") as fh:
            for line in fh:
                r = json.loads(line)
                r["_s"] = series
                yield r


PRICE_EDGES = [0.0, .01, .02, .05, .10, .20, .30, .40, .50,
               .60, .70, .80, .90, 1.001]


def bucket(p):
    for lo, hi in zip(PRICE_EDGES[:-1], PRICE_EDGES[1:]):
        if lo <= p < hi:
            return (lo, hi)
    return (1.0, None)


def main():
    st_by_series = Counter()
    st_tot = Counter()
    legs = {"ALL": Counter(), "OPEN": Counter(), "SETTLED": Counter()}
    # price streams
    P = {k: {"res": Reservoir(), "hist": Counter(), "sum": 0.0, "n": 0,
             "feesum": 0.0, "roundsum": 0.0, "premsum": 0.0, "premn": 0}
         for k in ("last_all", "mid_open", "last_settled")}
    quotes = Counter()
    bidnot = Reservoir()
    twosz = Reservoir()
    two_thr = Counter()
    vol = {k: {"n": 0, "traded": 0, "sum": 0.0, "res": Reservoir(),
               "oi_pos": 0, "oi_sum": 0.0, "vbuck": Counter()}
           for k in ("ALL", "OPEN", "SETTLED")}
    legtrade = defaultdict(lambda: [0, 0, 0.0])
    n = 0

    def add_price(key, p):
        d = P[key]
        d["res"].add(p)
        d["hist"][bucket(p)] += 1
        d["sum"] += p
        d["n"] += 1
        f = TAKER_COEF * p * (1 - p)
        d["feesum"] += f
        d["roundsum"] += math.ceil(f * 100 - 1e-9) / 100
        if p > 0:
            d["premsum"] += f / p
            d["premn"] += 1

    def add_vol(key, v, oi):
        d = vol[key]
        d["n"] += 1
        d["sum"] += v
        if v > 0:
            d["traded"] += 1
            d["res"].add(v)
            b = (1 if v <= 1 else 2 if v <= 5 else 3 if v <= 20
                 else 4 if v <= 100 else 5 if v <= 1000 else 6)
            d["vbuck"][b] += 1
        if oi > 0:
            d["oi_pos"] += 1
        d["oi_sum"] += oi

    for r in iter_rows():
        n += 1
        st = r["st"]
        st_by_series[(r["_s"], st)] += 1
        st_tot[st] += 1
        is_open = st in OPEN_ST
        is_set = st in SETTLED_ST
        nl = len(r.get("lg") or [])
        legs["ALL"][nl] += 1
        if is_open:
            legs["OPEN"][nl] += 1
        if is_set:
            legs["SETTLED"][nl] += 1

        lp = fnum(r["lp"])
        if lp and lp > 0:
            add_price("last_all", lp)
            if is_set:
                add_price("last_settled", lp)
        b, a = fnum(r["yb"]), fnum(r["ya"])
        if is_open:
            hb = b is not None and b > 0
            ha = a is not None and a < 1.0
            quotes[("bid" if hb else "") + ("ask" if ha else "") or "none"] += 1
            if hb and ha and a > b:
                add_price("mid_open", (a + b) / 2)
            bs = fnum(r["ybs"]) or 0.0
            asz = fnum(r["yas"]) or 0.0
            if hb:
                bidnot.add(bs * b)
            if hb and ha:
                sz = min(bs * b, asz * (1 - a))
                twosz.add(sz)
                for thr in (10, 100, 1000):
                    if sz >= thr:
                        two_thr[thr] += 1

        v = fnum(r["vol"]) or 0.0
        oi = fnum(r["oi"]) or 0.0
        add_vol("ALL", v, oi)
        if is_open:
            add_vol("OPEN", v, oi)
        if is_set:
            add_vol("SETTLED", v, oi)
        t = legtrade[nl]
        t[0] += 1
        if v > 0:
            t[1] += 1
            t[2] += v

    print(f"TOTAL instantiated KXMVE markets: {n:,}\n")
    print("== by series x status ==")
    for (s, st), v in sorted(st_by_series.items()):
        print(f"  {s:32s} {st:12s} {v:10,d}")
    print("  status VALUES returned:", dict(st_tot))
    no = sum(v for k, v in st_tot.items() if k in OPEN_ST)
    ns = sum(v for k, v in st_tot.items() if k in SETTLED_ST)
    print(f"\n  OPEN(active): {no:,}   SETTLED(finalized): {ns:,}   "
          f"other: {n-no-ns:,}")

    print("\n== leg-count distribution (from mve_selected_legs, EXACT) ==")
    for k, c in legs.items():
        tot = sum(c.values()) or 1
        print(f"  {k:8s} n={tot:,}  " +
              "  ".join(f"{a}:{b:,}({100*b/tot:.1f}%)"
                        for a, b in sorted(c.items())))

    print("\n== PRICE distribution ==")
    names = {"last_all": "last_price, ALL markets with last>0",
             "mid_open": "MID of two-sided book, OPEN markets",
             "last_settled": "last_price, SETTLED markets with last>0"}
    for k, d in P.items():
        if not d["n"]:
            print(f"\n  -- {names[k]}: NONE --")
            continue
        r = d["res"]
        print(f"\n  -- {names[k]} (n={d['n']:,}) --")
        print(f"     mean={d['sum']/d['n']:.4f}  p1={r.q(1):.4f} "
              f"p5={r.q(5):.4f} p25={r.q(25):.4f} med={r.q(50):.4f} "
              f"p75={r.q(75):.4f} p95={r.q(95):.4f} p99={r.q(99):.4f}")
        for lo, hi in zip(PRICE_EDGES[:-1], PRICE_EDGES[1:]):
            v = d["hist"][(lo, hi)]
            print(f"     [{lo:.2f},{hi:.2f})  {v:9,d}  {100*v/d['n']:5.1f}%")
        print(f"     taker fee/ct RAW    : mean={100*d['feesum']/d['n']:.3f}c "
              f"(max possible 1.750c at P=0.50)")
        print(f"     taker fee/ct ROUNDED to cent (1-lot order): "
              f"mean={100*d['roundsum']/d['n']:.3f}c")
        if d["premn"]:
            print(f"     RAW fee as % of PREMIUM paid: "
                  f"mean={100*d['premsum']/d['premn']:.2f}%")

    print(f"\n== QUOTE PRESENCE (open markets, n={no:,}) ==")
    print("  ", dict(quotes))
    for k, lbl in (("bid", "bid only"), ("ask", "ask only"),
                   ("bidask", "BOTH sides"), ("none", "no quote at all")):
        v = quotes.get(k, 0)
        print(f"  {lbl:18s} {v:9,d}  ({100*v/max(no,1):.2f}%)")
    anyb = quotes.get("bid", 0) + quotes.get("bidask", 0)
    anya = quotes.get("ask", 0) + quotes.get("bidask", 0)
    print(f"  any bid            {anyb:9,d}  ({100*anyb/max(no,1):.2f}%)")
    print(f"  any ask            {anya:9,d}  ({100*anya/max(no,1):.2f}%)")
    if len(bidnot):
        print(f"  bid-side notional $ (n={len(bidnot):,}): "
              f"med=${bidnot.q(50):.2f} p90=${bidnot.q(90):.2f}")
    if len(twosz):
        print(f"  two-sided min-side notional $ (n={len(twosz):,}): "
              f"med=${twosz.q(50):.2f} p90=${twosz.q(90):.2f}")
        for thr in (10, 100, 1000):
            print(f"    >= ${thr} on BOTH sides: {two_thr[thr]:,} "
                  f"({100*two_thr[thr]/max(no,1):.3f}% of open)")

    print("\n== VOLUME / OPEN INTEREST ==")
    bn = {1: "=1", 2: "2-5", 3: "6-20", 4: "21-100", 5: "101-1000", 6: ">1000"}
    for k, d in vol.items():
        N = max(d["n"], 1)
        print(f"  {k:8s} n={d['n']:,}  EVER TRADED (volume>0): "
              f"{d['traded']:,} ({100*d['traded']/N:.2f}%)  "
              f"total contracts={d['sum']:,.0f}")
        if d["traded"]:
            r = d["res"]
            print(f"           traded-only volume: med={r.q(50):.0f} "
                  f"p75={r.q(75):.0f} p90={r.q(90):.0f} p99={r.q(99):.0f} "
                  f"max={max(r.buf):.0f}")
            print(f"           volume buckets: "
                  f"{ {bn[a]: b for a, b in sorted(d['vbuck'].items())} }")
        print(f"           OI>0: {d['oi_pos']:,} "
              f"({100*d['oi_pos']/N:.2f}%)  total OI={d['oi_sum']:,.0f}")

    print("\n== ever-traded rate BY LEG COUNT ==")
    for lg in sorted(legtrade):
        tot, tr, vv = legtrade[lg]
        print(f"  {lg:2d} legs: n={tot:10,d}  traded={tr:8,d} "
              f"({100*tr/tot:5.2f}%)  contracts={vv:,.0f}")

    save("s4_summary.json", {
        "total": n, "status": dict(st_tot),
        "legs_all": {str(k): v for k, v in legs["ALL"].items()},
        "legs_open": {str(k): v for k, v in legs["OPEN"].items()},
        "quotes_open": dict(quotes),
        "traded": {k: {"n": d["n"], "traded": d["traded"], "vol": d["sum"],
                       "oi_pos": d["oi_pos"], "oi": d["oi_sum"]}
                   for k, d in vol.items()},
        "price": {k: {"n": d["n"], "mean": (d["sum"]/d["n"] if d["n"] else None),
                      "hist": {f"{a}-{b}": v for (a, b), v in d["hist"].items()},
                      "fee_raw_mean_c": (100*d["feesum"]/d["n"]) if d["n"] else None,
                      "fee_rounded_mean_c": (100*d["roundsum"]/d["n"]) if d["n"] else None}
                  for k, d in P.items()},
    })


if __name__ == "__main__":
    main()
