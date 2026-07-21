#!/usr/bin/env python3
"""
scripts/run_backtest.py
───────────────────────
CLI: replay edge_history.csv and print a performance summary.

Usage
─────
    python scripts/run_backtest.py                        # default path
    python scripts/run_backtest.py data/edge_history.csv  # explicit path
    python scripts/run_backtest.py data/edge_history.csv --simulate  # resolve PENDING
    python scripts/run_backtest.py data/edge_history.csv --export-dir data/exports/

Options
───────
  --simulate        Resolve PENDING rows using fair_prob Bernoulli simulation.
  --seed N          RNG seed for simulate (default: 42).
  --export-dir DIR  Write equity_curve.csv and report.csv to DIR.
  --bankroll N      Starting bankroll for equity curve (default: 10000).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.backtester import Backtester  # noqa: E402


def _parse_args(argv: list) -> dict:
    args = {
        "csv_path": Path("data/edge_history.csv"),
        "simulate": False,
        "seed": 42,
        "export_dir": None,
        "bankroll": 10_000.0,
    }
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            if tok == "--simulate":
                args["simulate"] = True
            elif tok == "--seed" and i + 1 < len(argv):
                i += 1
                args["seed"] = int(argv[i])
            elif tok == "--export-dir" and i + 1 < len(argv):
                i += 1
                args["export_dir"] = Path(argv[i])
            elif tok == "--bankroll" and i + 1 < len(argv):
                i += 1
                args["bankroll"] = float(argv[i])
        else:
            args["csv_path"] = Path(tok)
        i += 1
    return args


def main(argv: list | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv)
    bt = Backtester(initial_bankroll=args["bankroll"])

    trades = Backtester.load_csv(args["csv_path"])
    if not trades:
        print(f"[WARN] No trades loaded from {args['csv_path']}", file=sys.stderr)

    if args["simulate"]:
        result = bt.simulate(trades, seed=args["seed"])
        mode_label = f"SIMULATED (seed={args['seed']})"
    else:
        result = bt.run(trades)
        mode_label = "RECORDED"

    # ── Print summary ──────────────────────────────────────────────────────────
    w = 52
    sep = "─" * w
    print(f"\n{'BACKTEST RESULTS':^{w}}")
    print(f"{'Mode: ' + mode_label:^{w}}")
    print(sep)
    print(f"{'Trades':30s} {result.n_trades:>6d}"
          f"  ({result.n_wins}W / {result.n_losses}L"
          f" / {result.n_void}V / {result.n_pending}P)")
    print(f"{'Win rate':30s} {result.win_rate:>7.1%}")
    print(f"{'Total P&L':30s} {result.total_pnl_usd:>+10,.2f}")
    print(f"{'ROI':30s} {result.roi_pct:>+9.2f}%")
    print(f"{'Total wagered':30s} {result.total_wagered_usd:>10,.2f}")
    print(f"{'Avg edge':30s} {result.avg_edge_pct:>8.2%}")
    print(f"{'Avg EV':30s} {result.avg_ev:>8.4f}")
    print(f"{'Sharpe (per-trade)':30s} {result.sharpe:>8.3f}")
    print(f"{'Max drawdown':30s} "
          f"{result.max_drawdown_usd:>+10,.2f}"
          f"  ({result.max_drawdown_pct:.1%})")
    print(f"{'Final bankroll':30s} "
          f"{result.equity_curve[-1]:>10,.2f}"
          if result.equity_curve else "")

    if result.by_sport:
        print(f"\n{'By sport':}")
        print(f"  {'Sport':<30s} {'W':>4s} {'L':>4s} {'WR':>6s} {'P&L':>10s}")
        for sp, s in sorted(result.by_sport.items()):
            print(f"  {sp:<30s} {s['n_wins']:>4d} {s['n_losses']:>4d}"
                  f" {s['win_rate']:>6.1%} {s['total_pnl_usd']:>+10,.2f}")

    print()

    # ── Optional export ────────────────────────────────────────────────────────
    if args["export_dir"]:
        export_dir = Path(args["export_dir"])
        ec_path     = export_dir / "equity_curve.csv"
        report_path = export_dir / "backtest_report.csv"
        n_ec     = bt.export_equity_curve(result, ec_path)
        n_report = bt.export_report(result, report_path)
        print(f"[INFO] Equity curve written: {ec_path} ({n_ec} rows)")
        print(f"[INFO] Report written:        {report_path} ({n_report} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
