#!/usr/bin/env python3
"""Gate 0 margin under prior-rho vs fitted-rho, from stored leg marks.

Read-only on the shadow DB. Prices every traded row's copula joint twice and
reports the in-zone margin distribution both ways.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.combo_copula import (ComboCopula, DEFAULT_RHO, correlation_matrix,
                              load_rho, relation)


def strongest(tickers):
    order = {"same_player": 3, "same_game": 2, "same_day": 1, "cross": 0}
    best = "cross"
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            r = relation(tickers[i], tickers[j])
            if order[r] > order[best]:
                best = r
    return best


def eff_marginals(legs, marks):
    by = {}
    for m in marks or []:
        t = m.get("ticker")
        if t and t not in by:
            by[t] = m
    out = []
    for tkr, side in legs:
        mid = (by.get(tkr) or {}).get("yes_mid")
        if mid is None:
            return None
        p = float(mid)
        if (side or "yes").lower() == "no":
            p = 1.0 - p
        if not (0.0 < p < 1.0):
            return None
        out.append(p)
    return out


def stats(label, vals):
    if len(vals) < 5:
        print(f"  {label:<14} n={len(vals):>5}  (too few)")
        return
    v = sorted(vals)
    n = len(v)
    print(f"  {label:<14} n={n:>5}  median {v[n//2]:+7.2f}c  "
          f"p25 {v[n//4]:+7.2f}c  p75 {v[3*n//4]:+7.2f}c  "
          f"mean {sum(v)/n:+7.2f}c  frac>0 {100*sum(1 for x in v if x>0)/n:.0f}%")


def main():
    db = sys.argv[1]
    fitted_path = sys.argv[2]
    fitted = load_rho(fitted_path)
    prior = dict(DEFAULT_RHO)
    print(f"prior rho:  {prior}")
    print(f"fitted rho: {fitted}")

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT market_ticker, legs_json, leg_marks_json, first_trade_px,"
        " n_legs, in_zone FROM combo"
        " WHERE traded=1 AND first_trade_px IS NOT NULL").fetchall()
    con.close()
    print(f"traded rows with a first print: {len(rows)}")

    cop = ComboCopula()
    out = []          # (margin_prior, margin_fitted, margin_indep, in_zone, rel, n_legs)
    for tkr, legs_j, marks_j, px, n_legs, in_zone in rows:
        try:
            legs = [(l[0], l[1]) for l in json.loads(legs_j or "[]")]
            marks = json.loads(marks_j or "[]")
        except ValueError:
            continue
        eff = eff_marginals(legs, marks)
        if not eff or len(eff) != len(legs):
            continue
        tickers = [t for t, _ in legs]
        indep = 1.0
        for p in eff:
            indep *= p
        cp = cop.joint_yes(eff, correlation_matrix(tickers, prior))
        cf = cop.joint_yes(eff, correlation_matrix(tickers, fitted))
        out.append(((px - cp) * 100, (px - cf) * 100, (px - indep) * 100,
                    in_zone, strongest(tickers), n_legs))

    print(f"priceable: {len(out)}")
    for name, idx in (("PRIOR rho", 0), ("FITTED rho", 1), ("independence", 2)):
        print(f"\n=== margin vs copula[{name}] ===")
        stats("all", [r[idx] for r in out])
        stats("in-zone", [r[idx] for r in out if r[3] == 1])
        for rel in ("same_player", "same_game", "same_day", "cross"):
            stats(rel, [r[idx] for r in out if r[4] == rel])

    zone_p = sorted(r[0] for r in out if r[3] == 1)
    zone_f = sorted(r[1] for r in out if r[3] == 1)
    if zone_p:
        print(f"\nGate 0 in-zone median: prior {zone_p[len(zone_p)//2]:+.2f}c"
              f" -> fitted {zone_f[len(zone_f)//2]:+.2f}c"
              f"   (n={len(zone_f)}; thresholds: CONTINUE >=+2.0c n>=500,"
              f" STOP <+1.0c)")


if __name__ == "__main__":
    main()
