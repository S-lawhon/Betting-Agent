"""The Odds API client: fetches sportsbook odds with caching and rate awareness."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from src.logger_setup import get_logger
from src.utils import (
    RateLimiter,
    SportsbookEvent,
    SportsbookOdds,
    decimal_to_implied_prob,
)

logger = get_logger("odds_client")

BASE_URL = "https://api.the-odds-api.com/v4"

# Sharp bookmakers whose lines carry more weight
SHARP_BOOKS = {"pinnacle", "betonlineag", "bovada", "betcris", "bookmaker"}

# Supported sport keys mapping
SPORT_KEYS = {
    "basketball_nba": "basketball_nba",
    "icehockey_nhl": "icehockey_nhl",
    "basketball_ncaab": "basketball_ncaab",
    "americanfootball_nfl": "americanfootball_nfl",
    "soccer_epl": "soccer_epl",
    "soccer_usa_mls": "soccer_usa_mls",
    "mma_mixed_martial_arts": "mma_mixed_martial_arts",
}


class OddsClient:
    """Async client for The Odds API with caching and rate tracking."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        regions: Optional[list[str]] = None,
        markets: Optional[list[str]] = None,
        bookmakers_priority: Optional[list[str]] = None,
        cache_ttl_seconds: int = 300,
    ):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        self.regions = regions or ["us", "us2"]
        self.markets = markets or ["h2h", "spreads", "totals"]
        self.bookmakers_priority = bookmakers_priority or [
            "pinnacle", "betonlineag", "bovada", "draftkings", "fanduel"
        ]
        self.cache_ttl = cache_ttl_seconds

        # Simple in-memory cache: key -> (timestamp, data)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._client: Optional[httpx.AsyncClient] = None

        # Track API usage (free tier: 500 requests/month)
        self.requests_used = 0
        self.requests_remaining: Optional[int] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL, timeout=httpx.Timeout(30.0)
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Core fetch with caching
    # ------------------------------------------------------------------

    async def _fetch(
        self, path: str, params: dict[str, Any]
    ) -> tuple[Any, dict[str, str]]:
        """Fetch from API with cache check."""
        cache_key = f"{path}:{sorted(params.items())}"
        now = time.monotonic()

        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < self.cache_ttl:
            logger.debug(f"Cache hit for {path}")
            return cached[1], {}

        if not self.api_key:
            raise ValueError(
                "ODDS_API_KEY not set. Export it as an environment variable."
            )

        params["apiKey"] = self.api_key
        client = await self._get_client()

        try:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(f"Odds API error: {exc.response.status_code} for {path}")
            raise
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            logger.error(f"Odds API connection error: {exc}")
            raise

        # Track rate limit headers
        headers = dict(resp.headers)
        remaining = headers.get("x-requests-remaining")
        if remaining is not None:
            self.requests_remaining = int(remaining)
        used = headers.get("x-requests-used")
        if used is not None:
            self.requests_used = int(used)

        data = resp.json()
        self._cache[cache_key] = (now, data)

        logger.debug(
            f"Fetched {path} — API requests remaining: {self.requests_remaining}"
        )
        return data, headers

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get_sports(self) -> list[dict[str, Any]]:
        """Fetch list of available sports."""
        data, _ = await self._fetch("/sports", {})
        return data

    async def get_odds(
        self,
        sport: str,
        regions: Optional[list[str]] = None,
        markets: Optional[list[str]] = None,
    ) -> list[SportsbookEvent]:
        """Fetch odds for a sport from multiple bookmakers.

        Args:
            sport: Sport key (e.g., "basketball_nba").
            regions: Override default regions.
            markets: Override default market types.

        Returns:
            List of SportsbookEvent objects with odds from all bookmakers.
        """
        sport_key = SPORT_KEYS.get(sport, sport)
        regions_str = ",".join(regions or self.regions)
        markets_str = ",".join(markets or self.markets)

        params: dict[str, Any] = {
            "regions": regions_str,
            "markets": markets_str,
            "oddsFormat": "decimal",
        }

        data, _ = await self._fetch(f"/sports/{sport_key}/odds", params)

        events: list[SportsbookEvent] = []
        for raw_event in data:
            event = self._parse_event(raw_event, sport)
            if event:
                events.append(event)

        logger.info(f"Fetched {len(events)} events for {sport}")
        return events

    async def get_all_odds(
        self, sports: list[str]
    ) -> dict[str, list[SportsbookEvent]]:
        """Fetch odds for multiple sports concurrently.

        Returns:
            Dict mapping sport key to list of events.
        """
        results: dict[str, list[SportsbookEvent]] = {}

        # Fetch sequentially to avoid hammering the API
        for sport in sports:
            try:
                events = await self.get_odds(sport)
                results[sport] = events
            except Exception as exc:
                logger.error(f"Failed to fetch odds for {sport}: {exc}")
                results[sport] = []
            # Small delay between sports to be respectful
            await asyncio.sleep(1.0)

        return results

    # ------------------------------------------------------------------
    # Probability calculations
    # ------------------------------------------------------------------

    def calculate_sharp_probability(
        self, event: SportsbookEvent, outcome_name: str, market_type: str = "h2h"
    ) -> tuple[float, int]:
        """Calculate consensus sharp probability for an outcome.

        Uses a weighted approach: sharp books (Pinnacle, etc.) get higher weight.

        Args:
            event: The sportsbook event with odds.
            outcome_name: Team name or "Over"/"Under".
            market_type: "h2h", "spreads", or "totals".

        Returns:
            Tuple of (sharp_probability, num_books_agreeing).
        """
        relevant_odds: list[tuple[float, float]] = []  # (implied_prob, weight)

        for odds in event.bookmaker_odds:
            if odds.market_type != market_type:
                continue
            if odds.outcome_name.lower() != outcome_name.lower():
                continue

            prob = decimal_to_implied_prob(odds.price)
            if prob <= 0 or prob >= 1:
                continue

            # Weight sharp books higher
            weight = 2.0 if odds.bookmaker in SHARP_BOOKS else 1.0
            # Extra weight for priority bookmakers
            if odds.bookmaker in self.bookmakers_priority[:2]:
                weight = 3.0

            relevant_odds.append((prob, weight))

        if not relevant_odds:
            return 0.0, 0

        # Weighted average probability
        total_weight = sum(w for _, w in relevant_odds)
        weighted_prob = sum(p * w for p, w in relevant_odds) / total_weight

        return weighted_prob, len(relevant_odds)

    def calculate_median_probability(
        self, event: SportsbookEvent, outcome_name: str, market_type: str = "h2h"
    ) -> tuple[float, int]:
        """Calculate median implied probability across all bookmakers.

        Args:
            event: The sportsbook event.
            outcome_name: Team or Over/Under.
            market_type: Market type.

        Returns:
            Tuple of (median_probability, num_books).
        """
        probs: list[float] = []

        for odds in event.bookmaker_odds:
            if odds.market_type != market_type:
                continue
            if odds.outcome_name.lower() != outcome_name.lower():
                continue
            prob = decimal_to_implied_prob(odds.price)
            if 0 < prob < 1:
                probs.append(prob)

        if not probs:
            return 0.0, 0

        probs.sort()
        n = len(probs)
        if n % 2 == 0:
            median = (probs[n // 2 - 1] + probs[n // 2]) / 2
        else:
            median = probs[n // 2]

        return median, n

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_event(
        self, raw: dict[str, Any], sport: str
    ) -> Optional[SportsbookEvent]:
        """Parse a raw Odds API event into a SportsbookEvent."""
        try:
            commence_str = raw.get("commence_time", "")
            commence_time = datetime.fromisoformat(
                commence_str.replace("Z", "+00:00")
            )

            event = SportsbookEvent(
                event_id=raw.get("id", ""),
                sport=sport,
                home_team=raw.get("home_team", ""),
                away_team=raw.get("away_team", ""),
                commence_time=commence_time,
            )

            for bookmaker in raw.get("bookmakers", []):
                bk_name = bookmaker.get("key", "")
                last_update = None
                update_str = bookmaker.get("last_update")
                if update_str:
                    try:
                        last_update = datetime.fromisoformat(
                            update_str.replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        pass

                for market in bookmaker.get("markets", []):
                    market_type = market.get("key", "")
                    for outcome in market.get("outcomes", []):
                        odds = SportsbookOdds(
                            bookmaker=bk_name,
                            market_type=market_type,
                            outcome_name=outcome.get("name", ""),
                            price=outcome.get("price", 0),
                            point=outcome.get("point"),
                            implied_prob=decimal_to_implied_prob(
                                outcome.get("price", 0)
                            ),
                            last_update=last_update,
                        )
                        event.bookmaker_odds.append(odds)

            return event

        except Exception as exc:
            logger.debug(f"Failed to parse event: {exc}")
            return None

    def clear_cache(self) -> None:
        """Clear the odds cache."""
        self._cache.clear()
