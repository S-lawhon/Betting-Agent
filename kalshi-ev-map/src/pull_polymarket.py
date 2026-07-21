"""Pull active Polymarket markets (gamma API, public) for cross-venue basis.

Output: data/polymarket_active.parquet
"""
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GAMMA = "https://gamma-api.polymarket.com/markets"


def pull(min_volume=1000, max_rows=20000):
    session = requests.Session()
    session.headers.update({"User-Agent": "kalshi-ev-map-research/0.1"})
    rows, offset = [], 0
    while len(rows) < max_rows:
        batch = None
        for attempt in range(5):
            try:
                r = session.get(GAMMA, params={
                    "closed": "false", "active": "true", "limit": 500,
                    "order": "volumeNum", "ascending": "false",
                    "offset": offset}, timeout=30)
                r.raise_for_status()
                batch = r.json()
                break
            except requests.exceptions.RequestException as e:
                print(f"[poly] retry offset={offset}: {e}", flush=True)
                time.sleep(5 * (attempt + 1))
        if batch is None:
            print(f"[poly] giving up at offset {offset}, keeping "
                  f"{len(rows)} rows", flush=True)
            break
        if not batch:
            break
        rows.extend(batch)
        vols = [b.get("volumeNum") or 0 for b in batch]
        if min(vols) < min_volume:
            print(f"[poly] volume floor reached at offset {offset}", flush=True)
            break
        offset += 500
        if offset % 2000 == 0:
            print(f"[poly] {offset} markets", flush=True)
        time.sleep(0.3)
    print(f"[poly] raw total {len(rows)}", flush=True)
    return rows


def build_frame(rows):
    out = []
    for m in rows:
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            outcomes = json.loads(m.get("outcomes") or "[]")
        except json.JSONDecodeError:
            prices, outcomes = [], []
        yes_price = None
        if len(prices) == 2 and outcomes[:1] == ["Yes"]:
            yes_price = float(prices[0])
        out.append({
            "id": m.get("id"),
            "question": m.get("question"),
            "slug": m.get("slug"),
            "event_slug": (m.get("events") or [{}])[0].get("slug") if m.get("events") else None,
            "category": m.get("category"),
            "end_date": m.get("endDate"),
            "yes_price": yes_price,
            "best_bid": m.get("bestBid"),
            "best_ask": m.get("bestAsk"),
            "spread": m.get("spread"),
            "volume": m.get("volumeNum"),
            "volume_24h": m.get("volume24hr"),
            "liquidity": m.get("liquidityNum"),
        })
    df = pd.DataFrame(out)
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce", utc=True)
    return df


def main():
    rows = pull()
    df = build_frame(rows)
    out = DATA_DIR / "polymarket_active.parquet"
    df.to_parquet(out, index=False)
    print(f"[done] {len(df)} -> {out}", flush=True)


if __name__ == "__main__":
    main()
