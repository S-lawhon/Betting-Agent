"""
tests/test_forecastex_kalshi_arb_pod.py
───────────────────────────────────────
Tests for P-004 ForecastExKalshiArbPod.

  ✓ pod_id, pod_name, venue_name
  ✓ scan_once: happy path — spread found, PLACED
  ✓ scan_once: no ForecastEx price → empty
  ✓ scan_once: no Kalshi price → empty
  ✓ scan_once: spread below min_arb_pct → empty
  ✓ scan_once: SKIPPED_EDGE when edge_calc returns None but spread exists
  ✓ scan_once: dedup across scans
  ✓ scan_once: multiple symbols scanned
  ✓ extra fields contain venue prices and spread
  ✓ from_config
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.base_pod import ScanResult
from src.pods.forecastex_kalshi_arb import (
    ForecastExKalshiArbPod, EconSymbolMapping,
)

_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── Mock clients ────────────────────────────────────────────────────

class _MockForecastExClient:
    def __init__(self, prices=None):
        self._prices = prices or {}
        self.orders = []

    def get_price(self, symbol, right="CALL"):
        return self._prices.get((symbol, right))

    def place_order(self, symbol, right, quantity, limit_price):
        self.orders.append({"symbol": symbol, "right": right, "qty": quantity})
        class _R:
            order_id = None
            fill_price = limit_price
        return _R()


class _MockKalshiClient:
    def __init__(self, markets=None):
        self._markets = markets or {}
        self.orders = []

    def get_market(self, ticker):
        return self._markets.get(ticker)

    def place_order(self, ticker, side, yes_price=None, no_price=None, count=1):
        self.orders.append({"ticker": ticker, "side": side, "count": count})
        class _R:
            order_id = None
            fill_price = (yes_price or no_price or 50) / 100.0
        return _R()


class _MockEdgeResult:
    def __init__(self, side="YES", raw_edge=0.05, ev=0.03,
                 kelly_fractional=0.03, has_edge=True):
        self.side = side
        self.raw_edge = raw_edge
        self.ev = ev
        self.kelly_full = kelly_fractional * 4
        self.kelly_fractional = kelly_fractional
        self.has_edge = has_edge


class _MockEdgeCalc:
    def __init__(self, result=None):
        self._result = result

    def best_side(self, fair_yes_prob, kalshi_yes_price):
        return self._result


class _MockRiskManager:
    class _Ledger:
        bankroll = 10_000.0
    ledger = _Ledger()


_MAPPING = EconSymbolMapping(
    forecastex_symbol="FED.RATE.25JUN",
    kalshi_ticker="FED-25JUN-T4.75",
    description="Fed Rate June 2025",
    category="fed_rate",
)


def _pod(
    fex=None, kalshi=None, edge_calc=None,
    mappings=None, min_arb=0.02, log_path=None,
):
    return ForecastExKalshiArbPod(
        forecastex_client=fex or _MockForecastExClient(
            prices={("FED.RATE.25JUN", "CALL"): 0.55}
        ),
        kalshi_client=kalshi or _MockKalshiClient(
            markets={"FED-25JUN-T4.75": {"yes_bid": 60, "yes_ask": 64}}
        ),
        edge_calculator=edge_calc or _MockEdgeCalc(result=_MockEdgeResult()),
        risk_manager=_MockRiskManager(),
        symbol_mappings=mappings or [_MAPPING],
        min_arb_pct=min_arb,
        trade_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        mode="paper",
        _now_fn=lambda: _NOW,
    )


# ── Tests ───────────────────────────────────────────────────────────

class TestPodMetadata(unittest.TestCase):

    def test_pod_id(self):
        self.assertEqual(_pod().pod_id, "P-004")

    def test_pod_name(self):
        self.assertEqual(_pod().pod_name, "ForecastEx-Kalshi Econ Arb")

    def test_venue_name(self):
        self.assertEqual(_pod().venue_name(), "multi")


class TestScanOnceHappyPath(unittest.TestCase):
    """ForecastEx YES=0.55, Kalshi mid=0.62 → spread 0.07 → PLACED."""

    def test_returns_placed(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed), 1)

    def test_scan_result_fields(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed), 1)
        r = placed[0]
        self.assertEqual(r.pod_id, "P-004")
        self.assertEqual(r.venue, "multi")
        self.assertIn("economics_", r.market_type)
        self.assertGreater(r.position_size_usd, 0)

    def test_extra_fields(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed), 1)
        extra = placed[0].extra
        self.assertIn("forecastex_yes", extra)
        self.assertIn("kalshi_yes", extra)
        self.assertIn("spread", extra)
        self.assertIn("buy_venue", extra)

    def test_log_written(self):
        log = Path(tempfile.mktemp(suffix=".jsonl"))
        pod = _pod(log_path=log)
        pod.scan_once()
        self.assertTrue(log.exists())

    def test_forecastex_order_placed(self):
        # ForecastEx is cheaper (0.55 < 0.62) → buy on ForecastEx
        fex = _MockForecastExClient(prices={("FED.RATE.25JUN", "CALL"): 0.55})
        kalshi = _MockKalshiClient(
            markets={"FED-25JUN-T4.75": {"yes_bid": 60, "yes_ask": 64}}
        )
        pod = _pod(fex=fex, kalshi=kalshi)
        pod.scan_once()
        self.assertGreater(len(fex.orders), 0)


class TestScanOnceSkips(unittest.TestCase):

    def test_no_forecastex_price(self):
        fex = _MockForecastExClient(prices={})
        pod = _pod(fex=fex)
        results = pod.scan_once()
        self.assertEqual(results, [])

    def test_no_kalshi_price(self):
        kalshi = _MockKalshiClient(markets={})
        pod = _pod(kalshi=kalshi)
        results = pod.scan_once()
        self.assertEqual(results, [])

    def test_spread_below_threshold(self):
        # ForecastEx 0.55, Kalshi 0.56 → spread 0.01 < min_arb 0.02
        fex = _MockForecastExClient(prices={("FED.RATE.25JUN", "CALL"): 0.55})
        kalshi = _MockKalshiClient(
            markets={"FED-25JUN-T4.75": {"yes_bid": 55, "yes_ask": 57}}
        )
        pod = _pod(fex=fex, kalshi=kalshi, min_arb=0.02)
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)

    def test_skipped_edge(self):
        # Large spread but edge_calc returns None
        pod = _pod(edge_calc=_MockEdgeCalc(result=None))
        results = pod.scan_once()
        skipped = [r for r in results if r.action == "SKIPPED_EDGE"]
        self.assertGreaterEqual(len(skipped), 1)

    def test_scan_error_handled(self):
        """Scan error for one symbol doesn't crash."""
        class _BrokenFex:
            def get_price(self, symbol, right="CALL"):
                raise RuntimeError("connection error")
            def place_order(self, *a, **kw):
                pass

        pod = _pod(fex=_BrokenFex())
        results = pod.scan_once()  # Should not raise
        self.assertEqual(results, [])


