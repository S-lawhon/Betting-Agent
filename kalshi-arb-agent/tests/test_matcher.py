"""Tests for matcher module."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.matcher import (
    MarketMatcher,
    TEAM_ALIASES,
    SPORT_KEYWORDS,
    _build_reverse_alias_map,
)
from src.odds_client import OddsClient
from src.utils import KalshiMarket, SportsbookEvent, SportsbookOdds


def make_kalshi_market(
    ticker: str = "NBA-TEST",
    title: str = "Will the Lakers win?",
    subtitle: str = "",
    yes_price: float = 0.55,
    volume: int = 200000,
    close_time: datetime | None = None,
    category: str = "",
) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title=title,
        subtitle=subtitle,
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        volume=volume,
        open_interest=1000,
        close_time=close_time or datetime(2025, 6, 15, 20, 0, tzinfo=timezone.utc),
        category=category,
    )


def make_sportsbook_event(
    home: str = "Los Angeles Lakers",
    away: str = "Boston Celtics",
    sport: str = "basketball_nba",
    commence_time: datetime | None = None,
    odds: list[SportsbookOdds] | None = None,
) -> SportsbookEvent:
    event = SportsbookEvent(
        event_id="test-123",
        sport=sport,
        home_team=home,
        away_team=away,
        commence_time=commence_time or datetime(2025, 6, 15, 19, 0, tzinfo=timezone.utc),
    )
    if odds:
        event.bookmaker_odds = odds
    else:
        event.bookmaker_odds = [
            SportsbookOdds(
                bookmaker="pinnacle",
                market_type="h2h",
                outcome_name=home,
                price=1.80,
                implied_prob=1 / 1.80,
            ),
            SportsbookOdds(
                bookmaker="pinnacle",
                market_type="h2h",
                outcome_name=away,
                price=2.10,
                implied_prob=1 / 2.10,
            ),
            SportsbookOdds(
                bookmaker="draftkings",
                market_type="h2h",
                outcome_name=home,
                price=1.85,
                implied_prob=1 / 1.85,
            ),
            SportsbookOdds(
                bookmaker="draftkings",
                market_type="h2h",
                outcome_name=away,
                price=2.05,
                implied_prob=1 / 2.05,
            ),
            SportsbookOdds(
                bookmaker="fanduel",
                market_type="h2h",
                outcome_name=home,
                price=1.82,
                implied_prob=1 / 1.82,
            ),
            SportsbookOdds(
                bookmaker="fanduel",
                market_type="h2h",
                outcome_name=away,
                price=2.08,
                implied_prob=1 / 2.08,
            ),
        ]
    return event


class TestSportDetection:
    """Tests for sport detection from market titles."""

    def setup_method(self):
        self.odds_client = OddsClient(api_key="test")
        self.matcher = MarketMatcher(self.odds_client)

    def test_detect_nba(self):
        km = make_kalshi_market(title="NBA: Lakers to win")
        sport = self.matcher._detect_sport(km)
        assert sport == "basketball_nba"

    def test_detect_nhl(self):
        km = make_kalshi_market(title="NHL: Bruins to win")
        sport = self.matcher._detect_sport(km)
        assert sport == "icehockey_nhl"

    def test_detect_pro_basketball(self):
        km = make_kalshi_market(title="Pro Basketball game tonight")
        sport = self.matcher._detect_sport(km)
        assert sport == "basketball_nba"

    def test_no_sport_detected(self):
        km = make_kalshi_market(title="Will it rain tomorrow?", category="weather")
        sport = self.matcher._detect_sport(km)
        assert sport is None


class TestMarketTypeDetection:
    """Tests for market type detection."""

    def setup_method(self):
        self.odds_client = OddsClient(api_key="test")
        self.matcher = MarketMatcher(self.odds_client)

    def test_detect_moneyline(self):
        km = make_kalshi_market(title="Will the Lakers win?")
        assert self.matcher._detect_market_type(km) == "moneyline"

    def test_detect_spread(self):
        km = make_kalshi_market(title="Lakers to cover the spread")
        assert self.matcher._detect_market_type(km) == "spread"

    def test_detect_total(self):
        km = make_kalshi_market(title="Total points over 220.5")
        assert self.matcher._detect_market_type(km) == "total"

    def test_default_to_moneyline(self):
        km = make_kalshi_market(title="Lakers game result")
        assert self.matcher._detect_market_type(km) == "moneyline"


class TestTeamExtraction:
    """Tests for team name extraction and normalization."""

    def setup_method(self):
        self.odds_client = OddsClient(api_key="test")
        self.matcher = MarketMatcher(self.odds_client)

    def test_extract_full_team_name(self):
        km = make_kalshi_market(title="Los Angeles Lakers to win vs Boston Celtics")
        teams = self.matcher._extract_teams(km)
        assert "los angeles lakers" in teams
        assert "boston celtics" in teams

    def test_extract_team_abbreviation(self):
        km = make_kalshi_market(title="OKC to beat the Celtics")
        teams = self.matcher._extract_teams(km)
        assert "oklahoma city thunder" in teams

    def test_normalize_team_name(self):
        assert self.matcher._normalize_team("Lakers") == "los angeles lakers"
        assert self.matcher._normalize_team("OKC") == "oklahoma city thunder"
        assert self.matcher._normalize_team("unknown team") == "unknown team"


class TestMarketMatching:
    """Tests for the full matching pipeline."""

    def setup_method(self):
        self.odds_client = OddsClient(api_key="test")
        self.matcher = MarketMatcher(self.odds_client, min_match_confidence=50.0)

    def test_successful_match(self):
        km = make_kalshi_market(
            title="Will the Los Angeles Lakers win?",
            subtitle="NBA Game vs Boston Celtics",
        )
        event = make_sportsbook_event()
        events = {"basketball_nba": [event]}

        matched = self.matcher.match_markets([km], events)
        assert len(matched) == 1
        assert matched[0].match_type == "moneyline"
        assert matched[0].sharp_probability > 0

    def test_no_match_wrong_sport(self):
        km = make_kalshi_market(
            ticker="NHL-TEST",
            title="NHL: Bruins to win",
        )
        event = make_sportsbook_event()  # NBA event
        events = {"basketball_nba": [event]}

        matched = self.matcher.match_markets([km], events)
        assert len(matched) == 0

    def test_no_match_no_events(self):
        km = make_kalshi_market(title="NBA: Lakers to win")
        events = {"basketball_nba": []}

        matched = self.matcher.match_markets([km], events)
        assert len(matched) == 0

    def test_date_filter(self):
        """Markets with very different dates should not match."""
        km = make_kalshi_market(
            title="Will the Lakers win?",
            subtitle="NBA",
            close_time=datetime(2025, 8, 1, 20, 0, tzinfo=timezone.utc),
        )
        event = make_sportsbook_event(
            commence_time=datetime(2025, 6, 1, 19, 0, tzinfo=timezone.utc)
        )
        events = {"basketball_nba": [event]}

        matched = self.matcher.match_markets([km], events)
        assert len(matched) == 0

    def test_unmatched_log(self):
        """Unmatched markets should be logged."""
        km = make_kalshi_market(
            ticker="POL-TEST",
            title="Random non-sports market",
        )
        events = {"basketball_nba": []}

        self.matcher.match_markets([km], events)
        assert len(self.matcher.unmatched_markets) > 0


class TestReverseAliasMap:
    """Tests for the reverse alias lookup."""

    def test_canonical_names_map_to_self(self):
        reverse = _build_reverse_alias_map()
        assert reverse["los angeles lakers"] == "los angeles lakers"

    def test_abbreviations_map_to_canonical(self):
        reverse = _build_reverse_alias_map()
        assert reverse["lal"] == "los angeles lakers"
        assert reverse["okc"] == "oklahoma city thunder"

    def test_nicknames_map_to_canonical(self):
        reverse = _build_reverse_alias_map()
        assert reverse["celtics"] == "boston celtics"
        assert reverse["thunder"] == "oklahoma city thunder"
