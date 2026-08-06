"""Build the P-029 recalibration dataset on-box.

Joins EVERY shadow row (traded or not — trade-selection is itself a bias) to
archived combo settlements. Emits:
  /tmp/p029_calib/calib.jsonl.gz   one row per settled combo with stored marks
  /tmp/p029_calib/legs.txt         unique leg tickers (for outcome fetch)
Read-only on shadow.sqlite and the archive.
"""
import glob, gzip, json, os, sqlite3

os.makedirs("/tmp/p029_calib", exist_ok=True)

db = sqlite3.connect("file:/var/lib/p029/shadow.sqlite?mode=ro", uri=True)
want = {}
cur = db.execute("""
  SELECT market_ticker, date(seen_ts), n_legs, legs_json, leg_marks_json,
         indep_price, copula_price, traded, first_trade_px, in_zone
  FROM combo WHERE leg_marks_json IS NOT NULL AND indep_price IS NOT NULL""")
n_shadow = 0
tickers = set()
for r in cur:
    tickers.add(r[0])
    n_shadow += 1
print(f"shadow rows with marks: {n_shadow}", flush=True)

settled = {}
parts = sorted(glob.glob("/var/lib/p029/archive/combos_2026-0[78]-*.jsonl.gz"))
parts = [p for p in parts if p.split("combos_")[1][:10] >= "2026-07-29"]
print(f"scanning {len(parts)} archive parts", flush=True)
for i, p in enumerate(parts):
    if i % 400 == 0:
        print(f"  part {i}/{len(parts)}, matched {len(settled)}", flush=True)
    with gzip.open(p, "rt") as f:
        for line in f:
            d = json.loads(line)
            t = d.get("ticker")
            if t not in tickers or t in settled:
                continue
            sv = d.get("settlement_value_dollars")
            res = d.get("result")
            if sv is not None:
                settled[t] = float(sv)
            elif res == "yes":
                settled[t] = 1.0
            elif res == "no":
                settled[t] = 0.0
print(f"matched settlements: {len(settled)}", flush=True)

legs_seen = set()
n_out = 0
cur = db.execute("""
  SELECT market_ticker, date(seen_ts), n_legs, legs_json, leg_marks_json,
         indep_price, copula_price, traded, first_trade_px, in_zone
  FROM combo WHERE leg_marks_json IS NOT NULL AND indep_price IS NOT NULL""")
with gzip.open("/tmp/p029_calib/calib.jsonl.gz", "wt") as out:
    for (t, day, n_legs, legs_json, marks_json, indep, cop,
         traded, ftpx, in_zone) in cur:
        if t not in settled:
            continue
        legs = json.loads(legs_json)
        marks = json.loads(marks_json)
        # side-adjusted marginal per leg: p(leg pays) = mid if side yes else 1-mid
        ms = []
        ok = True
        for m in marks:
            mid = m.get("yes_mid")
            if mid is None:
                ok = False
                break
            ms.append(round(mid if m.get("side") == "yes" else 1.0 - mid, 4))
        if not ok or len(ms) != n_legs:
            continue
        for lg, _side in legs:
            legs_seen.add(lg)
        out.write(json.dumps({
            "t": t, "d": day, "k": n_legs,
            "legs": [lg for lg, _ in legs],
            "sides": [s for _, s in legs],
            "m": ms,
            "indep": indep, "cop": cop,
            "tr": traded, "px": ftpx, "z": in_zone,
            "sv": settled[t],
        }) + "\n")
        n_out += 1
print(f"calib rows written: {n_out}", flush=True)

with open("/tmp/p029_calib/legs.txt", "w") as f:
    for lg in sorted(legs_seen):
        f.write(lg + "\n")
print(f"unique legs: {len(legs_seen)}", flush=True)
