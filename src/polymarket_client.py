"""
src/polymarket_client.py
────────────────────────
Thin wrapper around py-clob-client with rate limiting, paper mode,
and error handling matching kalshi_client.py patterns.

Key design decisions:
  - Paper mode is the default (US Polymarket access pending).
  - get_price() preferred over get_book() (avoids ghost-market issue).
  - Injectable adapter parameter for test mocking.
  - from_config() reads polymarket section of YAML config.
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PolymarketEnvironment(Enum):
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class PolymarketMarket:
    """Normalised market representation from Polymarket."""
    condition_id: str
    token_id_yes: str
    token_id_no: str
    question: str
    slug: str
    active: bool
    closed: bool
    end_date: Optional[str] = None
    game_start_time: Optional[str] = None
    min_order_size: float = 1.0
    min_tick_size: float = 0.01
    yes_price: Optional[float] = None   # from Gamma outcomePrices
    yes_outcome_name: Optional[str] = None  # team/outcome name for outcomePrices[0]


@dataclass(frozen=True)
class PolymarketBook:
    """Order book snapshot."""
    token_id: str
    bids: List[Dict[str, float]]
    asks: List[Dict[str, float]]
    best_bid: Optional[float]
    best_ask: Optional[float]
    midpoint: Optional[float]


@dataclass(frozen=True)
class PolymarketOrderResult:
    """Result of placing an order."""
    success: bool
    order_id: Optional[str]
    error: Optional[str]
    fill_price: Optional[float]


class PolymarketClient:
    """Polymarket CLOB client wrapper.

    Mirrors KalshiClient patterns: injectable adapter for testing,
    rate limiter, paper/live modes, from_config factory.
    """

    def __init__(
        self,
        host: str = "https://clob.polymarket.com",
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        chain_id: int = 137,
        environment: PolymarketEnvironment = PolymarketEnvironment.PAPER,
        rate_limiter=None,
        adapter=None,
    ):
        self.host = host
        self.environment = environment
        self._rate_limiter = rate_limiter
        self._private_key = private_key
        self._api_key = api_key

        if adapter is not None:
            self._client = adapter
        elif private_key:
            try:
                from py_clob_client.client import ClobClient
                self._client = ClobClient(host, key=private_key, chain_id=chain_id)
                if api_key:
                    # User supplied an explicit API key — derive the full credential
                    # set from the private key first, then substitute the api_key.
                    # This handles cases where Polymarket issued a specific key ID.
                    try:
                        from py_clob_client.clob_types import ApiCreds
                        derived = self._client.create_or_derive_api_creds()
                        self._client.set_api_creds(ApiCreds(
                            api_key=api_key,
                            api_secret=derived.api_secret,
                            api_passphrase=derived.api_passphrase,
                        ))
                        logger.debug("PolymarketClient: using supplied api_key with derived secret/passphrase")
                    except (ImportError, AttributeError, TypeError, ValueError) as exc:
                        # Fall back to fully derived creds if ApiCreds substitution fails
                        creds = self._client.create_or_derive_api_creds()
                        self._client.set_api_creds(creds)
                        logger.debug("PolymarketClient: api_key substitution failed (%s), using fully derived creds", exc)
                else:
                    creds = self._client.create_or_derive_api_creds()
                    self._client.set_api_creds(creds)
            except ImportError:
                logger.warning("py_clob_client not installed; using read-only mode")
                self._client = None
            except Exception as exc:
                # Private key may be in wrong format (e.g. Builder API secret
                # instead of an Ethereum hex key).  Fall back to anonymous
                # read-only mode so price discovery still works in paper mode.
                logger.warning(
                    "PolymarketClient: wallet key auth failed (%s); "
                    "falling back to anonymous read-only mode. "
                    "For live trading supply a hex Ethereum private key.",
                    exc,
                )
                try:
                    from py_clob_client.client import ClobClient
                    self._client = ClobClient(host)
                except ImportError:
                    self._client = None
        else:
            try:
                from py_clob_client.client import ClobClient
                self._client = ClobClient(host)
            except ImportError:
                logger.warning("py_clob_client not installed; client is None")
                self._client = None

    def _wait(self):
        """Apply rate limiting if configured."""
        if self._rate_limiter:
            self._rate_limiter.wait()

    # ── Market data (no auth required) ──────────────────────────────

    # Gamma API is used for market *discovery* (supports active/closed filters).
    # CLOB API is used for order books and execution.
    GAMMA_HOST = "https://gamma-api.polymarket.com"
    _GAMMA_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BettingPodShop/1.0)"}

    @property
    def _http(self):
        """Lazy-initialized persistent HTTP client with connection pooling.

        Reuses TCP connections across requests to the Gamma API,
        avoiding the ~100ms overhead of a fresh TLS handshake per request.
        """
        if not hasattr(self, "_http_client") or self._http_client is None:
            try:
                import httpx
                self._http_client = httpx.Client(
                    headers=self._GAMMA_HEADERS,
                    timeout=15.0,
                )
            except ImportError:
                self._http_client = None
        return self._http_client

    def get_markets(
        self,
        next_cursor: Optional[str] = None,
        active_only: bool = True,
        tag_slug: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated market listing.
        Returns {'data': [...], 'next_cursor': ...}.

        active_only (default True): uses the Gamma API which supports proper
        active=true/closed=false filtering.  The CLOB /markets endpoint ignores
        those params and returns all markets (including years-old resolved ones).
        Pagination is offset-based; next_cursor encodes the integer offset as a
        decimal string so callers don't need to change their cursor logic.

        tag_slug: optional Polymarket tag slug (e.g. "nba", "nfl") to filter
        results by sport category.  When set, bypasses the politics-heavy global
        feed and fetches sport-specific markets directly.
        """
        self._wait()
        try:
            if active_only:
                http = self._http
                if http is None:
                    return {"data": [], "next_cursor": ""}
                offset = int(next_cursor) if next_cursor and str(next_cursor).lstrip("-").isdigit() else 0
                params: Dict[str, Any] = {
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                }
                if offset > 0:
                    params["offset"] = offset
                if tag_slug:
                    params["tag_slug"] = tag_slug
                resp = http.get(
                    f"{self.GAMMA_HOST}/markets",
                    params=params,
                )
                resp.raise_for_status()
                raw = resp.json()
                # Gamma returns a list directly; normalize to CLOB-style format
                if not isinstance(raw, list):
                    raw = raw.get("data", raw.get("markets", []))
                normalized = [self._normalize_gamma_market(m) for m in raw]
                # Signal end-of-results when fewer than limit items returned
                next_off = str(offset + len(raw)) if len(raw) == 100 else None
                return {"data": normalized, "next_cursor": next_off}
            # Unfiltered fallback (used by tests / explicit callers)
            if self._client is None:
                return {"data": [], "next_cursor": None}
            if next_cursor is not None:
                return self._client.get_markets(next_cursor=next_cursor)
            return self._client.get_markets()
        except Exception as e:
            logger.error("polymarket.get_markets failed: %s", e)
            return {"data": [], "next_cursor": None}

    @staticmethod
    def _normalize_gamma_market(m: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Gamma API market fields to CLOB-compatible snake_case format."""
        tokens = m.get("tokens", [])
        norm_tokens = []
        for t in tokens:
            norm_tokens.append({
                "token_id": t.get("tokenId") or t.get("token_id", ""),
                "outcome":  t.get("outcome", ""),
            })
        return {
            "condition_id":    m.get("conditionId") or m.get("condition_id", ""),
            "question":        m.get("question", ""),
            "market_slug":     m.get("slug") or m.get("market_slug", ""),
            "active":          m.get("active", False),
            "closed":          m.get("closed", False),
            "tokens":          norm_tokens if norm_tokens else tokens,
            "end_date_iso":    m.get("endDate") or m.get("end_date_iso"),
            "game_start_time": m.get("startDate") or m.get("game_start_time"),
            "description":     m.get("description", ""),
        }

    # Hardcoded fallback series_id map — used when /sports endpoint returns
    # an unexpected structure.  NBA=10345 is confirmed from Polymarket docs.
    _FALLBACK_SERIES_IDS: Dict[str, int] = {
        "NBA": 10345,
    }

    def get_sports_metadata(self) -> List[Dict[str, Any]]:
        """Fetch all sport configs from Gamma /sports endpoint.

        Returns a list of sport objects, each containing 'series' (list of
        {id, name, ...}) and 'tags' (list of {id, slug, ...}).
        The series_id is used to query /events for a specific league (e.g. NBA).
        Caches the result for the lifetime of the client.
        """
        if hasattr(self, "_sports_cache") and self._sports_cache is not None:  # type: ignore[has-type]
            return self._sports_cache  # type: ignore[has-type]
        self._wait()
        try:
            http = self._http
            if http is None:
                return []
            resp = http.get(
                f"{self.GAMMA_HOST}/sports",
            )
            resp.raise_for_status()
            raw = resp.json()

            # Diagnostic: log the raw response type and top-level structure
            if isinstance(raw, list):
                logger.info(
                    "polymarket /sports: got list of %d items, first_keys=%s",
                    len(raw),
                    sorted(raw[0].keys()) if raw else "[]",
                )
            elif isinstance(raw, dict):
                logger.info(
                    "polymarket /sports: got dict, keys=%s",
                    sorted(raw.keys()),
                )
            else:
                logger.info(
                    "polymarket /sports: unexpected type=%s",
                    type(raw).__name__,
                )

            if not isinstance(raw, list):
                raw = raw.get("data", raw.get("sports", []))
            self._sports_cache = raw

            # Log discovered leagues.
            # /sports returns items where 'series' is a STRING (league name),
            # not a list of dicts.  Structure:
            #   {id: <int>, series: "NBA", sport: "basketball", tags: [...], ...}
            seen_series = set()
            for sport_obj in raw:
                series_val = sport_obj.get("series", "")
                sport_id = sport_obj.get("id")
                sport_name = sport_obj.get("sport", "")

                if isinstance(series_val, str) and series_val:
                    # 'series' is the league name string (e.g. "NBA")
                    if series_val not in seen_series:
                        seen_series.add(series_val)
                        logger.info(
                            "polymarket /sports: series=%r sport=%r id=%s",
                            series_val, sport_name, sport_id,
                        )
                elif isinstance(series_val, list):
                    # Handle list-of-dicts structure (future-proofing)
                    for s in series_val:
                        if isinstance(s, dict):
                            sname = s.get("name", s.get("slug", ""))
                            sid = s.get("id", "?")
                            if sname and sname not in seen_series:
                                seen_series.add(sname)
                                logger.info(
                                    "polymarket /sports: series=%r id=%s (nested)",
                                    sname, sid,
                                )

            if not seen_series and raw:
                import json as _json
                sample = _json.dumps(raw[0], default=str)[:500]
                logger.info(
                    "polymarket /sports: no series found. sample=%s",
                    sample,
                )

            return raw
        except Exception as e:
            logger.error("polymarket.get_sports_metadata failed: %s", e)
            self._sports_cache = []
            return []

    def find_series_id(self, league_name: str) -> Optional[int]:
        """Look up the Polymarket series_id for a league name.

        league_name: case-insensitive match against series names from /sports
        (e.g. "NBA", "NFL", "NHL", "MLB").

        The /sports response has 'series' as a STRING (league name) and 'id'
        as the numeric identifier.  We match on the 'series' string.

        Falls back to hardcoded map if /sports doesn't provide the data.
        Returns the integer series_id, or None if not found.
        """
        metadata = self.get_sports_metadata()
        target = league_name.upper()

        for sport_obj in metadata:
            # /sports structure: series="10345" (the series_id), sport="nba" (the league abbrev)
            sport_abbrev = (sport_obj.get("sport") or "").upper()
            series_val = sport_obj.get("series", "")

            if sport_abbrev == target:
                # 'series' field holds the numeric series_id as a string
                try:
                    return int(series_val)
                except (ValueError, TypeError):
                    continue

        # Hardcoded fallback
        fallback = self._FALLBACK_SERIES_IDS.get(target)
        if fallback:
            logger.info(
                "polymarket: using fallback series_id=%d for %s",
                fallback, target,
            )
        return fallback

    def get_sport_events(
        self,
        series_id: Optional[int] = None,
        tag_id: Optional[int] = None,
        next_cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch active Polymarket events from the Gamma /events endpoint.

        Events group related markets under one game/matchup.  Each event has a
        human-readable title like "Golden State Warriors vs. Oklahoma City Thunder"
        which matches directly against Odds API event names.

        series_id: Polymarket league ID (e.g. 10345 for NBA).  Fetched
          dynamically from /sports.  This is the correct filter parameter;
          tag_slug is silently ignored by the API.

        tag_id: optional filter to e.g. game bets only (100639).

        Returns {'data': [...events...], 'next_cursor': ...}.
        Each event dict typically has: title, slug, startDate, endDate, markets[].
        """
        self._wait()
        try:
            http = self._http
            if http is None:
                return {"data": [], "next_cursor": ""}
            offset = (
                int(next_cursor)
                if next_cursor and str(next_cursor).lstrip("-").isdigit()
                else 0
            )
            params: Dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "limit": 50,
            }
            if offset > 0:
                params["offset"] = offset
            if series_id is not None:
                params["series_id"] = series_id
            if tag_id is not None:
                params["tag_id"] = tag_id
            resp = http.get(
                f"{self.GAMMA_HOST}/events",
                params=params,
            )
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = raw.get("data", raw.get("events", []))
            next_off = str(offset + len(raw)) if len(raw) == 50 else None
            return {"data": raw, "next_cursor": next_off}
        except Exception as e:
            logger.error("polymarket.get_sport_events failed: %s", e)
            return {"data": [], "next_cursor": None}

    def get_book(self, token_id: str) -> PolymarketBook:
        """Get order book for a token.  Use get_price() for fresher data."""
        self._wait()
        if self._client is None:
            return PolymarketBook(
                token_id=token_id, bids=[], asks=[],
                best_bid=None, best_ask=None, midpoint=None,
            )
        try:
            raw = self._client.get_order_book(token_id)
            bids = [{"price": float(o.price), "size": float(o.size)} for o in raw.bids]
            asks = [{"price": float(o.price), "size": float(o.size)} for o in raw.asks]
            best_bid = bids[0]["price"] if bids else None
            best_ask = asks[0]["price"] if asks else None
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
            return PolymarketBook(
                token_id=token_id, bids=bids, asks=asks,
                best_bid=best_bid, best_ask=best_ask, midpoint=mid,
            )
        except Exception as e:
            logger.error("polymarket.get_book failed: token=%s err=%s", token_id, e)
            return PolymarketBook(
                token_id=token_id, bids=[], asks=[],
                best_bid=None, best_ask=None, midpoint=None,
            )

    def get_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """Get best price.  More reliable than get_book() (avoids ghost market)."""
        self._wait()
        if self._client is None:
            return None
        try:
            return float(self._client.get_price(token_id, side))
        except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as exc:
            logger.debug("PolymarketClient: get_price failed for %s: %s", token_id, exc)
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token.

        The py-clob-client ``get_midpoint()`` returns a dict like
        ``{"mid": "0.335"}``.  We extract the ``"mid"`` value and
        convert to float.
        """
        self._wait()
        if self._client is None:
            return None
        try:
            result = self._client.get_midpoint(token_id)
            # py-clob-client returns {"mid": "0.335"} — extract the value
            if isinstance(result, dict):
                result = result.get("mid", result)
            return float(result)
        except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as exc:
            logger.debug("PolymarketClient: get_midpoint failed for %s: %s", token_id, exc)
            return None

    def get_market_resolution(
        self,
        condition_id: str,
        slug: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Look up a market via Gamma API and return resolution info.

        IMPORTANT: The Gamma ``/markets?condition_id=X`` endpoint is broken
        for sports markets — it always returns the "Will Joe Biden get
        Coronavirus" market regardless of the condition_id supplied.

        We therefore prefer to look up by **slug** first (which works
        reliably), and only fall back to condition_id if no slug is given.

        Returns a dict with:
          - closed (bool): whether the market is closed
          - resolved (bool): True if outcomePrices indicate a definitive result
          - result (str): "yes", "no", or "void" if resolved, else ""
          - outcome_prices (list[float]): [yes_price, no_price]
          - raw (dict): full Gamma market response

        Returns None if lookup fails.
        """
        self._wait()
        try:
            http = self._http
            if http is None:
                return None

            # Primary: look up by slug (reliable).
            # Fallback: look up by condition_id (broken for sports markets).
            if slug:
                params: dict = {"slug": slug, "limit": 1}
                lookup_desc = f"slug={slug}"
            else:
                params = {"condition_id": condition_id, "limit": 1}
                lookup_desc = f"condition_id={condition_id[:20]}"

            resp = http.get(
                f"{self.GAMMA_HOST}/markets",
                params=params,
            )
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list) or not raw:
                logger.debug("polymarket.get_market_resolution: no results for %s", lookup_desc)
                return None

            market = raw[0]

            # Gamma API may return booleans as strings ("true"/"false")
            # or as actual JSON booleans.  Normalise both to Python bool.
            def _to_bool(val, default: bool) -> bool:
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    return val.strip().lower() == "true"
                return default

            closed = _to_bool(market.get("closed"), False)
            active = _to_bool(market.get("active"), True)
            archived = _to_bool(market.get("archived"), False)

            # Parse outcomePrices — typically stored as JSON string '["0.95","0.05"]'
            outcome_prices_raw = market.get("outcomePrices", "")
            try:
                if isinstance(outcome_prices_raw, str):
                    import json as _json
                    outcome_prices = [float(p) for p in _json.loads(outcome_prices_raw)]
                elif isinstance(outcome_prices_raw, list):
                    outcome_prices = [float(p) for p in outcome_prices_raw]
                else:
                    outcome_prices = []
            except (ValueError, TypeError):
                outcome_prices = []

            # Determine resolution — require a clear winner: one price at
            # ≥0.95 and the other ≤0.05.  Polymarket snaps outcomePrices to
            # ["1","0"] or ["0","1"] upon resolution.
            #
            # We only require closed=True + definitive prices.  The `active`
            # flag is NOT required to be False — Polymarket sports markets
            # often stay active=True even after the game ends and the oracle
            # posts the result.  The outcome prices are the ground truth.
            resolved = False
            result = ""
            if closed and len(outcome_prices) >= 2:
                yes_price, no_price = outcome_prices[0], outcome_prices[1]
                if yes_price >= 0.95 and no_price <= 0.05:
                    resolved = True
                    result = "yes"
                elif no_price >= 0.95 and yes_price <= 0.05:
                    resolved = True
                    result = "no"
                # DO NOT treat ["0","0"] as void — that just means no
                # outcome prices are available yet for a closed market.

            logger.info(
                "polymarket.get_market_resolution: %s closed=%s active=%s "
                "archived=%s prices=%s resolved=%s result=%s",
                lookup_desc, closed, active, archived,
                outcome_prices, resolved, result,
            )

            return {
                "closed": closed,
                "active": active,
                "resolved": resolved,
                "result": result,
                "outcome_prices": outcome_prices,
                "raw": market,
            }
        except Exception as e:
            logger.error("polymarket.get_market_resolution failed: %s err=%s",
                         lookup_desc if slug else f"condition_id={condition_id}", e)
            return None

    # ── Trading (auth required) ─────────────────────────────────────

    def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
    ) -> PolymarketOrderResult:
        """Place an order.  In paper mode, logs but doesn't execute."""
        if self.environment == PolymarketEnvironment.PAPER:
            logger.info(
                "polymarket.paper_order: token=%s side=%s price=%s size=%s",
                token_id, side, price, size,
            )
            return PolymarketOrderResult(
                success=True,
                order_id=None,
                error=None,
                fill_price=price,
            )

        # Live mode
        if self._client is None:
            return PolymarketOrderResult(
                success=False, order_id=None,
                error="No authenticated client available", fill_price=None,
            )
        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            order_args = {
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": BUY if side.upper() == "BUY" else SELL,
            }
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, order_type=order_type)
            return PolymarketOrderResult(
                success=resp.get("success", False) if isinstance(resp, dict) else True,
                order_id=resp.get("orderID") if isinstance(resp, dict) else str(resp),
                error=resp.get("errorMsg") if isinstance(resp, dict) else None,
                fill_price=price,
            )
        except Exception as e:
            logger.error("polymarket.place_order failed: %s", e)
            return PolymarketOrderResult(
                success=False, order_id=None, error=str(e), fill_price=None,
            )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.  Returns True on success."""
        if self.environment == PolymarketEnvironment.PAPER:
            return True
        if self._client is None:
            return False
        try:
            return self._client.cancel(order_id)
        except Exception as exc:
            logger.warning("PolymarketClient: cancel_order failed for %s: %s", order_id, exc)
            return False

    # ── Config constructor ──────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> "PolymarketClient":
        """Build PolymarketClient from YAML config dict."""
        poly_cfg = config.get("polymarket", {})
        env_str = poly_cfg.get("environment", "paper")
        env = (
            PolymarketEnvironment.LIVE
            if env_str == "live"
            else PolymarketEnvironment.PAPER
        )
        return cls(
            host=poly_cfg.get("host", "https://clob.polymarket.com"),
            private_key=(
                poly_cfg.get("private_key")
                or os.environ.get("POLYMARKET_PRIVATE_KEY")
            ),
            api_key=(
                poly_cfg.get("api_key")
                or os.environ.get("POLYMARKET_API_KEY")
            ),
            chain_id=poly_cfg.get("chain_id", 137),
            environment=env,
        )
