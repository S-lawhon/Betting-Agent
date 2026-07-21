"""Scan Kalshi tennis orderbooks for multi-leg structural arbitrage.

Relations checked within each match (all legs are BUYS — Kalshi has no
short selling; "sell YES" is expressed as "buy NO on the same market"):

  R1  exact-score completeness: sum of YES asks over the 4 set scores < $1
  R2  match hedge: NO(match A) + YES(A 2-0) + YES(A 2-1) < $1  (and mirror)
  R3  complements: YES(A) + YES(B) < $1 on match/set1/set2 books
  R4  totals ladder dominance: YES(over L1) + NO(over L2) < $1 for L1 < L2
  R5  spread-implies-match: YES(match fav) + NO(fav -L) < $1
      (fav failing to cover but winning pays both? no: this pays $1 iff
       fav wins or fav doesn't cover — always at least one — cost < 1 - fees
       is an arb only when payouts are guaranteed; see note in code)

Depth-aware: walks the book ladders, reports max contracts executable at
arb prices and total profit net of taker fees on every leg.

Usage: python3 arb_scan.py <scratch_dir>
"""
import json
import sys
from collections import defaultdict
from tennis_pricer import kalshi_taker_fee

SC = sys.argv[1]
books = json.load(open(f"{SC}/kalshi_snap/orderbooks.json"))
snap = json.load(open(f"{SC}/kalshi_snap/open_markets.json"))


def ladder(t):
    """Return (yes_asks, no_asks) as sorted [(price, size)] cheapest first.

    Kalshi books list resting YES bids under 'yes_dollars' and resting NO
    bids under 'no_dollars'. Buying YES at market crosses the NO ladder:
    a resting NO bid at price q fills a YES taker at price 1-q. So:
        yes_asks = [(1-q, sz) for (q, sz) in no_dollars], cheapest first
        no_asks  = [(1-p, sz) for (p, sz) in yes_dollars], cheapest first
    """
    b = books.get(t)
    if not b or "err" in b:
        return [], []
    def side(key):
        return sorted(((float(p), float(s)) for p, s in b.get(key) or []),
                      key=lambda x: -x[0])
    no_bids = side("no_dollars")     # highest NO bid first
    yes_bids = side("yes_dollars")   # highest YES bid first
    yes_asks = [(round(1 - q, 4), s) for q, s in no_bids]
    no_asks = [(round(1 - p, 4), s) for p, s in yes_bids]
    return yes_asks, no_asks


def walk(legs, max_qty=10000):
    """legs: list of ladders ([(price,size)...]). Fill all legs in equal
    quantity; return (qty, total_cost, avg_prices) executable, walking depth."""
    qty = 0.0
    cost = 0.0
    idx = [0] * len(legs)
    rem = [legs[i][0][1] if legs[i] else 0 for i in range(len(legs))]
    while qty < max_qty:
        if any(i >= len(l) for i, l in zip(idx, legs)):
            break
        step = min(r for r in rem)
        if step <= 0:
            break
        prices = [l[i][0] for i, l in zip(idx, legs)]
        unit = sum(prices)
        yield_q = (qty, step, unit, prices)
        # caller decides profitability; we just enumerate tranches
        yield yield_q
        qty += step
        for j in range(len(legs)):
            rem[j] -= step
            if rem[j] <= 0:
                idx[j] += 1
                rem[j] = legs[j][idx[j]][1] if idx[j] < len(legs[j]) else 0


def tranche_scan(name, legs, payout=1.0):
    """Enumerate profitable tranches: fill equal qty on all legs while
    sum(prices) + fees < payout."""
    out = []
    for qty0, step, unit, prices in walk(legs):
        fees = sum(kalshi_taker_fee(p) for p in prices)
        edge = payout - unit - fees
        if edge <= 0:
            break
        out.append({"qty": step, "unit_cost": unit, "fees": fees,
                    "edge_per_contract": edge, "prices": prices})
    if out:
        total_q = sum(t["qty"] for t in out)
        total_profit = sum(t["qty"] * t["edge_per_contract"] for t in out)
        return {"relation": name, "qty": total_q, "profit": total_profit,
                "best_edge": out[0]["edge_per_contract"],
                "legs": out[0]["prices"]}
    return None


# organize markets per match
matches = defaultdict(dict)
for s, mkts in snap.items():
    for m in mkts:
        base = m["event_ticker"].split("-")[1]
        matches[base].setdefault(s, []).append(m)

