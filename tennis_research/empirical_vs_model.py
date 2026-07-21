"""Validate the point-level model against 2022-2026 empirical outcomes.

1. Fit the serve-sum parameter s = pa+pb per (tour, surface) by matching
   the empirical mean total games in Bo3 completed matches.
2. Bucket matches by devigged favorite probability; within each bucket
   compare empirical prop frequencies to model predictions at fitted s:
      P(fav wins 2-0 | fav range), P(fav wins set 1), P(any tiebreak),
      P(total games > 21.5), E[total games].
This tells us whether (model + market match line) reproduces reality —
the precondition for trusting model-vs-Kalshi price gaps.
"""
import json
import sys
from tennis_pricer import price_props, implied_holds

SNAP = sys.argv[1] if len(sys.argv) > 1 else "."
rows = json.load(open(f"{SNAP}/empirical_matches.json"))

BO3 = [r for r in rows if r["best_of"] == 3 and not r["retired"]
       and r["wsets"] is not None and r["wsets"] + (r["lsets"] or 0) <= 3
       and 12 <= r["total_games"] <= 39]
print(f"Bo3 completed matches with odds: {len(BO3)}")


def fit_s(sample, lo=1.05, hi=1.45):
    """Fit s so model E[total] averaged over the sample's match lines
    matches the empirical mean total games."""
    emp = sum(r["total_games"] for r in sample) / len(sample)
    # representative p_fav distribution: use deciles to save compute
    ps = sorted(r["p_fav"] for r in sample)
    reps = [ps[int(len(ps) * (i + 0.5) / 10)] for i in range(10)]
    def model_mean(s):
        tot = 0.0
        for p in reps:
            pa, pb = implied_holds(p, best_of=3, avg_spw=s / 2)
            pr = price_props(pa, pb)
            tot += sum(g * q for g, q in pr["total_games_dist"].items())
        return tot / len(reps)
    for _ in range(20):
        mid_s = (lo + hi) / 2
        if model_mean(mid_s) < emp:
            lo = mid_s
        else:
            hi = mid_s
    return (lo + hi) / 2, emp


segments = {}
for tour in ("ATP", "WTA"):
    for surf in ("Hard", "Clay", "Grass"):
        sample = [r for r in BO3 if r["tour"] == tour and r["surface"] == surf]
        if len(sample) < 300:
            continue
        s, emp = fit_s(sample)
        segments[(tour, surf)] = s
        print(f"{tour} {surf:<6} n={len(sample):>5}  emp E[total]={emp:.2f}  fitted s={s:.3f} (spw={s/2:.3f})")

json.dump({f"{t}|{s}": v for (t, s), v in segments.items()},
          open(f"{SNAP}/fitted_s.json", "w"))

# ---- bucket validation on the biggest segments
print("\n=== bucket validation (model @ fitted s vs empirical) ===")
for tour, surf in [("ATP", "Hard"), ("ATP", "Clay"), ("WTA", "Hard"), ("WTA", "Clay")]:
    s = segments.get((tour, surf))
    if not s:
        continue
    sample = [r for r in BO3 if r["tour"] == tour and r["surface"] == surf]
    print(f"\n{tour} {surf} (s={s:.3f}, n={len(sample)})")
    print(f"{'p_fav bucket':<14}{'n':>5} | {'P20 emp':>8}{'mod':>6} | {'set1 emp':>9}{'mod':>6} | "
          f"{'TB emp':>7}{'mod':>6} | {'o21.5 emp':>9}{'mod':>6} | {'E[tot] emp':>10}{'mod':>6}")
    for lo_b, hi_b in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.99)]:
        B = [r for r in sample if lo_b <= r["p_fav"] < hi_b]
        if len(B) < 50:
            continue
        pmid = sum(r["p_fav"] for r in B) / len(B)
        pa, pb = implied_holds(pmid, best_of=3, avg_spw=s / 2)
        pr = price_props(pa, pb)
        # empirical
        fav20 = sum(1 for r in B if r["fav_won"] and r["wsets"] == 2 and r["lsets"] == 0) / len(B)
        set1 = sum(1 for r in B if r["sets"] and ((r["sets"][0][0] > r["sets"][0][1]) == r["fav_won"])) / len(B)
        tb = sum(1 for r in B if r["any_tb"]) / len(B)
        o215 = sum(1 for r in B if r["total_games"] > 21.5) / len(B)
        etot = sum(r["total_games"] for r in B) / len(B)
        # model (fav = A)
        m20 = pr["exact_20"]
        ms1 = pr["set1_a"]
        mtb = pr["tiebreak_any"]
        mo = pr["total_games_over"][21.5]
        met = sum(g * q for g, q in pr["total_games_dist"].items())
        print(f"[{lo_b:.1f},{hi_b:.2f}) {len(B):>5} | {fav20:>8.3f}{m20:>6.3f} | {set1:>9.3f}{ms1:>6.3f} | "
              f"{tb:>7.3f}{mtb:>6.3f} | {o215:>9.3f}{mo:>6.3f} | {etot:>10.2f}{met:>6.2f}")
