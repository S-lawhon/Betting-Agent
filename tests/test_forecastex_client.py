"""
tests/test_forecastex_client.py
───────────────────────────────
Tests for ForecastExClient (IB ib_insync wrapper).

  ✓ Paper mode: place_order returns success without IB
  ✓ Paper mode: fill_price equals limit_price
  ✓ Paper mode: order_id is None
  ✓ Paper mode: no IB connection needed
  ✓ get_price: returns midpoint from mock IB
  ✓ get_price: returns None on NaN
  ✓ get_price: returns None when no IB
  ✓ connect: sets _connected flag
  ✓ disconnect: clears _connected flag
  ✓ Live mode: place_order routes through IB
  ✓ Live mode: place_order failure returns error
  ✓ Live mode: no IB → failure
  ✓ BUY-only constraint verified in order construction
  ✓ from_config: reads host, port, client_id, environment
  ✓ from_config: defaults
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.forecastex_client import ForecastExClient, ForecastExOrderResult


# ── Mock IB ─────────────────────────────────────────────────────────

class _MockTicker:
    def __init__(self, mid=0.55):
        self._mid = mid

    def midpoint(self):
        return self._mid


class _MockOrderStatus:
    def __init__(self, status="Filled", fill_price=0.55):
        self.status = status
        self.avgFillPrice = fill_price


class _MockOrder:
    def __init__(self, order_id=42):
        self.orderId = order_id


class _MockTrade:
    def __init__(self, status="Filled", fill_price=0.55, order_id=42):
        self.orderStatus = _MockOrderStatus(status, fill_price)
        self.order = _MockOrder(order_id)


class _MockIB:
    def __init__(self, midpoint=0.55, trade_status="Filled"):
        self._midpoint = midpoint
        self._trade_status = trade_status
        self.connect_calls = 0
        self.qualify_calls = 0
        self.place_calls = 0

    def isConnected(self):
        return False

    def connect(self, host, port, clientId):
        self.connect_calls += 1

    def disconnect(self):
        pass

    def qualifyContracts(self, contract):
        self.qualify_calls += 1
        return [contract]

    def reqMktData(self, contract):
        return _MockTicker(self._midpoint)

    def placeOrder(self, contract, order):
        self.place_calls += 1
        return _MockTrade(self._trade_status)

    def sleep(self, seconds):
        pass


# ── Tests ───────────────────────────────────────────────────────────

class TestPaperMode(unittest.TestCase):

    def test_place_order_returns_success(self):
        client = ForecastExClient(environment="paper")
        result = client.place_order("FED.RATE", "CALL", 10, 0.55)
        self.assertTrue(result.success)
        self.assertIsInstance(result, ForecastExOrderResult)

    def test_fill_price_equals_limit(self):
        client = ForecastExClient(environment="paper")
        result = client.place_order("FED.RATE", "CALL", 10, 0.55)
        self.assertAlmostEqual(result.fill_price, 0.55)

    def test_order_id_none(self):
        client = ForecastExClient(environment="paper")
        result = client.place_order("FED.RATE", "CALL", 10, 0.55)
        self.assertIsNone(result.order_id)

    def test_no_ib_needed(self):
        # Paper mode with no IB instance should work
        client = ForecastExClient(environment="paper")
        result = client.place_order("CPI.25JUN", "PUT", 5, 0.40)
        self.assertTrue(result.success)


class TestGetPrice(unittest.TestCase):

    def test_returns_midpoint(self):
        ib = _MockIB(midpoint=0.55)
        client = ForecastExClient(ib=ib, environment="live")
        client._connected = True
        price = client.get_price("FED.RATE", "CALL")
        self.assertAlmostEqual(price, 0.55)

    def test_returns_none_on_nan(self):
        ib = _MockIB(midpoint=float("nan"))
        client = ForecastExClient(ib=ib, environment="live")
        client._connected = True
        price = client.get_price("FED.RATE", "CALL")
        self.assertIsNone(price)

    def test_returns_none_when_no_ib(self):
        client = ForecastExClient(environment="paper")
        client._ib = None
        price = client.get_price("FED.RATE", "CALL")
        self.assertIsNone(price)


class TestConnect(unittest.TestCase):

    def test_connect_sets_flag(self):
        ib = _MockIB()
        client = ForecastExClient(ib=ib, environment="live")
        result = client.connect()
        self.assertTrue(result)
        self.assertTrue(client._connected)
        self.assertEqual(ib.connect_calls, 1)

    def test_disconnect_clears_flag(self):
        ib = _MockIB()
        client = ForecastExClient(ib=ib, environment="live")
        client.connect()
        client.disconnect()
        self.assertFalse(client._connected)


class TestLiveMode(unittest.TestCase):

    def test_place_order_filled(self):
        ib = _MockIB(trade_status="Filled")
        client = ForecastExClient(ib=ib, environment="live")
        client._connected = True
        result = client.place_order("FED.RATE", "CALL", 10, 0.55)
        self.assertTrue(result.success)
        self.assertEqual(result.order_id, 42)
        self.assertEqual(ib.place_calls, 1)

    def test_place_order_rejected(self):
        ib = _MockIB(trade_status="Cancelled")
        client = ForecastExClient(ib=ib, environment="live")
        client._connected = True
        result = client.place_order("FED.RATE", "CALL", 10, 0.55)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_no_ib_fails(self):
        client = ForecastExClient(environment="live")
        client._ib = None
        result = client.place_order("FED.RATE", "CALL", 10, 0.55)
        self.assertFalse(result.success)
        self.assertIn("No IB", result.error)

    def test_buy_only_in_order(self):
        """Verify order is always BUY (ForecastEx constraint)."""
        ib = _MockIB()
        client = ForecastExClient(ib=ib, environment="live")
        client._connected = True
        order = client._make_limit_order(10, 0.55)
        self.assertEqual(order.action, "BUY")


class TestFromConfig(unittest.TestCase):

    def test_reads_config(self):
        cfg = {
            "interactive_brokers": {
                "host": "192.168.1.100",
                "port": 4001,
                "client_id": 20,
                "environment": "live",
            }
        }
        client = ForecastExClient.from_config(cfg)
        self.assertEqual(client.host, "192.168.1.100")
        self.assertEqual(client.port, 4001)
        self.assertEqual(client.client_id, 20)
        self.assertEqual(client.environment, "live")

    def test_defaults(self):
        client = ForecastExClient.from_config({})
        self.assertEqual(client.host, "127.0.0.1")
        self.assertEqual(client.port, 7497)
        self.assertEqual(client.client_id, 10)
        self.assertEqual(client.environment, "paper")


if __name__ == "__main__":
    unittest.main()
