"""Minimal throttled Kalshi public client for the combo (KXMVE) census.

Modeled on /tmp/bfp/src/kalshi_public.py conventions: dollars-denominated
`*_fp` fields, cursor pagination, 429/5xx backoff, exact status logging.
No auth -- all endpoints used here are public read endpoints.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE, exist_ok=True)

# Every non-200 seen, for the feasibility section of the report.
STATUS_LOG: List[Dict[str, Any]] = []


def fnum(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Kalshi:
    def __init__(self, min_interval: float = 0.12, timeout: float = 30.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "combo-research/mve-census"})
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()
        self.n_calls = 0
        self.n_429 = 0

    def _throttle(self):
        with self._lock:
            slot = max(self._last + self.min_interval, time.time())
            self._last = slot
        d = slot - time.time()
        if d > 0:
            time.sleep(d)

    def get(self, path: str, params: Optional[dict] = None,
            retries: int = 4) -> Optional[dict]:
        for attempt in range(retries + 1):
            self._throttle()
            self.n_calls += 1
            try:
                r = self.s.get(f"{BASE}{path}", params=params,
                               timeout=self.timeout)
            except requests.RequestException as exc:
                STATUS_LOG.append({"path": path, "params": params,
                                   "err": str(exc)[:200]})
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    STATUS_LOG.append({"path": path, "status": 200,
                                       "err": "non-json"})
                    return None
            STATUS_LOG.append({"path": path, "params": params,
                               "status": r.status_code,
                               "body": r.text[:300]})
            if r.status_code == 429:
                self.n_429 += 1
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(5 * (attempt + 1) if r.status_code == 429
                           else 2 ** attempt)
                continue
            return None
        return None

    def paginate(self, path: str, params: dict, key: str,
                 max_pages: int = 100000, progress_every: int = 0) -> List[dict]:
        out: List[dict] = []
        p = dict(params)
        for i in range(max_pages):
            d = self.get(path, p)
            if not d:
                break
            rows = d.get(key) or []
            out.extend(rows)
            if progress_every and (i + 1) % progress_every == 0:
                print(f"  ...{path} page {i+1}, {len(out)} rows", flush=True)
            cur = d.get("cursor")
            if not cur or not rows:
                break
            p["cursor"] = cur
        return out


def save(name: str, obj) -> str:
    p = os.path.join(CACHE, name)
    with open(p, "w") as f:
        json.dump(obj, f)
    return p


def load(name: str):
    p = os.path.join(CACHE, name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)
