"""Finalize the recalibrated model: refit same_player on an extended grid,
day-clustered SE for the realized zone edge, freeze the artifact."""
import gzip, json, math, sys
from collections import defaultdict

import numpy as np
from scipy.special import ndtr, ndtri

sys.path.insert(0, "/opt/p029")
from src.combo_copula import relation  # noqa: E402

leg_map = json.load(open("/tmp/p029_calib/leg_map.json"))
model = json.load(open("/tmp/p029_calib/recalibrated_model.json"))
A, B = leg_map["a"], leg_map["b"]

def cal(m):
    m = np.clip(m, 1e-4, 1 - 1e-4)
    return 1.0 / (1.0 + np.exp(-(A + B * np.log(m / (1 - m)))))

_rc = {}
def rel(a, b):
    k = (a, b)
    if k not in _rc:
        _rc[k] = relation(a, b)
    return _rc[k]

# load only same_player-pure rows + traded rows (for the SE)
sp_rows, traded_rows = [], []
with gzip.open("/tmp/p029_calib/calib.jsonl.gz", "rt") as f:
    for line in f:
        d = json.loads(line)
        k = d["k"]
        if k < 2 or k > 4:
            continue
        legs = d["legs"]
        rels = {rel(legs[i], legs[j]) for i in range(k) for j in range(i + 1, k)}
        if rels == {"same_player"}:
            sp_rows.append(d)
        if d["tr"] and d["px"] is not None:
            traded_rows.append((d["d"], d["px"], d["sv"], d["cop"]))

print(f"same_player-pure rows: {len(sp_rows)}")
GRID = np.arange(0.0, 0.955, 0.015)
NODES, WTS = np.polynomial.hermite.hermgauss(48)
U = math.sqrt(2.0) * NODES
W = WTS / math.sqrt(math.pi)

Z = np.stack([np.pad(ndtri(cal(np.array(d["m"]))), (0, 4 - d["k"]))
              for d in sp_rows])
S = np.stack([np.pad(np.array([1.0 if s == "yes" else -1.0 for s in d["sides"]]),
                     (0, 4 - d["k"])) for d in sp_rows])
K = np.array([d["k"] for d in sp_rows])
SV = np.array([d["sv"] for d in sp_rows])
DAYS = np.array([d["d"] for d in sp_rows])
active = np.arange(4)[None, :] < K[:, None]

def curve():
    out = np.empty((len(GRID), len(sp_rows)))
    for gi, r in enumerate(GRID):
        sq, sq1 = math.sqrt(r), math.sqrt(max(1e-12, 1 - r))
        arg = (Z[:, :, None] - S[:, :, None] * sq * U[None, None, :]) / sq1
        F = np.where(active[:, :, None], ndtr(arg), 1.0)
        out[gi] = np.prod(F, axis=1) @ W
    return out

pg = curve()
day_ids = sorted(set(DAYS.tolist()))
curves = np.stack([pg[:, DAYS == d].mean(axis=1) for d in day_ids])
svd = np.array([SV[DAYS == d].mean() for d in day_ids])
wts = np.array([(DAYS == d).sum() for d in day_ids], dtype=float)

def solve(w):
    t = float(np.dot(w, svd) / w.sum())
    c = (w[:, None] * curves).sum(axis=0) / w.sum()
    if t <= c[0]:
        return 0.0
    if t >= c[-1]:
        return float(GRID[-1])
    return float(np.interp(t, c, GRID))

point = solve(wts)
rng = np.random.default_rng(20260805)
boots = []
for _ in range(400):
    pick = rng.integers(0, len(day_ids), len(day_ids))
    w = np.zeros(len(day_ids))
    for i in pick:
        w[i] += wts[i]
    if w.sum():
        boots.append(solve(w))
lo, hi = np.percentile(boots, [2.5, 97.5])
print(f"same_player extended grid: rho={point:.4f}  CI[{lo:.4f},{hi:.4f}]  "
      f"days={len(day_ids)}")

model["rho"]["same_player"] = round(float(point), 4)
model["rho_meta"]["same_player"] = {
    "n": len(sp_rows), "days": len(day_ids), "ci95": [float(lo), float(hi)],
    "note": "extended grid to 0.95 (first fit pegged the 0.60 cap)"}

# realized zone edge with day-clustered SE (zone on stored copula ~= frozen-model
# zone; exact zone re-derived at gate time by offline repricing)
byday = defaultdict(list)
for day, px, sv, cop in traded_rows:
    if cop is not None and 0.10 <= cop <= 0.35:
        byday[day].append((px - sv) * 100)
dm = [np.mean(v) for v in byday.values()]
n = sum(len(v) for v in byday.values())
mean = np.mean([x for v in byday.values() for x in v])
se = np.std(dm, ddof=1) / math.sqrt(len(dm))
print(f"realized SELLER edge (print - settle), stored zone: "
      f"{mean:+.2f}c  day-clustered se {se:.2f}c  n={n}  days={len(dm)}")

model["realized_zone_seller_edge_fit_window"] = {
    "mean_c": round(float(mean), 2), "day_se_c": round(float(se), 2),
    "n": int(n), "days": len(dm)}
json.dump(model, open("/tmp/p029_calib/recalibrated_model.json", "w"), indent=1)
print("frozen: /tmp/p029_calib/recalibrated_model.json")
