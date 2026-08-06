"""P-029 recalibration: refit rho on calibrated marginals with side-signed copula.

Model under fit ("recalibrated"):
  q_i  = sigmoid(a + b * logit(side-adjusted mid))          [leg map, already fit]
  corr = s_i * s_j * rho[relation(i,j)],  s = +1 YES / -1 NO [sign fix]
  P(all pay) = Gaussian copula orthant
Pure-relation combos are one-factor (loadings s_i*sqrt(rho)) -> exact
Gauss-Hermite quadrature. Mixed combos priced by MC (common random numbers,
antithetic) for validation only; the fit uses pure-class rows.

Fit: per relation class, rho solves mean(price(rho)) = mean(settle) on TRAIN
seen-days (<= 2026-08-01). Validation: incremental attribution A->D on TEST
seen-days. Final shipped rho: all-days fit, day-clustered bootstrap CI via
per-day price curves on a rho grid (day resampling = reweighting the curves).
"""
import gzip, json, math, sys
from collections import defaultdict

import numpy as np
from scipy.special import ndtr, ndtri

sys.path.insert(0, "/opt/p029")
from src.combo_copula import relation  # noqa: E402

TRAIN_END = "2026-08-01"
RHO_GRID = np.arange(0.0, 0.615, 0.015)
OLD_RHO = {"same_player": 0.35, "same_game": 0.2084,
           "same_day": 0.1159, "cross": 0.1065}
CLS = ["cross", "same_day", "same_game", "same_player"]
CLS_ID = {c: i for i, c in enumerate(CLS)}
MIXED = 255

leg_map = json.load(open("/tmp/p029_calib/leg_map.json"))
A, B = leg_map["a"], leg_map["b"]

def cal(m):
    m = np.clip(m, 1e-4, 1 - 1e-4)
    z = A + B * np.log(m / (1 - m))
    return 1.0 / (1.0 + np.exp(-z))

# ---------------- load ----------------
_rel_cache = {}
def rel(a, b):
    k = (a, b)
    r = _rel_cache.get(k)
    if r is None:
        r = relation(a, b)
        _rel_cache[k] = r
    return r

rows_k, rows_q, rows_s, rows_cls, rows_day = [], [], [], [], []
rows_sv, rows_px, rows_tr, rows_cop, rows_indep, rows_pairs = [], [], [], [], [], []
days_seen = {}
with gzip.open("/tmp/p029_calib/calib.jsonl.gz", "rt") as f:
    for line in f:
        d = json.loads(line)
        k = d["k"]
        if k < 2 or k > 4:
            continue
        legs = d["legs"]
        rels = [rel(legs[i], legs[j]) for i in range(k) for j in range(i + 1, k)]
        u = set(rels)
        cls = CLS_ID[rels[0]] if len(u) == 1 else MIXED
        day = d["d"]
        if day not in days_seen:
            days_seen[day] = len(days_seen)
        rows_k.append(k)
        rows_q.append(d["m"] + [0.0] * (4 - k))
        rows_s.append([1.0 if s == "yes" else -1.0 for s in d["sides"]]
                      + [0.0] * (4 - k))
        rows_cls.append(cls)
        rows_day.append(days_seen[day])
        rows_sv.append(d["sv"])
        rows_px.append(d["px"] if d["px"] is not None else np.nan)
        rows_tr.append(d["tr"])
        rows_cop.append(d["cop"] if d["cop"] is not None else np.nan)
        rows_indep.append(d["indep"])
        rows_pairs.append([CLS_ID[r] for r in rels] + [0] * (6 - len(rels)))

