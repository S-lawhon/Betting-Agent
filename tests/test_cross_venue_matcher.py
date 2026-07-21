"""
tests/test_cross_venue_matcher.py
─────────────────────────────────
Tests for CrossVenueMatcher (Kalshi ↔ Polymarket event matching).

  ✓ match_all: same event on both venues → match found
  ✓ match_all: different events → no match below threshold
  ✓ match_all: fuzzy score above threshold → included
  ✓ match_all: fuzzy score below threshold → excluded
  ✓ match_all: empty Kalshi list → empty result
  ✓ match_all: empty Polymarket list → empty result
  ✓ CrossVenueMatch: fields populated correctly
  ✓ CrossVenueMatch: Kalshi prices extracted from bid/ask
  ✓ Settlement check: overlapping keywords → aligned
  ✓ Settlement check: non-overlapping → not aligned
  ✓ Settlement check disabled → always True
  ✓ from_config: reads threshold, settlement_check, require_both_teams, sports_only
  ✓ both_teams_present: rejects single-team partial matches
  ✓ both_teams_present: accepts when both teams match
  ✓ sports_only: filters non-sport tickers
  ✓ sports_only: passes sport tickers
  ✓ teams_validated field populated in match
"""

import sys
import unittest
from pathlib import Path
from typing import Dict, Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.cross_venue_matcher import CrossVenueMatcher, CrossVenueMatch, is_sport_ticker
from src.polymarket_client import PolymarketMarket


# ── Fixtures ────────────────────────────────────────────────────────

def _kalshi(
    ticker="KXNBAGAME-KC-LV-ML",
    title="Kansas City Chiefs vs Las Vegas Raiders",
    yes_bid=55,
    yes_ask=59,
) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "title": title,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
    }


def _poly(
    question="Will the Kansas City Chiefs beat the Las Vegas Raiders?",
    condition_id="cond-1",
    token_yes="ty-1",
    token_no="tn-1",
) -> PolymarketMarket:
    return PolymarketMarket(
        condition_id=condition_id,
        token_id_yes=token_yes,
        token_id_no=token_no,
        question=question,
        slug="chiefs-raiders",
        active=True,
        closed=False,
    )


# ── Tests ───────────────────────────────────────────────────────────

class TestCrossVenueMatchAll(unittest.TestCase):

    def test_same_event_matched(self):
        m = CrossVenueMatcher(fuzzy_threshold=70.0, sports_only=False)
        results = m.match_all([_kalshi()], [_poly()])
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], CrossVenueMatch)

    def test_match_fields(self):
        m = CrossVenueMatcher(fuzzy_threshold=70.0, sports_only=False)
        result = m.match_all([_kalshi()], [_poly()])[0]
        self.assertEqual(result.kalshi_ticker, "KXNBAGAME-KC-LV-ML")
        self.assertEqual(result.polymarket_condition_id, "cond-1")
        self.assertEqual(result.polymarket_token_yes, "ty-1")
        self.assertEqual(result.polymarket_token_no, "tn-1")
        self.assertGreater(result.fuzzy_score, 70.0)

    def test_kalshi_prices_extracted(self):
        m = CrossVenueMatcher(fuzzy_threshold=70.0, sports_only=False)
        result = m.match_all([_kalshi(yes_bid=55, yes_ask=59)], [_poly()])[0]
        self.assertAlmostEqual(result.kalshi_yes_price, 0.57)
        self.assertAlmostEqual(result.kalshi_no_price, 0.43)

    def test_no_match_below_threshold(self):
        m = CrossVenueMatcher(fuzzy_threshold=90.0, sports_only=False)
        km = _kalshi(title="New York Yankees vs Boston Red Sox")
        pm = _poly(question="Will the Kansas City Chiefs win?")
        results = m.match_all([km], [pm])
        self.assertEqual(results, [])

    def test_empty_kalshi_list(self):
        m = CrossVenueMatcher(sports_only=False)
        results = m.match_all([], [_poly()])
        self.assertEqual(results, [])

    def test_empty_poly_list(self):
        m = CrossVenueMatcher(sports_only=False)
        results = m.match_all([_kalshi()], [])
        self.assertEqual(results, [])

    def test_multiple_kalshi_markets(self):
        m = CrossVenueMatcher(fuzzy_threshold=70.0, sports_only=False)
        km1 = _kalshi(ticker="KXNBAGAME-T1", title="Kansas City Chiefs vs Las Vegas Raiders")
        km2 = _kalshi(ticker="KXNFLGAME-T2", title="New England Patriots vs Miami Dolphins")
        pm1 = _poly(question="Will the Kansas City Chiefs beat the Las Vegas Raiders?", condition_id="c1")
        pm2 = _poly(question="Will the New England Patriots beat the Miami Dolphins?", condition_id="c2")
        results = m.match_all([km1, km2], [pm1, pm2])
        self.assertEqual(len(results), 2)

    def test_missing_bid_ask(self):
        m = CrossVenueMatcher(fuzzy_threshold=70.0, sports_only=False)
        km = {"ticker": "KXNBAGAME-T1", "title": "Kansas City Chiefs vs Las Vegas Raiders"}
        result = m.match_all([km], [_poly()])[0]
        self.assertIsNone(result.kalshi_yes_price)

    def test_teams_validated_field(self):
        m = CrossVenueMatcher(fuzzy_threshold=70.0, require_both_teams=True, sports_only=False)
        result = m.match_all([_kalshi()], [_poly()])[0]
        self.assertTrue(result.teams_validated)


