"""
src/kalshi_live_discovery.py
────────────────────────────
Discovers and matches Kalshi live sports markets to Odds API game IDs.

Reuses the proven matching approach from P-001's Scanner:
  1. Fetch game-winner markets via series_ticker (e.g. KXNBAGAME, KXMLBGAME)
  2. Fuzzy-match Kalshi titles to Odds API team names
  3. Infer YES-side resolution (home or away)
  4. Extract mid-price from yes_bid/yes_ask

Differences from P-001:
  - Caches market list per sport (refreshed every N seconds, not every scan)
  - Returns structured KalshiMarketMatch instead of log dicts
  - Focuses on live (in-play) games, but also discovers pre-game for readiness

Usage:
    discovery = KalshiLiveDiscovery(kalshi_client)
    match = discovery.find_market("basketball_nba", "Lakers", "Celtics")
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.et_time import et_date_tag
from src.fuzzy_utils import token_sort_ratio, team_name_in_text, both_teams_present

logger = logging.getLogger(__name__)


# ── Sport → Kalshi series ticker prefixes ─────────────────────────────
# Mirrors _SPORT_TICKER_PREFIXES from Legacy scanner.py
SPORT_SERIES: Dict[str, List[str]] = {
    "basketball_nba": ["KXNBAGAME", "KXNBA1HWINNER", "KXNBA2HWINNER"],
    "baseball_mlb":   ["KXMLBGAME"],
}

# Title noise words to strip before fuzzy matching
_TITLE_NOISE = re.compile(
    r"\b(winner|winners|at|vs\.?|v|the|by|over|in|match|game|series)\b|[?!]",
    re.IGNORECASE,
)

# "Will [X] win?" pattern for YES-side inference
_WILL_WIN_RE = re.compile(r"Will\s+(.+?)\s+win", re.IGNORECASE)


@dataclass(frozen=True)
class KalshiMarketMatch:
    """A matched Kalshi market for a live game."""
    game_id: str              # Odds API game ID
    ticker: str               # Kalshi market ticker
    title: str                # Kalshi market title
    yes_side: str             # "home" or "away" — which team YES resolves to
    home_team: str
    away_team: str
    yes_bid: int              # Integer cents 0-100
    yes_ask: int              # Integer cents 0-100
    volume: int               # Kalshi trading volume
    fuzzy_score: float        # Match confidence 0-100
    market_type: str          # "game_winner", "1h_winner", "2h_winner"

    @property
    def mid_price(self) -> float:
        """Mid-price as a probability (0-1)."""
        if self.yes_bid <= 0 and self.yes_ask <= 0:
            return 0.0
        return (self.yes_bid + self.yes_ask) / 200.0

    @property
    def home_yes_prob(self) -> float:
        """Implied probability of home winning from YES price."""
        mid = self.mid_price
        if self.yes_side == "home":
            return mid
        else:
            return 1.0 - mid

    @property
    def away_yes_prob(self) -> float:
        return 1.0 - self.home_yes_prob

    @property
    def spread_cents(self) -> int:
        """Bid-ask spread in cents."""
        return max(0, self.yes_ask - self.yes_bid)


def _parse_kalshi_price_cents(market: Dict, field: str) -> int:
    """Extract price as integer cents from Kalshi v2 dollar fields.

    Kalshi v2 API returns prices as dollar strings like "0.5200".
    Falls back to legacy integer-cent fields if dollar field is absent.
    """
    # Kalshi v2: "yes_bid_dollars" → "0.5200"
    dollar_val = market.get(f"{field}_dollars")
    if dollar_val is not None:
        try:
            return round(float(dollar_val) * 100)
        except (TypeError, ValueError):
            pass
    # Legacy fallback: "yes_bid" → 52  (integer cents)
    legacy_val = market.get(field)
    if legacy_val is not None:
        try:
            return int(legacy_val)
        except (TypeError, ValueError):
            pass
    return 0


def _parse_kalshi_volume(market: Dict) -> int:
    """Extract volume from Kalshi v2 response.

    v2 uses "volume_fp" (string like "67425.00"), legacy uses "volume" (int).
    """
    vol_fp = market.get("volume_fp")
    if vol_fp is not None:
        try:
            return int(float(vol_fp))
        except (TypeError, ValueError):
            pass
    return market.get("volume", 0)


class KalshiLiveDiscovery:
    """Discovers and caches Kalshi markets for live game matching.

    Fetches game-winner series from Kalshi API, caches results, and
    provides fuzzy-matching against Odds API game identifiers.
    """

    def __init__(
        self,
        kalshi_client,
        cache_ttl_seconds: float = 120.0,
        fuzzy_threshold: float = 65.0,
        min_volume: int = 0,
        _now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._kalshi = kalshi_client
        self._cache_ttl = cache_ttl_seconds
        self._fuzzy_threshold = fuzzy_threshold
        self._min_volume = min_volume
        self._now_fn = _now_fn or time.time

        # Cache: sport → (timestamp, list of market dicts)
        self._market_cache: Dict[str, Tuple[float, List[Dict]]] = {}

        # Match cache: (sport, home, away) → KalshiMarketMatch or None
        self._match_cache: Dict[Tuple[str, str, str], Optional[KalshiMarketMatch]] = {}

    def find_market(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        game_id: str = "",
    ) -> Optional[KalshiMarketMatch]:
        """Find the best Kalshi market match for a given game.

        Parameters
        ----------
        sport : str
            Odds API sport key (e.g. "basketball_nba")
        home_team : str
            Home team name from Odds API
        away_team : str
            Away team name from Odds API
        game_id : str
            Odds API game ID (for the match result)

        Returns
        -------
        KalshiMarketMatch or None
        """
        cache_key = (sport, home_team, away_team)
        if cache_key in self._match_cache:
            cached = self._match_cache[cache_key]
            # Return cached match but refresh price if we have a ticker
            if cached is not None:
                refreshed = self._refresh_price(cached)
                if refreshed:
                    return refreshed
            return cached

        markets = self._get_markets(sport)
        if not markets:
            return None

        best_match = self._match_game(
            markets, sport, home_team, away_team, game_id,
        )
        # list_markets doesn't return yes_bid/yes_ask — fetch live price
        if best_match is not None:
            refreshed = self._refresh_price(best_match)
            if refreshed is not None:
                best_match = refreshed
        self._match_cache[cache_key] = best_match
        return best_match

    def find_all_markets(
        self, sport: str
    ) -> List[Dict[str, Any]]:
        """Return all cached Kalshi markets for a sport."""
        return self._get_markets(sport)

    def clear_cache(self) -> None:
        """Force cache refresh on next call."""
        self._market_cache.clear()
        self._match_cache.clear()

    def get_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch live orderbook for a market."""
        try:
            return self._kalshi.get_orderbook(ticker)
        except Exception as exc:
            logger.debug("KalshiLiveDiscovery: orderbook error for %s: %s", ticker, exc)
            return None

    # ── Internal ──────────────────────────────────────────────────────

    def _get_markets(self, sport: str) -> List[Dict]:
        """Get Kalshi markets for a sport, using cache if fresh."""
        now = self._now_fn()
        cached = self._market_cache.get(sport)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        # Fetch from Kalshi API
        series_list = SPORT_SERIES.get(sport, [])
        if not series_list:
            logger.debug("KalshiLiveDiscovery: no series configured for %s", sport)
            return []

        all_markets: List[Dict] = []
        seen_tickers: set = set()

        for series in series_list:
            try:
                resp = self._kalshi.list_markets(
                    status="open",
                    limit=200,
                    series_ticker=series,
                )
                markets = resp.get("markets", [])
                for m in markets:
                    t = m.get("ticker", "")
                    if t and t not in seen_tickers:
                        seen_tickers.add(t)
                        all_markets.append(m)
            except Exception as exc:
                logger.warning(
                    "KalshiLiveDiscovery: failed to fetch series %s: %s",
                    series, exc,
                )

        self._market_cache[sport] = (now, all_markets)
        logger.info(
            "KalshiLiveDiscovery: %s — fetched %d markets from %d series",
            sport, len(all_markets), len(series_list),
        )
        return all_markets

    def _match_game(
        self,
        markets: List[Dict],
        sport: str,
        home_team: str,
        away_team: str,
        game_id: str,
    ) -> Optional[KalshiMarketMatch]:
        """Find the best matching Kalshi market for a game.

        Only considers GAME_WINNER markets (not 1H/2H) for Phase 2.
        When multiple date variants exist (same teams, different days),
        strongly prefers today's market via a date-match bonus.
        """
        game_str = f"{home_team} {away_team}"
        game_norm = _normalize_title(game_str)

        # Today's date tag as it appears in Kalshi tickers: "26MAR28".
        #
        # This MUST be the ET date, not the UTC date. Kalshi encodes the ET
        # calendar day, and between 00:00 and 04:00 UTC (20:00-00:00 ET) the
        # UTC date has already rolled over while the ticker tag has not — so
        # a UTC-derived tag was wrong for exactly the evening slate, which is
        # most of the board. The tag only drives a scoring BONUS, so the
        # symptom was a weakened same-day preference (and a possible wrong-day
        # match when the same teams play consecutive days), not an outright
        # discovery failure.
        today_tag = et_date_tag()

        best_match: Optional[KalshiMarketMatch] = None
        best_score = 0.0

        for market in markets:
            ticker = market.get("ticker", "")
            title = market.get("title", "")

            # Determine market type from ticker prefix
            market_type = self._infer_market_type(ticker, sport)

            # For Phase 2, prefer game_winner markets
            if market_type != "game_winner":
                continue

            # Skip low-volume markets
            volume = _parse_kalshi_volume(market)
            if volume < self._min_volume:
                continue

            # Normalize and fuzzy match
            title_norm = _normalize_title(title)
            score = token_sort_ratio(game_norm, title_norm)

            # Also check if both teams present in title
            teams_present = both_teams_present(home_team, away_team, title)

            # Require either high fuzzy score OR both teams present
            if score < self._fuzzy_threshold and not teams_present:
                continue

            # Infer YES side
            yes_side = self._infer_yes_side(market, home_team, away_team)
            if yes_side is None:
                continue

            # Date bonus: strongly prefer today's game over future dates
            date_bonus = 50.0 if today_tag in ticker.upper() else 0.0

            effective_score = score + (10.0 if teams_present else 0.0) + date_bonus
            if effective_score > best_score:
                best_score = effective_score
                best_match = KalshiMarketMatch(
                    game_id=game_id,
                    ticker=ticker,
                    title=title,
                    yes_side=yes_side,
                    home_team=home_team,
                    away_team=away_team,
                    yes_bid=_parse_kalshi_price_cents(market, "yes_bid"),
                    yes_ask=_parse_kalshi_price_cents(market, "yes_ask"),
                    volume=volume,
                    fuzzy_score=score,
                    market_type=market_type,
                )

        if best_match:
            logger.info(
                "KalshiLiveDiscovery: matched %s @ %s → %s (score=%.1f, yes=%s, mid=%.3f)",
                home_team, away_team, best_match.ticker,
                best_match.fuzzy_score, best_match.yes_side, best_match.mid_price,
            )
        else:
            logger.debug(
                "KalshiLiveDiscovery: no match for %s @ %s (%d markets checked)",
                home_team, away_team, len(markets),
            )

        return best_match

    def _infer_yes_side(
        self, market: Dict, home_team: str, away_team: str
    ) -> Optional[str]:
        """Infer which team YES resolves to.

        Mirrors _infer_yes_side from Legacy scanner.py.
        """
        title = market.get("title", "") or ""
        ticker = market.get("ticker", "") or ""

        # TIE suffix → skip
        suffix = ticker.rsplit("-", 1)[-1].upper() if "-" in ticker else ""
        if suffix == "TIE":
            return None

        # "Will [X] win?" pattern
        m = _WILL_WIN_RE.search(title)
        if m:
            yes_name = m.group(1).strip()
            home_score = token_sort_ratio(yes_name, home_team)
            away_score = token_sort_ratio(yes_name, away_team)
            if max(home_score, away_score) < 20:
                return None
            return "home" if home_score >= away_score else "away"

        # Ticker suffix fallback
        _MARKET_TYPE_SUFFIXES = {"ML", "OU", "SP", "TIE", "OVER", "UNDER"}
        if suffix and suffix not in _MARKET_TYPE_SUFFIXES and len(suffix) >= 2:
            home_score = token_sort_ratio(suffix, home_team)
            away_score = token_sort_ratio(suffix, away_team)
            if max(home_score, away_score) >= 15.0:
                return "home" if home_score >= away_score else "away"

        # Both teams in title → check which one is named first (less reliable)
        if team_name_in_text(home_team, title) and team_name_in_text(away_team, title):
            # Default to home if both present and no other signal
            return "home"

        return None

    def _infer_market_type(self, ticker: str, sport: str) -> str:
        """Classify market type from ticker prefix."""
        ticker_upper = ticker.upper()
        if "1HWINNER" in ticker_upper:
            return "1h_winner"
        if "2HWINNER" in ticker_upper:
            return "2h_winner"
        if "GAME" in ticker_upper:
            return "game_winner"
        return "unknown"

    def _refresh_price(self, match: KalshiMarketMatch) -> Optional[KalshiMarketMatch]:
        """Refresh the price for an existing match by re-fetching the market."""
        try:
            data = self._kalshi.get_market(match.ticker)
            market = data.get("market", data)
            yes_bid = _parse_kalshi_price_cents(market, "yes_bid") or match.yes_bid
            yes_ask = _parse_kalshi_price_cents(market, "yes_ask") or match.yes_ask
            vol = _parse_kalshi_volume(market) or match.volume
            logger.info(
                "KalshiLiveDiscovery: refreshed %s — yes_bid=%d, yes_ask=%d, vol=%d",
                match.ticker, yes_bid, yes_ask, vol,
            )
            return KalshiMarketMatch(
                game_id=match.game_id,
                ticker=match.ticker,
                title=match.title,
                yes_side=match.yes_side,
                home_team=match.home_team,
                away_team=match.away_team,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                volume=vol,
                fuzzy_score=match.fuzzy_score,
                market_type=match.market_type,
            )
        except Exception as exc:
            logger.warning("KalshiLiveDiscovery: refresh error for %s: %s", match.ticker, exc)
            return None


def _normalize_title(s: str) -> str:
    """Strip noise words from a title for fuzzy matching."""
    return " ".join(_TITLE_NOISE.sub(" ", s).split())
