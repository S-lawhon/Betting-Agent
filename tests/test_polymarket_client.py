"""
tests/test_polymarket_client.py
───────────────────────────────
Tests for PolymarketClient wrapper.

  ✓ Paper mode: place_order returns success without executing
  ✓ Paper mode: cancel_order returns True
  ✓ Paper mode: fill_price equals requested price
  ✓ Paper mode: order_id is None
  ✓ get_price: returns float from adapter
  ✓ get_price: returns None on adapter error
  ✓ get_midpoint: returns float from adapter
  ✓ get_midpoint: returns None on error
  ✓ get_markets: returns dict from adapter
  ✓ get_markets: returns empty on error
  ✓ get_book: builds PolymarketBook from adapter
  ✓ get_book: empty book on error
  ✓ Rate limiter: wait() called before each API call
  ✓ from_config: paper environment default
  ✓ from_config: live environment
  ✓ from_config: reads host
  ✓ No client: get_price returns None
  ✓ No client: place_order in live mode returns failure
"""

import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.polymarket_client import (
    PolymarketBook,
    PolymarketClient,
    PolymarketEnvironment,
    PolymarketOrderResult,
)


# ── Mock adapter ────────────────────────────────────────────────────

class _MockClobAdapter:
    """Injectable mock replacing py-clob-client ClobClient."""

    def __init__(self):
        self.get_markets_calls = 0
        self.get_price_calls = 0
        self.get_midpoint_calls = 0
        self.get_order_book_calls = 0

    def get_markets(self, next_cursor=None):
        self.get_markets_calls += 1
        return {"data": [{"id": "m1"}], "next_cursor": "cursor-2"}

    def get_price(self, token_id, side):
        self.get_price_calls += 1
        return "0.55"

    def get_midpoint(self, token_id):
        self.get_midpoint_calls += 1
        return "0.57"

    def get_order_book(self, token_id):
        self.get_order_book_calls += 1
        # Return mock with bids/asks that have .price and .size
        bid = MagicMock()
        bid.price = "0.55"
        bid.size = "1000"
        ask = MagicMock()
        ask.price = "0.58"
        ask.size = "500"
        book = MagicMock()
        book.bids = [bid]
        book.asks = [ask]
        return book


class _ErrorAdapter:
    """Adapter that raises on every call."""

    def get_markets(self, next_cursor=None):
        raise RuntimeError("network error")

    def get_price(self, token_id, side):
        raise RuntimeError("network error")

    def get_midpoint(self, token_id):
        raise RuntimeError("network error")

    def get_order_book(self, token_id):
        raise RuntimeError("network error")


class _MockRateLimiter:
    def __init__(self):
        self.wait_calls = 0

    def wait(self):
        self.wait_calls += 1


def _client(adapter=None, env=PolymarketEnvironment.PAPER, rl=None) -> PolymarketClient:
    return PolymarketClient(
        host="https://test.polymarket.com",
        environment=env,
        rate_limiter=rl,
        adapter=adapter or _MockClobAdapter(),
    )


# ── Paper mode ──────────────────────────────────────────────────────

class TestPolymarketPaperMode(unittest.TestCase):

    def test_place_order_returns_success(self):
        client = _client()
        result = client.place_order("token-1", "BUY", 0.55, 100)
        self.assertTrue(result.success)
        self.assertIsInstance(result, PolymarketOrderResult)

    def test_place_order_fill_price(self):
        client = _client()
        result = client.place_order("token-1", "BUY", 0.55, 100)
        self.assertAlmostEqual(result.fill_price, 0.55)

    def test_place_order_no_order_id(self):
        client = _client()
        result = client.place_order("token-1", "BUY", 0.55, 100)
        self.assertIsNone(result.order_id)

    def test_place_order_no_error(self):
        client = _client()
        result = client.place_order("token-1", "BUY", 0.55, 100)
        self.assertIsNone(result.error)

    def test_cancel_order_returns_true(self):
        client = _client()
        self.assertTrue(client.cancel_order("order-1"))


# ── Market data ─────────────────────────────────────────────────────

