"""
src/kalshi_tennis_client.py
───────────────────────────
Thin client for Kalshi's PUBLIC market-data API (no auth required).

Used by P-015 (Qualifier Favorite) for market discovery and by
KalshiTennisSettler for result polling.  Deliberately dependency-free
(urllib only) and read-only: paper-mode order placement never touches
the exchange, and live placement (if ever enabled) would go through the
authenticated client, not this one.

API notes (verified Jul 2026):
  * Base: https://api.elections.kalshi.com/trade-api/v2
  * Prices arrive as dollar strings in *_dollars fields
    ("yes_ask_dollars": "0.9400"); sizes in *_fp fields.
  * `occurrence_datetime` == scheduled match start; on older payloads it
    is absent and `expected_expiration_time` carries the same value.
  * Settled markets report result in the `result` field ("yes"/"no").
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"

TENNIS_MATCH_SERIES = ("KXATPMATCH", "KXWTAMATCH")


def parse_dollars(market: Dict[str, Any], field: str) -> Optional[float]:
    """Parse a '*_dollars' string field into a float, None if absent."""
    v = market.get(field)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class KalshiTennisClient:
    """Read-only Kalshi public API client for tennis match markets."""

    def __init__(
        self,
        base_url: str = PUBLIC_BASE,
        timeout: float = 15.0,
        max_retries: int = 2,
        _sleep_fn=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = _sleep_fn

    # ── HTTP ─────────────────────────────────────────────────────────

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        url = self.base_url + path
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "betting-pod-shop/P-015"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    self._sleep(1.5 * (attempt + 1))
                    continue
                logger.warning("kalshi_tennis: HTTP %s for %s", exc.code, path)
                return None
            except Exception as exc:
                if attempt < self.max_retries:
                    self._sleep(0.5)
                    continue
                logger.warning("kalshi_tennis: GET %s failed: %s", path, exc)
                return None
        return None

    # ── Market data ──────────────────────────────────────────────────

    def get_open_match_markets(
        self, series: tuple = TENNIS_MATCH_SERIES
    ) -> List[Dict[str, Any]]:
        """All open tennis match-winner markets across the given series."""
        out: List[Dict[str, Any]] = []
        for s in series:
            cursor = ""
            while True:
                path = f"/markets?series_ticker={s}&status=open&limit=1000"
                if cursor:
                    path += f"&cursor={cursor}"
                d = self._get(path)
                if d is None:
                    break
                markets = d.get("markets", [])
                for m in markets:
                    m["_series"] = s
                out.extend(markets)
                cursor = d.get("cursor") or ""
                if not cursor:
                    break
        return out

    def get_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Single market by ticker (used for settlement result polling)."""
        d = self._get(f"/markets/{ticker}")
        if d is None:
            return None
        return d.get("market")

    @classmethod
    def from_config(cls, config: dict) -> "KalshiTennisClient":
        cfg = (config.get("pods", {}).get("P-015", {}) or {}).get("api", {})
        return cls(
            base_url=cfg.get("base_url", PUBLIC_BASE),
            timeout=float(cfg.get("timeout", 15.0)),
        )
