"""Backtest: settled Kalshi tennis props vs empirical fair prices.

Joins pre-match candle prices (last hourly candle before scheduled match
start) of prop markets with their sibling match-winner market, computes
the empirical fair for every prop given the Kalshi match line, and
scores three things:

  A. GAP: distribution of (fair - market mid) per prop family
  B. CALIBRATION: Brier/log-loss of market mid vs empirical fair
     against realized outcomes  -> which price is closer to truth?
  C. TAKER SIM: buy YES at ask (or NO at 1-bid) whenever the empirical
     fair signals >= threshold net edge after Kalshi taker fees;
     realized PnL per trade, by series and threshold.

Usage: python3 backtest_candles.py <scratch_dir>
"""
import json
import math
import re
import sys

from empirical_fair import fair
from tennis_pricer import kalshi_taker_fee

SC = sys.argv[1]
CURVES = json.load(open(f"{SC}/empirical_curves.json"))

# ---- load candles
recs = []
for line in open(f"{SC}/kalshi_snap/candles.jsonl"):
    recs.append(json.loads(line))
print(f"candle records: {len(recs)}")

# ---- market metadata from settled snapshot
meta = {}
for s, mkts in json.load(open(f"{SC}/kalshi_snap/settled_markets.json")).items():
    for m in mkts:
        meta[m["ticker"]] = m


import os
LAG_H = float(os.environ.get("LAG_H", "0"))  # quote at occ - LAG_H hours


def pre_match_quote(rec, lead_max_h=24):
    """(mid, bid, ask) from last candle ending at/before match start
    minus LAG_H hours (sensitivity control for late-start contamination)."""
    ref = rec["occ"] - LAG_H * 3600
    best = None
    for ts, close, yb, ya, vol, oi in rec["candles"]:
        if ts is None:
            continue
        if ts <= ref + 300 and ts >= ref - lead_max_h * 3600:
            best = (ts, close, yb, ya)
    if not best:
        return None
    ts, close, yb, ya = best
    # dollar strings or cents ints depending on API era — normalize
    def num(v):
        if v is None:
            return None
        v = float(v)
        return v / 100 if v > 1.5 else v
    b, a = num(yb), num(ya)
    if b is None or a is None:
        return None
    if a - b > 0.30 or a <= 0.01 or b >= 0.99:
        return None
    return {"mid": (a + b) / 2, "bid": b, "ask": a}


# index match records by event base
by_series = {}
for r in recs:
    by_series.setdefault(r["series"], []).append(r)

match_line = {}  # base -> (p_fav, fav_code, tour)
for s in ("KXATPMATCH", "KXWTAMATCH"):
    for r in by_series.get(s, []):
        q = pre_match_quote(r)
        if not q:
            continue
        t = r["ticker"]
        base = t.split("-")[1]
        code = t.split("-")[-1]
        m = meta.get(t, {})
        # this side's prob = mid; other player is the complement
        p = q["mid"]
        ev = m.get("event_ticker", "")
        # find both player codes from event: base contains both 3-letter codes
        # ticker code is one side; favorite determined by p
        tour = "ATP" if s == "KXATPMATCH" else "WTA"
        if p >= 0.5:
            match_line[base] = {"p_fav": p, "fav_code": code, "tour": tour,
                                "res_fav": 1 if r["result"] == "yes" else 0}
        else:
            match_line[base] = {"p_fav": 1 - p, "fav_code": None,
                                "other_code": code, "tour": tour,
                                "res_fav": 1 if r["result"] == "no" else 0}
print(f"match lines resolved: {len(match_line)}")


def surface_for(tour):
    return "all"  # 28-day window spans grass+clay; use all-surface curves


def emp(tour, prop, p_fav):
    c = CURVES[f"{tour}|{surface_for(tour)}"]["curves"]
    if prop not in c:
        return None
    return fair(c, prop, p_fav)


WIMBLEDON = (1782604800, 1783900800)  # 2026-06-28 .. 2026-07-13 UTC

