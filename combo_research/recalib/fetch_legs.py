"""Fetch settled YES/NO outcomes for the unique legs in the calib dataset.

?tickers= (plural CSV) batched by CHARACTER budget (~3500); the singular
?ticker= is silently ignored. scalar / empty results are dropped (not binary /
not yet settled). Writes /tmp/p029_calib/leg_outcomes.json.
"""
import json, time, requests

API = "https://api.elections.kalshi.com/trade-api/v2"
s = requests.Session()
s.headers.update({"Accept": "application/json", "User-Agent": "p029-recalib/1.0"})

legs = [l.strip() for l in open("/tmp/p029_calib/legs.txt") if l.strip()]
print(f"legs to fetch: {len(legs)}", flush=True)

out, batch, budget, calls = {}, [], 0, 0
last = 0.0

def flush():
    global batch, budget, calls, last
    if not batch:
        return
    for attempt in range(5):
        dt = time.monotonic() - last
        if dt < 0.15:
            time.sleep(0.15 - dt)
        last = time.monotonic()
        try:
            r = s.get(f"{API}/markets",
                      params={"tickers": ",".join(batch), "limit": 1000},
                      timeout=30)
            calls += 1
        except requests.RequestException:
            time.sleep(1.5 * 2 ** attempt)
            continue
        if r.status_code == 200:
            for m in (r.json().get("markets") or []):
                res = (m.get("result") or "").lower()
                if res == "yes":
                    out[m["ticker"]] = 1
                elif res == "no":
                    out[m["ticker"]] = 0
            break
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * 2 ** attempt)
            continue
        print(f"HTTP {r.status_code}; dropping batch of {len(batch)}", flush=True)
        break
    batch, budget = [], 0

for t in legs:
    if budget + len(t) + 1 > 3500:
        flush()
    batch.append(t)
    budget += len(t) + 1
flush()

print(f"calls: {calls}  outcomes: {len(out)}  "
      f"coverage {len(out)/len(legs):.1%}", flush=True)
with open("/tmp/p029_calib/leg_outcomes.json", "w") as f:
    json.dump(out, f)
