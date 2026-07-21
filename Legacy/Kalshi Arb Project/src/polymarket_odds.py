"""
src/polymarket_odds.py
──────────────────────
Polymarket Gamma API adapter for tennis odds fallback.

When The Odds API has no events for a tennis sport key (common for
challengers and qualifying rounds), this module queries Polymarket's
public Gamma API for match-winner markets and converts them into the
same OddsEvent / ConsensusProbability objects the scanner expects.

This gives the P-001 Kalshi scanner a second consensus signal for tennis
matches that The Odds API doesn't cover.

Architecture
────────────
  fetch_polymarket_tennis_events(sport) → List[OddsEvent]
  build_consensus(event)                → ConsensusProbability

The Gamma API is public (no auth needed):
  GET https://gamma-api.polymarket.com/events?series_id=X&active=true&closed=false

Polymarket tennis events look like:
  title: "Sao Paulo: Roman Andres Burruchaga vs Juan Carlos Prado"
  markets[]: each has outcomePrices like "[0.45, 0.55]"
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from logger_setup import get_logger
from odds_client import BookmakerOdds, ConsensusProbability, OddsEvent, OddsOutcome

log = get_logger(__name__)

GAMMA_HOST = "https://gamma-api.polymarket.com"

# Rate limit: max 1 request per 2 seconds
_last_request_time = 0.0
_MIN_INTERVAL = 2.0

# Cache: sport_key → (events, timestamp)
_cache: Dict[str, Tuple[List[OddsEvent], float]] = {}
_CACHE_TTL = 300.0  # 5 minutes

# ── Polymarket series_id lookup ──────────────────────────────────────────────

# Hardcoded fallback series IDs for tennis leagues.
# Discovered from Polymarket /sports endpoint.
_TENNIS_SERIES_IDS: Dict[str, int] = {}  # Populated at first use


def _find_tennis_series_id(league: str) -> Optional[int]:
    """Look up series_id for 'ATP' or 'WTA' from Polymarket /sports."""
    global _TENNIS_SERIES_IDS
    if _TENNIS_SERIES_IDS:
        return _TENNIS_SERIES_IDS.get(league.upper())

    try:
        _rate_limit()
        url = f"{GAMMA_HOST}/sports"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 BettingPodShop/1.0")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        log.warning("polymarket_odds: failed to fetch /sports: %s", exc)
        return None

    for entry in data:
        sport_abbrev = (entry.get("sport") or "").upper()
        series_val = entry.get("series", "")
        if sport_abbrev in ("ATP", "WTA"):
            try:
                _TENNIS_SERIES_IDS[sport_abbrev] = int(series_val)
            except (ValueError, TypeError):
                pass

    log.info("polymarket_odds: tennis series IDs = %s", _TENNIS_SERIES_IDS)
    return _TENNIS_SERIES_IDS.get(league.upper())


# ── VS title parser ──────────────────────────────────────────────────────────

# Matches "Tournament: Player1 vs Player2" or "Player1 vs. Player2"
_VS_RE = re.compile(
    r"(?:.*?:\s*)?(.+?)\s+vs\.?\s+(.+?)(?:\s*\(|$)",
    re.IGNORECASE,
)


def _parse_players(title: str) -> Optional[Tuple[str, str]]:
    """Extract two player names from a Polymarket event title.

    Returns (home_player, away_player) or None if parsing fails.
    Polymarket tennis titles typically look like:
      "Sao Paulo: Roman Andres Burruchaga vs Juan Carlos Prado"
      "Miami Open: Jannik Sinner vs Jiri Lehecka"
    """
    m = _VS_RE.search(title)
    if not m:
        return None
    p1 = m.group(1).strip().rstrip(".")
    p2 = m.group(2).strip().rstrip(".")
    if not p1 or not p2:
        return None
    return (p1, p2)


# ── Rate limiter ─────────────────────────────────────────────────────────────

def _rate_limit():
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


# ── Gamma API fetcher ────────────────────────────────────────────────────────

def _fetch_events(series_id: int, max_pages: int = 5) -> List[Dict[str, Any]]:
    """Fetch active events from Gamma API for a given series_id."""
    all_events = []
    offset = 0
    limit = 50

    for _ in range(max_pages):
        _rate_limit()
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(limit),
            "series_id": str(series_id),
        }
        if offset > 0:
            params["offset"] = str(offset)

        url = f"{GAMMA_HOST}/events?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "Mozilla/5.0 BettingPodShop/1.0")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            log.warning("polymarket_odds: fetch error: %s", exc)
            break

        if isinstance(data, dict):
            data = data.get("data", data.get("events", []))

        if not data:
            break

        all_events.extend(data)
        if len(data) < limit:
            break
        offset += len(data)

    return all_events


def _extract_price(event: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Extract YES probabilities for each side from a Polymarket event.

    Polymarket events have a 'markets' array.  For binary match-winner
    events, there's typically one market with outcomePrices = "[p1, p2]".

    Returns (player1_prob, player2_prob) or None.
    """
    markets = event.get("markets", [])
    for mkt in markets:
        prices_raw = mkt.get("outcomePrices")
        if not prices_raw:
            continue
        try:
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw
            if isinstance(prices, list) and len(prices) == 2:
                p1 = float(prices[0])
                p2 = float(prices[1])
                if 0.01 < p1 < 0.99 and 0.01 < p2 < 0.99:
                    return (p1, p2)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return None


