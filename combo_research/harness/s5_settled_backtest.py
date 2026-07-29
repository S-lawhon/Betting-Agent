"""S5 -- Settled combo history: do combo BUYERS overpay? (Q4)

Population: every settled(`finalized`) KXMVE market that EVER TRADED
(volume_fp > 0).  That is ~18.5M rows, so this is a STREAMING pass: nothing is
materialised except (a) per-cluster (sum, n) accumulators, which are exactly
sufficient for a cluster bootstrap of a mean, and (b) a reservoir sample of
tickers for the trade-level sub-study.

Price sources, two tiers, reported separately because they answer different
questions:
  (a) `last_price_dollars` from the LIST pull -- the whole population.  It is
      the LAST print, so on a market that kept trading it has partly converged
      toward the outcome; treat it as an upper bound on buyer skill.
  (b) `/markets/trades` VWAP and FIRST print on a random sample -- the honest
      entry price.

The house note "settled-market LIST endpoints null out volume/price/last" is
NOT assumed either way: step (0) re-fetches a random sample per-ticker and
prints the agreement rate, so the report can state which regime KXMVE is in.

Uncertainty is bootstrapped CLUSTERED BY EVENT DAY -- the resolution day, i.e.
the LATEST game date parsed out of the leg tickers (KXNBAGAME-26JUN13NYKSAS ->
26JUN13).  Never by contract: one slate's combos share the same games and the
same resolution shocks, and a per-contract CI on 18M correlated rows would be
absurdly and falsely tight.
"""
from __future__ import annotations

import gzip
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

from kalshi_api import CACHE, Kalshi, STATUS_LOG, fnum, save

OPEN_ST = ("open", "active")
SETTLED_ST = ("settled", "finalized")

random.seed(20260728)
DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def iter_rows():
    for fn in sorted(os.listdir(CACHE)):
        if not fn.endswith(".jsonl.gz"):
            continue
        series = fn[len("mve_markets_"):-len(".jsonl.gz")]
        with gzip.open(os.path.join(CACHE, fn), "rt") as fh:
            for line in fh:
                r = json.loads(line)
                r["_series"] = series
                yield r


def event_day(r):
    """Resolution-day cluster key: latest game date across the legs."""
    best = None
    for mt, _s in r.get("lg") or []:
        m = DATE_RE.search(mt or "")
        if not m:
            continue
        yy, mon, dd = m.group(1), m.group(2), m.group(3)
        if mon not in MON:
            continue
        key = f"20{yy}-{MON[mon]:02d}-{dd}"
        if best is None or key > best:
            best = key
    if best:
        return best
    for f in ("sts", "ct", "ot"):
        if r.get(f):
            return r[f][:10]
    return "?"


class ClusterAcc:
    """Exactly sufficient statistics for a cluster bootstrap of a mean."""

    def __init__(self):
        self.s = defaultdict(float)
        self.n = defaultdict(int)

    def add(self, key, v):
        self.s[key] += v
        self.n[key] += 1

    def mean(self):
        tn = sum(self.n.values())
        return (sum(self.s.values()) / tn) if tn else float("nan")

    def boot(self, n_boot=4000):
        keys = list(self.n)
        if len(keys) < 3:
            return None
        s, n = self.s, self.n
        out = []
        K = len(keys)
        for _ in range(n_boot):
            ts = tn = 0.0
            for _ in range(K):
                k = keys[random.randrange(K)]
                ts += s[k]
                tn += n[k]
            if tn:
                out.append(ts / tn)
        out.sort()
        return out[int(.025 * len(out))], out[int(.975 * len(out))]


