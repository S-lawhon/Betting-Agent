#!/usr/bin/env python3
"""
scripts/run_dashboard.py
────────────────────────
CLI: print a snapshot of the betting agent dashboard to the terminal.

Reads the current config.yaml, instantiates a read-only Ledger (from the
trade log if present), and renders the ANSI dashboard once — or in a live
refresh loop.

Usage
─────
    python scripts/run_dashboard.py              # one-shot snapshot
    python scripts/run_dashboard.py --watch      # refresh every 5 seconds
    python scripts/run_dashboard.py --watch --interval 10
    python scripts/run_dashboard.py --no-color   # plain text (pipe-friendly)
    python scripts/run_dashboard.py --export     # also export CSV files

Options
───────
  --watch           Keep running, refreshing on the given interval.
  --interval N      Seconds between refreshes in watch mode (default: 5).
  --no-color        Disable ANSI colour codes (useful when piping output).
  --export          Export trade_log.csv and unmatched_markets.csv to
                    data/exports/ after rendering.
  --bankroll N      Override initial bankroll (default: from config.yaml,
                    or 10000 if not set).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.dashboard import Dashboard, export_trade_log_csv, export_unmatched_csv  # noqa: E402
from src.risk_manager import Ledger                                               # noqa: E402


def _parse_args(argv: list) -> dict:
    args = {
        "watch":     False,
        "interval":  5,
        "color":     True,
        "export":    False,
        "bankroll":  None,   # None = read from config
    }
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--watch":
            args["watch"] = True
        elif tok == "--no-color":
            args["color"] = False
        elif tok == "--export":
            args["export"] = True
        elif tok == "--interval" and i + 1 < len(argv):
            i += 1
            args["interval"] = int(argv[i])
        elif tok == "--bankroll" and i + 1 < len(argv):
            i += 1
            args["bankroll"] = float(argv[i])
        i += 1
    return args


def _load_config() -> dict:
    try:
        import yaml  # type: ignore[import]
        cfg_path = ROOT / "config.yaml"
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[WARN] Could not load config.yaml: {exc}", file=sys.stderr)
        return {}


def _rebuild_ledger_from_log(config: dict, initial_bankroll: float) -> Ledger:
    """
    Reconstruct a Ledger snapshot by replaying trade_log.jsonl.

    This gives approximate account state without a running agent — P&L is
    accumulated from resolved WIN/LOSS records.  PENDING trades are counted
    as open positions.
    """
    import json
    from datetime import datetime, timezone

    ledger = Ledger(initial_bankroll=initial_bankroll)
    log_path = Path(config.get("paths", {}).get(
        "trade_log", "data/trade_logs/trade_log.jsonl"
    ))
    if not log_path.exists():
        return ledger

    for raw in log_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue

        action  = (rec.get("action") or "").upper()
        outcome = (rec.get("outcome") or "").upper()
        ticker  = rec.get("market_ticker", "UNKNOWN")
        side    = rec.get("side", "YES")
        size    = float(rec.get("position_size_usd") or 0)
        pnl     = float(rec.get("pnl_usd") or 0)
        ts_str  = rec.get("timestamp") or rec.get("timestamp_utc") or ""

        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(tz=timezone.utc)

        if action in ("PLACED", "EXECUTED") and size > 0:
            # Open position — add it (may already be closed below if same log)
            try:
                ledger.open_position(
                    ticker=ticker,
                    side=side,
                    size_usd=size,
                    fill_price=float(rec.get("fill_price") or 0.5),
                    now=ts,
                )
            except Exception:
                pass

        if outcome in ("WIN", "LOSS", "VOID"):
            # Close the position with recorded P&L
            try:
                ledger.close_position(ticker=ticker, pnl_usd=pnl, now=ts)
            except Exception:
                pass

    return ledger


def main(argv: list | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv)

    config        = _load_config()
    risk_cfg      = config.get("risk", {})
    initial_bankroll = (
        args["bankroll"]
        if args["bankroll"] is not None
        else float(risk_cfg.get("initial_bankroll", 10_000.0))
    )

    paths = config.get("paths", {})
    trade_log_path   = Path(paths.get("trade_log",     "data/trade_logs/trade_log.jsonl"))
    unmatched_path   = Path(paths.get("unmatched_log", "data/trade_logs/unmatched_markets.jsonl"))
    exports_dir      = ROOT / "data" / "exports"

    def _render_once() -> None:
        ledger    = _rebuild_ledger_from_log(config, initial_bankroll)
        dashboard = Dashboard.from_config(
            config,
            ledger,
            color=args["color"],
        )
        dashboard.tick()
        if args["color"]:
            dashboard.print_dashboard()   # clears screen first
        else:
            print(dashboard.render())

        if args["export"]:
            exports_dir.mkdir(parents=True, exist_ok=True)
            n_tl = export_trade_log_csv(trade_log_path, exports_dir / "trade_log.csv")
            n_um = export_unmatched_csv(unmatched_path, exports_dir / "unmatched_markets.csv")
            print(f"\n[export] trade_log.csv: {n_tl} row(s)")
            print(f"[export] unmatched_markets.csv: {n_um} row(s)")

    if not args["watch"]:
        _render_once()
        return 0

    # ── Watch / refresh loop ───────────────────────────────────────────────────
    try:
        while True:
            _render_once()
            time.sleep(args["interval"])
    except KeyboardInterrupt:
        print("\n[dashboard] Stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
