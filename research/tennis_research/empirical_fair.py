"""Empirical fair-price curves for tennis props, conditioned on the match line.

For each prop we fit a logistic regression in x = logit(p_fav) on
2022-2026 completed Bo3 matches (per tour, optionally per surface):

    P(prop | p_fav) = sigmoid(b0 + b1*x + b2*x^2)

Props supported (from the favorite's perspective):
    fav_20      favorite wins 2-0
    fav_21      favorite wins 2-1
    dog_21      underdog wins 2-1
    dog_20      underdog wins 2-0
    fav_set1    favorite wins set 1
    any_tb      at least one tiebreak in the match
    over_L      total games > L for L in 18.5..26.5
    fav_sp_L    favorite wins by more than L games, L in 2.5..7.5

This is the model-free benchmark: no serve model, just 23k matches of
"given the closing match line, how often does each prop actually hit."
"""
import json
import math
import sys

SNAP = sys.argv[1] if len(sys.argv) > 1 else "."


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(z):
    return 1 / (1 + math.exp(-z))


def fit_logistic(xs, ys, l2=1e-4, iters=200):
    """Simple Newton-ish gradient fit of y ~ sigmoid(b0+b1 x+b2 x^2)."""
    b = [0.0, 0.0, 0.0]
    n = len(xs)
    lr = 0.5
    for it in range(iters):
        g = [0.0, 0.0, 0.0]
        for x, y in zip(xs, ys):
            z = b[0] + b[1] * x + b[2] * x * x
            e = sigmoid(z) - y
            g[0] += e
            g[1] += e * x
            g[2] += e * x * x
        for j in range(3):
            b[j] -= lr * (g[j] / n + l2 * b[j])
    return b


def prop_outcomes(r):
    """Return dict of 0/1 outcomes for each prop, favorite perspective."""
    out = {}
    fav = r["fav_won"]
    ws, ls = r["wsets"], r["lsets"]
    out["fav_20"] = 1 if (fav and ws == 2 and ls == 0) else 0
    out["fav_21"] = 1 if (fav and ws == 2 and ls == 1) else 0
    out["dog_21"] = 1 if ((not fav) and ws == 2 and ls == 1) else 0
    out["dog_20"] = 1 if ((not fav) and ws == 2 and ls == 0) else 0
    if r["sets"]:
        out["fav_set1"] = 1 if ((r["sets"][0][0] > r["sets"][0][1]) == fav) else 0
    out["any_tb"] = 1 if r["any_tb"] else 0
    for L in (18.5, 19.5, 20.5, 21.5, 22.5, 23.5, 24.5, 25.5, 26.5):
        out[f"over_{L}"] = 1 if r["total_games"] > L else 0
    mfav = r["margin_winner"] if fav else -r["margin_winner"]
    for L in (2.5, 3.5, 4.5, 5.5, 6.5, 7.5):
        out[f"fav_sp_{L}"] = 1 if mfav > L else 0
    return out


def build(rows, tour, surfaces=None):
    sample = [r for r in rows
              if r["tour"] == tour and r["best_of"] == 3 and not r["retired"]
              and r["wsets"] is not None and 12 <= r["total_games"] <= 39
              and (surfaces is None or r["surface"] in surfaces)]
    xs = [logit(r["p_fav"]) for r in sample]
    curves = {}
    keys = set()
    outs = [prop_outcomes(r) for r in sample]
    for o in outs:
        keys |= set(o.keys())
    for k in sorted(keys):
        pairs = [(x, o[k]) for x, o in zip(xs, outs) if k in o]
        b = fit_logistic([p[0] for p in pairs], [p[1] for p in pairs])
        curves[k] = b
    return curves, len(sample)


def fair(curves, prop, p_fav):
    b = curves[prop]
    x = logit(p_fav)
    return sigmoid(b[0] + b[1] * x + b[2] * x * x)


if __name__ == "__main__":
    rows = json.load(open(f"{SNAP}/empirical_matches.json"))
    out = {}
    for tour in ("ATP", "WTA"):
        for name, surfs in [("all", None), ("clay", ["Clay"]),
                            ("hard", ["Hard"]), ("grass", ["Grass"])]:
            curves, n = build(rows, tour, surfs)
            out[f"{tour}|{name}"] = {"n": n, "curves": curves}
            print(f"{tour}|{name}: n={n}")
    json.dump(out, open(f"{SNAP}/empirical_curves.json", "w"))

    # sanity: print fair curve values vs raw buckets for a few props
    curves = out["ATP|clay"]["curves"]
    print("\nATP clay fair curves (favorite prob -> prop fair):")
    hdr = ["p_fav", "fav_20", "fav_21", "dog_21", "fav_set1", "any_tb",
           "over_21.5", "over_22.5", "fav_sp_3.5"]
    print("".join(f"{h:>10}" for h in hdr))
    for pf in (0.55, 0.65, 0.75, 0.85, 0.93):
        vals = [fair(curves, k, pf) for k in hdr[1:]]
        print(f"{pf:>10.2f}" + "".join(f"{v:>10.3f}" for v in vals))
