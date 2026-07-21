"""
src/kalshi_client.py
────────────────────
Kalshi API client — demo mode only.

Architecture
────────────
Three cooperating layers:

  TokenBucketRateLimiter   — thread-safe token bucket; config-driven rate/burst.
  _with_retry              — exponential backoff for 429 / 5xx; injectable sleep.
  KalshiClient             — endpoint methods; injectable HTTP adapter for tests.

HTTP adapter protocol
─────────────────────
Any object with this signature works:

    def request(
        self,
        method: str,
        url: str,
        headers: dict | None = None,
        json_body: Any = None,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> _Response: ...

The default adapter is ``UrllibAdapter`` (stdlib, zero deps).
Tests inject ``MockAdapter``.

Demo guard
──────────
``place_order()`` raises ``KalshiDemoOnlyError`` if ``environment != "demo"``.
This is a hard code-level guard — it is NOT bypassable via config alone.
The live gate in Phase 7 (executor) adds a second layer on top of this.

Auth model
──────────
POST /log_in → {"token": "..."}.
Token stored in-memory; ``_authed_request()`` refreshes automatically on 401.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from logger_setup import get_logger

log = get_logger(__name__)

# ── Optional RSA signing (cryptography package) ───────────────────────────────
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as _asym_padding
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

# Kalshi API returns 429 and these 5xx codes on transient failures
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# ── Exceptions ────────────────────────────────────────────────────────────────

class KalshiError(Exception):
    """Base exception for all Kalshi client errors."""


class KalshiAPIError(KalshiError):
    """Non-retryable HTTP error from the Kalshi API (4xx except 429)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class KalshiAuthError(KalshiError):
    """Login failed or token invalid."""


class KalshiRateLimitError(KalshiError):
    """Rate limit still active after all retry attempts."""


class KalshiDemoOnlyError(KalshiError):
    """Raised when a live-only action is attempted in demo mode (and vice versa)."""


# ── HTTP response container ───────────────────────────────────────────────────

@dataclass
class _Response:
    status_code: int
    _body: bytes
    _headers: Dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self._body)

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            try:
                detail = self.json().get("error", self.text[:300])
            except Exception:
                detail = self.text[:300]
            if self.status_code == 401:
                raise KalshiAuthError(detail)
            raise KalshiAPIError(self.status_code, detail)


# ── HTTP adapters ─────────────────────────────────────────────────────────────

class UrllibAdapter:
    """
    Default HTTP adapter built on stdlib urllib.

    No external dependencies. httpx can replace this in Phase 7 by
    implementing the same ``request()`` signature.
    """

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_body: Any = None,
        params: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> _Response:
        if params:
            url = url + "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        body: Optional[bytes] = None
        h: Dict[str, str] = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            h["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=h, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _Response(
                    status_code=resp.status,
                    _body=resp.read(),
                    _headers=dict(resp.headers),
                )
        except urllib.error.HTTPError as exc:
            return _Response(
                status_code=exc.code,
                _body=exc.read(),
                _headers=dict(exc.headers),
            )


# ── Rate limiter ──────────────────────────────────────────────────────────────

class TokenBucketRateLimiter:
    """
    Thread-safe token bucket rate limiter.

    Tokens refill at ``rate_per_second`` up to ``burst`` maximum.
    Callers block in ``acquire()`` until a token is available.

    Parameters
    ----------
    rate_per_second : float
        Steady-state request rate (tokens added per second).
    burst : int
        Maximum tokens that can accumulate (allows short bursts).
    _sleep_fn : callable, optional
        Injectable sleep function (override in tests to avoid real waits).
    _monotonic_fn : callable, optional
        Injectable clock (override in tests for deterministic timing).
    """

    def __init__(
        self,
        rate_per_second: float,
        burst: int,
        _sleep_fn: Optional[Callable] = None,
        _monotonic_fn: Optional[Callable] = None,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive.")
        self._rate = rate_per_second
        self._burst = float(burst)
        self._tokens = float(burst)      # start full
        self._lock = threading.Lock()
        self._sleep = _sleep_fn or time.sleep
        self._monotonic = _monotonic_fn or time.monotonic
        self._last_refill = self._monotonic()

    def _refill(self) -> None:
        """Add tokens proportional to elapsed time. Must be called under lock."""
        now = self._monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self, timeout: float = 30.0) -> bool:
        """
        Block until a token is available or ``timeout`` seconds pass.

        Returns
        -------
        True if a token was acquired, False on timeout.
        """
        deadline = self._monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(1.0 / self._rate)

    @property
    def available_tokens(self) -> float:
        """Current token count (approximate, for logging/testing)."""
        with self._lock:
            self._refill()
            return self._tokens


