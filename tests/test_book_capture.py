"""
tests/test_book_capture.py
──────────────────────────
Coverage for the book capture daemon.

Priority is the properties that would silently corrupt two weeks of
collection before anyone noticed:
  • the market cap actually derives from the rate budget (no silent caps)
  • the rate budget is genuinely enforced
  • YES-ask inversion from the NO side is right (the CLAUDE.md gotcha)
  • day rollover doesn't drop records
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from src.book_capture import (
    BookCapture, CaptureConfig, RateBudget, RotatingJsonlWriter,
    _depth_summary, max_markets_for_budget,
)


# ── budget arithmetic ────────────────────────────────────────────────

def test_max_markets_derives_from_budget():
    # 4 req/s * 60s * 0.6 safety = 144 request-slots / 2 per market = 72
    assert max_markets_for_budget(4.0, 60.0, 2, 0.6) == 72
    # halving the cadence halves coverage
    assert max_markets_for_budget(4.0, 30.0, 2, 0.6) == 36
    # dropping the trades call doubles it
    assert max_markets_for_budget(4.0, 60.0, 1, 0.6) == 144


def test_max_markets_never_zero():
    """A pathological config must still capture something, not divide to 0."""
    assert max_markets_for_budget(0.1, 1.0, 2, 0.1) >= 1


def test_rate_budget_enforces_rate():
    budget = RateBudget(rate_per_s=20.0, burst=1.0)
    start = time.monotonic()
    for _ in range(6):
        budget.acquire()
    elapsed = time.monotonic() - start
    # 6 tokens at 20/s with burst 1 => >= ~0.25s of waiting
    assert elapsed >= 0.2, f"budget did not throttle (elapsed={elapsed:.3f}s)"


def test_rate_budget_rejects_bad_rate():
    with pytest.raises(ValueError):
        RateBudget(0)


# ── book parsing ─────────────────────────────────────────────────────

def test_depth_summary_aggregates():
    out = _depth_summary([["0.40", "100"], ["0.39", "50"]], "yes")
    assert out["yes_levels"] == 2
    assert out["yes_best"] == pytest.approx(0.40)
    assert out["yes_contracts"] == pytest.approx(150.0)
    assert out["yes_notional"] == pytest.approx(0.40 * 100 + 0.39 * 50)


def test_depth_summary_handles_cents_denominated_fallback():
    """orderbook_fp is dollars, but the fallback path can be cents."""
    out = _depth_summary([["40", "100"]], "yes")
    assert out["yes_best"] == pytest.approx(0.40)


def test_depth_summary_skips_malformed_levels():
    out = _depth_summary([["0.40", "100"], None, ["bad"], []], "yes")
    assert out["yes_levels"] == 1


class _FakeKalshi:
    """Stands in for KalshiPublic. Records calls so we can assert on them."""

    def __init__(self, markets=None, book=None, trades=None):
        self._markets = markets if markets is not None else []
        self._book = book
        self._trades = trades or []
        self.calls = []

    def open_markets(self, series, max_pages=10):
        self.calls.append(("open_markets", series))
        return [dict(m, series=series) for m in self._markets]

    def get(self, path, params=None, retries=3, on_status=None):
        self.calls.append(("get", path))
        if on_status:
            on_status(200)
        return self._book

    def trades_since(self, ticker, min_epoch, max_pages=5):
        self.calls.append(("trades_since", ticker))
        return list(self._trades)


def _book(yes_levels, no_levels):
    return {"orderbook_fp": {"yes_dollars": yes_levels,
                             "no_dollars": no_levels}}


def test_yes_ask_is_inverted_from_no_bid(tmp_path):
    """The CLAUDE.md gotcha: best YES ask = 1 - best NO bid."""
    cfg = CaptureConfig(out_dir=tmp_path, capture_trades=False)
    cap = BookCapture(
        cfg,
        client=_FakeKalshi(book=_book([["0.40", "10"]], [["0.55", "10"]])),
        writer=RotatingJsonlWriter(tmp_path),
    )
    rec = cap.snapshot_market({"ticker": "T1", "series": "S"})
    assert rec["yes_bid"] == pytest.approx(0.40)
    assert rec["yes_ask"] == pytest.approx(0.45)      # 1 - 0.55
    assert rec["spread"] == pytest.approx(0.05)
    assert rec["mid"] == pytest.approx(0.425)


def test_snapshot_survives_empty_book(tmp_path):
    cfg = CaptureConfig(out_dir=tmp_path, capture_trades=False)
    cap = BookCapture(cfg, client=_FakeKalshi(book=_book([], [])),
                      writer=RotatingJsonlWriter(tmp_path))
    rec = cap.snapshot_market({"ticker": "T1", "series": "S"})
    assert rec["yes_bid"] is None and rec["yes_ask"] is None
    assert rec["mid"] is None and rec["spread"] is None


def test_snapshot_returns_none_when_api_fails(tmp_path):
    cfg = CaptureConfig(out_dir=tmp_path, capture_trades=False)
    cap = BookCapture(cfg, client=_FakeKalshi(book=None),
                      writer=RotatingJsonlWriter(tmp_path))
    assert cap.snapshot_market({"ticker": "T1", "series": "S"}) is None


# ── truncation is loud ───────────────────────────────────────────────

def test_discovery_truncates_to_budget_and_keeps_highest_volume(tmp_path):
    markets = [{"ticker": f"T{i}", "volume_24h_fp": str(i)} for i in range(50)]
    cfg = CaptureConfig(out_dir=tmp_path, rate_per_s=1.0, cadence_s=10.0,
                        safety=0.6, capture_trades=True)
    cap = BookCapture(cfg, client=_FakeKalshi(markets=markets),
                      writer=RotatingJsonlWriter(tmp_path))
    assert cap.max_markets == 3          # 1*10*0.6/2 = 3
    targets = cap.discover()
    assert len(targets) == 3
    # kept the three highest-volume markets
    assert [t["ticker"] for t in targets] == ["T49", "T48", "T47"]


def test_truncation_is_logged_and_recorded(tmp_path, caplog):
    """A silent cap reads downstream as full coverage. It must be loud."""
    markets = [{"ticker": f"T{i}", "volume_24h_fp": "1"} for i in range(50)]
    cfg = CaptureConfig(out_dir=tmp_path, rate_per_s=1.0, cadence_s=10.0)
    cap = BookCapture(cfg, client=_FakeKalshi(markets=markets),
                      writer=RotatingJsonlWriter(tmp_path))
    with caplog.at_level("WARNING"):
        cap.discover()
    assert "DROPPING" in caplog.text

    # and it must be on disk, so an analyst reading the data sees the gap
    rows = [json.loads(l) for l in
            (list(Path(tmp_path).glob("*.jsonl"))[0]).read_text().splitlines()]
    disc = [r for r in rows if r["type"] == "DISCOVERY"][0]
    assert disc["n_discovered"] == 50
    assert disc["n_captured"] == 3
    assert disc["n_dropped"] == 47


def test_no_truncation_when_budget_is_ample(tmp_path, caplog):
    markets = [{"ticker": f"T{i}", "volume_24h_fp": "1"} for i in range(5)]
    cfg = CaptureConfig(out_dir=tmp_path, rate_per_s=4.0, cadence_s=60.0)
    cap = BookCapture(cfg, client=_FakeKalshi(markets=markets),
                      writer=RotatingJsonlWriter(tmp_path))
    with caplog.at_level("WARNING"):
        targets = cap.discover()
    assert len(targets) == 5
    assert "DROPPING" not in caplog.text


# ── adaptive backoff ─────────────────────────────────────────────────
#
# Capture shares the API budget with two trading services and the main engine
# already 429s at peak. These tests pin the property that capture yields.

def test_penalize_halves_rate_with_floor():
    b = RateBudget(4.0)
    assert b.penalize() == pytest.approx(2.0)
    assert b.penalize() == pytest.approx(1.0)
    for _ in range(10):
        b.penalize()
    assert b.rate == pytest.approx(0.5), "must not throttle to zero"


def test_penalize_empties_bucket_so_backoff_is_immediate():
    b = RateBudget(4.0, burst=10.0)
    b.penalize()
    assert b._tokens == 0.0


def test_recover_drifts_up_but_never_past_ceiling():
    b = RateBudget(4.0)
    b.penalize()                       # 2.0
    for _ in range(100):
        b.recover(ceiling=4.0)
    assert b.rate == pytest.approx(4.0)


def test_429_triggers_throttle_down(tmp_path):
    class _Throttling(_FakeKalshi):
        def get(self, path, params=None, retries=3, on_status=None):
            if on_status:
                on_status(429)
            return self._book

    cfg = CaptureConfig(out_dir=tmp_path, rate_per_s=4.0, capture_trades=False)
    cap = BookCapture(cfg, client=_Throttling(book=_book([], [])),
                      writer=RotatingJsonlWriter(tmp_path))
    cap.snapshot_market({"ticker": "T1", "series": "S"})
    assert cap._429s == 1
    assert cap.budget.rate == pytest.approx(2.0)


def test_clean_cycle_recovers_rate(tmp_path):
    cfg = CaptureConfig(out_dir=tmp_path, rate_per_s=4.0, capture_trades=False)
    cap = BookCapture(
        cfg,
        client=_FakeKalshi(markets=[{"ticker": "T1"}],
                           book=_book([["0.4", "1"]], [["0.5", "1"]])),
        writer=RotatingJsonlWriter(tmp_path),
    )
    cap.budget.penalize()              # simulate an earlier 429 -> 2.0
    cap.cycle()
    assert cap.budget.rate > 2.0
    assert cap.budget.rate <= 4.0


def test_on_status_ignores_non_429(tmp_path):
    cfg = CaptureConfig(out_dir=tmp_path, rate_per_s=4.0)
    cap = BookCapture(cfg, client=_FakeKalshi(), writer=RotatingJsonlWriter(tmp_path))
    for code in (200, 404, 500, 503):
        cap._on_status(code)
    assert cap._429s == 0
    assert cap.budget.rate == pytest.approx(4.0)


# ── kill switch ──────────────────────────────────────────────────────

def test_kill_file_stops_capture(tmp_path):
    kill = tmp_path / "KILL_CAPTURE"
    kill.touch()
    cfg = CaptureConfig(out_dir=tmp_path, kill_file=kill)
    cap = BookCapture(cfg, client=_FakeKalshi(markets=[{"ticker": "T1"}]),
                      writer=RotatingJsonlWriter(tmp_path))
    assert cap.cycle() == 0


def test_cycle_writes_snapshots(tmp_path):
    cfg = CaptureConfig(out_dir=tmp_path, capture_trades=False)
    cap = BookCapture(
        cfg,
        client=_FakeKalshi(markets=[{"ticker": "T1"}, {"ticker": "T2"}],
                           book=_book([["0.40", "10"]], [["0.55", "10"]])),
        writer=RotatingJsonlWriter(tmp_path),
    )
    assert cap.cycle() == 2
    rows = [json.loads(l) for l in
            (list(Path(tmp_path).glob("*.jsonl"))[0]).read_text().splitlines()]
    assert len([r for r in rows if r["type"] == "BOOK"]) == 2


def test_cycle_continues_after_one_market_raises(tmp_path):
    """One bad ticker must not abort the whole cycle."""
    class _Flaky(_FakeKalshi):
        def get(self, path, params=None, retries=3, on_status=None):
            if "BAD" in path:
                raise RuntimeError("boom")
            return self._book

    cfg = CaptureConfig(out_dir=tmp_path, capture_trades=False)
    cap = BookCapture(
        cfg,
        client=_Flaky(markets=[{"ticker": "BAD"}, {"ticker": "GOOD"}],
                      book=_book([["0.40", "10"]], [["0.55", "10"]])),
        writer=RotatingJsonlWriter(tmp_path),
    )
    assert cap.cycle() == 1


# ── writer ───────────────────────────────────────────────────────────

def test_writer_rolls_day_without_losing_records(tmp_path):
    """Rotation must gzip the closed day and lose nothing.

    Reads back across both plain and gzipped files, because a roll can leave
    either form on disk depending on where the day boundary landed.
    """
    w = RotatingJsonlWriter(tmp_path, prefix="bc")
    w.write({"a": 1})
    w.write({"a": 2})
    w._roll("2026-07-19")          # simulate the UTC midnight boundary
    w.write({"a": 3})
    w.close()

    recovered = []
    for p in sorted(tmp_path.iterdir()):
        opener = (lambda q: gzip.open(q, "rt")) if p.suffix == ".gz" else \
                 (lambda q: open(q))
        with opener(p) as fh:
            recovered.extend(json.loads(l) for l in fh if l.strip())

    assert sorted(r["a"] for r in recovered) == [1, 2, 3]
    assert list(tmp_path.glob("*.jsonl.gz")), "closed day was not compressed"


def test_writer_reopens_existing_day_without_truncating(tmp_path):
    w1 = RotatingJsonlWriter(tmp_path, prefix="bc")
    w1.write({"a": 1})
    w1.close()
    w2 = RotatingJsonlWriter(tmp_path, prefix="bc")
    w2.write({"a": 2})
    w2.close()
    path = list(tmp_path.glob("*.jsonl"))[0]
    assert len(path.read_text().splitlines()) == 2


# ── config ───────────────────────────────────────────────────────────

def test_config_from_yaml_block():
    cfg = CaptureConfig.from_config({
        "book_capture": {"cadence_s": 30, "rate_per_s": 2.5,
                         "series": ["KXMLBGAME", "KXNBA"]}
    })
    assert cfg.cadence_s == 30
    assert cfg.rate_per_s == 2.5
    assert cfg.series == ["KXMLBGAME", "KXNBA"]


def test_config_defaults_when_block_absent():
    cfg = CaptureConfig.from_config({})
    assert cfg.series == ["KXMLBGAME"]
    assert cfg.cadence_s == 60.0
