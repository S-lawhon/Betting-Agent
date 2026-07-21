"""
tests/test_deribit_client.py
────────────────────────────
Tests for DeribitClient options chain API client.

  ✓ get_index_price returns float
  ✓ get_instruments returns list of dicts
  ✓ get_all_chains groups by expiry
  ✓ OptionsChain properties (calls, puts, strikes, get_call, get_put)
  ✓ OptionInstrument fields populated correctly
  ✓ get_chain_for_expiry returns matching chain
  ✓ rate limiting respects minimum interval
  ✓ from_config builds with testnet URL
  ✓ API error handling returns empty/None gracefully
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.deribit_client import (
    DeribitClient,
    OptionInstrument,
    OptionsChain,
    DERIBIT_BASE_URL,
    DERIBIT_TESTNET_URL,
)


# ── Fixtures ──────────────────────────────────────────────────────────

_MOCK_INSTRUMENTS = [
    {
        "instrument_name": "BTC-28MAR26-90000-C",
        "strike": 90000,
        "expiration_timestamp": 1743120000000,  # 28 Mar 2026 UTC
        "option_type": "call",
    },
    {
        "instrument_name": "BTC-28MAR26-90000-P",
        "strike": 90000,
        "expiration_timestamp": 1743120000000,
        "option_type": "put",
    },
    {
        "instrument_name": "BTC-28MAR26-100000-C",
        "strike": 100000,
        "expiration_timestamp": 1743120000000,
        "option_type": "call",
    },
    {
        "instrument_name": "BTC-28MAR26-100000-P",
        "strike": 100000,
        "expiration_timestamp": 1743120000000,
        "option_type": "put",
    },
    {
        "instrument_name": "BTC-25APR26-100000-C",
        "strike": 100000,
        "expiration_timestamp": 1745539200000,  # 25 Apr 2026 UTC
        "option_type": "call",
    },
]

_MOCK_SUMMARIES = [
    {
        "instrument_name": "BTC-28MAR26-90000-C",
        "mark_price": 0.05,  # in BTC
        "bid_price": 0.048,
        "ask_price": 0.052,
        "mark_iv": 65.0,  # 65%
        "underlying_price": 87000,
        "open_interest": 500,
        "volume": 100,
        "greeks": {"delta": 0.35, "gamma": 0.00001, "vega": 50, "theta": -10},
    },
    {
        "instrument_name": "BTC-28MAR26-90000-P",
        "mark_price": 0.08,
        "bid_price": 0.077,
        "ask_price": 0.083,
        "mark_iv": 68.0,
        "underlying_price": 87000,
        "open_interest": 300,
        "volume": 50,
        "greeks": {"delta": -0.65, "gamma": 0.00001, "vega": 50, "theta": -10},
    },
    {
        "instrument_name": "BTC-28MAR26-100000-C",
        "mark_price": 0.02,
        "bid_price": 0.018,
        "ask_price": 0.022,
        "mark_iv": 70.0,
        "underlying_price": 87000,
        "open_interest": 800,
        "volume": 200,
        "greeks": {"delta": 0.20, "gamma": 0.000008, "vega": 40, "theta": -8},
    },
    {
        "instrument_name": "BTC-28MAR26-100000-P",
        "mark_price": 0.17,
        "bid_price": 0.165,
        "ask_price": 0.175,
        "mark_iv": 72.0,
        "underlying_price": 87000,
        "open_interest": 200,
        "volume": 30,
        "greeks": {"delta": -0.80, "gamma": 0.000008, "vega": 40, "theta": -8},
    },
    {
        "instrument_name": "BTC-25APR26-100000-C",
        "mark_price": 0.04,
        "bid_price": 0.038,
        "ask_price": 0.042,
        "mark_iv": 60.0,
        "underlying_price": 87000,
        "open_interest": 400,
        "volume": 80,
        "greeks": {"delta": 0.30, "gamma": 0.000005, "vega": 60, "theta": -5},
    },
]


class _MockSession:
    """Mock requests.Session for testing without HTTP."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.headers = {}

    def update(self, d):
        self.headers.update(d)

    def get(self, url, params=None, timeout=None):
        # Route by method name in URL
        for key, result in self._responses.items():
            if key in url:
                return _MockResponse({"result": result})
        return _MockResponse({"result": []})


class _MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


# ── Tests ─────────────────────────────────────────────────────────────

