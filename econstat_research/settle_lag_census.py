"""P-027 step 3 — the universal diagnostic.

For every ECONSTAT-template series, pull its markets and measure
    settlement_ts - close_time
per event.  Interpretation:

  * lag of minutes-to-hours  -> the market settled on the SAME release it
    closed one minute before.  No prelim/final gap.  No quirk.
  * lag of many days/weeks   -> the market closed on one print and waited for
    a later (non-preliminary) print to resolve.  THIS is the P-027 quirk.

Cache-first; polite 1.0s throttle (shared 2 req/s per-IP budget).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from src.kalshi_public import KalshiPublic  # noqa: E402
from econstat_research.fetch_markets import fetch_series_markets  # noqa: E402

DATA = os.path.join(HERE, "data")


def iso(ts):
    """Robust ISO-8601 parse.

    Kalshi stamps `settlement_ts` with a VARIABLE number of fractional-second
    digits ('...:36.21184Z', 5 digits).  Python 3.9's fromisoformat only
    accepts exactly 3 or 6, so the naive parse silently returned None for a
    large slice of the settled events and halved the census.
    """
    if not ts:
        return None
    s = ts.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = ""
        while rest and rest[0].isdigit():
            frac, rest = frac + rest[0], rest[1:]
        s = f"{head}.{frac[:6].ljust(6, '0')}{rest}"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def main():
    series = json.load(open(os.path.join(DATA, "econstat_series.json")))
    kp = KalshiPublic(min_interval=1.0)
    out = []
    for i, s in enumerate(series):
        t = s["ticker"]
        try:
            rows = fetch_series_markets(kp, t)
        except Exception as exc:                       # noqa: BLE001
            print(f"  !! {t}: {exc}")
            continue
        # one record per EVENT (all strikes in an event share close/settle)
        by_event = {}
        for m in rows:
            ev = m.get("event_ticker")
            if not ev:
                continue
            by_event.setdefault(ev, []).append(m)
        for ev, ms in by_event.items():
            m0 = ms[0]
            ct, st = iso(m0.get("close_time")), iso(m0.get("settlement_ts"))
            lag_h = (st - ct).total_seconds() / 3600.0 if (ct and st) else None
            vols = [float(x.get("volume_fp") or 0) for x in ms]
            out.append({
                "series": t,
                "series_title": s.get("title"),
                "event": ev,
                "status": m0.get("status"),
                "close_time": m0.get("close_time"),
                "settlement_ts": m0.get("settlement_ts"),
                "settle_lag_hours": lag_h,
                "expiration_value": m0.get("expiration_value"),
                "n_markets": len(ms),
                "total_volume": round(sum(vols), 2),
                "rules_primary": (m0.get("rules_primary") or "")[:300],
            })
        if (i + 1) % 20 == 0:
            print(f"  ... {i+1}/{len(series)} series")
    json.dump(out, open(os.path.join(DATA, "settle_lag.json"), "w"), indent=1)
    print(f"events: {len(out)}")


if __name__ == "__main__":
    main()
