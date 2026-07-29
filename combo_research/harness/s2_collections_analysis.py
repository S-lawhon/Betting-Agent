"""S2 -- Collections census analysis + the associated_events quoter schema.

Answers Q1 (census / live count / distribution) and Q2 (exact field names and
value distribution inside an associated_events entry).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from kalshi_api import load, save

NOW = datetime.now(timezone.utc)


def iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    rows = load("collections_raw.json")
    print(f"collections: {len(rows)}  unique: "
          f"{len({r['collection_ticker'] for r in rows})}\n")

    # ---- Q1: live vs expired -------------------------------------------
    live = [r for r in rows if (iso(r["close_date"]) or NOW) > NOW]
    opened = [r for r in rows if (iso(r["open_date"]) or NOW) <= NOW]
    print(f"NOW = {NOW.isoformat()}")
    print(f"live (close_date > now): {len(live)}")
    print(f"expired (close_date <= now): {len(rows)-len(live)}")
    print(f"already opened (open_date <= now): {len(opened)}")

    print("\n== by series_ticker: total / live / median n_assoc_events ==")
    bys = defaultdict(list)
    for r in rows:
        bys[r["series_ticker"]].append(r)
    livet = {r["collection_ticker"] for r in live}
    for s, rs in sorted(bys.items(), key=lambda kv: -len(kv[1])):
        ns = sorted(len(r["associated_events"]) for r in rs)
        nl = sum(1 for r in rs if r["collection_ticker"] in livet)
        print(f"  {s:32s} n={len(rs):5d} live={nl:4d} "
              f"assoc_events min/med/max={ns[0]}/{ns[len(ns)//2]}/{ns[-1]}  "
              f"total_assoc={sum(ns)}")

    # category from the live series list
    ser = {s["ticker"]: s for s in (load("series_all.json") or [])}
    print("\n== by series category ==")
    cc = Counter(ser.get(r["series_ticker"], {}).get("category", "?")
                 for r in rows)
    print(" ", dict(cc))

    print("\n== date ranges ==")
    od = sorted(x for x in (iso(r["open_date"]) for r in rows) if x)
    cd = sorted(x for x in (iso(r["close_date"]) for r in rows) if x)
    print(f"  open_date  {od[0].isoformat()} .. {od[-1].isoformat()}")
    print(f"  close_date {cd[0].isoformat()} .. {cd[-1].isoformat()}")

    print("\n== size_min / size_max ==")
    print("  size_min:", dict(Counter(r["size_min"] for r in rows)))
    print("  size_max:", dict(Counter(r["size_max"] for r in rows)))
    print("  size_max by series:",
          {s: dict(Counter(r["size_max"] for r in rs))
           for s, rs in bys.items()})

    print("\n== functional_description variants ==")
    for t, n in Counter(r["functional_description"] for r in rows).most_common():
        print(f"  [{n}] {t}")
    print("\n== description variants ==")
    for t, n in Counter(r["description"] for r in rows).most_common():
        print(f"  [{n}] {t}")

    # ---- Q2: associated_events entry schema ----------------------------
    print("\n" + "=" * 70)
    print("Q2: associated_events ENTRY SCHEMA")
    print("=" * 70)
    keycount = Counter()
    total_ae = 0
    valdist = defaultdict(Counter)
    quoter_vals = Counter()
    quoter_nonempty = []
    for r in rows:
        for ae in r["associated_events"]:
            total_ae += 1
            keycount.update(ae.keys())
            for k, v in ae.items():
                if k == "ticker":
                    continue
                if k == "active_quoters":
                    quoter_vals[json.dumps(v)] += 1
                    if v:
                        quoter_nonempty.append((r["collection_ticker"], ae))
                else:
                    valdist[k][json.dumps(v)] += 1
    print(f"\ntotal associated_events entries across all collections: {total_ae}")
    print("key presence (count / total):")
    for k, v in keycount.most_common():
        print(f"  {k}: {v}/{total_ae}  ({100*v/total_ae:.1f}%)")
    print("\nvalue distributions:")
    for k, c in valdist.items():
        print(f"  {k}: {dict(c.most_common(10))}")
    print(f"\n  active_quoters: {dict(quoter_vals.most_common(10))}")
    print(f"  entries with NON-EMPTY active_quoters: {len(quoter_nonempty)}")
    for ct, ae in quoter_nonempty[:20]:
        print(f"    {ct} -> {ae}")

    # verbatim dump of one entry + the collection it belongs to
    ex = rows[2]
    save("q2_example_collection.json", ex)
    print("\n-- VERBATIM one associated_events entry (with size_max present):")
    for ae in ex["associated_events"]:
        if "size_max" in ae:
            print(json.dumps(ae, indent=2, sort_keys=True))
            break
    print("-- VERBATIM one entry WITHOUT size_max:")
    for ae in ex["associated_events"]:
        if "size_max" not in ae:
            print(json.dumps(ae, indent=2, sort_keys=True))
            break

    # is size_max presence correlated with is_yes_only?
    print("\n-- size_max presence x is_yes_only --")
    x = Counter()
    for r in rows:
        for ae in r["associated_events"]:
            x[("size_max" in ae, ae.get("is_yes_only"))] += 1
    for kk, v in x.most_common():
        print(f"  has_size_max={kk[0]} is_yes_only={kk[1]}: {v}")

    # unique underlying events referenced
    ev = set()
    for r in rows:
        ev.update(r["associated_event_tickers"])
    print(f"\nunique underlying events referenced by any collection: {len(ev)}")
    pre = Counter(e.split("-")[0] for e in ev)
    print("  by event series prefix (top 30):", dict(pre.most_common(30)))

    save("collections_live.json",
         [r["collection_ticker"] for r in live])


if __name__ == "__main__":
    main()