rows = []
for s, rl in by_series.items():
    if s in ("KXATPMATCH", "KXWTAMATCH"):
        continue
    for r in rl:
        t = r["ticker"]
        # ATP slam matches are Bo5; our empirical curves are Bo3-only
        if s.startswith("KXATP") and WIMBLEDON[0] <= r["occ"] <= WIMBLEDON[1]:
            continue
        base = t.split("-")[1]
        ml = match_line.get(base)
        if not ml:
            continue
        q = pre_match_quote(r)
        if not q:
            continue
        m = meta.get(t, {})
        p_fav, tour = ml["p_fav"], ml["tour"]
        code = t.split("-")[-1]
        y = 1 if r["result"] == "yes" else 0

        def is_fav(player_code):
            if ml.get("fav_code"):
                return player_code == ml["fav_code"]
            return player_code != ml.get("other_code")

        prop = None
        f = None
        if s in ("KXATPSETWINNER", "KXWTASETWINNER"):
            fv = emp(tour, "fav_set1", p_fav)
            if fv is None:
                continue
            f = fv if is_fav(code) else 1 - fv
            prop = "set1"
        elif s == "KXATPEXACTMATCH":
            mm = re.match(r"([A-Z]+)(\d)(\d)", code)
            if not mm:
                continue
            score = mm.group(2) + mm.group(3)
            if score not in ("20", "21"):
                continue  # Bo5 scores — outside Bo3 curve support
            key = ("fav_" if is_fav(mm.group(1)) else "dog_") + score
            f20 = emp(tour, "fav_20", p_fav); f21 = emp(tour, "fav_21", p_fav)
            d21 = emp(tour, "dog_21", p_fav); d20 = emp(tour, "dog_20", p_fav)
            kf = p_fav / (f20 + f21); kd = (1 - p_fav) / (d20 + d21)
            vals = {"fav_20": f20 * kf, "fav_21": f21 * kf,
                    "dog_21": d21 * kd, "dog_20": d20 * kd}
            f = vals[key]
            prop = "exact"
        elif s == "KXATPGTOTAL":
            fs = meta.get(t, {}).get("floor_strike")
            if fs is None:
                continue
            f = emp(tour, f"over_{float(fs)}", p_fav)
            prop = "total"
        elif s == "KXATPGSPREAD":
            sub = m.get("yes_sub_title") or ""
            mm = re.search(r"-([\d.]+) games", sub)
            mmc = re.match(r"([A-Z]+)\d", code)
            if not mm or not mmc:
                continue
            line = float(mm.group(1))
            if not is_fav(mmc.group(1)):
                continue  # dog spreads lack a clean curve; skip
            f = emp(tour, f"fav_sp_{line}", p_fav)
            prop = "spread"
        if f is None or prop is None:
            continue
        rows.append({"series": s, "prop": prop, "ticker": t, "tour": tour,
                     "p_fav": p_fav, "fair": f, "y": y, **q,
                     "vol": r["vol"]})

print(f"joined prop observations: {len(rows)}")
json.dump(rows, open(f"{SC}/backtest_rows.json", "w"))

# ---------- A. gap distributions
print("\n=== A. fair - mid gaps by prop ===")
for prop in ("set1", "exact", "total", "spread"):
    v = [r for r in rows if r["prop"] == prop]
    if not v:
        continue
    gaps = sorted(r["fair"] - r["mid"] for r in v)
    n = len(gaps)
    print(f"{prop:<7} n={n:>5} p10={gaps[n//10]*100:+.1f}c med={gaps[n//2]*100:+.1f}c "
          f"p90={gaps[9*n//10]*100:+.1f}c mean={sum(gaps)/n*100:+.1f}c")

# ---------- B. calibration
print("\n=== B. Brier: market mid vs empirical fair ===")
for prop in ("set1", "exact", "total", "spread"):
    v = [r for r in rows if r["prop"] == prop]
    if len(v) < 50:
        continue
    bm = sum((r["mid"] - r["y"]) ** 2 for r in v) / len(v)
    bf = sum((r["fair"] - r["y"]) ** 2 for r in v) / len(v)
    # blend test: half-and-half
    bb = sum((((r["mid"] + r["fair"]) / 2) - r["y"]) ** 2 for r in v) / len(v)
    print(f"{prop:<7} n={len(v):>5}  Brier(mid)={bm:.4f}  Brier(fair)={bf:.4f}  "
          f"Brier(blend)={bb:.4f}  {'MARKET' if bm < bf else 'FAIR'} better")

# match-line calibration & FLB
mv = [(ml["p_fav"], ml["res_fav"]) for ml in match_line.values()]
print(f"\nmatch-line calibration (n={len(mv)}):")
for lo, hi in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]:
    b = [(p, y) for p, y in mv if lo <= p < hi]
    if len(b) < 30:
        continue
    pm = sum(p for p, _ in b) / len(b)
    ym = sum(y for _, y in b) / len(b)
    se = math.sqrt(max(ym * (1 - ym), 1e-9) / len(b))
    print(f"  [{lo:.1f},{hi:.1f}) n={len(b):>4} implied={pm:.3f} won={ym:.3f} (se {se:.3f})")

# ---------- C. taker simulation
print("\n=== C. taker sim (net edge threshold sweep) ===")
for thr in (0.02, 0.04, 0.06):
    for prop in ("set1", "exact", "total", "spread"):
        v = [r for r in rows if r["prop"] == prop]
        pnl = 0.0
        nt = 0
        wins = 0
        for r in v:
            fee_a = kalshi_taker_fee(r["ask"])
            fee_n = kalshi_taker_fee(1 - r["bid"])
            if r["fair"] - r["ask"] - fee_a >= thr:
                pnl += r["y"] - r["ask"] - fee_a
                nt += 1
                wins += r["y"]
            elif r["bid"] - r["fair"] - fee_n >= thr:
                pnl += (1 - r["y"]) - (1 - r["bid"]) - fee_n
                nt += 1
                wins += 1 - r["y"]
        if nt:
            print(f"thr={thr:.2f} {prop:<7} trades={nt:>4} hit={wins/nt:.3f} "
                  f"pnl={pnl:+.2f} (avg {pnl/nt*100:+.1f}c/contract)")