class TestPolymarketMarketData(unittest.TestCase):

    def test_get_price(self):
        client = _client()
        price = client.get_price("token-1")
        self.assertAlmostEqual(price, 0.55)

    def test_get_price_returns_none_on_error(self):
        client = _client(adapter=_ErrorAdapter())
        self.assertIsNone(client.get_price("token-1"))

    def test_get_midpoint(self):
        client = _client()
        mid = client.get_midpoint("token-1")
        self.assertAlmostEqual(mid, 0.57)

    def test_get_midpoint_returns_none_on_error(self):
        client = _client(adapter=_ErrorAdapter())
        self.assertIsNone(client.get_midpoint("token-1"))

    def test_get_markets(self):
        client = _client()
        result = client.get_markets(active_only=False)
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["next_cursor"], "cursor-2")

    def test_get_markets_error_returns_empty(self):
        client = _client(adapter=_ErrorAdapter())
        result = client.get_markets(active_only=False)
        self.assertEqual(result["data"], [])

    def test_get_book(self):
        client = _client()
        book = client.get_book("token-1")
        self.assertIsInstance(book, PolymarketBook)
        self.assertEqual(book.token_id, "token-1")
        self.assertEqual(len(book.bids), 1)
        self.assertEqual(len(book.asks), 1)
        self.assertAlmostEqual(book.best_bid, 0.55)
        self.assertAlmostEqual(book.best_ask, 0.58)
        self.assertAlmostEqual(book.midpoint, 0.565)

    def test_get_book_error_returns_empty(self):
        client = _client(adapter=_ErrorAdapter())
        book = client.get_book("token-1")
        self.assertEqual(book.bids, [])
        self.assertEqual(book.asks, [])
        self.assertIsNone(book.best_bid)


# ── Rate limiter ────────────────────────────────────────────────────

class TestPolymarketRateLimiter(unittest.TestCase):

    def test_wait_called_on_get_price(self):
        rl = _MockRateLimiter()
        client = _client(rl=rl)
        client.get_price("token-1")
        self.assertEqual(rl.wait_calls, 1)

    def test_wait_called_on_get_markets(self):
        rl = _MockRateLimiter()
        client = _client(rl=rl)
        client.get_markets()
        self.assertEqual(rl.wait_calls, 1)

    def test_wait_called_on_get_book(self):
        rl = _MockRateLimiter()
        client = _client(rl=rl)
        client.get_book("token-1")
        self.assertEqual(rl.wait_calls, 1)

    def test_wait_called_on_get_midpoint(self):
        rl = _MockRateLimiter()
        client = _client(rl=rl)
        client.get_midpoint("token-1")
        self.assertEqual(rl.wait_calls, 1)


# ── No-client mode ──────────────────────────────────────────────────

class TestPolymarketNoClient(unittest.TestCase):

    def test_no_client_get_price_returns_none(self):
        client = PolymarketClient(
            host="https://test.com",
            environment=PolymarketEnvironment.PAPER,
            adapter=None,
        )
        client._client = None  # Simulate no py_clob_client
        self.assertIsNone(client.get_price("token-1"))

    def test_no_client_live_place_order_fails(self):
        client = PolymarketClient(
            host="https://test.com",
            environment=PolymarketEnvironment.LIVE,
            adapter=None,
        )
        client._client = None
        result = client.place_order("token-1", "BUY", 0.55, 100)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)


# ── from_config ─────────────────────────────────────────────────────

class TestPolymarketFromConfig(unittest.TestCase):

    def test_paper_default(self):
        client = PolymarketClient.from_config({})
        self.assertEqual(client.environment, PolymarketEnvironment.PAPER)

    def test_paper_explicit(self):
        cfg = {"polymarket": {"environment": "paper"}}
        client = PolymarketClient.from_config(cfg)
        self.assertEqual(client.environment, PolymarketEnvironment.PAPER)

    def test_live_environment(self):
        cfg = {"polymarket": {"environment": "live"}}
        client = PolymarketClient.from_config(cfg)
        self.assertEqual(client.environment, PolymarketEnvironment.LIVE)

    def test_reads_host(self):
        cfg = {"polymarket": {"host": "https://custom.polymarket.com"}}
        client = PolymarketClient.from_config(cfg)
        self.assertEqual(client.host, "https://custom.polymarket.com")

    def test_default_host(self):
        client = PolymarketClient.from_config({})
        self.assertEqual(client.host, "https://clob.polymarket.com")


