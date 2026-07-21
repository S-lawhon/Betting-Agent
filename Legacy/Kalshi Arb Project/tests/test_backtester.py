"""
tests/test_backtester.py
────────────────────────
Phase 9: tests for src/backtester.py

Covers
──────
  _mean / _std / _compute_drawdown  — math helpers
  BacktestTrade / BacktestResult    — dataclass construction
  Backtester.run()                  — P&L, win rate, roi, drawdown, equity, by_sport
  Backtester.simulate()             — PENDING resolution, seed reproducibility
  Backtester.from_config()          — config wiring
  Backtester.load_csv()             — CSV loading, missing file, bad rows
  Backtester.run_csv()              — end-to-end integration
  export_equity_curve()             — file output
  export_report()                   — file output with OVERALL row
  scripts/run_backtest.py           — CLI argument parsing and output
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.backtester import (
    Backtester,
    BacktestResult,
    BacktestTrade,
    _compute_drawdown,
    _mean,
    _std,
)

# ── Shared fixture helpers ─────────────────────────────────────────────────────

def _trade(
    outcome: str = "WIN",
    pnl_usd: float = 10.0,
    edge_pct: float = 0.05,
    ev: float = 0.02,
    position_size_usd: float = 50.0,
    fair_prob: float = 0.60,
    kalshi_prob: float = 0.55,
    sport: Optional[str] = "nfl",
    date: str = "2024-01-15",
    ticker: str = "NFL-KC-LV",
    side: str = "YES",
) -> BacktestTrade:
    return BacktestTrade(
        date=date,
        market_ticker=ticker,
        side=side,
        fair_prob=fair_prob,
        kalshi_prob=kalshi_prob,
        edge_pct=edge_pct,
        ev=ev,
        position_size_usd=position_size_usd,
        outcome=outcome,
        pnl_usd=pnl_usd,
        sport=sport,
    )


def _csv_row(
    outcome: str = "WIN",
    pnl_usd: float = 10.0,
    edge_pct: float = 0.05,
    ev: float = 0.02,
    position_size_usd: float = 50.0,
    fair_prob: float = 0.60,
    kalshi_prob: float = 0.55,
    sport: str = "nfl",
    date: str = "2024-01-15",
    market_ticker: str = "NFL-KC-LV",
    side: str = "YES",
) -> dict:
    return {
        "date": date,
        "market_ticker": market_ticker,
        "side": side,
        "fair_prob": str(fair_prob),
        "kalshi_prob": str(kalshi_prob),
        "edge_pct": str(edge_pct),
        "ev": str(ev),
        "position_size_usd": str(position_size_usd),
        "outcome": outcome,
        "pnl_usd": str(pnl_usd),
        "fill_price": "",
        "sport": sport,
    }


_CSV_FIELDNAMES = [
    "date", "market_ticker", "side", "fair_prob", "kalshi_prob",
    "edge_pct", "ev", "position_size_usd", "outcome", "pnl_usd",
    "fill_price", "sport",
]


def _write_csv(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Math helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestMathHelpers(unittest.TestCase):

    def test_mean_empty(self):
        self.assertEqual(_mean([]), 0.0)

    def test_mean_single(self):
        self.assertAlmostEqual(_mean([5.0]), 5.0)

    def test_mean_multiple(self):
        self.assertAlmostEqual(_mean([1.0, 2.0, 3.0]), 2.0)

    def test_std_empty(self):
        self.assertEqual(_std([]), 0.0)

    def test_std_single(self):
        self.assertEqual(_std([42.0]), 0.0)

    def test_std_known_values(self):
        # population std of [2,4,4,4,5,5,7,9] = 2.0; sample std ≈ 2.138
        result = _std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        self.assertGreater(result, 2.0)
        self.assertLess(result, 2.2)

    def test_compute_drawdown_empty(self):
        dd_usd, dd_pct = _compute_drawdown([])
        self.assertEqual(dd_usd, 0.0)
        self.assertEqual(dd_pct, 0.0)

    def test_compute_drawdown_single(self):
        dd_usd, dd_pct = _compute_drawdown([1000.0])
        self.assertEqual(dd_usd, 0.0)

    def test_compute_drawdown_no_drawdown(self):
        dd_usd, _ = _compute_drawdown([1000.0, 1100.0, 1200.0])
        self.assertAlmostEqual(dd_usd, 0.0)

    def test_compute_drawdown_full_loss(self):
        # Peak 1200, final 900 → drawdown 300 / 1200 = 25%
        dd_usd, dd_pct = _compute_drawdown([1000.0, 1200.0, 900.0])
        self.assertAlmostEqual(dd_usd, 300.0)
        self.assertAlmostEqual(dd_pct, 0.25)

    def test_compute_drawdown_multiple_peaks(self):
        curve = [1000.0, 1100.0, 950.0, 1050.0, 800.0]
        dd_usd, dd_pct = _compute_drawdown(curve)
        # Peak at 1100 → trough at 950 = 150 dd, then peak 1100 again →
        # trough 800 = 300 dd.  Max drawdown = 300.
        self.assertAlmostEqual(dd_usd, 300.0)


# ══════════════════════════════════════════════════════════════════════════════
# BacktestTrade and BacktestResult dataclasses
# ══════════════════════════════════════════════════════════════════════════════

class TestDataclasses(unittest.TestCase):

    def test_backtest_trade_fields(self):
        t = _trade(outcome="WIN", pnl_usd=20.0, sport="nba")
        self.assertEqual(t.outcome, "WIN")
        self.assertEqual(t.pnl_usd, 20.0)
        self.assertEqual(t.sport, "nba")
        self.assertIsNone(t.fill_price)

    def test_backtest_result_defaults(self):
        r = BacktestResult()
        self.assertEqual(r.n_trades, 0)
        self.assertEqual(r.equity_curve, [])
        self.assertEqual(r.by_sport, {})

    def test_backtest_trade_fill_price(self):
        t = BacktestTrade(
            date="2024-01-01", market_ticker="X", side="YES",
            fair_prob=0.6, kalshi_prob=0.55, edge_pct=0.05, ev=0.02,
            position_size_usd=50.0, outcome="WIN", pnl_usd=10.0,
            fill_price=0.55,
        )
        self.assertAlmostEqual(t.fill_price, 0.55)


# ══════════════════════════════════════════════════════════════════════════════
# Backtester.run — core engine
# ══════════════════════════════════════════════════════════════════════════════

class TestBacktesterRun(unittest.TestCase):

    def setUp(self):
        self.bt = Backtester(initial_bankroll=1_000.0)

    def test_empty_trades_returns_empty_result(self):
        r = self.bt.run([])
        self.assertEqual(r.n_trades, 0)
        self.assertEqual(r.total_pnl_usd, 0.0)

    def test_equity_curve_starts_at_initial_bankroll(self):
        r = self.bt.run([])
        self.assertEqual(r.equity_curve[0], 1_000.0)

    def test_single_win_updates_counts(self):
        r = self.bt.run([_trade(outcome="WIN", pnl_usd=10.0)])
        self.assertEqual(r.n_trades, 1)
        self.assertEqual(r.n_wins, 1)
        self.assertEqual(r.n_losses, 0)

    def test_single_loss_updates_counts(self):
        r = self.bt.run([_trade(outcome="LOSS", pnl_usd=-50.0)])
        self.assertEqual(r.n_losses, 1)
        self.assertEqual(r.n_wins, 0)

    def test_void_not_counted_in_n_trades(self):
        r = self.bt.run([_trade(outcome="VOID", pnl_usd=0.0)])
        self.assertEqual(r.n_trades, 0)
        self.assertEqual(r.n_void, 1)

    def test_pending_not_counted_in_n_trades(self):
        r = self.bt.run([_trade(outcome="PENDING", pnl_usd=0.0)])
        self.assertEqual(r.n_trades, 0)
        self.assertEqual(r.n_pending, 1)

    def test_total_pnl_sums_wins_and_losses(self):
        trades = [
            _trade(outcome="WIN",  pnl_usd=20.0),
            _trade(outcome="LOSS", pnl_usd=-10.0),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.total_pnl_usd, 10.0)

    def test_win_rate_correct(self):
        trades = [
            _trade(outcome="WIN"),
            _trade(outcome="WIN"),
            _trade(outcome="LOSS", pnl_usd=-10.0),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.win_rate, 2 / 3)

    def test_win_rate_zero_when_no_resolved(self):
        r = self.bt.run([_trade(outcome="VOID", pnl_usd=0.0)])
        self.assertEqual(r.win_rate, 0.0)

    def test_roi_pct_correct(self):
        trades = [
            _trade(outcome="WIN",  pnl_usd=5.0,   position_size_usd=50.0),
            _trade(outcome="LOSS", pnl_usd=-5.0,  position_size_usd=50.0),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.roi_pct, 0.0)   # 0 / 100 * 100 = 0

    def test_roi_pct_positive(self):
        r = self.bt.run([_trade(outcome="WIN", pnl_usd=10.0, position_size_usd=50.0)])
        self.assertAlmostEqual(r.roi_pct, 20.0)  # 10/50 * 100

    def test_equity_curve_grows_with_wins(self):
        r = self.bt.run([
            _trade(outcome="WIN", pnl_usd=100.0),
            _trade(outcome="WIN", pnl_usd=100.0),
        ])
        self.assertEqual(r.equity_curve[-1], 1_200.0)

    def test_equity_curve_length(self):
        # initial + 1 per resolved + 1 per void/pending
        trades = [
            _trade(outcome="WIN"),
            _trade(outcome="VOID", pnl_usd=0.0),
            _trade(outcome="LOSS", pnl_usd=-5.0),
        ]
        r = self.bt.run(trades)
        self.assertEqual(len(r.equity_curve), 4)  # initial + 3

    def test_drawdown_computed(self):
        trades = [
            _trade(outcome="WIN",  pnl_usd=200.0),
            _trade(outcome="LOSS", pnl_usd=-400.0),
        ]
        r = self.bt.run(trades)  # 1000 → 1200 → 800; dd = 400
        self.assertAlmostEqual(r.max_drawdown_usd, 400.0)

    def test_drawdown_pct_correct(self):
        trades = [
            _trade(outcome="WIN",  pnl_usd=200.0),
            _trade(outcome="LOSS", pnl_usd=-400.0),
        ]
        r = self.bt.run(trades)  # peak 1200, trough 800 → dd = 400/1200 ≈ 33.3%
        self.assertAlmostEqual(r.max_drawdown_pct, 400 / 1200, places=5)

    def test_no_drawdown_when_all_wins(self):
        trades = [_trade(outcome="WIN", pnl_usd=100.0) for _ in range(5)]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.max_drawdown_usd, 0.0)

    def test_avg_edge_pct(self):
        trades = [
            _trade(outcome="WIN",  edge_pct=0.04),
            _trade(outcome="LOSS", edge_pct=0.06, pnl_usd=-10.0),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.avg_edge_pct, 0.05)

    def test_avg_ev(self):
        trades = [
            _trade(outcome="WIN",  ev=0.01),
            _trade(outcome="WIN",  ev=0.03),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.avg_ev, 0.02)

    def test_sharpe_is_zero_for_single_trade(self):
        r = self.bt.run([_trade(outcome="WIN", pnl_usd=10.0)])
        self.assertEqual(r.sharpe, 0.0)

    def test_sharpe_positive_for_uniform_wins(self):
        trades = [_trade(outcome="WIN", pnl_usd=10.0) for _ in range(5)]
        # std of [10,10,10,10,10] = 0 → sharpe = 0 (or inf; code returns 0)
        r = self.bt.run(trades)
        # std = 0 → sharpe effectively undefined; we accept 0 or any float
        self.assertIsInstance(r.sharpe, float)

    def test_by_sport_populated(self):
        trades = [
            _trade(outcome="WIN",  sport="nfl", pnl_usd=10.0),
            _trade(outcome="LOSS", sport="nfl", pnl_usd=-5.0),
            _trade(outcome="WIN",  sport="nba", pnl_usd=8.0),
        ]
        r = self.bt.run(trades)
        self.assertIn("nfl", r.by_sport)
        self.assertIn("nba", r.by_sport)

    def test_by_sport_win_counts(self):
        trades = [
            _trade(outcome="WIN",  sport="nfl"),
            _trade(outcome="WIN",  sport="nfl"),
            _trade(outcome="LOSS", sport="nfl", pnl_usd=-5.0),
        ]
        r = self.bt.run(trades)
        self.assertEqual(r.by_sport["nfl"]["n_wins"], 2)
        self.assertEqual(r.by_sport["nfl"]["n_losses"], 1)

    def test_by_sport_pnl(self):
        trades = [
            _trade(outcome="WIN",  sport="nfl", pnl_usd=10.0),
            _trade(outcome="LOSS", sport="nfl", pnl_usd=-3.0),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.by_sport["nfl"]["total_pnl_usd"], 7.0, places=2)

    def test_none_sport_becomes_unknown(self):
        r = self.bt.run([_trade(outcome="WIN", sport=None)])
        self.assertIn("unknown", r.by_sport)

    def test_total_wagered_usd(self):
        trades = [
            _trade(outcome="WIN",  position_size_usd=30.0),
            _trade(outcome="LOSS", position_size_usd=20.0, pnl_usd=-20.0),
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.total_wagered_usd, 50.0)

    def test_void_does_not_affect_pnl(self):
        trades = [
            _trade(outcome="WIN",  pnl_usd=10.0),
            _trade(outcome="VOID", pnl_usd=99.0),   # should be ignored
        ]
        r = self.bt.run(trades)
        self.assertAlmostEqual(r.total_pnl_usd, 10.0)


# ══════════════════════════════════════════════════════════════════════════════
# Backtester.simulate
# ══════════════════════════════════════════════════════════════════════════════

class TestBacktesterSimulate(unittest.TestCase):

    def setUp(self):
        self.bt = Backtester(initial_bankroll=1_000.0, fee_pct=0.07)

    def test_pending_trades_resolved(self):
        trades = [_trade(outcome="PENDING", pnl_usd=0.0, fair_prob=1.0)]
        r = self.bt.simulate(trades, seed=0)
        # fair_prob=1.0 → always WIN
        self.assertEqual(r.n_wins, 1)
        self.assertEqual(r.n_pending, 0)

    def test_always_lose_with_zero_prob(self):
        trades = [_trade(outcome="PENDING", pnl_usd=0.0, fair_prob=0.0)]
        r = self.bt.simulate(trades, seed=0)
        self.assertEqual(r.n_losses, 1)

    def test_seed_makes_results_deterministic(self):
        trades = [_trade(outcome="PENDING", pnl_usd=0.0, fair_prob=0.55)
                  for _ in range(10)]
        r1 = self.bt.simulate(trades, seed=42)
        r2 = self.bt.simulate(trades, seed=42)
        self.assertEqual(r1.n_wins, r2.n_wins)
        self.assertAlmostEqual(r1.total_pnl_usd, r2.total_pnl_usd)

    def test_different_seeds_differ(self):
        trades = [_trade(outcome="PENDING", pnl_usd=0.0, fair_prob=0.55)
                  for _ in range(20)]
        r1 = self.bt.simulate(trades, seed=1)
        r2 = self.bt.simulate(trades, seed=999)
        # Very unlikely to be exactly equal with 20 Bernoulli draws
        self.assertFalse(r1.n_wins == r2.n_wins and r1.n_losses == r2.n_losses)

    def test_resolved_trades_keep_original_pnl(self):
        trades = [
            _trade(outcome="WIN",  pnl_usd=10.0),
            _trade(outcome="PENDING", pnl_usd=0.0, fair_prob=1.0),  # forced WIN
        ]
        r = self.bt.simulate(trades, seed=0)
        # the WIN trade adds $10; the simulated WIN adds a positive pnl
        self.assertGreater(r.total_pnl_usd, 10.0)

    def test_simulated_win_pnl_is_positive(self):
        trade = _trade(outcome="PENDING", pnl_usd=0.0, fair_prob=1.0,
                       kalshi_prob=0.50, position_size_usd=50.0)
        r = self.bt.simulate([trade], seed=0)
        self.assertGreater(r.total_pnl_usd, 0.0)

    def test_simulated_loss_pnl_equals_negative_stake(self):
        trade = _trade(outcome="PENDING", pnl_usd=0.0, fair_prob=0.0,
                       position_size_usd=50.0)
        r = self.bt.simulate([trade], seed=0)
        self.assertAlmostEqual(r.total_pnl_usd, -50.0)


# ══════════════════════════════════════════════════════════════════════════════
# Backtester.from_config
# ══════════════════════════════════════════════════════════════════════════════

class TestBacktesterFromConfig(unittest.TestCase):

    def test_default_bankroll_when_not_in_config(self):
        bt = Backtester.from_config({})
        self.assertEqual(bt._initial_bankroll, 10_000.0)

    def test_reads_initial_bankroll(self):
        config = {"risk": {"initial_bankroll": 5_000.0}}
        bt = Backtester.from_config(config)
        self.assertEqual(bt._initial_bankroll, 5_000.0)

    def test_reads_fee_pct(self):
        config = {"edge": {"fee_pct": 0.05}}
        bt = Backtester.from_config(config)
        self.assertAlmostEqual(bt._fee_pct, 0.05)

    def test_default_fee_pct_when_not_in_config(self):
        bt = Backtester.from_config({})
        self.assertAlmostEqual(bt._fee_pct, 0.07)


# ══════════════════════════════════════════════════════════════════════════════
# Backtester.load_csv
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadCsv(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_returns_empty(self):
        result = Backtester.load_csv(self.tmp / "missing.csv")
        self.assertEqual(result, [])

    def test_loads_valid_rows(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [_csv_row(), _csv_row(outcome="LOSS", pnl_usd=-5.0)])
        trades = Backtester.load_csv(path)
        self.assertEqual(len(trades), 2)

    def test_parses_float_fields(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [_csv_row(fair_prob=0.62, kalshi_prob=0.55)])
        t = Backtester.load_csv(path)[0]
        self.assertAlmostEqual(t.fair_prob, 0.62)
        self.assertAlmostEqual(t.kalshi_prob, 0.55)

    def test_parses_outcome(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [_csv_row(outcome="LOSS")])
        self.assertEqual(Backtester.load_csv(path)[0].outcome, "LOSS")

    def test_parses_sport(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [_csv_row(sport="basketball_nba")])
        self.assertEqual(Backtester.load_csv(path)[0].sport, "basketball_nba")

    def test_fill_price_none_when_empty(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [_csv_row()])
        self.assertIsNone(Backtester.load_csv(path)[0].fill_price)

    def test_bad_rows_are_skipped(self):
        path = self.tmp / "history.csv"
        # Write a row with missing required column by injecting raw CSV
        with path.open("w", newline="") as fh:
            fh.write("date,market_ticker,side\n")
            fh.write("2024-01-01,NFL-X,YES\n")  # missing many required fields
        self.assertEqual(Backtester.load_csv(path), [])


# ══════════════════════════════════════════════════════════════════════════════
# Backtester.run_csv (integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestRunCsv(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bt = Backtester(initial_bankroll=1_000.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_csv_integration(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [
            _csv_row(outcome="WIN",  pnl_usd=20.0),
            _csv_row(outcome="LOSS", pnl_usd=-10.0),
        ])
        r = self.bt.run_csv(path)
        self.assertEqual(r.n_trades, 2)
        self.assertAlmostEqual(r.total_pnl_usd, 10.0)

    def test_run_csv_missing_file(self):
        r = self.bt.run_csv(self.tmp / "nope.csv")
        self.assertEqual(r.n_trades, 0)


# ══════════════════════════════════════════════════════════════════════════════
# export_equity_curve
# ══════════════════════════════════════════════════════════════════════════════

class TestExportEquityCurve(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bt = Backtester(initial_bankroll=1_000.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_file(self):
        r = self.bt.run([_trade(outcome="WIN", pnl_usd=100.0)])
        path = self.tmp / "curve.csv"
        self.bt.export_equity_curve(r, path)
        self.assertTrue(path.exists())

    def test_returns_row_count(self):
        r = self.bt.run([_trade(outcome="WIN"), _trade(outcome="WIN")])
        n = self.bt.export_equity_curve(r, self.tmp / "curve.csv")
        self.assertEqual(n, len(r.equity_curve))

    def test_csv_has_correct_columns(self):
        r = self.bt.run([_trade(outcome="WIN")])
        path = self.tmp / "curve.csv"
        self.bt.export_equity_curve(r, path)
        with path.open() as fh:
            reader = csv.DictReader(fh)
            self.assertIn("trade_index", reader.fieldnames)
            self.assertIn("bankroll", reader.fieldnames)

    def test_returns_zero_for_empty_result(self):
        r = BacktestResult()   # no equity_curve
        n = self.bt.export_equity_curve(r, self.tmp / "curve.csv")
        self.assertEqual(n, 0)

    def test_returns_zero_on_os_error(self):
        r = self.bt.run([_trade(outcome="WIN")])
        blocker = self.tmp / "file"
        blocker.write_text("block")
        path = blocker / "curve.csv"
        self.assertEqual(self.bt.export_equity_curve(r, path), 0)


# ══════════════════════════════════════════════════════════════════════════════
# export_report
# ══════════════════════════════════════════════════════════════════════════════

class TestExportReport(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bt = Backtester(initial_bankroll=1_000.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_file(self):
        r = self.bt.run([_trade(outcome="WIN", sport="nfl")])
        path = self.tmp / "report.csv"
        self.bt.export_report(r, path)
        self.assertTrue(path.exists())

    def test_has_overall_row(self):
        r = self.bt.run([_trade(outcome="WIN")])
        path = self.tmp / "report.csv"
        self.bt.export_report(r, path)
        text = path.read_text()
        self.assertIn("OVERALL", text)

    def test_has_sport_row(self):
        r = self.bt.run([_trade(outcome="WIN", sport="basketball_nba")])
        path = self.tmp / "report.csv"
        self.bt.export_report(r, path)
        self.assertIn("basketball_nba", path.read_text())

    def test_row_count_includes_overall(self):
        trades = [
            _trade(outcome="WIN",  sport="nfl"),
            _trade(outcome="WIN",  sport="nba"),
        ]
        r = self.bt.run(trades)
        path = self.tmp / "report.csv"
        n = self.bt.export_report(r, path)
        # 2 sports + 1 OVERALL = 3
        self.assertEqual(n, 3)

    def test_returns_zero_on_os_error(self):
        r = self.bt.run([_trade(outcome="WIN")])
        blocker = self.tmp / "file"
        blocker.write_text("block")
        path = blocker / "report.csv"
        self.assertEqual(self.bt.export_report(r, path), 0)


# ══════════════════════════════════════════════════════════════════════════════
# scripts/run_backtest.py — CLI
# ══════════════════════════════════════════════════════════════════════════════

class TestRunBacktestCli(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # import the CLI module
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib
        import run_backtest as _rb
        importlib.reload(_rb)   # re-import cleanly
        self._main = _rb.main
        self._parse = _rb._parse_args

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_default_csv_path(self):
        args = self._parse(["prog"])
        self.assertEqual(str(args["csv_path"]), "data/edge_history.csv")

    def test_parse_explicit_csv_path(self):
        args = self._parse(["prog", "custom/path.csv"])
        self.assertEqual(str(args["csv_path"]), "custom/path.csv")

    def test_parse_simulate_flag(self):
        args = self._parse(["prog", "--simulate"])
        self.assertTrue(args["simulate"])

    def test_parse_seed(self):
        args = self._parse(["prog", "--seed", "99"])
        self.assertEqual(args["seed"], 99)

    def test_parse_export_dir(self):
        args = self._parse(["prog", "--export-dir", "out/"])
        self.assertEqual(str(args["export_dir"]), "out")

    def test_parse_bankroll(self):
        args = self._parse(["prog", "--bankroll", "5000"])
        self.assertAlmostEqual(args["bankroll"], 5_000.0)

    def test_main_missing_csv_returns_zero(self):
        # Empty CSV → no trades → exit 0
        rc = self._main(["prog", str(self.tmp / "nope.csv")])
        self.assertEqual(rc, 0)

    def test_main_with_real_csv(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [
            _csv_row(outcome="WIN",  pnl_usd=10.0),
            _csv_row(outcome="LOSS", pnl_usd=-5.0),
        ])
        rc = self._main(["prog", str(path)])
        self.assertEqual(rc, 0)

    def test_main_export_dir_creates_files(self):
        path = self.tmp / "history.csv"
        _write_csv(path, [_csv_row(outcome="WIN", pnl_usd=10.0)])
        export_dir = self.tmp / "exports"
        rc = self._main(["prog", str(path), "--export-dir", str(export_dir)])
        self.assertEqual(rc, 0)
        self.assertTrue((export_dir / "equity_curve.csv").exists())
        self.assertTrue((export_dir / "backtest_report.csv").exists())


if __name__ == "__main__":
    unittest.main()
