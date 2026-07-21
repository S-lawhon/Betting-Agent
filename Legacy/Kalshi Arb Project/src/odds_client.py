"""
src/odds_client.py
──────────────────
The Odds API client with TTL caching and consensus probability computation.

Architecture
────────────
  _TTLCache            — simple dict-based TTL cache; injectable clock.
  OddsClient           — fetches events, caches raw API responses, and
                         computes consensus fair probabilities.

Caching strategy
────────────────
Raw API responses are cached per (sport_key, regions, markets) for
``cache_ttl_seconds`` (default 300 s = 5 min) to protect the monthly
request quota.  Cache keys are invalidated explicitly via
``invalidate_cache(sport)`` or cleared wholesale via ``clear_cache()``.

Consensus probability methods
──────────────────────────────
  weighted_sharp   — Pinnacle gets 2× weight; all other configured books
                     get 1×.  Falls back to median if Pinnacle absent.
  median           — Lower median of per-book fair probabilities.

For each book, vig is removed with the additive method from src/utils.py.

Monthly quota guardrails
────────────────────────
The Odds API returns ``x-requests-remaining`` and ``x-requests-used``
headers.  OddsClient logs a WARNING when fewer than 100 requests remain
and an ERROR when fewer than 20 remain.  It never blocks on quota — the
caller can decide whether to continue.

HTTP adapter protocol
─────────────────────
Same protocol as KalshiClient — injectable for tests::

    def request(method, url, headers, json_body, params, timeout) -> _OddsResponse
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from logger_setup import get_logger
from utils import remove_vig_additive, weighted_consensus_prob, median_prob

log = get_logger(__name__)

# Monthly quota warning thresholds
_QUOTA_WARN_LOW = 100
_QUOTA_WARN_CRITICAL = 20

# Pinnacle is the sharpest book — double weight in consensus
_SHARP_BOOK_WEIGHT = 2.0
_DEFAULT_BOOK_WEIGHT = 1.0


# ── Exceptions ────────────────────────────────────────────────────────────────

class OddsAPIError(Exception):
    """Non-retryable error from The Odds API."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {detail}")


class OddsQuotaExhaustedError(OddsAPIError):
    """Raised when the monthly API quota is fully exhausted (402 / 429)."""


# ── HTTP layer ────────────────────────────────────────────────────────────────

