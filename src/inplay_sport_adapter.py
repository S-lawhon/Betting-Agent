"""
src/inplay_sport_adapter.py
───────────────────────────
P-018 sport adapter — the pluggable layer between the sport-agnostic
surprise gate (``src/inplay_surprise.py``) and a specific sport's live
state feed + win-probability model.

Why an adapter and not just "call mlb_win_prob": P-018 is meant to
generalize across sports (MLB first, NBA second — spec §7).  The gate is
identical for every sport; what differs is (a) how you poll live state,
(b) the win-prob model, (c) which observed states carry *unresolved event
risk* (pull, don't quote), and (d) the Kalshi series + market discovery.
Those four things are exactly the ``SportAdapter`` surface.

MlbAdapter reuses the P-016 stack verbatim (``mlb_statsapi`` +
``mlb_win_prob``) — nothing is re-modelled.  NbaAdapter is a stub: it
needs an NBA win-prob model that does not exist yet (spec §7), so it
raises rather than pretending.

EVENT-RISK LIMITATION (documented, not silent):
    The MLB free linescore feed carries inning / half / outs / bases /
    score — but NOT the ball-strike count or a "pitch in flight" flag.
    So ``event_risk`` here covers the states linescore CAN see: a runner
    in scoring position with < 2 outs, and any at-bat that can plate the
    tying or lead run.  The count-based triggers the spec lists (2-strike
    / 3-ball) and pitch-in-flight need the richer play-by-play / live
    feed (``/game/{pk}/playByPlay``); wiring that is a follow-up flagged
    in the spec §3 note and NOT faked here.  Under-covering event risk is
    the unsafe direction, so this is called out loudly for the pilot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol, Tuple, runtime_checkable

from src.kalshi_public import KalshiPublic, fnum
from src.mlb_statsapi import (MlbGame, MlbLiveState, MlbStatsApi,
                              kalshi_team_code)
from src.mlb_win_prob import (MlbGameSnapshot, home_win_probability,
                              implied_pregame_prob)

logger = logging.getLogger(__name__)


@dataclass
class AdapterState:
    """Sport-agnostic snapshot the gate consumes.

    ``state_key`` is the discrete-event fingerprint (any change = a
    material in-play event).  ``event_risk`` is True when the informed
    side can run a maker over right now — the gate PULLS on it.  ``winner``
    is 1.0 (home/YES) or 0.0 once ``is_final``.
    """
    game_id: int
    state_key: str
    fair: float
    sensitivity: float
    is_final: bool
    event_risk: bool
    winner: Optional[float] = None       # 1.0 home wins / 0.0 away, when final
    mid_hint: Optional[float] = None     # market mid if the adapter fetched it


@runtime_checkable
class SportAdapter(Protocol):
    """The four things that differ per sport.  Deliberately NOT tied to
    ``BasePod``; P-018 runs its own fast loop like P-016."""

    sport: str
    series_ticker: str      # Kalshi series for fee lookup + discovery

    def discover(self, date_str: str) -> List[Tuple[int, str]]:
        """Return (game_id, home_yes_ticker) pairs tradable today."""
        ...

    def pregame_anchor(self, game_id: int, ticker: str,
                       live_mid: Optional[float]) -> Tuple[float, str]:
        """Resolve the pregame win-prob prior for a book (+ a source tag)."""
        ...

    def poll(self, game_id: int, ticker: str,
             pregame_prob: float) -> Optional[AdapterState]:
        """Fetch live state + model fair for one game, or None if unavailable."""
        ...


# ══════════════════════════════════════════════════════════════════════
# MLB
# ══════════════════════════════════════════════════════════════════════

def mlb_event_risk(state: MlbLiveState) -> bool:
    """Unresolved event risk visible from the linescore (see module note).

    True when a single pitch can move true value a lot AND an informed
    trader is likely to see it first:
      * runner in scoring position (2nd or 3rd) with fewer than 2 outs; or
      * any at-bat that can plate the tying or lead run — a runner on with
        the batting team within one run (or tied).

    Returns False for the low-leverage majority of at-bats, where quoting
    is the whole point of the pod.
    """
    if state.is_final:
        return False
    risp = (state.on_second or state.on_third) and state.outs < 2
    runner_on = state.on_first or state.on_second or state.on_third
    margin = abs(state.home_score - state.away_score)
    close_and_pressured = runner_on and margin <= 1
    return bool(risp or close_and_pressured)


class MlbAdapter:
    """SportAdapter for MLB — reuses P-016's StatsAPI + win-prob stack."""

    sport = "mlb"
    series_ticker = "KXMLBGAME"

    def __init__(self, statsapi: Optional[MlbStatsApi] = None,
                 kalshi: Optional[KalshiPublic] = None):
        self.statsapi = statsapi or MlbStatsApi()
        self.kalshi = kalshi or KalshiPublic()

    # ── discovery (mirrors LiveMakerEngine.discover matching) ─────────

    @staticmethod
    def _et_hhmm(start_utc: str) -> Optional[str]:
        try:
            dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.astimezone(timezone(timedelta(hours=-4))).strftime("%H%M")

    @staticmethod
    def _et_datecode(start_utc: str) -> Optional[str]:
        try:
            dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        except ValueError:
            return None
        et = dt.astimezone(timezone(timedelta(hours=-4)))
        return et.strftime("%y%b%d").upper()

    def _match_ticker(self, g: MlbGame, markets: List[dict]) -> Optional[str]:
        datecode = self._et_datecode(g.start_utc)
        hhmm = self._et_hhmm(g.start_utc)
        if not datecode or not hhmm:
            return None
        home_code = kalshi_team_code(g.home_abbr)
        away_code = kalshi_team_code(g.away_abbr)
        event_frag = f"{datecode}{hhmm}{away_code}{home_code}"
        loose_frag = f"{away_code}{home_code}"
        for m in markets:
            tk = m.get("ticker", "")
            if event_frag in tk and tk.endswith(f"-{home_code}"):
                return tk
        for m in markets:  # start-time drift fallback: date + team pair
            tk = m.get("ticker", "")
            if datecode in tk and loose_frag in tk and tk.endswith(f"-{home_code}"):
                return tk
        return None

    def discover(self, date_str: str) -> List[Tuple[int, str]]:
        games = self.statsapi.schedule(date_str)
        if not games:
            return []
        markets = self.kalshi.open_markets(self.series_ticker)
        if not markets:
            return []
        out: List[Tuple[int, str]] = []
        for g in games:
            tk = self._match_ticker(g, markets)
            if tk:
                out.append((g.game_pk, tk))
            else:
                logger.info("P-018/MLB: no market for %s @ %s",
                            g.away_abbr, g.home_abbr)
        return out

    def pregame_anchor(self, game_id: int, ticker: str,
                       live_mid: Optional[float]) -> Tuple[float, str]:
        """Recover the pregame prior.  If the game is already in progress,
        invert the model against the live mid (never pass a live mid in as
        a "pregame" prior — that double-counts the score, the 8-17pp bug
        P-016 documents).  Falls back to a league-ish 0.54 home edge."""
        ls = self.statsapi.linescore(game_id)
        if ls is not None and not ls.is_final and live_mid is not None:
            snap = _snap(ls)
            # A pristine pregame state (top 1, no outs, bases empty, tied)
            # means the live mid IS the pregame prior.
            pristine = (ls.inning == 1 and ls.is_top and ls.outs == 0
                        and not (ls.on_first or ls.on_second or ls.on_third)
                        and ls.home_score == ls.away_score == 0)
            if pristine:
                return live_mid, "kalshi_pregame_mid"
            return implied_pregame_prob(snap, live_mid), "implied_from_live_mid"
        if live_mid is not None:
            return live_mid, "kalshi_pregame_mid"
        return 0.54, "default_0.54"

    def poll(self, game_id: int, ticker: str,
             pregame_prob: float) -> Optional[AdapterState]:
        ls = self.statsapi.linescore(game_id)
        if ls is None:
            return None
        snap = _snap(ls)
        if ls.is_final:
            return AdapterState(
                game_id=game_id, state_key=ls.state_key(),
                fair=1.0 if ls.home_score > ls.away_score else 0.0,
                sensitivity=0.0, is_final=True, event_risk=False,
                winner=1.0 if ls.home_score > ls.away_score else 0.0,
            )
        fv = home_win_probability(snap, pregame_prob)
        ob = self.kalshi.orderbook(ticker)
        mid = _mid(ob)
        return AdapterState(
            game_id=game_id, state_key=ls.state_key(),
            fair=fv.home_wp, sensitivity=fv.sensitivity,
            is_final=False, event_risk=mlb_event_risk(ls), mid_hint=mid,
        )


