"""Leg-level calibration: do side-adjusted leg mids predict leg outcomes?

Unit of observation = unique (leg, side) — a leg repeated across thousands of
combos is one bet on one game, not thousands of observations. Mark = median of
its observed side-adjusted mids. Day-clustered by the leg's embedded event day.
"""
import gzip, json, statistics
from collections import defaultdict

outcomes = json.load(open("/tmp/p029_calib/leg_outcomes.json"))

marks = defaultdict(list)   # (leg, side) -> [side-adjusted mids]
with gzip.open("/tmp/p029_calib/calib.jsonl.gz", "rt") as f:
    for line in f:
        d = json.loads(line)
        for lg, sd, m in zip(d["legs"], d["sides"], d["m"]):
            marks[(lg, sd)].append(m)

def day_of(t):
    parts = t.split("-")
    return parts[1][:7] if len(parts) >= 2 and len(parts[1]) >= 7 else "unk"

rows = []   # (mark, pays, day)
for (lg, sd), ms in marks.items():
    if lg not in outcomes:
        continue
    pays = outcomes[lg] if sd == "yes" else 1 - outcomes[lg]
    rows.append((statistics.median(ms), pays, day_of(lg)))
print(f"unique (leg, side) with outcome: {len(rows)}")

def cluster_se(pairs):  # [(gap, day)]
    byday = defaultdict(list)
    for g, d in pairs:
        byday[d].append(g)
    dm = [statistics.mean(v) for v in byday.values()]
    if len(dm) < 2:
        return float("nan"), len(dm)
    return statistics.stdev(dm) / len(dm) ** 0.5, len(dm)

gaps = [(p - m, d) for m, p, d in rows]
se, nd = cluster_se(gaps)
print(f"OVERALL  E[pays - mark] = {statistics.mean(g for g, _ in gaps)*100:+.2f}c"
      f"  (day-clustered se {se*100:.2f}c, {nd} event days, n={len(gaps)})")

print(f"\n{'mark bucket':14s} {'n':>6s} {'mean mark':>10s} {'freq pays':>10s} {'gap':>8s} {'se':>6s}")
for lo in [i / 10 for i in range(10)]:
    hi = lo + 0.1
    sel = [(m, p, d) for m, p, d in rows if lo <= m < hi]
    if len(sel) < 30:
        continue
    mm = statistics.mean(m for m, _, _ in sel)
    fp = statistics.mean(p for _, p, _ in sel)
    se, nd = cluster_se([(p - m, d) for m, p, d in sel])
    print(f"[{lo:.1f},{hi:.1f})    {len(sel):6d} {mm:10.3f} {fp:10.3f} "
          f"{(fp-mm)*100:+7.2f}c {se*100:5.2f}c")

# by leg series family (first ticker segment) — is bias concentrated?
fam = defaultdict(list)
for m, p, d in rows:
    pass
famrows = defaultdict(list)
for (lg, sd), ms in marks.items():
    if lg not in outcomes:
        continue
    pays = outcomes[lg] if sd == "yes" else 1 - outcomes[lg]
    famrows[lg.split("-")[0]].append((statistics.median(ms), pays, day_of(lg)))
print(f"\n{'series family':22s} {'n':>6s} {'gap':>8s} {'se':>6s}")
for k in sorted(famrows, key=lambda k: -len(famrows[k]))[:12]:
    sel = famrows[k]
    if len(sel) < 50:
        continue
    se, nd = cluster_se([(p - m, d) for m, p, d in sel])
    g = statistics.mean(p - m for m, p, _ in sel)
    print(f"{k:22s} {len(sel):6d} {g*100:+7.2f}c {se*100:5.2f}c")
