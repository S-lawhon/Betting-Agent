"""
tests/test_macro_nowcast_pod.py
───────────────────────────────
Tests for P-012 MacroNowcastPod.

  ✓ pod_id, pod_name, venue_name
  ✓ nowcast_to_probability: basic Gaussian calculation
  ✓ nowcast_to_probability: with CI
  ✓ nowcast_to_probability: above vs below direction
  ✓ scan_once: happy path — edge found, PLACED
  ✓ scan_once: no nowcast readings → empty
  ✓ scan_once: no Kalshi price → skips ticker
  ✓ scan_once: SKIPPED_EDGE when edge_calc returns None
  ✓ scan_once: dedup across scans
  ✓ scan_once: multiple indicators scanned
  ✓ extra fields contain nowcast data
  ✓ from_config
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.base_pod import ScanResult
from src.nowcast_client import NowcastReading
from src.pods.macro_nowcast import MacroNowcastPod, nowcast_to_probability

_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

_CPI_READING = NowcastReading(
    source="cleveland_fed_cpi",
    indicator="cpi_yoy",
    value=3.2,
    lower_bound=2.8,
    upper_bound=3.6,
    as_of_date="2024-06-01",
)


# ── Fixtures ────────────────────────────────────────────────────────

class _MockNowcastClient:
    def __init__(self, readings=None):
        self._readings = readings or {}

    def get_all_readings(self):
        return dict(self._readings)

    def get_reading(self, source):
        return self._readings.get(source)


class _MockKalshi:
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


_ECON_MARKETS = [
    {
        "source": "cleveland_fed_cpi",
        "indicator": "cpi_yoy",
        "kalshi_tickers": [
            {"ticker": "CPI-25JUN-T3.0", "threshold": 3.0, "direction": "above"},
            {"ticker": "CPI-25JUN-T3.5", "threshold": 3.5, "direction": "above"},
        ],
    },
]


def _pod(
    nowcast=None, kalshi=None, edge_calc=None,
    econ_markets=None, log_path=None,
):
    return MacroNowcastPod(
        nowcast_client=nowcast or _MockNowcastClient(
            readings={"cleveland_fed_cpi": _CPI_READING}
        ),
        kalshi_client=kalshi or _MockKalshi(markets={
            "CPI-25JUN-T3.0": {"yes_bid": 70, "yes_ask": 74},
            "CPI-25JUN-T3.5": {"yes_bid": 30, "yes_ask": 34},
        }),
        edge_calculator=edge_calc or _MockEdgeCalc(result=_MockEdgeResult()),
        risk_manager=_MockRiskManager(),
        econ_markets=econ_markets or _ECON_MARKETS,
        trade_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        mode="paper",
        _now_fn=lambda: _NOW,
    )


# ── nowcast_to_probability tests ────────────────────────────────────

class TestNowcastToProbability(unittest.TestCase):

    def test_above_threshold_below_value(self):
        # CPI=3.2, threshold=3.0, direction=above → high probability
        reading = _CPI_READING
        prob = nowcast_to_probability(reading, 3.0, "above")
        self.assertIsNotNone(prob)
        self.assertGreater(prob, 0.5)  # Value > threshold → prob > 50%

    def test_above_threshold_above_value(self):
        # CPI=3.2, threshold=3.5, direction=above → lower probability
        reading = _CPI_READING
        prob = nowcast_to_probability(reading, 3.5, "above")
        self.assertIsNotNone(prob)
        self.assertLess(prob, 0.5)  # Value < threshold → prob < 50%

    def test_below_direction(self):
        # CPI=3.2, threshold=3.5, direction=below → high probability
        reading = _CPI_READING
        prob = nowcast_to_probability(reading, 3.5, "below")
        self.assertIsNotNone(prob)
        self.assertGreater(prob, 0.5)

    def test_at_threshold(self):
        # CPI=3.2, threshold=3.2 → 50/50
        reading = _CPI_READING
        prob = nowcast_to_probability(reading, 3.2, "above")
        self.assertIsNotNone(prob)
        self.assertAlmostEqual(prob, 0.5, places=1)

    def test_no_ci_uses_fallback(self):
        # Reading without bounds → uses 10% heuristic sigma
        reading = NowcastReading(source="test", indicator="x", value=5.0)
        prob = nowcast_to_probability(reading, 4.0, "above")
        self.assertIsNotNone(prob)
        self.assertGreater(prob, 0.5)

    def test_tight_ci_extreme_prob(self):
        # Very tight CI → strong probability signal
        reading = NowcastReading(
            source="test", indicator="x", value=3.2,
            lower_bound=3.15, upper_bound=3.25,  # Tight CI
        )
        prob = nowcast_to_probability(reading, 2.0, "above")
        self.assertGreater(prob, 0.99)

    def test_returns_bounded_probability(self):
        prob = nowcast_to_probability(_CPI_READING, 3.0, "above")
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)


# ── Pod tests ───────────────────────────────────────────────────────

class TestPodMetadata(unittest.TestCase):

    def test_pod_id(self):
        self.assertEqual(_pod().pod_id, "P-012")

    def test_pod_name(self):
        self.assertEqual(_pod().pod_name, "Macro Economic Nowcast")

    def test_venue_name(self):
        self.assertEqual(_pod().venue_name(), "kalshi")


class TestScanOnceHappyPath(unittest.TestCase):

    def test_returns_results(self):
        pod = _pod()
        results = pod.scan_once()
        self.assertGreater(len(results), 0)

    def test_placed_result_fields(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed), 1)
        r = placed[0]
        self.assertEqual(r.pod_id, "P-012")
        self.assertEqual(r.venue, "kalshi")
        self.assertIn("economics_", r.market_type)

    def test_extra_fields(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed), 1)
        extra = placed[0].extra
        self.assertIn("nowcast_value", extra)
        self.assertIn("nowcast_source", extra)
        self.assertIn("threshold", extra)
        self.assertIn("direction", extra)

    def test_log_written(self):
        log = Path(tempfile.mktemp(suffix=".jsonl"))
        pod = _pod(log_path=log)
        pod.scan_once()
        self.assertTrue(log.exists())


class TestScanOnceSkips(unittest.TestCase):

    def test_no_nowcast_readings(self):
        nowcast = _MockNowcastClient(readings={})
        pod = _pod(nowcast=nowcast)
        results = pod.scan_once()
        self.assertEqual(results, [])

    def test_no_kalshi_price(self):
        kalshi = _MockKalshi(markets={})
        pod = _pod(kalshi=kalshi)
        results = pod.scan_once()
        # Should get SKIPPED_EDGE at most (since no venue price → None → skip)
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)

    def test_skipped_edge(self):
        pod = _pod(edge_calc=_MockEdgeCalc(result=None))
        results = pod.scan_once()
        skipped = [r for r in results if r.action == "SKIPPED_EDGE"]
        self.assertGreater(len(skipped), 0)


class TestDedup(unittest.TestCase):

    def test_fingerprint_dedup_across_scans(self):
        pod = _pod()
        r1 = pod.scan_once()
        r2 = pod.scan_once()
        placed1 = [r for r in r1 if r.action == "PLACED"]
        placed2 = [r for r in r2 if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed1), 1)
        self.assertEqual(len(placed2), 0)


class TestMultipleIndicators(unittest.TestCase):

    def test_two_indicators(self):
        nowcast = _MockNowcastClient(readings={
            "cleveland_fed_cpi": _CPI_READING,
            "atlanta_fed_gdp": NowcastReading(
                source="atlanta_fed_gdp",
                indicator="gdp_quarterly",
                value=2.1,
                lower_bound=1.5,
                upper_bound=2.7,
            ),
        })
        econ = [
            {
                "source": "cleveland_fed_cpi",
                "indicator": "cpi_yoy",
                "kalshi_tickers": [
                    {"ticker": "CPI-25JUN-T3.0", "threshold": 3.0, "direction": "above"},
                ],
            },
            {
                "source": "atlanta_fed_gdp",
                "indicator": "gdp_quarterly",
                "kalshi_tickers": [
                    {"ticker": "GDP-25Q2-T2.0", "threshold": 2.0, "direction": "above"},
                ],
            },
        ]
        kalshi = _MockKalshi(markets={
            "CPI-25JUN-T3.0": {"yes_bid": 70, "yes_ask": 74},
            "GDP-25Q2-T2.0": {"yes_bid": 50, "yes_ask": 54},
        })
        pod = _pod(nowcast=nowcast, kalshi=kalshi, econ_markets=econ)
        results = pod.scan_once()
        # Should have results from both indicators
        self.assertGreater(len(results), 0)
        markets = {r.market_id for r in results}
        self.assertGreater(len(markets), 0)


class TestFromConfig(unittest.TestCase):

    def test_from_config(self):
        cfg = {
            "pods": {"P-012": {"environment": "demo"}},
            "environment": "demo",
        }
        pod = MacroNowcastPod.from_config(
            cfg,
            nowcast_client=_MockNowcastClient(),
            kalshi_client=_MockKalshi(),
            edge_calculator=_MockEdgeCalc(),
            risk_manager=_MockRiskManager(),
        )
        self.assertEqual(pod.pod_id, "P-012")
        self.assertEqual(pod.mode, "paper")


if __name__ == "__main__":
    unittest.main()
