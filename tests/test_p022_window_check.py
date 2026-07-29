"""
tests/test_p022_window_check.py
───────────────────────────────
Tests for the P-022 quotable-window detector (scripts/p022_window_check.py).

The detector exists because P-022's failure mode is silence and silence is
also its correct behaviour between tournaments. So the property under test is
not "does it run" but **does it stay quiet exactly when quiet is right, and
shout exactly when it is not**:

  * placeholder close reference -> ALARM even though no window is open and no
    quote is missing. This is the live 2026-07-27 state: 346 markets listed,
    every close reference a ~20-day fallback, so the pod's [12h, 24h] window
    cannot open while they are tradeable. A detector that reused the pod's
    own window arithmetic would agree with the pod and stay silent — which is
    precisely what happened for three days.
  * window genuinely open, names in band, no quotes -> ALARM.
  * window open and quoting -> silent.
  * nothing listed, or listed but not yet in window -> silent.
"""
import importlib.util
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.golf_schedule import RoundClose
from src.round_leader_fade_maker import RoundLeaderFadeMakerEngine

_SPEC = importlib.util.spec_from_file_location(
    "p022_window_check",
    Path(__file__).resolve().parent.parent / "scripts" / "p022_window_check.py")
wc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wc)


NOW = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc).timestamp()
EVENT = "KXPGAR1LEAD-ROC26"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class FakeKalshi:
    def __init__(self, markets, mid=0.06):
        self._markets = markets
        self._mid = mid

    def open_markets(self, series):
        return [m for m in self._markets if m["ticker"].startswith(series)]

    def orderbook(self, ticker, depth=5):
        if self._mid is None:
            return None
        # Thick by default so tests measure what they name; the size-screen
        # parity case sets min_top_size above this instead of thinning here.
        return {"yes_bid": self._mid - 0.01, "yes_ask": self._mid + 0.01,
                "bid_qty": 500, "ask_qty": 500}

    def get(self, path, params=None):
        if path.startswith("/events/"):
            return {"event": {"product_metadata": {
                "competition": "Rocket Classic",
                "competition_scope": "Round 1 Leader"}}}
        return None


class FakeSchedule:
    """Stands in for GolfScheduleResolver.

    `resolve_it=None` simulates the fail-closed path (ESPN has no confident
    match), which is now its own alarm state rather than silence.
    """

    def __init__(self, close_epoch, source="tee_times"):
        self.close_epoch = close_epoch
        self.source = source

    def resolve(self, competition, series_ticker, scope=None, near=None):
        if self.close_epoch is None:
            return None
        return RoundClose(
            close_epoch=self.close_epoch, source=self.source,
            competition=competition, tour="KXPGA", round_number=1,
            espn_event_id="1", espn_name=competition, lag_h=4.0)


def _market(close_epoch: float, open_epoch: float, n: int = 1):
    return [{
        "ticker": f"{EVENT}-N{i}",
        "event_ticker": EVENT,
        "close_time": _iso(close_epoch),
        "expiration_time": _iso(close_epoch),
        "open_time": _iso(open_epoch),
    } for i in range(n)]


def _engine(markets, mid=0.06, resolved_close="from_market", **kw):
    """`resolved_close` is what the external schedule returns.

    Default mirrors the market payload so the existing cases read unchanged;
    pass None to exercise the fail-closed path.
    """
    if resolved_close == "from_market":
        resolved_close = (
            datetime.fromisoformat(
                markets[0]["close_time"].replace("Z", "+00:00")).timestamp()
            if markets else None)
    return RoundLeaderFadeMakerEngine(
        kalshi=FakeKalshi(markets, mid),
        series=("KXPGAR1LEAD",),
        # A private dir per engine: `assess` now calls `rebuild_from_log`, so a
        # shared /tmp would let one test's fills log leak into another's caps.
        log_dir=Path(tempfile.mkdtemp(prefix="p022test-")),
        schedule=FakeSchedule(resolved_close),
        _now_fn=lambda: NOW,
        **kw,
    )


def _assess(engine, quotes=None, quote_ts=None, grace_s=1200.0):
    wc.recent_quotes = lambda path, since: quotes or {
        "exists": False, "n_recent": 0, "total": 0, "last_ts": None}
    wc.last_quote_per_ticker = lambda path: dict(quote_ts or {})
    return wc.assess(engine, placeholder_days=7.0, quote_lookback_s=3600.0,
                     grace_s=grace_s)


# ── the live 2026-07-27 state ────────────────────────────────────────

