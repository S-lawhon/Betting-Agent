"""Exploratory: join shadow traded rows to archived settlements.

Answers which reading of Gate 0's -2.54c is true:
  A) our copula overprices the joint  -> E[settle - copula] < 0, buyers at the print lose
  B) incumbents underprice            -> E[settle - print] > 0, buyers at the print win
Read-only everywhere. NOT a gate; no thresholds pre-registered.
"""
import glob, gzip, json, sqlite3, statistics

db = sqlite3.connect("file:/var/lib/p029/shadow.sqlite?mode=ro", uri=True)
rows = db.execute("""
  SELECT market_ticker, date(seen_ts), first_trade_px, copula_price, in_zone
  FROM combo WHERE traded=1 AND first_trade_px IS NOT NULL
             AND copula_price IS NOT NULL""").fetchall()
want = {r[0]: r for r in rows}
print(f"traded rows wanted: {len(want)}")

settled = {}
parts = sorted(glob.glob("/var/lib/p029/archive/combos_2026-0[78]-*.jsonl.gz"))
# only parts from runs on/after 07-29 can hold combos from the shadow window
parts = [p for p in parts if p.split("combos_")[1][:10] >= "2026-07-29"]
print(f"scanning {len(parts)} archive parts")
for p in parts:
    with gzip.open(p, "rt") as f:
        for line in f:
            d = json.loads(line)
            t = d.get("ticker")
            if t not in want or t in settled:
                continue
            sv = d.get("settlement_value_dollars")
            res = d.get("result")
            if sv is None:
                if res == "yes": sv = 1.0
                elif res == "no": sv = 0.0
                else: continue
            settled[t] = float(sv)
print(f"matched settlements: {len(settled)}")

def report(label, sel):
    xs_print  = [(settled[t] - want[t][2]) * 100 for t in sel]   # buyer edge vs print
    xs_copula = [(settled[t] - want[t][3]) * 100 for t in sel]   # calibration of copula
    if not xs_print:
        print(f"{label}: n=0"); return
    n = len(xs_print)
    for name, xs in (("settle-print (buyer edge)", xs_print),
                     ("settle-copula (calibration)", xs_copula)):
        m = statistics.mean(xs)
        sd = statistics.stdev(xs) if n > 1 else 0.0
        # day-clustered SE
        byday = {}
        for t, x in zip(sel, xs): byday.setdefault(want[t][1], []).append(x)
        dm = [statistics.mean(v) for v in byday.values()]
        se = (statistics.stdev(dm) / len(dm) ** 0.5) if len(dm) > 1 else float("nan")
        print(f"  {label:14s} n={n:6d}  {name:28s} mean {m:+7.2f}c  sd {sd:6.1f}  "
              f"day-clustered se {se:5.2f}  days {len(dm)}")

inz  = [t for t in settled if want[t][4] == 1]
outz = [t for t in settled if want[t][4] != 1]
report("in-zone", inz)
report("out-of-zone", outz)
report("all", list(settled))

# by day, in-zone buyer edge
byday = {}
for t in inz: byday.setdefault(want[t][1], []).append((settled[t] - want[t][2]) * 100)
print("\nin-zone buyer edge (settle - print) by seen-day:")
for d in sorted(byday):
    xs = byday[d]
    print(f"  {d}  n={len(xs):5d}  mean {statistics.mean(xs):+7.2f}c  "
          f"median {statistics.median(xs):+7.2f}c  frac>0 {sum(1 for x in xs if x>0)/len(xs):4.0%}")
