"""
src/mlb_statsapi.py
───────────────────
Free MLB StatsAPI client for P-016 (Live Maker).

statsapi.mlb.com requires no auth.  We poll:
  /api/v1/schedule  — today's games, status, teams (with abbreviations)
  /api/v1/game/{gamePk}/linescore — inning, half, outs, bases, score

The feed lags reality by a few seconds; P-016 quotes (not races), so the
lag is acceptable — but quote-pulling logic must assume we are behind.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE = "https://statsapi.mlb.com/api/v1"

# StatsAPI team abbreviation → Kalshi ticker code, where they differ.
# Verified live 2026-07-20 across a full 15-game slate: Kalshi uses the
# StatsAPI abbreviations verbatim (incl. WSH, AZ, ATH, CWS, CHC).
KALSHI_CODE_OVERRIDES: Dict[str, str] = {}


@dataclass
class MlbGame:
    game_pk: int
    away_name: str
    home_name: str
    away_abbr: str
    home_abbr: str
    start_utc: str            # ISO timestamp
    status: str               # Preview / Live / Final
    detailed_state: str = ""


@dataclass
class MlbLiveState:
    game_pk: int
    home_score: int
    away_score: int
    inning: int
    is_top: bool
    outs: int
    on_first: bool
    on_second: bool
    on_third: bool
    is_final: bool
    fetched_at: float = field(default_factory=time.time)

    def state_key(self) -> str:
        """Compact fingerprint of the tradable game state — any change
        here is a 'material event' for quote management."""
        return (f"{self.away_score}-{self.home_score}|i{self.inning}"
                f"{'T' if self.is_top else 'B'}|o{self.outs}"
                f"|b{int(self.on_first)}{int(self.on_second)}{int(self.on_third)}")


class MlbStatsApi:
    def __init__(self, timeout: float = 10.0, session=None):
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": "betting-pod-shop/P-016-live-maker"}
        )

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            resp = self._session.get(
                f"{BASE}{path}", params=params, timeout=self._timeout
            )
            if resp.status_code != 200:
                logger.warning("statsapi %s -> HTTP %s", path, resp.status_code)
                return None
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("statsapi %s failed: %s", path, exc)
            return None

    def schedule(self, date_str: str) -> List[MlbGame]:
        """Games for a date (YYYY-MM-DD), with team abbreviations."""
        data = self._get("/schedule", {
            "sportId": 1, "date": date_str, "hydrate": "team",
        })
        games: List[MlbGame] = []
        if not data:
            return games
        for day in data.get("dates", []):
            for g in day.get("games", []):
                try:
                    away = g["teams"]["away"]["team"]
                    home = g["teams"]["home"]["team"]
                    games.append(MlbGame(
                        game_pk=g["gamePk"],
                        away_name=away.get("name", ""),
                        home_name=home.get("name", ""),
                        away_abbr=away.get("abbreviation", ""),
                        home_abbr=home.get("abbreviation", ""),
                        start_utc=g.get("gameDate", ""),
                        status=(g.get("status") or {}).get(
                            "abstractGameState", ""),
                        detailed_state=(g.get("status") or {}).get(
                            "detailedState", ""),
                    ))
                except (KeyError, TypeError):
                    continue
        return games

    def linescore(self, game_pk: int,
                  status_hint: str = "") -> Optional[MlbLiveState]:
        """Current live state for one game."""
        data = self._get(f"/game/{game_pk}/linescore")
        if not data:
            return None
        teams = data.get("teams") or {}
        home_runs = ((teams.get("home") or {}).get("runs"))
        away_runs = ((teams.get("away") or {}).get("runs"))
        offense = data.get("offense") or {}
        inning_state = (data.get("inningState") or "").lower()
        # inningState: Top / Bottom / Middle / End.  Between halves
        # ("middle"/"end") treat as the upcoming half with 0 outs, bases empty.
        if inning_state == "middle":
            is_top, outs, bases_clear = False, 0, True
        elif inning_state == "end":
            is_top, outs, bases_clear = True, 0, True
        else:
            is_top = data.get("isTopInning", True)
            outs = int(data.get("outs") or 0)
            if outs >= 3:      # side retired but state not rolled yet
                outs, bases_clear = 0, True
                is_top = not is_top
            else:
                bases_clear = False
        inning = int(data.get("currentInning") or 1)
        if inning_state == "end":
            inning += 1

        return MlbLiveState(
            game_pk=game_pk,
            home_score=int(home_runs or 0),
            away_score=int(away_runs or 0),
            inning=max(inning, 1),
            is_top=bool(is_top),
            outs=outs,
            on_first=(not bases_clear) and bool(offense.get("first")),
            on_second=(not bases_clear) and bool(offense.get("second")),
            on_third=(not bases_clear) and bool(offense.get("third")),
            is_final=(status_hint == "Final"),
        )


def kalshi_team_code(abbr: str) -> str:
    """Map a StatsAPI team abbreviation to the Kalshi ticker code."""
    a = (abbr or "").upper()
    return KALSHI_CODE_OVERRIDES.get(a, a)
