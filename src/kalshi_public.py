"""
src/kalshi_public.py
────────────────────
Minimal throttled client for Kalshi PUBLIC (unauthenticated) market-data
endpoints, used by P-016 (Live Maker).  Modeled on the verified client in
kalshi-ev-map/src/kalshi_client.py (response shapes checked live 2026-07).

Paper trading only — this client cannot place orders.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def fnum(x) -> Optional[float]:
    """Parse Kalshi's stringified numerics ('0.4700') to float."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class KalshiPublic:
    def __init__(self, min_interval: float = 0.25, timeout: float = 15.0):
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "betting-pod-shop/P-016-live-maker"}
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
            retries: int = 3,
            on_status: Optional[callable] = None) -> Optional[dict]:
        """GET a public endpoint.

        `on_status` is an optional callback invoked with each HTTP status code
        seen.  It exists so a caller can react to 429s (the book-capture
        daemon throttles itself down on them).  Default None preserves the
        exact prior behaviour for P-016 and every other existing caller.
        """
        for attempt in range(retries + 1):
            self._throttle()
            try:
                resp = self._session.get(
                    f"{BASE}{path}", params=params, timeout=self._timeout
                )
            except requests.RequestException as exc:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                logger.warning("kalshi_public %s failed: %s", path, exc)
                return None
            if on_status is not None:
                try:
                    on_status(resp.status_code)
                except Exception:       # a bad hook must never break a fetch
                    logger.debug("on_status hook raised", exc_info=True)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    return None
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(4 * (attempt + 1) if resp.status_code == 429
                           else 2 ** attempt)
                continue
            logger.warning("kalshi_public %s -> HTTP %s", path, resp.status_code)
            return None
        return None

    # ── Market data ──────────────────────────────────────────────────

    def open_markets(self, series_ticker: str,
                     max_pages: int = 10) -> List[Dict[str, Any]]:
        """All open markets for a series (cursor-paginated)."""
        out: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {
            "series_ticker": series_ticker, "status": "open", "limit": 200,
        }
        for _ in range(max_pages):
            data = self.get("/markets", params)
            if not data:
                break
            out.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return out

    def open_events(self, series_ticker: str,
                    max_pages: int = 10) -> List[Dict[str, Any]]:
        """All open events with their markets for a series."""
        out: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": "open",
            "with_nested_markets": "true",
            "limit": 200,
        }
        for _ in range(max_pages):
            data = self.get("/events", params)
            if data is None:
                raise RuntimeError("Kalshi event request failed")
            out.extend(item for item in data.get("events", [])
                       if isinstance(item, dict))
            cursor = data.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return out

    def get_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Single market by ticker, or None.

        Use this — NOT /markets list filtering — to read settlement state.
        The LIST endpoints null out price/volume (and are unreliable for
        `result`) on settled markets; the per-ticker GET carries the real
        `status` / `result`.
        """
        data = self.get(f"/markets/{ticker}")
        if not data:
            return None
        return data.get("market") or None

    def orderbook(self, ticker: str, depth: int = 5) -> Optional[dict]:
        """Best bid/ask (YES perspective, dollars) + top-level depth.

        Returns {yes_bid, yes_ask, bid_qty, ask_qty} or None.
        The 'yes' side holds resting YES bids; the 'no' side holds resting
        NO bids, so best YES ask = 1 - best NO bid.
        """
        ob = self.get(f"/markets/{ticker}/orderbook", {"depth": depth})
        if not ob:
            return None
        book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
        yes_levels = book.get("yes_dollars") or book.get("yes") or []
        no_levels = book.get("no_dollars") or book.get("no") or []

        def _best(levels):
            best_px, best_qty = None, 0.0
            for lvl in levels or []:
                try:
                    px, qty = fnum(lvl[0]), fnum(lvl[1]) or 0.0
                except (TypeError, IndexError):
                    continue
                if px is None:
                    continue
                if px > 1.5:          # cents-denominated fallback
                    px = px / 100.0
                if best_px is None or px > best_px:
                    best_px, best_qty = px, qty
            return best_px, best_qty

        yes_bid, bid_qty = _best(yes_levels)
        no_bid, no_qty = _best(no_levels)
        yes_ask = (1.0 - no_bid) if no_bid is not None else None
        return {
            "yes_bid": yes_bid, "yes_ask": yes_ask,
            "bid_qty": bid_qty, "ask_qty": no_qty,
        }

    def trades_since(self, ticker: str, min_epoch: float,
                     max_pages: int = 5) -> List[Dict[str, Any]]:
        """Public trade prints for `ticker` newer than `min_epoch`,
        oldest-first.  Endpoint returns newest-first; we walk back until
        the cutoff."""
        out: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {"ticker": ticker, "limit": 100}
        done = False
        for _ in range(max_pages):
            data = self.get("/markets/trades", params)
            if not data:
                break
            trades = data.get("trades", [])
            if not trades:
                break
            for t in trades:
                ts = _parse_iso(t.get("created_time"))
                if ts is None:
                    continue
                if ts <= min_epoch:
                    done = True
                    break
                px = fnum(t.get("yes_price_dollars"))
                if px is None:
                    raw = fnum(t.get("yes_price"))
                    px = raw / 100.0 if raw is not None else None
                cnt = fnum(t.get("count_fp")) or fnum(t.get("count")) or 0.0
                if px is None or cnt <= 0:
                    continue
                out.append({
                    "epoch": ts,
                    "yes_price": px,
                    "count": cnt,
                    "taker_side": t.get("taker_side", ""),
                    "trade_id": t.get("trade_id", ""),
                })
            if done:
                break
            cursor = data.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        out.sort(key=lambda t: t["epoch"])
        return out


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
