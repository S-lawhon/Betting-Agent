"""Paper maker for weather brackets (Build 2 validation loop).

Each run: compute fair values for today's (and tomorrow's, pre-day) bracket
markets, decide the quotes a maker would post (fair +/- required edge, never
crossing the book), and log the intents. No orders are placed — this is the
paper phase; fills are evaluated afterward against the public trades tape by
weather_paper_eval.py.

Append-only log: data/paper_quotes.parquet
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
import fees
from weather_pipeline import (STATIONS, load_station_params, fetch_ensemble,
                              fetch_det_daily_max, member_daily_max,
                              prob_table_empirical, prob_table, bracket_prob,
                              live_markets, SIGMA_DEFAULT)

import pandas as pd

kc.MIN_INTERVAL = 0.3
REQ_EDGE = 0.03          # required edge per side, before maker fee (0 here)
MAX_QUOTE_SIZE = 100     # contracts per quote (paper)
OUT = kc.DATA_DIR / "paper_quotes.parquet"


def quotes_for_day(target_day, now_iso):
    rows = []
    params = load_station_params()
    for series, (iem, lat, lon, tz) in STATIONS.items():
        if series.startswith("KXLOW"):
            continue
        # Until intraday obs conditioning exists (v2), only quote a day's
        # market while the forecast is still the best information: before
        # 10:00 local on the settlement day. Day-1 eval showed in-day quotes
        # off a stale forecast bleed ~-10%/afternoon to informed lifters.
        local_now = pd.Timestamp.now(tz=tz)
        if target_day == local_now.date() and local_now.hour >= 10:
            continue
        sp = params.get(series) or {}
        det = fetch_det_daily_max(lat, lon, target_day)
        if det is None or not sp.get("errors"):
            continue
        center = det + sp["bias"]
        probs = prob_table_empirical(center, sp["errors"])
        for m in live_markets(series, target_day):
            if m.get("status") != "active":
                continue
            fair = bracket_prob(probs, m.get("floor_strike"),
                                m.get("cap_strike"), m.get("strike_type"))
            best_bid = kc.fnum(m.get("yes_bid_dollars")) or 0.0
            best_ask = kc.fnum(m.get("yes_ask_dollars")) or 1.0
            my_bid = round(min(fair - REQ_EDGE, best_ask - 0.01), 2)
            my_ask = round(max(fair + REQ_EDGE, best_bid + 0.01), 2)
            rows.append({
                "snap_utc": now_iso, "target_day": target_day.isoformat(),
                "series": series, "ticker": m["ticker"],
                "sub": m.get("yes_sub_title"),
                "fair": round(fair, 3), "center": round(center, 1),
                "mkt_bid": best_bid, "mkt_ask": best_ask,
                "my_bid": my_bid if 0.01 <= my_bid <= 0.97 else None,
                "my_ask": my_ask if 0.03 <= my_ask <= 0.99 else None,
                "size": MAX_QUOTE_SIZE,
            })
    return rows


def main():
    now_iso = datetime.utcnow().isoformat(timespec="minutes")
    rows = quotes_for_day(date.today(), now_iso)
    # pre-day quotes for tomorrow once its markets are listed
    rows += quotes_for_day(date.today() + timedelta(days=1), now_iso)
    df = pd.DataFrame(rows)
    if OUT.exists():
        df = pd.concat([pd.read_parquet(OUT), df])
    df.to_parquet(OUT, index=False)
    print(f"[paper] +{len(rows)} quote intents ({now_iso}) -> {OUT}",
          flush=True)


if __name__ == "__main__":
    main()