K = np.array(rows_k, dtype=np.int8)
M = np.array(rows_q, dtype=np.float64)          # raw side-adjusted mids
S = np.array(rows_s, dtype=np.float64)
C = np.array(rows_cls, dtype=np.int16)
DAY = np.array(rows_day, dtype=np.int16)
SV = np.array(rows_sv, dtype=np.float64)
PX = np.array(rows_px, dtype=np.float64)
TR = np.array(rows_tr, dtype=np.int8)
COP = np.array(rows_cop, dtype=np.float64)
INDEP = np.array(rows_indep, dtype=np.float64)
PAIRS = np.array(rows_pairs, dtype=np.int8)
del rows_k, rows_q, rows_s, rows_cls, rows_day, rows_sv, rows_px, rows_tr
del rows_cop, rows_indep, rows_pairs

day_names = sorted(days_seen, key=days_seen.get)
train_mask = np.array([day_names[i] <= TRAIN_END for i in DAY])
Q = cal(M)
Q = np.where(np.arange(4)[None, :] < K[:, None], np.clip(Q, 1e-4, 1 - 1e-4), 0.5)
Zthr = ndtri(np.where(np.arange(4)[None, :] < K[:, None], Q, 0.5))

n = len(K)
print(f"rows {n}  train {train_mask.sum()}  test {(~train_mask).sum()}  days {len(day_names)}")
for c in CLS + ["mixed"]:
    cid = MIXED if c == "mixed" else CLS_ID[c]
    sel = C == cid
    print(f"  {c:12s} n={sel.sum():8d}  days {len(set(DAY[sel].tolist())):2d}  "
          f"traded {int(TR[sel].sum()):6d}")

# ---------------- quadrature price on the rho grid (pure classes) ----------
NODES, WTS = np.polynomial.hermite.hermgauss(48)
U = math.sqrt(2.0) * NODES                     # integration variable
W = WTS / math.sqrt(math.pi)

def price_grid(sel):
    """Prices for rows sel (pure class) at every rho in RHO_GRID -> (len(grid), n_sel)."""
    z = Zthr[sel]; s = S[sel]; k = K[sel]
    active = np.arange(4)[None, :] < k[:, None]
    out = np.empty((len(RHO_GRID), sel.sum()))
    for gi, r in enumerate(RHO_GRID):
        sq, sq1 = math.sqrt(r), math.sqrt(max(1e-12, 1 - r))
        # arg: (n, 4, nodes)
        arg = (z[:, :, None] - s[:, :, None] * sq * U[None, None, :]) / sq1
        F = np.where(active[:, :, None], ndtr(arg), 1.0)
        out[gi] = np.prod(F, axis=1) @ W
    return out

def fit_rho(cid, mask_extra, label):
    sel = (C == cid) & mask_extra
    ns = int(sel.sum())
    days = sorted(set(DAY[sel].tolist()))
    if ns < 200 or len(days) < 3:
        print(f"  {label}: UNIDENTIFIED (n={ns}, days={len(days)})")
        return None, ns, len(days), None
    pg = price_grid(sel)                        # (grid, ns)
    dsel = DAY[sel]
    day_ids = sorted(set(dsel.tolist()))
    curves = np.stack([pg[:, dsel == d].mean(axis=1) for d in day_ids])   # (days, grid)
    svd = np.array([SV[sel][dsel == d].mean() for d in day_ids])
    wts = np.array([(dsel == d).sum() for d in day_ids], dtype=float)

    def solve(w):
        target = float(np.dot(w, svd) / w.sum())
        curve = (w[:, None] * curves).sum(axis=0) / w.sum()               # (grid,)
        if target <= curve[0]:
            return 0.0
        if target >= curve[-1]:
            return float(RHO_GRID[-1])
        return float(np.interp(target, curve, RHO_GRID))

    point = solve(wts)
    rng = np.random.default_rng(20260805)
    boots = []
    for _ in range(400):
        pick = rng.integers(0, len(day_ids), len(day_ids))
        w = np.zeros(len(day_ids))
        for i in pick:
            w[i] += wts[i]
        if w.sum() == 0:
            continue
        boots.append(solve(w))
    lo, hi = (np.percentile(boots, [2.5, 97.5]) if boots else (None, None))
    print(f"  {label}: rho={point:.4f}  CI[{lo:.4f},{hi:.4f}]  n={ns} days={len(days)}")
    return point, ns, len(days), (float(lo), float(hi))

