"""
tests/test_dashboard.py
───────────────────────
Phase 8: tests for src/dashboard.py

Covers
──────
  _Style          — ANSI colours, pnl colouring, action colouring
  _divider/_col   — layout helpers
  _read_jsonl     — JSONL reader
  _write_csv      — CSV writer
  export_trade_log_csv / export_unmatched_csv  — module-level exports
  Dashboard       — construction, from_config, tick, render, all sections
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.dashboard import (
    Dashboard,
    _Style,
    _col,
    _divider,
    _read_jsonl,
    _write_csv,
    export_trade_log_csv,
    export_unmatched_csv,
)
from src.risk_manager import LedgerSnapshot, Position

# ── Shared helpers ────────────────────────────────────────────────────────────

_FIXED_NOW = datetime(2024, 1, 15, 18, 30, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return _FIXED_NOW


def _make_snapshot(
    bankroll: float = 1_000.0,
    daily_pnl_usd: float = 0.0,
    daily_pnl_pct: float = 0.0,
    exposure_usd: float = 0.0,
    exposure_pct: float = 0.0,
    peak_equity: float = 1_000.0,
    drawdown_usd: float = 0.0,
    drawdown_pct: float = 0.0,
    in_cooldown: bool = False,
    cooldown_until: Optional[datetime] = None,
    open_positions: int = 0,
) -> LedgerSnapshot:
    return LedgerSnapshot(
        bankroll=bankroll,
        open_positions=open_positions,
        exposure_usd=exposure_usd,
        exposure_pct=exposure_pct,
        daily_pnl_usd=daily_pnl_usd,
        daily_pnl_pct=daily_pnl_pct,
        peak_equity=peak_equity,
        drawdown_usd=drawdown_usd,
        drawdown_pct=drawdown_pct,
        in_cooldown=in_cooldown,
        cooldown_until=cooldown_until,
        as_of=_FIXED_NOW,
    )


def _make_ledger_mock(
    snapshot: Optional[LedgerSnapshot] = None,
    positions: Optional[list] = None,
) -> MagicMock:
    m = MagicMock()
    m.snapshot.return_value = snapshot or _make_snapshot()
    m.open_positions = positions or []
    return m


def _make_dashboard(
    snapshot: Optional[LedgerSnapshot] = None,
    positions: Optional[list] = None,
    trade_log_path: Optional[Path] = None,
    mode: str = "paper",
    color: bool = False,   # off by default → readable string assertions
    width: int = 80,
) -> Dashboard:
    ledger = _make_ledger_mock(snapshot, positions)
    return Dashboard(
        ledger=ledger,
        trade_log_path=trade_log_path or Path("/nonexistent/trade_log.jsonl"),
        mode=mode,
        color=color,
        width=width,
        _now_fn=_now,
    )


def _make_trade_entry(
    ticker: str = "NBA-2024-LAL-BOS",
    side: str = "YES",
    action: str = "PLACED",
    edge_pct: float = 0.05,
    size_usd: float = 25.0,
    ts: str = "2024-01-15T18:00:00+00:00",
) -> dict:
    return {
        "fingerprint": "abc123",
        "timestamp_utc": ts,
        "mode": "paper",
        "sport": "basketball_nba",
        "event": "Lakers vs Celtics",
        "market_ticker": ticker,
        "side": side,
        "fair_prob": 0.55,
        "kalshi_prob": 0.50,
        "edge_pct": edge_pct,
        "ev": 0.02,
        "kelly_fraction": 0.03,
        "position_size_usd": size_usd,
        "action": action,
        "skip_reason": None,
        "order_id": None,
        "fill_price": 0.50,
    }


# ══════════════════════════════════════════════════════════════════════════════
# _Style
# ══════════════════════════════════════════════════════════════════════════════

class TestStyle(unittest.TestCase):
    """ANSI colour helper."""

    def test_bold_enabled_wraps_code(self):
        self.assertIn("\033[1m", _Style(enabled=True).bold("x"))

    def test_green_enabled_wraps_code(self):
        self.assertIn("\033[92m", _Style(enabled=True).green("x"))

    def test_red_enabled_wraps_code(self):
        self.assertIn("\033[91m", _Style(enabled=True).red("x"))

    def test_yellow_enabled_wraps_code(self):
        self.assertIn("\033[93m", _Style(enabled=True).yellow("x"))

    def test_cyan_enabled_wraps_code(self):
        self.assertIn("\033[96m", _Style(enabled=True).cyan("x"))

    def test_dim_enabled_wraps_code(self):
        self.assertIn("\033[2m", _Style(enabled=True).dim("x"))

    def test_disabled_bold_plain(self):
        self.assertEqual(_Style(enabled=False).bold("hello"), "hello")

    def test_disabled_all_methods_plain(self):
        st = _Style(enabled=False)
        for method in (st.bold, st.dim, st.green, st.red, st.yellow, st.cyan):
            self.assertEqual(method("text"), "text",
                             f"{method.__name__} should return plain text")

    def test_pnl_positive_green(self):
        self.assertIn("\033[92m", _Style(enabled=True).pnl(10.0))

    def test_pnl_negative_red(self):
        self.assertIn("\033[91m", _Style(enabled=True).pnl(-5.0))

    def test_pnl_zero_no_colour(self):
        result = _Style(enabled=True).pnl(0.0)
        self.assertNotIn("\033[9", result)   # no colour escape

    def test_pnl_format_string(self):
        result = _Style(enabled=False).pnl(12.5)
        self.assertIn("12.50", result)

    def test_action_placed_green(self):
        self.assertIn("\033[92m", _Style(enabled=True).action("PLACED"))

    def test_action_executed_green(self):
        self.assertIn("\033[92m", _Style(enabled=True).action("EXECUTED"))

    def test_action_skipped_edge_yellow(self):
        self.assertIn("\033[93m", _Style(enabled=True).action("SKIPPED_EDGE"))

    def test_action_skipped_risk_yellow(self):
        self.assertIn("\033[93m", _Style(enabled=True).action("SKIPPED_RISK"))

    def test_action_error_red(self):
        self.assertIn("\033[91m", _Style(enabled=True).action("ERROR"))

    def test_action_unknown_no_colour(self):
        result = _Style(enabled=True).action("UNKNOWN_CODE")
        self.assertNotIn("\033[", result)


# ══════════════════════════════════════════════════════════════════════════════
# Layout helpers: _col, _divider
# ══════════════════════════════════════════════════════════════════════════════

class TestLayoutHelpers(unittest.TestCase):

    def test_col_left_align_pads_to_width(self):
        result = _col("hi", 10)
        self.assertEqual(len(result), 10)
        self.assertTrue(result.startswith("hi"))

    def test_col_right_align(self):
        result = _col("hi", 10, ">")
        self.assertTrue(result.endswith("hi"))
        self.assertEqual(len(result), 10)

    def test_col_truncates_long_input(self):
        result = _col("abcde", 3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result, "abc")

    def test_col_exact_length(self):
        self.assertEqual(_col("abc", 3), "abc")

    def test_divider_contains_title(self):
        st = _Style(enabled=False)
        self.assertIn("ACCOUNT", _divider("ACCOUNT", 80, st))

    def test_divider_plain_length_equals_width(self):
        st = _Style(enabled=False)
        # With colour disabled the string length equals width
        self.assertEqual(len(_divider("SECTION", 80, st)), 80)


# ══════════════════════════════════════════════════════════════════════════════
# _read_jsonl
# ══════════════════════════════════════════════════════════════════════════════

class TestReadJsonl(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_returns_empty(self):
        self.assertEqual(_read_jsonl(self.tmp / "nope.jsonl"), [])

    def test_reads_all_valid_lines(self):
        f = self.tmp / "data.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
        rows = _read_jsonl(f)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"a": 1})
        self.assertEqual(rows[1], {"b": 2})

    def test_skips_malformed_lines(self):
        f = self.tmp / "data.jsonl"
        f.write_text('{"a":1}\nnot-json\n{"c":3}\n', encoding="utf-8")
        self.assertEqual(len(_read_jsonl(f)), 2)

    def test_skips_blank_lines(self):
        f = self.tmp / "data.jsonl"
        f.write_text('{"a":1}\n\n{"b":2}\n\n', encoding="utf-8")
        self.assertEqual(len(_read_jsonl(f)), 2)


# ══════════════════════════════════════════════════════════════════════════════
# _write_csv
# ══════════════════════════════════════════════════════════════════════════════

class TestWriteCsv(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_row_count(self):
        rows = [{"x": 1}, {"x": 2}]
        n = _write_csv(rows, self.tmp / "out.csv", ["x"])
        self.assertEqual(n, 2)

    def test_writes_header(self):
        path = self.tmp / "out.csv"
        _write_csv([{"a": 1}], path, ["a", "b"])
        with path.open() as fh:
            reader = csv.DictReader(fh)
            self.assertIn("a", reader.fieldnames)
            self.assertIn("b", reader.fieldnames)

    def test_missing_field_becomes_empty_string(self):
        path = self.tmp / "out.csv"
        _write_csv([{"x": 1}], path, ["x", "y"])
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(next(reader)["y"], "")

    def test_creates_parent_directories(self):
        path = self.tmp / "sub" / "nested" / "out.csv"
        _write_csv([{"a": 1}], path, ["a"])
        self.assertTrue(path.exists())

    def test_returns_zero_on_os_error(self):
        # make the parent path an existing file → mkdir will fail
        blocker = self.tmp / "file"
        blocker.write_text("block")
        path = blocker / "out.csv"   # parent is a file → OSError
        self.assertEqual(_write_csv([{"a": 1}], path, ["a"]), 0)


# ══════════════════════════════════════════════════════════════════════════════
# export_trade_log_csv
# ══════════════════════════════════════════════════════════════════════════════

class TestExportTradeLogCsv(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_jsonl_returns_zero(self):
        n = export_trade_log_csv(self.tmp / "missing.jsonl", self.tmp / "out.csv")
        self.assertEqual(n, 0)

    def test_exports_correct_row_count(self):
        jsonl = self.tmp / "log.jsonl"
        jsonl.write_text(
            json.dumps(_make_trade_entry()) + "\n"
            + json.dumps(_make_trade_entry(ticker="NFL-KC-LV")) + "\n"
        )
        n = export_trade_log_csv(jsonl, self.tmp / "out.csv")
        self.assertEqual(n, 2)

    def test_csv_has_standard_columns(self):
        jsonl = self.tmp / "log.jsonl"
        jsonl.write_text(json.dumps(_make_trade_entry()) + "\n")
        out = self.tmp / "out.csv"
        export_trade_log_csv(jsonl, out)
        with out.open() as fh:
            reader = csv.DictReader(fh)
            for col in ("market_ticker", "action", "edge_pct", "position_size_usd"):
                self.assertIn(col, reader.fieldnames)

    def test_custom_fieldnames_honoured(self):
        jsonl = self.tmp / "log.jsonl"
        jsonl.write_text(
            json.dumps({"market_ticker": "TEST", "action": "PLACED"}) + "\n"
        )
        out = self.tmp / "out.csv"
        export_trade_log_csv(jsonl, out, fieldnames=["market_ticker", "action"])
        with out.open() as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, ["market_ticker", "action"])


# ══════════════════════════════════════════════════════════════════════════════
# export_unmatched_csv
# ══════════════════════════════════════════════════════════════════════════════

class TestExportUnmatchedCsv(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _unmatched_row(self, ticker: str = "NBA-LAL") -> dict:
        return {
            "timestamp_utc": "2024-01-15T18:00:00+00:00",
            "market_ticker": ticker,
            "market_title": "Lakers game",
            "event_start_utc": "2024-01-15T20:00:00+00:00",
            "reason": "NO_FUZZY_MATCH",
            "best_candidate": "Lakers vs Heat",
            "best_score": 45.0,
        }

    def test_missing_jsonl_returns_zero(self):
        n = export_unmatched_csv(self.tmp / "missing.jsonl", self.tmp / "out.csv")
        self.assertEqual(n, 0)

    def test_exports_correct_row_count(self):
        jsonl = self.tmp / "unmatched.jsonl"
        jsonl.write_text(
            json.dumps(self._unmatched_row()) + "\n"
            + json.dumps(self._unmatched_row("NFL-KC")) + "\n"
        )
        self.assertEqual(export_unmatched_csv(jsonl, self.tmp / "out.csv"), 2)

    def test_csv_has_unmatched_columns(self):
        jsonl = self.tmp / "unmatched.jsonl"
        jsonl.write_text(json.dumps(self._unmatched_row()) + "\n")
        out = self.tmp / "out.csv"
        export_unmatched_csv(jsonl, out)
        with out.open() as fh:
            reader = csv.DictReader(fh)
            for col in ("market_ticker", "reason", "best_candidate", "best_score"):
                self.assertIn(col, reader.fieldnames)


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard construction and from_config
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardConstruction(unittest.TestCase):

    def test_default_mode_paper(self):
        self.assertEqual(_make_dashboard()._mode, "paper")

    def test_paper_mode_stored(self):
        self.assertEqual(_make_dashboard(mode="paper")._mode, "paper")

    def test_live_mode_stored(self):
        ledger = _make_ledger_mock()
        d = Dashboard(ledger=ledger, mode="live", color=False, _now_fn=_now)
        self.assertEqual(d._mode, "live")

    def test_tick_starts_at_zero(self):
        self.assertEqual(_make_dashboard()._scan_count, 0)

    def test_tick_increments_each_call(self):
        d = _make_dashboard()
        d.tick()
        d.tick()
        d.tick()
        self.assertEqual(d._scan_count, 3)

    def test_from_config_demo_becomes_paper(self):
        config = {"environment": "demo", "paths": {}}
        d = Dashboard.from_config(config, _make_ledger_mock(), color=False, _now_fn=_now)
        self.assertEqual(d._mode, "paper")

    def test_from_config_paper_mode(self):
        config = {"environment": "paper", "paths": {}}
        d = Dashboard.from_config(config, _make_ledger_mock(), color=False, _now_fn=_now)
        self.assertEqual(d._mode, "paper")

    def test_from_config_live_mode(self):
        config = {"environment": "live", "paths": {}}
        d = Dashboard.from_config(config, _make_ledger_mock(), color=False, _now_fn=_now)
        self.assertEqual(d._mode, "live")

    def test_from_config_custom_trade_log_path(self):
        config = {"paths": {"trade_log": "custom/logs/trade_log.jsonl"}}
        d = Dashboard.from_config(config, _make_ledger_mock(), color=False, _now_fn=_now)
        self.assertEqual(str(d._trade_log), "custom/logs/trade_log.jsonl")

    def test_from_config_default_trade_log_path(self):
        config = {"paths": {}}
        d = Dashboard.from_config(config, _make_ledger_mock(), color=False, _now_fn=_now)
        self.assertIn("trade_log.jsonl", str(d._trade_log))


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard._header
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardHeader(unittest.TestCase):

    def test_contains_date(self):
        self.assertIn("2024-01-15", _make_dashboard()._header())

    def test_contains_time(self):
        self.assertIn("18:30:00", _make_dashboard()._header())

    def test_paper_mode_label(self):
        self.assertIn("paper", _make_dashboard(mode="paper")._header().lower())

    def test_live_mode_label(self):
        ledger = _make_ledger_mock()
        d = Dashboard(ledger=ledger, mode="live", color=False, width=80, _now_fn=_now)
        self.assertIn("LIVE", d._header())

    def test_scan_count_shown(self):
        d = _make_dashboard()
        d.tick()
        d.tick()
        d.tick()
        self.assertIn("3", d._header())


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard._account_section
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardAccountSection(unittest.TestCase):

    def test_shows_bankroll(self):
        snap = _make_snapshot(bankroll=5_000.0)
        self.assertIn("5,000.00", _make_dashboard(snapshot=snap)._account_section())

    def test_shows_positive_daily_pnl(self):
        snap = _make_snapshot(daily_pnl_usd=150.0)
        self.assertIn("150", _make_dashboard(snapshot=snap)._account_section())

    def test_shows_negative_daily_pnl(self):
        snap = _make_snapshot(daily_pnl_usd=-75.0)
        self.assertIn("75", _make_dashboard(snapshot=snap)._account_section())

    def test_shows_peak_equity(self):
        snap = _make_snapshot(peak_equity=1_200.0)
        self.assertIn("1,200.00", _make_dashboard(snapshot=snap)._account_section())

    def test_shows_drawdown_nonzero(self):
        snap = _make_snapshot(
            bankroll=900.0, peak_equity=1_000.0,
            drawdown_usd=100.0, drawdown_pct=0.10,
        )
        self.assertIn("100", _make_dashboard(snapshot=snap)._account_section())

    def test_cooldown_active_shows_time(self):
        cool_until = datetime(2024, 1, 15, 19, 0, 0, tzinfo=timezone.utc)
        snap = _make_snapshot(in_cooldown=True, cooldown_until=cool_until)
        section = _make_dashboard(snapshot=snap)._account_section()
        self.assertIn("19:00:00", section)

    def test_no_cooldown_shows_no(self):
        snap = _make_snapshot(in_cooldown=False)
        self.assertIn("No", _make_dashboard(snapshot=snap)._account_section())

    def test_section_label_present(self):
        self.assertIn("ACCOUNT", _make_dashboard()._account_section())


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard._positions_section
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardPositionsSection(unittest.TestCase):

    def test_empty_shows_no_positions_message(self):
        section = _make_dashboard(positions=[])._positions_section()
        self.assertIn("No open positions", section)

    def test_count_reflected(self):
        pos = Position.new("NBA-LAL", "YES", 50.0, 0.55, _FIXED_NOW)
        section = _make_dashboard(positions=[pos])._positions_section()
        self.assertIn("1", section)

    def test_ticker_shown_in_row(self):
        pos = Position.new("NBA-LAL-BOS-01", "YES", 50.0, 0.55, _FIXED_NOW)
        section = _make_dashboard(positions=[pos])._positions_section()
        self.assertIn("NBA-LAL-BOS-01", section)

    def test_yes_side_shown(self):
        pos = Position.new("NBA-X", "YES", 50.0, 0.55, _FIXED_NOW)
        self.assertIn("YES", _make_dashboard(positions=[pos])._positions_section())

    def test_no_side_shown(self):
        pos = Position.new("NBA-X", "NO", 30.0, 0.45, _FIXED_NOW)
        self.assertIn("NO", _make_dashboard(positions=[pos])._positions_section())

    def test_size_shown(self):
        pos = Position.new("NBA-X", "YES", 42.50, 0.55, _FIXED_NOW)
        self.assertIn("42.50", _make_dashboard(positions=[pos])._positions_section())


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard._trades_section
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardTradesSection(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_log_shows_no_trades_message(self):
        d = _make_dashboard(trade_log_path=self.tmp / "missing.jsonl")
        self.assertIn("No trades logged", d._trades_section())

    def test_shows_trade_ticker(self):
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps(_make_trade_entry(ticker="NBA-2024-LAL-BOST")) + "\n")
        section = _make_dashboard(trade_log_path=log)._trades_section()
        self.assertIn("NBA-2024-LAL", section)

    def test_shows_action_code(self):
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps(_make_trade_entry(action="PLACED")) + "\n")
        self.assertIn("PLACED", _make_dashboard(trade_log_path=log)._trades_section())

    def test_shows_skipped_action(self):
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps(_make_trade_entry(action="SKIPPED_EDGE")) + "\n")
        self.assertIn("SKIPPED_EDGE",
                      _make_dashboard(trade_log_path=log)._trades_section())

    def test_newest_first_ordering(self):
        log = self.tmp / "log.jsonl"
        e1 = _make_trade_entry(ticker="TICK-OLDER", ts="2024-01-15T16:00:00+00:00")
        e2 = _make_trade_entry(ticker="TICK-NEWER", ts="2024-01-15T17:00:00+00:00")
        with log.open("w") as fh:
            fh.write(json.dumps(e1) + "\n")
            fh.write(json.dumps(e2) + "\n")
        section = _make_dashboard(trade_log_path=log)._trades_section()
        self.assertLess(section.index("TICK-NEWER"), section.index("TICK-OLDER"))

    def test_max_trades_limits_output(self):
        log = self.tmp / "log.jsonl"
        with log.open("w") as fh:
            for i in range(20):
                fh.write(json.dumps(_make_trade_entry(ticker=f"TK-{i:03d}")) + "\n")
        ledger = _make_ledger_mock()
        d = Dashboard(
            ledger=ledger,
            trade_log_path=log,
            color=False,
            width=80,
            max_recent_trades=5,
            _now_fn=_now,
        )
        section = d._trades_section()
        shown = sum(1 for i in range(20) if f"TK-{i:03d}" in section)
        self.assertEqual(shown, 5)

    def test_section_label_present(self):
        d = _make_dashboard(trade_log_path=self.tmp / "missing.jsonl")
        self.assertIn("RECENT TRADES", d._trades_section())


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard.render (integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardRender(unittest.TestCase):

    def test_render_returns_string(self):
        self.assertIsInstance(_make_dashboard().render(), str)

    def test_render_contains_account_section(self):
        self.assertIn("ACCOUNT", _make_dashboard().render())

    def test_render_contains_positions_section(self):
        self.assertIn("OPEN POSITIONS", _make_dashboard().render())

    def test_render_contains_trades_section(self):
        self.assertIn("RECENT TRADES", _make_dashboard().render())

    def test_render_color_false_no_ansi(self):
        self.assertNotIn("\033[", _make_dashboard(color=False).render())

    def test_render_color_true_has_ansi(self):
        ledger = _make_ledger_mock(_make_snapshot(bankroll=1_000.0))
        d = Dashboard(ledger=ledger, color=True, width=80, _now_fn=_now)
        self.assertIn("\033[", d.render())

    def test_render_after_tick_shows_scan_count(self):
        d = _make_dashboard()
        d.tick()
        d.tick()
        self.assertIn("2", d.render())


if __name__ == "__main__":
    unittest.main()