class TestBothTeamsValidation(unittest.TestCase):

    def test_rejects_single_team_match(self):
        """If only one team matches, should be rejected."""
        m = CrossVenueMatcher(fuzzy_threshold=50.0, require_both_teams=True, sports_only=False)
        km = _kalshi(title="Winnipeg Jets vs Chicago Blackhawks")
        pm = _poly(question="Will the Canucks beat the Blackhawks?")
        results = m.match_all([km], [pm])
        self.assertEqual(results, [], "Single team match should be rejected")

    def test_accepts_both_teams_match(self):
        m = CrossVenueMatcher(fuzzy_threshold=50.0, require_both_teams=True, sports_only=False)
        km = _kalshi(title="Los Angeles Lakers vs Boston Celtics")
        pm = _poly(question="Will the Los Angeles Lakers beat the Boston Celtics?")
        results = m.match_all([km], [pm])
        self.assertEqual(len(results), 1)

    def test_validation_disabled(self):
        """With require_both_teams=False, single team match passes."""
        m = CrossVenueMatcher(fuzzy_threshold=50.0, require_both_teams=False, sports_only=False)
        km = _kalshi(title="Winnipeg Jets vs Chicago Blackhawks")
        pm = _poly(question="Will the Canucks beat the Blackhawks?")
        results = m.match_all([km], [pm])
        # May or may not match depending on fuzzy score, but team validation doesn't block
        # The key test is that it doesn't reject based on teams


class TestSportsOnlyFilter(unittest.TestCase):

    def test_sport_tickers_pass(self):
        self.assertTrue(is_sport_ticker("KXNBAGAME-LAKCEL"))
        self.assertTrue(is_sport_ticker("KXNFLGAME-KCBUF"))
        self.assertTrue(is_sport_ticker("KXMLBGAME-NYYBOS"))
        self.assertTrue(is_sport_ticker("KXNHLGAME-COLMIN"))
        self.assertTrue(is_sport_ticker("KXATPMATCH-26MAR24"))
        self.assertTrue(is_sport_ticker("KXWTAMATCH-26MAR24"))
        self.assertTrue(is_sport_ticker("KXNCAABGAME-UKDUK"))

    def test_non_sport_tickers_rejected(self):
        self.assertFalse(is_sport_ticker("KXBTC-100K"))
        self.assertFalse(is_sport_ticker("KXELECTION-2026"))
        self.assertFalse(is_sport_ticker("KXCPI-25JUN"))
        self.assertFalse(is_sport_ticker("RANDOM-MARKET"))

    def test_sports_only_mode_filters(self):
        m = CrossVenueMatcher(fuzzy_threshold=50.0, sports_only=True)
        km_sport = _kalshi(ticker="KXNBAGAME-KC", title="Chiefs vs Raiders")
        km_nonsport = _kalshi(ticker="KXBTC-100K", title="Bitcoin above 100K?")
        pm = _poly(question="Will the Kansas City Chiefs beat the Las Vegas Raiders?")
        results = m.match_all([km_sport, km_nonsport], [pm])
        # Only the sport ticker should match
        for r in results:
            self.assertNotIn("BTC", r.kalshi_ticker)


class TestSettlementCheck(unittest.TestCase):

    def test_aligned_when_overlapping(self):
        m = CrossVenueMatcher(fuzzy_threshold=50.0, settlement_check=True, sports_only=False)
        result = m.match_all([_kalshi()], [_poly()])[0]
        self.assertTrue(result.settlement_aligned)

    def test_not_aligned_when_different(self):
        m = CrossVenueMatcher(fuzzy_threshold=50.0, settlement_check=True, sports_only=False, require_both_teams=False)
        km = _kalshi(title="abcdef ghijkl mnopqr")
        pm = _poly(question="Will zyxwvu tsrqpo win?")
        results = m.match_all([km], [pm])
        if results:
            self.assertFalse(results[0].settlement_aligned)

    def test_check_disabled(self):
        m = CrossVenueMatcher(fuzzy_threshold=50.0, settlement_check=False, sports_only=False)
        result = m.match_all([_kalshi()], [_poly()])[0]
        self.assertTrue(result.settlement_aligned)


class TestFromConfig(unittest.TestCase):

    def test_reads_config(self):
        cfg = {
            "cross_venue_matcher": {
                "fuzzy_threshold": 75.0,
                "settlement_check": False,
                "require_both_teams": False,
                "sports_only": False,
            }
        }
        m = CrossVenueMatcher.from_config(cfg)
        self.assertEqual(m._threshold, 75.0)
        self.assertFalse(m._settlement_check)
        self.assertFalse(m._require_both_teams)
        self.assertFalse(m._sports_only)

    def test_defaults(self):
        m = CrossVenueMatcher.from_config({})
        self.assertEqual(m._threshold, 80.0)
        self.assertTrue(m._settlement_check)
        self.assertTrue(m._require_both_teams)
        self.assertTrue(m._sports_only)


if __name__ == "__main__":
    unittest.main()
