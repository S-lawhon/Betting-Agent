"""
tests/test_qualifier_favorite_pod.py
────────────────────────────────────
Tests for P-015 QualifierFavoritePod and KalshiTennisSettler.

  Pod:
  ✓ places YES on a qualifier favorite inside the ask band
  ✓ skips non-qualifier matches
  ✓ skips asks below floor / above cap
  ✓ skips wide spreads
  ✓ skips in-play (start time in the past)
  ✓ dedup: same market not placed twice
  ✓ max_open_positions honored; on_settlement releases a slot
  ✓ WTA size multiplier applied; include_wta=False skips WTA
  ✓ depth haircut caps size to displayed ask size
  ✓ pod_id / venue / registration
  Settler:
  ✓ WIN pnl math (profit only), LOSS is -stake, VOID is 0
  ✓ settles from Kalshi result field; leaves unresolved open
  ✓ stale positions auto-void after timeout
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.kalshi_tennis_settler import KalshiTennisSettler, _calc_outcome_pnl
from src.pods.qualifier_favorite_pod import (
    QualifierFavoritePod,
    is_qualifier,
    kalshi_taker_fee,
)

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _market(
    ticker="KXATPMATCH-26JUL21AAABBB-AAA",
    series="KXATPMATCH",
    title="Will A win the A vs B: Qualification Final match?",
    bid=0.92,
    ask=0.94,
    ask_size=500.0,
    start_offset_hours=4.0,
) -> Dict[str, Any]:
    start = _NOW + timedelta(hours=start_offset_hours)
    return {
        "ticker": ticker,
        "_series": series,
        "title": title,
        "yes_bid_dollars": f"{bid:.4f}",
        "yes_ask_dollars": f"{ask:.4f}",
        "yes_ask_size_fp": f"{ask_size:.2f}",
        "occurrence_datetime": start.isoformat().replace("+00:00", "Z"),
    }


class _FakeClient:
    def __init__(self, markets: Optional[List[Dict]] = None):
        self.markets = markets or []
        self.by_ticker: Dict[str, Dict] = {}

    def get_open_match_markets(self, series=None):
        return list(self.markets)

    def get_market(self, ticker):
        return self.by_ticker.get(ticker)


class _FakeRiskManager:
    max_bet_pct = 0.03
    max_position_usd = 100.0

    class ledger:
        bankroll = 1000.0


def _make_pod(markets, tmpdir, **kwargs) -> QualifierFavoritePod:
    defaults = dict(
        kalshi_tennis_client=_FakeClient(markets),
        risk_manager=_FakeRiskManager(),
        trade_log_path=Path(tmpdir) / "P-015.jsonl",
        mode="paper",
        _now_fn=lambda: _NOW,
    )
    defaults.update(kwargs)
    return QualifierFavoritePod(**defaults)


class TestHelpers(unittest.TestCase):
    def test_is_qualifier_true(self):
        self.assertTrue(is_qualifier(_market()))

    def test_is_qualifier_false(self):
        m = _market(title="Will A win the A vs B: Round of 16 match?")
        self.assertFalse(is_qualifier(m))

    def test_taker_fee_rounds_up(self):
        # 0.07 * 0.94 * 0.06 = 0.003948 → 1 cent
        self.assertEqual(kalshi_taker_fee(0.94), 0.01)
        self.assertEqual(kalshi_taker_fee(0.50), 0.02)  # 0.0175 → 2c


class TestScan(unittest.TestCase):
    def _placed(self, results):
        return [r for r in results if r.action == "PLACED"]

    def test_places_qualifier_favorite(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_market()], td)
            placed = self._placed(pod.scan_once())
            self.assertEqual(len(placed), 1)
            r = placed[0]
            self.assertEqual(r.pod_id, "P-015")
            self.assertEqual(r.side, "YES")
            self.assertEqual(r.venue, "kalshi")
            self.assertAlmostEqual(r.venue_prob, 0.94)
            self.assertAlmostEqual(r.fair_prob, 0.965)  # ask + 0.025 bump
            self.assertEqual(r.fill_price, 0.94)
            self.assertGreater(r.position_size_usd, 0)

    def test_skips_non_qualifier(self):
        with tempfile.TemporaryDirectory() as td:
            m = _market(title="Will A win the A vs B: Semifinal match?")
            pod = _make_pod([m], td)
            self.assertEqual(self._placed(pod.scan_once()), [])

    def test_skips_ask_below_floor(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_market(bid=0.80, ask=0.82)], td)
            self.assertEqual(self._placed(pod.scan_once()), [])

    def test_skips_ask_above_cap(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_market(bid=0.97, ask=0.98)], td)
            self.assertEqual(self._placed(pod.scan_once()), [])

    def test_skips_wide_spread(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_market(bid=0.85, ask=0.94)], td)
            self.assertEqual(self._placed(pod.scan_once()), [])

    def test_skips_in_play(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_market(start_offset_hours=-1.0)], td)
            self.assertEqual(self._placed(pod.scan_once()), [])

    def test_dedup_same_market(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_market()], td)
            first = self._placed(pod.scan_once())
            second = self._placed(pod.scan_once())
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 0)

    def test_max_open_positions_and_release(self):
        with tempfile.TemporaryDirectory() as td:
            markets = [
                _market(ticker=f"KXATPMATCH-26JUL21M{i}-A{i}")
                for i in range(3)
            ]
            pod = _make_pod(markets, td, max_open_positions=2)
            results = pod.scan_once()
            placed = self._placed(results)
            skipped = [r for r in results if r.action == "SKIPPED_RISK"]
            self.assertEqual(len(placed), 2)
            self.assertEqual(len(skipped), 1)
            # settle one → slot released → third market can be placed
            pod.on_settlement(placed[0].market_id)
            self.assertEqual(pod._open_count, 1)

    def test_slam_window_raises_the_cap(self):
        """Inside a configured slam window the concurrency cap is the slam
        cap; the base cap would throttle the US Open qualifying spike the
        120-trade gate is counting on. _NOW is 2026-07-20."""
        with tempfile.TemporaryDirectory() as td:
            markets = [
                _market(ticker=f"KXATPMATCH-26JUL21M{i}-A{i}")
                for i in range(5)
            ]
            pod = _make_pod(markets, td, max_open_positions=2,
                            slam_max_open_positions=4,
                            slam_windows=[["2026-07-19", "2026-07-21"]])
            results = pod.scan_once()
            self.assertEqual(len(self._placed(results)), 4)
            self.assertEqual(
                len([r for r in results if r.action == "SKIPPED_RISK"]), 1)

    def test_outside_slam_window_base_cap_holds(self):
        with tempfile.TemporaryDirectory() as td:
            markets = [
                _market(ticker=f"KXATPMATCH-26JUL21M{i}-A{i}")
                for i in range(5)
            ]
            pod = _make_pod(markets, td, max_open_positions=2,
                            slam_max_open_positions=4,
                            slam_windows=[["2026-08-17", "2026-08-22"]])
            self.assertEqual(len(self._placed(pod.scan_once())), 2)

    def test_malformed_slam_window_degrades_to_base_cap(self):
        """A bad config line must not take the pod down or widen the cap."""
        with tempfile.TemporaryDirectory() as td:
            markets = [
                _market(ticker=f"KXATPMATCH-26JUL21M{i}-A{i}")
                for i in range(3)
            ]
            pod = _make_pod(markets, td, max_open_positions=2,
                            slam_max_open_positions=4,
                            slam_windows=[["not-a-date", "2026-07-21"],
                                          ["2026-07-22", "2026-07-19"],
                                          "garbage"])
            self.assertEqual(pod.slam_windows, [])
            self.assertEqual(len(self._placed(pod.scan_once())), 2)

    def test_slam_cap_unset_means_windows_change_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            markets = [
                _market(ticker=f"KXATPMATCH-26JUL21M{i}-A{i}")
                for i in range(3)
            ]
            pod = _make_pod(markets, td, max_open_positions=2,
                            slam_windows=[["2026-07-19", "2026-07-21"]])
            self.assertEqual(len(self._placed(pod.scan_once())), 2)

    def test_from_config_parses_slam_cap(self):
        cfg = {"pods": {"P-015": {
            "risk": {"max_open_positions": 6,
                     "slam_max_open_positions": 10,
                     "slam_windows": [["2026-08-17", "2026-08-22"]]},
        }}}
        pod = QualifierFavoritePod.from_config(
            cfg, kalshi_tennis_client=_FakeClient(),
            risk_manager=_FakeRiskManager())
        self.assertEqual(pod.max_open_positions, 6)
        self.assertEqual(pod.slam_max_open_positions, 10)
        self.assertEqual(pod.slam_windows, [("2026-08-17", "2026-08-22")])
        in_window = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        outside = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(pod._effective_max_open(in_window), 10)
        self.assertEqual(pod._effective_max_open(outside), 6)

    def test_wta_size_mult(self):
        with tempfile.TemporaryDirectory() as td:
            atp = _market()
            wta = _market(
                ticker="KXWTAMATCH-26JUL21CCCDDD-CCC",
                series="KXWTAMATCH",
            )
            pod = _make_pod([atp, wta], td, wta_size_mult=0.5)
            placed = {r.extra["tour"]: r for r in self._placed(pod.scan_once())}
            self.assertIn("ATP", placed)
            self.assertIn("WTA", placed)
            self.assertAlmostEqual(
                placed["WTA"].position_size_usd,
                placed["ATP"].position_size_usd * 0.5,
                places=1,
            )

    def test_include_wta_false(self):
        with tempfile.TemporaryDirectory() as td:
            wta = _market(
                ticker="KXWTAMATCH-26JUL21CCCDDD-CCC",
                series="KXWTAMATCH",
            )
            pod = _make_pod([wta], td, include_wta=False)
            self.assertEqual(self._placed(pod.scan_once()), [])

    def test_depth_haircut_caps_size(self):
        with tempfile.TemporaryDirectory() as td:
            # 10 contracts at 0.94 → $9.40 shown; haircut 0.5 → $4.70 cap
            pod = _make_pod([_market(ask_size=10.0)], td)
            placed = self._placed(pod.scan_once())
            self.assertEqual(len(placed), 1)
            self.assertLessEqual(placed[0].position_size_usd, 4.71)

    def test_registered(self):
        from src.pod_registry import get_pod_class
        self.assertIs(get_pod_class("P-015"), QualifierFavoritePod)


class TestSettler(unittest.TestCase):
    def test_pnl_math(self):
        self.assertEqual(_calc_outcome_pnl("YES", "yes", 94.0, 0.94),
                         ("WIN", 6.0))
        self.assertEqual(_calc_outcome_pnl("YES", "no", 94.0, 0.94),
                         ("LOSS", -94.0))
        self.assertEqual(_calc_outcome_pnl("YES", "void", 94.0, 0.94),
                         ("VOID", 0.0))

    def _write_placed(self, path: Path, ticker: str, start: datetime):
        rec = {
            "fingerprint": "fp-" + ticker,
            "pod_id": "P-015",
            "action": "PLACED",
            "market_id": ticker,
            "side": "YES",
            "position_size_usd": 94.0,
            "fill_price": 0.94,
            "venue_prob": 0.94,
            "extra": {"scheduled_start_utc": start.isoformat()},
        }
        with path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def test_settles_resolved_market(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "P-015.jsonl"
            self._write_placed(log, "T1", _NOW - timedelta(hours=5))
            client = _FakeClient()
            client.by_ticker["T1"] = {"status": "settled", "result": "yes"}
            settler = KalshiTennisSettler(
                client=client, log_path=log, _now_fn=lambda: _NOW,
            )
            settled = settler.settle_cycle()
            self.assertEqual(len(settled), 1)
            self.assertEqual(settled[0]["outcome"], "WIN")
            self.assertAlmostEqual(settled[0]["pnl_usd"], 6.0)
            # second cycle: nothing left open
            self.assertEqual(settler.settle_cycle(), [])

    def test_leaves_unresolved_open(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "P-015.jsonl"
            self._write_placed(log, "T2", _NOW - timedelta(hours=5))
            client = _FakeClient()
            client.by_ticker["T2"] = {"status": "active", "result": ""}
            settler = KalshiTennisSettler(
                client=client, log_path=log, _now_fn=lambda: _NOW,
            )
            self.assertEqual(settler.settle_cycle(), [])

    def test_stale_auto_void(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "P-015.jsonl"
            self._write_placed(log, "T3", _NOW - timedelta(days=15))
            client = _FakeClient()  # market lookup returns None
            settler = KalshiTennisSettler(
                client=client, log_path=log, _now_fn=lambda: _NOW,
            )
            settled = settler.settle_cycle()
            self.assertEqual(len(settled), 1)
            self.assertEqual(settled[0]["outcome"], "VOID")
            self.assertEqual(settled[0]["pnl_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
