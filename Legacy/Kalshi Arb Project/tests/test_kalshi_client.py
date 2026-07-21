"""
tests/test_kalshi_client.py
────────────────────────────
Phase 3 must-pass tests:
  ✓ MockAdapter records calls and serves canned responses
  ✓ Rate limiter: token bucket refills correctly
  ✓ Rate limiter: blocks when empty, unblocks after refill
  ✓ Retry: skips retry on 2xx / non-retryable 4xx
  ✓ Retry: retries on 429 and 5xx with exponential backoff
  ✓ Retry: raises KalshiRateLimitError after max attempts on 429
  ✓ Client: login() stores token, raises KalshiAuthError on failure
  ✓ Client: authed requests include Authorization header
  ✓ Client: auto-refreshes token on 401
  ✓ Client: list_markets passes params correctly
  ✓ Client: get_orderbook targets correct URL
  ✓ Client: place_order blocked outside demo mode (KalshiDemoOnlyError)
  ✓ Client: place_order succeeds in demo mode
  ✓ Client: get_positions / get_balance parse responses
  ✓ Client: from_config wires all params from config.yaml
  ✓ Client: ping() returns True on 2xx, False on 5xx
"""

import json
import sys
import time
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.kalshi_client import (
    KalshiAPIError,
    KalshiAuthError,
    KalshiClient,
    KalshiDemoOnlyError,
    KalshiRateLimitError,
    TokenBucketRateLimiter,
    _Response,
    _with_retry,
)


# ── Test doubles ──────────────────────────────────────────────────────────────

def _resp(status: int, body: Any = None, headers: dict = None) -> _Response:
    """Convenience: build a _Response with a JSON body."""
    raw = json.dumps(body if body is not None else {}).encode()
    return _Response(status_code=status, _body=raw, _headers=headers or {})


class MockAdapter:
    """
    HTTP adapter test double.

    Queue up responses with ``add_response()`` or ``add_json()``.
    Inspect recorded calls via ``self.calls``.
    """

    def __init__(self) -> None:
        self._queue: List[_Response] = []
        self.calls: List[Dict[str, Any]] = []

    def add_response(self, response: _Response) -> "MockAdapter":
        self._queue.append(response)
        return self

    def add_json(self, status: int, body: Any) -> "MockAdapter":
        return self.add_response(_resp(status, body))

    def request(
        self,
        method: str,
        url: str,
        headers: dict = None,
        json_body: Any = None,
        params: dict = None,
        timeout: float = 10.0,
    ) -> _Response:
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers or {},
            "json_body": json_body,
            "params": params or {},
        })
        if not self._queue:
            raise AssertionError(
                f"MockAdapter has no more responses queued "
                f"(call #{len(self.calls)}: {method} {url})"
            )
        return self._queue.pop(0)


def _make_client(
    adapter: MockAdapter,
    environment: str = "demo",
    token: str = "test-token",
    sleep_fn=None,
) -> KalshiClient:
    """Build a KalshiClient wired to a MockAdapter with a pre-seeded token."""
    rl = TokenBucketRateLimiter(100, 100)   # effectively unlimited for tests
    client = KalshiClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        email="test@example.com",
        password="secret",
        environment=environment,
        rate_limiter=rl,
        max_retry_attempts=3,
        backoff_base=0.0,           # no real sleeps in tests
        adapter=adapter,
        _sleep_fn=sleep_fn or (lambda _: None),
    )
    client._token = token           # skip login for most tests
    return client


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — _Response
# ═══════════════════════════════════════════════════════════════════════════════