# ══════════════════════════════════════════════════════════════════════
# NBA (stub — needs a win-prob model, spec §7)
# ══════════════════════════════════════════════════════════════════════

class NbaAdapter:
    """Placeholder so the engine can enumerate sports and fail loudly.

    A real NBA adapter needs (1) a live NBA win-probability model and (2) a
    play-clock-aware ``event_risk`` (possession in progress in the final
    3 min / free throws pending / after-timeout inbound).  Neither exists
    in the repo yet — do NOT ship NBA until both land and MLB clears gate
    #1 (spec §8).
    """

    sport = "nba"
    series_ticker = "KXNBA"

    def discover(self, date_str: str) -> List[Tuple[int, str]]:
        raise NotImplementedError(
            "NbaAdapter: no NBA win-prob model yet (spec §7). MLB must "
            "clear gate #1 before NBA is built.")

    def pregame_anchor(self, game_id: int, ticker: str,
                       live_mid: Optional[float]) -> Tuple[float, str]:
        raise NotImplementedError("NbaAdapter: not implemented (spec §7).")

    def poll(self, game_id: int, ticker: str,
             pregame_prob: float) -> Optional[AdapterState]:
        raise NotImplementedError("NbaAdapter: not implemented (spec §7).")


# ══════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════

def _snap(ls: MlbLiveState) -> MlbGameSnapshot:
    return MlbGameSnapshot(
        home_score=ls.home_score, away_score=ls.away_score,
        inning=ls.inning, is_top=ls.is_top, outs=ls.outs,
        on_first=ls.on_first, on_second=ls.on_second,
        on_third=ls.on_third, is_final=ls.is_final,
    )


def _mid(ob: Optional[dict]) -> Optional[float]:
    if not ob:
        return None
    b, a = ob.get("yes_bid"), ob.get("yes_ask")
    if b is None or a is None or not (0 < b <= 1 and 0 < a <= 1):
        return None
    return (b + a) / 2.0


def get_adapter(sport: str, **kw) -> SportAdapter:
    s = (sport or "").lower()
    if s == "mlb":
        return MlbAdapter(**kw)
    if s == "nba":
        return NbaAdapter()
    raise ValueError(f"P-018: unknown sport {sport!r} (have: mlb, nba-stub)")