def test_placeholder_close_reference_alarms_with_no_window_and_no_quote():
    """The state that went unnoticed for three days.

    Markets are listed and the pod is 'healthy'; the close reference is a
    20-day fallback, so no window will ever open while they are tradeable.
    Nothing is missing from the quote log yet — the alarm must fire anyway.
    """
    close = NOW + 20 * 86400
    listed = NOW - 0.01 * 86400
    r = _assess(_engine(_market(close, listed, n=5)))
    assert r["state"] == "CLOSE_REF_PLACEHOLDER"
    assert r["alarm"] is True
    assert r["n_placeholder_events"] == 1
    assert r["n_in_window_events"] == 0        # no window — alarms regardless
    assert "CANNOT QUOTE" in r["detail"]


def test_real_close_reference_outside_window_is_silent():
    """A genuine close 3 days out: correct behaviour, must not page."""
    close = NOW + 3 * 86400
    listed = NOW - 0.5 * 86400               # span 3.5d < 7d -> real
    r = _assess(_engine(_market(close, listed)))
    assert r["state"] == "NO_WINDOW"
    assert r["legacy_state"] == "WAITING"
    assert r["alarm"] is False


def test_no_markets_listed_is_silent():
    """Between tournaments. The registry deliberately carries no heartbeat
    for exactly this reason."""
    r = _assess(_engine([]))
    assert r["state"] == "NO_MARKETS"
    assert r["alarm"] is False


# ── the condition the task was written to catch ──────────────────────

def test_window_open_candidate_and_no_quote_alarms():
    """THE state this file exists for: everything the pod screens on passes,
    the window has been open past the grace period, and nothing was written."""
    close = NOW + 18 * 3600                  # inside [12h, 24h]; open for 6h
    listed = NOW - 2 * 86400                 # span 2.75d -> real
    r = _assess(_engine(_market(close, listed, n=3), mid=0.06))
    assert r["state"] == "WINDOW_OPEN_CANDIDATE_NO_QUOTE"
    assert r["alarm"] is True
    assert r["n_in_band"] == 3
    assert r["funnel"]["passes_every_screen"] == 3
    assert r["funnel"]["quoted_since_window_open"] == 0
    assert len(r["candidates_without_quote"]) == 3


def test_window_open_and_quoting_is_silent():
    """A quote written after the window opened clears it — even though the
    pod then rests that quote for hours writing nothing more."""
    close = NOW + 18 * 3600
    listed = NOW - 2 * 86400
    window_open = close - 24 * 3600
    r = _assess(_engine(_market(close, listed, n=3), mid=0.06),
                quotes={"exists": True, "n_recent": 0, "total": 3,
                        "last_ts": window_open + 60},
                quote_ts={f"{EVENT}-N{i}": window_open + 60 for i in range(3)})
    assert r["state"] == "QUOTED"
    assert r["alarm"] is False
    assert r["funnel"]["quoted_since_window_open"] == 3


def test_a_quote_from_BEFORE_this_window_does_not_clear_it():
    """Otherwise last tournament's quote log would silence this tournament."""
    close = NOW + 18 * 3600
    window_open = close - 24 * 3600
    r = _assess(_engine(_market(close, NOW - 2 * 86400, n=3), mid=0.06),
                quote_ts={f"{EVENT}-N{i}": window_open - 3600 for i in range(3)})
    assert r["state"] == "WINDOW_OPEN_CANDIDATE_NO_QUOTE"
    assert r["alarm"] is True


def test_window_open_but_nothing_in_band_is_silent_and_says_so():
    """No name priced in [0.03, 0.12] is a legitimate reason not to quote —
    but it is NOT the same state as 'no window is open', which is exactly the
    conflation that made the detector useless."""
    close = NOW + 18 * 3600
    listed = NOW - 2 * 86400
    r = _assess(_engine(_market(close, listed, n=3), mid=0.55))
    assert r["state"] == "WINDOW_OPEN_NO_CANDIDATE"
    assert r["alarm"] is False
    assert r["n_in_band"] == 0
    assert r["funnel"]["inside_window"] == 3


def test_in_band_but_caps_refuse_is_silent_not_an_alarm():
    """A cap refusal is the pod obeying §7, not the pod being broken."""
    close = NOW + 18 * 3600
    r = _assess(_engine(_market(close, NOW - 2 * 86400, n=3), mid=0.06,
                        max_contracts_per_name=0.0))
    assert r["state"] == "WINDOW_OPEN_NO_CANDIDATE"
    assert r["alarm"] is False
    assert r["screen_refusals"] == {"cap_per_name": 3}


