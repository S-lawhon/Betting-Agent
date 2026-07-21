"""
tests/test_settler.py
─────────────────────
Settlement engine must-pass tests:

  _calc_outcome_pnl
  ✓ YES wins → WIN, correct profit
  ✓ YES loses → LOSS, -size_usd
  ✓ NO wins → WIN, correct profit
  ✓ NO loses → LOSS, -size_usd
  ✓ void result → VOID, 0.0
  ✓ pnl rounded to 2 decimal places

  _open_placed_entries
  ✓ returns empty dict when log file missing
  ✓ returns PLACED entries with positive size
  ✓ skips PLACED entries with zero size
  ✓ excludes tickers already resolved (WIN/LOSS/VOID outcome)
  ✓ skips malformed JSON lines silently

  settle_cycle
  ✓ settled market (YES wins) → WIN record appended to log
  ✓ settled market (NO wins)  → WIN record appended to log
  ✓ settled market (LOSS)     → LOSS record appended, pnl=-size
  ✓ settled market (void)     → VOID record appended, pnl=0
  ✓ open market (not settled) → no record written
  ✓ API error → skipped gracefully, no crash
  ✓ already-resolved ticker not re-queried

  rebuild_ledger_from_log
  ✓ opens positions in ledger for all unresolved PLACED entries
  ✓ resolved entries not reopened
  ✓ zero-size entries skipped
  ✓ returns count of reopened positions

  Ledger.close_by_ticker
  ✓ closes matching open position, returns ClosedPosition
  ✓ returns None when ticker not found
  ✓ updates bankroll correctly after close
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from src.kalshi_client import KalshiAPIError
from src.risk_manager import Ledger, Position
from src.settler import Settler, _calc_outcome_pnl


# ── pytest.approx compatibility shim (pytest not available in this env) ──────

class _Approx:
    """Lightweight drop-in for pytest.approx using math.isclose."""
    def __init__(self, expected, rel=1e-6, abs=1e-12):
        self._expected = expected
        self._rel = rel
        self._abs = abs

    def __eq__(self, other):
        return math.isclose(other, self._expected, rel_tol=self._rel, abs_tol=self._abs)

    def __repr__(self):
        return f"≈{self._expected}"

class _PytestShim:
    @staticmethod
    def approx(expected, rel=1e-6, abs=1e-12):
        return _Approx(expected, rel=rel, abs=abs)

pytest = _PytestShim()

# ── Fixtures ──────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)


def _now_fn() -> datetime:
    return _NOW


def _ledger(initial: float = 10_000.0) -> Ledger:
    return Ledger(initial_bankroll=initial, _now_fn=_now_fn)


def _settler(tmp_path: Path, kalshi: Any = None, ledger: Any = None) -> Settler:
    if kalshi is None:
        kalshi = MagicMock()
    if ledger is None:
        ledger = _ledger()
    log_path = tmp_path / "trade_log.jsonl"
    return Settler(
        kalshi=kalshi,
        ledger=ledger,
        log_path=log_path,
        _now_fn=_now_fn,
    )


def _write_placed(log_path: Path, records: List[Dict[str, Any]]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _placed_record(ticker: str, side: str = "YES",
                   size: float = 300.0, prob: float = 0.5) -> Dict[str, Any]:
    return {
        "action": "PLACED",
        "market_ticker": ticker,
        "side": side,
        "position_size_usd": size,
        "kalshi_prob": prob,
        "timestamp_utc": "2026-02-20T10:00:00+00:00",
        "fingerprint": f"fp_{ticker}",
    }


def _market_response(status: str = "settled", result: str = "yes") -> Dict[str, Any]:
    return {"market": {"ticker": "TICK", "status": status, "result": result}}


# ── _calc_outcome_pnl ─────────────────────────────────────────────────────────

class TestCalcOutcomePnl:

    def test_yes_wins(self):
        outcome, pnl = _calc_outcome_pnl("YES", "yes", 300.0, 0.5)
        assert outcome == "WIN"
        # profit = 300 * (1 - 0.5) / 0.5 = 300
        assert pnl == pytest.approx(300.0, rel=1e-4)

    def test_yes_loses(self):
        outcome, pnl = _calc_outcome_pnl("YES", "no", 300.0, 0.5)
        assert outcome == "LOSS"
        assert pnl == -300.0

    def test_no_wins(self):
        outcome, pnl = _calc_outcome_pnl("NO", "no", 300.0, 0.7)
        # profit = 300 * 0.7 / (1 - 0.7) = 300 * 7/3 = 700
        assert outcome == "WIN"
        assert pnl == pytest.approx(700.0, rel=1e-4)

    def test_no_loses(self):
        outcome, pnl = _calc_outcome_pnl("NO", "yes", 300.0, 0.7)
        assert outcome == "LOSS"
        assert pnl == -300.0

    def test_void(self):
        outcome, pnl = _calc_outcome_pnl("YES", "void", 300.0, 0.5)
        assert outcome == "VOID"
        assert pnl == 0.0

    def test_no_void(self):
        outcome, pnl = _calc_outcome_pnl("NO", "void", 100.0, 0.3)
        assert outcome == "VOID"
        assert pnl == 0.0

    def test_pnl_rounded(self):
        # size=100, prob=0.3 → profit = 100 * 0.7 / 0.3 = 233.333...
        outcome, pnl = _calc_outcome_pnl("YES", "yes", 100.0, 0.3)
        assert outcome == "WIN"
        assert isinstance(pnl, float)
        assert round(pnl, 2) == pnl   # must be rounded to 2 dp

    def test_yes_at_low_prob(self):
        # YES at 10¢; profit should be large
        outcome, pnl = _calc_outcome_pnl("YES", "yes", 100.0, 0.1)
        assert outcome == "WIN"
        assert pnl == pytest.approx(900.0, rel=1e-3)

    def test_case_insensitive(self):
        # side and result should be case-insensitive
        outcome, pnl = _calc_outcome_pnl("yes", "YES", 200.0, 0.5)
        assert outcome == "WIN"
        assert pnl == pytest.approx(200.0, rel=1e-4)


# ── _open_placed_entries ──────────────────────────────────────────────────────

class TestOpenPlacedEntries:

    def test_missing_log_returns_empty(self, tmp_path):
        s = _settler(tmp_path)
        assert s._open_placed_entries() == {}

    def test_returns_placed_entries(self, tmp_path):
        s = _settler(tmp_path)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1"), _placed_record("TICK-2")])
        result = s._open_placed_entries()
        assert set(result.keys()) == {"TICK-1", "TICK-2"}

    def test_skips_zero_size(self, tmp_path):
        s = _settler(tmp_path)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [
            _placed_record("TICK-1", size=0.0),
            _placed_record("TICK-2", size=300.0),
        ])
        result = s._open_placed_entries()
        assert "TICK-1" not in result
        assert "TICK-2" in result

    def test_excludes_resolved_ticker(self, tmp_path):
        s = _settler(tmp_path)
        log_path = tmp_path / "trade_log.jsonl"
        records = [
            _placed_record("TICK-1"),
            {
                "action": "WIN",
                "outcome": "WIN",
                "market_ticker": "TICK-1",
                "pnl_usd": 150.0,
                "position_size_usd": 300.0,
            },
            _placed_record("TICK-2"),
        ]
        _write_placed(log_path, records)
        result = s._open_placed_entries()
        assert "TICK-1" not in result
        assert "TICK-2" in result

    def test_loss_outcome_excludes(self, tmp_path):
        s = _settler(tmp_path)
        log_path = tmp_path / "trade_log.jsonl"
        records = [
            _placed_record("TICK-1"),
            {"action": "LOSS", "outcome": "LOSS", "market_ticker": "TICK-1",
             "pnl_usd": -300.0, "position_size_usd": 300.0},
        ]
        _write_placed(log_path, records)
        assert s._open_placed_entries() == {}

    def test_void_outcome_excludes(self, tmp_path):
        s = _settler(tmp_path)
        log_path = tmp_path / "trade_log.jsonl"
        records = [
            _placed_record("TICK-1"),
            {"action": "VOID", "outcome": "VOID", "market_ticker": "TICK-1",
             "pnl_usd": 0.0, "position_size_usd": 300.0},
        ]
        _write_placed(log_path, records)
        assert s._open_placed_entries() == {}

    def test_malformed_json_skipped(self, tmp_path):
        s = _settler(tmp_path)
        log_path = tmp_path / "trade_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as fh:
            fh.write("not-valid-json\n")
            fh.write(json.dumps(_placed_record("TICK-1")) + "\n")
        result = s._open_placed_entries()
        assert "TICK-1" in result


# ── settle_cycle ──────────────────────────────────────────────────────────────

class TestSettleCycle:

    def test_yes_wins(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "yes")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1", side="YES", prob=0.5)])

        results = s.settle_cycle()

        assert len(results) == 1
        rec = results[0]
        assert rec["outcome"] == "WIN"
        assert rec["pnl_usd"] == pytest.approx(300.0)
        assert rec["result"] == "yes"
        assert rec["market_ticker"] == "TICK-1"

    def test_no_wins_on_no_result(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "no")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1", side="NO", prob=0.7)])

        results = s.settle_cycle()

        assert results[0]["outcome"] == "WIN"
        assert results[0]["pnl_usd"] == pytest.approx(700.0, rel=1e-3)

    def test_loss_written_correctly(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "no")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1", side="YES", prob=0.5)])

        results = s.settle_cycle()

        assert results[0]["outcome"] == "LOSS"
        assert results[0]["pnl_usd"] == -300.0

    def test_void_written_correctly(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "void")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1", side="YES", prob=0.5)])

        results = s.settle_cycle()

        assert results[0]["outcome"] == "VOID"
        assert results[0]["pnl_usd"] == 0.0

    def test_open_market_not_settled(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("open", "")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1")])

        results = s.settle_cycle()
        assert results == []

    def test_closed_market_not_yet_settled(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("closed", "")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1")])

        results = s.settle_cycle()
        assert results == []

    def test_api_error_skipped_gracefully(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.side_effect = KalshiAPIError(404, "not found")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1")])

        results = s.settle_cycle()   # must not raise
        assert results == []

    def test_network_error_skipped_gracefully(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.side_effect = ConnectionError("timeout")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1")])

        results = s.settle_cycle()   # must not raise
        assert results == []

    def test_already_resolved_not_re_queried(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "yes")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        records = [
            _placed_record("TICK-1"),
            {"action": "WIN", "outcome": "WIN", "market_ticker": "TICK-1",
             "pnl_usd": 300.0, "position_size_usd": 300.0},
        ]
        _write_placed(log_path, records)

        results = s.settle_cycle()

        assert results == []
        kalshi.get_market.assert_not_called()

    def test_settlement_record_appended_to_log(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "yes")
        s = _settler(tmp_path, kalshi=kalshi)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1")])

        s.settle_cycle()

        # Log should now have 2 lines: original PLACED + settlement
        lines = [
            json.loads(l) for l in log_path.read_text().splitlines() if l.strip()
        ]
        assert len(lines) == 2
        assert lines[1]["outcome"] == "WIN"
        assert lines[1]["settled_at_utc"] is not None

    def test_no_open_positions_returns_empty(self, tmp_path):
        kalshi = MagicMock()
        s = _settler(tmp_path, kalshi=kalshi)
        # No log file at all
        results = s.settle_cycle()
        assert results == []
        kalshi.get_market.assert_not_called()

    def test_settle_cycle_closes_ledger_position(self, tmp_path):
        kalshi = MagicMock()
        kalshi.get_market.return_value = _market_response("settled", "yes")
        ledger = _ledger(10_000.0)
        s = _settler(tmp_path, kalshi=kalshi, ledger=ledger)

        # Open a position in the ledger
        pos = Position.new("TICK-1", "YES", 300.0, 0.5, opened_at=_NOW)
        ledger.open_position(pos)
        assert ledger.open_count == 1

        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1", side="YES", prob=0.5)])

        s.settle_cycle()

        # Position should be closed
        assert ledger.open_count == 0
        # Bankroll should have increased by profit
        assert ledger.bankroll == pytest.approx(10_000.0 + 300.0, rel=1e-4)


# ── rebuild_ledger_from_log ───────────────────────────────────────────────────

class TestRebuildLedgerFromLog:

    def test_opens_unresolved_positions(self, tmp_path):
        ledger = _ledger(10_000.0)
        s = _settler(tmp_path, ledger=ledger)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [
            _placed_record("TICK-1"),
            _placed_record("TICK-2"),
        ])

        count = s.rebuild_ledger_from_log()

        assert count == 2
        assert ledger.open_count == 2

    def test_resolved_not_reopened(self, tmp_path):
        ledger = _ledger(10_000.0)
        s = _settler(tmp_path, ledger=ledger)
        log_path = tmp_path / "trade_log.jsonl"
        records = [
            _placed_record("TICK-1"),
            {"action": "WIN", "outcome": "WIN", "market_ticker": "TICK-1",
             "pnl_usd": 300.0, "position_size_usd": 300.0},
            _placed_record("TICK-2"),   # still open
        ]
        _write_placed(log_path, records)

        count = s.rebuild_ledger_from_log()

        assert count == 1
        assert ledger.open_count == 1
        assert ledger.open_positions[0].market_ticker == "TICK-2"

    def test_zero_size_skipped(self, tmp_path):
        ledger = _ledger()
        s = _settler(tmp_path, ledger=ledger)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [_placed_record("TICK-1", size=0.0)])

        count = s.rebuild_ledger_from_log()

        assert count == 0
        assert ledger.open_count == 0

    def test_returns_count(self, tmp_path):
        ledger = _ledger()
        s = _settler(tmp_path, ledger=ledger)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [
            _placed_record("T1"), _placed_record("T2"), _placed_record("T3")
        ])

        count = s.rebuild_ledger_from_log()
        assert count == 3

    def test_missing_log_returns_zero(self, tmp_path):
        ledger = _ledger()
        s = _settler(tmp_path, ledger=ledger)
        count = s.rebuild_ledger_from_log()
        assert count == 0

    def test_exposure_reflected_in_ledger(self, tmp_path):
        ledger = _ledger(10_000.0)
        s = _settler(tmp_path, ledger=ledger)
        log_path = tmp_path / "trade_log.jsonl"
        _write_placed(log_path, [
            _placed_record("T1", size=300.0),
            _placed_record("T2", size=500.0),
        ])

        s.rebuild_ledger_from_log()

        assert ledger.exposure_usd == pytest.approx(800.0)


# ── Ledger.close_by_ticker ────────────────────────────────────────────────────

class TestLedgerCloseByTicker:

    def test_closes_matching_position(self):
        ledger = _ledger(10_000.0)
        pos = Position.new("TICK-1", "YES", 300.0, 0.5, opened_at=_NOW)
        ledger.open_position(pos)

        closed = ledger.close_by_ticker("TICK-1", 150.0)

        assert closed is not None
        assert closed.pnl_usd == 150.0
        assert closed.position.market_ticker == "TICK-1"
        assert ledger.open_count == 0

    def test_returns_none_when_not_found(self):
        ledger = _ledger(10_000.0)
        result = ledger.close_by_ticker("NONEXISTENT", 0.0)
        assert result is None

    def test_bankroll_updated_on_close(self):
        ledger = _ledger(10_000.0)
        pos = Position.new("TICK-1", "YES", 300.0, 0.5, opened_at=_NOW)
        ledger.open_position(pos)

        ledger.close_by_ticker("TICK-1", 300.0)   # WIN profit = $300

        assert ledger.bankroll == pytest.approx(10_300.0)

    def test_bankroll_decremented_on_loss(self):
        ledger = _ledger(10_000.0)
        pos = Position.new("TICK-1", "YES", 300.0, 0.5, opened_at=_NOW)
        ledger.open_position(pos)

        ledger.close_by_ticker("TICK-1", -300.0)   # LOSS

        assert ledger.bankroll == pytest.approx(9_700.0)

    def test_only_first_matching_ticker_closed(self):
        """Should only close one position even if two share the same ticker
        (edge case guard)."""
        ledger = _ledger(10_000.0)
        pos1 = Position.new("TICK-1", "YES", 300.0, 0.5, opened_at=_NOW)
        pos2 = Position.new("TICK-1", "YES", 200.0, 0.5, opened_at=_NOW)
        ledger.open_position(pos1)
        ledger.open_position(pos2)

        closed = ledger.close_by_ticker("TICK-1", 100.0)

        assert closed is not None
        assert ledger.open_count == 1   # one still open
