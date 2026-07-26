"""Regression tests for the P-001 CLV capture path.

Covers the three defects found by the 2026-07-26 capture diagnostic
(`research/REPORT_CLV_Capture_2026-07.md`):

1. `close_fair()` had five indistinguishable `return None` paths, so a capture
   miss could not be attributed. It now returns (result, reason).
2. The settlement job took team names from the row's free-text `event` field,
   which names the Odds API game the pod *priced* — not always the market it
   traded. Names now come from the ticker.
3. The Kalshi→Odds API matcher broke fuzzy-score ties by list order. Every game
   of an MLB series scores identically, so the pod routinely priced one game
   and placed the bet on another day's market (528 of 671 settled MLB bets).
   Ties are now broken by start-time proximity, and a ticker-derived start
   gets a tight window.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent /
                       "Legacy" / "Kalshi Arb Project" / "src"))

from src.clv_close import close_fair, norm  # noqa: E402
from src.mlb_teams import teams_from_mlb_ticker  # noqa: E402

UTC = timezone.utc


# ── ticker → team names ───────────────────────────────────────────────────────

class TestTeamsFromTicker(unittest.TestCase):

    def test_three_letter_pair(self):
        self.assertEqual(teams_from_mlb_ticker("KXMLBGAME-26JUL201910BALBOS-BOS"),
                         ("Baltimore Orioles", "Boston Red Sox"))

    def test_two_letter_code(self):
        # KC is two characters; SD, SF, TB and AZ are the other short codes.
        self.assertEqual(teams_from_mlb_ticker("KXMLBGAME-26APR071810KCCLE-KC"),
                         ("Kansas City Royals", "Cleveland Guardians"))

    def test_doubleheader_suffix_is_stripped(self):
        self.assertEqual(teams_from_mlb_ticker("KXMLBGAME-26JUL071945MILSTLG2-STL"),
                         ("Milwaukee Brewers", "St. Louis Cardinals"))

    def test_non_mlb_ticker_returns_none(self):
        self.assertIsNone(teams_from_mlb_ticker("KXNBAGAME-26APR03UTAHOU-UTA"))

    def test_ticker_beats_a_mislabelled_event_field(self):
        """The row that motivated the fix: ticker BAL/BOS, event named Houston.

        The event string can never match a snapshot, but the ticker can.
        """
        ticker = "KXMLBGAME-26JUL201910BALBOS-BOS"
        event_names = {norm(x) for x in
                       "Houston Astros vs Baltimore Orioles".split(" vs ")}
        away, home = teams_from_mlb_ticker(ticker)
        self.assertNotEqual(event_names, {norm(away), norm(home)})
        self.assertEqual({norm(away), norm(home)},
                         {norm("Baltimore Orioles"), norm("Boston Red Sox")})


# ── close_fair reason tags ────────────────────────────────────────────────────

def _snapshot(events):
    return {"data": events}


def _event(home, away, commence, books=None):
    return {"home_team": home, "away_team": away,
            "commence_time": commence, "bookmakers": books or []}


def _pinnacle(home, away, home_price=-120, away_price=100):
    return [{"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
        {"name": home, "price": home_price},
        {"name": away, "price": away_price}]}]}]


class TestCloseFairReasons(unittest.TestCase):

    expected = datetime(2026, 7, 20, 23, 10, tzinfo=UTC)
    names = [norm("Baltimore Orioles"), norm("Boston Red Sox")]

    def _run(self, fetch):
        return close_fair(self.expected, self.names, "KEY", fetch=fetch)

    def test_ok(self):
        ev = _event("Boston Red Sox", "Baltimore Orioles",
                    "2026-07-20T23:11:00Z",
                    _pinnacle("Boston Red Sox", "Baltimore Orioles"))
        res, reason = self._run(lambda url: _snapshot([ev]))
        self.assertEqual(reason, "OK")
        self.assertEqual(res[0], "Boston Red Sox")
        self.assertTrue(0.0 < res[2] < 1.0)

    def test_api_error(self):
        def boom(url):
            raise OSError("connection reset")
        self.assertEqual(self._run(boom)[1], "API_ERROR")

    def test_snapshot_empty(self):
        self.assertEqual(self._run(lambda url: _snapshot([]))[1], "SNAPSHOT_EMPTY")

    def test_no_team_match(self):
        ev = _event("New York Yankees", "Toronto Blue Jays", "2026-07-20T23:11:00Z")
        self.assertEqual(self._run(lambda url: _snapshot([ev]))[1], "NO_TEAM_MATCH")

    def test_time_gt_3h(self):
        ev = _event("Boston Red Sox", "Baltimore Orioles", "2026-07-21T17:11:00Z",
                    _pinnacle("Boston Red Sox", "Baltimore Orioles"))
        self.assertEqual(self._run(lambda url: _snapshot([ev]))[1], "TIME_GT_3H")

    def test_no_pinnacle(self):
        ev = _event("Boston Red Sox", "Baltimore Orioles", "2026-07-20T23:11:00Z",
                    [{"key": "betfair", "markets": []}])
        self.assertEqual(self._run(lambda url: _snapshot([ev]))[1], "NO_PINNACLE")

    def test_pinn_no_h2h(self):
        ev = _event("Boston Red Sox", "Baltimore Orioles", "2026-07-20T23:11:00Z",
                    [{"key": "pinnacle", "markets": [{"key": "totals",
                                                      "outcomes": []}]}])
        self.assertEqual(self._run(lambda url: _snapshot([ev]))[1], "PINN_NO_H2H")

    def test_pinn_name_mismatch(self):
        ev = _event("Boston Red Sox", "Baltimore Orioles", "2026-07-20T23:11:00Z",
                    _pinnacle("Red Sox", "Orioles"))
        self.assertEqual(self._run(lambda url: _snapshot([ev]))[1],
                         "PINN_NAME_MISMATCH")

    def test_nearest_of_several_candidates_is_used(self):
        near = _event("Boston Red Sox", "Baltimore Orioles", "2026-07-20T23:11:00Z",
                      _pinnacle("Boston Red Sox", "Baltimore Orioles"))
        far = _event("Boston Red Sox", "Baltimore Orioles", "2026-07-21T23:11:00Z",
                     _pinnacle("Boston Red Sox", "Baltimore Orioles"))
        res, reason = self._run(lambda url: _snapshot([far, near]))
        self.assertEqual(reason, "OK")
        self.assertEqual(res[3], "2026-07-20T23:11:00Z")


# ── matcher: ties broken by start time, not list order ───────────────────────

class TestMatcherSeriesDisambiguation(unittest.TestCase):
    """The bug that made 84% of P-001's MLB CLV rows meaningless.

    Every game of an MLB series has the same two team names, so all of them
    tie on fuzzy score. The old loop kept the first maximal event.
    """

    def _events(self):
        from odds_client import OddsEvent
        # Friday and Saturday of the same Guardians @ Rays series.
        return [
            OddsEvent(event_id="fri", sport_key="baseball_mlb",
                      commence_time=datetime(2026, 7, 25, 22, 11, tzinfo=UTC),
                      home_team="Tampa Bay Rays", away_team="Cleveland Guardians",
                      bookmaker_odds=[]),
            OddsEvent(event_id="sat", sport_key="baseball_mlb",
                      commence_time=datetime(2026, 7, 26, 16, 15, tzinfo=UTC),
                      home_team="Tampa Bay Rays", away_team="Cleveland Guardians",
                      bookmaker_odds=[]),
        ]

    def _matcher(self, **kw):
        import tempfile
        from matcher import Matcher
        kw.setdefault("fuzzy_threshold", 60.0)
        kw.setdefault("configured_sports", ["baseball_mlb"])
        kw.setdefault("unmatched_log_path",
                      Path(tempfile.mkdtemp()) / "unmatched.jsonl")
        return Matcher(**kw)

    def _market(self, ticker):
        return {"ticker": ticker, "title": "Cleveland vs Tampa Bay Winner?",
                # close_time is the market's CLOSING stamp, hours after the
                # first pitch — deliberately unhelpful, as in production.
                "close_time": "2026-07-26T18:19:38Z"}

    def test_saturday_ticker_matches_saturday_game(self):
        m = self._matcher()
        res = m.match(self._market("KXMLBGAME-26JUL261215CLETB-TB"),
                      self._events(), sport="baseball_mlb")
        self.assertIsNotNone(res)
        self.assertEqual(res.odds_event.event_id, "sat")

    def test_friday_ticker_matches_friday_game(self):
        """Fails on the old first-wins tie-break: 'fri' is listed first only by
        luck, so flip the list to prove selection is by time, not order."""
        m = self._matcher()
        res = m.match(self._market("KXMLBGAME-26JUL251810CLETB-TB"),
                      list(reversed(self._events())), sport="baseball_mlb")
        self.assertIsNotNone(res)
        self.assertEqual(res.odds_event.event_id, "fri")

    def test_wrong_day_only_candidate_is_rejected(self):
        """When the correct day's event is absent, matching the neighbouring
        game is worse than no match: the pod would price one game and trade
        another. The ticker-derived window (default 12h) rejects it."""
        from odds_client import OddsEvent
        only_friday = [OddsEvent(
            event_id="fri", sport_key="baseball_mlb",
            commence_time=datetime(2026, 7, 25, 22, 11, tzinfo=UTC),
            home_team="Tampa Bay Rays", away_team="Cleveland Guardians",
            bookmaker_odds=[])]
        m = self._matcher()
        res = m.match(self._market("KXMLBGAME-26JUL261215CLETB-TB"),
                      only_friday, sport="baseball_mlb")
        self.assertIsNone(res)

    def test_doubleheader_still_matches(self):
        """The tight window must not reject a legitimate same-day second game."""
        from odds_client import OddsEvent
        events = [OddsEvent(
            event_id="g2", sport_key="baseball_mlb",
            commence_time=datetime(2026, 7, 26, 20, 15, tzinfo=UTC),
            home_team="Tampa Bay Rays", away_team="Cleveland Guardians",
            bookmaker_odds=[])]
        m = self._matcher()
        res = m.match(self._market("KXMLBGAME-26JUL261615CLETBG2-TB"),
                      events, sport="baseball_mlb")
        self.assertIsNotNone(res)

    def test_date_only_ticker_keeps_the_loose_window(self):
        """NBA/NHL/tennis tickers carry no time, so they must keep falling back
        to close_time and the legacy wide window — no behaviour change."""
        from odds_client import OddsEvent
        events = [OddsEvent(
            event_id="nba", sport_key="basketball_nba",
            commence_time=datetime(2026, 4, 3, 23, 40, tzinfo=UTC),
            home_team="Houston Rockets", away_team="Utah Jazz",
            bookmaker_odds=[])]
        m = self._matcher(configured_sports=["basketball_nba"],
                          time_window_minutes=43200.0)
        res = m.match({"ticker": "KXNBAGAME-26APR03UTAHOU-UTA",
                       "title": "Utah Jazz vs Houston Rockets Winner?",
                       "close_time": "2026-04-04T03:00:00Z"},
                      events, sport="basketball_nba")
        self.assertIsNotNone(res)


if __name__ == "__main__":
    unittest.main()
