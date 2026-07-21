"""
tests/test_golf_settler.py
──────────────────────────
Tests for KalshiGolfSettler (P-017 settlement).

Focus is the two live-API shapes that would silently corrupt the P-017
validation if handled the way the tennis settler handles them:

  * status="closed" with an empty result — the ~1-day finalization lag.
    Must NOT settle (booking these as VOID zeroes a whole tournament).
  * result="scalar" — competitor withdrew; market cancelled, not
    resolved.  Must VOID, and be labelled as such.

Plus the tie case that is P-017's entire thesis: an event may settle far
more than N markets YES, and every one pays in full.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.kalshi_golf_settler import KalshiGolfSettler, _calc_outcome_pnl


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    """Stands in for KalshiPublic.get_market."""

    def __init__(self, markets):
        self._markets = markets
        self.calls = []

    def get_market(self, ticker):
        self.calls.append(ticker)
        return self._markets.get(ticker)


def _placed(ticker, size=100.0, fill=0.20, contracts=500,
            taker_fee=0.0112, close_utc="2026-07-26T22:00:00+00:00"):
    return {
        "pod_id": "P-017",
        "action": "PLACED",
        "market_id": ticker,
        "side": "YES",
        "position_size_usd": size,
        "fill_price": fill,
        "venue_prob": fill,
        "extra": {
            "contracts": contracts,
            "taker_fee": taker_fee,
            "close_utc": close_utc,
            "event_code": "3MO26",
        },
    }


def _write_log(tmp_path: Path, entries) -> Path:
    p = tmp_path / "P-017.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return p


def _settler(tmp_path, entries, markets, now=NOW):
    return KalshiGolfSettler(
        client=FakeClient(markets),
        log_path=_write_log(tmp_path, entries),
        _now_fn=lambda: now,
    )


# ── The finalization-lag trap ────────────────────────────────────────

def test_closed_with_empty_result_is_not_settled(tmp_path):
    """status=closed + result='' is the ~1-day lag, NOT a void."""
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-AAA")],
        {"KXPGATOP10-3MO26-AAA": {"status": "closed", "result": ""}},
    )
    assert s.settle_cycle() == []


def test_closed_position_settles_once_result_populates(tmp_path):
    """Same market, a cycle later, now finalized — settles normally."""
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-AAA")],
        {"KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "yes"}},
    )
    out = s.settle_cycle()
    assert len(out) == 1
    assert out[0]["outcome"] == "WIN"


def test_whole_tournament_in_lag_window_settles_nothing(tmp_path):
    """The regression that matters: 40 closed markets, zero spurious voids."""
    entries = [_placed(f"KXPGATOP10-3MO26-{i:03d}") for i in range(40)]
    markets = {
        e["market_id"]: {"status": "closed", "result": ""} for e in entries
    }
    s = _settler(tmp_path, entries, markets)
    assert s.settle_cycle() == []


# ── Withdrawn competitors (result="scalar") ──────────────────────────

def test_scalar_result_voids_as_withdrawn(tmp_path):
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-WD")],
        {"KXPGATOP10-3MO26-WD": {"status": "finalized", "result": "scalar"}},
    )
    out = s.settle_cycle()
    assert len(out) == 1
    assert out[0]["outcome"] == "VOID"
    assert out[0]["resolution_source"] == "kalshi_withdrawn"
    assert out[0]["pnl_gross_usd"] == 0.0


def test_unknown_result_is_left_open_not_voided(tmp_path):
    """A new Kalshi result value must surface, not silently zero P&L."""
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-NEW")],
        {"KXPGATOP10-3MO26-NEW": {"status": "finalized", "result": "banana"}},
    )
    assert s.settle_cycle() == []


# ── Ties — P-017's thesis ────────────────────────────────────────────

def test_ties_pay_every_yes_in_full(tmp_path):
    """A 6-way tie for T-8 settles all six YES at full value.

    Nothing may assume a fixed YES count per event.
    """
    entries = [_placed(f"KXPGATOP10-3MO26-T{i}") for i in range(6)]
    markets = {
        e["market_id"]: {"status": "finalized", "result": "yes"}
        for e in entries
    }
    out = _settler(tmp_path, entries, markets).settle_cycle()
    assert len(out) == 6
    assert all(r["outcome"] == "WIN" for r in out)
    # each pays the same full-value profit, independent of how many tied
    assert len({r["pnl_gross_usd"] for r in out}) == 1


# ── P&L is net of the taker fee ──────────────────────────────────────

def test_win_pnl_is_net_of_taker_fee(tmp_path):
    """Validation compares against a fee-net backtest baseline."""
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-W", size=100.0, fill=0.20,
                 contracts=500, taker_fee=0.0112)],
        {"KXPGATOP10-3MO26-W": {"status": "finalized", "result": "yes"}},
    )
    r = s.settle_cycle()[0]
    assert r["pnl_gross_usd"] == pytest.approx(400.0)   # 100 * .8/.2
    assert r["fees_usd"] == pytest.approx(5.6)          # 500 * .0112
    assert r["pnl_usd"] == pytest.approx(394.4)


def test_loss_pnl_includes_fee(tmp_path):
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-L", size=100.0, contracts=500,
                 taker_fee=0.0112)],
        {"KXPGATOP10-3MO26-L": {"status": "finalized", "result": "no"}},
    )
    r = s.settle_cycle()[0]
    assert r["pnl_gross_usd"] == pytest.approx(-100.0)
    assert r["pnl_usd"] == pytest.approx(-105.6)


def test_void_refunds_stake_but_not_fee(tmp_path):
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-V", contracts=500, taker_fee=0.0112)],
        {"KXPGATOP10-3MO26-V": {"status": "finalized", "result": "scalar"}},
    )
    r = s.settle_cycle()[0]
    assert r["pnl_gross_usd"] == 0.0
    assert r["pnl_usd"] == pytest.approx(-5.6)


# ── Stale guard ──────────────────────────────────────────────────────

def test_stale_position_voids_after_grace(tmp_path):
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-STUCK",
                 close_utc="2026-07-01T00:00:00+00:00")],
        {"KXPGATOP10-3MO26-STUCK": {"status": "closed", "result": ""}},
    )
    out = s.settle_cycle()
    assert len(out) == 1
    assert out[0]["outcome"] == "VOID"
    assert out[0]["resolution_source"] == "stale_timeout"


def test_recently_closed_is_not_stale(tmp_path):
    """Inside the grace window, keep waiting."""
    close = (NOW - timedelta(days=1)).isoformat()
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-FRESH", close_utc=close)],
        {"KXPGATOP10-3MO26-FRESH": {"status": "closed", "result": ""}},
    )
    assert s.settle_cycle() == []


def test_missing_market_does_not_crash(tmp_path):
    """API returns None (404 / transient) — skip, retry next cycle."""
    close = (NOW - timedelta(days=1)).isoformat()
    s = _settler(
        tmp_path,
        [_placed("KXPGATOP10-3MO26-GONE", close_utc=close)],
        {},
    )
    assert s.settle_cycle() == []


# ── Log bookkeeping ──────────────────────────────────────────────────

def test_already_settled_positions_are_skipped(tmp_path):
    placed = _placed("KXPGATOP10-3MO26-DONE")
    done = {**placed, "action": "WIN", "outcome": "WIN", "pnl_usd": 10.0}
    s = _settler(
        tmp_path, [placed, done],
        {"KXPGATOP10-3MO26-DONE": {"status": "finalized", "result": "yes"}},
    )
    assert s.settle_cycle() == []
    assert s._client.calls == []


def test_other_pods_entries_ignored(tmp_path):
    foreign = {**_placed("KXTENNIS-X"), "pod_id": "P-015"}
    s = _settler(
        tmp_path, [foreign],
        {"KXTENNIS-X": {"status": "finalized", "result": "yes"}},
    )
    assert s.settle_cycle() == []


def test_bankroll_reset_clears_prior_positions(tmp_path):
    placed = _placed("KXPGATOP10-3MO26-OLD")
    reset = {"pod_id": "P-017", "action": "BANKROLL_RESET", "market_id": "-"}
    s = _settler(
        tmp_path, [placed, reset],
        {"KXPGATOP10-3MO26-OLD": {"status": "finalized", "result": "yes"}},
    )
    assert s.settle_cycle() == []


def test_settlement_appended_to_log(tmp_path):
    log = _write_log(tmp_path, [_placed("KXPGATOP10-3MO26-AAA")])
    s = KalshiGolfSettler(
        client=FakeClient(
            {"KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "yes"}}
        ),
        log_path=log,
        _now_fn=lambda: NOW,
    )
    s.settle_cycle()
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[1]["outcome"] == "WIN"
    # a second pass must not double-settle
    assert s.settle_cycle() == []


# ── P&L helper ───────────────────────────────────────────────────────

@pytest.mark.parametrize("result,expected", [
    ("yes", "WIN"), ("no", "LOSS"), ("scalar", "VOID"), ("", "VOID"),
])
def test_calc_outcome_labels(result, expected):
    outcome, _ = _calc_outcome_pnl("YES", result, 100.0, 0.25)
    assert outcome == expected


# ── TradeStore is the real source/sink ───────────────────────────────
#
# Regression: the settler originally read data/pods/P-017.jsonl, copying
# the tennis settler.  With a TradeStore attached, BasePod.write_log
# delegates to store.append() and that per-pod file is NEVER written —
# verified in production, where 27 P-017 placements landed in the shared
# trade_log.jsonl and data/pods/ was empty.  A file-reading settler finds
# nothing and settles nothing, silently.


class FakeStore:
    """Minimal stand-in for TradeStore."""

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


def test_reads_open_positions_from_trade_store(tmp_path):
    """The per-pod log file is empty in production — use the store."""
    entry = {**_placed("KXPGATOP10-3MO26-AAA"), "fingerprint": "fp1"}
    store = FakeStore([entry])
    s = KalshiGolfSettler(
        client=FakeClient(
            {"KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "yes"}}
        ),
        log_path=tmp_path / "never-written.jsonl",   # deliberately absent
        _now_fn=lambda: NOW,
        trade_store=store,
    )
    out = s.settle_cycle()
    assert len(out) == 1
    assert out[0]["outcome"] == "WIN"


def test_settlement_persists_through_trade_store(tmp_path):
    """append() fsyncs to the shared log; the per-pod file would be lost."""
    entry = {**_placed("KXPGATOP10-3MO26-AAA"), "fingerprint": "fp1"}
    store = FakeStore([entry])
    s = KalshiGolfSettler(
        client=FakeClient(
            {"KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "no"}}
        ),
        log_path=tmp_path / "never-written.jsonl",
        _now_fn=lambda: NOW,
        trade_store=store,
    )
    s.settle_cycle()
    assert len(store.appended) == 1
    assert store.appended[0]["outcome"] == "LOSS"
    # nothing should have been written to the per-pod file
    assert not (tmp_path / "never-written.jsonl").exists()


def test_store_backed_settler_does_not_resettle(tmp_path):
    entry = {**_placed("KXPGATOP10-3MO26-AAA"), "fingerprint": "fp1"}
    store = FakeStore([entry])
    s = KalshiGolfSettler(
        client=FakeClient(
            {"KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "yes"}}
        ),
        log_path=tmp_path / "never-written.jsonl",
        _now_fn=lambda: NOW,
        trade_store=store,
    )
    assert len(s.settle_cycle()) == 1
    assert s.settle_cycle() == []


def test_store_lag_window_still_respected(tmp_path):
    """The closed-but-unsettled guard must hold on the store path too."""
    entry = {**_placed("KXPGATOP10-3MO26-AAA"), "fingerprint": "fp1"}
    store = FakeStore([entry])
    s = KalshiGolfSettler(
        client=FakeClient(
            {"KXPGATOP10-3MO26-AAA": {"status": "closed", "result": ""}}
        ),
        log_path=tmp_path / "never-written.jsonl",
        _now_fn=lambda: NOW,
        trade_store=store,
    )
    assert s.settle_cycle() == []
    assert store.appended == []


def test_store_path_filters_by_pod(tmp_path):
    mine = {**_placed("KXPGATOP10-3MO26-AAA"), "fingerprint": "fp1"}
    other = {**_placed("KXTENNIS-X"), "pod_id": "P-015", "fingerprint": "fp2"}
    store = FakeStore([mine, other])
    s = KalshiGolfSettler(
        client=FakeClient({
            "KXPGATOP10-3MO26-AAA": {"status": "finalized", "result": "yes"},
            "KXTENNIS-X": {"status": "finalized", "result": "yes"},
        }),
        log_path=tmp_path / "never-written.jsonl",
        _now_fn=lambda: NOW,
        trade_store=store,
    )
    out = s.settle_cycle()
    assert [r["market_id"] for r in out] == ["KXPGATOP10-3MO26-AAA"]
