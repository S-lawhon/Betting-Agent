"""P-027 step 4 — analyse the settle-lag census.

The quirk P-027 posits requires settlement to WAIT for a later, non-preliminary
print after the market closed on the flash.  That shows up as a large
settlement_ts - close_time lag.  Anything settling within ~a day of close
settled on the very release it closed for -> no prelim/final gap.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

PRELIM_MARKERS = ("PREL", "FLASH", " ADV", "ADVANCE", "PRELIM")


def main():
    ev = json.load(open(os.path.join(DATA, "settle_lag.json")))
    done = [e for e in ev if e["settle_lag_hours"] is not None]
    print(f"events total={len(ev)}  with settlement_ts={len(done)}")

    buckets = {"<2h": 0, "2-24h": 0, "1-3d": 0, "3-10d": 0, ">10d": 0,
               "negative": 0}
    long_lag = []
    for e in done:
        h = e["settle_lag_hours"]
        if h < 0:
            buckets["negative"] += 1
        elif h < 2:
            buckets["<2h"] += 1
        elif h < 24:
            buckets["2-24h"] += 1
        elif h < 72:
            buckets["1-3d"] += 1
        elif h < 240:
            buckets["3-10d"] += 1
        else:
            buckets[">10d"] += 1
        if h >= 24:
            long_lag.append(e)
    print("settlement lag (settlement_ts - close_time):")
    for k, v in buckets.items():
        print(f"   {k:<10} {v:>4}  ({100.0*v/len(done):.1f}%)")

    print(f"\nevents with lag >= 24h  ({len(long_lag)}) — the only place a "
          f"prelim->final gap could hide:")
    for e in sorted(long_lag, key=lambda x: -x["settle_lag_hours"]):
        print(f"  {e['series']:<20} {e['event']:<26} "
              f"lag={e['settle_lag_hours']:8.1f}h close={e['close_time']} "
              f"expval={e['expiration_value']!r} vol={e['total_volume']}")
        print(f"      {e['series_title']}")

    # series whose TITLE names a preliminary/flash/advance print
    prelim = [e for e in ev
              if any(m in (e["series_title"] or "").upper()
                     for m in PRELIM_MARKERS)]
    ser = sorted({e["series"] for e in prelim})
    print(f"\nseries whose title names a PRELIM/FLASH/ADV print: {len(ser)}")
    for s in ser:
        rows = [e for e in prelim if e["series"] == s]
        lags = [e["settle_lag_hours"] for e in rows
                if e["settle_lag_hours"] is not None]
        vol = sum(e["total_volume"] for e in rows)
        print(f"  {s:<16} events={len(rows):<3} settled={len(lags):<3} "
              f"maxlag={max(lags) if lags else float('nan'):8.2f}h "
              f"vol={vol:>10.0f}  {rows[0]['series_title']}")

    # volume distribution across the whole family
    vols = sorted((e["total_volume"] for e in ev), reverse=True)
    print(f"\nECONSTAT family traded volume per event (contracts): "
          f"n={len(vols)} total={sum(vols):,.0f}")
    print(f"   zero-volume events: {sum(1 for v in vols if v == 0)}")
    print(f"   median={vols[len(vols)//2]:.0f}  "
          f"p90={vols[int(len(vols)*0.10)]:.0f}  max={vols[0]:.0f}")


if __name__ == "__main__":
    main()
