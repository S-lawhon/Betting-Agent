"""Pull settled MLB markets for all 12 study series (Phase 1, Q2 calibration).

Writes data/settled/<series>.json (raw) and data/settled_markets.parquet.
Throttled to ~2.2 req/s: the live collector runs concurrently at ~4.5 req/s
and the combined budget must stay under Kalshi's ~7 req/s public ceiling.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi-ev-map" / "src"))
import kalshi_client as kc

kc.MIN_INTERVAL = 0.45  # share rate budget with the live collector

import pandas as pd

SERIES = [
    "KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBF5", "KXMLBRFI",
    "KXMLBKS", "KXMLBHR", "KXMLBHIT", "KXMLBTB", "KXMLBTEAMTOTAL",
    "KXMLBHRR", "KXMLBSB",
]
DAYS = 90

OUT_DIR = Path(__file__).resolve().parent / "data" / "settled"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def pull(series, min_close_ts):
    cache = OUT_DIR / f"{series}.json"
    if cache.exists():
        with open(cache) as f:
            out = json.load(f)
        print(f"[{series}] cached: {len(out)}", flush=True)
        return out
    out = []
    for m in kc.paginate("/markets", {
            "series_ticker": series, "status": "settled",
            "min_close_ts": min_close_ts, "limit": 1000},
            list_key="markets",
            page_cb=lambda p, n: print(f"[{series}] page {p} (+{n})", flush=True)):
        out.append(m)
    with open(cache, "w") as f:
        json.dump(out, f)
    print(f"[{series}] {len(out)} settled markets", flush=True)
    return out


def main():
    min_close_ts = int(time.time()) - DAYS * 86400
    f = kc.fnum
    rows = []
    for s in SERIES:
        for m in pull(s, min_close_ts):
            rows.append({
                "series": s,
                "ticker": m.get("ticker"),
                "event_ticker": m.get("event_ticker"),
                "title": m.get("title"),
                "yes_sub_title": m.get("yes_sub_title"),
                "result": m.get("result"),
                "open_time": m.get("open_time"),
                "close_time": m.get("close_time"),
                "expiration_time": m.get("expiration_time"),
                "last_price": f(m.get("last_price_dollars")),
                "volume": f(m.get("volume_fp")),
                "open_interest": f(m.get("open_interest_fp")),
            })
    df = pd.DataFrame(rows)
    for col in ("open_time", "close_time", "expiration_time"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    out = Path(__file__).resolve().parent / "data" / "settled_markets.parquet"
    df.to_parquet(out, index=False)
    print(f"[done] {len(df)} rows -> {out}", flush=True)
    print(df.groupby("series").agg(n=("ticker", "count"),
                                   vol=("volume", "sum")).to_string(), flush=True)


if __name__ == "__main__":
    main()