def _event_start_time(event: Dict[str, Any]) -> Optional[datetime]:
    """Parse the event startDate into a timezone-aware datetime."""
    raw = event.get("startDate") or event.get("start_date")
    if not raw:
        return None
    try:
        # Polymarket uses ISO 8601 format
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── Public API ───────────────────────────────────────────────────────────────

def _sport_to_league(sport: str) -> Optional[str]:
    """Map Odds API sport key to Polymarket league name."""
    if "atp" in sport.lower():
        return "ATP"
    if "wta" in sport.lower():
        return "WTA"
    return None


def fetch_polymarket_tennis_events(sport: str) -> List[OddsEvent]:
    """Fetch tennis events from Polymarket and return as OddsEvent objects.

    Parameters
    ----------
    sport : str
        Odds API sport key, e.g. "tennis_atp_miami_open" or "tennis_wta".

    Returns
    -------
    List of OddsEvent objects with synthetic BookmakerOdds from Polymarket
    prices. Returns empty list if no events found or on error.
    """
    # Check cache
    now = time.monotonic()
    cached = _cache.get(sport)
    if cached and (now - cached[1]) < _CACHE_TTL:
        log.debug("polymarket_odds: cache hit for %s", sport)
        return cached[0]

    league = _sport_to_league(sport)
    if not league:
        return []

    series_id = _find_tennis_series_id(league)
    if not series_id:
        log.info("polymarket_odds: no series_id for %s/%s", sport, league)
        return []

    raw_events = _fetch_events(series_id)
    log.info(
        "polymarket_odds: fetched %d events for %s (series_id=%d)",
        len(raw_events), sport, series_id,
    )

    odds_events = []
    for ev in raw_events:
        title = ev.get("title", "")
        players = _parse_players(title)
        if not players:
            continue

        prices = _extract_price(ev)
        if not prices:
            continue

        home_player, away_player = players
        p1_prob, p2_prob = prices

        start_time = _event_start_time(ev) or datetime.now(timezone.utc)

        # Create a stable event_id from the title
        event_id = hashlib.md5(title.encode()).hexdigest()

        # Build synthetic BookmakerOdds from Polymarket prices.
        # Convert probability to American odds for compatibility:
        #   p > 0.5  → negative American odds: -100 * p / (1-p)
        #   p <= 0.5 → positive American odds: +100 * (1-p) / p
        def _prob_to_american(p: float) -> float:
            if p >= 0.99:
                return -10000.0
            if p <= 0.01:
                return 10000.0
            if p > 0.5:
                return round(-100.0 * p / (1.0 - p), 1)
            return round(100.0 * (1.0 - p) / p, 1)

        bookmaker = BookmakerOdds(
            bookmaker_key="polymarket",
            bookmaker_title="Polymarket",
            last_update=start_time,
            outcomes=[
                OddsOutcome(name=home_player, price=_prob_to_american(p1_prob)),
                OddsOutcome(name=away_player, price=_prob_to_american(p2_prob)),
            ],
        )

        odds_event = OddsEvent(
            event_id=event_id,
            sport_key=sport,
            commence_time=start_time,
            home_team=home_player,
            away_team=away_player,
            bookmaker_odds=[bookmaker],
        )
        odds_events.append(odds_event)

    _cache[sport] = (odds_events, now)
    log.info(
        "polymarket_odds: produced %d OddsEvents for %s",
        len(odds_events), sport,
    )
    return odds_events


def build_polymarket_consensus(event: OddsEvent) -> Optional[ConsensusProbability]:
    """Build a ConsensusProbability from a Polymarket-sourced OddsEvent.

    Since Polymarket is the only source, we use its prices directly as
    the consensus (no Pinnacle weighting or multi-book aggregation).
    """
    if not event.bookmaker_odds:
        return None

    bm = event.bookmaker_odds[0]
    if len(bm.outcomes) < 2:
        return None

    # Convert American odds back to implied probabilities
    def _american_to_prob(american: float) -> float:
        if american < 0:
            return abs(american) / (abs(american) + 100.0)
        return 100.0 / (american + 100.0)

    home_prob = _american_to_prob(bm.outcomes[0].price)
    away_prob = _american_to_prob(bm.outcomes[1].price)

    # Normalize to sum to 1.0
    total = home_prob + away_prob
    if total > 0:
        home_prob /= total
        away_prob /= total

    return ConsensusProbability(
        event_id=event.event_id,
        sport_key=event.sport_key,
        home_team=event.home_team,
        away_team=event.away_team,
        commence_time=event.commence_time,
        home_prob=home_prob,
        away_prob=away_prob,
        books_used=["polymarket"],
        method="polymarket_single",
    )
