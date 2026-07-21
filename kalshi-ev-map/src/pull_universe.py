"""Pull the full open-market universe + series metadata + event structure.

Outputs:
  data/kalshi_universe.parquet   -- one row per open market, joined to series meta
  data/raw/series_meta.json      -- series ticker -> series object
  data/raw/events_open.json      -- open events with nested markets (for bracket checks)
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd


def pull_open_markets():
    cached = kc.load_cached("markets_open")
    if cached:
        print(f"[markets] using cache: {len(cached)}", flush=True)
        return cached
    markets = []
    t0 = time.time()

    def cb(pages, n):
        if pages % 10 == 0:
            print(f"[markets] page {pages}, total {len(markets)}, "
                  f"{time.time()-t0:.0f}s", flush=True)
            kc.cache_json("markets_open_partial", markets)

    for m in kc.paginate("/markets", {"status": "open", "limit": 1000},
                         list_key="markets", page_cb=cb):
        markets.append(m)
    kc.cache_json("markets_open", markets)
    print(f"[markets] done: {len(markets)} open markets", flush=True)
    return markets


def series_ticker_of(market):
    # market objects don't carry series_ticker; derive from event_ticker prefix
    ev = market.get("event_ticker") or ""
    return ev.split("-")[0]


def pull_series_meta(markets):
    cached = kc.load_cached("series_meta")
    meta = cached or {}
    tickers = sorted({series_ticker_of(m) for m in markets} - set(meta))
    print(f"[series] need {len(tickers)} series", flush=True)
    for i, st in enumerate(tickers):
        try:
            meta[st] = kc.get(f"/series/{st}")["series"]
        except Exception as e:
            meta[st] = {"ticker": st, "error": str(e)}
        if (i + 1) % 50 == 0:
            print(f"[series] {i+1}/{len(tickers)}", flush=True)
            kc.cache_json("series_meta", meta)
    kc.cache_json("series_meta", meta)
    return meta


def pull_open_events():
    cached = kc.load_cached("events_open")
    if cached:
        print(f"[events] using cache: {len(cached)}", flush=True)
        return cached
    events = []

    def cb(pages, n):
        if pages % 20 == 0:
            print(f"[events] page {pages}, total {len(events)}", flush=True)

    for ev in kc.paginate("/events", {"status": "open", "limit": 200,
                                      "with_nested_markets": "true"},
                          list_key="events", page_cb=cb):
        events.append(ev)
    kc.cache_json("events_open", events)
    print(f"[events] done: {len(events)}", flush=True)
    return events


def build_frame(markets, series_meta):
    f = kc.fnum
    rows = []
    for m in markets:
        st = series_ticker_of(m)
        s = series_meta.get(st, {})
        yes_bid, yes_ask = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
        spread = (yes_ask - yes_bid) if (yes_ask is not None and yes_bid is not None) else None
        rows.append({
            "ticker": m.get("ticker"),
            "event_ticker": m.get("event_ticker"),
            "series_ticker": st,
            "title": m.get("title"),
            "yes_sub_title": m.get("yes_sub_title"),
            "category": s.get("category"),
            "series_title": s.get("title"),
            "frequency": s.get("frequency"),
            "fee_type": s.get("fee_type"),
            "fee_multiplier": s.get("fee_multiplier"),
            "settlement_sources": json.dumps([x.get("name") for x in s.get("settlement_sources", [])]),
            "market_type": m.get("market_type"),
            "strike_type": m.get("strike_type"),
            "is_mve": bool(m.get("mve_collection_ticker")),
            "status": m.get("status"),
            "open_time": m.get("open_time"),
            "close_time": m.get("close_time"),
            "expected_expiration_time": m.get("expected_expiration_time"),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "spread": spread,
            "yes_bid_size": f(m.get("yes_bid_size_fp")),
            "yes_ask_size": f(m.get("yes_ask_size_fp")),
            "last_price": f(m.get("last_price_dollars")),
            "volume": f(m.get("volume_fp")),
            "volume_24h": f(m.get("volume_24h_fp")),
            "open_interest": f(m.get("open_interest_fp")),
            "liquidity": f(m.get("liquidity_dollars")),
        })
    df = pd.DataFrame(rows)
    for col in ("open_time", "close_time", "expected_expiration_time"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    now = pd.Timestamp.now(tz="UTC")
    df["days_to_close"] = (df["close_time"] - now).dt.total_seconds() / 86400
    return df


def main():
    markets = pull_open_markets()
    series_meta = pull_series_meta(markets)
    events = pull_open_events()
    df = build_frame(markets, series_meta)
    out = kc.DATA_DIR / "kalshi_universe.parquet"
    df.to_parquet(out, index=False)
    print(f"[done] {len(df)} markets, {df['series_ticker'].nunique()} series, "
          f"{len(events)} events -> {out}", flush=True)


if __name__ == "__main__":
    main()
