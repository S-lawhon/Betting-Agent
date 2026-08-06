"""Fit the leg marginal calibration map: logit(p_true) = a + b*logit(mid).

- one_sided snapshots checked as an artifact control (mid of a one-sided book
  is junk); the map is fit on two-sided observations only.
- TRAIN = leg event days <= 2026-08-01 (ticker-embedded date), TEST = later.
- Unit = unique (leg, side); mark = median two-sided side-adjusted mid.
Writes /tmp/p029_calib/leg_map.json.
"""
import gzip, json, math, sqlite3, statistics
from collections import defaultdict

import numpy as np

outcomes = json.load(open("/tmp/p029_calib/leg_outcomes.json"))

# rebuild leg observations keeping the one_sided flag per snapshot
obs = defaultdict(lambda: {"two": [], "one": []})
db = sqlite3.connect("file:/var/lib/p029/shadow.sqlite?mode=ro", uri=True)
cur = db.execute("SELECT leg_marks_json FROM combo WHERE leg_marks_json IS NOT NULL")
for (mj,) in cur:
    for m in json.loads(mj):
        mid = m.get("yes_mid")
        if mid is None:
            continue
        p = mid if m.get("side") == "yes" else 1.0 - mid
        key = (m["ticker"], m.get("side"))
        obs[key]["one" if m.get("one_sided") else "two"].append(p)

MONTH = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
         "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

def day_of(t):
    parts = t.split("-")
    if len(parts) < 2 or len(parts[1]) < 7:
        return None
    d = parts[1][:7]  # e.g. 26AUG04
    try:
        return f"20{d[:2]}-{MONTH[d[2:5]]:02d}-{int(d[5:7]):02d}"
    except (KeyError, ValueError):
        return None

rows = []  # (mark, pays, day, had_two_sided)
n_one_only = 0
for (lg, sd), d in obs.items():
    if lg not in outcomes:
        continue
    pays = outcomes[lg] if sd == "yes" else 1 - outcomes[lg]
    day = day_of(lg)
    if day is None:
        continue
    if d["two"]:
        rows.append((statistics.median(d["two"]), pays, day, True))
    elif d["one"]:
        rows.append((statistics.median(d["one"]), pays, day, False))
        n_one_only += 1

two = [(m, p, d) for m, p, d, t in rows if t]
one = [(m, p, d) for m, p, d, t in rows if not t]
def gap_se(sel):
    byday = defaultdict(list)
    for m, p, d in sel:
        byday[d].append(p - m)
    dm = [statistics.mean(v) for v in byday.values()]
    g = statistics.mean(p - m for m, p, _ in sel)
    se = statistics.stdev(dm) / len(dm) ** 0.5 if len(dm) > 1 else float("nan")
    return g, se, len(dm)

for label, sel in (("two-sided", two), ("one-sided-only", one)):
    if sel:
        g, se, nd = gap_se(sel)
        print(f"{label:16s} n={len(sel):6d}  gap {g*100:+.2f}c  se {se*100:.2f}c  days {nd}")

TRAIN_END = "2026-08-01"
train = [(m, p) for m, p, d in two if d <= TRAIN_END]
test  = [(m, p, d) for m, p, d in two if d > TRAIN_END]
print(f"train n={len(train)}  test n={len(test)}")

# logistic fit on train: maximize Bernoulli log-lik of p = sigmoid(a + b*logit(m))
eps = 1e-4
X = np.array([math.log(min(max(m, eps), 1 - eps) / (1 - min(max(m, eps), 1 - eps)))
              for m, _ in train])
Y = np.array([p for _, p in train], dtype=float)
a, b = 0.0, 1.0
for _ in range(200):                       # Newton-Raphson, 2 params
    z = a + b * X
    p = 1.0 / (1.0 + np.exp(-z))
    W = p * (1 - p)
    g0, g1 = np.sum(Y - p), np.sum((Y - p) * X)
    h00, h01, h11 = np.sum(W), np.sum(W * X), np.sum(W * X * X)
    det = h00 * h11 - h01 * h01
    if det <= 0:
        break
    da = (h11 * g0 - h01 * g1) / det
    db = (-h01 * g0 + h00 * g1) / det
    a, b = a + da, b + db
    if abs(da) < 1e-10 and abs(db) < 1e-10:
        break
print(f"\nfitted map: logit(p) = {a:+.4f} {b:+.4f} * logit(mid)")

def cal(m):
    m = min(max(m, eps), 1 - eps)
    z = a + b * math.log(m / (1 - m))
    return 1.0 / (1.0 + math.exp(-z))

print(f"\nTEST-day calibration (days > {TRAIN_END}):")
print(f"{'bucket':12s} {'n':>6s} {'raw gap':>9s} {'mapped gap':>11s}")
for lo in [i / 10 for i in range(10)]:
    sel = [(m, p) for m, p, _ in test if lo <= m < lo + 0.1]
    if len(sel) < 30:
        continue
    raw = statistics.mean(p - m for m, p in sel)
    mapped = statistics.mean(p - cal(m) for m, p in sel)
    print(f"[{lo:.1f},{lo+.1:.1f})  {len(sel):6d} {raw*100:+8.2f}c {mapped*100:+10.2f}c")
raw = statistics.mean(p - m for m, p, _ in test)
mapped = statistics.mean(p - cal(m) for m, p, _ in test)
print(f"{'ALL TEST':12s} {len(test):6d} {raw*100:+8.2f}c {mapped*100:+10.2f}c")

json.dump({"a": round(a, 6), "b": round(b, 6),
           "form": "logit(p_cal) = a + b*logit(mid_side_adjusted)",
           "fit_on": f"two-sided unique (leg,side), event days <= {TRAIN_END}",
           "n_train": len(train), "fitted": "2026-08-05"},
          open("/tmp/p029_calib/leg_map.json", "w"), indent=1)
print("\nwrote /tmp/p029_calib/leg_map.json")
