"""Export P&L and trade log to CSV/Excel."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

TRADE_LOG_PATH = os.path.join("data", "trade_log.json")


def load_trades(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"Trade log not found: {path}")
        sys.exit(1)
    with open(path, "r") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def export_csv(trades: list[dict], output_path: str) -> None:
    """Export trades to CSV."""
    if not trades:
        print("No trades to export.")
        return

    fieldnames = [
        "trade_id", "timestamp", "ticker", "sport", "market_type", "teams",
        "event_time", "side", "kalshi_price_at_entry", "sharp_prob_at_entry",
        "edge_at_entry", "position_size_usd", "kalshi_volume", "num_books",
        "time_to_event_hours", "line_movement", "order_type", "mode",
        "outcome", "pnl", "order_id", "hour_of_day",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade)

    print(f"Exported {len(trades)} trades to {output_path}")


def export_excel(trades: list[dict], output_path: str) -> None:
    """Export trades to Excel with formatting."""
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required for Excel export. Install with: pip install pandas openpyxl")
        return

    if not trades:
        print("No trades to export.")
        return

    df = pd.DataFrame(trades)

    # Calculate summary stats
    settled = df[df["outcome"].notna()].copy()

    summary = {
        "Total Trades": len(df),
        "Settled Trades": len(settled),
        "Open Trades": len(df) - len(settled),
        "Wins": len(settled[settled["outcome"] == "win"]) if len(settled) > 0 else 0,
        "Losses": len(settled[settled["outcome"] == "loss"]) if len(settled) > 0 else 0,
        "Total P&L": settled["pnl"].sum() if "pnl" in settled.columns and len(settled) > 0 else 0,
        "Avg Edge at Entry": df["edge_at_entry"].mean() if "edge_at_entry" in df.columns else 0,
    }

    if len(settled) > 0 and (summary["Wins"] + summary["Losses"]) > 0:
        summary["Win Rate"] = summary["Wins"] / (summary["Wins"] + summary["Losses"])
    else:
        summary["Win Rate"] = 0

    # Write to Excel with multiple sheets
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="All Trades", index=False)

        if len(settled) > 0:
            settled.to_excel(writer, sheet_name="Settled Trades", index=False)

        summary_df = pd.DataFrame([summary])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

        # P&L by sport
        if len(settled) > 0 and "sport" in settled.columns:
            sport_pnl = settled.groupby("sport").agg(
                trades=("pnl", "count"),
                total_pnl=("pnl", "sum"),
                avg_pnl=("pnl", "mean"),
                avg_edge=("edge_at_entry", "mean"),
            ).reset_index()
            sport_pnl.to_excel(writer, sheet_name="P&L by Sport", index=False)

    print(f"Exported {len(trades)} trades to {output_path}")
    print(f"Summary: {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export trade results")
    parser.add_argument(
        "--input", default=TRADE_LOG_PATH, help="Path to trade_log.json"
    )
    parser.add_argument(
        "--output", default=None, help="Output file path (auto-detect format from extension)"
    )
    parser.add_argument(
        "--format",
        choices=["csv", "excel"],
        default="csv",
        help="Output format (default: csv)",
    )
    args = parser.parse_args()

    trades = load_trades(args.input)

    if args.output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ext = "xlsx" if args.format == "excel" else "csv"
        args.output = f"trade_export_{timestamp}.{ext}"

    if args.format == "excel" or args.output.endswith(".xlsx"):
        export_excel(trades, args.output)
    else:
        export_csv(trades, args.output)


if __name__ == "__main__":
    main()
