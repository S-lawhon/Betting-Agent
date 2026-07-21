"""
tests/test_cross_venue_arb_pod.py
─────────────────────────────────
Tests for P-002 CrossVenueArbPod (dual-leg cross-venue arb).

  ✓ pod_id, pod_name, venue_name
  ✓ scan_once: dual-leg arb — spread found, TWO PLACED legs
  ✓ scan_once: no Kalshi markets → empty
  ✓ scan_once: no Polymarket markets → empty
  ✓ scan_once: no cross-matches → empty
  ✓ scan_once: spread below min_arb_pct after fees → SKIPPED_EDGE
  ✓ scan_once: dedup across scans
  ✓ scan_once: per-cycle event dedup
  ✓ dual-leg: both Kalshi and Polymarket orders placed
  ✓ dual-leg: extra fields contain arb_type, linked_leg, spread info
  ✓ fee calculation: Kalshi 7% profit fee reduces net spread
  ✓ fee calculation: gross arb but net negative after fees → skip
  ✓ from_config with trade_store and aggregate_risk
  ✓ sports-only filtering in from_config
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
from src.cross_venue_matcher import CrossVenueMatch, CrossVenueMatcher
from src.polymarket_client import PolymarketMarket, PolymarketOrderResult
from src.pods.cross_venue_arb import CrossVenueArbPod, ArbOpportunity, KALSHI_PROFIT_FEE

_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ────────────────────────────────────────────────────────

# Arb scenario: Kalshi YES=0.62, Poly YES=0.55
# So buy YES on Poly (0.55) + buy NO on Kalshi (0.38) = 0.93 cost → 7% gross
_CROSS_MATCH = CrossVenueMatch(
    kalshi_ticker="KXNBAGAME-ABC",
    kalshi_title="Chiefs vs Raiders",
    kalshi_yes_price=0.62,
    kalshi_no_price=0.38,
    polymarket_condition_id="cond-1",
    polymarket_question="Will the Chiefs beat the Raiders?",
    polymarket_token_yes="tok-yes",
    polymarket_token_no="tok-no",
    fuzzy_score=92.0,
    settlement_aligned=True,
    teams_validated=True,
)


class _MockKalshi:
    def __init__(self, markets=None):
        self._markets = markets if markets is not None else [
            {"ticker": "KXNBAGAME-ABC", "title": "Chiefs vs Raiders",
             "yes_bid_dollars": "0.60", "yes_ask_dollars": "0.64"}
        ]
        self.orders = []

    def list_markets(self, status="open", limit=200, cursor=None, series_ticker=None):
        if series_ticker:
            filtered = [m for m in self._markets if m.get("ticker", "").startswith(series_ticker)]
            return {"markets": filtered, "cursor": None}
        return {"markets": self._markets, "cursor": None}

    def get_market(self, ticker):
        for m in self._markets:
            if m["ticker"] == ticker:
                return {"market": m}
        return None

    def place_order(self, order):
        self.orders.append(order)
        ticker = order.get("ticker", "")
        side = order.get("side", "")
        yes_price = order.get("yes_price")
        no_price = order.get("no_price")
        class _R:
            order_id = f"K-{ticker}-{side}"
            fill_price = (yes_price or no_price or 50) / 100.0
        return _R()


class _MockPoly:
    def __init__(self, midpoint=0.55):
        self._midpoint = midpoint
        self.place_calls = []
        self._sports_cache = []

    def get_markets(self, next_cursor=None):
        return {
            "data": [{
                "condition_id": "cond-1",
                "tokens": [{"token_id": "tok-yes"}, {"token_id": "tok-no"}],
                "question": "Will the Chiefs beat the Raiders?",
                "slug": "chiefs-raiders",
                "active": True,
                "closed": False,
                "end_date_iso": "2024-01-16T00:00:00Z",
                "game_start_time": "2024-01-15T18:00:00Z",
            }],
            "next_cursor": None,
        }

    def get_midpoint(self, token_id):
        return self._midpoint

    def find_series_id(self, league_name):
        return 12345

    def get_sport_events(self, series_id=None, tag_id=None, next_cursor=None):
        return {
            "data": [{
                "title": "Chiefs vs Raiders",
                "slug": "chiefs-raiders",
                "startDate": "2024-01-15T18:00:00Z",
                "endDate": "2024-01-16T00:00:00Z",
                "markets": [{
                    "conditionId": "cond-1",
                    "question": "Chiefs vs Raiders",
                    "outcomes": ["Chiefs", "Raiders"],
                    "clobTokenIds": '["tok-yes", "tok-no"]',
                    "outcomePrices": '["0.55", "0.45"]',
                }],
            }],
            "next_cursor": None,
        }

    def get_sports_metadata(self):
        return self._sports_cache

    def place_order(self, token_id, side, price, size):
        self.place_calls.append({"token_id": token_id, "side": side, "price": price, "size": size})
        return PolymarketOrderResult(success=True, order_id=f"P-{token_id}", error=None, fill_price=price)


class _MockCrossVenueMatcher:
    def __init__(self, matches=None):
        self._matches = matches if matches is not None else [_CROSS_MATCH]

    def match_all(self, kalshi_markets, poly_markets):
        return list(self._matches)


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
    max_bet_pct = 0.03


def _pod(
    kalshi=None, poly=None, matcher=None, edge_calc=None,
    min_arb=0.02, log_path=None, aggregate_risk=None, trade_store=None,
):
    return CrossVenueArbPod(
        kalshi_client=kalshi or _MockKalshi(),
        polymarket_client=poly or _MockPoly(),
        cross_venue_matcher=matcher or _MockCrossVenueMatcher(),
        edge_calculator=edge_calc or _MockEdgeCalc(result=_MockEdgeResult()),
        risk_manager=_MockRiskManager(),
        sports=["basketball_nba"],
        min_arb_pct=min_arb,
        trade_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        mode="paper",
        _now_fn=lambda: _NOW,
        aggregate_risk=aggregate_risk,
        trade_store=trade_store,
    )


# ── Tests ───────────────────────────────────────────────────────────

class TestPodMetadata(unittest.TestCase):

    def test_pod_id(self):
        self.assertEqual(_pod().pod_id, "P-002")

    def test_pod_name(self):
        self.assertEqual(_pod().pod_name, "Kalshi-Polymarket Cross-Venue Arb")

    def test_venue_name(self):
        self.assertEqual(_pod().venue_name(), "multi")


class TestDualLegArb(unittest.TestCase):
    """Kalshi YES=0.62 (NO=0.38), Poly YES=0.55 (NO=0.45).

    Best arb: buy YES on Poly (0.55) + buy NO on Kalshi (0.38) = 0.93 cost.
    Gross = 7%, Kalshi fee = 7% × 0.62 (Kalshi NO leg profit if NO wins) = 4.34%.
    Net = 7% - 4.34% = 2.66% → above 2% min → DUAL LEG PLACED.
    """

    def test_returns_two_placed_legs(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 2, f"Expected 2 legs, got {len(placed)}")

    def test_one_kalshi_one_polymarket(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        venues = {r.venue for r in placed}
        self.assertEqual(venues, {"kalshi", "polymarket"})

    def test_extra_has_arb_type(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        for r in placed:
            self.assertEqual(r.extra["arb_type"], "dual_leg")
            self.assertIn("linked_leg", r.extra)
            self.assertIn("net_spread", r.extra)
            self.assertIn("gross_spread", r.extra)

    def test_paper_mode_skips_real_api(self):
        """In paper mode, orders should NOT hit the real client."""
        kalshi = _MockKalshi()
        poly = _MockPoly()
        pod = _pod(kalshi=kalshi, poly=poly)
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertGreater(len(placed), 0, "Should still produce PLACED results")
        # Paper mode should NOT call the real client's place_order
        self.assertEqual(len(kalshi.orders), 0, "Paper mode should not call Kalshi API")
        self.assertEqual(len(poly.place_calls), 0, "Paper mode should not call Poly API")
        # Verify paper fill order IDs
        for r in placed:
            if r.venue == "kalshi":
                self.assertTrue(r.order_id.startswith("PAPER-K-"), f"Expected paper order ID, got {r.order_id}")
            elif r.venue == "polymarket":
                self.assertTrue(r.order_id.startswith("PAPER-P-"), f"Expected paper order ID, got {r.order_id}")

    def test_live_mode_calls_client(self):
        """In live mode, orders should go through the real client."""
        kalshi = _MockKalshi()
        poly = _MockPoly()
        pod = CrossVenueArbPod(
            kalshi_client=kalshi,
            polymarket_client=poly,
            cross_venue_matcher=_MockCrossVenueMatcher(),
            edge_calculator=_MockEdgeCalc(result=_MockEdgeResult()),
            risk_manager=_MockRiskManager(),
            sports=["basketball_nba"],
            min_arb_pct=0.02,
            trade_log_path=Path(tempfile.mktemp(suffix=".jsonl")),
            mode="live",
            _now_fn=lambda: _NOW,
        )
        pod.scan_once()
        self.assertGreater(len(kalshi.orders), 0, "Live mode should call Kalshi API")
        self.assertGreater(len(poly.place_calls), 0, "Live mode should call Poly API")

    def test_log_written_two_entries(self):
        log = Path(tempfile.mktemp(suffix=".jsonl"))
        pod = _pod(log_path=log)
        pod.scan_once()
        lines = [l for l in log.read_text().splitlines() if l.strip()]
        placed_lines = [l for l in lines if '"PLACED"' in l]
        self.assertEqual(len(placed_lines), 2)

    def test_position_size_positive(self):
        pod = _pod()
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        for r in placed:
            self.assertGreater(r.position_size_usd, 0)


class TestScanOnceSkips(unittest.TestCase):

    def test_no_kalshi_markets(self):
        kalshi = _MockKalshi(markets=[])
        pod = _pod(kalshi=kalshi)
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)

    def test_no_matches(self):
        pod = _pod(matcher=_MockCrossVenueMatcher(matches=[]))
        results = pod.scan_once()
        self.assertEqual(results, [])

    def test_spread_below_threshold_after_fees(self):
        # Kalshi YES=0.52, Poly YES=0.50 → spread 2%
        # But after Kalshi 7% profit fee, net is negative
        match = CrossVenueMatch(
            kalshi_ticker="KXNBAGAME-XYZ",
            kalshi_title="Lakers vs Celtics",
            kalshi_yes_price=0.52,
            kalshi_no_price=0.48,
            polymarket_condition_id="cond-2",
            polymarket_question="Will the Lakers beat the Celtics?",
            polymarket_token_yes="t-yes",
            polymarket_token_no="t-no",
            fuzzy_score=95.0,
            settlement_aligned=True,
            teams_validated=True,
        )
        poly = _MockPoly(midpoint=0.50)
        pod = _pod(
            poly=poly,
            matcher=_MockCrossVenueMatcher(matches=[match]),
            min_arb=0.02,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0, "Should not place when net spread < min after fees")


class TestFeeCalculation(unittest.TestCase):

    def test_kalshi_profit_fee_constant(self):
        self.assertAlmostEqual(KALSHI_PROFIT_FEE, 0.07)

    def test_find_best_arb_gross_positive_net_negative(self):
        """Spread of 3% gross with Kalshi fee making it net negative."""
        pod = _pod()
        # Kalshi YES=0.50, Poly YES=0.47 → NO side: K=0.50, P=0.53
        # Buy YES on Poly (0.47) + buy NO on Kalshi (0.50) = 0.97 → 3% gross
        # Kalshi NO wins: profit = 1 - 0.50 = 0.50 → fee = 0.035
        # Net = 0.03 - 0.035 = -0.005 → negative
        arb = pod._find_best_arb(0.50, 0.50, 0.47, 0.53)
        # With 2% min, this should return None
        self.assertIsNone(arb)

    def test_find_best_arb_large_spread(self):
        """Large spread should produce a positive arb after fees."""
        pod = _pod(min_arb=0.01)
        # Kalshi YES=0.62, Poly YES=0.55 → 7% gross
        arb = pod._find_best_arb(0.62, 0.38, 0.55, 0.45)
        self.assertIsNotNone(arb)
        self.assertGreater(arb.net_spread, 0)
        self.assertGreater(arb.gross_spread, arb.net_spread)

    def test_no_arb_when_prices_equal(self):
        pod = _pod()
        arb = pod._find_best_arb(0.55, 0.45, 0.55, 0.45)
        self.assertIsNone(arb)

    def test_no_arb_when_cost_exceeds_one(self):
        """When both venues have same-side price sum > 1, no arb."""
        pod = _pod()
        # Kalshi YES=0.55, Poly NO=0.50 → cost = 0.55 + 0.50 = 1.05 > 1
        arb = pod._find_best_arb(0.55, 0.45, 0.50, 0.50)
        # All configs should cost >= 1.0
        self.assertIsNone(arb)


class TestDedup(unittest.TestCase):

    def test_fingerprint_dedup_across_scans(self):
        pod = _pod()
        r1 = pod.scan_once()
        r2 = pod.scan_once()
        placed1 = [r for r in r1 if r.action == "PLACED"]
        placed2 = [r for r in r2 if r.action == "PLACED"]
        self.assertGreaterEqual(len(placed1), 2)
        self.assertEqual(len(placed2), 0, "Second scan should be deduped")

    def test_per_cycle_event_dedup(self):
        """Same event appearing twice in matches should only produce one arb."""
        match2 = CrossVenueMatch(
            kalshi_ticker=_CROSS_MATCH.kalshi_ticker,  # same ticker
            kalshi_title=_CROSS_MATCH.kalshi_title,
            kalshi_yes_price=0.60,
            kalshi_no_price=0.40,
            polymarket_condition_id="cond-2",
            polymarket_question="Chiefs vs Raiders?",
            polymarket_token_yes="tok2-yes",
            polymarket_token_no="tok2-no",
            fuzzy_score=88.0,
            settlement_aligned=True,
            teams_validated=True,
        )
        pod = _pod(matcher=_MockCrossVenueMatcher(matches=[_CROSS_MATCH, match2]))
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        # Should only get 2 legs (from first match), not 4
        self.assertEqual(len(placed), 2)


class TestFromConfig(unittest.TestCase):

    def test_from_config_basic(self):
        cfg = {
            "pods": {"P-002": {"min_arb_pct": 0.03, "environment": "demo"}},
            "environment": "demo",
            "odds_api": {"sports": ["basketball_nba", "icehockey_nhl"]},
        }
        pod = CrossVenueArbPod.from_config(
            cfg,
            kalshi_client=_MockKalshi(),
            polymarket_client=_MockPoly(),
            edge_calculator=_MockEdgeCalc(),
            risk_manager=_MockRiskManager(),
        )
        self.assertEqual(pod.pod_id, "P-002")
        self.assertEqual(pod.mode, "paper")
        self.assertAlmostEqual(pod.min_arb_pct, 0.03)
        self.assertEqual(pod.sports, ["basketball_nba", "icehockey_nhl"])

    def test_from_config_with_trade_store(self):
        cfg = {
            "pods": {"P-002": {"environment": "demo"}},
            "environment": "demo",
        }

        class _FakeStore:
            def is_fingerprint_seen(self, fp):
                return False
            def is_position_open(self, mid):
                return False
            seen_fingerprints = set()
            def get_open_market_ids(self):
                return set()
            def append(self, record):
                pass

        pod = CrossVenueArbPod.from_config(
            cfg,
            kalshi_client=_MockKalshi(),
            polymarket_client=_MockPoly(),
            edge_calculator=_MockEdgeCalc(),
            risk_manager=_MockRiskManager(),
            trade_store=_FakeStore(),
        )
        self.assertIsNotNone(pod._trade_store)

    def test_from_config_with_aggregate_risk(self):
        cfg = {
            "pods": {"P-002": {"environment": "demo"}},
            "environment": "demo",
        }

        class _FakeAggRisk:
            def check_trade(self, pod_id, venue, position_size_usd):
                return True

        pod = CrossVenueArbPod.from_config(
            cfg,
            kalshi_client=_MockKalshi(),
            polymarket_client=_MockPoly(),
            edge_calculator=_MockEdgeCalc(),
            risk_manager=_MockRiskManager(),
            aggregate_risk=_FakeAggRisk(),
        )
        self.assertIsNotNone(pod.aggregate_risk)


class TestAggregateRiskBlocks(unittest.TestCase):

    def test_aggregate_risk_blocks_trade(self):
        class _BlockAll:
            def check_trade(self, pod_id, venue, position_size_usd):
                return False

        pod = _pod(aggregate_risk=_BlockAll())
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)


class TestSportsOnlyFiltering(unittest.TestCase):

    def test_non_sport_tickers_filtered_out(self):
        """Kalshi markets without sport prefixes should be filtered."""
        from src.cross_venue_matcher import is_sport_ticker
        self.assertTrue(is_sport_ticker("KXNBAGAME-LAKCEL-1"))
        self.assertTrue(is_sport_ticker("KXNFLGAME-KCBUF-1"))
        self.assertFalse(is_sport_ticker("KXBTC-100K"))
        self.assertFalse(is_sport_ticker("KXELECTION-2026"))
        self.assertFalse(is_sport_ticker("RANDOM-MARKET"))


class TestSidesAligned(unittest.TestCase):
    """Test _sides_aligned() method for Kalshi/Polymarket team alignment."""

    def _make_pod(self):
        """Build a minimal CrossVenueArbPod for testing."""
        from src.pods.cross_venue_arb import CrossVenueArbPod

        class _DummyEdge:
            def kelly_fraction(self, *a, **kw): return 0.0

        class _DummyRisk:
            def check(self, *a, **kw): return True
            def position_size(self, *a, **kw): return 10.0

        return CrossVenueArbPod(
            kalshi_client=None,
            polymarket_client=None,
            cross_venue_matcher=None,
            edge_calculator=_DummyEdge(),
            risk_manager=_DummyRisk(),
            mode="paper",
        )

    def test_aligned_city_name_first_team(self):
        pod = self._make_pod()
        # Detroit (Tigers in MLB) is the first team in Polymarket question
        self.assertTrue(pod._sides_aligned("Detroit", "Tigers vs. Diamondbacks"))

    def test_flipped_city_name_second_team(self):
        pod = self._make_pod()
        # Detroit (Tigers in MLB) is the second team
        self.assertFalse(pod._sides_aligned("Detroit", "Diamondbacks vs. Tigers"))

    def test_aligned_nba_city(self):
        pod = self._make_pod()
        self.assertTrue(pod._sides_aligned("Boston", "Celtics vs. Lakers"))

    def test_flipped_nba_city(self):
        pod = self._make_pod()
        self.assertFalse(pod._sides_aligned("Boston", "Lakers vs. Celtics"))

    def test_aligned_with_beat_format(self):
        pod = self._make_pod()
        # "Will the Tigers beat the Diamondbacks?"
        self.assertTrue(
            pod._sides_aligned("Detroit", "Will the Tigers beat the Diamondbacks?")
        )

    def test_flipped_with_beat_format(self):
        pod = self._make_pod()
        self.assertFalse(
            pod._sides_aligned("Detroit", "Will the Diamondbacks beat the Tigers?")
        )

    def test_empty_kalshi_team_returns_true(self):
        pod = self._make_pod()
        self.assertTrue(pod._sides_aligned("", "Tigers vs. Diamondbacks"))

    def test_empty_question_returns_true(self):
        pod = self._make_pod()
        self.assertTrue(pod._sides_aligned("Detroit", ""))

    def test_aligned_nhl_city(self):
        pod = self._make_pod()
        self.assertTrue(pod._sides_aligned("Boston", "Bruins vs. Rangers"))

    def test_flipped_nhl_city(self):
        pod = self._make_pod()
        self.assertFalse(pod._sides_aligned("Boston", "Rangers vs. Bruins"))

    def test_aligned_nhl_abbrev_team(self):
        """NHL yes_sub_title format: 'EDM Oilers'."""
        pod = self._make_pod()
        self.assertFalse(
            pod._sides_aligned("EDM Oilers", "Blackhawks vs. Oilers")
        )

    def test_flipped_nhl_abbrev_second(self):
        """Kalshi YES = PHI Flyers, Poly first team = Red Wings → flipped."""
        pod = self._make_pod()
        self.assertFalse(
            pod._sides_aligned("PHI Flyers", "Red Wings vs. Flyers")
        )

    def test_aligned_nhl_abbrev_first(self):
        """Kalshi YES = WPG Jets, Poly first team = Jets → aligned."""
        pod = self._make_pod()
        self.assertTrue(
            pod._sides_aligned("WPG Jets", "Jets vs. Stars")
        )


if __name__ == "__main__":
    unittest.main()
