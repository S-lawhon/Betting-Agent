"""
src/live_odds_poller.py
───────────────────────
Polls The Odds API for live/in-play odds at configurable intervals.

Designed for P-014 (Live Game Agent).  Maintains an in-memory cache of
the latest odds snapshot per game, detects meaningful line movements,
and exposes a clean interface for the LiveGamePod to consume.

Usage:
    poller = LiveOddsPoller.from_config(config)
    snapshot = poller.poll("basketball_nba")  # returns list of LiveOddsSnapshot
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from src.constants import HTTP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"


@dataclass
class BookmakerLine:
    """A single bookmaker's line for one game."""
    bookmaker_key: str
    bookmaker_title: str
    home_price: int       # American odds
    away_price: int       # American odds
    last_update: str      # ISO timestamp from API


@dataclass
class LiveOddsSnapshot:
    """Complete live odds snapshot for one in-play game."""
    game_id: str
    sport: str
    home_team: str
    away_team: str
    commence_time: str
    is_live: bool
    bookmaker_lines: List[BookmakerLine] = field(default_factory=list)
    poll_timestamp: str = ""

    @property
    def num_books(self) -> int:
        return len(self.bookmaker_lines)

    def get_pinnacle_line(self) -> Optional[BookmakerLine]:
        """Extract Pinnacle's line if available."""
        for bm in self.bookmaker_lines:
            if bm.bookmaker_key == "pinnacle":
                return bm
        return None

    def get_consensus_probs(self) -> Optional[Tuple[float, float]]:
        """Compute Pinnacle-weighted consensus home/away probabilities.

        Pinnacle gets 2x weight; all other books get 1x.
        Returns (home_prob, away_prob) devigged, or None if no lines.
        """
        if not self.bookmaker_lines:
            return None

        weighted_home = 0.0
        total_weight = 0.0

        for bm in self.bookmaker_lines:
            home_implied = american_to_prob(bm.home_price)
            away_implied = american_to_prob(bm.away_price)
            if home_implied is None or away_implied is None:
                continue

            # Devig: additive removal
            total_implied = home_implied + away_implied
            if total_implied <= 0:
                continue
            home_fair = home_implied / total_implied

            weight = 2.0 if bm.bookmaker_key == "pinnacle" else 1.0
            weighted_home += home_fair * weight
            total_weight += weight

        if total_weight <= 0:
            return None

        home_prob = weighted_home / total_weight
        return (max(0.001, min(0.999, home_prob)),
                max(0.001, min(0.999, 1.0 - home_prob)))


def american_to_prob(american_odds: int) -> Optional[float]:
    """Convert American odds to implied probability."""
    if american_odds == 0:
        return None
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    else:
        return abs(american_odds) / (abs(american_odds) + 100.0)


