"""Backtesting: replay historical edge data to evaluate strategy performance."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field

import numpy as np

from rich.console import Console
from rich.table import Table

EDGE_HISTORY_PATH = os.path.join("data", "edge_history.csv")

console = Console()


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    min_edge: float
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0
    pnl_series: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0

    @property
    def roi(self) -> float:
        total_wagered = self.total_trades * 100  # Assume $100 per trade
        return (self.total_pnl / total_wagered * 100) if total_wagered > 0 else 0

    @property
    def sharpe_ratio(self) -> float:
        if len(self.pnl_series) < 2:
            return 0.0
        returns = np.array(self.pnl_series)
        if returns.std() == 0:
            return 0.0
        return float(returns.mean() / returns.std() * np.sqrt(252))


def load_edge_history(path: str) -> list[dict]:
    """Load edge history CSV."""
    if not os.path.exists(path):
        console.print(f"[red]Edge history file not found: {path}[/]")
        console.print("Run the agent first to generate edge history data.")
        sys.exit(1)

    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["edge_pct"] = float(row.get("edge_pct", 0))
                row["kalshi_yes_price"] = float(row.get("kalshi_yes_price", 0.5))
                row["sharp_prob"] = float(row.get("sharp_prob", 0.5))
                row["kalshi_volume"] = int(row.get("kalshi_volume", 0))
                row["num_books"] = int(row.get("num_books", 0))
                rows.append(row)
            except (ValueError, TypeError):
                continue

    return rows


def simulate_trade(
    edge_pct: float,
    kalshi_price: float,
    sharp_prob: float,
    side: str,
    position_size: float = 100.0,
    fee_rate: float = 0.07,
) -> tuple[str, float]:
    """Simulate a trade outcome based on sharp probability.

    Uses the sharp probability as the 'true' probability to determine
    win/loss via a Monte Carlo draw.

    Returns:
        (outcome, pnl) tuple.
    """
    # Determine win probability
    if side == "yes":
        win_prob = sharp_prob
        cost = kalshi_price
    else:
        win_prob = 1.0 - sharp_prob
        cost = 1.0 - kalshi_price

    # Monte Carlo: did we win?
    won = np.random.random() < win_prob

    if won:
        gross_profit = position_size * (1.0 - cost) / cost
        fee = gross_profit * fee_rate
        pnl = gross_profit - fee
        return "win", pnl
    else:
        return "loss", -position_size


def run_backtest(
    data: list[dict],
    min_edge: float = 3.0,
    max_edge: float = 25.0,
    min_books: int = 3,
    position_size: float = 100.0,
    max_daily_loss: float = 500.0,
    seed: int = 42,
) -> BacktestResult:
    """Run a backtest with given parameters.

    Args:
        data: Edge history data.
        min_edge: Minimum edge threshold.
        max_edge: Maximum edge threshold.
        min_books: Minimum sportsbook consensus.
        position_size: USD per trade.
        max_daily_loss: Daily stop loss.
        seed: Random seed for reproducibility.
    """
    np.random.seed(seed)
    result = BacktestResult(min_edge=min_edge)
    equity = 0.0
    daily_pnl = 0.0
    current_date = ""

    for row in data:
        edge = row.get("edge_pct", 0)
        books = row.get("num_books", 0)
        timestamp = row.get("timestamp", "")
        date = timestamp[:10] if timestamp else ""

        # Daily reset
        if date != current_date:
            current_date = date
            daily_pnl = 0.0

        # Filters
        if edge < min_edge or edge > max_edge:
            continue
        if books < min_books:
            continue
        if daily_pnl <= -max_daily_loss:
            continue

        side = row.get("side", "yes")
        kalshi_price = row.get("kalshi_yes_price", 0.5)
        sharp_prob = row.get("sharp_prob", 0.5)

        outcome, pnl = simulate_trade(
            edge, kalshi_price, sharp_prob, side, position_size
        )

        result.total_trades += 1
        if outcome == "win":
            result.wins += 1
        else:
            result.losses += 1

        result.total_pnl += pnl
        result.pnl_series.append(pnl)
        equity += pnl
        daily_pnl += pnl

        if equity > result.peak_equity:
            result.peak_equity = equity
        drawdown = result.peak_equity - equity
        if drawdown > result.max_drawdown:
            result.max_drawdown = drawdown

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the Kalshi arbitrage strategy")
    parser.add_argument(
        "--data", default=EDGE_HISTORY_PATH, help="Path to edge_history.csv"
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        nargs="+",
        default=[2.0, 3.0, 4.0, 5.0, 6.0, 8.0],
        help="Min edge thresholds to test",
    )
    parser.add_argument("--position-size", type=float, default=100.0)
    parser.add_argument("--min-books", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data = load_edge_history(args.data)

    if not data:
        console.print("[yellow]No edge history data to backtest.[/]")
        console.print(
            "Run the agent to accumulate data in data/edge_history.csv first."
        )
        return

    console.print(f"\n[bold]Backtesting on {len(data)} historical edge records[/]\n")

    # Results table
    table = Table(title="Backtest Results by Min Edge Threshold")
    table.add_column("Min Edge %", justify="center", style="cyan")
    table.add_column("Trades", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("Total P&L", justify="right")
    table.add_column("ROI %", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Sharpe", justify="right")

    for me in args.min_edge:
        result = run_backtest(
            data,
            min_edge=me,
            position_size=args.position_size,
            min_books=args.min_books,
            seed=args.seed,
        )

        pnl_style = "green" if result.total_pnl >= 0 else "red"
        roi_style = "green" if result.roi >= 0 else "red"

        table.add_row(
            f"{me:.1f}%",
            str(result.total_trades),
            f"{result.win_rate:.1f}%",
            f"[{pnl_style}]${result.total_pnl:+,.2f}[/]",
            f"[{roi_style}]{result.roi:+.2f}%[/]",
            f"${result.max_drawdown:,.2f}",
            f"{result.sharpe_ratio:.2f}",
        )

    console.print(table)
    console.print(
        f"\n[dim]Position size: ${args.position_size:.0f} | "
        f"Min books: {args.min_books} | Seed: {args.seed}[/]"
    )


if __name__ == "__main__":
    main()
