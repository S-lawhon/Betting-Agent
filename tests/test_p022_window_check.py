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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
        return {"yes_bid": self._mid - 0.01, "yes_ask": self._mid + 0.01}


def _market(close_epoch: float, open_epoch: float, n: int = 1):
    return [{
        "ticker": f"{EVENT}-N{i}",
        "event_ticker": EVENT,
        "close_time": _iso(close_epoch),
        "expiration_time": _iso(close_epoch),
        "open_time": _iso(open_epoch),
    } for i in range(n)]


def _engine(markets, mid=0.06):
    return RoundLeaderFadeMakerEngine(
        kalshi=FakeKalshi(markets, mid),
        series=("KXPGAR1LEAD",),
        log_dir=Path("/tmp"),
        _now_fn=lambda: NOW,
    )


def _assess(engine, quotes=None, monkeypatch=None):
    wc.recent_quotes = lambda path, since: quotes or {
        "exists": False, "n_recent": 0, "total": 0, "last_ts": None}
    return wc.assess(engine, placeholder_days=7.0, quote_lookback_s=3600.0)


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
    assert r["state"] == "WAITING"
    assert r["alarm"] is False


def test_no_markets_listed_is_silent():
    """Between tournaments. The registry deliberately carries no heartbeat
    for exactly this reason."""
    r = _assess(_engine([]))
    assert r["state"] == "NO_MARKETS"
    assert r["alarm"] is False


# ── the condition the task was written to catch ──────────────────────

def test_window_open_in_band_and_no_quotes_alarms():
    close = NOW + 18 * 3600                  # inside [12h, 24h]
    listed = NOW - 2 * 86400                 # span 2.75d -> real
    r = _assess(_engine(_market(close, listed, n=3), mid=0.06))
    assert r["state"] == "WINDOW_OPEN_NO_QUOTES"
    assert r["alarm"] is True
    assert r["n_in_band"] == 3


def test_window_open_and_quoting_is_silent():
    close = NOW + 18 * 3600
    listed = NOW - 2 * 86400
    r = _assess(_engine(_market(close, listed, n=3), mid=0.06),
                quotes={"exists": True, "n_recent": 3, "total": 3,
                        "last_ts": NOW - 60})
    assert r["state"] == "WINDOW_OPEN_QUOTING"
    assert r["alarm"] is False


def test_window_open_but_nothing_in_band_is_silent():
    """No name priced in [0.03, 0.12] is a legitimate reason not to quote."""
    close = NOW + 18 * 3600
    listed = NOW - 2 * 86400
    r = _assess(_engine(_market(close, listed, n=3), mid=0.55))
    assert r["alarm"] is False
    assert r["n_in_band"] == 0


# ── the detector must track the POD, not a private copy ──────────────

def test_uses_the_pods_own_close_reference():
    """If _close_epoch changes, the detector must change with it — otherwise
    it measures a pod that does not exist."""
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
