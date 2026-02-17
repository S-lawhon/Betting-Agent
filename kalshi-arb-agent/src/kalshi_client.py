"""Kalshi API client with authentication, rate limiting, and retry logic."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from src.logger_setup import get_logger
from src.utils import KalshiMarket, RateLimiter

logger = get_logger("kalshi_client")

# API base URLs
DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    """Async client for the Kalshi trading API (v2).

    Handles authentication, rate limiting, retries with exponential backoff,
    and provides typed methods for market data and order management.
    """

    def __init__(
        self,
        api_key_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
        env: str = "demo",
        rate_limit_per_min: int = 30,
    ):
        self.api_key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID", "")
        self.private_key_path = private_key_path or os.environ.get(
            "KALSHI_API_PRIVATE_KEY_PATH", ""
        )
        self.env = env or os.environ.get("KALSHI_ENV", "demo")
        self.base_url = PROD_BASE_URL if self.env == "prod" else DEMO_BASE_URL

        self.rate_limiter = RateLimiter(max_calls=rate_limit_per_min, period_seconds=60.0)
        self._client: Optional[httpx.AsyncClient] = None
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _ensure_auth(self) -> dict[str, str]:
        """Return auth headers, refreshing token if needed."""
        if not self.api_key_id:
            raise ValueError(
                "KALSHI_API_KEY_ID not set. Export it as an environment variable."
            )

        now = time.time()
        if self._token and now < self._token_expiry:
            return {"Authorization": f"Bearer {self._token}"}

        # Kalshi uses API key auth via login endpoint
        client = await self._get_client()
        try:
            # Try RSA key-based auth if private key path is set
            if self.private_key_path and os.path.exists(self.private_key_path):
                headers = self._build_rsa_headers()
                return headers

            # Fall back to API key ID + secret login
            resp = await client.post(
                "/login",
                json={
                    "email": self.api_key_id,
                    "password": self.private_key_path,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("token", "")
            # Tokens typically last 24h; refresh after 23h
            self._token_expiry = now + 23 * 3600
            logger.info("Authenticated with Kalshi API (login)")
            return {"Authorization": f"Bearer {self._token}"}

        except Exception as exc:
            logger.error(f"Kalshi authentication failed: {exc}")
            raise

    def _build_rsa_headers(self) -> dict[str, str]:
        """Build RSA-signed auth headers for API key authentication.

        Kalshi's API key auth uses the key ID directly in a bearer header
        for demo, or RSA-signed timestamps for production.
        """
        # For demo environment, use simple bearer token with key ID
        if self.env == "demo":
            return {"Authorization": f"Bearer {self.api_key_id}"}

        # For production, build RSA-signed headers
        try:
            import hashlib
            import base64
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            timestamp = str(int(time.time() * 1000))

            with open(self.private_key_path, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)

            # Sign the timestamp
            msg = timestamp.encode("utf-8")
            signature = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
            sig_b64 = base64.b64encode(signature).decode("utf-8")

            return {
                "KALSHI-ACCESS-KEY": self.api_key_id,
                "KALSHI-ACCESS-SIGNATURE": sig_b64,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
            }
        except ImportError:
            logger.warning(
                "cryptography package not installed; falling back to key-id auth"
            )
            return {"Authorization": f"Bearer {self.api_key_id}"}

    # ------------------------------------------------------------------
    # HTTP helpers with retry
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        retry_attempts: int = 3,
    ) -> dict[str, Any]:
        """Make an authenticated, rate-limited request with retry on failure."""
        await self.rate_limiter.acquire()
        auth_headers = await self._ensure_auth()
        client = await self._get_client()

        last_exc: Optional[Exception] = None
        for attempt in range(retry_attempts):
            try:
                resp = await client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers=auth_headers,
                )

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited (429), waiting {wait}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        f"Server error {resp.status_code}, retrying in {wait}s "
                        f"(attempt {attempt + 1})"
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in (429, 500, 502, 503, 504):
                    wait = 2 ** (attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                raise
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_exc = exc
                wait = 2 ** (attempt + 1)
                logger.warning(f"Connection error: {exc}, retrying in {wait}s")
                await asyncio.sleep(wait)

        raise RuntimeError(
            f"Request {method} {path} failed after {retry_attempts} attempts: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        limit: int = 50,
        cursor: Optional[str] = None,
        series_ticker: Optional[str] = None,
        status: str = "open",
    ) -> dict[str, Any]:
        """Fetch open markets with optional filters."""
        params: dict[str, Any] = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        return await self._request("GET", "/markets", params=params)

    async def get_market(self, ticker: str) -> dict[str, Any]:
        """Fetch a specific market by ticker."""
        return await self._request("GET", f"/markets/{ticker}")

    async def get_orderbook(self, ticker: str) -> dict[str, Any]:
        """Fetch the orderbook for a specific market."""
        return await self._request("GET", f"/markets/{ticker}/orderbook")

    async def get_all_sports_markets(
        self, batch_size: int = 50
    ) -> list[KalshiMarket]:
        """Fetch all open sports-related markets, handling pagination."""
        all_markets: list[KalshiMarket] = []
        cursor: Optional[str] = None

        while True:
            try:
                data = await self.get_markets(limit=batch_size, cursor=cursor)
            except Exception as exc:
                logger.error(f"Failed fetching markets: {exc}")
                break

            markets = data.get("markets", [])
            if not markets:
                break

            for m in markets:
                try:
                    km = self._parse_market(m)
                    if km:
                        all_markets.append(km)
                except Exception as exc:
                    logger.debug(f"Skipping unparseable market: {exc}")

            cursor = data.get("cursor")
            if not cursor:
                break

        logger.info(f"Fetched {len(all_markets)} Kalshi markets")
        return all_markets

    def _parse_market(self, raw: dict[str, Any]) -> Optional[KalshiMarket]:
        """Parse raw API response into a KalshiMarket object."""
        ticker = raw.get("ticker", "")
        title = raw.get("title", "")
        subtitle = raw.get("subtitle", "")

        yes_price = raw.get("yes_bid", 0) or raw.get("last_price", 0) or 0
        no_price = raw.get("no_bid", 0) or 0
        if yes_price == 0 and no_price == 0:
            yes_price = raw.get("last_price", 0.5) or 0.5
            no_price = 1.0 - yes_price

        # Normalize prices to 0-1 range
        if yes_price > 1:
            yes_price = yes_price / 100.0
        if no_price > 1:
            no_price = no_price / 100.0

        close_time = None
        close_str = raw.get("close_time") or raw.get("expiration_time")
        if close_str:
            try:
                close_time = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        volume = raw.get("volume", 0) or 0
        open_interest = raw.get("open_interest", 0) or 0

        return KalshiMarket(
            ticker=ticker,
            title=title,
            subtitle=subtitle,
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            open_interest=open_interest,
            close_time=close_time,
            status=raw.get("status", "open"),
            category=raw.get("category", ""),
            series_ticker=raw.get("series_ticker", ""),
            result=raw.get("result"),
            yes_ask=raw.get("yes_ask"),
            yes_bid=raw.get("yes_bid"),
            no_ask=raw.get("no_ask"),
            no_bid=raw.get("no_bid"),
        )

    # ------------------------------------------------------------------
    # Portfolio / orders
    # ------------------------------------------------------------------

    async def get_positions(self) -> list[dict[str, Any]]:
        """Fetch current open positions."""
        data = await self._request("GET", "/portfolio/positions")
        positions = data.get("market_positions", [])
        logger.debug(f"Fetched {len(positions)} open positions")
        return positions

    async def get_balance(self) -> dict[str, Any]:
        """Fetch account balance."""
        return await self._request("GET", "/portfolio/balance")

    async def place_order(
        self,
        ticker: str,
        side: str,
        action: str = "buy",
        order_type: str = "limit",
        count: int = 1,
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        expiration_ts: Optional[int] = None,
    ) -> dict[str, Any]:
        """Place an order on a Kalshi market.

        Args:
            ticker: Market ticker.
            side: "yes" or "no".
            action: "buy" or "sell".
            order_type: "limit" or "market".
            count: Number of contracts.
            yes_price: Limit price in cents for YES side (1-99).
            no_price: Limit price in cents for NO side (1-99).
            expiration_ts: Unix timestamp for order expiry.
        """
        body: dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "type": order_type,
            "count": count,
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        if expiration_ts is not None:
            body["expiration_ts"] = expiration_ts

        logger.info(
            f"Placing order: {action} {count}x {side} on {ticker} "
            f"@ yes={yes_price} no={no_price} ({order_type})",
            extra={"ticker": ticker, "side": side, "action": "place_order"},
        )
        return await self._request("POST", "/portfolio/orders", json_body=body)

    async def get_orders(
        self, ticker: Optional[str] = None, status: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Fetch orders, optionally filtered by ticker or status."""
        params: dict[str, Any] = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        data = await self._request("GET", "/portfolio/orders", params=params)
        return data.get("orders", [])

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel a pending order."""
        logger.info(f"Cancelling order {order_id}", extra={"order_id": order_id})
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get status of a specific order."""
        return await self._request("GET", f"/portfolio/orders/{order_id}")
