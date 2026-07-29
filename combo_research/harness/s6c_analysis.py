"""S6c -- Correct read of the s6b reconstruction.

s6b's headline "combo mid vs product of leg mids" is CONTAMINATED and must not
be quoted: 70% of open MVE markets are ASK-ONLY (yes_bid = 0), so their "mid"
is just ask/2 and is mechanically far below any product of two-sided leg mids.
That artefact, not correlation pricing, is what produced the negative mean.

The statistics that survive:
  A. combo_ask - prod(leg mids)  -- what the customer pays vs independence-fair.
                                    This is the quoter's gross markup.
  B. combo_ask - prod(leg asks)  -- combo vs actually legging it in yourself.
  C. combo_mid - prod(leg mids)  -- only on the genuinely TWO-SIDED subset.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from kalshi_api import load


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))]


def stat(label, xs, unit="c"):
    if len(xs) < 10:
        print(f"    {label}: n={len(xs)} TOO THIN")
        return
    print(f"    {label}: n={len(xs):,} mean={sum(xs)/len(xs):+.3f}{unit} "
          f"med={pct(xs,50):+.3f} p25={pct(xs,25):+.3f} "
          f"p75={pct(xs,75):+.3f} p95={pct(xs,95):+.3f} "
          f"frac>0={100*sum(1 for x in xs if x>0)/len(xs):.1f}%")


def main():
    recs = load("s6b_reconstruction.json")
    print(f"reconstructions: {len(recs):,}")
    two = [r for r in recs if (r["combo_bid"] or 0) > 0]
    ask_only = [r for r in recs if (r["combo_bid"] or 0) <= 0]
    print(f"  genuinely TWO-SIDED (combo_bid>0): {len(two):,} "
          f"({100*len(two)/len(recs):.1f}%)")
    print(f"  ASK-ONLY (combo_bid==0):           {len(ask_only):,} "
          f"({100*len(ask_only)/len(recs):.1f}%)")
    print("  -> the s6b 'combo mid' figure is ask/2 for the ask-only rows "
          "and must not be quoted as a mid.\n")

    def block(label, rs):
        if len(rs) < 10:
            print(f"\n== {label}: n={len(rs)} TOO THIN ==")
            return
        print(f"\n== {label} (n={len(rs):,}) ==")
        ca = [100 * r["combo_ask"] for r in rs]
        pm = [100 * r["prod"] for r in rs]
        pa = [100 * r["prod_ask"] for r in rs]
        print(f"    combo ASK        : med={pct(ca,50):7.3f}c "
              f"mean={sum(ca)/len(ca):7.3f}c")
        print(f"    prod(leg MIDS)   : med={pct(pm,50):7.3f}c "
              f"mean={sum(pm)/len(pm):7.3f}c")
        print(f"    prod(leg ASKS)   : med={pct(pa,50):7.3f}c "
              f"mean={sum(pa)/len(pa):7.3f}c")
        stat("A. combo_ask - prod(leg mids)   [quoter markup vs independence]",
             [100 * (r["combo_ask"] - r["prod"]) for r in rs])
        stat("B. combo_ask - prod(leg asks)   [combo vs legging it yourself]",
             [100 * (r["combo_ask"] - r["prod_ask"]) for r in rs])
        rel = [100 * (r["combo_ask"] / r["prod"] - 1) for r in rs
               if r["prod"] > 1e-9]
        stat("A%. relative markup over independence", rel, unit="%")
        t2 = [r for r in rs if (r["combo_bid"] or 0) > 0]
        if len(t2) >= 10:
            stat("C. combo_mid - prod(leg mids)  [TWO-SIDED subset only]",
                 [100 * (r["combo_mid"] - r["prod"]) for r in t2])
        else:
            print(f"    C. two-sided subset n={len(t2)} TOO THIN")

    block("ALL", recs)
    block("SAME-GAME legs (correlated)", [r for r in recs if r["same_game"]])
    block("CROSS-GAME legs", [r for r in recs if not r["same_game"]])
    for n in sorted({r["n_legs"] for r in recs}):
        rs = [r for r in recs if r["n_legs"] == n]
        if len(rs) >= 40:
            block(f"{n} legs", rs)

    print("\n== summary table: markup by leg count ==")
    print(f"  {'legs':>4} {'n':>6} {'combo_ask':>10} {'prod_mid':>9} "
          f"{'A(c)':>8} {'A(%)':>8} {'B(c)':>8}")
    for n in sorted({r["n_legs"] for r in recs}):
        rs = [r for r in recs if r["n_legs"] == n]
        if len(rs) < 30:
            continue
        ca = sum(r["combo_ask"] for r in rs) / len(rs)
        pm = sum(r["prod"] for r in rs) / len(rs)
        a = [100 * (r["combo_ask"] - r["prod"]) for r in rs]
        b = [100 * (r["combo_ask"] - r["prod_ask"]) for r in rs]
        rel = [100 * (r["combo_ask"] / r["prod"] - 1) for r in rs
               if r["prod"] > 1e-9]
        print(f"  {n:>4} {len(rs):>6} {100*ca:>9.2f}c {100*pm:>8.2f}c "
              f"{pct(a,50):>+7.2f}c {pct(rel,50):>+7.1f}% {pct(b,50):>+7.2f}c")


if __name__ == "__main__":
    main()