@dataclass
class _OddsResponse:
    status_code: int
    _body: bytes
    _headers: Dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self._body)

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Case-insensitive header lookup."""
        lower = {k.lower(): v for k, v in self._headers.items()}
        return lower.get(name.lower(), default)


class _UrllibOddsAdapter:
    """Stdlib urllib HTTP adapter for The Odds API."""

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_body: Any = None,
        params: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> _OddsResponse:
        if params:
            url = url + "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        h = dict(headers or {})
        body: Optional[bytes] = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            h["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=h, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _OddsResponse(
                    status_code=resp.status,
                    _body=resp.read(),
                    _headers=dict(resp.headers),
                )
        except urllib.error.HTTPError as exc:
            return _OddsResponse(
                status_code=exc.code,
                _body=exc.read(),
                _headers=dict(exc.headers),
            )


# ── TTL Cache ─────────────────────────────────────────────────────────────────

class _TTLCache:
    """
    Simple in-memory TTL cache.

    Parameters
    ----------
    ttl_seconds : float
        Entries older than this are considered stale.
    _now_fn : callable, optional
        Returns current time in seconds (override in tests).
    """

    def __init__(
        self,
        ttl_seconds: float,
        _now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._store: Dict[str, Tuple[Any, float]] = {}
        self._now = _now_fn or time.monotonic

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if self._now() - ts >= self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, self._now())

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    def age_seconds(self, key: str) -> Optional[float]:
        """Return seconds since the entry was cached, or None if absent/stale."""
        entry = self._store.get(key)
        if entry is None:
            return None
        _, ts = entry
        age = self._now() - ts
        return age if age < self._ttl else None


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class OddsOutcome:
    """One side of a two-outcome market from a bookmaker."""
    name: str           # team name as returned by the API
    price: float        # American odds (e.g. -165 or +145)


@dataclass
class BookmakerOdds:
    """Odds from a single bookmaker for a single event."""
    bookmaker_key: str
    bookmaker_title: str
    last_update: datetime
    outcomes: List[OddsOutcome]   # always 2 for h2h markets


@dataclass
class OddsEvent:
    """One event from The Odds API with odds from multiple bookmakers."""
    event_id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmaker_odds: List[BookmakerOdds]


@dataclass
class FairOdds:
    """Devigged (fair) probabilities from one bookmaker."""
    bookmaker_key: str
    home_prob: float    # probability home team wins
    away_prob: float    # 1 - home_prob (stored explicitly for clarity)


@dataclass
class ConsensusProbability:
    """
    Consensus fair probability for one event, aggregated across books.

    Probabilities are from the home team's perspective:
        ``home_prob`` = P(home team wins)
        ``away_prob`` = 1 − home_prob
    """
    event_id: str
    sport_key: str
    home_team: str
    away_team: str
    commence_time: datetime
    home_prob: float
    away_prob: float
    books_used: List[str]
    method: str             # "weighted_sharp" | "median"
    requests_remaining: Optional[int] = None


# ── Odds Client ───────────────────────────────────────────────────────────────

class OddsClient:
    """
    The Odds API client with TTL caching and consensus probability computation.

    Parameters
    ----------
    api_key : str
        From ``ODDS_API_KEY`` env var.
    base_url : str
        API base URL (default: https://api.the-odds-api.com/v4).
    cache_ttl_seconds : float
        How long to cache raw API responses (default: 300 s).
    priority_books : list of str
        Bookmaker keys in priority order.  First entry gets 2× weight if
        it is Pinnacle (``"pinnacle"``); all others get 1×.
    sports : list of str
        Restrict to these sport keys.
    adapter : optional
        Injectable HTTP adapter for tests.
    _cache_now_fn : callable, optional
        Clock for TTL cache (override in tests).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.the-odds-api.com/v4",
        cache_ttl_seconds: float = 300.0,
        priority_books: Optional[List[str]] = None,
        sports: Optional[List[str]] = None,
        adapter: Optional[Any] = None,
        _cache_now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._priority_books = priority_books or [
            "pinnacle", "draftkings", "fanduel", "betmgm"
        ]
        self._sports = sports or []
        self._adapter = adapter or _UrllibOddsAdapter()
        self._cache = _TTLCache(cache_ttl_seconds, _now_fn=_cache_now_fn)
        self._requests_remaining: Optional[int] = None
        self._requests_used: Optional[int] = None

    @classmethod
    def from_config(
        cls,
        config: dict,
        api_key: str,
        **kwargs,
    ) -> "OddsClient":
        """Construct from loaded config.yaml dict."""
        odds_cfg = config.get("odds_api", {})
        return cls(
            api_key=api_key,
            base_url=odds_cfg.get("base_url", "https://api.the-odds-api.com/v4"),
            cache_ttl_seconds=odds_cfg.get("cache_ttl_seconds", 300.0),
            priority_books=odds_cfg.get("priority_books"),
            sports=odds_cfg.get("sports"),
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_events(self, sport: str) -> List[OddsEvent]:
        """
        Return parsed odds events for a sport, using the TTL cache.

        Parameters
        ----------
        sport : str
            Sport key, e.g. ``"americanfootball_nfl"``.

        Returns
        -------
        List of OddsEvent objects.
        """
        cache_key = f"odds:{sport}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.debug("odds_cache_hit", sport=sport, age_s=self._cache.age_seconds(cache_key))
            return cached

        raw_events = self._fetch_odds(sport)
        events = [self._parse_event(e) for e in raw_events]
        self._cache.set(cache_key, events)
        log.info("odds_fetched", sport=sport, event_count=len(events),
                 requests_remaining=self._requests_remaining)
        return events

    def get_consensus(self, event: OddsEvent) -> Optional[ConsensusProbability]:
        """
        Compute consensus fair probability for one event.

        Uses the weighted-sharp method when Pinnacle is available;
        falls back to the median across all configured books otherwise.

        Parameters
        ----------
        event : OddsEvent

        Returns
        -------
        ConsensusProbability or None if no configured books have odds.
        """
        fair_list = self._devig_all_books(event)
        if not fair_list:
            log.warning("odds_no_books", event_id=event.event_id,
                        home=event.home_team, away=event.away_team)
            return None

        books_used = [f.bookmaker_key for f in fair_list]
        home_probs = [f.home_prob for f in fair_list]

        pinnacle_key = "pinnacle"
        has_pinnacle = any(f.bookmaker_key == pinnacle_key for f in fair_list)

        if has_pinnacle:
            weights = [
                _SHARP_BOOK_WEIGHT if f.bookmaker_key == pinnacle_key
                else _DEFAULT_BOOK_WEIGHT
                for f in fair_list
            ]
            home_prob = weighted_consensus_prob(home_probs, weights)
            method = "weighted_sharp"
        else:
            home_prob = median_prob(home_probs)
            method = "median"

        home_prob = max(0.001, min(0.999, home_prob))   # clamp to (0,1)
        away_prob = 1.0 - home_prob

        return ConsensusProbability(
            event_id=event.event_id,
            sport_key=event.sport_key,
            home_team=event.home_team,
            away_team=event.away_team,
            commence_time=event.commence_time,
            home_prob=home_prob,
            away_prob=away_prob,
            books_used=books_used,
            method=method,
            requests_remaining=self._requests_remaining,
        )

    def get_all_consensus(self, sport: str) -> List[ConsensusProbability]:
        """
        Fetch events and compute consensus for every event in a sport.

        Returns only events where at least one configured book has odds.
        """
        events = self.get_events(sport)
        results = []
        for event in events:
            cp = self.get_consensus(event)
            if cp is not None:
                results.append(cp)
        return results

    def invalidate_cache(self, sport: Optional[str] = None) -> None:
        """Invalidate the cache for one sport (or everything if sport is None)."""
        if sport:
            self._cache.invalidate(f"odds:{sport}")
        else:
            self._cache.clear()

    @property
    def requests_remaining(self) -> Optional[int]:
        """Last known monthly request quota remaining."""
        return self._requests_remaining

    @property
    def requests_used(self) -> Optional[int]:
        """Last known monthly request count used."""
        return self._requests_used

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_odds(self, sport: str) -> List[Dict[str, Any]]:
        """
        Make one API call to /v4/sports/{sport}/odds.

        Updates quota tracking from response headers.

        Raises
        ------
        OddsQuotaExhaustedError
            On 402 / 429 status.
        OddsAPIError
            On other non-200 status codes.
        """
        url = f"{self._base_url}/sports/{sport}/odds"
        params: Dict[str, str] = {
            "apiKey": self._api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
            # Do NOT filter by bookmakers here — mid-major college games are often
            # absent from Pinnacle/DraftKings/FanDuel, which would return 0 events.
            # Priority-book filtering is applied downstream in _parse_event() when
            # computing the consensus probability.
        }

        resp = self._adapter.request("GET", url, params=params)
        self._update_quota(resp)

        if resp.status_code in (402, 429):
            raise OddsQuotaExhaustedError(
                resp.status_code, "Monthly Odds API quota exhausted."
            )
        if resp.status_code != 200:
            try:
                detail = resp.json().get("message", resp._body[:200].decode())
            except Exception:
                detail = resp._body[:200].decode(errors="replace")
            raise OddsAPIError(resp.status_code, detail)

        return resp.json()

    def _update_quota(self, resp: _OddsResponse) -> None:
        """Parse quota headers and emit warnings if quota is low."""
        remaining_str = resp.header("x-requests-remaining")
        used_str = resp.header("x-requests-used")

        if remaining_str is not None:
            try:
                self._requests_remaining = int(remaining_str)
            except ValueError:
                pass
        if used_str is not None:
            try:
                self._requests_used = int(used_str)
            except ValueError:
                pass

        if self._requests_remaining is not None:
            if self._requests_remaining < _QUOTA_WARN_CRITICAL:
                log.error(
                    "odds_quota_critical",
                    requests_remaining=self._requests_remaining,
                    detail="Odds API monthly quota critically low — consider pausing.",
                )
            elif self._requests_remaining < _QUOTA_WARN_LOW:
                log.warning(
                    "odds_quota_low",
                    requests_remaining=self._requests_remaining,
                    detail="Odds API monthly quota running low.",
                )

    def _parse_event(self, raw: Dict[str, Any]) -> OddsEvent:
        """Parse one raw API event dict into an OddsEvent."""
        bookmaker_odds: List[BookmakerOdds] = []

        for bm in raw.get("bookmakers", []):
            bm_key = bm.get("key", "")
            if self._priority_books and bm_key not in self._priority_books:
                continue

            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = [
                    OddsOutcome(name=o["name"], price=float(o["price"]))
                    for o in market.get("outcomes", [])
                    if "name" in o and "price" in o
                ]
                if len(outcomes) != 2:
                    continue  # skip non-binary markets

                last_update_str = bm.get("last_update") or market.get("last_update", "")
                try:
                    last_update = datetime.fromisoformat(
                        last_update_str.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    last_update = datetime.now(tz=timezone.utc)

                bookmaker_odds.append(BookmakerOdds(
                    bookmaker_key=bm_key,
                    bookmaker_title=bm.get("title", bm_key),
                    last_update=last_update,
                    outcomes=outcomes,
                ))

        commence_str = raw.get("commence_time", "")
        try:
            commence_time = datetime.fromisoformat(
                commence_str.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            commence_time = datetime.now(tz=timezone.utc)

        return OddsEvent(
            event_id=raw.get("id", ""),
            sport_key=raw.get("sport_key", ""),
            commence_time=commence_time,
            home_team=raw.get("home_team", ""),
            away_team=raw.get("away_team", ""),
            bookmaker_odds=bookmaker_odds,
        )

    def _devig_all_books(self, event: OddsEvent) -> List[FairOdds]:
        """
        Devig odds from all available (and configured) books for an event.

        Returns only books that have exactly 2 outcomes for the h2h market.
        """
        results: List[FairOdds] = []

        for bm in event.bookmaker_odds:
            if len(bm.outcomes) != 2:
                continue

            o1, o2 = bm.outcomes
            # Identify which outcome is the home team
            home_outcome = o1 if o1.name == event.home_team else o2
            away_outcome = o2 if o1.name == event.home_team else o1

            from utils import american_to_implied_prob
            home_implied = american_to_implied_prob(home_outcome.price)
            away_implied = american_to_implied_prob(away_outcome.price)

            fair_home, fair_away = remove_vig_additive([home_implied, away_implied])
            results.append(FairOdds(
                bookmaker_key=bm.bookmaker_key,
                home_prob=fair_home,
                away_prob=fair_away,
            ))

        return results
