"""S6b -- Leg-price reconstruction from a FRESH open-market snapshot (Q5).

Why this exists: s6 sampled quoted combos out of the S3 cache and, on refetch
a few hours later, 3,961 of 4,000 no longer had a quote at all.  MVE quotes
are pulled as soon as the underlying game gets close, so ANY combo sample
older than minutes is mostly dead.  Everything here therefore happens inside
one pass: pull open MVE markets, keep the quoted ones, fetch their legs
immediately.

Legs come from `mve_selected_legs` (exact market_ticker + side), never from
parsing the combo ticker -- the MVE suffix is an opaque hash.

price(leg, side=yes) = leg yes-mid ; price(leg, side=no) = 1 - leg yes-mid
independence-implied combo price = product of leg prices
statistic = combo_mid - product, in cents (positive = combo priced ABOVE
independence, i.e. the quoter is charging for positive correlation or margin).

Batching is by CHARACTER BUDGET: /markets?tickers= 414s past ~5k URL chars.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

from kalshi_api import Kalshi, STATUS_LOG, fnum, save

random.seed(20260728)
LIVE_SERIES = ["KXMVECROSSCATEGORY", "KXMVESPORTSMULTIGAMEEXTENDED"]
GAME_RE = re.compile(r"^[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2}[A-Z0-9]*?)(?:-|$)")


def game_key(mt):
    m = GAME_RE.match(mt or "")
    return m.group(1) if m else None


def mid(m):
    b, a = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
    if b is None or a is None or a <= b:
        return None, None
    if b <= 0 and a >= 1.0:
        return None, None
    return (b + a) / 2.0, a - b


def batched(seq, n=200, max_chars=3500):
    cur, cl = [], 0
    for x in seq:
        if cur and (cl + len(x) + 1 > max_chars or len(cur) >= n):
            yield cur
            cur, cl = [], 0
        cur.append(x)
        cl += len(x) + 1
    if cur:
        yield cur


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))]


def main():
    TARGET = int(os.environ.get("TARGET", "4000"))
    MAXPAGE = int(os.environ.get("MAXPAGE", "400"))
    k = Kalshi()

    quoted = []
    for s in LIVE_SERIES:
        params = {"series_ticker": s, "status": "open", "limit": 1000}
        page = 0
        seen = 0
        while page < MAXPAGE and len(quoted) < TARGET * 3:
            d = k.get("/markets", params)
            if not d:
                break
            rows = d.get("markets") or []
            seen += len(rows)
            for m in rows:
                if not m.get("mve_selected_legs"):
                    continue
                if mid(m)[0] is None:
                    continue
                quoted.append(m)
            page += 1
            cur = d.get("cursor")
            if not cur or not rows:
                break
            params["cursor"] = cur
        print(f"  {s}: scanned {seen:,} open, quoted+legged {len(quoted):,}",
              flush=True)

    print(f"total quoted open combos collected: {len(quoted):,}")
    if not quoted:
        print("NONE -- cannot run.")
        return
    samp = random.sample(quoted, min(TARGET, len(quoted)))
    print(f"sampled {len(samp):,}")

    legs_needed = sorted({l["market_ticker"] for m in samp
                          for l in m["mve_selected_legs"]})
    print(f"distinct underlying leg markets: {len(legs_needed):,}")
    legq = {}
    for i, b in enumerate(batched(legs_needed)):
        d = k.get("/markets", {"tickers": ",".join(b), "limit": 1000})
        for m in (d or {}).get("markets", []):
            legq[m["ticker"]] = m
        if (i + 1) % 20 == 0:
            print(f"   leg batch {i+1}, {len(legq):,}", flush=True)
    print(f"leg quotes fetched: {len(legq):,} "
          f"({len(legs_needed)-len(legq):,} missing)")

    recs = []
    drop = Counter()
    for m in samp:
        cmid, cspr = mid(m)
        lg = m["mve_selected_legs"]
        prod = 1.0
        pa = 1.0
        ok = True
        for l in lg:
            lm = legq.get(l["market_ticker"])
            if lm is None:
                ok = False
                drop["leg_missing"] += 1
                break
            lmid, _ = mid(lm)
            if lmid is None:
                ok = False
                drop["leg_no_quote"] += 1
                break
            side = l["side"]
            prod *= lmid if side == "yes" else 1.0 - lmid
            lb = fnum(lm.get("yes_bid_dollars"))
            la = fnum(lm.get("yes_ask_dollars"))
            pa *= la if side == "yes" else (1.0 - lb)
        if not ok:
            continue
        gks = {game_key(l["market_ticker"]) for l in lg}
        recs.append({
            "t": m["ticker"], "n_legs": len(lg), "combo_mid": cmid,
            "combo_bid": fnum(m.get("yes_bid_dollars")),
            "combo_ask": fnum(m.get("yes_ask_dollars")),
            "combo_spread": cspr, "prod": prod, "prod_ask": pa,
            "diff_c": 100.0 * (cmid - prod),
            "ratio": (cmid / prod) if prod > 0 else None,
            "same_game": len(gks) == 1 and None not in gks,
            "n_games": len(gks),
        })
    save("s6b_reconstruction.json", recs)
    print(f"\nusable reconstructions: {len(recs):,}  drops: {dict(drop)}")
    if not recs:
        return

    def rep(label, rs):
        if len(rs) < 10:
            print(f"\n  -- {label}: n={len(rs)} TOO THIN --")
            return
        d = [r["diff_c"] for r in rs]
        cm = [r["combo_mid"] for r in rs]
        pr = [r["prod"] for r in rs]
        print(f"\n  -- {label} (n={len(rs):,}) --")
        print(f"     combo mid    : med={100*pct(cm,50):7.3f}c "
              f"mean={100*sum(cm)/len(cm):7.3f}c")
        print(f"     prod of legs : med={100*pct(pr,50):7.3f}c "
              f"mean={100*sum(pr)/len(pr):7.3f}c")
        print(f"     DIFF combo-product (cents): mean={sum(d)/len(d):+.3f} "
              f"med={pct(d,50):+.3f} p5={pct(d,5):+.3f} p25={pct(d,25):+.3f} "
              f"p75={pct(d,75):+.3f} p95={pct(d,95):+.3f}")
        print(f"     priced ABOVE independence: "
              f"{100*sum(1 for x in d if x > 0)/len(d):.1f}%")
        rt = [r["ratio"] for r in rs if r["ratio"]]
        if rt:
            print(f"     ratio combo/product: med={pct(rt,50):.3f} "
                  f"p25={pct(rt,25):.3f} p75={pct(rt,75):.3f}")
        sp = [r["combo_spread"] for r in rs]
        print(f"     combo bid-ask: med={100*pct(sp,50):.2f}c "
              f"p90={100*pct(sp,90):.2f}c")
        aa = [100 * (r["combo_ask"] - r["prod_ask"]) for r in rs]
        print(f"     ASK-side combo_ask - prod(leg asks): "
              f"med={pct(aa,50):+.3f}c mean={sum(aa)/len(aa):+.3f}c "
              f"(>0 = combo dearer than legging it)")

    rep("ALL", recs)
    rep("SAME-GAME legs (correlated)", [r for r in recs if r["same_game"]])
    rep("CROSS-GAME legs", [r for r in recs if not r["same_game"]])
    for n in sorted({r["n_legs"] for r in recs}):
        rep(f"{n} legs", [r for r in recs if r["n_legs"] == n])
    print("\nsame_game split:", dict(Counter(r["same_game"] for r in recs)))
    print("n_games:", dict(Counter(r["n_games"] for r in recs).most_common(10)))
    if STATUS_LOG:
        save("s6b_status_log.json", STATUS_LOG)


if __name__ == "__main__":
    main()
