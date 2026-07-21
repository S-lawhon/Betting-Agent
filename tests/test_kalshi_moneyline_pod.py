"""
tests/test_kalshi_moneyline_pod.py
──────────────────────────────────
Tests for P-001 KalshiMoneylinePod wrapper.

  ✓ scan_once delegates to Scanner.scan_once()
  ✓ scan_once converts dict entries to ScanResult
  ✓ ScanResult fields mapped correctly from Scanner dict
  ✓ pod_id is "P-001"
  ✓ pod_name is "Kalshi vs Sharp's Strategy"
  ✓ venue_name() returns "kalshi"
  ✓ empty scanner returns empty list
  ✓ multiple entries all converted
  ✓ SKIPPED_EDGE entries converted with correct action
  ✓ from_config requires scanner in overrides
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.base_pod import ScanResult
from src.pods.kalshi_moneyline import KalshiMoneylinePod


# ── Fixtures ────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


def _placed_entry(
    ticker="NFL-KC-LV-ML",
    side="YES",
    kalshi_prob=0.57,
    size_usd=50.0,
    sport="americanfootball_nfl",
) -> Dict[str, Any]:
    return {
        "fingerprint": "abc123",
        "timestamp_utc": _NOW.isoformat(),
        "mode": "paper",
        "sport": sport,
        "event": "Kansas City Chiefs vs Las Vegas Raiders",
        "market_ticker": ticker,
        "side": side,
        "fair_prob": 0.62,
        "kalshi_prob": kalshi_prob,
        "edge_pct": 0.05,
        "ev": 0.03,
        "kelly_fraction": 0.005,
        "position_size_usd": size_usd,
        "action": "PLACED",
        "order_id": None,
        "fill_price": kalshi_prob,
    }


def _skipped_entry(**overrides):
    entry = _placed_entry()
    entry["action"] = "SKIPPED_EDGE"
    entry["skip_reason"] = "Below min_edge_pct"
    entry["position_size_usd"] = 0.0
    entry.update(overrides)
    return entry


class _MockScanner:
    def __init__(self, entries=None):
        self._entries = entries if entries is not None else []
        self.scan_calls = 0
        self._edge = None
        self._risk = None
        self._log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        self._mode = "paper"

    def scan_once(self) -> List[Dict[str, Any]]:
        self.scan_calls += 1
        return list(self._entries)


class _MockEdge:
    pass


class _MockRisk:
    pass


def _pod(scanner=None, log_path=None) -> KalshiMoneylinePod:
    s = scanner or _MockScanner()
    return KalshiMoneylinePod(
        scanner=s,
        edge_calculator=_MockEdge(),
        risk_manager=_MockRisk(),
        trade_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        mode="paper",
        _now_fn=lambda: _NOW,
    )


# ── Tests ───────────────────────────────────────────────────────────

class TestKalshiMoneylinePod(unittest.TestCase):

    def test_pod_id(self):
        pod = _pod()
        self.assertEqual(pod.pod_id, "P-001")

    def test_pod_name(self):
        pod = _pod()
        self.assertEqual(pod.pod_name, "Kalshi vs Sharp's Strategy")

    def test_venue_name(self):
        pod = _pod()
        self.assertEqual(pod.venue_name(), "kalshi")

    def test_delegates_to_scanner(self):
        scanner = _MockScanner(entries=[_placed_entry()])
        pod = _pod(scanner=scanner)
        pod.scan_once()
        self.assertEqual(scanner.scan_calls, 1)

    def test_converts_dict_to_scan_result(self):
        scanner = _MockScanner(entries=[_placed_entry()])
        pod = _pod(scanner=scanner)
        results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], ScanResult)

    def test_field_mapping(self):
        entry = _placed_entry(
            ticker="NFL-T", side="YES", kalshi_prob=0.57,
            size_usd=50.0, sport="americanfootball_nfl",
        )
        scanner = _MockScanner(entries=[entry])
        pod = _pod(scanner=scanner)
        result = pod.scan_once()[0]

        self.assertEqual(result.pod_id, "P-001")
        self.assertEqual(result.pod_name, "Kalshi vs Sharp's Strategy")
        self.assertEqual(result.venue, "kalshi")
        self.assertEqual(result.market_type, "sports_americanfootball_nfl")
        self.assertEqual(result.market_id, "NFL-T")
        self.assertEqual(result.side, "YES")
        self.assertAlmostEqual(result.fair_prob, 0.62)
        self.assertAlmostEqual(result.venue_prob, 0.57)
        self.assertEqual(result.action, "PLACED")
        self.assertEqual(result.mode, "paper")

    def test_empty_scanner_returns_empty(self):
        scanner = _MockScanner(entries=[])
        pod = _pod(scanner=scanner)
        results = pod.scan_once()
        self.assertEqual(results, [])

    def test_multiple_entries(self):
        entries = [
            _placed_entry(ticker="T1"),
            _placed_entry(ticker="T2"),
        ]
        scanner = _MockScanner(entries=entries)
        pod = _pod(scanner=scanner)
        results = pod.scan_once()
        self.assertEqual(len(results), 2)
        ids = {r.market_id for r in results}
        self.assertIn("T1", ids)
        self.assertIn("T2", ids)

    def test_skipped_entry_converted(self):
        scanner = _MockScanner(entries=[_skipped_entry()])
        pod = _pod(scanner=scanner)
        results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "SKIPPED_EDGE")
        self.assertEqual(results[0].skip_reason, "Below min_edge_pct")


class TestKalshiMoneylinePodFromConfig(unittest.TestCase):

    def test_requires_scanner(self):
        with self.assertRaises(ValueError):
            KalshiMoneylinePod.from_config({})

    def test_from_config_with_scanner(self):
        scanner = _MockScanner()
        pod = KalshiMoneylinePod.from_config(
            {"environment": "demo"},
            scanner=scanner,
        )
        self.assertEqual(pod.pod_id, "P-001")
        self.assertEqual(pod.mode, "paper")

    def test_from_config_live_env(self):
        scanner = _MockScanner()
        pod = KalshiMoneylinePod.from_config(
            {"environment": "live"},
            scanner=scanner,
        )
        self.assertEqual(pod.mode, "live")


if __name__ == "__main__":
    unittest.main()