# ── Retry helper ──────────────────────────────────────────────────────────────

def _with_retry(
    fn: Callable[[], _Response],
    max_attempts: int = 4,
    backoff_base: float = 1.0,
    _sleep_fn: Optional[Callable] = None,
) -> _Response:
    """
    Call ``fn()`` up to ``max_attempts`` times with exponential backoff on
    retryable HTTP status codes (429, 5xx).

    Parameters
    ----------
    fn : callable
        Zero-argument callable that performs one HTTP request and returns
        a ``_Response``.
    max_attempts : int
        Maximum total attempts (including the first).
    backoff_base : float
        Initial sleep duration in seconds; doubles each retry.
    _sleep_fn : callable, optional
        Replacement for ``time.sleep`` (use in tests to skip real waits).

    Raises
    ------
    KalshiRateLimitError
        If the final attempt still returns 429.
    KalshiAPIError
        If a non-retryable 4xx is returned on any attempt.
    """
    sleep = _sleep_fn or time.sleep
    last_response: Optional[_Response] = None

    for attempt in range(max_attempts):
        resp = fn()
        last_response = resp

        if resp.status_code not in RETRYABLE_STATUS:
            return resp

        if attempt == max_attempts - 1:
            break

        wait = backoff_base * (2 ** attempt)
        log.warning(
            "kalshi_retry",
            status_code=resp.status_code,
            attempt=attempt + 1,
            max_attempts=max_attempts,
            wait_seconds=round(wait, 2),
        )
        sleep(wait)

    # Exhausted retries
    if last_response is not None and last_response.status_code == 429:
        raise KalshiRateLimitError(
            f"Rate limit persisted after {max_attempts} attempts."
        )
    # Return last response so caller can inspect it
    return last_response  # type: ignore[return-value]


# ── Kalshi Client ─────────────────────────────────────────────────────────────

