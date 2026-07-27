"""
src/golf_schedule.py
────────────────────
Resolve ``(competition, round_number) -> real round-end UTC`` for the five
golf tours P-022 quotes, from an EXTERNAL schedule.

Why this exists
───────────────
**Kalshi does not publish the real close of an open market.** On an open
round-leader market all six time fields (``close_time``, ``occurrence_
datetime``, ``expected_expiration_time``, ``expiration_time``,
``latest_expiration_time``) collapse to one conservative ~20-day fallback;
``close_time`` is rewritten to the truth only at the instant the market
closes.  Measured 2026-07-27 across 346 open markets on three tours: every
field read ``2026-08-16T00:00:00Z``.  P-022's placement window is defined
relative to the round end, so with no external source the window opens ~2.5
weeks after the round has settled and the pod is structurally incapable of
quoting.  It ran that way from 2026-07-23 to 2026-07-28 and emitted nothing.

Source: ESPN's public golf API
──────────────────────────────
Chosen over the alternatives because it is the only one that satisfies all
four requirements at once — all five tours, per-round timing, free, no key:

* **DataGolf** (`src/datagolf_client.py`) — paid, ``DATAGOLF_API_KEY`` is not
  set, and it is a model/odds feed rather than a schedule feed.
* **PGA Tour's own site** — covers PGA and Champions only; LPGA, DP World and
  LIV each need a separate scraper, and it is HTML, not a documented feed.
* **Kalshi itself** — ruled out above; that is the whole problem.

Two ESPN endpoints, both unauthenticated:

* ``/scoreboard?dates=YYYY`` per league — the season's tournaments with
  ``date`` (start) and ``endDate``, at DAY granularity stamped 04:00Z.
* ``/leaderboard?league=..&event=..`` — ``linescores[].teeTime``, a real
  per-round UTC tee-time per player.  Published a day or two before the
  round, **not** at listing time: checked 2026-07-27 against all three
  then-listed tournaments and all three returned zero competitors.  The
  day-granularity fallback is therefore load-bearing, not decorative.

Calibration and the direction of error
──────────────────────────────────────
Validated against 72 settled round-leader events (2026-05-18 → 2026-07-25),
where Kalshi's ``close_time`` *has* been rewritten to the truth.  See
``golf_quirks_research/schedule_probe.py`` and
``research/REPORT_P022_Close_Time_2026-07.md``.

The asymmetry that drives every constant below:

    predicted too EARLY  ->  pod posts EARLIER than the validated window.
                             Untested, but strictly further from the round.
    predicted too LATE   ->  pod posts INTO A LIVE ROUND.  Phase 2 measured
                             that regime directly (H=6h) and the edge
                             collapses.

Measured round span (close − FIRST tee of the round) is **12.5h median**, so
the locked window's 12h lower edge sits essentially exactly at the first tee.
There is no room at all on the late side.  Every lag below is therefore set
at or *below* the observed minimum residual, which makes the resolver's error
**one-sided positive** on all 69 validation events with tee times.

Fail-closed
───────────
Any failure — network, no name match, unknown tour, unparseable payload —
returns ``None``, and the caller must not quote.  A wrong round time is worse
than no quote: it produces fills outside the validated window, and those
tournaments would have to be excluded from T anyway.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

ESPN_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/golf/"
                   "{league}/scoreboard")
ESPN_LEADERBOARD = ("https://site.api.espn.com/apis/site/v2/sports/golf/"
                    "leaderboard")

# Kalshi series prefix -> ESPN golf league slug.  Longest-prefix matched, so
# KXLPGAR1LEAD never resolves through KXPGA.
TOUR_LEAGUE: Dict[str, str] = {
    "KXPGA": "pga",
    "KXLPGA": "lpga",
    "KXCHAMPTOUR": "champions-tour",
    "KXDPWORLDTOUR": "eur",
    "KXLIV": "liv",
}

# ── Calibrated lags, in hours ────────────────────────────────────────
# Each is the CONSERVATIVE (low) end of the measured residual distribution,
# so `predicted <= true` holds on every validation event.  n / min / median
# are from schedule_probe.py over the 72-event settled sample.

# close − last tee time of that round.   n=69  min 4.16  med 5.58
LAG_TEE_H = 4.0

# close − (this event's R1 last tee + (n−1)·24h).  n=69  min 1.23  med 5.44
# Used for R2/R3 before their own pairings are published; it inherits the
# venue's timezone from R1 for free, which the day anchor cannot.
LAG_R1_ANCHOR_H = 1.0

# close − (ESPN start day @04:00Z + (n−1)·24h), per tour.
# The spread between tours is venue timezone: ESPN stamps every tournament
# at 04:00Z (midnight ET) wherever it is played, so a UK event's round ends
# ~5h "earlier" in this frame than a US one.
LAG_DAY_H: Dict[str, float] = {
    "KXPGA":         14.5,   # n=36  min 15.09  med 19.81
    "KXLPGA":        11.5,   # n=15  min 11.99  med 19.33
    "KXDPWORLDTOUR": 12.0,   # n=14  min 12.51  med 14.14
    "KXLIV":          5.5,   # n=6   min  6.01  med 11.17
    # n=1 (min 20.01).  One observation cannot calibrate a floor, so this is
    # set far below it rather than at min−0.5h like the others.  The cost is
    # posting early on Champions events; the alternative is posting into a
    # live round on the strength of a single data point.
    "KXCHAMPTOUR":   12.0,
}

_SCOREBOARD_TTL_S = 6 * 3600.0    # the season calendar barely moves
_LEADERBOARD_TTL_S = 30 * 60.0    # tee times DO move (weather, re-pairing)

# Words with no discriminating power when matching tournament names.  Note
# what is NOT here: "open", "championship" and "tournament" are load-bearing
# ("The Open Championship" -> the empty set if you strip them, which silently
# drops the three biggest events in the sample).
_STOP = {"the", "presented", "pres", "by", "golf", "of", "at", "and", "a"}
_ACCENTS = str.maketrans("áàâäãéèêëíìîïóòôöõúùûüñç", "aaaaaeeeeiiiiooooouuuunc")

_MIN_NAME_SCORE = 0.34            # Jaccard; "U.S. Open" vs "The Open" = 0.25
_MAX_DATE_GAP_D = 30.0


@dataclass(frozen=True)
class RoundClose:
    """A resolved round end.  ``close_epoch`` is deliberately conservative."""
    close_epoch: float
    source: str                   # tee_times | r1_tee_anchor | tour_day_offset
    competition: str
    tour: str
    round_number: int
    espn_event_id: str
    espn_name: str
    lag_h: float

    @property
    def close_iso(self) -> str:
        return datetime.fromtimestamp(
            self.close_epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def tour_of(series_ticker: str) -> Optional[str]:
    """Longest-prefix tour match.  KXLPGA* must never resolve to KXPGA."""
    best: Optional[str] = None
    for pref in TOUR_LEAGUE:
        if series_ticker.startswith(pref) and (best is None or len(pref) > len(best)):
            best = pref
    return best


_ROUND_RE = re.compile(r"R([1-4])LEAD")


def round_of(series_ticker: str, scope: Optional[str] = None) -> Optional[int]:
    """Round number from the series ticker, cross-checked against
    ``product_metadata.competition_scope`` ("Round 1 Leader") when given."""
    m = _ROUND_RE.search(series_ticker or "")
    from_ticker = int(m.group(1)) if m else None
    from_scope = None
    if scope:
        ms = re.search(r"Round\s*([1-4])", scope, re.I)
        if ms:
            from_scope = int(ms.group(1))
    if from_ticker and from_scope and from_ticker != from_scope:
        logger.warning("golf_schedule: round disagreement ticker=%s scope=%r",
                       series_ticker, scope)
        return None
    return from_ticker or from_scope


def _norm(s: str) -> set:
    s = (s or "").lower().translate(_ACCENTS)
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return {w for w in s.split() if w and w not in _STOP}


class GolfScheduleResolver:
    """ESPN-backed round-close resolver.  Thread-safe, cached, fail-closed."""

    def __init__(self, timeout: float = 15.0,
                 scoreboard_ttl: float = _SCOREBOARD_TTL_S,
                 leaderboard_ttl: float = _LEADERBOARD_TTL_S,
                 session: Optional[Any] = None,
                 _now_fn=None):
        self._session = session or requests.Session()
        self._timeout = timeout
        self._scoreboard_ttl = scoreboard_ttl
        self._leaderboard_ttl = leaderboard_ttl
        self._now = _now_fn or time.time
        self._lock = threading.Lock()
        # (league, year) -> (fetched_at, [event dicts])
        self._sb_cache: Dict[Tuple[str, int], Tuple[float, List[dict]]] = {}
        # espn_event_id -> (fetched_at, {round: {"first":iso,"last":iso}})
        self._lb_cache: Dict[str, Tuple[float, Dict[str, Dict[str, str]]]] = {}

    # ── HTTP ─────────────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            logger.warning("golf_schedule: %s failed: %s", url, exc)
            return None
        if r.status_code != 200:
            logger.warning("golf_schedule: %s -> HTTP %s", url, r.status_code)
            return None
        try:
            return r.json()
        except ValueError:
            logger.warning("golf_schedule: %s -> non-JSON body", url)
            return None

    # ── Scoreboard (season calendar) ─────────────────────────────────

    def _scoreboard(self, league: str, year: int) -> List[dict]:
        key = (league, year)
        now = self._now()
        with self._lock:
            hit = self._sb_cache.get(key)
            if hit and now - hit[0] < self._scoreboard_ttl:
                return hit[1]
        data = self._get(ESPN_SCOREBOARD.format(league=league),
                         {"dates": year})
        if data is None:
            # Serve a stale cache rather than nothing: a season calendar that
            # is hours old is still correct, and failing closed on a transient
            # 502 would mute the pod for a whole window.
            with self._lock:
                hit = self._sb_cache.get(key)
            return hit[1] if hit else []
        events = [e for e in (data.get("events") or []) if e.get("id")]
        with self._lock:
            self._sb_cache[key] = (now, events)
        return events

    # ── Leaderboard (tee times) ──────────────────────────────────────

    def _tee_times(self, league: str, espn_event_id: str
                   ) -> Dict[str, Dict[str, str]]:
        now = self._now()
        with self._lock:
            hit = self._lb_cache.get(espn_event_id)
            if hit and now - hit[0] < self._leaderboard_ttl:
                return hit[1]
        data = self._get(ESPN_LEADERBOARD,
                         {"league": league, "event": espn_event_id})
        if data is None:
            with self._lock:
                hit = self._lb_cache.get(espn_event_id)
            return hit[1] if hit else {}
        out: Dict[str, Dict[str, str]] = {}
        for e in data.get("events") or []:
            for c in e.get("competitions") or []:
                for comp in c.get("competitors") or []:
                    for ls in comp.get("linescores") or []:
                        tt, per = ls.get("teeTime"), ls.get("period")
                        if not tt or per is None:
                            continue
                        try:
                            k = str(int(per))
                        except (TypeError, ValueError):
                            continue
                        slot = out.setdefault(k, {"first": tt, "last": tt})
                        slot["first"] = min(slot["first"], tt)
                        slot["last"] = max(slot["last"], tt)
        with self._lock:
            self._lb_cache[espn_event_id] = (now, out)
        return out

    # ── Name match ───────────────────────────────────────────────────

    def _find_event(self, league: str, competition: str,
                    near: datetime) -> Optional[dict]:
        want = _norm(competition)
        if not want:
            return None
        candidates: List[dict] = []
        for year in (near.year, near.year + 1):
            candidates.extend(self._scoreboard(league, year))
        seen, uniq = set(), []
        for e in candidates:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            uniq.append(e)
        scored = []
        for e in uniq:
            have = _norm(e.get("name") or "")
            if not have:
                continue
            inter = len(want & have)
            if not inter:
                continue
            jac = inter / len(want | have)
            start = _iso(e.get("date") or "")
            if start is None:
                continue
            gap_d = abs((start - near).total_seconds()) / 86400.0
            scored.append((round(jac, 3), -gap_d, e))
        if not scored:
            return None
        # Name first (rounded, so near-ties fall through to date), then the
        # nearest date.  ESPN carries two seasons and some names repeat.
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        best = scored[0]
        if best[0] < _MIN_NAME_SCORE or -best[1] > _MAX_DATE_GAP_D:
            logger.info("golf_schedule: no confident match for %r on %s "
                        "(best score %.2f, gap %.1fd)",
                        competition, league, best[0], -best[1])
            return None
        return best[2]

    # ── Public API ───────────────────────────────────────────────────

    def resolve(self, competition: str, series_ticker: str,
                scope: Optional[str] = None,
                near: Optional[datetime] = None) -> Optional[RoundClose]:
        """Resolve the round end for one Kalshi round-leader event.

        ``near`` disambiguates the season and repeated tournament names; it
        defaults to now, which is right for a live pod (a listed market's
        round is days away, not months).

        Returns ``None`` — meaning **do not quote** — if the tour is unknown,
        the round cannot be determined, ESPN has no confident match, or the
        HTTP calls fail with no usable cache.
        """
        if not competition:
            return None
        tour = tour_of(series_ticker or "")
        league = TOUR_LEAGUE.get(tour or "")
        rnd = round_of(series_ticker or "", scope)
        if not tour or not league or not rnd:
            logger.info("golf_schedule: unresolvable series %r (tour=%s round=%s)",
                        series_ticker, tour, rnd)
            return None
        near = near or datetime.fromtimestamp(self._now(), timezone.utc)
        ev = self._find_event(league, competition, near)
        if ev is None:
            return None
        eid = str(ev["id"])
        start = _iso(ev.get("date") or "")
        if start is None:
            return None

        tees = self._tee_times(league, eid)

        # 1. This round's own tee times — the precise source.
        own = tees.get(str(rnd)) or {}
        last = _iso(own.get("last") or "")
        if last is not None:
            return RoundClose(
                close_epoch=(last + timedelta(hours=LAG_TEE_H)).timestamp(),
                source="tee_times", competition=competition, tour=tour,
                round_number=rnd, espn_event_id=eid,
                espn_name=ev.get("name") or "", lag_h=LAG_TEE_H)

        # 2. This event's round 1 tee times + (n-1) days — inherits the
        #    venue timezone, and is published well before R2/R3 pairings are.
        r1 = _iso((tees.get("1") or {}).get("last") or "")
        if r1 is not None and rnd > 1:
            pred = r1 + timedelta(days=rnd - 1, hours=LAG_R1_ANCHOR_H)
            return RoundClose(
                close_epoch=pred.timestamp(), source="r1_tee_anchor",
                competition=competition, tour=tour, round_number=rnd,
                espn_event_id=eid, espn_name=ev.get("name") or "",
                lag_h=LAG_R1_ANCHOR_H)

        # 3. Day granularity + a per-tour lag.  This is what actually runs at
        #    listing time — ESPN publishes no tee times three days out.
        lag = LAG_DAY_H.get(tour)
        if lag is None:
            return None
        pred = start + timedelta(days=rnd - 1, hours=lag)
        return RoundClose(
            close_epoch=pred.timestamp(), source="tour_day_offset",
            competition=competition, tour=tour, round_number=rnd,
            espn_event_id=eid, espn_name=ev.get("name") or "", lag_h=lag)