class TestResponse(unittest.TestCase):

    def test_json_parses_body(self):
        r = _resp(200, {"key": "value"})
        self.assertEqual(r.json(), {"key": "value"})

    def test_text_decodes_body(self):
        r = _Response(200, b"hello world", {})
        self.assertEqual(r.text, "hello world")

    def test_raise_for_status_passthrough_on_2xx(self):
        _resp(200, {}).raise_for_status()  # should not raise

    def test_raise_for_status_raises_api_error_on_4xx(self):
        r = _resp(404, {"error": "not found"})
        with self.assertRaises(KalshiAPIError) as ctx:
            r.raise_for_status()
        self.assertEqual(ctx.exception.status_code, 404)

    def test_raise_for_status_raises_auth_error_on_401(self):
        r = _resp(401, {"error": "unauthorized"})
        with self.assertRaises(KalshiAuthError):
            r.raise_for_status()

    def test_raise_for_status_raises_on_5xx(self):
        r = _resp(500, {"error": "internal"})
        with self.assertRaises(KalshiAPIError) as ctx:
            r.raise_for_status()
        self.assertEqual(ctx.exception.status_code, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TokenBucketRateLimiter
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenBucket(unittest.TestCase):

    def _make_rl(self, rate=5.0, burst=5, initial_tokens=None):
        """Build a rate limiter with injectable clock and no-op sleep."""
        self._now = [0.0]  # mutable so the lambda can read updates
        sleeps = []

        rl = TokenBucketRateLimiter(
            rate_per_second=rate,
            burst=burst,
            _sleep_fn=lambda s: sleeps.append(s),
            _monotonic_fn=lambda: self._now[0],
        )
        if initial_tokens is not None:
            rl._tokens = float(initial_tokens)
        rl._sleeps = sleeps
        return rl

    def test_rejects_nonpositive_rate(self):
        with self.assertRaises(ValueError):
            TokenBucketRateLimiter(0, 5)
        with self.assertRaises(ValueError):
            TokenBucketRateLimiter(-1, 5)

    def test_starts_full(self):
        rl = self._make_rl(rate=5, burst=10)
        self.assertAlmostEqual(rl.available_tokens, 10.0)

    def test_acquire_consumes_token(self):
        rl = self._make_rl(rate=5, burst=5)
        acquired = rl.acquire(timeout=0.0)
        self.assertTrue(acquired)
        self.assertAlmostEqual(rl.available_tokens, 4.0, places=1)

    def test_acquire_all_burst(self):
        rl = self._make_rl(rate=5, burst=3)
        for _ in range(3):
            self.assertTrue(rl.acquire(timeout=0.0))
        # Bucket empty — should time out immediately
        result = rl.acquire(timeout=0.0)
        self.assertFalse(result)

    def test_tokens_refill_over_time(self):
        rl = self._make_rl(rate=5, burst=5, initial_tokens=0)
        # Advance clock by 1 second — should add 5 tokens (burst cap)
        self._now[0] = 1.0
        self.assertAlmostEqual(rl.available_tokens, 5.0, places=1)

    def test_tokens_capped_at_burst(self):
        rl = self._make_rl(rate=10, burst=5, initial_tokens=5)
        self._now[0] = 10.0   # 100 tokens would be added, but cap is 5
        self.assertAlmostEqual(rl.available_tokens, 5.0, places=1)

    def test_thread_safety(self):
        """
        Multiple threads acquiring tokens concurrently must not corrupt state.

        The real safety invariant is that _tokens never goes negative.
        We don't assert on exact success count because tokens refill during
        thread execution (real clock, rate=100/s over ~50ms window adds tokens).
        """
        rl = TokenBucketRateLimiter(rate_per_second=100, burst=10)
        results = []

        def _acquire():
            results.append(rl.acquire(timeout=0.05))

        threads = [threading.Thread(target=_acquire) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Core invariant: internal token count must never go negative
        with rl._lock:
            self.assertGreaterEqual(rl._tokens, -0.001,
                "Token count went negative — mutex is broken")

        # Sanity: all 20 threads should have got a definite True/False
        self.assertEqual(len(results), 20)
        self.assertTrue(all(isinstance(r, bool) for r in results))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — _with_retry
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetry(unittest.TestCase):

    def _retry(self, responses, max_attempts=3):
        """Run _with_retry with a sequence of pre-canned responses."""
        it = iter(responses)
        sleeps = []

        result = _with_retry(
            fn=lambda: next(it),
            max_attempts=max_attempts,
            backoff_base=1.0,
            _sleep_fn=lambda s: sleeps.append(s),
        )
        return result, sleeps

    def test_no_retry_on_200(self):
        result, sleeps = self._retry([_resp(200, {"ok": True})])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(sleeps, [])

    def test_no_retry_on_400(self):
        result, sleeps = self._retry([_resp(400, {"error": "bad"})])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(sleeps, [])

    def test_retries_on_429(self):
        responses = [_resp(429), _resp(429), _resp(200, {"ok": True})]
        result, sleeps = self._retry(responses, max_attempts=3)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(sleeps), 2)

    def test_retries_on_500(self):
        responses = [_resp(500), _resp(200, {"ok": True})]
        result, sleeps = self._retry(responses, max_attempts=3)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(sleeps), 1)

    def test_exponential_backoff_sequence(self):
        """Sleep durations must double each attempt: 1, 2, 4, ..."""
        responses = [_resp(503)] * 3 + [_resp(200)]
        _, sleeps = self._retry(responses, max_attempts=4)
        self.assertEqual(sleeps, [1.0, 2.0, 4.0])

    def test_raises_rate_limit_error_after_max_attempts_on_429(self):
        responses = [_resp(429)] * 4
        with self.assertRaises(KalshiRateLimitError):
            _with_retry(
                fn=lambda r=iter(responses): next(r),
                max_attempts=4,
                backoff_base=0.0,
                _sleep_fn=lambda _: None,
            )

    def test_returns_last_5xx_after_exhaustion(self):
        """If final response is 503 (not 429), return it instead of raising."""
        responses = [_resp(503)] * 3
        result, _ = self._retry(responses, max_attempts=3)
        self.assertEqual(result.status_code, 503)

    def test_retries_on_502_and_504(self):
        for status in [502, 504]:
            with self.subTest(status=status):
                responses = [_resp(status), _resp(200, {"ok": True})]
                result, _ = self._retry(responses, max_attempts=2)
                self.assertEqual(result.status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — KalshiClient auth
# ═══════════════════════════════════════════════════════════════════════════════

class TestKalshiClientAuth(unittest.TestCase):

    def test_login_stores_token(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"token": "my-secret-token"})

        rl = TokenBucketRateLimiter(100, 100)
        client = KalshiClient(
            base_url="https://demo-api.kalshi.co/trade-api/v2",
            email="user@test.com",
            password="pass",
            rate_limiter=rl,
            adapter=adapter,
            _sleep_fn=lambda _: None,
        )
        token = client.login()
        self.assertEqual(token, "my-secret-token")
        self.assertEqual(client._token, "my-secret-token")

    def test_login_raises_auth_error_on_401(self):
        adapter = MockAdapter()
        adapter.add_json(401, {"error": "invalid credentials"})
        rl = TokenBucketRateLimiter(100, 100)
        client = KalshiClient(
            "https://demo-api.kalshi.co/trade-api/v2",
            "u@t.com", "bad",
            rate_limiter=rl, adapter=adapter,
            _sleep_fn=lambda _: None,
        )
        with self.assertRaises(KalshiAuthError):
            client.login()

    def test_login_raises_auth_error_on_missing_token_field(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"not_token": "oops"})
        rl = TokenBucketRateLimiter(100, 100)
        client = KalshiClient(
            "https://demo-api.kalshi.co/trade-api/v2",
            "u@t.com", "pass",
            rate_limiter=rl, adapter=adapter,
            _sleep_fn=lambda _: None,
        )
        with self.assertRaises(KalshiAuthError):
            client.login()

    def test_authed_requests_include_bearer_token(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": [], "cursor": None})
        client = _make_client(adapter, token="bearer-xyz")

        client.list_markets(limit=10)

        call = adapter.calls[0]
        self.assertIn("Authorization", call["headers"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer bearer-xyz")

    def test_auto_refresh_on_401(self):
        """On a 401 response, the client should re-login and retry once."""
        adapter = MockAdapter()
        adapter.add_json(401, {"error": "token expired"})          # first attempt
        adapter.add_json(200, {"token": "fresh-token"})            # login
        adapter.add_json(200, {"markets": [], "cursor": None})     # retry

        rl = TokenBucketRateLimiter(100, 100)
        client = KalshiClient(
            "https://demo-api.kalshi.co/trade-api/v2",
            "u@t.com", "pass",
            rate_limiter=rl, adapter=adapter,
            _sleep_fn=lambda _: None,
        )
        client._token = "old-token"

        result = client.list_markets()
        self.assertEqual(client._token, "fresh-token")
        self.assertEqual(result["markets"], [])


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — KalshiClient endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestListMarkets(unittest.TestCase):

    def test_returns_markets_list(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": [{"ticker": "M-1"}], "cursor": None})
        client = _make_client(adapter)

        result = client.list_markets(limit=50, status="open")
        self.assertIn("markets", result)
        self.assertEqual(result["markets"][0]["ticker"], "M-1")

    def test_passes_limit_param(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": [], "cursor": None})
        client = _make_client(adapter)

        client.list_markets(limit=25)
        params = adapter.calls[0]["params"]
        self.assertEqual(params.get("limit"), "25")

    def test_passes_status_param(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": [], "cursor": None})
        client = _make_client(adapter)

        client.list_markets(status="settled")
        params = adapter.calls[0]["params"]
        self.assertEqual(params.get("status"), "settled")

    def test_passes_event_ticker_param(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": [], "cursor": None})
        client = _make_client(adapter)

        client.list_markets(event_ticker="INXD-24DEC")
        params = adapter.calls[0]["params"]
        self.assertEqual(params.get("event_ticker"), "INXD-24DEC")

    def test_uses_get_method(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": [], "cursor": None})
        client = _make_client(adapter)
        client.list_markets()
        self.assertEqual(adapter.calls[0]["method"], "GET")


class TestGetOrderbook(unittest.TestCase):

    def test_returns_orderbook(self):
        body = {"orderbook": {"yes": [[55, 100]], "no": [[45, 200]]}}
        adapter = MockAdapter()
        adapter.add_json(200, body)
        client = _make_client(adapter)

        result = client.get_orderbook("INXD-24DEC31-B4500", depth=5)
        self.assertIn("orderbook", result)
        self.assertEqual(result["orderbook"]["yes"], [[55, 100]])

    def test_url_contains_ticker(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"orderbook": {"yes": [], "no": []}})
        client = _make_client(adapter)

        client.get_orderbook("MY-MARKET-TICKER")
        self.assertIn("MY-MARKET-TICKER", adapter.calls[0]["url"])

    def test_passes_depth_param(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"orderbook": {"yes": [], "no": []}})
        client = _make_client(adapter)

        client.get_orderbook("M-1", depth=15)
        params = adapter.calls[0]["params"]
        self.assertEqual(params.get("depth"), "15")

    def test_raises_on_404(self):
        adapter = MockAdapter()
        adapter.add_json(404, {"error": "market not found"})
        client = _make_client(adapter)
        with self.assertRaises(KalshiAPIError) as ctx:
            client.get_orderbook("NONEXISTENT")
        self.assertEqual(ctx.exception.status_code, 404)


class TestPlaceOrder(unittest.TestCase):

    ORDER = {
        "ticker": "INXD-24DEC31-B4500",
        "action": "buy",
        "side": "yes",
        "type": "limit",
        "count": 10,
        "yes_price": 55,
    }

    def test_place_order_succeeds_in_demo_mode(self):
        adapter = MockAdapter()
        adapter.add_json(201, {"order": {"order_id": "ord-123", "status": "resting"}})
        client = _make_client(adapter, environment="demo")

        result = client.place_order(self.ORDER)
        self.assertIn("order", result)
        self.assertEqual(result["order"]["order_id"], "ord-123")

    def test_place_order_blocked_in_live_mode(self):
        adapter = MockAdapter()
        client = _make_client(adapter, environment="live")
        with self.assertRaises(KalshiDemoOnlyError):
            client.place_order(self.ORDER)
        # No HTTP call should have been made
        self.assertEqual(len(adapter.calls), 0)

    def test_place_order_uses_post(self):
        adapter = MockAdapter()
        adapter.add_json(201, {"order": {"order_id": "x"}})
        client = _make_client(adapter, environment="demo")
        client.place_order(self.ORDER)
        self.assertEqual(adapter.calls[0]["method"], "POST")

    def test_place_order_sends_body(self):
        adapter = MockAdapter()
        adapter.add_json(201, {"order": {"order_id": "x"}})
        client = _make_client(adapter, environment="demo")
        client.place_order(self.ORDER)
        self.assertEqual(adapter.calls[0]["json_body"]["ticker"], "INXD-24DEC31-B4500")


class TestGetPositions(unittest.TestCase):

    def test_returns_positions(self):
        body = {"positions": [{"ticker": "M-1", "position": 10}]}
        adapter = MockAdapter()
        adapter.add_json(200, body)
        client = _make_client(adapter)

        result = client.get_positions()
        self.assertIn("positions", result)
        self.assertEqual(len(result["positions"]), 1)

    def test_uses_get_method(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"positions": []})
        client = _make_client(adapter)
        client.get_positions()
        self.assertEqual(adapter.calls[0]["method"], "GET")

    def test_url_contains_portfolio(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"positions": []})
        client = _make_client(adapter)
        client.get_positions()
        self.assertIn("portfolio", adapter.calls[0]["url"])


class TestGetBalance(unittest.TestCase):

    def test_returns_balance(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"balance": 50000})   # $500.00 in cents
        client = _make_client(adapter)

        result = client.get_balance()
        self.assertEqual(result["balance"], 50000)

    def test_url_contains_balance(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"balance": 0})
        client = _make_client(adapter)
        client.get_balance()
        self.assertIn("balance", adapter.calls[0]["url"])


class TestPing(unittest.TestCase):

    def test_ping_returns_true_on_200(self):
        adapter = MockAdapter()
        adapter.add_json(200, {"markets": []})
        client = _make_client(adapter)
        self.assertTrue(client.ping())

    def test_ping_returns_false_on_500(self):
        adapter = MockAdapter()
        adapter.add_json(500, {"error": "server error"})
        client = _make_client(adapter)
        self.assertFalse(client.ping())

    def test_ping_returns_false_on_exception(self):
        class ExplodingAdapter:
            def request(self, *a, **kw):
                raise ConnectionError("network down")

        rl = TokenBucketRateLimiter(100, 100)
        client = KalshiClient(
            "https://demo-api.kalshi.co/trade-api/v2",
            "u@t.com", "pass",
            rate_limiter=rl,
            adapter=ExplodingAdapter(),
            _sleep_fn=lambda _: None,
        )
        self.assertFalse(client.ping())


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — from_config
# ═══════════════════════════════════════════════════════════════════════════════

class TestFromConfig(unittest.TestCase):

    def _load_config(self):
        import yaml
        with (ROOT / "config.yaml").open() as fh:
            return yaml.safe_load(fh)

    def test_from_config_wires_base_url(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = KalshiClient.from_config(
            config, "u@t.com", "pass", adapter=adapter, _sleep_fn=lambda _: None
        )
        self.assertEqual(client.base_url, config["kalshi"]["base_url"].rstrip("/"))

    def test_from_config_default_environment_is_demo(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = KalshiClient.from_config(
            config, "u@t.com", "pass", adapter=adapter, _sleep_fn=lambda _: None
        )
        self.assertEqual(client.environment, "demo")

    def test_from_config_place_order_blocked_live(self):
        config = self._load_config()
        config = dict(config, environment="live")
        adapter = MockAdapter()
        client = KalshiClient.from_config(
            config, "u@t.com", "pass", adapter=adapter, _sleep_fn=lambda _: None
        )
        with self.assertRaises(KalshiDemoOnlyError):
            client.place_order({"ticker": "X"})

    def test_from_config_retry_attempts(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = KalshiClient.from_config(
            config, "u@t.com", "pass", adapter=adapter, _sleep_fn=lambda _: None
        )
        self.assertEqual(
            client._max_retry_attempts,
            config["kalshi"]["retry"]["max_attempts"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Integration smoke: retry + rate limit + endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrationSmoke(unittest.TestCase):

    def test_client_retries_then_succeeds(self):
        """Transient 503 → retry → 200 with correct data returned."""
        adapter = MockAdapter()
        adapter.add_json(503, {})
        adapter.add_json(200, {"markets": [{"ticker": "NFL-2024"}], "cursor": None})

        rl = TokenBucketRateLimiter(100, 100)
        client = KalshiClient(
            "https://demo-api.kalshi.co/trade-api/v2",
            "u@t.com", "pass",
            rate_limiter=rl,
            max_retry_attempts=3,
            backoff_base=0.0,
            adapter=adapter,
            _sleep_fn=lambda _: None,
        )
        client._token = "tok"
        result = client.list_markets()
        self.assertEqual(result["markets"][0]["ticker"], "NFL-2024")
        self.assertEqual(len(adapter.calls), 2)  # one retry happened

    def test_place_order_round_trip_demo(self):
        """Full order placement in demo mode with all fields."""
        order_response = {
            "order": {
                "order_id": "ord-abc123",
                "ticker": "INXD-24DEC31-B4500",
                "side": "yes",
                "type": "limit",
                "yes_price": 55,
                "count": 10,
                "status": "resting",
            }
        }
        adapter = MockAdapter()
        adapter.add_json(201, order_response)
        client = _make_client(adapter, environment="demo")

        result = client.place_order({
            "ticker": "INXD-24DEC31-B4500",
            "action": "buy",
            "side": "yes",
            "type": "limit",
            "count": 10,
            "yes_price": 55,
        })
        self.assertEqual(result["order"]["order_id"], "ord-abc123")
        self.assertEqual(result["order"]["status"], "resting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