class Agg:
    """Streaming accumulator for one price definition."""

    def __init__(self, label):
        self.label = label
        self.n = 0
        self.sp = 0.0
        self.sy = 0.0
        self.cl = ClusterAcc()
        self.wnum = 0.0
        self.wden = 0.0
        self.byleg = defaultdict(lambda: [0, 0.0, 0.0, ClusterAcc()])
        self.bybuck = defaultdict(lambda: [0, 0.0, 0.0])

    def add(self, px, y, day, nlegs, vol):
        self.n += 1
        self.sp += px
        self.sy += y
        e = y - px
        self.cl.add(day, e)
        self.wnum += e * vol
        self.wden += vol
        b = self.byleg[min(nlegs, 12)]
        b[0] += 1
        b[1] += px
        b[2] += y
        b[3].add(day, e)
        k = bucket(px)
        c = self.bybuck[k]
        c[0] += 1
        c[1] += px
        c[2] += y

    def report(self):
        if not self.n:
            print(f"  [{self.label}] no data")
            return
        mp, my = self.sp / self.n, self.sy / self.n
        print(f"\n  [{self.label}] n={self.n:,}  "
              f"event-day clusters={len(self.cl.n):,}")
        print(f"    mean traded price      : {mp:.4f}  ({100*mp:.2f}c)")
        print(f"    realised YES rate      : {my:.4f}  ({100*my:.2f}%)")
        print(f"    gap (realised - price) : {100*(my-mp):+.2f} c/contract "
              "(BUYER gross edge, before fees)")
        ci = self.cl.boot()
        if ci:
            print(f"    day-clustered 95% CI   : [{100*ci[0]:+.2f}, "
                  f"{100*ci[1]:+.2f}] c/ct   (point {100*self.cl.mean():+.2f}c)")
            ns = sorted(self.cl.n.values())
            print(f"    clusters={len(ns):,}  per-cluster n: "
                  f"min={ns[0]} med={ns[len(ns)//2]:,} max={ns[-1]:,}")
        else:
            print("    day-clustered CI: <3 clusters, NOT COMPUTABLE")
        if self.wden:
            print(f"    CONTRACT-WEIGHTED buyer gross edge: "
                  f"{100*self.wnum/self.wden:+.2f} c/ct on "
                  f"{self.wden:,.0f} contracts")
        print("    by leg count (12 = 12+):")
        for lg in sorted(self.byleg):
            n, sp, sy, cl = self.byleg[lg]
            if n < 30:
                continue
            ci2 = cl.boot(1200)
            cs = (f"[{100*ci2[0]:+.2f},{100*ci2[1]:+.2f}]" if ci2 else "n/a")
            print(f"      {lg:2d}: n={n:10,d} price={100*sp/n:6.2f}c "
                  f"yes={100*sy/n:6.2f}% gap={100*(sy/n-sp/n):+6.2f}c ci={cs}")
        print("    calibration by price bucket:")
        for k in sorted(self.bybuck):
            n, sp, sy = self.bybuck[k]
            if n < 30:
                continue
            print(f"      [{k[0]:.2f},{k[1]:.2f}) n={n:10,d} "
                  f"price={100*sp/n:6.2f}c yes={100*sy/n:6.2f}% "
                  f"gap={100*(sy/n-sp/n):+6.2f}c")


EDGES = [0, .01, .02, .05, .10, .20, .35, .50, .70, 1.01]


def bucket(p):
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        if lo <= p < hi:
            return (lo, hi)
    return (1.0, 1.01)