arbs = []
depth_stats = defaultdict(list)
for base, groups in matches.items():
    # --- depth bookkeeping for frictions section
    for s, ml in groups.items():
        for m in ml:
            ya, na = ladder(m["ticker"])
            if ya and na:
                best_ask, best_no = ya[0][0], na[0][0]
                mid = (best_ask + (1 - best_no)) / 2
                d5y = sum(sz for p, sz in ya if p <= best_ask + 0.05)
                d5n = sum(sz for p, sz in na if p <= best_no + 0.05)
                depth_stats[s].append({"mid": mid, "spread": best_ask - (1 - best_no),
                                       "depth5_yes": d5y, "depth5_no": d5n,
                                       "dollar_depth": (d5y + d5n) * mid})

    # --- R3 complements
    for s in ("KXATPMATCH", "KXWTAMATCH"):
        ml = groups.get(s) or []
        if len(ml) == 2:
            l1, _ = ladder(ml[0]["ticker"])
            l2, _ = ladder(ml[1]["ticker"])
            if l1 and l2:
                r = tranche_scan(f"R3 match complements {base}", [l1, l2])
                if r:
                    arbs.append(r)
    for s in ("KXATPSETWINNER", "KXWTASETWINNER"):
        ml = groups.get(s) or []
        by_set = defaultdict(list)
        for m in ml:
            by_set[m["event_ticker"].split("-")[-1]].append(m)
        for setno, mm in by_set.items():
            if len(mm) == 2:
                l1, _ = ladder(mm[0]["ticker"])
                l2, _ = ladder(mm[1]["ticker"])
                if l1 and l2:
                    r = tranche_scan(f"R3 set{setno} complements {base}", [l1, l2])
                    if r:
                        arbs.append(r)

    # --- R1 exact completeness
    ex = groups.get("KXATPEXACTMATCH") or []
    if len(ex) == 4:
        ladders = [ladder(m["ticker"])[0] for m in ex]
        if all(ladders):
            r = tranche_scan(f"R1 exact completeness {base}", ladders)
            if r:
                arbs.append(r)

    # --- R2 match hedge: NO(match A) + YES(A20) + YES(A21)
    mlist = groups.get("KXATPMATCH") or []
    if len(mlist) == 2 and len(ex) == 4:
        for mA in mlist:
            codeA = mA["ticker"].split("-")[-1]
            _, noA = ladder(mA["ticker"])
            exA = [m for m in ex if m["ticker"].split("-")[-1].startswith(codeA)]
            if len(exA) == 2 and noA:
                lad = [noA] + [ladder(m["ticker"])[0] for m in exA]
                if all(lad):
                    r = tranche_scan(f"R2 match hedge {base}/{codeA}", lad)
                    if r:
                        arbs.append(r)

    # --- R4 totals ladder dominance
    tot = sorted((groups.get("KXATPGTOTAL") or []),
                 key=lambda m: float(m.get("floor_strike") or 0))
    for i in range(len(tot)):
        for j in range(i + 1, len(tot)):
            yaL, _ = ladder(tot[i]["ticker"])   # YES over L1 (lower line)
            _, naH = ladder(tot[j]["ticker"])   # NO over L2 (higher line)
            if yaL and naH:
                r = tranche_scan(
                    f"R4 totals {base} o{tot[i].get('floor_strike')}/o{tot[j].get('floor_strike')}",
                    [yaL, naH])
                if r:
                    arbs.append(r)


print("=== depth stats (top-of-book to 5c, per market family) ===")
print(f"{'series':<18}{'n':>4}{'med spread':>11}{'med $depth':>11}{'p90 $depth':>11}")
for s, v in sorted(depth_stats.items()):
    sp = sorted(x["spread"] for x in v)
    dd = sorted(x["dollar_depth"] for x in v)
    print(f"{s:<18}{len(v):>4}{sp[len(sp)//2]*100:>10.1f}c"
          f"{dd[len(dd)//2]:>11.0f}{dd[9*len(dd)//10]:>11.0f}")

print(f"\n=== executable multi-leg arbs (net of taker fees): {len(arbs)} ===")
for a in sorted(arbs, key=lambda a: -a["profit"]):
    print(f"{a['relation']:<44} qty={a['qty']:>6.0f} "
          f"edge={a['best_edge']*100:+.1f}c profit=${a['profit']:.2f} legs={a['legs']}")
json.dump(arbs, open(f"{SC}/arbs_found.json", "w"), indent=1)
