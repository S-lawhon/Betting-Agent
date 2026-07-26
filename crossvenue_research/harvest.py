"""
crossvenue_research/harvest.py
──────────────────────────────
P-020 Phase-1 data harvest.  READ-ONLY.  Caches every pull to
crossvenue_research/data/ so the pipeline resumes without re-hitting either API.

Never imports src.polymarket_client's order paths; talks to Gamma / CLOB
read endpoints directly over HTTP.

Steps (each idempotent, cache-backed):
  1. kalshi_series.json    — Politics/World/Economics series metadata
  2. kalshi_settled.json   — settled markets closing in the study window
  3. poly_closed.json      — Polymarket closed markets in the window
  4. poly_history/<tok>.json, kalshi_candles/<ticker>.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

KBASE = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# Study window: bounded on the left by Polymarket's ~30-day prices-history
# retention (verified 2026-07-26: markets ending before ~2026-06-20 return an
# empty history array).
WIN_START = dt.datetime(2026, 6, 15, tzinfo=dt.timezone.utc)
WIN_END = dt.datetime(2026, 7, 27, tzinfo=dt.timezone.utc)

UA = {"User-Agent": "betting-pod-shop/p020-research (read-only)"}

_k_last = [0.0]
_p_last = [0.0]
K_INTERVAL = float(os.environ.get("P020_KALSHI_INTERVAL", "1.0"))
P_INTERVAL = 0.30


def _throttle(slot: list, interval: float) -> None:
    wait = slot[0] + interval - time.time()
    if wait > 0:
        time.sleep(wait)
    slot[0] = time.time()


_session = requests.Session()
_session.headers.update(UA)


def kget(path: str, params: Optional[dict] = None, retries: int = 4) -> Optional[dict]:
    for attempt in range(retries + 1):
        _throttle(_k_last, K_INTERVAL)
        try:
            r = _session.get(KBASE + path, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code == 429:
            # shared 2 req/s per-IP budget: back off hard, never retry-storm
            time.sleep(8 * (attempt + 1))
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        return None
    return None


def pget(url: str, params: Optional[dict] = None, retries: int = 3) -> Any:
    for attempt in range(retries + 1):
        _throttle(_p_last, P_INTERVAL)
        try:
            r = _session.get(url, params=params, timeout=40)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(4 * (attempt + 1))
            continue
        return None
    return None


def cache(name: str, fn):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    val = fn()
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(val, f)
    os.replace(tmp, p)
    return val


# ── Step 1: Kalshi series in the relevant categories ─────────────────

CATEGORIES = ["Politics", "World", "Economics"]


def fetch_kalshi_series() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for cat in CATEGORIES:
        d = kget("/series/", {"category": cat, "limit": 1000})
        for s in (d or {}).get("series", []):
            s["_category"] = cat
            out[s["ticker"]] = s
    return out


# ── Step 2: Kalshi settled markets in the window ─────────────────────


def fetch_kalshi_settled_for_series(tickers: List[str]) -> List[dict]:
    """Settled markets closing in the window, queried per series.

    The global /markets?status=settled feed is unusable here: it is dominated
    by hourly crypto markets (>400k rows in a 6-week window, of which 31 were
    politics).  Per-series queries are targeted and cheap.
    """
    mn = int(WIN_START.timestamp())
    mx = int(WIN_END.timestamp())
    out: List[dict] = []
    for i, st in enumerate(tickers):
        cursor = None
        for _ in range(6):
            params = {
                "series_ticker": st, "status": "settled",
                "min_close_ts": mn, "max_close_ts": mx, "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            d = kget("/markets", params)
            if not d:
                break
            for m in d.get("markets", []):
                m["_series"] = st
                out.append(m)
            cursor = d.get("cursor")
            if not cursor:
                break
        sys.stderr.write(
            f"\r  kalshi settled: series {i+1}/{len(tickers)} kept={len(out)}   ")
        sys.stderr.flush()
    sys.stderr.write("\n")
    return out


# ── Step 3: Polymarket closed markets in the window ──────────────────

# Gamma tag ids covering politics / world / economics.  Resolved live from
# /tags/slug/<slug> on 2026-07-26.  Offset pagination caps at ~2100 rows, so
# we page per tag and union.
POLY_TAGS = {
    2: "politics", 789: "us-politics", 144: "elections",
    1597: "global-elections", 101970: "world", 100265: "geopolitics",
    101794: "foreign-policy", 100328: "economy", 107: "business",
    159: "fed", 702: "inflation", 126: "trump", 102618: "white-house",
    514: "congress", 1628: "courts", 154: "middle-east", 95: "russia",
    96: "ukraine", 180: "israel",
}


def fetch_poly_closed() -> List[dict]:
    seen: Dict[str, dict] = {}
    for tag in POLY_TAGS:
        cursor = None
        pages = 0
        while pages < 60:
            params = {
                "closed": "true", "limit": 500,
                "end_date_min": WIN_START.isoformat().replace("+00:00", "Z"),
                "end_date_max": WIN_END.isoformat().replace("+00:00", "Z"),
                "tag_id": tag,
            }
            if cursor:
                params["next_cursor"] = cursor
            d = pget(GAMMA + "/markets/keyset", params)
            if not isinstance(d, dict):
                break
            ms = d.get("markets", [])
            for m in ms:
                if m.get("id"):
                    m.setdefault("_tag", tag)
                    seen.setdefault(str(m["id"]), m)
            cursor = d.get("next_cursor")
            pages += 1
            sys.stderr.write(f"\r  poly tag {tag}: page={pages} uniq={len(seen)}   ")
            sys.stderr.flush()
            if not ms or not cursor:
                break
    sys.stderr.write("\n")
    return list(seen.values())


# ── Step 4: per-market time series ───────────────────────────────────


def poly_history(token_id: str) -> List[dict]:
    def _f():
        d = pget(CLOB + "/prices-history",
                 {"market": token_id, "interval": "max", "fidelity": 60})
        return (d or {}).get("history", []) if isinstance(d, dict) else []
    return cache(f"poly_history__{token_id[:24]}.json", _f)


def kalshi_candles(series_ticker: str, ticker: str,
                   start_ts: int, end_ts: int) -> List[dict]:
    """Hourly candlesticks.  Kalshi caps the span per request; we chunk."""
    def _f():
        out: List[dict] = []
        # 60-min period; Kalshi allows <=5000 periods per call
        step = 3600 * 24 * 30
        t = start_ts
        while t < end_ts:
            e = min(t + step, end_ts)
            d = kget(
                f"/series/{series_ticker}/markets/{ticker}/candlesticks",
                {"start_ts": t, "end_ts": e, "period_interval": 60},
            )
            if d:
                out.extend(d.get("candlesticks", []))
            t = e
        return out
    safe = ticker.replace("/", "_")
    return cache(f"kalshi_candles__{safe}.json", _f)


if __name__ == "__main__":
    print("1. kalshi series ...")
    series = cache("kalshi_series.json", fetch_kalshi_series)
    print(f"   {len(series)} series")

    print("2. polymarket closed markets in window ...")
    poly = cache("poly_closed.json", fetch_poly_closed)
    print(f"   {len(poly)} closed poly markets")

    if "--kalshi" in sys.argv:
        shortlist = json.load(open(os.path.join(DATA, "series_shortlist.json")))
        print(f"3. kalshi settled markets for {len(shortlist)} shortlisted series ...")
        ks = cache("kalshi_settled.json",
                   lambda: fetch_kalshi_settled_for_series(shortlist))
        print(f"   {len(ks)} settled markets")