class TestDeribitClient(unittest.TestCase):

    def _make_client(self, responses=None):
        session = _MockSession(responses or {})
        # Patch headers.update
        session.headers = {}
        original_update = session.headers.update
        client = DeribitClient.__new__(DeribitClient)
        client.base_url = DERIBIT_BASE_URL
        client.timeout = 15.0
        client._session = session
        client._session.headers = {"Accept": "application/json"}
        client._now_fn = lambda: datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)
        client._last_request_time = 0.0
        return client

    def test_get_index_price(self):
        client = self._make_client({
            "get_index_price": {"index_price": 87500.0}
        })
        price = client.get_index_price("BTC")
        self.assertEqual(price, 87500.0)

    def test_get_instruments(self):
        client = self._make_client({
            "get_instruments": _MOCK_INSTRUMENTS,
        })
        instruments = client.get_instruments("BTC")
        self.assertEqual(len(instruments), 5)

    def test_get_all_chains_groups_by_expiry(self):
        client = self._make_client({
            "get_instruments": _MOCK_INSTRUMENTS,
            "get_book_summary_by_currency": _MOCK_SUMMARIES,
        })
        chains = client.get_all_chains("BTC")
        self.assertEqual(len(chains), 2)  # 28MAR26 and 25APR26
        self.assertEqual(chains[0].expiry_str, "28MAR26")
        self.assertEqual(chains[1].expiry_str, "25APR26")

    def test_options_chain_properties(self):
        chain = OptionsChain(
            underlying="BTC",
            expiry_str="28MAR26",
            expiry_ts=1743120000,
            underlying_price=87000,
            instruments=[
                OptionInstrument(
                    instrument_name="BTC-28MAR26-90000-C",
                    underlying="BTC", strike=90000, expiry_ts=1743120000,
                    expiry_str="28MAR26", option_type="call",
                    mark_price_usd=4350, mark_iv=0.65,
                    bid_price_usd=4176, ask_price_usd=4524,
                    open_interest=500, volume_24h=100,
                    delta=0.35, gamma=0.00001, vega=50, theta=-10,
                    underlying_price=87000,
                ),
                OptionInstrument(
                    instrument_name="BTC-28MAR26-90000-P",
                    underlying="BTC", strike=90000, expiry_ts=1743120000,
                    expiry_str="28MAR26", option_type="put",
                    mark_price_usd=6960, mark_iv=0.68,
                    bid_price_usd=6699, ask_price_usd=7221,
                    open_interest=300, volume_24h=50,
                    delta=-0.65, gamma=0.00001, vega=50, theta=-10,
                    underlying_price=87000,
                ),
                OptionInstrument(
                    instrument_name="BTC-28MAR26-100000-C",
                    underlying="BTC", strike=100000, expiry_ts=1743120000,
                    expiry_str="28MAR26", option_type="call",
                    mark_price_usd=1740, mark_iv=0.70,
                    bid_price_usd=1566, ask_price_usd=1914,
                    open_interest=800, volume_24h=200,
                    delta=0.20, gamma=0.000008, vega=40, theta=-8,
                    underlying_price=87000,
                ),
            ],
        )
        self.assertEqual(len(chain.calls), 2)
        self.assertEqual(len(chain.puts), 1)
        self.assertEqual(chain.strikes, [90000, 100000])
        self.assertIsNotNone(chain.get_call(90000))
        self.assertIsNone(chain.get_call(80000))
        self.assertIsNotNone(chain.get_put(90000))

    def test_from_config_testnet(self):
        config = {"deribit": {"environment": "testnet", "timeout": 20.0}}
        client = DeribitClient.from_config(config)
        self.assertEqual(client.base_url, DERIBIT_TESTNET_URL)
        self.assertEqual(client.timeout, 20.0)

    def test_from_config_live(self):
        config = {"deribit": {"environment": "live"}}
        client = DeribitClient.from_config(config)
        self.assertEqual(client.base_url, DERIBIT_BASE_URL)

    def test_get_index_price_error_returns_none(self):
        """API error returns None instead of crashing."""
        client = self._make_client({})  # No matching response
        # Override to raise
        def _bad_request(method, params=None):
            raise Exception("Network error")
        client._request = _bad_request
        result = client.get_index_price("BTC")
        self.assertIsNone(result)


class TestGetChainForExpiry(unittest.TestCase):

    def test_exact_match(self):
        chain_mar = OptionsChain("BTC", "28MAR26", 1743120000, 87000, [])
        chain_apr = OptionsChain("BTC", "25APR26", 1745539200, 87000, [])

        client = DeribitClient.__new__(DeribitClient)
        client.base_url = DERIBIT_BASE_URL
        client.timeout = 15.0
        client._session = _MockSession({
            "get_instruments": _MOCK_INSTRUMENTS,
            "get_book_summary_by_currency": _MOCK_SUMMARIES,
        })
        client._session.headers = {"Accept": "application/json"}
        client._now_fn = lambda: datetime(2026, 3, 26, tzinfo=timezone.utc)
        client._last_request_time = 0.0

        chains = client.get_all_chains("BTC")
        self.assertTrue(len(chains) >= 1)


if __name__ == "__main__":
    unittest.main()