def main():
    NS = int(os.environ.get("TRADE_SAMPLE", "1500"))
    RES = 60_000                      # reservoir of candidate tickers

    agg = Agg("whole population, last_price_dollars")
    res_pool, seen = [], 0
    tot = nset = ntraded = 0
    res_vals = Counter()
    series_c = Counter()
    vol_vs_oi_mismatch = 0

    for r in iter_rows():
        tot += 1
        if r["st"] not in SETTLED_ST:
            continue
        nset += 1
        v = fnum(r["vol"]) or 0.0
        if v <= 0:
            continue
        ntraded += 1
        series_c[r["_series"]] += 1
        res_vals[r["res"]] += 1
        px = fnum(r["lp"])
        resu = (r["res"] or "").lower()
        if px is not None and resu in ("yes", "no"):
            agg.add(px, 1.0 if resu == "yes" else 0.0,
                    event_day(r), len(r.get("lg") or []), v)
        # reservoir of tickers for the trade-level sub-study
        seen += 1
        keep = {"t": r["t"], "res": r["res"], "lp": r["lp"], "vol": r["vol"],
                "lg": r["lg"], "sts": r["sts"], "_series": r["_series"]}
        if len(res_pool) < RES:
            res_pool.append(keep)
        else:
            j = random.randrange(seen)
            if j < RES:
                res_pool[j] = keep

    print(f"scanned {tot:,} KXMVE markets")
    print(f"settled/finalized: {nset:,}")
    print(f"settled AND ever-traded: {ntraded:,} "
          f"({100*ntraded/max(nset,1):.2f}% of settled)")
    print("\nby series:", dict(series_c))
    print("`result` VALUES on settled+traded:", dict(res_vals.most_common(10)))

    k = Kalshi()

    # ---------- (0) validate LIST fields against per-ticker GET ----------
    val = random.sample(res_pool, min(40, len(res_pool)))
    ok = bad = miss = 0
    diffs = []
    for r in val:
        m = k.get(f"/markets/{r['t']}")
        if not m or not m.get("market"):
            miss += 1
            continue
        mm = m["market"]
        same = (mm.get("result") == r["res"]
                and fnum(mm.get("last_price_dollars")) == fnum(r["lp"])
                and fnum(mm.get("volume_fp")) == fnum(r["vol"]))
        ok += same
        bad += (not same)
        if not same:
            diffs.append((r["t"], "LIST", r["res"], r["lp"], r["vol"],
                          "GET", mm.get("result"),
                          mm.get("last_price_dollars"), mm.get("volume_fp")))
    print(f"\n== LIST-vs-per-ticker-GET validation, n={ok+bad} settled markets: "
          f"AGREE={ok} DISAGREE={bad} (unfetchable {miss}) ==")
    for d in diffs[:6]:
        print("   MISMATCH", d)

    # ---------- (1) whole population ----------
    print("\n" + "=" * 70)
    print("(1) WHOLE SETTLED+TRADED POPULATION -- last_price vs realised")
    print("=" * 70)
    agg.report()

    # ---------- (2) trade-level sample ----------
    print("\n" + "=" * 70)
    print(f"(2) RANDOM SAMPLE n={NS} -- /markets/trades (actual fills)")
    print("=" * 70)
    fn = f"trades_sample_{NS}.json"
    p = os.path.join(CACHE, fn)
    if os.path.exists(p):
        cached = json.load(open(p))
        print("  (using cached trade pulls)")
    else:
        samp = random.sample(res_pool, min(NS, len(res_pool)))
        cached = {}
        for i, r in enumerate(samp):
            d = k.get("/markets/trades", {"ticker": r["t"], "limit": 500})
            cached[r["t"]] = (d or {}).get("trades", [])
            if (i + 1) % 250 == 0:
                print(f"   ...{i+1}/{len(samp)}", flush=True)
        save(fn, cached)
    byt = {r["t"]: r for r in res_pool}

    a_vwap = Agg("trade VWAP")
    a_first = Agg("FIRST print (entry price)")
    a_last = Agg("last_price, same sample (comparability check)")
    nprints, taker, block = [], Counter(), Counter()
    volcheck = [0, 0]
    for t, trades in cached.items():
        r = byt.get(t)
        if not r:
            continue
        resu = (r["res"] or "").lower()
        if resu not in ("yes", "no"):
            continue
        y = 1.0 if resu == "yes" else 0.0
        day = event_day(r)
        nl = len(r.get("lg") or [])
        v = fnum(r["vol"]) or 0.0
        lp = fnum(r["lp"])
        if lp is not None:
            a_last.add(lp, y, day, nl, v)
        if not trades:
            continue
        nprints.append(len(trades))
        num = den = 0.0
        ts = sorted(trades, key=lambda x: x.get("created_time") or "")
        for tr in ts:
            px = fnum(tr.get("yes_price_dollars"))
            c = fnum(tr.get("count_fp")) or 0.0
            if px is None or c <= 0:
                continue
            num += px * c
            den += c
            taker[tr.get("taker_side")] += 1
            block[bool(tr.get("is_block_trade"))] += 1
        if den > 0:
            a_vwap.add(num / den, y, day, nl, v)
            volcheck[0] += 1
            if abs(den - v) < 1e-6:
                volcheck[1] += 1
        px0 = fnum(ts[0].get("yes_price_dollars"))
        if px0 is not None:
            a_first.add(px0, y, day, nl, v)

    print(f"  markets in sample with >=1 print: {len(nprints)} / {len(cached)}")
    if nprints:
        nprints.sort()
        print(f"  prints per market: med={nprints[len(nprints)//2]} "
              f"mean={sum(nprints)/len(nprints):.2f} max={max(nprints)}")
    print(f"  taker_side on every print: {dict(taker)}")
    print(f"  is_block_trade: {dict(block)}")
    print(f"  VOLUME CHECK: sum(trade counts) == volume_fp on "
          f"{volcheck[1]}/{volcheck[0]} sampled markets "
          "(note: /markets/trades is capped at 500 prints per pull)")
    a_last.report()
    a_vwap.report()
    a_first.report()

    if STATUS_LOG:
        save("s5_status_log.json", STATUS_LOG)


if __name__ == "__main__":
    main()
