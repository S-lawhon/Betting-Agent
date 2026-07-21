"""Sample the global public trade tape for a recent window.

Walks /markets/trades (newest-first) back `--hours` hours, then aggregates
notional by series/family to measure where volume actually clears.

Output: data/trades_sample.parquet
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd

kc.MIN_INTERVAL = 0.15


def main():
    hours = 6.0
    for a in sys.argv[1:]:
        if a.startswith("--hours"):
            hours = float(a.split("=")[1])
    cutoff = time.time() - hours * 3600
    rows, pages = [], 0
    params = {"limit": 1000}
    while True:
        data = kc.get("/markets/trades", params)
        trades = data.get("trades", [])
        if not trades:
            break
        for t in trades:
            ts = t.get("created_time", "")
            rows.append({
                "ticker": t.get("ticker"),
                "created_time": ts,
                "count": kc.fnum(t.get("count_fp")),
                "yes_price": kc.fnum(t.get("yes_price_dollars")),
                "taker_side": t.get("taker_side"),
            })
        pages += 1
        last_ts = trades[-1].get("created_time")
        last_epoch = datetime.fromisoformat(
            last_ts.replace("Z", "+00:00")).timestamp()
        if pages % 50 == 0:
            print(f"[trades] page {pages}, {len(rows)} trades, back to "
                  f"{last_ts}", flush=True)
        if last_epoch < cutoff:
            break
        cursor = data.get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    df = pd.DataFrame(rows)
    df["created_time"] = pd.to_datetime(df["created_time"], utc=True,
                                        errors="coerce")
    df["series_ticker"] = df["ticker"].str.split("-").str[0]
    df["notional"] = df["count"] * df["yes_price"].clip(0.01, 0.99)
    df.to_parquet(kc.DATA_DIR / "trades_sample.parquet", index=False)
    print(f"[done] {len(df)} trades over ~{hours}h -> trades_sample.parquet",
          flush=True)


if __name__ == "__main__":
    main()
