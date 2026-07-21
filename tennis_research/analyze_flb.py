"""Extended favorite-longshot-bias analysis on the full Kalshi tennis history.

Input: hist_candles.jsonl (one hash-picked side per settled match event,
hourly candles for the 40h before scheduled start, plus title/rules text).

Outputs:
  1. calibration curve with Wilson 95% CIs, at T-0 and T-2h
  2. realized taker EV of "buy favorite at ask >= floor", net of fees,
     threshold sweep with bootstrap CIs
  3. splits: tour, year, qualification vs main draw, slam vs regular
Usage: python3 analyze_flb.py <scratch_dir>
"""
import json
import math
import random
import sys
from tennis_pricer import kalshi_taker_fee

SC = sys.argv[1]

SLAMS = ("Wimbledon", "French Open", "US Open", "Australian Open",
         "Roland Garros")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, c - h, c + h)


def load(lag_h=0.0, filename="hist_candles.jsonl"):
    rows = []
    for line in open(f"{SC}/kalshi_snap/{filename}"):
        r = json.loads(line)
        ref = r["occ"] - lag_h * 3600
        best = None
        for ts, close, yb, ya, vol, oi in r["candles"]:
            if ts is None or yb is None or ya is None:
                continue
            if ts <= ref + 300 and ts >= ref - 40 * 3600:
                best = (float(yb), float(ya))
        if not best:
            continue
        b, a = best
        if a - b > 0.15 or a <= 0.02 or b >= 0.99 or (b == 0 and a == 1):
            continue
        mid = (a + b) / 2
        y = 1 if r["result"] == "yes" else 0
        # favorite-perspective observation
        if mid >= 0.5:
            fav_mid, fav_ask, fav_won = mid, a, y
        else:
            # complement side: NO of this market is the favorite; its ask
            # is 1 - yes_bid
            fav_mid, fav_ask, fav_won = 1 - mid, 1 - b, 1 - y
        title = r["title"]
        series = r["series"]
        rows.append({
            "series": series,
            "tour": "WTA" if "WTA" in series or "ITFW" in series else "ATP",
            "tier": ("challenger" if "CHALLENGER" in series
                     else "itf" if "ITF" in series else "tour"),
            "year": r["occ"] // 31557600 + 1970,  # approx, fine for split
            "occ": r["occ"],
            "qual": "Qualification" in title or "Qualifying" in title,
            "slam": any(s in title for s in SLAMS),
            "fav_mid": fav_mid, "fav_ask": fav_ask, "fav_won": fav_won,
            "vol": r["vol"],
        })
    return rows


def calibration(rows, label):
    print(f"\n--- calibration: {label} (n={len(rows)}) ---")
    print(f"{'bucket':<12}{'n':>6}{'implied':>9}{'won':>7}{'95% CI':>17}{'edge':>7}")
    for lo, hi in [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.85),
                   (0.85, 0.90), (0.90, 0.93), (0.93, 0.96), (0.96, 0.99)]:
        B = [r for r in rows if lo <= r["fav_mid"] < hi]
        if len(B) < 25:
            continue
        imp = sum(r["fav_mid"] for r in B) / len(B)
        k = sum(r["fav_won"] for r in B)
        p, clo, chi = wilson(k, len(B))
        sig = "*" if clo > imp else (" " if chi > imp else "-")
        print(f"[{lo:.2f},{hi:.2f}) {len(B):>6}{imp:>9.3f}{p:>7.3f}"
              f"   [{clo:.3f},{chi:.3f}]{(p-imp)*100:>+6.1f}c {sig}")


def taker_ev(rows, floor, cap=0.985):
    """Buy favorite at ask when floor <= ask <= cap. Realized net PnL/contract."""
    trades = [r for r in rows if floor <= r["fav_ask"] <= cap]
    if not trades:
        return None
    pnl = [r["fav_won"] - r["fav_ask"] - kalshi_taker_fee(r["fav_ask"])
           for r in trades]
    mean = sum(pnl) / len(pnl)
    # bootstrap CI
    random.seed(11)
    bs = []
    for _ in range(2000):
        s = [pnl[random.randrange(len(pnl))] for _ in range(len(pnl))]
        bs.append(sum(s) / len(s))
    bs.sort()
    return {"n": len(trades), "mean": mean,
            "lo": bs[50], "hi": bs[1949],
            "hit": sum(r["fav_won"] for r in trades) / len(trades)}


if __name__ == "__main__":
    for lag in (0.0, 2.0):
        rows = load(lag)
        tag = f"T-{lag:.0f}h"
        calibration(rows, f"all {tag}")
        if lag == 2.0:
            for tour in ("ATP", "WTA"):
                calibration([r for r in rows if r["tour"] == tour], f"{tour} {tag}")
            calibration([r for r in rows if r["qual"]], f"qualifiers {tag}")
            calibration([r for r in rows if not r["qual"]], f"main draw {tag}")
            calibration([r for r in rows if r["slam"]], f"slams {tag}")

    rows = load(2.0)  # conservative timestamp for the strategy sweep
    print("\n=== taker EV sweep: buy favorite at ask in [floor, 0.985], T-2h ===")
    print(f"{'floor':>6}{'n':>6}{'hit':>7}{'EV/ct':>8}{'95% CI':>20}")
    for floor in (0.85, 0.88, 0.90, 0.92, 0.94, 0.96):
        r = taker_ev(rows, floor)
        if r:
            print(f"{floor:>6.2f}{r['n']:>6}{r['hit']:>7.3f}{r['mean']*100:>+7.2f}c"
                  f"   [{r['lo']*100:+.2f}c,{r['hi']*100:+.2f}c]")
    # stability splits at the headline floor
    print("\n=== floor=0.90 splits ===")
    for name, pred in [("ATP", lambda r: r["tour"] == "ATP"),
                       ("WTA", lambda r: r["tour"] == "WTA"),
                       ("2025", lambda r: r["year"] <= 2025),
                       ("2026", lambda r: r["year"] >= 2026),
                       ("qual", lambda r: r["qual"]),
                       ("main", lambda r: not r["qual"]),
                       ("slam", lambda r: r["slam"]),
                       ("vol>10k", lambda r: r["vol"] > 10000)]:
        r = taker_ev([x for x in rows if pred(x)], 0.90)
        if r:
            print(f"{name:<8} n={r['n']:>5} hit={r['hit']:.3f} "
                  f"EV={r['mean']*100:+.2f}c [{r['lo']*100:+.2f},{r['hi']*100:+.2f}]")
