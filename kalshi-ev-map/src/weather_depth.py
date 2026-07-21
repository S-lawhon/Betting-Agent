"""Capture weather-market orderbook depth (cron target, run in US morning).

Evening books were ask-only; capacity for Build 2 depends on what the books
look like while the day's high is still uncertain. Appends snapshots to
data/weather_depth.parquet.
"""
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
from weather_pipeline import STATIONS, date_suffix

import pandas as pd

kc.MIN_INTERVAL = 0.3
OUT = kc.DATA_DIR / "weather_depth.parquet"


def main():
    rows = []
    now = datetime.utcnow().isoformat(timespec="minutes")
    for series in STATIONS:
        ev = f"{series}-{date_suffix(date.today())}"
        try:
            mkts = kc.get("/markets", {"event_ticker": ev, "limit": 20}).get("markets", [])
        except Exception as e:
            print(f"[warn] {ev}: {e}", flush=True)
            continue
        for m in mkts:
            try:
                ob = kc.get(f"/markets/{m['ticker']}/orderbook", {"depth": 10})
            except Exception:
                continue
            book = ob.get("orderbook_fp") or {}
            yes = book.get("yes_dollars") or []
            no = book.get("no_dollars") or []
            rows.append({
                "snap_utc": now, "ticker": m["ticker"], "series": series,
                "yes_bid_levels": len(yes), "no_bid_levels": len(no),
                "yes_depth_dollars": sum(float(p) * float(s) for p, s in yes),
                "no_depth_dollars": sum(float(p) * float(s) for p, s in no),
                "best_yes_bid": max((float(p) for p, s in yes), default=None),
                "best_no_bid": max((float(p) for p, s in no), default=None),
                "volume_24h": kc.fnum(m.get("volume_24h_fp")),
            })
    df = pd.DataFrame(rows)
    if OUT.exists():
        df = pd.concat([pd.read_parquet(OUT), df])
    df.to_parquet(OUT, index=False)
    print(f"[done] +{len(rows)} book snaps -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
