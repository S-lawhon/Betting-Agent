"""Tests for src/kalshi_live_discovery.py — Kalshi market discovery and matching."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock
from src.kalshi_live_discovery import (
    KalshiLiveDiscovery,
    KalshiMarketMatch,
    _normalize_title,
    SPORT_SERIES,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_market(
    ticker="KXNBAGAME-26MAR28-LAL",
    title="Will the Los Angeles Lakers win?",
    yes_bid=55,
    yes_ask=60,
    volume=1000,
    status="open",
):
    return {
        "ticker": ticker,
        "title": title,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "volume": volume,
        "status": status,
    }


def _make_client(markets=None, market_single=None):
    """Build a mock Kalshi client."""
    client = MagicMock()
    if markets is None:
        markets = [_make_market()]
    client.list_markets.return_value = {"markets": markets}
    if market_single:
        client.get_market.return_value = {"market": market_single}
    else:
        client.get_market.return_value = {"market": markets[0] if markets else {}}
    client.get_orderbook.return_value = {"yes": [], "no": []}
    return client


def _make_discovery(client=None, **kwargs):
    if client is None:
        client = _make_client()
    now_time = [1000.0]

    def _now():
        return now_time[0]

    disc = KalshiLiveDiscovery(client, _now_fn=_now, **kwargs)
    disc._advance_time = lambda secs: now_time.__setitem__(0, now_time[0] + secs)
    disc._now_time = now_time
    return disc


# ── KalshiMarketMatch properties ─────────────────────────────────────

class TestKalshiMarketMatch:
    def test_mid_price(self):
        m = KalshiMarketMatch(
            game_id="g1", ticker="T", title="X", yes_side="home",
            home_team="A", away_team="B", yes_bid=50, yes_ask=60,
            volume=100, fuzzy_score=80.0, market_type="game_winner",
        )
        assert m.mid_price == pytest.approx(0.55)

    def test_mid_price_zero_bid_ask(self):
        m = KalshiMarketMatch(
            game_id="g1", ticker="T", title="X", yes_side="home",
            home_team="A", away_team="B", yes_bid=0, yes_ask=0,
            volume=100, fuzzy_score=80.0, market_type="game_winner",
        )
        assert m.mid_price == 0.0

    def test_home_yes_prob_when_yes_is_home(self):
        m = KalshiMarketMatch(
            game_id="g1", ticker="T", title="X", yes_side="home",
            home_team="A", away_team="B", yes_bid=60, yes_ask=70,
            volume=100, fuzzy_score=80.0, market_type="game_winner",
        )
        assert m.home_yes_prob == pytest.approx(0.65)
        assert m.away_yes_prob == pytest.approx(0.35)

    def test_home_yes_prob_when_yes_is_away(self):
        m = KalshiMarketMatch(
            game_id="g1", ticker="T", title="X", yes_side="away",
            home_team="A", away_team="B", yes_bid=60, yes_ask=70,
            volume=100, fuzzy_score=80.0, market_type="game_winner",
        )
        # mid = 0.65, but YES resolves to away, so home_yes_prob = 1 - 0.65
        assert m.home_yes_prob == pytest.approx(0.35)
        assert m.away_yes_prob == pytest.approx(0.65)

    def test_spread_cents(self):
        m = KalshiMarketMatch(
            game_id="g1", ticker="T", title="X", yes_side="home",
            home_team="A", away_team="B", yes_bid=50, yes_ask=60,
            volume=100, fuzzy_score=80.0, market_type="game_winner",
        )
        assert m.spread_cents == 10


# ── Sport series config ──────────────────────────────────────────────

class TestSportSeries:
    def test_nba_has_series(self):
        assert "basketball_nba" in SPORT_SERIES
        assert "KXNBAGAME" in SPORT_SERIES["basketball_nba"]

    def test_mlb_has_series(self):
        assert "baseball_mlb" in SPORT_SERIES
        assert "KXMLBGAME" in SPORT_SERIES["baseball_mlb"]


# ── Title normalization ──────────────────────────────────────────────

class TestNormalizeTitle:
    def test_strips_noise_words(self):
        result = _normalize_title("Will the Lakers win at home?")
        assert "winner" not in result.lower()
        assert "at" not in result.lower().split()

    def test_keeps_team_names(self):
        result = _normalize_title("Lakers vs. Celtics")
        assert "lakers" in result.lower()
        assert "celtics" in result.lower()


# ── Market type inference ────────────────────────────────────────────

class TestMarketTypeInference:
    def test_game_winner(self):
        disc = _make_discovery()
        assert disc._infer_market_type("KXNBAGAME-26MAR28-LAL", "basketball_nba") == "game_winner"

    def test_first_half_winner(self):
        disc = _make_discovery()
        assert disc._infer_market_type("KXNBA1HWINNER-26MAR28-LAL", "basketball_nba") == "1h_winner"

    def test_second_half_winner(self):
        disc = _make_discovery()
        assert disc._infer_market_type("KXNBA2HWINNER-26MAR28-LAL", "basketball_nba") == "2h_winner"

    def test_unknown_type(self):
        disc = _make_discovery()
        assert disc._infer_market_type("KXNBASPECIAL-LAL", "basketball_nba") == "unknown"


# ── YES-side inference ───────────────────────────────────────────────

class TestYesSideInference:
    def test_will_win_pattern_home(self):
        disc = _make_discovery()
        market = _make_market(title="Will the Los Angeles Lakers win?")
        result = disc._infer_yes_side(market, "Los Angeles Lakers", "Boston Celtics")
        assert result == "home"

    def test_will_win_pattern_away(self):
        disc = _make_discovery()
        market = _make_market(title="Will the Boston Celtics win?")
        result = disc._infer_yes_side(market, "Los Angeles Lakers", "Boston Celtics")
        assert result == "away"

    def test_tie_suffix_skipped(self):
        disc = _make_discovery()
        market = _make_market(ticker="KXNBAGAME-26MAR28-TIE", title="Will there be a tie?")
        result = disc._infer_yes_side(market, "Lakers", "Celtics")
        assert result is None

    def test_ticker_suffix_fallback(self):
        disc = _make_discovery()
        market = _make_market(
            ticker="KXNBAGAME-26MAR28-LAKERS",
            title="NBA Game March 28",
        )
        result = disc._infer_yes_side(market, "Los Angeles Lakers", "Boston Celtics")
        # ticker suffix "LAKERS" should match home
        assert result == "home"

    def test_both_teams_in_title_defaults_home(self):
        disc = _make_discovery()
        market = _make_market(
            ticker="KXNBAGAME-26MAR28",
            title="Lakers Celtics Game",
        )
        result = disc._infer_yes_side(market, "Los Angeles Lakers", "Boston Celtics")
        # Both teams present, no "Will X win?" → defaults to home
        assert result == "home"


# ── find_market ──────────────────────────────────────────────────────

class TestFindMarket:
    def test_finds_matching_market(self):
        market = _make_market(
            ticker="KXNBAGAME-26MAR28-LAL",
            title="Will the Los Angeles Lakers beat the Boston Celtics?",
        )
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client, fuzzy_threshold=50.0)

        result = disc.find_market(
            "basketball_nba", "Los Angeles Lakers", "Boston Celtics", "game_1"
        )
        assert result is not None
        assert result.ticker == "KXNBAGAME-26MAR28-LAL"
        assert result.yes_side == "home"
        assert result.game_id == "game_1"

    def test_returns_none_for_no_match(self):
        market = _make_market(
            ticker="KXNBAGAME-26MAR28-MIA",
            title="Will the Miami Heat win?",
        )
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client, fuzzy_threshold=80.0)

        result = disc.find_market(
            "basketball_nba", "Phoenix Suns", "Milwaukee Bucks", "game_2"
        )
        assert result is None

    def test_uses_cache_on_second_call(self):
        market = _make_market(title="Will the Los Angeles Lakers win?")
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client)

        disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")
        disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")

        # list_markets should be called once (for the initial fetch)
        # Second call uses match cache → price refresh via get_market
        assert client.list_markets.call_count == len(SPORT_SERIES.get("basketball_nba", []))

    def test_cache_refreshes_after_ttl(self):
        market = _make_market(title="Will the Los Angeles Lakers win?")
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client, cache_ttl_seconds=30.0)

        disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")
        initial_calls = client.list_markets.call_count

        # Clear match cache to force market re-fetch, advance past TTL
        disc._match_cache.clear()
        disc._now_time[0] += 60  # past 30s TTL
        disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")

        assert client.list_markets.call_count > initial_calls

    def test_filters_low_volume(self):
        market = _make_market(
            title="Will the Los Angeles Lakers win?",
            volume=5,
        )
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client, min_volume=100)

        result = disc.find_market(
            "basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1"
        )
        assert result is None

    def test_skips_non_game_winner_markets(self):
        """Phase 2 only looks at game_winner markets."""
        market = _make_market(
            ticker="KXNBA1HWINNER-26MAR28-LAL",
            title="Will the Los Angeles Lakers win the first half?",
        )
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client)

        result = disc.find_market(
            "basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1"
        )
        assert result is None

    def test_unsupported_sport_returns_none(self):
        client = _make_client()
        disc = _make_discovery(client=client)
        result = disc.find_market("hockey_nhl", "Rangers", "Bruins", "g1")
        assert result is None

    def test_clear_cache(self):
        client = _make_client()
        disc = _make_discovery(client=client)
        disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")
        assert len(disc._match_cache) > 0
        disc.clear_cache()
        assert len(disc._match_cache) == 0
        assert len(disc._market_cache) == 0


# ── Orderbook ────────────────────────────────────────────────────────

class TestOrderbook:
    def test_get_orderbook(self):
        client = _make_client()
        client.get_orderbook.return_value = {"yes": [{"price": 55}], "no": []}
        disc = _make_discovery(client=client)
        ob = disc.get_orderbook("KXNBAGAME-26MAR28-LAL")
        assert ob is not None
        assert "yes" in ob

    def test_orderbook_error_returns_none(self):
        client = _make_client()
        client.get_orderbook.side_effect = Exception("API error")
        disc = _make_discovery(client=client)
        assert disc.get_orderbook("BAD-TICKER") is None


# ── API error handling ───────────────────────────────────────────────

class TestErrorHandling:
    def test_api_error_returns_empty_markets(self):
        client = _make_client()
        client.list_markets.side_effect = Exception("Connection refused")
        disc = _make_discovery(client=client)
        result = disc.find_market("basketball_nba", "Lakers", "Celtics", "g1")
        assert result is None

    def test_price_refresh_error_returns_cached(self):
        market = _make_market(title="Will the Los Angeles Lakers beat the Boston Celtics?")
        client = _make_client(markets=[market])
        disc = _make_discovery(client=client, fuzzy_threshold=50.0)

        # First call caches the match
        match = disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")
        assert match is not None

        # Make get_market fail for refresh
        client.get_market.side_effect = Exception("timeout")
        # Fetching again uses match cache → tries refresh → fails
        result = disc.find_market("basketball_nba", "Los Angeles Lakers", "Boston Celtics", "g1")
        # When refresh fails, _refresh_price returns None, so cached match returns as-is (the code returns cached)
        # Looking at code: if refreshed is None it falls through and returns `cached` directly
        assert result is not None
