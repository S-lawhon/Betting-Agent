"""Start-anchored candle pull for settled MLB markets (kill-test #1).

Anchors quotes to scheduled game start (parsed from the ticker, ET) rather
than market close, eliminating close-time selection effects:
  pre   = start - 30min   (pre-game)
  in2h  = start + 2h      (mid-game)
  in3h  = start + 3h      (late game)

Output: data/mlb_candles_anchored.parquet
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd

kc.MIN_INTERVAL = 0.15

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
SAMPLES = {"KXMLBTOTAL": 3000, "KXMLBSPREAD": 1500, "KXMLBGAME": 800}
ANCHORS = {"pre": -1800, "in2h": 7200, "in3h": 10800}


def start_utc(event_ticker):
    m = re.match(r"KXMLB\w+-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})", event_ticker)
    if not m:
        return None
    y, mon, d, hh, mm = m.groups()
    try:
        return pd.Timestamp(2000 + int(y), MONTHS[mon], int(d), int(hh),
                            int(mm), tz="US/Eastern").tz_convert("UTC")
    except (ValueError, KeyError):
        return None


def candle_at(candles, target_ts):
    prior = [c for c in candles if c["end_period_ts"] <= target_ts]
    return prior[-1] if prior else None


def fetch(r):
    start_ts = int(r.start.timestamp())
    close_ts = int(r.close_time.timestamp())
    try:
        data = kc.get(
            f"/series/{r.series_ticker}/markets/{r.ticker}/candlesticks",
            {"start_ts": start_ts - 3600 * 3, "end_ts": min(close_ts, start_ts + 5 * 3600),
             "period_interval": 1})
    except Exception as e:
        print(f"[warn] {r.ticker}: {e}", flush=True)
        return None
    candles = data.get("candlesticks", [])
    if not candles:
        return None
    row = {"ticker": r.ticker, "series_ticker": r.series_ticker,
           "result": r.result, "volume": r.volume,
           "start_ts": start_ts, "close_ts": close_ts,
           "open_rel_s": int(r.open_time.timestamp()) - start_ts
           if pd.notna(r.open_time) else None}
    for name, off in ANCHORS.items():
        t = start_ts + off
        if t >= close_ts:  # market already closed at this anchor
            continue
        c = candle_at(candles, t)
        if not c:
            continue
        g = lambda side, k: kc.fnum((c.get(side) or {}).get(k))
        row[f"bid_{name}"] = g("yes_bid", "close_dollars")
        row[f"ask_{name}"] = g("yes_ask", "close_dollars")
        row[f"vwap_{name}"] = g("price", "mean_dollars")
    return row


def main():
    df = pd.read_parquet(kc.DATA_DIR / "kalshi_settled.parquet")
    df = df[df.series_ticker.isin(SAMPLES) & (df.volume >= 500)
            & df.result.isin(["yes", "no"])].copy()
    df["start"] = df.event_ticker.map(start_utc)
    df = df[df.start.notna()]
    parts = [g.sample(min(n, len(g)), random_state=11)
             for st, n in SAMPLES.items()
             for _, g in [(st, df[df.series_ticker == st])]]
    sample = pd.concat(parts)
    out_path = kc.DATA_DIR / "mlb_candles_anchored.parquet"
    have = set()
    existing = None
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        have = set(existing["ticker"])
    todo = [r for r in sample.itertuples() if r.ticker not in have]
    print(f"[mlb] {len(todo)} markets to fetch", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    rows, n = [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch, r) for r in todo]
        for fut in as_completed(futs):
            row = fut.result()
            n += 1
            if row:
                rows.append(row)
            if n % 500 == 0:
                print(f"[mlb] {n}/{len(todo)}", flush=True)
                snap = pd.DataFrame(rows)
                comb = pd.concat([existing, snap]) if existing is not None else snap
                comb.to_parquet(out_path, index=False)
    snap = pd.DataFrame(rows)
    comb = pd.concat([existing, snap]) if existing is not None else snap
    comb.to_parquet(out_path, index=False)
    print(f"[done] {len(comb)} -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
