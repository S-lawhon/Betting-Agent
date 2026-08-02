"""Read-only client for Gemini's public prediction-market catalogue.

This module intentionally exposes no authentication, account, funding, or
order methods.  It is a research data source, not an execution client.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import requests


logger = logging.getLogger(__name__)
BASE = "https://api.gemini.com/v1"


class GeminiPublic:
    """Small throttled client for unauthenticated Gemini endpoints."""

    def __init__(self, min_interval: float = 0.25, timeout: float = 15.0):
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "betting-pod-shop/gemini-crossvenue-research"}
        )
        self._min_interval = min_interval
        self._timeout = timeout
        self._last_ts = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            slot = max(self._last_ts + self._min_interval, time.time())
            self._last_ts = slot
        delay = slot - time.time()
        if delay > 0:
            time.sleep(delay)

    def get(self, path: str, params: Optional[dict] = None,
            retries: int = 3) -> Optional[dict]:
        for attempt in range(retries + 1):
            self._throttle()
            try:
                response = self._session.get(
                    f"{BASE}{path}", params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                logger.warning("gemini_public %s failed: %s", path, exc)
                return None
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    logger.warning("gemini_public %s returned invalid JSON", path)
                    return None
                return payload if isinstance(payload, dict) else {"data": payload}
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(4 * (attempt + 1) if response.status_code == 429
                           else 2 ** attempt)
                continue
            logger.warning("gemini_public %s -> HTTP %s", path,
                           response.status_code)
            return None
        return None

    def active_prediction_events(self, limit: int = 500,
                                 max_pages: int = 10) -> List[Dict[str, Any]]:
        """Return active prediction events using offset pagination."""
        limit = max(1, min(int(limit), 500))
        events: List[Dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            payload = self.get(
                "/prediction-markets/events",
                {"status": "active", "limit": limit, "offset": offset},
            )
            if payload is None:
                raise RuntimeError("Gemini prediction-market request failed")
            page = payload.get("data")
            if page is None:
                page = payload.get("events")
            if not isinstance(page, list):
                raise RuntimeError("Gemini prediction-market response has no event list")
            events.extend(item for item in page if isinstance(item, dict))
            pagination = payload.get("pagination") or {}
            total = pagination.get("total")
            if len(page) < limit or (isinstance(total, int) and len(events) >= total):
                break
            offset += limit
        return events
