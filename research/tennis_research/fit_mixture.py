"""Fit the mixture dispersion parameters (s0, sig_s, sig_d) per segment.

Targets: empirical bucket moments (P(fav 2-0), P(any TB), E[total games],
P(over 21.5)) across favorite-probability buckets. Grid search.
"""
import json
import sys
from tennis_pricer import price_props_mixture, implied_holds_mixture

SC = sys.argv[1]
rows = json.load(open(f"{SC}/empirical_matches.json"))

BUCKETS = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9)]


def moments(sample):
    out = []
    for lo, hi in BUCKETS:
        B = [r for r in sample if lo <= r["p_fav"] < hi]
        if len(B) < 80:
            out.append(None)
            continue
        pmid = sum(r["p_fav"] for r in B) / len(B)
        out.append({
            "n": len(B), "p_fav": pmid,
            "p20": sum(1 for r in B if r["fav_won"] and r["wsets"] == 2 and r["lsets"] == 0) / len(B),
            "tb": sum(1 for r in B if r["any_tb"]) / len(B),
            "etot": sum(r["total_games"] for r in B) / len(B),
            "o215": sum(1 for r in B if r["total_games"] > 21.5) / len(B),
        })
    return out


def model_moments(p_fav, s0, sig_s, sig_d):
    # solve d on the base model (fast; mixture shifts match_a only ~1c)
    from tennis_pricer import implied_holds
    pa, pb = implied_holds(p_fav, best_of=3, avg_spw=s0 / 2)
    pa, pb = round(pa, 3), round(pb, 3)  # round for DP cache reuse
    pr = price_props_mixture(pa, pb, sig_s, sig_d)
    return {
        "p20": pr["exact_20"],
        "tb": pr["tiebreak_any"],
        "etot": sum(g * q for g, q in pr["total_games_dist"].items()),
        "o215": pr["total_games_over"][21.5],
    }


def loss(emp, mod):
    L = 0.0
    L += ((emp["p20"] - mod["p20"]) / 0.01) ** 2
    L += ((emp["tb"] - mod["tb"]) / 0.01) ** 2
    L += ((emp["etot"] - mod["etot"]) / 0.25) ** 2
    L += ((emp["o215"] - mod["o215"]) / 0.01) ** 2
    return L


results = {}
for tour, surf in [("ATP", "Hard"), ("ATP", "Clay"), ("ATP", "Grass"),
                   ("WTA", "Hard"), ("WTA", "Clay"), ("WTA", "Grass")]:
    sample = [r for r in rows if r["tour"] == tour and r["surface"] == surf
              and r["best_of"] == 3 and not r["retired"]
              and r["wsets"] is not None and 12 <= r["total_games"] <= 39]
    if len(sample) < 800:
        continue
    emps = moments(sample)
    best = None
    for s0i in range(116, 137, 4):          # s0 in 1.16..1.36
        for sgs in (0.04, 0.07, 0.10, 0.13):
            for sgd in (0.02, 0.05, 0.08, 0.11):
                s0 = s0i / 100
                L = 0.0
                for emp in emps:
                    if emp is None:
                        continue
                    mod = model_moments(emp["p_fav"], s0, sgs, sgd)
                    L += loss(emp, mod)
                if best is None or L < best[0]:
                    best = (L, s0, sgs, sgd)
    L, s0, sgs, sgd = best
    results[f"{tour}|{surf}"] = {"s0": s0, "sig_s": sgs, "sig_d": sgd, "loss": L}
    print(f"{tour} {surf}: s0={s0:.2f} sig_s={sgs:.2f} sig_d={sgd:.2f} loss={L:.0f}", flush=True)
    # show fit quality on the 0.6-0.7 bucket
    emp = emps[1]
    if emp:
        mod = model_moments(emp["p_fav"], s0, sgs, sgd)
        print(f"   bucket .6-.7 emp: p20={emp['p20']:.3f} tb={emp['tb']:.3f} "
              f"etot={emp['etot']:.2f} o215={emp['o215']:.3f}")
        print(f"   bucket .6-.7 mod: p20={mod['p20']:.3f} tb={mod['tb']:.3f} "
              f"etot={mod['etot']:.2f} o215={mod['o215']:.3f}")

json.dump(results, open(f"{SC}/mixture_params.json", "w"))
print("saved mixture_params.json")