def test_grace_period_covers_the_pods_rediscover_interval():
    """`run_round_leader_fade.py` re-discovers every 900 s, so a window that
    opened a minute ago is not yet evidence of anything."""
    close = NOW + 24 * 3600 - 300            # window opened 5 min ago
    r = _assess(_engine(_market(close, NOW - 2 * 86400, n=3), mid=0.06))
    assert r["state"] == "WINDOW_OPEN_GRACE"
    assert r["alarm"] is False
    assert r["n_candidates"] == 3
    assert r["candidates_without_quote"] == []


def test_checker_failure_is_a_failure_never_a_skip():
    """A total listing outage used to read as NO_MARKETS — indistinguishable
    from 'between tournaments'."""
    eng = _engine(_market(NOW + 18 * 3600, NOW - 2 * 86400, n=3))

    def boom(series):
        raise RuntimeError("kalshi 503")
    eng.kalshi.open_markets = boom
    r = _assess(eng)
    assert r["state"] == "CHECK_FAILED"
    assert r["alarm"] is True
    assert "KXPGAR1LEAD" in r["discovery_errors"]


def test_screen_agrees_with_the_engines_own_decision():
    """The detector duplicates the pod's post-band screens on purpose (see
    `screen_after_band`). This pins the duplication: a real engine is driven
    over the same states and the two answers must match."""
    close = NOW + 18 * 3600
    cases = [
        ({}, 0.06, True),                       # ordinary candidate
        ({"max_contracts_per_name": 0.0}, 0.06, False),   # per-name cap
        ({"pct_per_tournament": 0.0}, 0.06, False),       # tournament cap
        ({"pct_total": 0.0}, 0.06, False),                # aggregate cap
        ({"quote_offset": 0.0}, 0.06, False),             # px not above mid
        ({"pct_per_name": 0.0000001}, 0.06, False),       # sizes to zero
        # R5 size screen: the fake book carries 500 a side, so a floor above
        # that refuses — and the engine must refuse identically.
        ({"min_top_size": 1000.0}, 0.06, False),
    ]
    for kw, mid, expect in cases:
        eng = _engine(_market(close, NOW - 2 * 86400, n=1), mid=mid, **kw)
        ticker = f"{EVENT}-N0"
        # Price through the engine's own _mid first, as assess() does — the
        # size screen reads the book snapshot that call records.
        eng._mid(ticker)
        ok, _why, _size, _px = wc.screen_after_band(
            eng, ticker, "ROC26", mid)
        assert ok is expect, (kw, "detector")

        # ... and the engine itself, through its real cycle path.
        eng2 = _engine(_market(close, NOW - 2 * 86400, n=1), mid=mid, **kw)
        eng2.discover()
        eng2.cycle()
        placed = eng2.books[ticker].ask_quote is not None
        assert placed is expect, (kw, "engine")


# ── the detector must track the POD, not a private copy ──────────────

def test_uses_the_pods_own_close_reference():
    """If the pod's close resolution changes, the detector must change with
    it — otherwise it measures a pod that does not exist."""
    close = NOW + 20 * 86400
    listed = NOW - 0.01 * 86400
    eng = _engine(_market(close, listed))
    r = _assess(eng)
    assert abs(r["events"][0]["close_ref"] - close) < 1.0
    assert r["params"]["mid_band"] == [0.03, 0.12]
    assert r["params"]["fade_start_h"] == 24.0
    assert r["params"]["no_new_quote_h"] == 12.0


def test_per_event_close_reference_is_recorded_for_drift_analysis():
    """Each run banks the close reference it saw, so the open question
    'does Kalshi ever correct close_time before the round?' is answerable
    from the status log without a second script."""
    close = NOW + 20 * 86400
    r = _assess(_engine(_market(close, NOW - 864.0)))
    ev = r["events"][0]
    assert ev["event"] == EVENT
    assert ev["close_ref_iso"].startswith("2026-08-16")
    assert ev["listing_span_days"] > 19.0


# ── fail-closed must never be silent ─────────────────────────────────

def test_unresolved_schedule_alarms_rather_than_looking_healthy():
    """The pod refusing to quote because it cannot time the round is correct
    behaviour AND indistinguishable from health. It must page.

    A wrong round time is worse than no quote — it produces fills outside the
    validated window, and §7 would exclude those tournaments from T anyway —
    so failing closed is the right call. But five days of silence is exactly
    how T stayed at 0 without anyone noticing, which is why this is an alarm
    state rather than WAITING.
    """
    listed = NOW - 0.5 * 86400
    r = _assess(_engine(_market(NOW + 3 * 86400, listed, n=4),
                        resolved_close=None))
    assert r["state"] == "SCHEDULE_UNRESOLVED"
    assert r["alarm"] is True
    assert r["n_unresolved_events"] == 1
    assert r["n_resolved_events"] == 0
