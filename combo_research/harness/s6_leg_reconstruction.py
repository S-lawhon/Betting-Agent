"""S6 -- Leg-price reconstruction: combo price vs product of leg prices (Q5).

Legs are NOT parsed out of the combo ticker (the suffix is an opaque hash).
They come from the market's own `mve_selected_legs` array, which gives the
exact underlying market_ticker AND the side taken -- so the reconstruction is
exact, not inferred.  This is the single biggest reason the numbers here are
trustworthy: there is no field-ordering assumption anywhere.

Combo and leg prices are RE-FETCHED IN THE SAME PASS (batched 200 tickers per
/markets call) so the two sides of the comparison are contemporaneous; using
the S3 cache for the combo side would compare a stale combo quote to a live
leg quote.

price(leg, side=yes) = leg yes-mid ; price(leg, side=no) = 1 - leg yes-mid.
independence-implied combo price = product over legs.
reported statistic = combo_price - product, in cents.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict

from kalshi_api import CACHE, Kalshi, STATUS_LOG, fnum, save

# Kalshi returns "active" for live markets and "finalized" for settled ones.
# `status=open` / `status=settled` are the QUERY filters; these are the VALUES.
OPEN_ST = ("open", "active")
SETTLED_ST = ("settled", "finalized")

random.seed(20260728)
GAME_RE = re.compile(r"^[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2}[A-Z0-9]*?)(?:-|$)")


def iter_rows():
    for fn in sorted(os.listdir(CACHE)):
        if not fn.endswith(".jsonl.gz"):
            continue
        with gzip.open(os.path.join(CACHE, fn), "rt") as fh:
            for line in fh:
                yield json.loads(line)


def game_key(mt: str):
    m = GAME_RE.match(mt or "")
    return m.group(1) if m else None


def mid(m):
    b, a = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
    if b is None or a is None:
        return None, None
    if a <= b:
        return None, None
    if b <= 0 and a >= 1.0:
        return None, None            # empty book, not a quote
    return (b + a) / 2.0, a - b


def batched(seq, n=200, max_chars=3500):
    """Batch by CHARACTER BUDGET, not by count.

    /markets?tickers=<csv> 414s once the URL gets long: 200 MVE tickers
    (9,599 chars of CSV) is a hard 414 from CloudFront, 100 (4,799) is fine.
    MVE tickers are ~47 chars vs ~30 for an ordinary market ticker, so a
    count-based batch size that works for legs silently dies on combos.
    """
    seq = list(seq)
    cur, cur_len = [], 0
    for x in seq:
        if cur and (cur_len + len(x) + 1 > max_chars or len(cur) >= n):
            yield cur
            cur, cur_len = [], 0
        cur.append(x)
        cur_len += len(x) + 1
    if cur:
        yield cur


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))]


def main():
    N = int(os.environ.get("N_COMBOS", "4000"))
    print("selecting OPEN KXMVE markets that carry a two-sided quote ...")
    cands = []
    for r in iter_rows():
        if r["st"] not in OPEN_ST or not r.get("lg"):
            continue
        b, a = fnum(r["yb"]), fnum(r["ya"])
        if b is None or a is None:
            continue
        if b > 0 or a < 1.0:
            cands.append(r["t"])
    print(f"  open+quoted candidates: {len(cands):,}")
    if not cands:
        print("  NONE -- cannot run leg reconstruction.")
        return
    samp = random.sample(cands, min(N, len(cands)))
    print(f"  sampled {len(samp):,}")

    k = Kalshi()
    # 1) fresh combo quotes
    combos = {}
    for i, b in enumerate(batched(samp)):
        d = k.get("/markets", {"tickers": ",".join(b), "limit": 1000})
        for m in (d or {}).get("markets", []):
            combos[m["ticker"]] = m
        if (i + 1) % 5 == 0:
            print(f"   combo batch {i+1}, {len(combos):,} fetched", flush=True)
    print(f"  combo quotes refetched: {len(combos):,}")

    legmap = {}
    for r in iter_rows():
        if r["t"] in combos:
            legmap[r["t"]] = r["lg"]

    legs_needed = sorted({mt for lg in legmap.values() for mt, _ in lg})
    print(f"  distinct underlying leg markets: {len(legs_needed):,}")

    # 2) fresh leg quotes -- SAME PASS, contemporaneous
    legq = {}
    for i, b in enumerate(batched(legs_needed)):
        d = k.get("/markets", {"tickers": ",".join(b), "limit": 1000})
        for m in (d or {}).get("markets", []):
            legq[m["ticker"]] = m
        if (i + 1) % 10 == 0:
            print(f"   leg batch {i+1}, {len(legq):,} fetched", flush=True)
    print(f"  leg quotes fetched: {len(legq):,} "
          f"({len(legs_needed)-len(legq):,} missing)")
    save("s6_leg_quotes_meta.json",
         {"n_combos": len(combos), "n_legs_needed": len(legs_needed),
          "n_legs_fetched": len(legq)})

    # 3) reconstruct
    recs = []
    drop = Counter()
    for t, m in combos.items():
        lg = legmap.get(t) or []
        if not lg:
            drop["no_legs"] += 1
            continue
        cmid, cspr = mid(m)
        if cmid is None:
            drop["combo_no_quote"] += 1
            continue
        prod = 1.0
        ok = True
        legpx = []
        spreads = []
        for mt, side in lg:
            lm = legq.get(mt)
            if lm is None:
                ok = False
                drop["leg_missing"] += 1
                break
            lmid, lspr = mid(lm)
            if lmid is None:
                ok = False
                drop["leg_no_quote"] += 1
                break
            p = lmid if side == "yes" else 1.0 - lmid
            legpx.append(p)
            spreads.append(lspr)
            prod *= p
        if not ok:
            continue
        gks = {game_key(mt) for mt, _ in lg}
        recs.append({
            "t": t, "n_legs": len(lg), "combo_mid": cmid,
            "combo_spread": cspr, "prod": prod,
            "diff_c": 100.0 * (cmid - prod),
            "ratio": (cmid / prod) if prod > 0 else None,
            "same_game": len(gks) == 1 and None not in gks,
            "n_games": len(gks),
            "leg_spread_max_c": 100.0 * max(spreads),
            "combo_ask": fnum(m.get("yes_ask_dollars")),
            "combo_bid": fnum(m.get("yes_bid_dollars")),
            "prod_ask": None,
        })
    # ask-side reconstruction (what a BUYER actually pays vs buying the legs)
    for r in recs:
        lg = legmap[r["t"]]
        pa = 1.0
        okk = True
        for mt, side in lg:
            lm = legq.get(mt)
            b, a = fnum(lm.get("yes_bid_dollars")), fnum(lm.get("yes_ask_dollars"))
            if b is None or a is None or a <= b:
                okk = False
                break
            pa *= a if side == "yes" else (1.0 - b)
        r["prod_ask"] = pa if okk else None

    save("s6_reconstruction.json", recs)
    print(f"\n  usable reconstructions: {len(recs):,}   drops: {dict(drop)}")
    if not recs:
        return

    def rep(label, rs):
        if len(rs) < 5:
            print(f"  {label}: n={len(rs)} (too thin)")
            return
        d = [r["diff_c"] for r in rs]
        cm = [r["combo_mid"] for r in rs]
        pr = [r["prod"] for r in rs]
        print(f"\n  -- {label} (n={len(rs):,}) --")
        print(f"     combo mid   : med={100*pct(cm,50):6.2f}c "
              f"mean={100*sum(cm)/len(cm):6.2f}c")
        print(f"     prod of legs: med={100*pct(pr,50):6.2f}c "
              f"mean={100*sum(pr)/len(pr):6.2f}c")
        print(f"     DIFF (combo - product), cents:")
        print(f"       mean={sum(d)/len(d):+.3f}  med={pct(d,50):+.3f}  "
              f"p5={pct(d,5):+.3f} p25={pct(d,25):+.3f} "
              f"p75={pct(d,75):+.3f} p95={pct(d,95):+.3f}")
        above = sum(1 for x in d if x > 0)
        print(f"       fraction priced ABOVE independence: "
              f"{100*above/len(d):.1f}%")
        rt = [r["ratio"] for r in rs if r["ratio"]]
        if rt:
            print(f"       ratio combo/product: med={pct(rt,50):.3f} "
                  f"p25={pct(rt,25):.3f} p75={pct(rt,75):.3f}")
        sp = [r["combo_spread"] for r in rs]
        print(f"       combo bid-ask spread: med={100*pct(sp,50):.2f}c "
              f"p90={100*pct(sp,90):.2f}c")
        pa = [(r["combo_ask"] - r["prod_ask"]) * 100
              for r in rs if r["prod_ask"] is not None]
        if pa:
            print(f"       ASK-side: combo_ask - prod(leg asks) : "
                  f"med={pct(pa,50):+.3f}c mean={sum(pa)/len(pa):+.3f}c")

    rep("ALL", recs)
    rep("SAME-GAME legs (correlated)", [r for r in recs if r["same_game"]])
    rep("CROSS-GAME legs", [r for r in recs if not r["same_game"]])
    for lgc in sorted({r["n_legs"] for r in recs}):
        rep(f"{lgc} legs", [r for r in recs if r["n_legs"] == lgc])
    print("\n  same_game split:",
          dict(Counter(r["same_game"] for r in recs)))
    print("  n_games distribution:",
          dict(Counter(r["n_games"] for r in recs).most_common(8)))

    if STATUS_LOG:
        save("s6_status_log.json", STATUS_LOG)


if __name__ == "__main__":
    main()