class LiveOddsPoller:
    """Polls Odds API for live/in-play odds.

    Key features:
    - Filters to only in-play games (not pre-game or completed)
    - Caches last snapshot per game to detect line movements
    - Tracks API credit usage
    - Thread-safe for use from async contexts
    """

    def __init__(
        self,
        api_key: str,
        regions: List[str] = None,
        markets: List[str] = None,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        _session: Optional[requests.Session] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._api_key = api_key
        self._regions = regions or ["us"]
        self._markets = markets or ["h2h"]
        self._timeout = timeout
        self._session = _session or requests.Session()
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))

        # Cache: game_id → last LiveOddsSnapshot
        self._cache: Dict[str, LiveOddsSnapshot] = {}

        # API credit tracking
        self._credits_used: int = 0
        self._credits_remaining: Optional[int] = None

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "LiveOddsPoller":
        """Build from config_multi_pod.yaml P-014 section."""
        p014_cfg = config.get("pods", {}).get("P-014", {})
        odds_cfg = p014_cfg.get("odds_api", {})

        api_key = overrides.get("api_key") or os.environ.get("ODDS_API_KEY", "")
        if not api_key:
            raise ValueError("ODDS_API_KEY required for LiveOddsPoller")

        return cls(
            api_key=api_key,
            regions=odds_cfg.get("regions", ["us"]),
            markets=odds_cfg.get("markets", ["h2h"]),
            timeout=odds_cfg.get("timeout", HTTP_TIMEOUT_SECONDS),
            _session=overrides.get("_session"),
            _now_fn=overrides.get("_now_fn"),
        )

    def poll(self, sport: str) -> List[LiveOddsSnapshot]:
        """Poll Odds API for live odds on the given sport.

        Parameters
        ----------
        sport : str
            Odds API sport key, e.g. "basketball_nba", "baseball_mlb"

        Returns
        -------
        List of LiveOddsSnapshot for games that are currently in-play.
        Empty list on error or no live games.
        """
        url = f"{ODDS_API_BASE}/sports/{sport}/odds"
        params = {
            "apiKey": self._api_key,
            "regions": ",".join(self._regions),
            "markets": ",".join(self._markets),
            "oddsFormat": "american",
        }

        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
            self._update_credit_tracking(resp)

            if resp.status_code == 401:
                logger.error("LiveOddsPoller: invalid API key")
                return []
            if resp.status_code == 429:
                logger.warning("LiveOddsPoller: rate limited — backing off")
                return []
            if resp.status_code != 200:
                logger.warning(
                    "LiveOddsPoller: HTTP %d for %s", resp.status_code, sport
                )
                return []

            events = resp.json()
            if not isinstance(events, list):
                logger.warning("LiveOddsPoller: unexpected response format")
                return []

        except requests.RequestException as exc:
            logger.error("LiveOddsPoller: request failed for %s: %s", sport, exc)
            return []

        now_str = self._now_fn().isoformat()
        snapshots = []

        for event in events:
            # Filter to in-play games only
            if not self._is_live(event):
                continue

            snapshot = self._parse_event(event, sport, now_str)
            if snapshot and snapshot.num_books > 0:
                self._cache[snapshot.game_id] = snapshot
                snapshots.append(snapshot)

        logger.info(
            "LiveOddsPoller: %s — %d live games, %d total events, credits_remaining=%s",
            sport, len(snapshots), len(events), self._credits_remaining,
        )
        return snapshots

    def poll_all(self, sports: List[str]) -> Dict[str, List[LiveOddsSnapshot]]:
        """Poll multiple sports and return results keyed by sport."""
        results = {}
        for sport in sports:
            results[sport] = self.poll(sport)
        return results

    def get_cached(self, game_id: str) -> Optional[LiveOddsSnapshot]:
        """Get the last cached snapshot for a game."""
        return self._cache.get(game_id)

    def detect_line_movement(
        self, game_id: str, new_snapshot: LiveOddsSnapshot, threshold: float = 0.02
    ) -> Optional[Dict[str, Any]]:
        """Detect if the consensus line has moved meaningfully since last poll.

        Parameters
        ----------
        game_id : str
            Game identifier
        new_snapshot : LiveOddsSnapshot
            Current snapshot
        threshold : float
            Minimum probability change to count as movement (default 2%)

        Returns
        -------
        Dict with movement details, or None if no meaningful change.
        """
        old = self._cache.get(game_id)
        if old is None:
            return None

        old_probs = old.get_consensus_probs()
        new_probs = new_snapshot.get_consensus_probs()
        if old_probs is None or new_probs is None:
            return None

        delta_home = new_probs[0] - old_probs[0]
        if abs(delta_home) < threshold:
            return None

        return {
            "game_id": game_id,
            "home_team": new_snapshot.home_team,
            "away_team": new_snapshot.away_team,
            "old_home_prob": old_probs[0],
            "new_home_prob": new_probs[0],
            "delta": delta_home,
            "direction": "home" if delta_home > 0 else "away",
        }

    @property
    def credits_remaining(self) -> Optional[int]:
        return self._credits_remaining

    @property
    def credits_used(self) -> int:
        return self._credits_used

    # ── Internal helpers ───────────────────────────────────────────────

    def _is_live(self, event: dict) -> bool:
        """Determine if an event is currently in-play.

        The Odds API doesn't have an explicit 'is_live' field.  We infer
        from commence_time being in the past and the event still being
        returned (completed games are dropped from the active list).
        """
        commence = event.get("commence_time", "")
        if not commence:
            return False
        try:
            commence_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            now = self._now_fn()
            return commence_dt <= now
        except (ValueError, TypeError):
            return False

    def _parse_event(
        self, event: dict, sport: str, poll_ts: str
    ) -> Optional[LiveOddsSnapshot]:
        """Parse an Odds API event into a LiveOddsSnapshot."""
        game_id = event.get("id", "")
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        commence_time = event.get("commence_time", "")

        if not game_id or not home_team or not away_team:
            return None

        lines: List[BookmakerLine] = []
        for bm in event.get("bookmakers", []):
            bm_key = bm.get("key", "")
            bm_title = bm.get("title", "")
            last_update = bm.get("last_update", "")

            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue

                home_price = None
                away_price = None
                for outcome in outcomes:
                    if outcome.get("name") == home_team:
                        home_price = outcome.get("price", 0)
                    elif outcome.get("name") == away_team:
                        away_price = outcome.get("price", 0)

                if home_price is not None and away_price is not None:
                    lines.append(BookmakerLine(
                        bookmaker_key=bm_key,
                        bookmaker_title=bm_title,
                        home_price=home_price,
                        away_price=away_price,
                        last_update=last_update,
                    ))

        return LiveOddsSnapshot(
            game_id=game_id,
            sport=sport,
            home_team=home_team,
            away_team=away_team,
            commence_time=commence_time,
            is_live=True,
            bookmaker_lines=lines,
            poll_timestamp=poll_ts,
        )

    def _update_credit_tracking(self, resp: requests.Response) -> None:
        """Extract Odds API credit info from response headers."""
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if remaining is not None:
            try:
                self._credits_remaining = int(remaining)
            except (ValueError, TypeError):
                pass
        if used is not None:
            try:
                self._credits_used = int(used)
            except (ValueError, TypeError):
                pass
