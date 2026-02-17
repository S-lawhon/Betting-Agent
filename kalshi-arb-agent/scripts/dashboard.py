"""Terminal dashboard: live view of positions, P&L, opportunities, and system stats."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Paths relative to project root
TRADE_LOG_PATH = os.path.join("data", "trade_log.json")
MODEL_STATE_PATH = os.path.join("data", "model_state.json")
EDGE_HISTORY_PATH = os.path.join("data", "edge_history.csv")

console = Console()


def load_json(path: str) -> list | dict:
    if not os.path.exists(path):
        return [] if path.endswith("trade_log.json") else {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return [] if path.endswith("trade_log.json") else {}


def build_positions_table(trades: list[dict]) -> Table:
    """Build a table of open positions."""
    table = Table(title="Open Positions", expand=True)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Side", style="bold")
    table.add_column("Entry $", justify="right")
    table.add_column("Size $", justify="right")
    table.add_column("Edge %", justify="right")
    table.add_column("Sport")
    table.add_column("Mode", style="dim")

    open_trades = [t for t in trades if t.get("outcome") is None]
    for t in open_trades[-15:]:  # Show last 15
        side_style = "green" if t.get("side") == "yes" else "red"
        table.add_row(
            t.get("ticker", "?"),
            Text(t.get("side", "?").upper(), style=side_style),
            f"{t.get('kalshi_price_at_entry', 0):.2f}",
            f"${t.get('position_size_usd', 0):.2f}",
            f"{t.get('edge_at_entry', 0):.1f}%",
            t.get("sport", "?").split("_")[-1].upper(),
            t.get("mode", "paper"),
        )

    if not open_trades:
        table.add_row("—", "—", "—", "—", "—", "—", "—")

    return table


def build_pnl_panel(trades: list[dict]) -> Panel:
    """Build P&L summary panel."""
    settled = [t for t in trades if t.get("outcome") is not None]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total_pnl = sum(t.get("pnl", 0) or 0 for t in settled)
    today_trades = [t for t in settled if t.get("timestamp", "").startswith(today)]
    today_pnl = sum(t.get("pnl", 0) or 0 for t in today_trades)

    wins = sum(1 for t in settled if t.get("outcome") == "win")
    losses = sum(1 for t in settled if t.get("outcome") == "loss")
    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0

    open_count = sum(1 for t in trades if t.get("outcome") is None)
    total_exposure = sum(
        t.get("position_size_usd", 0) for t in trades if t.get("outcome") is None
    )

    pnl_color = "green" if total_pnl >= 0 else "red"
    today_color = "green" if today_pnl >= 0 else "red"

    text = Text()
    text.append(f"All-time P&L:  ", style="bold")
    text.append(f"${total_pnl:+.2f}\n", style=pnl_color)
    text.append(f"Today's P&L:   ", style="bold")
    text.append(f"${today_pnl:+.2f}\n", style=today_color)
    text.append(f"Win Rate:      {win_rate:.1f}% ({wins}W / {losses}L)\n")
    text.append(f"Total Trades:  {total}\n")
    text.append(f"Open:          {open_count} (${total_exposure:.2f})\n")
    text.append(f"Today:         {len(today_trades)} trades")

    return Panel(text, title="P&L Summary", border_style="green" if total_pnl >= 0 else "red")


def build_recent_trades_table(trades: list[dict]) -> Table:
    """Build table of recent trade outcomes."""
    table = Table(title="Recent Trades", expand=True)
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Ticker", style="cyan")
    table.add_column("Side")
    table.add_column("Edge %", justify="right")
    table.add_column("Result")
    table.add_column("P&L", justify="right")

    settled = [t for t in trades if t.get("outcome") is not None]
    for t in settled[-10:]:  # Last 10
        ts = t.get("timestamp", "")
        if len(ts) > 16:
            ts = ts[11:16]  # HH:MM

        outcome = t.get("outcome", "?")
        outcome_style = "green bold" if outcome == "win" else "red bold"

        pnl = t.get("pnl", 0) or 0
        pnl_style = "green" if pnl >= 0 else "red"

        table.add_row(
            ts,
            t.get("ticker", "?")[:20],
            t.get("side", "?").upper(),
            f"{t.get('edge_at_entry', 0):.1f}%",
            Text(outcome.upper(), style=outcome_style),
            Text(f"${pnl:+.2f}", style=pnl_style),
        )

    if not settled:
        table.add_row("—", "—", "—", "—", "—", "—")

    return table


def build_learner_panel(model_state: dict) -> Panel:
    """Build learner statistics panel."""
    text = Text()

    total = model_state.get("total_trades", 0)
    method = model_state.get("edge_adjustment_method", "bayesian")
    last_recal = model_state.get("last_recalibration", "never")
    ml_model = model_state.get("ml_model_path") is not None

    text.append(f"Method:        {method}\n")
    text.append(f"Total Trades:  {total}\n")
    text.append(f"Last Recal:    {last_recal or 'never'}\n")
    text.append(f"ML Model:      {'Active' if ml_model else 'Not trained'}\n\n")

    # Category stats from bayesian priors
    priors = model_state.get("bayesian_priors", {})
    if priors:
        text.append("Category Win Rates:\n", style="bold")
        for cat, prior in priors.items():
            alpha = prior.get("alpha", 1)
            beta_val = prior.get("beta", 1)
            total_cat = prior.get("total_trades", 0)
            wr = alpha / (alpha + beta_val) if (alpha + beta_val) > 0 else 0.5
            wr_style = "green" if wr > 0.52 else ("red" if wr < 0.48 else "yellow")
            text.append(f"  {cat}: ", style="dim")
            text.append(f"{wr:.1%}", style=wr_style)
            text.append(f" ({total_cat} trades)\n")

    return Panel(text, title="Learner Stats", border_style="blue")


def build_risk_panel(trades: list[dict]) -> Panel:
    """Build risk utilization panel."""
    open_trades = [t for t in trades if t.get("outcome") is None]
    exposure = sum(t.get("position_size_usd", 0) for t in open_trades)
    max_exposure = 5000  # From config default

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_settled = [
        t for t in trades
        if t.get("outcome") is not None and t.get("timestamp", "").startswith(today)
    ]
    daily_pnl = sum(t.get("pnl", 0) or 0 for t in today_settled)
    max_daily_loss = 500

    text = Text()
    text.append("Exposure:\n", style="bold")

    exp_pct = (exposure / max_exposure * 100) if max_exposure > 0 else 0
    exp_style = "green" if exp_pct < 60 else ("yellow" if exp_pct < 80 else "red")
    text.append(f"  ${exposure:.0f} / ${max_exposure:.0f} ", style=exp_style)
    text.append(f"({exp_pct:.0f}%)\n", style=exp_style)

    bar_len = 30
    filled = int(bar_len * min(exp_pct / 100, 1.0))
    text.append("  [")
    text.append("█" * filled, style=exp_style)
    text.append("░" * (bar_len - filled), style="dim")
    text.append("]\n\n")

    text.append("Daily Loss:\n", style="bold")
    if daily_pnl < 0:
        loss_pct = abs(daily_pnl) / max_daily_loss * 100
        loss_style = "green" if loss_pct < 50 else ("yellow" if loss_pct < 80 else "red")
        text.append(f"  ${daily_pnl:+.0f} / -${max_daily_loss:.0f} ", style=loss_style)
        text.append(f"({loss_pct:.0f}% used)\n", style=loss_style)
    else:
        text.append(f"  ${daily_pnl:+.0f} (no losses today)\n", style="green")

    text.append(f"\nOpen Positions: {len(open_trades)} / 20\n")

    return Panel(text, title="Risk Status", border_style="yellow")


def build_dashboard() -> Layout:
    """Build the full dashboard layout."""
    trades = load_json(TRADE_LOG_PATH)
    model_state = load_json(MODEL_STATE_PATH)

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )

    # Header
    header_text = Text()
    header_text.append("  KALSHI ARBITRAGE AGENT  ", style="bold white on blue")
    header_text.append(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    layout["header"].update(Panel(header_text))

    # Body: split into columns
    layout["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=2),
    )

    # Left column: positions + recent trades
    layout["left"].split_column(
        Layout(build_positions_table(trades), name="positions"),
        Layout(build_recent_trades_table(trades), name="recent"),
    )

    # Right column: P&L + risk + learner
    layout["right"].split_column(
        Layout(build_pnl_panel(trades), name="pnl", size=10),
        Layout(build_risk_panel(trades), name="risk", size=14),
        Layout(build_learner_panel(model_state), name="learner"),
    )

    # Footer
    footer_text = Text("Press Ctrl+C to exit | Data refreshes every 5s", style="dim")
    layout["footer"].update(Panel(footer_text))

    return layout


def main() -> None:
    """Run the live dashboard."""
    console.clear()
    console.print("[bold blue]Starting Kalshi Agent Dashboard...[/]\n")

    try:
        with Live(build_dashboard(), console=console, refresh_per_second=0.2) as live:
            while True:
                time.sleep(5)
                live.update(build_dashboard())
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/]")


if __name__ == "__main__":
    main()
