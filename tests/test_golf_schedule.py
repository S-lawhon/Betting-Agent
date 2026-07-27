"""
tests/test_golf_schedule.py
───────────────────────────
Tests for src/golf_schedule.py — the external round-close resolver P-022
depends on, because Kalshi publishes no field carrying the real close of an
OPEN market.

The properties under test are the ones that decide whether the pod quotes
inside the validated window or not at all:

  * **Fail closed.** Anything unresolvable returns None, and the pod does not
    quote. A wrong round time is worse than no quote.
  * **Error is one-sided EARLY.** Every lag is calibrated at or below the
    observed minimum residual, so predicted <= true. A prediction that is LATE
    puts quotes into a live round, which is the regime Phase 2 measured at
    H=6h and found the edge does not survive.
  * **Source precedence.** Per-round tee times > this event's R1 tee times +
    (n−1) days > per-tour day offset. ESPN publishes no tee times three days
    out, so the coarse path is what actually runs at listing time.
  * **Longest-prefix tour matching.** KXLPGA* must never resolve as KXPGA.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.golf_schedule import (LAG_DAY_H, LAG_R1_ANCHOR_H, LAG_TEE_H,
                               GolfScheduleResolver, round_of, tour_of)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


START = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)   # ESPN stamps 04:00Z
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Serves a scoreboard and a leaderboard; records what was asked for."""

    def __init__(self, events=None, tee_times=None, fail=False):
        self.events = events if events is not None else [{
            "id": "401811960", "name": "Rocket Classic",
            "date": _iso(START), "endDate": _iso(START + timedelta(days=3)),
        }]
        # {round: [teeTime, ...]}
        self.tee_times = tee_times or {}
        self.fail = fail
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if self.fail:
            return FakeResponse(None, status=503)
        if "scoreboard" in url:
            return FakeResponse({"events": self.events})
        competitors = []
        for rnd, times in self.tee_times.items():
            for t in times:
                competitors.append({"linescores": [
                    {"period": int(rnd), "teeTime": t}]})
        return FakeResponse({"events": [{"competitions": [
            {"competitors": competitors}]}]})


def _resolver(session, **kw):
    return GolfScheduleResolver(session=session,
                                _now_fn=lambda: NOW.timestamp(), **kw)


# ── tour / round parsing ─────────────────────────────────────────────

def test_tour_matching_is_longest_prefix():
    """KXLPGA starts with neither more nor less than KXPGA by luck — it does
    not start with KXPGA at all, but KXCHAMPTOUR/KXDPWORLDTOUR style nesting
    is exactly the trap the fee table hit four times, so this is explicit."""
    assert tour_of("KXPGAR1LEAD") == "KXPGA"
    assert tour_of("KXLPGAR3LEAD") == "KXLPGA"
    assert tour_of("KXDPWORLDTOURR2LEAD") == "KXDPWORLDTOUR"
    assert tour_of("KXCHAMPTOURR1LEAD") == "KXCHAMPTOUR"
    assert tour_of("KXLIVR3LEAD") == "KXLIV"
    assert tour_of("KXMLBGAME") is None


def test_round_number_from_ticker_and_scope():
    assert round_of("KXPGAR2LEAD") == 2
    assert round_of("KXPGAR2LEAD", "Round 2 Leader") == 2
    assert round_of("KXPGATOP10") is None


def test_round_disagreement_refuses_rather_than_guessing():
    """If the ticker and Kalshi's own scope text disagree, something is wrong
    about the event and timing it is not a coin flip."""
    assert round_of("KXPGAR1LEAD", "Round 3 Leader") is None


# ── source precedence ────────────────────────────────────────────────

def test_prefers_this_rounds_own_tee_times():
    last = START + timedelta(hours=15)          # 19:00Z on the round day
    s = FakeSession(tee_times={"1": [_iso(START + timedelta(hours=11)),
                                     _iso(last)]})
    rc = _resolver(s).resolve("Rocket Classic", "KXPGAR1LEAD")
    assert rc is not None
    assert rc.source == "tee_times"
    assert abs(rc.close_epoch
               - (last + timedelta(hours=LAG_TEE_H)).timestamp()) < 1.0


def test_falls_back_to_the_r1_anchor_for_a_later_round():
    """R2/R3 pairings are published only after the previous round, but the
    window for them opens earlier than that. R1's tee times carry the venue's
    timezone, which the day offset cannot."""
    r1_last = START + timedelta(hours=15)
    s = FakeSession(tee_times={"1": [_iso(r1_last)]})
    rc = _resolver(s).resolve("Rocket Classic", "KXPGAR3LEAD")
    assert rc.source == "r1_tee_anchor"
    expect = r1_last + timedelta(days=2, hours=LAG_R1_ANCHOR_H)
    assert abs(rc.close_epoch - expect.timestamp()) < 1.0


def test_falls_back_to_the_per_tour_day_offset_when_no_tee_times():
    """The path that actually runs at listing time: checked live on
    2026-07-27, ESPN had zero competitors for all three listed tournaments."""
    s = FakeSession(tee_times={})
    rc = _resolver(s).resolve("Rocket Classic", "KXPGAR1LEAD")
    assert rc.source == "tour_day_offset"
    expect = START + timedelta(hours=LAG_DAY_H["KXPGA"])
    assert abs(rc.close_epoch - expect.timestamp()) < 1.0


