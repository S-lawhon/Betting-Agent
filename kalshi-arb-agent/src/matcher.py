"""Market matcher: maps Kalshi markets to sportsbook events using fuzzy matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from rapidfuzz import fuzz, process

from src.logger_setup import get_logger
from src.odds_client import OddsClient
from src.utils import KalshiMarket, MatchedMarket, SportsbookEvent

logger = get_logger("matcher")

# ---------------------------------------------------------------------------
# Team name normalization mappings
# ---------------------------------------------------------------------------

# Common abbreviations / nicknames to full names
TEAM_ALIASES: dict[str, list[str]] = {
    # NBA
    "atlanta hawks": ["atl", "hawks"],
    "boston celtics": ["bos", "celtics"],
    "brooklyn nets": ["bkn", "nets"],
    "charlotte hornets": ["cha", "hornets"],
    "chicago bulls": ["chi", "bulls"],
    "cleveland cavaliers": ["cle", "cavaliers", "cavs"],
    "dallas mavericks": ["dal", "mavericks", "mavs"],
    "denver nuggets": ["den", "nuggets"],
    "detroit pistons": ["det", "pistons"],
    "golden state warriors": ["gsw", "gs", "warriors"],
    "houston rockets": ["hou", "rockets"],
    "indiana pacers": ["ind", "pacers"],
    "los angeles clippers": ["lac", "la clippers", "clippers"],
    "los angeles lakers": ["lal", "la lakers", "lakers"],
    "memphis grizzlies": ["mem", "grizzlies"],
    "miami heat": ["mia", "heat"],
    "milwaukee bucks": ["mil", "bucks"],
    "minnesota timberwolves": ["min", "timberwolves", "wolves"],
    "new orleans pelicans": ["nop", "no", "pelicans"],
    "new york knicks": ["nyk", "ny knicks", "knicks"],
    "oklahoma city thunder": ["okc", "thunder"],
    "orlando magic": ["orl", "magic"],
    "philadelphia 76ers": ["phi", "phl", "sixers", "76ers"],
    "phoenix suns": ["phx", "suns"],
    "portland trail blazers": ["por", "blazers", "trail blazers"],
    "sacramento kings": ["sac", "kings"],
    "san antonio spurs": ["sas", "sa", "spurs"],
    "toronto raptors": ["tor", "raptors"],
    "utah jazz": ["uta", "jazz"],
    "washington wizards": ["was", "wsh", "wizards"],
    # NHL
    "anaheim ducks": ["ana", "ducks"],
    "arizona coyotes": ["ari", "coyotes"],
    "boston bruins": ["bos", "bruins"],
    "buffalo sabres": ["buf", "sabres"],
    "calgary flames": ["cgy", "flames"],
    "carolina hurricanes": ["car", "hurricanes", "canes"],
    "chicago blackhawks": ["chi", "blackhawks"],
    "colorado avalanche": ["col", "avalanche", "avs"],
    "columbus blue jackets": ["cbj", "blue jackets"],
    "dallas stars": ["dal", "stars"],
    "detroit red wings": ["det", "red wings"],
    "edmonton oilers": ["edm", "oilers"],
    "florida panthers": ["fla", "panthers"],
    "los angeles kings": ["lak", "la kings"],
    "minnesota wild": ["min", "wild"],
    "montreal canadiens": ["mtl", "canadiens", "habs"],
    "nashville predators": ["nsh", "predators", "preds"],
    "new jersey devils": ["njd", "nj", "devils"],
    "new york islanders": ["nyi", "ny islanders", "islanders"],
    "new york rangers": ["nyr", "ny rangers", "rangers"],
    "ottawa senators": ["ott", "senators", "sens"],
    "philadelphia flyers": ["phi", "flyers"],
    "pittsburgh penguins": ["pit", "penguins", "pens"],
    "san jose sharks": ["sjs", "sj", "sharks"],
    "seattle kraken": ["sea", "kraken"],
    "st. louis blues": ["stl", "blues", "st louis blues", "saint louis blues"],
    "tampa bay lightning": ["tbl", "tb", "lightning", "bolts"],
    "toronto maple leafs": ["tor", "maple leafs", "leafs"],
    "vancouver canucks": ["van", "canucks"],
    "vegas golden knights": ["vgk", "vegas", "golden knights"],
    "washington capitals": ["wsh", "was", "capitals", "caps"],
    "winnipeg jets": ["wpg", "jets"],
}

# Sport keywords to detect from Kalshi market titles
SPORT_KEYWORDS: dict[str, list[str]] = {
    "basketball_nba": ["nba", "pro basketball", "national basketball"],
    "icehockey_nhl": ["nhl", "pro hockey", "ice hockey", "national hockey"],
    "basketball_ncaab": ["ncaa basketball", "college basketball", "ncaab", "march madness"],
    "americanfootball_nfl": ["nfl", "pro football", "national football"],
    "soccer_epl": ["premier league", "epl", "english premier"],
    "soccer_usa_mls": ["mls", "major league soccer"],
}

# Market type keywords
MARKET_TYPE_KEYWORDS: dict[str, list[str]] = {
    "moneyline": ["win", "winner", "to win", "will win", "victory", "beat"],
    "spread": ["spread", "cover", "margin", "by more than", "by at least"],
    "total": ["total", "over", "under", "combined", "points scored", "goals scored"],
}


def _build_reverse_alias_map() -> dict[str, str]:
    """Build a reverse lookup: alias -> canonical team name."""
    reverse: dict[str, str] = {}
    for canonical, aliases in TEAM_ALIASES.items():
        reverse[canonical] = canonical
        for alias in aliases:
            reverse[alias.lower()] = canonical
    return reverse


_REVERSE_ALIASES = _build_reverse_alias_map()


class MarketMatcher:
    """Matches Kalshi prediction markets to equivalent sportsbook events."""

    def __init__(
        self,
        odds_client: OddsClient,
        min_match_confidence: float = 60.0,
        event_horizon_hours: int = 168,
    ):
        self.odds_client = odds_client
        self.min_match_confidence = min_match_confidence
        self.event_horizon_hours = event_horizon_hours
        self._unmatched_log: list[str] = []

    def match_markets(
        self,
        kalshi_markets: list[KalshiMarket],
        sportsbook_events: dict[str, list[SportsbookEvent]],
    ) -> list[MatchedMarket]:
        """Match Kalshi markets against sportsbook events.

        Args:
            kalshi_markets: List of Kalshi markets to match.
            sportsbook_events: Dict of sport -> events from sportsbooks.

        Returns:
            List of successfully matched markets.
        """
        matched: list[MatchedMarket] = []
        self._unmatched_log.clear()

        for km in kalshi_markets:
            sport = self._detect_sport(km)
            if not sport:
                self._unmatched_log.append(
                    f"No sport detected: {km.ticker} — {km.title}"
                )
                continue

            events = sportsbook_events.get(sport, [])
            if not events:
                continue

            market_type = self._detect_market_type(km)
            match = self._find_best_match(km, events, sport, market_type)
            if match:
                matched.append(match)
            else:
                self._unmatched_log.append(
                    f"No match: {km.ticker} — {km.title} ({sport}/{market_type})"
                )

        logger.info(
            f"Matched {len(matched)}/{len(kalshi_markets)} markets. "
            f"{len(self._unmatched_log)} unmatched."
        )
        if self._unmatched_log:
            for entry in self._unmatched_log[:10]:
                logger.debug(f"  Unmatched: {entry}")

        return matched

    # ------------------------------------------------------------------
    # Sport detection
    # ------------------------------------------------------------------

    def _detect_sport(self, market: KalshiMarket) -> Optional[str]:
        """Detect the sport from a Kalshi market's title/category."""
        text = f"{market.title} {market.subtitle} {market.category}".lower()

        for sport, keywords in SPORT_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return sport
        return None

    # ------------------------------------------------------------------
    # Market type detection
    # ------------------------------------------------------------------

    def _detect_market_type(self, market: KalshiMarket) -> str:
        """Detect whether a market is moneyline, spread, or total."""
        text = f"{market.title} {market.subtitle}".lower()

        for mtype, keywords in MARKET_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return mtype

        # Default to moneyline
        return "moneyline"

    # ------------------------------------------------------------------
    # Team extraction
    # ------------------------------------------------------------------

    def _extract_teams(self, market: KalshiMarket) -> list[str]:
        """Extract team names from a Kalshi market title."""
        text = f"{market.title} {market.subtitle}".lower()

        found_teams: list[str] = []
        for canonical, aliases in TEAM_ALIASES.items():
            for alias in [canonical] + aliases:
                if alias.lower() in text:
                    if canonical not in found_teams:
                        found_teams.append(canonical)
                    break

        return found_teams

    def _normalize_team(self, name: str) -> str:
        """Normalize a team name to its canonical form."""
        lower = name.strip().lower()
        return _REVERSE_ALIASES.get(lower, lower)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _find_best_match(
        self,
        kalshi_market: KalshiMarket,
        events: list[SportsbookEvent],
        sport: str,
        market_type: str,
    ) -> Optional[MatchedMarket]:
        """Find the best sportsbook event match for a Kalshi market."""
        kalshi_teams = self._extract_teams(kalshi_market)

        best_match: Optional[MatchedMarket] = None
        best_score = 0.0

        for event in events:
            # Check date proximity
            if not self._dates_match(kalshi_market, event):
                continue

            # Score team name match
            score = self._score_team_match(kalshi_teams, event, kalshi_market)
            if score < self.min_match_confidence:
                continue

            if score > best_score:
                best_score = score

                # Determine which Kalshi side maps to which outcome
                kalshi_side = self._determine_kalshi_side(
                    kalshi_market, event, market_type
                )

                # Calculate sharp probability
                odds_market_type = self._map_market_type(market_type)
                outcome_name = self._get_outcome_name(
                    kalshi_market, event, kalshi_side, market_type
                )

                sharp_prob, num_books = self.odds_client.calculate_sharp_probability(
                    event, outcome_name, odds_market_type
                )

                if sharp_prob <= 0 or num_books == 0:
                    continue

                best_match = MatchedMarket(
                    kalshi_market=kalshi_market,
                    sportsbook_event=event,
                    match_type=market_type,
                    match_confidence=score / 100.0,
                    kalshi_side=kalshi_side,
                    sharp_probability=sharp_prob,
                    num_books_agreeing=num_books,
                )

        return best_match

    def _score_team_match(
        self,
        kalshi_teams: list[str],
        event: SportsbookEvent,
        kalshi_market: KalshiMarket,
    ) -> float:
        """Score how well the Kalshi market teams match the sportsbook event.

        Returns a score 0-100.
        """
        if not kalshi_teams:
            # Fall back to fuzzy matching on full title
            home_norm = self._normalize_team(event.home_team)
            away_norm = self._normalize_team(event.away_team)
            title = f"{kalshi_market.title} {kalshi_market.subtitle}".lower()

            home_score = fuzz.partial_ratio(home_norm, title)
            away_score = fuzz.partial_ratio(away_norm, title)
            return max(home_score, away_score)

        event_teams = {
            self._normalize_team(event.home_team),
            self._normalize_team(event.away_team),
        }

        # Check exact canonical matches
        matches = sum(1 for t in kalshi_teams if t in event_teams)
        if matches >= 2:
            return 95.0  # Both teams match
        if matches == 1:
            return 80.0  # One team matches

        # Fuzzy match each Kalshi team against event teams
        scores: list[float] = []
        for kt in kalshi_teams:
            best = 0.0
            for et in event_teams:
                s = fuzz.ratio(kt, et)
                best = max(best, s)
            scores.append(best)

        return sum(scores) / len(scores) if scores else 0.0

    def _dates_match(
        self, kalshi_market: KalshiMarket, event: SportsbookEvent
    ) -> bool:
        """Check if dates are close enough to be the same event."""
        if kalshi_market.close_time is None:
            return True  # Can't filter, allow it

        # Allow a 48-hour window for date matching
        delta = abs(
            (kalshi_market.close_time - event.commence_time).total_seconds()
        )
        return delta < 48 * 3600

    def _determine_kalshi_side(
        self,
        kalshi_market: KalshiMarket,
        event: SportsbookEvent,
        market_type: str,
    ) -> str:
        """Determine if Kalshi YES maps to home or away team."""
        title = f"{kalshi_market.title} {kalshi_market.subtitle}".lower()
        home_norm = self._normalize_team(event.home_team)
        away_norm = self._normalize_team(event.away_team)

        # Check which team the YES side refers to
        home_in_title = fuzz.partial_ratio(home_norm, title)
        away_in_title = fuzz.partial_ratio(away_norm, title)

        # The team mentioned more prominently in the YES context is the YES side
        if home_in_title >= away_in_title:
            return "yes"  # YES = home team wins/covers
        return "no"  # YES = away team wins/covers

    def _get_outcome_name(
        self,
        kalshi_market: KalshiMarket,
        event: SportsbookEvent,
        kalshi_side: str,
        market_type: str,
    ) -> str:
        """Get the sportsbook outcome name that corresponds to the Kalshi YES side."""
        if market_type == "total":
            title_lower = kalshi_market.title.lower()
            if "over" in title_lower:
                return "Over"
            return "Under"

        # For moneyline and spreads, return the team name
        title = f"{kalshi_market.title} {kalshi_market.subtitle}".lower()
        home_score = fuzz.partial_ratio(
            self._normalize_team(event.home_team), title
        )
        away_score = fuzz.partial_ratio(
            self._normalize_team(event.away_team), title
        )

        if home_score >= away_score:
            return event.home_team
        return event.away_team

    @staticmethod
    def _map_market_type(market_type: str) -> str:
        """Map our internal market type to Odds API market key."""
        mapping = {
            "moneyline": "h2h",
            "spread": "spreads",
            "total": "totals",
        }
        return mapping.get(market_type, "h2h")

    @property
    def unmatched_markets(self) -> list[str]:
        """Return log of unmatched markets for review."""
        return list(self._unmatched_log)
