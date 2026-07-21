"""Pull pre-close candlesticks for a sample of settled markets.

For each settled market in data/kalshi_settled.parquet (stratified sample per
series), fetch candlesticks and record executable bid/ask + VWAP at fixed
horizons before close (T-1h, T-24h, T-7d where available).

Output: data/settled_candles.parquet
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd

kc.MIN_INTERVAL = 0.15

HORIZONS_H = [1, 24, 168]


def candle_at(candles, target_ts):
    """Last candle ending at or before target_ts."""
    prior = [c for c in candles if c["end_period_ts"] <= target_ts]
    return prior[-1] if prior else None


def main():
    per_series = 400
    for a in sys.argv[1:]:
        if a.startswith("--per-series"):
            per_series = int(a.split("=")[1])
    df = pd.read_parquet(kc.DATA_DIR / "kalshi_settled.parquet")
    df = df[df["volume"].fillna(0) > 0]
    sample = (df.sort_values("volume", ascending=False)
                .groupby("series_ticker", group_keys=False)
                .head(per_series))
    print(f"[candles] {len(sample)} settled markets to fetch", flush=True)

    out_path = kc.DATA_DIR / "settled_candles.parquet"
    existing = None
    have = set()
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        have = set(existing["ticker"])
        print(f"[candles] resume: {len(have)} already fetched", flush=True)

    todo = [r for r in sample.itertuples() if r.ticker not in have]

    def fetch(r):
        close_ts = int(r.close_time.timestamp())
        open_ts = int(r.open_time.timestamp()) if pd.notna(r.open_time) else close_ts - 90*86400
        span_h = (close_ts - open_ts) / 3600
        interval = 60 if span_h > 48 else 1  # hourly vs minute candles
        try:
            data = kc.get(
                f"/series/{r.series_ticker}/markets/{r.ticker}/candlesticks",
                {"start_ts": max(open_ts, close_ts - 8*86400),
                 "end_ts": close_ts, "period_interval": interval})
        except Exception as e:
            print(f"[warn] {r.ticker}: {e}", flush=True)
            return None
        candles = data.get("candlesticks", [])
        if not candles:
            return None
        row = {"ticker": r.ticker, "series_ticker": r.series_ticker,
               "result": r.result, "volume": r.volume,
               "close_ts": close_ts, "span_h": span_h}
        for h in HORIZONS_H:
            c = candle_at(candles, close_ts - h * 3600)
            if not c:
                continue
            g = lambda side, k: kc.fnum((c.get(side) or {}).get(k))
            row[f"bid_t{h}"] = g("yes_bid", "close_dollars")
            row[f"ask_t{h}"] = g("yes_ask", "close_dollars")
            row[f"vwap_t{h}"] = g("price", "mean_dollars") or g("price", "close_dollars")
        return row

    from concurrent.futures import ThreadPoolExecutor, as_completed
    rows = []
    fetched = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch, r) for r in todo]
        for fut in as_completed(futs):
            row = fut.result()
            fetched += 1
            if row:
                rows.append(row)
            if fetched % 500 == 0:
                print(f"[candles] fetched {fetched}/{len(todo)}", flush=True)
                snap = pd.DataFrame(rows)
                combined = (pd.concat([existing, snap])
                            if existing is not None else snap)
                combined.to_parquet(out_path, index=False)
    snap = pd.DataFrame(rows)
    combined = pd.concat([existing, snap]) if existing is not None else snap
    combined.to_parquet(out_path, index=False)
    print(f"[done] {len(combined)} rows -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
