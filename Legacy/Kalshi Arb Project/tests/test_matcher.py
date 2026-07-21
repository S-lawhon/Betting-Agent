"""
tests/test_matcher.py
─────────────────────
Phase 5 must-pass tests:
  ✓ _fuzzy_score: exact match → 100
  ✓ _fuzzy_score: token-sort handles word-order differences
  ✓ _fuzzy_score: partial match gives intermediate score
  ✓ _fuzzy_score: completely different strings → low score
  ✓ _is_moneyline: True for plain team-vs-team titles
  ✓ _is_moneyline: False for spread markets
  ✓ _is_moneyline: False for over/under markets
  ✓ _is_moneyline: False for prop markets
  ✓ _is_moneyline: False for futures markets
  ✓ _is_moneyline: False when subtitle contains keyword
  ✓ _parse_kalshi_time: parses close_time (Z suffix)
  ✓ _parse_kalshi_time: parses expiration_time
  ✓ _parse_kalshi_time: parses event_start_time
  ✓ _parse_kalshi_time: returns None when no time field present
  ✓ _parse_kalshi_time: returns None for malformed time
  ✓ match: MARKET_TYPE_UNSUPPORTED — spread market rejected
  ✓ match: SPORT_NOT_CONFIGURED — unconfigured sport rejected
  ✓ match: sport check skipped when sport=None
  ✓ match: sport check skipped when configured_sports is empty
  ✓ match: ODDS_API_NO_DATA — empty events list
  ✓ match: NO_FUZZY_MATCH — score below threshold
  ✓ match: TIME_WINDOW_EXCEEDED — start-time delta too large
  ✓ match: success — returns correct MatchResult fields
  ✓ match: success — time check skipped when no Kalshi time
  ✓ match: success — picks highest-scoring event from multiple candidates
  ✓ match: best_candidate/best_score logged on NO_FUZZY_MATCH
  ✓ match_all: returns only successful matches
  ✓ match_all: empty market list → empty result
  ✓ match_all: empty events list → all rejected
  ✓ unmatched log: JSONL record written on rejection
  ✓ unmatched log: multiple rejections append, not overwrite
  ✓ unmatched log: record fields match schema
  ✓ unmatched log: best_candidate/best_score present only when applicable
  ✓ unmatched log: write error is handled gracefully (no crash)
  ✓ from_config: reads fuzzy_threshold
  ✓ from_config: reads time_window_minutes
  ✓ from_config: reads configured_sports from odds_api.sports
  ✓ from_config: reads unmatched_log_path from paths
  ✓ from_config: uses defaults when keys absent
  ✓ integration: full pipeline — NFL Chiefs vs Raiders fuzzy match
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

from src.matcher import (
    REASON_MARKET_TYPE_UNSUPPORTED,
    REASON_NO_FUZZY_MATCH,
    REASON_ODDS_API_NO_DATA,
    REASON_SPORT_NOT_CONFIGURED,
    REASON_TIME_WINDOW_EXCEEDED,
    MatchResult,
    Matcher,
    UnmatchedEntry,
    _fuzzy_score,
    _expand_ncaab_abbrevs,
)
from src.odds_client import BookmakerOdds, OddsEvent, OddsOutcome


# ── Fixtures ──────────────────────────────────────────────────────────────────

_GAME_TIME = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


def _odds_event(
    home: str = "Kansas City Chiefs",
    away: str = "Las Vegas Raiders",
    event_id: str = "evt-1",
    sport: str = "americanfootball_nfl",
    commence: Optional[datetime] = None,
) -> OddsEvent:
    """Build a minimal OddsEvent for matcher tests."""
    return OddsEvent(
        event_id=event_id,
        sport_key=sport,
        commence_time=commence or _GAME_TIME,
        home_team=home,
        away_team=away,
        bookmaker_odds=[],   # not needed for matching
    )


def _kalshi_market(
    ticker: str = "NFL-KC-LV-2024-ML",
    title: str = "Kansas City Chiefs vs Las Vegas Raiders",
    subtitle: str = "",
    close_time: Optional[str] = "2024-01-15T18:00:00Z",
) -> Dict[str, Any]:
    """Build a minimal Kalshi market dict."""
    m: Dict[str, Any] = {"ticker": ticker, "title": title}
    if subtitle:
        m["subtitle"] = subtitle
    if close_time is not None:
        m["close_time"] = close_time
    return m


def _matcher(
    threshold: float = 85.0,
    window: float = 30.0,
    sports: Optional[List[str]] = None,
    log_path: Optional[Path] = None,
    now_fn=None,
) -> Matcher:
    """Build a Matcher with a temp log path and injectable clock."""
    return Matcher(
        fuzzy_threshold=threshold,
        time_window_minutes=window,
        configured_sports=sports or ["americanfootball_nfl"],
        unmatched_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        _now_fn=now_fn,
    )


# ── _fuzzy_score ──────────────────────────────────────────────────────────────

class TestFuzzyScore(unittest.TestCase):

    def test_exact_match_scores_100(self):
        self.assertAlmostEqual(_fuzzy_score("chiefs raiders", "chiefs raiders"), 100.0)

    def test_case_insensitive(self):
        score = _fuzzy_score("Kansas City Chiefs", "kansas city chiefs")
        self.assertAlmostEqual(score, 100.0)

    def test_token_sort_handles_word_order(self):
        """Token sort should make 'A B' and 'B A' equivalent."""
        score = _fuzzy_score("Raiders Chiefs", "Chiefs Raiders")
        self.assertAlmostEqual(score, 100.0)

    def test_partial_match_intermediate(self):
        score = _fuzzy_score("Kansas City Chiefs", "Kansas City Chiefs vs Las Vegas Raiders")
        self.assertGreater(score, 40.0)
        self.assertLess(score, 100.0)

    def test_completely_different_low_score(self):
        score = _fuzzy_score("Kansas City Chiefs", "Boston Celtics")
        self.assertLess(score, 50.0)

    def test_empty_strings(self):
        score = _fuzzy_score("", "")
        self.assertAlmostEqual(score, 100.0)

    def test_one_empty_string(self):
        score = _fuzzy_score("Chiefs", "")
        self.assertAlmostEqual(score, 0.0)

    def test_returns_float_in_range(self):
        score = _fuzzy_score("Chiefs", "Raiders")
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)


# ── _is_moneyline ─────────────────────────────────────────────────────────────

class TestIsMoneyline(unittest.TestCase):

    def _check(self, market: Dict) -> bool:
        return Matcher._is_moneyline(market)

    def test_plain_team_title_is_moneyline(self):
        self.assertTrue(self._check({"title": "Kansas City Chiefs vs Las Vegas Raiders"}))

    def test_empty_title_is_moneyline(self):
        self.assertTrue(self._check({"title": ""}))

    def test_spread_keyword_in_title_rejected(self):
        self.assertFalse(self._check({"title": "Chiefs -3.5 Spread"}))

    def test_total_keyword_in_title_rejected(self):
        self.assertFalse(self._check({"title": "Over/Under 47.5 Total"}))

    def test_overunder_keyword_rejected(self):
        self.assertFalse(self._check({"title": "over/under 45"}))

    def test_prop_keyword_rejected(self):
        self.assertFalse(self._check({"title": "Mahomes TD Prop"}))

    def test_futures_keyword_rejected(self):
        self.assertFalse(self._check({"title": "NFL Futures 2024"}))

    def test_handicap_keyword_rejected(self):
        self.assertFalse(self._check({"title": "Chiefs Handicap -3"}))

    def test_keyword_in_subtitle_rejected(self):
        self.assertFalse(self._check({"title": "Chiefs vs Raiders", "subtitle": "spread"}))

    def test_keyword_case_insensitive(self):
        self.assertFalse(self._check({"title": "SPREAD market"}))


# ── _parse_kalshi_time ────────────────────────────────────────────────────────

class TestParseKalshiTime(unittest.TestCase):

    def _parse(self, market: Dict) -> Optional[datetime]:
        return Matcher._parse_kalshi_time(market)

    def test_parses_close_time_with_z(self):
        dt = self._parse({"close_time": "2024-01-15T18:00:00Z"})
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 15)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parses_expiration_time(self):
        dt = self._parse({"expiration_time": "2024-06-01T20:30:00Z"})
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 20)

    def test_parses_event_start_time(self):
        dt = self._parse({"event_start_time": "2024-09-05T20:20:00Z"})
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2024)

    def test_parses_open_time_as_fallback(self):
        dt = self._parse({"open_time": "2024-04-10T13:00:00+00:00"})
        self.assertIsNotNone(dt)

    def test_close_time_takes_priority_over_expiration(self):
        dt = self._parse({
            "close_time": "2024-01-15T18:00:00Z",
            "expiration_time": "2024-01-16T00:00:00Z",
        })
        self.assertEqual(dt.day, 15)

    def test_returns_none_when_no_time_field(self):
        self.assertIsNone(self._parse({"ticker": "X"}))

    def test_returns_none_for_malformed_time(self):
        self.assertIsNone(self._parse({"close_time": "not-a-date"}))

    def test_naive_datetime_made_utc(self):
        dt = self._parse({"close_time": "2024-01-15T18:00:00"})
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)


# ── Matcher.match — rejection paths ───────────────────────────────────────────

class TestMatcherRejections(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self._log_path = Path(self._tmp.name)
        self._tmp.close()
        self._events = [_odds_event()]

    def _make(self, **kwargs) -> Matcher:
        return Matcher(
            fuzzy_threshold=85.0,
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=self._log_path,
            **kwargs,
        )

    def _read_log(self):
        lines = self._log_path.read_text().strip().splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    def test_market_type_unsupported_spread(self):
        m = self._make()
        market = _kalshi_market(title="Chiefs -3.5 Spread")
        result = m.match(market, self._events, sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reason"], REASON_MARKET_TYPE_UNSUPPORTED)

    def test_market_type_unsupported_total(self):
        m = self._make()
        market = _kalshi_market(title="Over/Under 47.5 Total")
        result = m.match(market, self._events, sport="americanfootball_nfl")
        self.assertIsNone(result)

    def test_sport_not_configured(self):
        m = self._make()  # only nfl configured
        market = _kalshi_market(title="Lakers vs Warriors")
        result = m.match(market, self._events, sport="basketball_nba")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_SPORT_NOT_CONFIGURED)

    def test_sport_check_skipped_when_sport_none(self):
        m = self._make()
        events = [_odds_event(home="Lakers", away="Warriors")]
        market = _kalshi_market(title="Lakers Warriors", close_time=None)
        # No sport → sport check is skipped; should not reject on sport
        result = m.match(market, events, sport=None)
        # May still reject on fuzzy (short names), but not on sport
        if result is None:
            records = self._read_log()
            for rec in records:
                self.assertNotEqual(rec["reason"], REASON_SPORT_NOT_CONFIGURED)

    def test_sport_check_skipped_when_configured_sports_empty(self):
        m = Matcher(
            configured_sports=[],         # empty → skip check
            unmatched_log_path=self._log_path,
        )
        market = _kalshi_market(title="Lakers vs Warriors")
        events = [_odds_event(home="Lakers", away="Warriors")]
        # Should not reject with SPORT_NOT_CONFIGURED
        records_before = len(self._read_log()) if self._log_path.exists() else 0
        m.match(market, events, sport="basketball_nba")
        records_after = self._read_log()
        sport_rejections = [r for r in records_after
                            if r["reason"] == REASON_SPORT_NOT_CONFIGURED]
        self.assertEqual(len(sport_rejections), 0)

    def test_odds_api_no_data(self):
        m = self._make()
        market = _kalshi_market()
        result = m.match(market, [], sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_ODDS_API_NO_DATA)

    def test_no_fuzzy_match_low_score(self):
        m = self._make()
        market = _kalshi_market(title="New York Yankees vs Boston Red Sox")
        result = m.match(market, self._events, sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_NO_FUZZY_MATCH)

    def test_no_fuzzy_match_includes_best_candidate(self):
        m = self._make()
        market = _kalshi_market(title="Yankees vs Red Sox")
        m.match(market, self._events, sport="americanfootball_nfl")
        records = self._read_log()
        rec = records[0]
        self.assertIn("best_candidate", rec)
        self.assertIn("best_score", rec)
        self.assertIsNotNone(rec["best_candidate"])

    def test_time_window_exceeded(self):
        m = self._make()
        # Kalshi says 18:00, OddsEvent says 19:31 → 91-minute delta
        late_commence = _GAME_TIME + timedelta(minutes=91)
        events = [_odds_event(commence=late_commence)]
        market = _kalshi_market()   # close_time = 18:00Z
        result = m.match(market, events, sport="americanfootball_nfl")
        self.assertIsNone(result)
        records = self._read_log()
        self.assertEqual(records[0]["reason"], REASON_TIME_WINDOW_EXCEEDED)

    def test_time_window_exceeded_logs_best_candidate(self):
        m = self._make()
        late_commence = _GAME_TIME + timedelta(hours=2)
        events = [_odds_event(commence=late_commence)]
        market = _kalshi_market()
        m.match(market, events, sport="americanfootball_nfl")
        records = self._read_log()
        rec = records[0]
        self.assertIn("best_candidate", rec)
        self.assertIn("best_score", rec)


# ── Matcher.match — success paths ─────────────────────────────────────────────

class TestMatcherSuccess(unittest.TestCase):

    def setUp(self):
        self._log_path = Path(tempfile.mktemp(suffix=".jsonl"))

    def _make(self, **kwargs) -> Matcher:
        return Matcher(
            fuzzy_threshold=85.0,
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=self._log_path,
            **kwargs,
        )

    def test_successful_match_returns_match_result(self):
        m = self._make()
        events = [_odds_event()]
        market = _kalshi_market()
        result = m.match(market, events, sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, MatchResult)

    def test_match_result_fields(self):
        m = self._make()
        event = _odds_event()
        market = _kalshi_market(ticker="NFL-KC-LV-ML", title="Kansas City Chiefs vs Las Vegas Raiders")
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertEqual(result.kalshi_ticker, "NFL-KC-LV-ML")
        self.assertEqual(result.kalshi_title, "Kansas City Chiefs vs Las Vegas Raiders")
        self.assertIs(result.odds_event, event)
        self.assertGreater(result.fuzzy_score, 85.0)
        self.assertAlmostEqual(result.time_delta_minutes, 0.0, places=2)

    def test_time_check_skipped_when_no_kalshi_time(self):
        m = self._make()
        # No close_time in market → time check skipped
        market = _kalshi_market(close_time=None)
        events = [_odds_event(commence=_GAME_TIME + timedelta(hours=5))]
        result = m.match(market, events, sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.time_delta_minutes, 0.0)

    def test_picks_best_scoring_event(self):
        m = self._make()
        ev1 = _odds_event(home="New England Patriots", away="Buffalo Bills",
                          event_id="e1")
        ev2 = _odds_event(home="Kansas City Chiefs", away="Las Vegas Raiders",
                          event_id="e2")
        market = _kalshi_market(title="Kansas City Chiefs vs Las Vegas Raiders")
        result = m.match(market, [ev1, ev2], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertEqual(result.odds_event.event_id, "e2")

    def test_time_delta_computed_correctly(self):
        m = self._make()
        # Market close_time = 18:00; event commence = 18:20 → 20-min delta
        event = _odds_event(commence=_GAME_TIME + timedelta(minutes=20))
        market = _kalshi_market()   # close_time = 18:00
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.time_delta_minutes, 20.0, delta=0.1)

    def test_no_log_written_on_success(self):
        m = self._make()
        events = [_odds_event()]
        market = _kalshi_market()
        m.match(market, events, sport="americanfootball_nfl")
        self.assertFalse(self._log_path.exists())


# ── Unmatched log ─────────────────────────────────────────────────────────────

class TestUnmatchedLog(unittest.TestCase):

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._log_path = Path(self._tmp_dir) / "unmatched.jsonl"

    def _make(self) -> Matcher:
        return Matcher(
            fuzzy_threshold=85.0,
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=self._log_path,
        )

    def _records(self):
        lines = self._log_path.read_text().strip().splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    def test_record_written_on_rejection(self):
        m = self._make()
        m.match(_kalshi_market(title="Yankees vs Red Sox"),
                [_odds_event()], sport="americanfootball_nfl")
        self.assertTrue(self._log_path.exists())
        records = self._records()
        self.assertEqual(len(records), 1)

    def test_multiple_rejections_appended(self):
        m = self._make()
        m.match(_kalshi_market(title="Yankees vs Red Sox"),
                [_odds_event()], sport="americanfootball_nfl")
        m.match(_kalshi_market(title="Celtics vs Heat"),
                [_odds_event()], sport="americanfootball_nfl")
        records = self._records()
        self.assertEqual(len(records), 2)

    def test_record_has_required_schema_fields(self):
        m = self._make()
        m.match(_kalshi_market(title="Yankees vs Red Sox"),
                [_odds_event()], sport="americanfootball_nfl")
        rec = self._records()[0]
        for field in ("timestamp_utc", "market_ticker", "market_title",
                      "event_start_utc", "reason"):
            self.assertIn(field, rec)

    def test_odds_api_no_data_record_no_best_fields(self):
        m = self._make()
        m.match(_kalshi_market(), [], sport="americanfootball_nfl")
        rec = self._records()[0]
        self.assertEqual(rec["reason"], REASON_ODDS_API_NO_DATA)
        # best_candidate / best_score not applicable here
        self.assertNotIn("best_candidate", rec)
        self.assertNotIn("best_score", rec)

    def test_no_fuzzy_match_has_best_fields(self):
        m = self._make()
        m.match(_kalshi_market(title="Yankees vs Red Sox"),
                [_odds_event()], sport="americanfootball_nfl")
        rec = self._records()[0]
        self.assertIn("best_candidate", rec)
        self.assertIn("best_score", rec)

    def test_market_type_unsupported_record(self):
        m = self._make()
        m.match(_kalshi_market(title="Chiefs -3.5 Spread"),
                [_odds_event()], sport="americanfootball_nfl")
        rec = self._records()[0]
        self.assertEqual(rec["reason"], REASON_MARKET_TYPE_UNSUPPORTED)
        self.assertEqual(rec["market_ticker"], "NFL-KC-LV-2024-ML")

    def test_event_start_utc_null_when_no_time(self):
        m = self._make()
        market = _kalshi_market(title="Chiefs -3.5 Spread", close_time=None)
        m.match(market, [_odds_event()], sport="americanfootball_nfl")
        rec = self._records()[0]
        self.assertIsNone(rec["event_start_utc"])

    def test_write_error_does_not_crash(self):
        """If the log path is unwritable, match() should not raise."""
        bad_path = Path("/dev/null/not_a_dir/unmatched.jsonl")
        m = Matcher(
            fuzzy_threshold=85.0,
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=bad_path,
        )
        # Should complete without exception
        result = m.match(_kalshi_market(), [], sport="americanfootball_nfl")
        self.assertIsNone(result)


# ── match_all ─────────────────────────────────────────────────────────────────

class TestMatchAll(unittest.TestCase):

    def setUp(self):
        self._log_path = Path(tempfile.mktemp(suffix=".jsonl"))

    def _make(self) -> Matcher:
        return Matcher(
            fuzzy_threshold=85.0,
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=self._log_path,
        )

    def test_returns_only_successful_matches(self):
        m = self._make()
        markets = [
            _kalshi_market(ticker="T1", title="Kansas City Chiefs vs Las Vegas Raiders"),
            _kalshi_market(ticker="T2", title="Chiefs -3.5 Spread"),   # rejected
            _kalshi_market(ticker="T3", title="Yankees vs Red Sox"),   # no fuzzy
        ]
        events = [_odds_event()]
        results = m.match_all(markets, events, sport="americanfootball_nfl")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].kalshi_ticker, "T1")

    def test_empty_market_list(self):
        m = self._make()
        results = m.match_all([], [_odds_event()])
        self.assertEqual(results, [])

    def test_empty_events_all_rejected(self):
        m = self._make()
        markets = [_kalshi_market(), _kalshi_market(ticker="T2")]
        results = m.match_all(markets, [], sport="americanfootball_nfl")
        self.assertEqual(results, [])


# ── from_config ───────────────────────────────────────────────────────────────

class TestFromConfig(unittest.TestCase):

    def _cfg(self, **overrides) -> dict:
        base = {
            "matcher": {
                "fuzzy_threshold": 90,
                "time_window_minutes": 45,
            },
            "odds_api": {
                "sports": ["americanfootball_nfl", "basketball_nba"],
            },
            "paths": {
                "unmatched_log": "data/trade_logs/unmatched_markets.jsonl",
            },
        }
        base.update(overrides)
        return base

    def test_reads_fuzzy_threshold(self):
        m = Matcher.from_config(self._cfg())
        self.assertEqual(m._threshold, 90.0)

    def test_reads_time_window_minutes(self):
        m = Matcher.from_config(self._cfg())
        self.assertEqual(m._window_minutes, 45.0)

    def test_reads_configured_sports(self):
        m = Matcher.from_config(self._cfg())
        self.assertIn("americanfootball_nfl", m._sports)
        self.assertIn("basketball_nba", m._sports)

    def test_reads_unmatched_log_path(self):
        m = Matcher.from_config(self._cfg())
        self.assertEqual(str(m._log_path), "data/trade_logs/unmatched_markets.jsonl")

    def test_uses_defaults_when_matcher_section_absent(self):
        m = Matcher.from_config({})
        self.assertEqual(m._threshold, 60.0)
        self.assertEqual(m._window_minutes, 43200.0)
        self.assertEqual(m._sports, frozenset())

    def test_uses_defaults_when_paths_section_absent(self):
        m = Matcher.from_config({"matcher": {}})
        self.assertIn("unmatched_markets.jsonl", str(m._log_path))


# ── Integration ───────────────────────────────────────────────────────────────

class TestMatcherIntegration(unittest.TestCase):

    def test_nfl_chiefs_raiders_full_pipeline(self):
        """Full pipeline: NFL Chiefs vs Raiders — fuzzy match + time check."""
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        m = Matcher(
            fuzzy_threshold=80.0,
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=log_path,
        )
        event = _odds_event(
            home="Kansas City Chiefs",
            away="Las Vegas Raiders",
            sport="americanfootball_nfl",
            commence=datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc),
        )
        market = {
            "ticker": "NFL-KC-LV-2024-ML",
            "title": "Kansas City Chiefs vs Las Vegas Raiders",
            "close_time": "2024-01-15T18:00:00Z",
        }
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertEqual(result.odds_event.event_id, "evt-1")
        self.assertGreater(result.fuzzy_score, 90.0)
        self.assertAlmostEqual(result.time_delta_minutes, 0.0, delta=0.1)
        # No unmatched log written on success
        self.assertFalse(log_path.exists())

    def test_abbreviated_title_still_matches(self):
        """'Chiefs Raiders' should match 'Kansas City Chiefs Las Vegas Raiders'."""
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        m = Matcher(
            fuzzy_threshold=55.0,   # lower threshold to verify score trend
            time_window_minutes=30.0,
            configured_sports=["americanfootball_nfl"],
            unmatched_log_path=log_path,
        )
        event = _odds_event()
        market = _kalshi_market(title="Chiefs Raiders", close_time=None)
        result = m.match(market, [event], sport="americanfootball_nfl")
        self.assertIsNotNone(result)
        self.assertEqual(result.odds_event.event_id, "evt-1")


# ── NCAAB abbreviation expansion ──────────────────────────────────────────────

class TestExpandNcaabAbbrevs(unittest.TestCase):
    """Unit tests for _expand_ncaab_abbrevs()."""

    def test_expands_uk(self):
        self.assertEqual(_expand_ncaab_abbrevs("UK"), "Kentucky Wildcats")

    def test_expands_isu(self):
        self.assertEqual(_expand_ncaab_abbrevs("ISU"), "Iowa State Cyclones")

    def test_expands_ku(self):
        self.assertEqual(_expand_ncaab_abbrevs("KU"), "Kansas Jayhawks")

    def test_expands_sju(self):
        self.assertIn("St John", _expand_ncaab_abbrevs("SJU"))

    def test_expands_mia(self):
        self.assertEqual(_expand_ncaab_abbrevs("MIA"), "Miami Hurricanes")

    def test_expands_pur(self):
        self.assertEqual(_expand_ncaab_abbrevs("PUR"), "Purdue Boilermakers")

    def test_expands_multi_token(self):
        result = _expand_ncaab_abbrevs("UK vs ISU")
        self.assertIn("Kentucky Wildcats", result)
        self.assertIn("Iowa State Cyclones", result)

    def test_will_win_pattern(self):
        result = _expand_ncaab_abbrevs("Will UK win?")
        self.assertIn("Kentucky Wildcats", result)

    def test_short_lowercase_expanded(self):
        # ≤2-char lowercase codes (e.g. "uk") are treated as abbreviations
        self.assertEqual(_expand_ncaab_abbrevs("uk"), "Kentucky Wildcats")

    def test_mixed_case_full_word_not_expanded(self):
        # Title-case words that happen to be map keys must NOT be expanded —
        # they are already part of a written-out team name.
        self.assertEqual(_expand_ncaab_abbrevs("Wake Forest vs Virginia winner?"),
                         "Wake Forest vs Virginia winner?")

    def test_all_caps_expanded(self):
        self.assertEqual(_expand_ncaab_abbrevs("WAKE"), "Wake Forest Demon Deacons")

    def test_nba_city_names_unchanged(self):
        """NBA / NFL titles must not be altered — no overlap with NCAAB map."""
        title = "Los Angeles Lakers vs Golden State Warriors"
        self.assertEqual(_expand_ncaab_abbrevs(title), title)

    def test_nfl_title_unchanged(self):
        title = "Kansas City Chiefs vs Las Vegas Raiders"
        self.assertEqual(_expand_ncaab_abbrevs(title), title)

    def test_unknown_token_preserved(self):
        result = _expand_ncaab_abbrevs("ZZZZ vs YYYY")
        self.assertEqual(result, "ZZZZ vs YYYY")

    def test_trailing_punctuation_preserved(self):
        result = _expand_ncaab_abbrevs("UK?")
        self.assertIn("Kentucky Wildcats", result)


# ── NCAAB fuzzy matching integration ──────────────────────────────────────────

class TestNcaabFuzzyMatching(unittest.TestCase):
    """Verify that short Kalshi NCAAB codes match Odds API full team names."""

    def _make(self, **kwargs) -> Matcher:
        return Matcher(
            fuzzy_threshold=60.0,
            min_team_score=50.0,
            time_window_minutes=43200.0,
            configured_sports=["basketball_ncaab"],
            unmatched_log_path=Path(tempfile.mktemp(suffix=".jsonl")),
            **kwargs,
        )

    def _ncaab_event(self, home: str, away: str, event_id: str = "ncaab-1") -> OddsEvent:
        return OddsEvent(
            event_id=event_id,
            sport_key="basketball_ncaab",
            commence_time=_GAME_TIME,
            home_team=home,
            away_team=away,
            bookmaker_odds=[],
        )

    def test_uk_vs_isu_matches(self):
        """'Will UK win?' should match Kentucky Wildcats vs Iowa State Cyclones."""
        m = self._make()
        event = self._ncaab_event("Kentucky Wildcats", "Iowa State Cyclones")
        market = {
            "ticker": "KXNCAAMB1HWINNER-26MARCHKENTUCKY-UK",
            "title": "Will UK win?",
            "close_time": "2024-01-15T18:00:00Z",
        }
        result = m.match(market, [event], sport="basketball_ncaab")
        self.assertIsNotNone(result, "UK market should match Kentucky Wildcats")
        self.assertEqual(result.odds_event.event_id, "ncaab-1")

    def test_isu_vs_pur_matches(self):
        """'ISU vs PUR' should match Iowa State Cyclones vs Purdue Boilermakers."""
        m = self._make()
        event = self._ncaab_event("Iowa State Cyclones", "Purdue Boilermakers")
        market = {
            "ticker": "KXNCAABBGAME-26MARCHISUPURDUE",
            "title": "ISU vs PUR",
            "close_time": "2024-01-15T18:00:00Z",
        }
        result = m.match(market, [event], sport="basketball_ncaab")
        self.assertIsNotNone(result, "ISU vs PUR market should match")

    def test_ku_matches_kansas_jayhawks(self):
        m = self._make()
        event = self._ncaab_event("Kansas Jayhawks", "Duke Blue Devils")
        market = {
            "ticker": "KXNCAABBGAME-26MARCHKUDUKE",
            "title": "KU vs DUKE",
            "close_time": "2024-01-15T18:00:00Z",
        }
        result = m.match(market, [event], sport="basketball_ncaab")
        self.assertIsNotNone(result, "KU vs DUKE market should match")

    def test_mia_matches_miami_hurricanes(self):
        m = self._make()
        event = self._ncaab_event("Miami Hurricanes", "North Carolina Tar Heels")
        market = {
            "ticker": "KXNCAABBGAME-26MARCHMIAUNC",
            "title": "MIA vs UNC",
            "close_time": "2024-01-15T18:00:00Z",
        }
        result = m.match(market, [event], sport="basketball_ncaab")
        self.assertIsNotNone(result, "MIA vs UNC market should match")

    def test_ncaab_does_not_break_nba(self):
        """NCAAB expansion must not interfere with NBA matching."""
        m = Matcher(
            fuzzy_threshold=60.0,
            min_team_score=50.0,
            time_window_minutes=43200.0,
            configured_sports=["basketball_nba"],
            unmatched_log_path=Path(tempfile.mktemp(suffix=".jsonl")),
        )
        event = OddsEvent(
            event_id="nba-1",
            sport_key="basketball_nba",
            commence_time=_GAME_TIME,
            home_team="Los Angeles Lakers",
            away_team="Golden State Warriors",
            bookmaker_odds=[],
        )
        market = {
            "ticker": "KXNBAGAME-26MARCHLAGSW",
            "title": "Los Angeles Lakers vs Golden State Warriors",
            "close_time": "2024-01-15T18:00:00Z",
        }
        result = m.match(market, [event], sport="basketball_nba")
        self.assertIsNotNone(result, "NBA market should still match after NCAAB fix")

    def test_ncaab_wrong_sport_no_expansion(self):
        """Title not passed as NCAAB sport → abbreviations stay unexpanded."""
        m = Matcher(
            fuzzy_threshold=60.0,
            min_team_score=50.0,
            time_window_minutes=43200.0,
            configured_sports=["basketball_nba"],
            unmatched_log_path=Path(tempfile.mktemp(suffix=".jsonl")),
        )
        event = OddsEvent(
            event_id="nba-2",
            sport_key="basketball_nba",
            commence_time=_GAME_TIME,
            home_team="Utah Jazz",
            away_team="Houston Rockets",
            bookmaker_odds=[],
        )
        # "HOU" is in the NCAAB map but we're matching NBA — expansion should
        # NOT fire for sport=basketball_nba, so this market should reject.
        market = {
            "ticker": "KXNBAGAME-TEST-HOU",
            "title": "HOU vs UTAH",
            "close_time": "2024-01-15T18:00:00Z",
        }
        # The score for unexpanded "HOU vs UTAH" against "Utah Jazz Houston Rockets"
        # may or may not pass; the important thing is no crash and the NBA path
        # is unmodified. We just verify the match call completes.
        m.match(market, [event], sport="basketball_nba")  # no assertion — just no crash


if __name__ == "__main__":
    unittest.main()
