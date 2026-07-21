"""Pull settled-market history for target series (for calibration/backtests).

Usage: python3 pull_settled.py SERIES1 SERIES2 ... [--days N]
Outputs data/settled_<series>.json caches and data/kalshi_settled.parquet.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd


def pull_series_settled(series_ticker, min_close_ts):
    name = f"settled_{series_ticker}"
    cached = kc.load_cached(name)
    if cached is not None:
        return cached
    out = []
    for m in kc.paginate("/markets", {
            "series_ticker": series_ticker, "status": "settled",
            "min_close_ts": min_close_ts, "limit": 1000}, list_key="markets"):
        out.append(m)
    kc.cache_json(name, out)
    print(f"[{series_ticker}] {len(out)} settled markets", flush=True)
    return out


def build_frame(all_markets):
    f = kc.fnum
    rows = []
    for m in all_markets:
        rows.append({
            "ticker": m.get("ticker"),
            "event_ticker": m.get("event_ticker"),
            "series_ticker": (m.get("event_ticker") or "").split("-")[0],
            "title": m.get("title"),
            "yes_sub_title": m.get("yes_sub_title"),
            "result": m.get("result"),
            "strike_type": m.get("strike_type"),
            "is_mve": bool(m.get("mve_collection_ticker")),
            "open_time": m.get("open_time"),
            "close_time": m.get("close_time"),
            "settled_time": m.get("settled_time") or m.get("expiration_time"),
            "last_price": f(m.get("last_price_dollars")),
            "volume": f(m.get("volume_fp")),
            "open_interest": f(m.get("open_interest_fp")),
            "liquidity": f(m.get("liquidity_dollars")),
        })
    df = pd.DataFrame(rows)
    for col in ("open_time", "close_time", "settled_time"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    return df


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    days = 90
    for a in sys.argv[1:]:
        if a.startswith("--days"):
            days = int(a.split("=")[1])
    min_close_ts = int(time.time()) - days * 86400
    all_markets = []
    for st in args:
        all_markets.extend(pull_series_settled(st, min_close_ts))
    df = build_frame(all_markets)
    out = kc.DATA_DIR / "kalshi_settled.parquet"
    if out.exists():
        old = pd.read_parquet(out)
        df = (pd.concat([old, df])
                .drop_duplicates(subset="ticker", keep="last"))
    df.to_parquet(out, index=False)
    print(f"[done] {len(df)} settled rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
