"""
tests/test_polymarket_matcher.py
────────────────────────────────
Tests for PolymarketMatcher.

  ✓ _fuzzy_score: exact match → 100
  ✓ _fuzzy_score: case insensitive
  ✓ _fuzzy_score: completely different → low score
  ✓ _extract_teams_from_question: strips "Will ... beat" patterns
  ✓ _extract_teams_from_question: strips "Will ... win?" patterns
  ✓ match: success — returns PolymarketMatchResult
  ✓ match: SPORT_NOT_CONFIGURED rejection
  ✓ match: ODDS_API_NO_DATA rejection
  ✓ match: NO_FUZZY_MATCH rejection
  ✓ match: TEAM_MISMATCH rejection — single team overlap
  ✓ match: TEAM_MISMATCH — Jets vs Blackhawks ≠ Canucks vs Blackhawks
  ✓ match: both teams present with nicknames → success
  ✓ match: TIME_WINDOW_EXCEEDED rejection
  ✓ match: time check skipped when no Polymarket time
  ✓ match: picks best-scoring event
  ✓ match_all: returns only successful matches
  ✓ match_all: empty markets → empty result
  ✓ Unmatched log: record written on rejection
  ✓ Unmatched log: write error handled gracefully
  ✓ from_config: reads threshold, window, sports
  ✓ team_name_in_text: full name, nickname, two-word nickname
  ✓ both_teams_present: requires BOTH teams
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.polymarket_client import PolymarketMarket
from src.fuzzy_utils import both_teams_present, team_name_in_text
from src.polymarket_matcher import (
    REASON_NO_FUZZY_MATCH,
    REASON_ODDS_API_NO_DATA,
    REASON_SPORT_NOT_CONFIGURED,
    REASON_TEAM_MISMATCH,
    REASON_TIME_WINDOW_EXCEEDED,
    PolymarketMatchResult,
    PolymarketMatcher,
    _extract_teams_from_question,
    _fuzzy_score,
)


# ── Fixtures ────────────────────────────────────────────────────────

_GAME_TIME = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


class _OddsEvent:
    """Minimal OddsEvent mock."""
    def __init__(self, home="Kansas City Chiefs", away="Las Vegas Raiders",
                 event_id="evt-1", sport="americanfootball_nfl",
                 commence=None):
        self.home_team = home
        self.away_team = away
        self.event_id = event_id
        self.sport_key = sport
        self.commence_time = commence or _GAME_TIME


def _poly_market(
    question="Will the Kansas City Chiefs beat the Las Vegas Raiders?",
    condition_id="cond-1",
    token_yes="tok-yes-1",
    token_no="tok-no-1",
    game_start="2024-01-15T18:00:00Z",
    end_date=None,
) -> PolymarketMarket:
    return PolymarketMarket(
        condition_id=condition_id,
        token_id_yes=token_yes,
        token_id_no=token_no,
        question=question,
        slug="chiefs-vs-raiders",
        active=True,
        closed=False,
        game_start_time=game_start,
        end_date=end_date,
    )


def _matcher(
    threshold=80.0,
    window=60.0,
    sports=None,
    log_path=None,
) -> PolymarketMatcher:
    return PolymarketMatcher(
        fuzzy_threshold=threshold,
        time_window_minutes=window,
        configured_sports=sports or ["americanfootball_nfl"],
        unmatched_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
    )


# ── _fuzzy_score ────────────────────────────────────────────────────

class TestFuzzyScore(unittest.TestCase):

    def test_exact_match(self):
        self.assertAlmostEqual(_fuzzy_score("chiefs raiders", "chiefs raiders"), 100.0)

    def test_case_insensitive(self):
        score = _fuzzy_score("Kansas City Chiefs", "kansas city chiefs")
        self.assertAlmostEqual(score, 100.0)

    def test_different_strings_low(self):
        score = _fuzzy_score("Kansas City Chiefs", "Boston Celtics")
        self.assertLess(score, 50.0)

    def test_partial_overlap(self):
        score = _fuzzy_score("Kansas City Chiefs", "Kansas City Chiefs vs Raiders")
        self.assertGreater(score, 60.0)


# ── _extract_teams_from_question ────────────────────────────────────

class TestExtractTeams(unittest.TestCase):

    def test_will_beat_pattern(self):
        result = _extract_teams_from_question(
            "Will the Kansas City Chiefs beat the Las Vegas Raiders?"
        )
        self.assertIn("Kansas City Chiefs", result)
        self.assertIn("Las Vegas Raiders", result)

    def test_will_win_pattern(self):
        result = _extract_teams_from_question("Will Kansas City Chiefs win?")
        self.assertIn("Kansas City Chiefs", result)

    def test_plain_question(self):
        result = _extract_teams_from_question("Chiefs vs Raiders")
        self.assertEqual(result, "Chiefs Raiders")


# ── match: success ──────────────────────────────────────────────────

class TestMatcherSuccess(unittest.TestCase):

    def test_successful_match(self):
        m = _matcher()
        events = [_OddsEvent()]
        market = _poly_market()
        result = m.match(market, events, sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, PolymarketMatchResult)

    def test_result_fields(self):
        m = _matcher()
        event = _OddsEvent()
        market = _poly_market(condition_id="cond-X", token_yes="ty", token_no="tn")
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertEqual(result.polymarket_condition_id, "cond-X")
        self.assertEqual(result.token_id_yes, "ty")
        self.assertEqual(result.token_id_no, "tn")
        self.assertIs(result.odds_event, event)
        self.assertGreater(result.fuzzy_score, 80.0)

    def test_picks_best_scoring_event(self):
        m = _matcher()
        ev1 = _OddsEvent(home="New England Patriots", away="Miami Dolphins", event_id="e1")
        ev2 = _OddsEvent(home="Kansas City Chiefs", away="Las Vegas Raiders", event_id="e2")
        market = _poly_market()
        result = m.match(market, [ev1, ev2], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertEqual(result.odds_event.event_id, "e2")

    def test_time_check_skipped_when_no_polymarket_time(self):
        m = _matcher()
        late_event = _OddsEvent(commence=_GAME_TIME + timedelta(hours=5))
        market = _poly_market(game_start=None, end_date=None)
        # market has no time → time check skipped
        result = m.match(market, [late_event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)


# ── match: rejections ───────────────────────────────────────────────

class TestMatcherRejections(unittest.TestCase):

    def setUp(self):
        self._log_path = Path(tempfile.mktemp(suffix=".jsonl"))

    def _read_log(self):
        if not self._log_path.exists():
            return []
        lines = self._log_path.read_text().strip().splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    def test_sport_not_configured(self):
        m = _matcher(sports=["americanfootball_nfl"], log_path=self._log_path)
        result = m.match(_poly_market(), [_OddsEvent()], sport="basketball_nba")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_SPORT_NOT_CONFIGURED)

    def test_odds_api_no_data(self):
        m = _matcher(log_path=self._log_path)
        result = m.match(_poly_market(), [], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_ODDS_API_NO_DATA)

    def test_no_fuzzy_match(self):
        m = _matcher(log_path=self._log_path)
        market = _poly_market(question="Will the Boston Celtics win?")
        result = m.match(market, [_OddsEvent()], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_NO_FUZZY_MATCH)

    def test_no_fuzzy_match_has_best_fields(self):
        m = _matcher(log_path=self._log_path)
        market = _poly_market(question="Will the Boston Celtics win?")
        m.match(market, [_OddsEvent()], sport="americanfootball_nfl")
        records = self._read_log()
        self.assertIn("best_candidate", records[0])
        self.assertIn("best_score", records[0])

    def test_time_window_exceeded(self):
        m = _matcher(window=30.0, log_path=self._log_path)
        late_event = _OddsEvent(commence=_GAME_TIME + timedelta(hours=2))
        market = _poly_market(game_start="2024-01-15T18:00:00Z")
        result = m.match(market, [late_event], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_TIME_WINDOW_EXCEEDED)

    def test_team_mismatch_single_overlap(self):
        """Jets vs Blackhawks should NOT match 'Canucks vs. Blackhawks'."""
        m = _matcher(threshold=40.0, log_path=self._log_path)
        event = _OddsEvent(home="Winnipeg Jets", away="Chicago Blackhawks")
        market = _poly_market(question="Canucks vs. Blackhawks")
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_TEAM_MISMATCH)

    def test_team_mismatch_wrong_opponent(self):
        """Flames vs Stars should NOT match 'Flames vs. Capitals'."""
        m = _matcher(threshold=40.0, log_path=self._log_path)
        event = _OddsEvent(home="Calgary Flames", away="Dallas Stars")
        market = _poly_market(question="Flames vs. Capitals")
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_TEAM_MISMATCH)

    def test_team_mismatch_mlb_wrong_team(self):
        """Giants vs Yankees should NOT match 'Twins vs. Yankees'."""
        m = _matcher(threshold=40.0, log_path=self._log_path)
        event = _OddsEvent(home="San Francisco Giants", away="New York Yankees")
        market = _poly_market(question="Minnesota Twins vs. New York Yankees")
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_TEAM_MISMATCH)

    def test_both_teams_present_allows_match(self):
        """Both teams present (via nickname) should pass verification."""
        m = _matcher(threshold=40.0)
        event = _OddsEvent(home="Kansas City Chiefs", away="Las Vegas Raiders")
        market = _poly_market(question="Chiefs vs. Raiders")
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)

    def test_both_teams_present_full_names(self):
        """Full team names in question should pass."""
        m = _matcher(threshold=40.0)
        event = _OddsEvent(home="Milwaukee Bucks", away="Atlanta Hawks")
        market = _poly_market(
            question="Will the Milwaukee Bucks beat the Atlanta Hawks?"
        )
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)


# ── match_all ───────────────────────────────────────────────────────

class TestMatchAll(unittest.TestCase):

    def test_returns_only_successful(self):
        m = _matcher()
        markets = [
            _poly_market(question="Will the Kansas City Chiefs beat the Las Vegas Raiders?"),
            _poly_market(question="Will the Boston Celtics win?", condition_id="c2"),
        ]
        events = [_OddsEvent()]
        results = m.match_all(markets, events, sport="americanfootball_nfl")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].polymarket_condition_id, "cond-1")

    def test_empty_markets(self):
        m = _matcher()
        results = m.match_all([], [_OddsEvent()])
        self.assertEqual(results, [])

    def test_empty_events_all_rejected(self):
        m = _matcher()
        markets = [_poly_market(), _poly_market(condition_id="c2")]
        results = m.match_all(markets, [], sport="americanfootball_nfl")
        self.assertEqual(results, [])


# ── Unmatched log ───────────────────────────────────────────────────

class TestUnmatchedLog(unittest.TestCase):

    def test_record_written_on_rejection(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        m = _matcher(log_path=log_path)
        m.match(_poly_market(), [], sport="americanfootball_nfl")
        self.assertTrue(log_path.exists())

    def test_write_error_handled(self):
        bad_path = Path("/dev/null/not_a_dir/log.jsonl")
        m = _matcher(log_path=bad_path)
        # Should not raise
        m.match(_poly_market(), [], sport="americanfootball_nfl")


# ── from_config ─────────────────────────────────────────────────────

class TestFromConfig(unittest.TestCase):

    def test_reads_threshold(self):
        cfg = {"polymarket": {"matcher": {"fuzzy_threshold": 75.0}}}
        m = PolymarketMatcher.from_config(cfg)
        self.assertEqual(m._threshold, 75.0)

    def test_reads_window(self):
        cfg = {"polymarket": {"matcher": {"time_window_minutes": 90.0}}}
        m = PolymarketMatcher.from_config(cfg)
        self.assertEqual(m._window_minutes, 90.0)

    def test_reads_sports(self):
        cfg = {"odds_api": {"sports": ["americanfootball_nfl", "basketball_nba"]}}
        m = PolymarketMatcher.from_config(cfg)
        self.assertIn("americanfootball_nfl", m._sports)
        self.assertIn("basketball_nba", m._sports)

    def test_defaults(self):
        m = PolymarketMatcher.from_config({})
        self.assertEqual(m._threshold, 55.0)
        self.assertEqual(m._window_minutes, 60.0)


# ── team_name_in_text / both_teams_present ─────────────────────────

class TestTeamNameInText(unittest.TestCase):

    def test_full_name(self):
        self.assertTrue(team_name_in_text("Kansas City Chiefs", "kansas city chiefs las vegas raiders"))

    def test_nickname(self):
        self.assertTrue(team_name_in_text("Kansas City Chiefs", "Chiefs vs Raiders"))

    def test_two_word_nickname(self):
        self.assertTrue(team_name_in_text("Portland Trail Blazers", "blazers grizzlies trail blazers"))

    def test_no_match(self):
        self.assertFalse(team_name_in_text("Kansas City Chiefs", "boston celtics lakers"))

    def test_short_token_ignored(self):
        # "FC" is only 2 chars, should not match on its own
        self.assertFalse(team_name_in_text("FC Barcelona", "FC Porto vs Benfica"))

    def test_case_insensitive(self):
        self.assertTrue(team_name_in_text("New York KNICKS", "knicks vs thunder"))

    def test_maple_leafs(self):
        self.assertTrue(team_name_in_text("Toronto Maple Leafs", "Maple Leafs vs Canadiens"))

    def test_single_shared_word_not_enough(self):
        # "New" appears in both but is too short (3 chars) to be nickname match
        self.assertFalse(team_name_in_text("New York Knicks", "New Orleans Pelicans"))


class TestBothTeamsPresent(unittest.TestCase):

    def test_both_present(self):
        self.assertTrue(both_teams_present(
            "Kansas City Chiefs", "Las Vegas Raiders",
            "Chiefs vs Raiders"
        ))

    def test_one_missing(self):
        self.assertFalse(both_teams_present(
            "Winnipeg Jets", "Chicago Blackhawks",
            "Canucks vs Blackhawks"
        ))

    def test_both_missing(self):
        self.assertFalse(both_teams_present(
            "Kansas City Chiefs", "Las Vegas Raiders",
            "Celtics vs Lakers"
        ))

    def test_nhl_correct_match(self):
        self.assertTrue(both_teams_present(
            "Vancouver Canucks", "Carolina Hurricanes",
            "Canucks vs. Hurricanes"
        ))


if __name__ == "__main__":
    unittest.main()
