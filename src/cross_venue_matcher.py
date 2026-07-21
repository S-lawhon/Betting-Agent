"""
src/cross_venue_matcher.py
──────────────────────────
Matches events across Kalshi and Polymarket for cross-venue arb scanning.

Three-pass matching:
  1. Normalise titles (city→team, strip "Winner?", etc.)
  2. Fuzzy title matching (token-sort ratio)
  3. Both-teams-present validation (prevents single-team partial matches)
  4. Settlement source verification (flags mismatches)

Kalshi titles use city/market names: "Boston at Columbus Winner?"
Polymarket titles use team names:   "Bruins vs. Blue Jackets"
The normaliser converts both to team-name form before comparing.

Settlement mismatches are flagged but not auto-rejected — some are
acceptable (same underlying, slightly different resolution timing).
The consuming pod (P-002) decides whether to trade.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.fuzzy_utils import token_sort_ratio, both_teams_present
from src.polymarket_client import PolymarketMarket
from src.polymarket_matcher import _extract_teams_from_question

logger = logging.getLogger(__name__)


# ── Sport-specific city/market-name → team name mappings ──────────
# Kalshi uses city/market names; Polymarket uses team nicknames.
# Mappings are per-sport to handle cities with multiple teams
# (e.g. "Boston" = Celtics in NBA, Bruins in NHL, Red Sox in MLB).
_SPORT_CITY_TO_TEAM: Dict[str, Dict[str, str]] = {
    "nba": {
        "atlanta": "Hawks", "boston": "Celtics", "brooklyn": "Nets",
        "charlotte": "Hornets", "chicago": "Bulls", "cleveland": "Cavaliers",
        "dallas": "Mavericks", "denver": "Nuggets", "detroit": "Pistons",
        "golden state": "Warriors", "houston": "Rockets", "indiana": "Pacers",
        "la clippers": "Clippers", "la lakers": "Lakers",
        "los angeles c": "Clippers", "los angeles l": "Lakers",
        "memphis": "Grizzlies", "miami": "Heat", "milwaukee": "Bucks",
        "minnesota": "Timberwolves", "new orleans": "Pelicans",
        "new york": "Knicks", "oklahoma city": "Thunder", "orlando": "Magic",
        "philadelphia": "76ers", "phoenix": "Suns", "portland": "Trail Blazers",
        "sacramento": "Kings", "san antonio": "Spurs", "toronto": "Raptors",
        "utah": "Jazz", "washington": "Wizards",
    },
    "nhl": {
        "anaheim": "Ducks", "arizona": "Coyotes", "boston": "Bruins",
        "buffalo": "Sabres", "calgary": "Flames", "carolina": "Hurricanes",
        "chicago": "Blackhawks", "colorado": "Avalanche",
        "columbus": "Blue Jackets", "dallas": "Stars", "detroit": "Red Wings",
        "edmonton": "Oilers", "florida": "Panthers",
        "los angeles": "Kings", "minnesota": "Wild",
        "montreal": "Canadiens", "nashville": "Predators",
        "new jersey": "Devils", "new york i": "Islanders", "new york r": "Rangers",
        "new york": "Rangers",  # default NY → Rangers in NHL
        "ottawa": "Senators", "philadelphia": "Flyers",
        "pittsburgh": "Penguins", "san jose": "Sharks", "seattle": "Kraken",
        "st louis": "Blues", "tampa bay": "Lightning",
        "vancouver": "Canucks", "vegas": "Golden Knights",
        "washington": "Capitals", "winnipeg": "Jets",
    },
    "mlb": {
        "arizona": "Diamondbacks", "atlanta": "Braves",
        "baltimore": "Orioles", "boston": "Red Sox",
        "chicago c": "Cubs", "chicago w": "White Sox", "chicago": "Cubs",
        "cincinnati": "Reds", "cleveland": "Guardians",
        "colorado": "Rockies", "detroit": "Tigers",
        "houston": "Astros", "kansas city": "Royals",
        "los angeles a": "Angels", "los angeles d": "Dodgers",
        "los angeles": "Dodgers",
        "miami": "Marlins", "milwaukee": "Brewers",
        "minnesota": "Twins", "new york m": "Mets", "new york y": "Yankees",
        "new york": "Yankees",
        "oakland": "Athletics", "philadelphia": "Phillies",
        "pittsburgh": "Pirates", "san diego": "Padres",
        "san francisco": "Giants", "seattle": "Mariners",
        "st louis": "Cardinals", "tampa bay": "Rays",
        "texas": "Rangers", "toronto": "Blue Jays",
        "washington": "Nationals",
    },
    "nfl": {
        "arizona": "Cardinals", "atlanta": "Falcons",
        "baltimore": "Ravens", "buffalo": "Bills",
        "carolina": "Panthers", "chicago": "Bears",
        "cincinnati": "Bengals", "cleveland": "Browns",
        "dallas": "Cowboys", "denver": "Broncos",
        "detroit": "Lions", "green bay": "Packers",
        "houston": "Texans", "indianapolis": "Colts",
        "jacksonville": "Jaguars", "kansas city": "Chiefs",
        "las vegas": "Raiders", "los angeles ch": "Chargers",
        "los angeles r": "Rams", "los angeles": "Rams",
        "miami": "Dolphins", "minnesota": "Vikings",
        "new england": "Patriots", "new orleans": "Saints",
        "new york g": "Giants", "new york j": "Jets",
        "new york": "Giants",
        "philadelphia": "Eagles", "pittsburgh": "Steelers",
        "san francisco": "49ers", "seattle": "Seahawks",
        "tampa bay": "Buccaneers", "tennessee": "Titans",
        "washington": "Commanders",
    },
}

# Kalshi ticker prefix → sport key for the mapping
_TICKER_PREFIX_TO_SPORT: Dict[str, str] = {
    "KXNBAGAME": "nba", "KXNCAABGAME": "nba", "KXNCAABBGAME": "nba",
    "KXNCAAWBGAME": "nba", "KXNCAAWGAME": "nba",
    "KXNHLGAME": "nhl",
    "KXMLBGAME": "mlb",
    "KXNFLGAME": "nfl", "KXNFLTOTAL": "nfl",
    "KXATPMATCH": "tennis", "KXWTAMATCH": "tennis",
}


def _detect_sport_from_ticker(ticker: str) -> Optional[str]:
    """Detect sport from Kalshi ticker prefix."""
    upper = ticker.upper()
    for prefix, sport in _TICKER_PREFIX_TO_SPORT.items():
        if upper.startswith(prefix):
            return sport
    return None


def normalise_market_title(title: str, sport: Optional[str] = None) -> str:
    """Normalise a market title for cross-venue matching.

    Strips "Winner?", "at"/"vs"/"vs." noise, and replaces city names
    with team nicknames (using sport-specific mappings) so both venues
    use comparable text.

    "Boston at Columbus Winner?" (nhl) → "Bruins vs Blue Jackets"
    "Mavericks vs. Bucks"              → "Mavericks vs Bucks"
    """
    # Strip "Will ... win/beat ..." question framing
    s = re.sub(r"^Will\s+", "", title, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+win(\s+the\s+.*)?[\?\s]*$", "", s, flags=re.IGNORECASE).strip()
    # Strip trailing "Winner?" / "winner?" / ": Game Winner?" etc.
    s = re.sub(r"[:\?]*\s*(Game\s+)?Winner\??", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s*winner\??\s*$", "", s, flags=re.IGNORECASE).strip()
    # Strip trailing "?"
    s = s.rstrip("?").strip()
    # Normalise "at" / "beat" / "defeat" → "vs" and strip periods after "vs"
    s = re.sub(r"\s+at\s+", " vs ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+beat\s+(the\s+)?", " vs ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+defeat\s+(the\s+)?", " vs ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bvs\.\s*", "vs ", s)
    # Strip leading "the " from each side
    s = re.sub(r"^the\s+", "", s, flags=re.IGNORECASE)
    # Strip parenthetical like "(FL)" in college names
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # Split by "vs" to get the two sides
    parts = re.split(r"\s+vs\s+", s, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return s  # Can't parse — return cleaned version

    # Get sport-specific mapping (fall back to trying all sports)
    city_map = _SPORT_CITY_TO_TEAM.get(sport, {}) if sport else {}

    normalised_parts = []
    for part in parts:
        p = part.strip()
        resolved = _resolve_city_to_team(p, city_map, sport)
        normalised_parts.append(resolved)

    return f"{normalised_parts[0]} vs {normalised_parts[1]}"


def _resolve_city_to_team(
    name: str, primary_map: Dict[str, str], sport: Optional[str]
) -> str:
    """Try to resolve a city/market name to a team nickname.

    Uses the sport-specific map only.  Does NOT fall back to other
    sports because multi-sport cities (Boston, Chicago, New York, etc.)
    would produce wrong mappings.  If no sport is specified, returns
    the name unchanged — the fuzzy matcher still handles it.
    """
    if not primary_map:
        return name

    lower = name.lower().strip()

    # Try exact match in primary map
    if lower in primary_map:
        return primary_map[lower]

    # Try prefix match in primary map (handles "New York R" → "Rangers")
    best = _prefix_match(lower, primary_map)
    if best:
        return best

    # Already a team name or unrecognised — return as-is
    return name


def _prefix_match(lower_name: str, mapping: Dict[str, str]) -> Optional[str]:
    """Find the longest city prefix match in the mapping."""
    best_match = None
    best_len = 0
    for city_key, team in mapping.items():
        if lower_name.startswith(city_key) and len(city_key) > best_len:
            best_match = team
            best_len = len(city_key)
    return best_match if best_match and best_len >= 4 else None


# ── Kalshi sport ticker prefixes (same as legacy scanner) ────────────
# Only markets matching these prefixes are considered sports markets.
# This filters out political, climate, crypto, and prop markets.
_SPORT_TICKER_PREFIXES: Dict[str, tuple] = {
    "americanfootball_nfl": ("KXNFLGAME", "KXNFLTOTAL"),
    "basketball_nba": ("KXNBAGAME",),
    "basketball_ncaab": ("KXNCAABGAME", "KXNCAABBGAME", "KXNCAABBTOTAL"),
    "basketball_ncaaw": ("KXNCAAWBGAME", "KXNCAAWGAME", "KXNCAAWBTOTAL"),
    "baseball_mlb": ("KXMLBGAME",),
    "icehockey_nhl": ("KXNHLGAME",),
    "tennis_atp": ("KXATPMATCH",),
    "tennis_wta": ("KXWTAMATCH",),
}

# Flattened set for fast prefix checking
_ALL_SPORT_PREFIXES: set = set()
for _prefixes in _SPORT_TICKER_PREFIXES.values():
    _ALL_SPORT_PREFIXES.update(_prefixes)


def is_sport_ticker(ticker: str) -> bool:
    """Return True if a Kalshi ticker matches a known sport prefix."""
    upper = ticker.upper()
    return any(upper.startswith(p) for p in _ALL_SPORT_PREFIXES)


@dataclass(frozen=True)
class CrossVenueMatch:
    """An event identified on both Kalshi and Polymarket."""
    kalshi_ticker: str
    kalshi_title: str
    kalshi_yes_price: Optional[float]
    kalshi_no_price: Optional[float]
    polymarket_condition_id: str
    polymarket_question: str
    polymarket_token_yes: str
    polymarket_token_no: str
    fuzzy_score: float
    settlement_aligned: bool
    teams_validated: bool = False
    # Which team Kalshi YES refers to (from yes_sub_title API field).
    # Used by P-002 to align sides with Polymarket.
    kalshi_yes_team: str = ""


class CrossVenueMatcher:
    """Matches events across Kalshi and Polymarket.

    For each Kalshi market, finds the best-matching Polymarket market
    above the fuzzy threshold.  Validates both teams are present in both
    market titles, and optionally checks settlement alignment.
    """

    def __init__(
        self,
        fuzzy_threshold: float = 80.0,
        settlement_check: bool = True,
        require_both_teams: bool = True,
        sports_only: bool = True,
    ):
        self._threshold = fuzzy_threshold
        self._settlement_check = settlement_check
        self._require_both_teams = require_both_teams
        self._sports_only = sports_only

    def match_all(
        self,
        kalshi_markets: List[Dict[str, Any]],
        polymarket_markets: List[PolymarketMarket],
    ) -> List[CrossVenueMatch]:
        """Match Kalshi markets to Polymarket markets.

        Normalises titles (city→team) before fuzzy matching so that
        "Boston at Columbus Winner?" can match "Blue Jackets vs. Bruins".

        Returns list of CrossVenueMatch for all pairs above threshold
        that pass team validation.
        """
        results = []

        # Pre-filter Kalshi markets to sports only
        if self._sports_only:
            kalshi_markets = [
                km for km in kalshi_markets
                if is_sport_ticker(km.get("ticker", ""))
            ]

        for km in kalshi_markets:
            kalshi_title = km.get("title", "")
            kalshi_ticker = km.get("ticker", "")
            # Use sport from ticker prefix for accurate city→team mapping
            sport = _detect_sport_from_ticker(kalshi_ticker)
            kalshi_norm = normalise_market_title(kalshi_title, sport=sport)

            best_idx = -1
            best_score = 0.0

            for i, pm in enumerate(polymarket_markets):
                # Normalise Polymarket with the same sport context so both
                # sides use the same city→team resolution
                pn = normalise_market_title(pm.question, sport=sport)
                score = token_sort_ratio(kalshi_norm, pn)
                if score > best_score:
                    best_score = score
                    best_idx = i

            if best_score < self._threshold or best_idx < 0:
                continue

            pm = polymarket_markets[best_idx]

            # ── Both-teams-present validation ──────────────────────────
            # Prevents the P-006-style matching bug where a single shared
            # team name (e.g. "Blackhawks") produces a 55-60 score even
            # when the opponent is completely different.
            teams_ok = True
            if self._require_both_teams:
                teams_ok = self._validate_both_teams(
                    kalshi_title, pm.question, sport=sport,
                )
                if not teams_ok:
                    logger.debug(
                        "P-002: TEAM_MISMATCH %s vs %s (score=%.1f)",
                        kalshi_title, pm.question, best_score,
                    )
                    continue

            # Settlement check (heuristic: same keywords in resolution)
            aligned = True
            if self._settlement_check:
                aligned = self._check_settlement(km, pm, sport=sport)

            # Extract Kalshi prices (API returns *_dollars fields as
            # dollar-denominated strings, e.g. "0.5700")
            yes_bid = km.get("yes_bid_dollars") or km.get("yes_bid")
            yes_ask = km.get("yes_ask_dollars") or km.get("yes_ask")
            kalshi_yes = None
            kalshi_no = None
            if yes_bid is not None and yes_ask is not None:
                try:
                    bid_f = float(yes_bid)
                    ask_f = float(yes_ask)
                    # _dollars fields are already in [0,1] range;
                    # legacy cent fields were 0-99 — detect and convert
                    if bid_f > 1.0 or ask_f > 1.0:
                        bid_f /= 100.0
                        ask_f /= 100.0
                    kalshi_yes = (bid_f + ask_f) / 2.0
                    kalshi_no = 1.0 - kalshi_yes
                except (ValueError, TypeError):
                    pass

            results.append(CrossVenueMatch(
                kalshi_ticker=kalshi_ticker,
                kalshi_title=kalshi_title,
                kalshi_yes_price=kalshi_yes,
                kalshi_no_price=kalshi_no,
                polymarket_condition_id=pm.condition_id,
                polymarket_question=pm.question,
                polymarket_token_yes=pm.token_id_yes,
                polymarket_token_no=pm.token_id_no,
                fuzzy_score=best_score,
                settlement_aligned=aligned,
                teams_validated=teams_ok,
                kalshi_yes_team=km.get("yes_sub_title", ""),
            ))

        return results

    def _validate_both_teams(
        self,
        kalshi_title: str,
        poly_question: str,
        sport: Optional[str] = None,
    ) -> bool:
        """Validate that both teams from the Kalshi title appear in the
        Polymarket question.

        Normalises both titles first (city→team) so that "Boston" matches
        "Bruins" (NHL).  Falls back to the flexible team_name_in_text() heuristic.
        """
        # Normalise both sides to team names (sport-aware)
        k_norm = normalise_market_title(kalshi_title, sport=sport)
        p_norm = normalise_market_title(poly_question, sport=sport)

        # Extract the two sides from the normalised Kalshi title
        parts = re.split(r"\s+vs\s+", k_norm, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            # Can't extract two teams — fall back to fuzzy-only
            return True
        team_a = parts[0].strip()
        team_b = parts[1].strip()
        if not team_a or not team_b:
            return True
        return both_teams_present(team_a, team_b, p_norm)

    def _check_settlement(
        self,
        kalshi_market: Dict[str, Any],
        poly_market: PolymarketMarket,
        sport: Optional[str] = None,
    ) -> bool:
        """Heuristic settlement alignment check.

        Checks if both markets likely resolve from the same source
        by comparing key terms in normalised titles.
        """
        kalshi_text = normalise_market_title(
            kalshi_market.get("title", ""), sport=sport,
        ).lower()
        poly_text = normalise_market_title(
            poly_market.question, sport=sport,
        ).lower()

        # Extract meaningful tokens (team names, event terms)
        kalshi_tokens = set(kalshi_text.split()) - {"vs", "the", "will", "win", "?"}
        poly_tokens = set(poly_text.split()) - {"vs", "the", "will", "win", "?",
                                                  "beat", "defeat"}

        if not kalshi_tokens or not poly_tokens:
            return False

        # Check overlap — if >50% of tokens overlap, likely same event
        overlap = kalshi_tokens & poly_tokens
        smaller = min(len(kalshi_tokens), len(poly_tokens))
        if smaller == 0:
            return False

        return len(overlap) / smaller >= 0.4

    @classmethod
    def from_config(cls, config: dict) -> "CrossVenueMatcher":
        """Build from config dict."""
        xvm_cfg = config.get("cross_venue_matcher", {})
        return cls(
            fuzzy_threshold=xvm_cfg.get("fuzzy_threshold", 80.0),
            settlement_check=xvm_cfg.get("settlement_check", True),
            require_both_teams=xvm_cfg.get("require_both_teams", True),
            sports_only=xvm_cfg.get("sports_only", True),
        )
