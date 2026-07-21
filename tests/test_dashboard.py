"""
tests/test_dashboard.py
───────────────────────
Tests for src/dashboard.py — the terminal dashboard renderer.

All tests run with use_color=False so ANSI codes do not interfere with
string assertions.  A separate section verifies that color mode does
inject ANSI codes.
"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.dashboard import (
    DashboardRenderer,
    _C,
    _pnl_color,
    _pnl_str,
    _rpad,
    _strip_ansi,
    _vlen,
    colorize,
    render_cycle_panel,
    render_header,
    render_pods_table,
    render_risk_panel,
    render_settlement_bar,
    render_two_column,
)


# ── Mock factories ────────────────────────────────────────────────────

def _snap(
    total_exposure_usd=1_250.0,
    total_exposure_pct=0.125,
    daily_pnl=147.0,
    daily_pnl_pct=0.0147,
    open_positions=8,
    halted=False,
    halt_reason=None,
):
    return SimpleNamespace(
        total_exposure_usd=total_exposure_usd,
        total_exposure_pct=total_exposure_pct,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        open_positions=open_positions,
        halted=halted,
        halt_reason=halt_reason,
        venue_exposure={},
        pod_exposure={},
        timestamp_utc="2026-01-15T14:32:07+00:00",
    )


def _report(
    cycle_number=42,
    duration_seconds=0.43,
    pods_scanned=4,
    placed_count=3,
    skipped_count=12,
    error_count=0,
    total_results=15,
):
    obj = SimpleNamespace(
        cycle_number=cycle_number,
        duration_seconds=duration_seconds,
        pods_scanned=pods_scanned,
        placed_count=placed_count,
        skipped_count=skipped_count,
        error_count=error_count,
        total_results=total_results,
        results=[],
    )
    obj.success_rate = (
        (total_results - error_count) / total_results if total_results else 1.0
    )
    return obj


def _perf(pod_id, placed=10, wins=6, losses=4, pnl=150.0):
    return SimpleNamespace(
        pod_id=pod_id,
        total_placed=placed,
        total_wins=wins,
        total_losses=losses,
        total_pnl=pnl,
        win_rate=(wins / (wins + losses)) if (wins + losses) else 0.0,
    )


def _alloc(pod_id, alloc_pct=0.20, max_pos=200.0):
    return SimpleNamespace(
        pod_id=pod_id,
        allocation_pct=alloc_pct,
        allocated_usd=alloc_pct * 10_000,
        max_position_usd=max_pos,
        active=True,
    )


def _settle(total_pnl=437.25, total_settled=38, wins=24, losses=12, voids=2,
            win_rate=0.615):
    return dict(
        total_pnl=total_pnl,
        total_settled=total_settled,
        wins=wins,
        losses=losses,
        voids=voids,
        win_rate=win_rate,
    )


# ── TestColorizeAndHelpers ────────────────────────────────────────────

class TestColorizeAndHelpers(unittest.TestCase):

    def test_colorize_contains_text(self):
        result = colorize("hello", _C.GREEN)
        self.assertIn("hello", result)

    def test_colorize_contains_reset(self):
        result = colorize("x", _C.RED)
        self.assertIn(_C.RESET, result)

    def test_colorize_bold_contains_bold_code(self):
        result = colorize("x", _C.GREEN, bold=True)
        self.assertIn(_C.BOLD, result)

    def test_colorize_no_bold_no_bold_code(self):
        result = colorize("x", _C.GREEN, bold=False)
        self.assertNotIn(_C.BOLD, result)

    def test_strip_ansi_removes_codes(self):
        raw = "\033[1m\033[32mhello\033[0m"
        self.assertEqual(_strip_ansi(raw), "hello")

    def test_strip_ansi_plain_unchanged(self):
        self.assertEqual(_strip_ansi("plain text"), "plain text")

    def test_vlen_plain_string(self):
        self.assertEqual(_vlen("hello"), 5)

    def test_vlen_with_ansi_codes(self):
        colored = colorize("hello", _C.GREEN)
        self.assertEqual(_vlen(colored), 5)

    def test_rpad_pads_to_width(self):
        result = _rpad("ab", 10)
        self.assertEqual(len(result), 10)

    def test_rpad_no_truncation(self):
        long_str = "a" * 20
        result = _rpad(long_str, 10)
        self.assertEqual(result, long_str)

    def test_pnl_color_positive_is_green(self):
        self.assertEqual(_pnl_color(1.0), _C.GREEN)

    def test_pnl_color_negative_is_red(self):
        self.assertEqual(_pnl_color(-1.0), _C.RED)

    def test_pnl_color_zero_is_dim(self):
        self.assertEqual(_pnl_color(0.0), _C.DIM)

    def test_pnl_str_positive_has_plus(self):
        self.assertTrue(_pnl_str(50.0).startswith("+"))

    def test_pnl_str_negative_has_minus(self):
        self.assertTrue(_pnl_str(-50.0).startswith("-"))

    def test_pnl_str_zero_has_plus(self):
        self.assertTrue(_pnl_str(0.0).startswith("+"))

    def test_pnl_str_contains_dollar(self):
        self.assertIn("$", _pnl_str(100.0))


# ── TestRenderHeader ──────────────────────────────────────────────────

class TestRenderHeader(unittest.TestCase):

    def test_returns_three_lines(self):
        lines = render_header(width=78, timestamp="2026-01-01T00:00:00 UTC")
        self.assertEqual(len(lines), 3)

    def test_first_line_is_rule(self):
        lines = render_header(width=78, timestamp="ts")
        self.assertRegex(lines[0], r"^═+$")

    def test_last_line_is_rule(self):
        lines = render_header(width=78, timestamp="ts")
        self.assertRegex(lines[2], r"^═+$")

    def test_title_in_body_line(self):
        lines = render_header(width=78, timestamp="ts")
        self.assertIn("BETTING POD SHOP", lines[1])

    def test_custom_timestamp_in_body_line(self):
        ts = "2026-06-15 10:00:00 UTC"
        lines = render_header(width=78, timestamp=ts)
        self.assertIn(ts, lines[1])

    def test_width_respected_by_rule(self):
        lines = render_header(width=60, timestamp="ts")
        self.assertEqual(len(lines[0]), 60)


# ── TestRenderRiskPanel ───────────────────────────────────────────────

class TestRenderRiskPanel(unittest.TestCase):

    def _lines(self, **kwargs) -> str:
        return "\n".join(render_risk_panel(_snap(**kwargs), use_color=False))

    def test_contains_exposure_usd(self):
        text = self._lines(total_exposure_usd=1_250.0)
        self.assertIn("1,250.00", text)

    def test_contains_exposure_pct(self):
        text = self._lines(total_exposure_pct=0.125)
        self.assertIn("12.5%", text)

    def test_contains_daily_pnl(self):
        text = self._lines(daily_pnl=147.0)
        self.assertIn("+$147.00", text)

    def test_negative_pnl_has_minus(self):
        text = self._lines(daily_pnl=-50.0)
        self.assertIn("-$50.00", text)

    def test_contains_positions(self):
        text = self._lines(open_positions=8)
        self.assertIn("8 open", text)

    def test_active_status_text(self):
        text = self._lines(halted=False)
        self.assertIn("ACTIVE", text)

    def test_halted_status_text(self):
        text = self._lines(halted=True, halt_reason="daily_loss")
        self.assertIn("HALTED", text)

    def test_halt_reason_in_output(self):
        text = self._lines(halted=True, halt_reason="daily_loss")
        self.assertIn("daily_loss", text)

    def test_no_color_no_ansi(self):
        lines = render_risk_panel(_snap(), use_color=False)
        for line in lines:
            self.assertEqual(line, _strip_ansi(line))

    def test_color_mode_has_ansi(self):
        lines = render_risk_panel(_snap(), use_color=True)
        combined = "".join(lines)
        self.assertIn("\033[", combined)

    def test_returns_list_of_strings(self):
        result = render_risk_panel(_snap(), use_color=False)
        self.assertIsInstance(result, list)
        self.assertTrue(all(isinstance(s, str) for s in result))


# ── TestRenderCyclePanel ──────────────────────────────────────────────

class TestRenderCyclePanel(unittest.TestCase):

    def _text(self, **kwargs) -> str:
        return "\n".join(render_cycle_panel(_report(**kwargs), use_color=False))

    def test_contains_cycle_number(self):
        text = self._text(cycle_number=42)
        self.assertIn("42", text)

    def test_contains_duration(self):
        text = self._text(duration_seconds=0.43)
        self.assertIn("0.43", text)

    def test_contains_pods_scanned(self):
        text = self._text(pods_scanned=4)
        self.assertIn("4", text)

    def test_contains_placed_count(self):
        text = self._text(placed_count=3)
        self.assertIn("3", text)

    def test_contains_skipped_count(self):
        text = self._text(skipped_count=12)
        self.assertIn("12", text)

    def test_contains_error_count(self):
        text = self._text(error_count=2)
        self.assertIn("2", text)

    def test_success_rate_100_pct(self):
        text = self._text(total_results=10, error_count=0)
        self.assertIn("100%", text)

    def test_success_rate_partial(self):
        text = self._text(total_results=10, error_count=2)
        self.assertIn("80%", text)

    def test_no_color_no_ansi(self):
        lines = render_cycle_panel(_report(), use_color=False)
        for line in lines:
            self.assertEqual(line, _strip_ansi(line))


# ── TestRenderTwoColumn ───────────────────────────────────────────────

class TestRenderTwoColumn(unittest.TestCase):

    def test_output_count_equals_max(self):
        left  = ["a", "b", "c"]
        right = ["x", "y"]
        result = render_two_column(left, right, col_width=10)
        self.assertEqual(len(result), 3)

    def test_longer_right_padded(self):
        left  = ["a"]
        right = ["x", "y", "z"]
        result = render_two_column(left, right, col_width=10)
        self.assertEqual(len(result), 3)

    def test_left_is_padded_to_col_width(self):
        result = render_two_column(["ab"], ["right"], col_width=10)
        # Left should be padded so "right" starts at visual position 10
        line = result[0]
        plain = _strip_ansi(line)
        self.assertEqual(plain[10:], "right")

    def test_empty_inputs_returns_empty(self):
        self.assertEqual(render_two_column([], []), [])

    def test_left_only(self):
        result = render_two_column(["hello"], [], col_width=10)
        self.assertEqual(len(result), 1)
        self.assertIn("hello", result[0])


# ── TestRenderPodsTable ───────────────────────────────────────────────

class TestRenderPodsTable(unittest.TestCase):

    def _text(self, perfs, allocs=None) -> str:
        return "\n".join(render_pods_table(perfs, allocs, width=78, use_color=False))

    def test_empty_performances_placeholder(self):
        text = self._text({})
        self.assertIn("no performance data", text)

    def test_pod_id_in_output(self):
        text = self._text({"P-001": _perf("P-001")})
        self.assertIn("P-001", text)

    def test_placed_count_in_output(self):
        text = self._text({"P-001": _perf("P-001", placed=10)})
        self.assertIn("10", text)

    def test_pnl_in_output(self):
        text = self._text({"P-001": _perf("P-001", pnl=234.5)})
        self.assertIn("+$234.50", text)

    def test_negative_pnl_in_output(self):
        text = self._text({"P-001": _perf("P-001", pnl=-50.0)})
        self.assertIn("-$50.00", text)

    def test_totals_row_present(self):
        text = self._text({"P-001": _perf("P-001"), "P-006": _perf("P-006")})
        self.assertIn("TOTAL", text)

    def test_totals_pnl_sums_pods(self):
        perfs = {
            "P-001": _perf("P-001", pnl=100.0),
            "P-006": _perf("P-006", pnl=50.0),
        }
        text = self._text(perfs)
        self.assertIn("+$150.00", text)

    def test_win_pct_in_output(self):
        # 6 wins, 4 losses → 60.0%
        text = self._text({"P-001": _perf("P-001", wins=6, losses=4)})
        self.assertIn("60.0%", text)

    def test_with_allocations_shows_alloc_pct(self):
        perfs  = {"P-001": _perf("P-001")}
        allocs = {"P-001": _alloc("P-001", alloc_pct=0.20)}
        text = self._text(perfs, allocs)
        self.assertIn("20.0%", text)

    def test_multiple_pods_all_listed(self):
        perfs = {
            "P-001": _perf("P-001"),
            "P-006": _perf("P-006"),
            "P-009": _perf("P-009"),
        }
        text = self._text(perfs)
        self.assertIn("P-001", text)
        self.assertIn("P-006", text)
        self.assertIn("P-009", text)

    def test_pods_sorted_by_id(self):
        perfs = {"P-006": _perf("P-006"), "P-001": _perf("P-001")}
        text = self._text(perfs)
        self.assertLess(text.index("P-001"), text.index("P-006"))

    def test_no_color_no_ansi(self):
        lines = render_pods_table({"P-001": _perf("P-001")}, width=78, use_color=False)
        for line in lines:
            self.assertEqual(line, _strip_ansi(line))


# ── TestRenderSettlementBar ───────────────────────────────────────────

class TestRenderSettlementBar(unittest.TestCase):

    def _text(self, **kwargs) -> str:
        return "\n".join(render_settlement_bar(_settle(**kwargs), use_color=False))

    def test_positive_pnl_in_output(self):
        text = self._text(total_pnl=437.25)
        self.assertIn("+$437.25", text)

    def test_negative_pnl_in_output(self):
        text = self._text(total_pnl=-120.0)
        self.assertIn("-$120.00", text)

    def test_win_rate_in_output(self):
        text = self._text(win_rate=0.615)
        self.assertIn("61.5%", text)

    def test_trade_counts_in_output(self):
        text = self._text(total_settled=38)
        self.assertIn("38", text)

    def test_wins_losses_voids_in_output(self):
        text = self._text(wins=24, losses=12, voids=2)
        self.assertIn("W:24", text)
        self.assertIn("L:12", text)
        self.assertIn("V:2", text)

    def test_no_color_no_ansi(self):
        lines = render_settlement_bar(_settle(), use_color=False)
        for line in lines:
            self.assertEqual(line, _strip_ansi(line))

    def test_returns_one_line(self):
        lines = render_settlement_bar(_settle(), use_color=False)
        self.assertEqual(len(lines), 1)


# ── TestDashboardRenderer ─────────────────────────────────────────────

class TestDashboardRenderer(unittest.TestCase):

    def _renderer(self, **kwargs):
        kwargs.setdefault("use_color", False)
        kwargs.setdefault("width", 78)
        return DashboardRenderer(**kwargs)

    def test_use_color_property(self):
        r = self._renderer(use_color=False)
        self.assertFalse(r.use_color)

    def test_use_color_true_property(self):
        r = self._renderer(use_color=True)
        self.assertTrue(r.use_color)

    def test_width_property(self):
        r = self._renderer(width=90)
        self.assertEqual(r.width, 90)

    def test_render_returns_string(self):
        r = self._renderer()
        output = r.render(_snap(), _report())
        self.assertIsInstance(output, str)

    def test_render_nonempty(self):
        r = self._renderer()
        output = r.render(_snap(), _report())
        self.assertGreater(len(output), 100)

    def test_render_contains_exposure(self):
        r = self._renderer()
        output = r.render(_snap(total_exposure_usd=5_000.0), _report())
        self.assertIn("5,000.00", output)

    def test_render_contains_daily_pnl(self):
        r = self._renderer()
        output = r.render(_snap(daily_pnl=99.99), _report())
        self.assertIn("99.99", output)

    def test_render_contains_cycle_number(self):
        r = self._renderer()
        output = r.render(_snap(), _report(cycle_number=99))
        self.assertIn("99", output)

    def test_render_contains_placed_count(self):
        r = self._renderer()
        output = r.render(_snap(), _report(placed_count=7))
        self.assertIn("7", output)

    def test_render_without_settlement(self):
        r = self._renderer()
        output = r.render(_snap(), _report(), settlement_summary=None)
        # Should not raise; settlement bar simply omitted
        self.assertIsInstance(output, str)

    def test_render_with_settlement(self):
        r = self._renderer()
        output = r.render(_snap(), _report(), settlement_summary=_settle(total_pnl=200.0))
        self.assertIn("+$200.00", output)

    def test_render_with_performances(self):
        r = self._renderer()
        perfs = {"P-001": _perf("P-001", pnl=123.0)}
        output = r.render(_snap(), _report(), performances=perfs)
        self.assertIn("P-001", output)
        self.assertIn("+$123.00", output)

    def test_render_with_allocations(self):
        r = self._renderer()
        perfs  = {"P-001": _perf("P-001")}
        allocs = {"P-001": _alloc("P-001", alloc_pct=0.15)}
        output = r.render(_snap(), _report(), performances=perfs, allocations=allocs)
        self.assertIn("15.0%", output)

    def test_render_no_color_no_ansi(self):
        r = self._renderer(use_color=False)
        output = r.render(_snap(), _report())
        self.assertEqual(output, _strip_ansi(output))

    def test_render_color_mode_has_ansi(self):
        r = self._renderer(use_color=True)
        output = r.render(_snap(), _report())
        self.assertIn("\033[", output)

    def test_clear_and_render_starts_with_escape(self):
        r = self._renderer()
        output = r.clear_and_render(_snap(), _report())
        self.assertTrue(output.startswith("\033["))

    def test_clear_and_render_contains_dashboard(self):
        r = self._renderer()
        output = r.clear_and_render(_snap(), _report())
        self.assertIn("BETTING POD SHOP", output)

    def test_render_halted_state(self):
        r = self._renderer()
        output = r.render(_snap(halted=True, halt_reason="daily_loss"), _report())
        self.assertIn("HALTED", output)
        self.assertIn("daily_loss", output)

    def test_render_ends_with_double_rule(self):
        r = self._renderer(width=78)
        output = r.render(_snap(), _report())
        self.assertTrue(output.rstrip().endswith("═" * 78))


# ── TestMakeCycleCallbackWithRenderer ─────────────────────────────────

class TestMakeCycleCallbackWithRenderer(unittest.TestCase):
    """Verify that the renderer is called when injected into make_cycle_callback."""

    def test_renderer_clear_and_render_called(self):
        from unittest.mock import MagicMock
        from src.main import make_cycle_callback
        from src.pod_runner import CycleReport

        guard = MagicMock()
        guard.update_post_cycle.return_value = None
        guard.snapshot.return_value = _snap()

        renderer = MagicMock()
        renderer.clear_and_render.return_value = "dashboard output"

        cb = make_cycle_callback(guard, renderer=renderer)

        report = CycleReport(
            cycle_number=1,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            duration_seconds=0.1,
            pods_scanned=1,
            total_results=1,
            placed_count=1,
            skipped_count=0,
            error_count=0,
        )
        cb(report)
        renderer.clear_and_render.assert_called_once()

    def test_no_renderer_does_not_crash(self):
        from unittest.mock import MagicMock
        from src.main import make_cycle_callback
        from src.pod_runner import CycleReport

        guard = MagicMock()
        guard.update_post_cycle.return_value = None
        guard.snapshot.return_value = _snap()

        cb = make_cycle_callback(guard, renderer=None)
        report = CycleReport(
            cycle_number=1,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            duration_seconds=0.1,
            pods_scanned=1,
            total_results=1,
            placed_count=0,
            skipped_count=1,
            error_count=0,
        )
        cb(report)  # should not raise


# ── TestMainDashboardFlag ─────────────────────────────────────────────

class TestMainDashboardFlag(unittest.TestCase):
    """Verify the --dashboard argument is registered in the CLI parser."""

    def test_dashboard_flag_defaults_false(self):
        from src.main import _build_parser
        args = _build_parser().parse_args(["--once"])
        self.assertFalse(args.dashboard)

    def test_dashboard_flag_long_form(self):
        from src.main import _build_parser
        args = _build_parser().parse_args(["--once", "--dashboard"])
        self.assertTrue(args.dashboard)

    def test_dashboard_flag_short_form(self):
        from src.main import _build_parser
        args = _build_parser().parse_args(["--once", "-d"])
        self.assertTrue(args.dashboard)


if __name__ == "__main__":
    unittest.main()
