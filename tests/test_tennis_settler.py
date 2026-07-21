"""
tests/test_tennis_settler.py
────────────────────────────
Tests for KalshiTennisSettler (P-015 settlement).

The settler had no test coverage and a latent defect: it read
data/pods/P-015.jsonl, which is never written when a TradeStore is
attached (BasePod.write_log delegates to store.append(), so everything
lands in the shared trade_log.jsonl).  P-015 had not yet placed a trade,
so nothing had surfaced it — P-017 hit the same bug in production, with
27 placements and an empty data/pods/ directory.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.kalshi_tennis_settler import KalshiTennisSettler, _calc_outcome_pnl


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, markets):
        self._markets = markets
        self.calls = []

    def get_market(self, ticker):
        self.calls.append(ticker)
        return self._markets.get(ticker)


class FakeStore:
    """Minimal TradeStore stand-in."""

    def __init__(self, open_trades):
        self._open = list(open_trades)
        self.appended = []

    def get_open_trades(self, pod_id=None):
        return [e for e in self._open
                if pod_id is None or e.get("pod_id") == pod_id]

    def append(self, entry):
        self.appended.append(entry)
        fp = entry.get("fingerprint")
        if (entry.get("outcome") or "").upper() in ("WIN", "LOSS", "VOID"):
            self._open = [e for e in self._open
                          if e.get("fingerprint") != fp]


def _placed(ticker, size=100.0, fill=0.40, start=None):
    return {
        "pod_id": "P-015",
        "action": "PLACED",
        "market_id": ticker,
        "side": "YES",
        "position_size_usd": size,
        "fill_price": fill,
        "venue_prob": fill,
        "fingerprint": f"fp-{ticker}",
        "extra": {"scheduled_start_utc": start or "2026-07-26T12:00:00+00:00"},
    }


def _settler(store=None, markets=None, log_path=None, now=NOW):
    return KalshiTennisSettler(
        client=FakeClient(markets or {}),
        log_path=log_path or Path("/nonexistent/never-written.jsonl"),
        _now_fn=lambda: now,
        trade_store=store,
    )


# ── The defect: per-pod file is never written ────────────────────────

def test_reads_open_positions_from_trade_store():
    store = FakeStore([_placed("KXATP-XYZ-ALCARAZ")])
    s = _settler(
        store,
        {"KXATP-XYZ-ALCARAZ": {"status": "finalized", "result": "yes"}},
    )
    out = s.settle_cycle()
    assert len(out) == 1
    assert out[0]["outcome"] == "WIN"


def test_settlement_persists_through_trade_store(tmp_path):
    store = FakeStore([_placed("KXATP-XYZ-SINNER")])
    log = tmp_path / "never-written.jsonl"
    s = _settler(
        store,
        {"KXATP-XYZ-SINNER": {"status": "finalized", "result": "no"}},
        log_path=log,
    )
    s.settle_cycle()
    assert len(store.appended) == 1
    assert store.appended[0]["outcome"] == "LOSS"
    assert not log.exists()


def test_does_not_resettle():
    store = FakeStore([_placed("KXATP-XYZ-ALCARAZ")])
    s = _settler(
        store,
        {"KXATP-XYZ-ALCARAZ": {"status": "finalized", "result": "yes"}},
    )
    assert len(s.settle_cycle()) == 1
    assert s.settle_cycle() == []


def test_filters_to_own_pod():
    mine = _placed("KXATP-XYZ-ALCARAZ")
    other = {**_placed("KXPGATOP10-3MO26-AAA"), "pod_id": "P-017"}
    store = FakeStore([mine, other])
    s = _settler(store, {
        "KXATP-XYZ-ALCARAZ": {"status": "finalized", "result": "yes"},
        "KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "yes"},
    })
    out = s.settle_cycle()
    assert [r["market_id"] for r in out] == ["KXATP-XYZ-ALCARAZ"]


def test_unresolved_market_is_left_open():
    store = FakeStore([_placed("KXATP-XYZ-ALCARAZ")])
    s = _settler(
        store,
        {"KXATP-XYZ-ALCARAZ": {"status": "active", "result": ""}},
    )
    assert s.settle_cycle() == []
    assert store.appended == []


def test_missing_market_does_not_crash():
    store = FakeStore([_placed("KXATP-XYZ-GONE")])
    assert _settler(store, {}).settle_cycle() == []


# ── Stale guard ──────────────────────────────────────────────────────

def test_stale_match_voids_after_two_weeks():
    old = (NOW - timedelta(days=15)).isoformat()
    store = FakeStore([_placed("KXATP-XYZ-STUCK", start=old)])
    s = _settler(
        store,
        {"KXATP-XYZ-STUCK": {"status": "active", "result": ""}},
    )
    out = s.settle_cycle()
    assert len(out) == 1
    assert out[0]["outcome"] == "VOID"
    assert out[0]["resolution_source"] == "stale_timeout"


def test_recent_match_is_not_stale():
    recent = (NOW - timedelta(days=1)).isoformat()
    store = FakeStore([_placed("KXATP-XYZ-FRESH", start=recent)])
    s = _settler(
        store,
        {"KXATP-XYZ-FRESH": {"status": "active", "result": ""}},
    )
    assert s.settle_cycle() == []


# ── File fallback still works (standalone / offline use) ─────────────

def test_file_fallback_without_store(tmp_path):
    log = tmp_path / "trade_log.jsonl"
    log.write_text(json.dumps(_placed("KXATP-XYZ-ALCARAZ")) + "\n",
                   encoding="utf-8")
    s = KalshiTennisSettler(
        client=FakeClient(
            {"KXATP-XYZ-ALCARAZ": {"status": "finalized", "result": "yes"}}
        ),
        log_path=log,
        _now_fn=lambda: NOW,
    )
    out = s.settle_cycle()
    assert len(out) == 1
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert len(lines) == 2


def test_from_config_uses_shared_trade_log_not_pod_logs():
    """Regression: pod_logs is never written; must default to trade_log."""
    cfg = {"paths": {
        "trade_log": "data/trade_logs/trade_log.jsonl",
        "pod_logs": "data/pods/",
    }}

    class _StubClient:
        @classmethod
        def from_config(cls, config):
            return cls()

    import src.kalshi_tennis_settler as mod
    real = mod.KalshiTennisClient
    mod.KalshiTennisClient = _StubClient
    try:
        s = KalshiTennisSettler.from_config(cfg)
    finally:
        mod.KalshiTennisClient = real

    assert "pods" not in str(s._log_path)
    assert str(s._log_path).endswith("trade_log.jsonl")


# ── P&L helper ───────────────────────────────────────────────────────

@pytest.mark.parametrize("result,expected", [
    ("yes", "WIN"), ("no", "LOSS"), ("", "VOID"),
])
def test_calc_outcome_labels(result, expected):
    outcome, _ = _calc_outcome_pnl("YES", result, 100.0, 0.40)
    assert outcome == expected