print("\n=== rho refit (calibrated marginals, sign-corrected), TRAIN days ===")
rho_train = {}
for c in CLS:
    r, *_ = fit_rho(CLS_ID[c], train_mask, c)
    rho_train[c] = r
print("=== rho refit, ALL days (final candidate) ===")
rho_final, rho_meta = {}, {}
for c in CLS:
    r, ns, nd, ci = fit_rho(CLS_ID[c], np.ones(n, bool), c)
    rho_final[c] = r
    rho_meta[c] = {"n": ns, "days": nd, "ci95": ci}

# fall back to old priors where unidentified
for c in CLS:
    if rho_final[c] is None:
        rho_final[c] = OLD_RHO[c]
        rho_meta[c]["fallback"] = "old value kept; block unidentified"
    if rho_train[c] is None:
        rho_train[c] = rho_final[c]

# ---------------- MC pricer for any row (mixed matrices) ----------------
rng = np.random.default_rng(20260729)
half = 4000
_zmc = rng.standard_normal((half, 4))
ZMC = np.vstack([_zmc, -_zmc])                  # antithetic, 8000 draws

PAIR_IDX = {2: [(0, 1)], 3: [(0, 1), (0, 2), (1, 2)],
            4: [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]}

def price_mc(idx, rho_dict, use_cal, signed):
    marg = cal(M[idx]) if use_cal else M[idx]
    out = np.empty(len(idx))
    rv = np.array([rho_dict[c] for c in CLS])
    for oi, i in enumerate(idx):
        k = int(K[i])
        q = np.clip(marg[oi, :k], 1e-4, 1 - 1e-4)
        z = ndtri(q)
        corr = np.eye(k)
        for pi, (a, b) in enumerate(PAIR_IDX[k]):
            r = rv[PAIRS[i, pi]]
            if signed:
                r *= S[i, a] * S[i, b]
            corr[a, b] = corr[b, a] = r
        try:
            L = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            w, V = np.linalg.eigh(corr)
            corr2 = V @ np.diag(np.clip(w, 1e-6, None)) @ V.T
            dd = np.sqrt(np.diag(corr2))
            L = np.linalg.cholesky(corr2 / np.outer(dd, dd))
        draws = ZMC[:, :k] @ L.T
        out[oi] = np.all(draws < z, axis=1).mean()
    return out

# validation subsample on TEST days: all traded + up to 60k untraded
test_idx = np.where(~train_mask)[0]
tr_idx = test_idx[TR[test_idx] == 1]
untr = test_idx[TR[test_idx] == 0]
rng2 = np.random.default_rng(7)
untr_s = rng2.choice(untr, size=min(60000, len(untr)), replace=False)
vidx = np.concatenate([tr_idx, untr_s])
print(f"\nvalidation subsample (TEST days): {len(vidx)} rows "
      f"({len(tr_idx)} traded)")

def clus(err, idx):
    byday = defaultdict(list)
    for e, d in zip(err, DAY[idx]):
        byday[d].append(e)
    dm = [np.mean(v) for v in byday.values()]
    return (np.std(dm, ddof=1) / math.sqrt(len(dm))) if len(dm) > 1 else float("nan")

print("\n=== attribution on TEST days: E[settle - price] (cents) ===")
pA = COP[vidx]
ok = ~np.isnan(pA)
eA = (SV[vidx] - pA)[ok]
print(f"  A stored copula (mids, old rho, unsigned): {eA.mean()*100:+.2f}c  "
      f"se {clus(eA, vidx[ok])*100:.2f}  n={ok.sum()}")
pB = price_mc(vidx, OLD_RHO, use_cal=False, signed=True)
eB = SV[vidx] - pB
print(f"  B + side-sign fix:                        {eB.mean()*100:+.2f}c  "
      f"se {clus(eB, vidx)*100:.2f}")