class KalshiClient:
    """
    Kalshi API wrapper — demo mode only.

    Parameters
    ----------
    base_url : str
        API base URL from config (default: demo endpoint).
    email : str
        Kalshi account email (from .env).
    password : str
        Kalshi account password (from .env).
    environment : str
        ``"demo"`` or ``"live"``.  ``place_order()`` is gated on ``"demo"``
        unless ``live_trading_confirmed=True`` (set by the Phase 7 executor).
    live_trading_confirmed : bool
        When ``True`` and ``environment="live"``, the demo guard in
        ``place_order()`` is bypassed.  Only the Executor should set this —
        after verifying the ``I_UNDERSTAND_LIVE_TRADING`` env var.
    rate_limiter : TokenBucketRateLimiter
        Pre-constructed rate limiter.
    max_retry_attempts : int
        Passed to ``_with_retry``.
    backoff_base : float
        First retry sleep in seconds.
    adapter : UrllibAdapter | MockAdapter
        HTTP adapter (injectable for tests).
    _sleep_fn : callable, optional
        Injectable sleep (used by retry logic in tests).
    """

    def __init__(
        self,
        base_url: str,
        email: str = "",
        password: str = "",
        api_key_id: str = "",
        private_key: str = "",
        environment: str = "demo",
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
        max_retry_attempts: int = 4,
        backoff_base: float = 1.0,
        adapter: Optional[Any] = None,
        _sleep_fn: Optional[Callable] = None,
        live_trading_confirmed: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._api_key_id = api_key_id
        self._private_key_pem = private_key
        self._private_key_obj: Optional[Any] = None   # loaded lazily
        # Prefer RSA auth when key credentials are present
        self._auth_mode = "rsa" if (api_key_id and private_key) else "token"
        self.environment = environment
        self.live_trading_confirmed = live_trading_confirmed
        self._rate_limiter = rate_limiter or TokenBucketRateLimiter(5, 10)
        self._max_retry_attempts = max_retry_attempts
        self._backoff_base = backoff_base
        self._adapter = adapter or UrllibAdapter()
        self._sleep = _sleep_fn or time.sleep
        self._token: Optional[str] = None

    @classmethod
    def from_config(
        cls,
        config: dict,
        email: str = "",
        password: str = "",
        api_key_id: str = "",
        private_key: str = "",
        **kwargs,
    ) -> "KalshiClient":
        """
        Construct from the loaded config.yaml dict.

        Supports two auth modes (RSA preferred when both are provided):
          RSA mode  — api_key_id + private_key  (current Kalshi API)
          Token mode — email + password          (legacy, may be deprecated)

        In main.py, pass credentials read from env vars:
          api_key_id  = os.environ.get("KALSHI_API_KEY_ID", "")
          private_key = os.environ.get("KALSHI_PRIVATE_KEY", "")
          email       = os.environ.get("KALSHI_EMAIL", "")
          password    = os.environ.get("KALSHI_PASSWORD", "")
        """
        kalshi_cfg = config.get("kalshi", {})
        rl_cfg = kalshi_cfg.get("rate_limit", {})
        retry_cfg = kalshi_cfg.get("retry", {})
        environment = config.get("environment", "demo")

        rate_limiter = TokenBucketRateLimiter(
            rate_per_second=rl_cfg.get("requests_per_second", 5),
            burst=rl_cfg.get("burst", 10),
        )
        return cls(
            base_url=kalshi_cfg.get("base_url", "https://api.elections.kalshi.com/trade-api/v2"),
            email=email,
            password=password,
            api_key_id=api_key_id,
            private_key=private_key,
            environment=environment,
            rate_limiter=rate_limiter,
            max_retry_attempts=retry_cfg.get("max_attempts", 4),
            backoff_base=retry_cfg.get("backoff_base_seconds", 1.0),
            **kwargs,
        )

    # ── Auth ──────────────────────────────────────────────────────────────────

    def login(self) -> str:
        """
        Authenticate with Kalshi and store the session token.

        Returns the token string.

        Raises
        ------
        KalshiAuthError
            If login fails (wrong credentials, etc.).
        """
        resp = self._raw_request(
            "POST",
            "/log_in",
            json_body={"email": self._email, "password": self._password},
            _skip_auth=True,
        )
        if resp.status_code != 200:
            raise KalshiAuthError(
                f"Login failed with status {resp.status_code}: {resp.text[:200]}"
            )
        token = resp.json().get("token")
        if not token:
            raise KalshiAuthError("Login response missing 'token' field.")
        self._token = token
        log.info("kalshi_login_ok", environment=self.environment)
        return token

    def _sign_request(self, method: str, path: str) -> Dict[str, str]:
        """
        Generate RSA-signed authentication headers for the Kalshi API.

        Kalshi's current auth scheme (replaces email/password token flow):
          KALSHI-ACCESS-KEY       — API key ID from Settings → API
          KALSHI-ACCESS-SIGNATURE — base64(RSA-SHA256(timestamp + METHOD + path))
          KALSHI-ACCESS-TIMESTAMP — milliseconds since epoch (str)

        Requires the ``cryptography`` package:  pip install cryptography

        Raises
        ------
        KalshiAuthError
            If the cryptography package is not installed, or the private key
            PEM string is invalid.
        """
        if not _CRYPTO_AVAILABLE:
            raise KalshiAuthError(
                "RSA signing requires the 'cryptography' package. "
                "Run: pip install cryptography"
            )
        # Load the private key lazily (once)
        if self._private_key_obj is None:
            try:
                self._private_key_obj = serialization.load_pem_private_key(
                    self._private_key_pem.encode("utf-8"),
                    password=None,
                )
            except Exception as exc:
                raise KalshiAuthError(
                    f"Failed to load RSA private key: {exc}"
                ) from exc

        ts_ms = str(int(time.time() * 1000))
        # Strip query string — Kalshi signs path only, not params
        signing_path = path.split("?")[0]
        message = (ts_ms + method.upper() + signing_path).encode("utf-8")
        signature = self._private_key_obj.sign(
            message,
            _asym_padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY":       self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        }

    # ── Endpoints ─────────────────────────────────────────────────────────────

    def list_markets(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        status: Optional[str] = "open",
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List Kalshi markets with optional filtering.

        Parameters
        ----------
        limit : int
            Page size (max 200).
        cursor : str, optional
            Pagination cursor from a previous response.
        status : str, optional
            ``"open"`` | ``"closed"`` | ``"settled"`` | None for all.
        event_ticker : str, optional
            Filter to a specific event.
        series_ticker : str, optional
            Filter to a specific series.

        Returns
        -------
        dict with keys ``"markets"`` (list) and ``"cursor"`` (str | None).
        """
        params: Dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker

        resp = self._authed_request("GET", "/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_market(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch a single market by ticker.

        Parameters
        ----------
        ticker : str
            Kalshi market ticker (e.g. ``"KXNCAABBGAME-26FEB192100UTTPAC-UTT"``).

        Returns
        -------
        dict with ``"market"`` key containing the full market object.
        Relevant fields: ``ticker``, ``title``, ``status``
        (``"open"`` | ``"closed"`` | ``"settled"``), and ``result``
        (``"yes"`` | ``"no"`` | ``"void"`` | ``""`` when not yet settled).
        """
        resp = self._authed_request("GET", f"/markets/{ticker}")
        resp.raise_for_status()
        return resp.json()

    def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        """
        Fetch the orderbook for a specific market.

        Parameters
        ----------
        ticker : str
            Kalshi market ticker (e.g. ``"INXD-24DEC31-B4500"``).
        depth : int
            Number of price levels to return (max 25).

        Returns
        -------
        dict with ``"orderbook"`` containing ``"yes"`` and ``"no"`` ladders.
        """
        resp = self._authed_request(
            "GET",
            f"/markets/{ticker}/orderbook",
            params={"depth": depth},
        )
        resp.raise_for_status()
        return resp.json()

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an order — DEMO MODE ONLY.

        Parameters
        ----------
        order : dict
            Order payload as per Kalshi API spec::

                {
                    "ticker": "MARKET-TICKER",
                    "action": "buy",
                    "side": "yes",
                    "type": "limit",
                    "count": 10,
                    "yes_price": 55,   # cents (1–99)
                }

        Returns
        -------
        dict with ``"order"`` containing the created order details.

        Raises
        ------
        KalshiDemoOnlyError
            If ``environment != "demo"`` — live orders must go through
            the Phase 7 executor which adds interactive confirmation.
        """
        if self.environment != "demo" and not self.live_trading_confirmed:
            raise KalshiDemoOnlyError(
                "place_order() is restricted to demo mode. "
                "Use the Phase 7 executor for live trading."
            )
        resp = self._authed_request("POST", "/orders", json_body=order)
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> Dict[str, Any]:
        """
        Fetch current open positions for the authenticated account.

        Returns
        -------
        dict with ``"positions"`` list.
        """
        resp = self._authed_request("GET", "/portfolio/positions")
        resp.raise_for_status()
        return resp.json()

    def get_balance(self) -> Dict[str, Any]:
        """
        Fetch account balance.

        Returns
        -------
        dict with ``"balance"`` in cents (integer).
        """
        resp = self._authed_request("GET", "/portfolio/balance")
        resp.raise_for_status()
        return resp.json()

    def ping(self) -> bool:
        """
        Connectivity check — hits /markets with limit=1.

        Returns True on success, False on any failure.
        """
        try:
            resp = self._raw_request("GET", "/markets", params={"limit": "1"}, _skip_auth=True)
            return resp.status_code < 500
        except Exception as exc:
            log.warning("kalshi_ping_failed", error=str(exc))
            return False

    # ── Internal request machinery ────────────────────────────────────────────

    def _authed_request(self, method: str, path: str, **kwargs) -> _Response:
        """
        Make an authenticated request.

        RSA mode  — signs every request with the private key (no token needed).
        Token mode — uses Bearer token; auto-refreshes on 401.
        """
        if self._auth_mode == "rsa":
            rsa_headers = self._sign_request(method, path)
            return self._raw_request(method, path, _extra_headers=rsa_headers, **kwargs)

        # Token (legacy email/password) mode
        if self._token is None:
            self.login()

        resp = self._raw_request(method, path, **kwargs)

        if resp.status_code == 401:
            log.info("kalshi_token_expired_refreshing")
            self.login()
            resp = self._raw_request(method, path, **kwargs)

        return resp

    def _raw_request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        _skip_auth: bool = False,
        _extra_headers: Optional[Dict[str, str]] = None,
    ) -> _Response:
        """
        Rate-limited, retried HTTP request.

        Parameters
        ----------
        _skip_auth : bool
            If True, omit the Authorization header (used for /log_in).
        _extra_headers : dict, optional
            Additional headers merged in (used for RSA auth headers).
        """
        if not self._rate_limiter.acquire(timeout=30.0):
            raise KalshiRateLimitError("Timed out waiting for rate limit token.")

        url = self.base_url + path
        headers: Dict[str, str] = {"Accept": "application/json"}
        if not _skip_auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if _extra_headers:
            headers.update(_extra_headers)

        def _do_request() -> _Response:
            return self._adapter.request(
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                params={str(k): str(v) for k, v in (params or {}).items()},
            )

        return _with_retry(
            _do_request,
            max_attempts=self._max_retry_attempts,
            backoff_base=self._backoff_base,
            _sleep_fn=self._sleep,
        )