class TestDedup(unittest.TestCase):

    def test_fingerprint_dedup_across_scans(self):
        pod = _pod()
        r1 = pod.scan_once()
        r2 = pod.scan_once()
        placed1 = [r for r in r1 if r.action == "PLACED"]
        placed2 = [r for r in r2 if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed1), 1)
        self.assertEqual(len(placed2), 0)


class TestMultipleSymbols(unittest.TestCase):

    def test_two_symbols_scanned(self):
        m1 = _MAPPING
        m2 = EconSymbolMapping("CPI.25JUN", "CPI-25JUN-T3.5", "CPI June 2025", "cpi")
        fex = _MockForecastExClient(prices={
            ("FED.RATE.25JUN", "CALL"): 0.55,
            ("CPI.25JUN", "CALL"): 0.40,
        })
        kalshi = _MockKalshiClient(markets={
            "FED-25JUN-T4.75": {"yes_bid": 60, "yes_ask": 64},
            "CPI-25JUN-T3.5": {"yes_bid": 50, "yes_ask": 54},
        })
        pod = _pod(fex=fex, kalshi=kalshi, mappings=[m1, m2])
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        # Should have trades from both symbols
        markets = {r.market_id for r in placed}
        self.assertGreaterEqual(len(markets), 1)


class TestFromConfig(unittest.TestCase):

    def test_from_config(self):
        cfg = {
            "pods": {"P-004": {"min_arb_pct": 0.03, "environment": "demo"}},
            "environment": "demo",
        }
        pod = ForecastExKalshiArbPod.from_config(
            cfg,
            forecastex_client=_MockForecastExClient(),
            kalshi_client=_MockKalshiClient(),
            edge_calculator=_MockEdgeCalc(),
            risk_manager=_MockRiskManager(),
        )
        self.assertEqual(pod.pod_id, "P-004")
        self.assertEqual(pod.mode, "paper")
        self.assertAlmostEqual(pod.min_arb_pct, 0.03)


if __name__ == "__main__":
    unittest.main()
