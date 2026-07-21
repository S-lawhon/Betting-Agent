"""Minimal Kalshi public-API client with rate limiting and disk cache.

Public (unauthenticated) read endpoints on the trade API v2.
Base URL verified live 2026-07-18.
"""
import json
import threading
import time
from pathlib import Path

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / "raw"

_session = requests.Session()
_session.headers.update({"User-Agent": "kalshi-ev-map-research/0.1"})
_last_request_ts = [0.0]
_throttle_lock = threading.Lock()
MIN_INTERVAL = 0.22  # ~4.5 req/s, well under the basic-tier limit


def _throttle():
    # Reserve the next send slot under the lock (thread-safe token spacing),
    # then sleep outside it.
    with _throttle_lock:
        slot = max(_last_request_ts[0] + MIN_INTERVAL, time.time())
        _last_request_ts[0] = slot
    delay = slot - time.time()
    if delay > 0:
        time.sleep(delay)


def get(path, params=None, retries=4):
    """GET with throttle + exponential backoff on 429/5xx."""
    for attempt in range(retries + 1):
        _throttle()
        try:
            resp = _session.get(f"{BASE}{path}", params=params, timeout=30)
        except requests.exceptions.RequestException:
            if attempt < retries:
                time.sleep(3 * (2 ** attempt))
                continue
            raise
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(5 * (attempt + 1) if resp.status_code == 429
                       else 2 ** attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"unreachable: {path}")


def paginate(path, params=None, list_key=None, max_pages=None, page_cb=None):
    """Iterate a cursor-paginated endpoint, yielding items."""
    params = dict(params or {})
    list_key = list_key or path.strip("/").split("/")[-1]
    pages = 0
    while True:
        data = get(path, params)
        items = data.get(list_key, [])
        for item in items:
            yield item
        pages += 1
        if page_cb:
            page_cb(pages, len(items))
        cursor = data.get("cursor")
        if not cursor or not items:
            return
        if max_pages and pages >= max_pages:
            return
        params["cursor"] = cursor


def cache_json(name, obj):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(obj, f)
    return path


def load_cached(name):
    path = CACHE_DIR / f"{name}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def fnum(x):
    """Parse Kalshi's stringified numerics ('0.6600', '99.42') to float."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