# ── API key credential tests ─────────────────────────────────────────

class TestPolymarketApiKey(unittest.TestCase):
    """Tests for the new api_key parameter and POLYMARKET_API_KEY env var."""

    def test_api_key_stored_on_init(self):
        client = PolymarketClient(api_key="my-api-key", adapter=MagicMock())
        self.assertEqual(client._api_key, "my-api-key")

    def test_api_key_defaults_to_none(self):
        client = PolymarketClient(adapter=MagicMock())
        self.assertIsNone(client._api_key)

    def test_from_config_reads_api_key_from_env(self):
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY")}
        clean_env["POLYMARKET_API_KEY"] = "env-api-key-abc"
        with patch.dict(os.environ, clean_env, clear=True):
            client = PolymarketClient.from_config({})
        self.assertEqual(client._api_key, "env-api-key-abc")

    def test_from_config_reads_api_key_from_config_dict(self):
        cfg = {"polymarket": {"api_key": "cfg-api-key"}}
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY")}
        with patch.dict(os.environ, clean_env, clear=True):
            client = PolymarketClient.from_config(cfg)
        self.assertEqual(client._api_key, "cfg-api-key")

    def test_from_config_no_api_key_gives_none(self):
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY")}
        with patch.dict(os.environ, clean_env, clear=True):
            client = PolymarketClient.from_config({})
        self.assertIsNone(client._api_key)

    def test_from_config_config_api_key_used_when_env_absent(self):
        """Config dict api_key is used when POLYMARKET_API_KEY env var is absent."""
        cfg = {"polymarket": {"api_key": "config-only-key"}}
        clean_env = {k: v for k, v in os.environ.items() if k != "POLYMARKET_API_KEY"}
        with patch.dict(os.environ, clean_env, clear=True):
            client = PolymarketClient.from_config(cfg)
        self.assertEqual(client._api_key, "config-only-key")

    def test_api_key_with_private_key_substitutes_in_derived_creds(self):
        """With both private_key and api_key, ApiCreds is called with our api_key."""
        mock_clob_module = MagicMock()
        fake_clob_instance = MagicMock()
        mock_clob_module.ClobClient.return_value = fake_clob_instance

        fake_derived = MagicMock()
        fake_derived.api_secret = "sec"
        fake_derived.api_passphrase = "pass"
        fake_clob_instance.create_or_derive_api_creds.return_value = fake_derived

        fake_creds_cls = MagicMock()
        fake_clob_types = MagicMock()
        fake_clob_types.ApiCreds = fake_creds_cls

        with patch.dict("sys.modules", {
            "py_clob_client": MagicMock(),
            "py_clob_client.client": mock_clob_module,
            "py_clob_client.clob_types": fake_clob_types,
        }):
            client = PolymarketClient(private_key="priv-key", api_key="my-api-key")

        fake_creds_cls.assert_called_once_with(
            api_key="my-api-key",
            api_secret="sec",
            api_passphrase="pass",
        )

    def test_private_key_only_uses_fully_derived_creds(self):
        """With only private_key, set_api_creds receives the fully derived object."""
        mock_clob_module = MagicMock()
        fake_clob_instance = MagicMock()
        mock_clob_module.ClobClient.return_value = fake_clob_instance

        fake_derived = MagicMock()
        fake_clob_instance.create_or_derive_api_creds.return_value = fake_derived

        with patch.dict("sys.modules", {
            "py_clob_client": MagicMock(),
            "py_clob_client.client": mock_clob_module,
            "py_clob_client.clob_types": MagicMock(),
        }):
            client = PolymarketClient(private_key="priv-only")

        fake_clob_instance.set_api_creds.assert_called_once_with(fake_derived)


if __name__ == "__main__":
    unittest.main()
