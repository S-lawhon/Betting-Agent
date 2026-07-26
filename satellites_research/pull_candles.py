"""
satellites_research/pull_candles.py
───────────────────────────────────
Pre-close candlesticks for settled markets in Studies A and B.  READ-ONLY.

Settled markets have their bid/ask/volume NULLED on the LIST endpoints and
`last_price` is settlement-contaminated, so every price used in this task comes
from candlesticks: per-hour yes_bid / yes_ask closes plus traded VWAP.  Anchors
are measured backwards from `close_time` (which for RT *is* the 10:00 AM ET
Monday read time, and for awards is the ceremony expiration).

Cached per ticker; re-runs cost nothing.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CAND = os.path.join(DATA, "candles")
os.makedirs(CAND, exist_ok=True)


def _iso(ts):
    from datetime import datetime
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pull(markets: list[tuple[str, str, str]], days: int = 10) -> None:
    """markets: (series_ticker, market_ticker, close_time_iso)"""
    k = KalshiPublic(min_interval=0.7)
    for ser, tick, close_iso in markets:
        p = os.path.join(CAND, f"{tick}.json")
        if os.path.exists(p):
            continue
        close_ts = _iso(close_iso)
        if close_ts is None:
            continue
        d = k.get(f"/series/{ser}/markets/{tick}/candlesticks",
                  {"start_ts": int(close_ts - days * 86400),
                   "end_ts": int(close_ts), "period_interval": 60})
        with open(p, "w") as fh:
            json.dump(d or {}, fh)
        n = len((d or {}).get("candlesticks") or [])
        print(f"  {tick}: {n} candles", flush=True)


def _collect(dirname: str, want) -> list[tuple[str, str, str]]:
    out = []
    for fp in glob.glob(os.path.join(DATA, dirname, "*.json")):
        for m in json.load(open(fp)):
            if want(m):
                ser = m["ticker"].split("-")[0]
                # series_ticker is the market ticker up to the first '-' only for
                # simple series; prefer the filename, which carries it exactly.
                ser = os.path.basename(fp)[:-5].split("__")[1]
                out.append((ser, m["ticker"], m.get("close_time")))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["rt", "awards"], required=True)
    a = ap.parse_args()
    if a.which == "rt":
        tgt = _collect("rt_markets", lambda m: bool(m.get("result")))
    else:
        tgt = _collect("award_markets", lambda m: bool(m.get("result")))
    print(f"{len(tgt)} settled markets to pull")
    pull(tgt)