pC = price_mc(vidx, OLD_RHO, use_cal=True, signed=True)
eC = SV[vidx] - pC
print(f"  C + leg calibration map:                  {eC.mean()*100:+.2f}c  "
      f"se {clus(eC, vidx)*100:.2f}")
pD = price_mc(vidx, rho_train, use_cal=True, signed=True)
eD = SV[vidx] - pD
print(f"  D + refit rho (train-day fit):            {eD.mean()*100:+.2f}c  "
      f"se {clus(eD, vidx)*100:.2f}")

print("\nD residuals by class (TEST):")
for c in CLS + ["mixed"]:
    cid = MIXED if c == "mixed" else CLS_ID[c]
    m = C[vidx] == cid
    if m.sum() < 50:
        continue
    print(f"  {c:12s} n={m.sum():6d}  {eD[m].mean()*100:+.2f}c  "
          f"se {clus(eD[m], vidx[m])*100:.2f}")
print("D residuals by price bucket (TEST):")
for lo in (0.0, 0.05, 0.10, 0.20, 0.35, 0.60):
    hi = {0.0: 0.05, 0.05: 0.10, 0.10: 0.20, 0.20: 0.35, 0.35: 0.60, 0.60: 1.0}[lo]
    m = (pD >= lo) & (pD < hi)
    if m.sum() < 50:
        continue
    print(f"  [{lo:.2f},{hi:.2f}) n={m.sum():6d}  {eD[m].mean()*100:+.2f}c  "
          f"se {clus(eD[m], vidx[m])*100:.2f}")

# ---------------- gate margin re-read under the final model ---------------
tr_all = np.where((TR == 1) & ~np.isnan(PX))[0]
pF = price_mc(tr_all, rho_final, use_cal=True, signed=True)
margin = (PX[tr_all] - pF) * 100
zone = (pF >= 0.10) & (pF <= 0.35)
print(f"\n=== traded-row margin (px - recalibrated fair), ALL days ===")
print(f"  all traded    n={len(tr_all):6d}  median {np.median(margin):+.2f}c  "
      f"mean {margin.mean():+.2f}c  frac>0 {(margin>0).mean():.0%}")
print(f"  uniform zone on recalibrated price:")
print(f"  in-zone       n={int(zone.sum()):6d}  median {np.median(margin[zone]):+.2f}c  "
      f"mean {margin[zone].mean():+.2f}c  frac>0 {(margin[zone]>0).mean():.0%}")
byday = defaultdict(list)
for mg, d in zip(margin[zone], DAY[tr_all][zone]):
    byday[d].append(mg)
for d in sorted(byday):
    xs = byday[d]
    print(f"    {day_names[d]}  n={len(xs):5d}  median {np.median(xs):+.2f}c  "
          f"mean {np.mean(xs):+.2f}c")
# realised buyer edge on those fills, sanity
be = (SV[tr_all] - PX[tr_all]) * 100
print(f"  buyer edge at print, zone rows: mean {be[zone].mean():+.2f}c")

json.dump({
    "leg_map": leg_map,
    "rho": {c: round(float(rho_final[c]), 4) for c in CLS},
    "rho_meta": rho_meta,
    "rho_train_days": {c: (None if rho_train[c] is None else round(float(rho_train[c]), 4))
                       for c in CLS},
    "sign_correction": "corr_ij = s_i*s_j*rho[relation]; s=+1 yes / -1 no",
    "fitted": "2026-08-05",
    "window": [day_names[0], day_names[-1]],
    "estimator": "moment match E[price]=E[settle] per pure-relation class, "
                 "Gauss-Hermite one-factor; day-clustered bootstrap CI",
}, open("/tmp/p029_calib/recalibrated_model.json", "w"), indent=1)
print("\nwrote /tmp/p029_calib/recalibrated_model.json")