def test_day_offset_advances_one_day_per_round():
    s = FakeSession(tee_times={})
    rc = _resolver(s).resolve("Rocket Classic", "KXPGAR3LEAD")
    expect = START + timedelta(days=2, hours=LAG_DAY_H["KXPGA"])
    assert abs(rc.close_epoch - expect.timestamp()) < 1.0


# ── the calibration must stay conservative ───────────────────────────

def test_every_lag_is_below_the_observed_minimum_residual():
    """Measured minima over the 72-event settled validation sample
    (golf_quirks_research/schedule_probe.py).  A lag ABOVE its minimum means
    the resolver can predict a close LATER than the truth, which puts quotes
    into a live round — the one direction with no tolerance at all, because
    the median round span (close − first tee) is 12.5h and the locked window's
    lower edge is 12h."""
    observed_min = {
        "tee": 4.16, "r1": 1.23,
        "KXPGA": 15.09, "KXLPGA": 11.99, "KXDPWORLDTOUR": 12.51,
        "KXLIV": 6.01,
    }
    assert LAG_TEE_H <= observed_min["tee"]
    assert LAG_R1_ANCHOR_H <= observed_min["r1"]
    for tour, floor in observed_min.items():
        if tour in LAG_DAY_H:
            assert LAG_DAY_H[tour] <= floor, tour
    # Champions has n=1 in the sample, so its lag is set far below that single
    # observation (20.01h) rather than at min−0.5h like the calibrated tours.
    assert LAG_DAY_H["KXCHAMPTOUR"] <= 20.01 - 5.0


# ── fail closed ──────────────────────────────────────────────────────

def test_unknown_tour_returns_none():
    assert _resolver(FakeSession()).resolve("Rocket Classic", "KXMLBGAME") is None


def test_no_name_match_returns_none():
    s = FakeSession(events=[{"id": "1", "name": "Wyndham Championship",
                             "date": _iso(START), "endDate": _iso(START)}])
    assert _resolver(s).resolve("Rocket Classic", "KXPGAR1LEAD") is None


def test_http_failure_with_no_cache_returns_none():
    assert _resolver(FakeSession(fail=True)).resolve(
        "Rocket Classic", "KXPGAR1LEAD") is None


def test_empty_competition_returns_none():
    assert _resolver(FakeSession()).resolve("", "KXPGAR1LEAD") is None


# ── name matching that the sample actually needed ────────────────────

def test_matches_names_that_are_mostly_stopwords():
    """'The Open Championship' -> ESPN 'The Open'. The first matcher stripped
    'open'/'championship'/'tournament' as noise, which normalises this to the
    EMPTY SET and silently dropped the three biggest events in the validation
    sample."""
    s = FakeSession(events=[
        {"id": "1", "name": "The Open", "date": _iso(START),
         "endDate": _iso(START)},
        {"id": "2", "name": "U.S. Open", "date": _iso(START),
         "endDate": _iso(START)},
    ], tee_times={})
    rc = _resolver(s).resolve("The Open Championship", "KXPGAR1LEAD")
    assert rc is not None and rc.espn_name == "The Open"


def test_matches_across_accents_and_sponsor_noise():
    s = FakeSession(events=[{"id": "1", "name": "LIV Golf Andalucía",
                             "date": _iso(START), "endDate": _iso(START)}])
    rc = _resolver(s).resolve("LIV Golf Andalucia", "KXLIVR1LEAD")
    assert rc is not None and rc.source == "tour_day_offset"

    s2 = FakeSession(events=[{"id": "2",
                              "name": "the Memorial Tournament pres. by Workday",
                              "date": _iso(START), "endDate": _iso(START)}])
    rc2 = _resolver(s2).resolve("The Memorial Tournament", "KXPGAR1LEAD")
    assert rc2 is not None


def test_same_name_two_seasons_disambiguated_by_date():
    """ESPN carries two seasons at once and tournament names repeat yearly."""
    old = START - timedelta(days=365)
    s = FakeSession(events=[
        {"id": "old", "name": "Rocket Classic", "date": _iso(old),
         "endDate": _iso(old)},
        {"id": "new", "name": "Rocket Classic", "date": _iso(START),
         "endDate": _iso(START)},
    ])
    rc = _resolver(s).resolve("Rocket Classic", "KXPGAR1LEAD")
    assert rc.espn_event_id == "new"


# ── caching ──────────────────────────────────────────────────────────

def test_scoreboard_is_cached_between_calls():
    s = FakeSession()
    r = _resolver(s)
    r.resolve("Rocket Classic", "KXPGAR1LEAD")
    n_after_first = len(s.calls)
    r.resolve("Rocket Classic", "KXPGAR2LEAD")
    # Second resolve adds no scoreboard fetches (the leaderboard is cached by
    # event id too, so this should add nothing at all).
    assert len(s.calls) == n_after_first


def test_a_transient_failure_serves_the_stale_scoreboard():
    """A season calendar hours old is still correct; failing closed on a
    transient 502 would mute the pod for a whole placement window."""
    s = FakeSession()
    r = _resolver(s)
    assert r.resolve("Rocket Classic", "KXPGAR1LEAD") is not None
    s.fail = True
    r._sb_cache = {k: (0.0, v[1]) for k, v in r._sb_cache.items()}   # expire
    r._lb_cache = {k: (0.0, v[1]) for k, v in r._lb_cache.items()}
    assert r.resolve("Rocket Classic", "KXPGAR1LEAD") is not None
