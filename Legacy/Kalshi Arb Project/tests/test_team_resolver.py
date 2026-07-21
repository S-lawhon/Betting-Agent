"""
tests/test_team_resolver.py
───────────────────────────
Unit tests for the NBA team name/abbreviation resolver.
"""

from __future__ import annotations

import unittest

from src.team_resolver import (
    NBA_EAST,
    NBA_TEAMS,
    NBA_TEAMS_REVERSE,
    NBA_WEST,
    abbrev_to_full,
    get_conference,
    normalize_team,
    resolve_team,
    resolve_team_from_ticker,
    resolve_team_from_title,
)


class TestNBATeamData(unittest.TestCase):
    """Verify the static team data is complete and consistent."""

    def test_30_teams(self):
        self.assertEqual(len(NBA_TEAMS), 30)

    def test_reverse_map_size(self):
        self.assertEqual(len(NBA_TEAMS_REVERSE), 30)

    def test_east_west_partition(self):
        """East + West should cover all 30 teams with no overlap."""
        self.assertEqual(len(NBA_EAST), 15)
        self.assertEqual(len(NBA_WEST), 15)
        self.assertEqual(NBA_EAST | NBA_WEST, set(NBA_TEAMS.keys()))
        self.assertEqual(NBA_EAST & NBA_WEST, set())

    def test_reverse_roundtrip(self):
        for abbrev, full_name in NBA_TEAMS.items():
            self.assertEqual(NBA_TEAMS_REVERSE[full_name], abbrev)


class TestResolveFromTicker(unittest.TestCase):
    """Extract team abbreviation from Kalshi market tickers."""

    def test_playoff_ticker(self):
        self.assertEqual(resolve_team_from_ticker("KXNBAPLAYOFF-26-BOS"), "BOS")

    def test_championship_ticker(self):
        self.assertEqual(resolve_team_from_ticker("KXNBA-26-LAL"), "LAL")

    def test_conference_east_ticker(self):
        self.assertEqual(resolve_team_from_ticker("KXNBAEAST-26-MIL"), "MIL")

    def test_conference_west_ticker(self):
        self.assertEqual(resolve_team_from_ticker("KXNBAWEST-26-DEN"), "DEN")

    def test_game_ticker(self):
        self.assertEqual(resolve_team_from_ticker("KXNBAGAME-26FEB23BOSKNK-BOS"), "BOS")

    def test_game_ticker_away_team(self):
        self.assertEqual(resolve_team_from_ticker("KXNBAGAME-26FEB23BOSKNK-NYK"), "NYK")

    def test_alternate_abbrev_in_ticker(self):
        """Kalshi might use GS instead of GSW — should normalize."""
        # Fallback to last-hyphen-segment parsing
        self.assertEqual(resolve_team_from_ticker("KXNBAPLAYOFF-26-GSW"), "GSW")

    def test_unknown_ticker_returns_none(self):
        self.assertIsNone(resolve_team_from_ticker("KXSOMETHINGELSE"))

    def test_empty_ticker(self):
        self.assertIsNone(resolve_team_from_ticker(""))

    def test_ncaab_ticker_returns_none(self):
        """NCAAB tickers won't match NBA lookup — returns None."""
        result = resolve_team_from_ticker("KXNCAAMBGAME-26FEB23LOUUNC-UNC")
        # UNC is not an NBA team
        self.assertIsNone(result)


class TestResolveFromTitle(unittest.TestCase):
    """Extract team from Kalshi market titles."""

    def test_will_win_championship(self):
        self.assertEqual(
            resolve_team_from_title("Will Boston Celtics win the NBA Championship?"),
            "BOS",
        )

    def test_will_win_with_article(self):
        self.assertEqual(
            resolve_team_from_title("Will the Lakers make the playoffs?"),
            "LAL",
        )

    def test_will_win_conference(self):
        self.assertEqual(
            resolve_team_from_title("Will Milwaukee Bucks win the Eastern Conference?"),
            "MIL",
        )

    def test_team_name_in_title_no_pattern(self):
        """Even without 'Will X win?' pattern, should find team by substring."""
        self.assertEqual(
            resolve_team_from_title("Golden State Warriors playoff qualifier"),
            "GSW",
        )

    def test_nickname_match(self):
        self.assertEqual(resolve_team_from_title("Will the Celtics win?"), "BOS")

    def test_unknown_title_returns_none(self):
        self.assertIsNone(resolve_team_from_title("Some random market title"))

    def test_empty_title(self):
        self.assertIsNone(resolve_team_from_title(""))


class TestResolveTeam(unittest.TestCase):
    """Combined ticker + title resolution."""

    def test_ticker_preferred_over_title(self):
        """Ticker is more reliable; should be used when available."""
        result = resolve_team(
            ticker="KXNBA-26-BOS",
            title="Will Los Angeles Lakers win the NBA Championship?",
        )
        # Ticker says BOS, title says LAL — ticker wins
        self.assertEqual(result, "BOS")

    def test_falls_back_to_title(self):
        result = resolve_team(
            ticker="UNKNOWN-TICKER",
            title="Will Boston Celtics win?",
        )
        self.assertEqual(result, "BOS")

    def test_both_unresolvable(self):
        result = resolve_team(
            ticker="SOMETHING",
            title="random text",
        )
        self.assertIsNone(result)


class TestNormalizeTeam(unittest.TestCase):
    """Normalize various team name formats to canonical abbreviation."""

    def test_full_name(self):
        self.assertEqual(normalize_team("Boston Celtics"), "BOS")

    def test_abbreviation(self):
        self.assertEqual(normalize_team("BOS"), "BOS")

    def test_alternate_abbreviation_gs(self):
        self.assertEqual(normalize_team("GS"), "GSW")

    def test_alternate_abbreviation_ny(self):
        self.assertEqual(normalize_team("NY"), "NYK")

    def test_nickname(self):
        self.assertEqual(normalize_team("Celtics"), "BOS")

    def test_case_insensitive(self):
        self.assertEqual(normalize_team("boston celtics"), "BOS")

    def test_seventy_sixers(self):
        self.assertEqual(normalize_team("76ers"), "PHI")

    def test_unknown_returns_none(self):
        self.assertIsNone(normalize_team("Fluxington Widgets"))

    def test_empty_string(self):
        self.assertIsNone(normalize_team(""))


class TestGetConference(unittest.TestCase):
    """Conference lookup."""

    def test_east_team(self):
        self.assertEqual(get_conference("BOS"), "east")

    def test_west_team(self):
        self.assertEqual(get_conference("LAL"), "west")

    def test_unknown_team(self):
        self.assertIsNone(get_conference("ZZZ"))


class TestAbbrevToFull(unittest.TestCase):
    """Abbreviation to full name lookup."""

    def test_valid(self):
        self.assertEqual(abbrev_to_full("BOS"), "Boston Celtics")

    def test_invalid(self):
        self.assertIsNone(abbrev_to_full("ZZZ"))


if __name__ == "__main__":
    unittest.main()
