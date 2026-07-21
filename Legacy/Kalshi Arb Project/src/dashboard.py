"""
src/dashboard.py
────────────────
Terminal dashboard and CSV/JSONL export utilities.

No third-party dependencies — uses stdlib ANSI escape codes and
``shutil.get_terminal_size()`` so it works in any environment.

Architecture
────────────
  _Style           — thin ANSI wrapper; disabled cleanly when color=False.
  Dashboard        — renders a multi-section snapshot to a string or stdout.
  export_trade_log_csv()   — converts trade_log.jsonl → CSV.
  export_unmatched_csv()   — converts unmatched_markets.jsonl → CSV.

Dashboard sections
──────────────────
  Header     — timestamp, mode, scan count.
  Account    — bankroll, daily P&L, exposure, drawdown, cooldown.
  Positions  — open positions table (ticker / side / size / price / opened).
  Trades     — last N rows from trade_log.jsonl.

Colour coding
─────────────
  P&L / edge positive → green
  P&L / edge negative → red
  PLACED              → green
  SKIPPED_*           → yellow
  ERROR               → red
  Disabled by passing color=False (e.g. for file / CI output).
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from logger_setup import get_logger
from risk_manager import Ledger

log = get_logger(__name__)

# ── ANSI style helpers ────────────────────────────────────────────────────────

_R = "\033[0m"    # reset


class _Style:
    """Thin ANSI escape wrapper.  All methods return plain str when disabled."""

    def __init__(self, enabled: bool = True) -> None:
        self._on = enabled

    def _wrap(self, code: str, s: str) -> str:
        return f"{code}{s}{_R}" if self._on else s

    def bold(self, s: str) -> str:   return self._wrap("\033[1m",  s)
    def dim(self, s: str) -> str:    return self._wrap("\033[2m",  s)
    def green(self, s: str) -> str:  return self._wrap("\033[92m", s)
    def red(self, s: str) -> str:    return self._wrap("\033[91m", s)
    def yellow(self, s: str) -> str: return self._wrap("\033[93m", s)
    def cyan(self, s: str) -> str:   return self._wrap("\033[96m", s)

    def pnl(self, value: float, fmt: str = "+,.2f") -> str:
        """Format a P&L value, coloured green/red/plain by sign."""
        s = format(value, fmt)
        if value > 0:
            return self.green(s)
        if value < 0:
            return self.red(s)
        return s

    def action(self, a: str) -> str:
        """Colour an action code."""
        if a == "PLACED" or a == "EXECUTED":
            return self.green(a)
        if a.startswith("SKIPPED"):
            return self.yellow(a)
        if a == "ERROR":
            return self.red(a)
        return a


# ── Layout helpers ────────────────────────────────────────────────────────────

def _terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def _divider(title: str, width: int, st: _Style) -> str:
    """Return a section divider: '── TITLE ──────────────────'"""
    inner = f" {title} "
    dashes = width - len(inner) - 4   # 4 for leading '  ──'
    line = f"  {st.cyan('──')} {st.bold(title)} {st.cyan('─' * max(0, dashes))}"
    return line


def _col(value: str, width: int, align: str = "<") -> str:
    """Left/right-pad *plain* text to fixed width (no ANSI in value)."""
    return format(str(value)[:width], f"{align}{width}")


# ── Export functions ──────────────────────────────────────────────────────────

_TRADE_LOG_FIELDS = [
    "fingerprint", "timestamp_utc", "mode", "sport", "event",
    "market_ticker", "side", "fair_prob", "kalshi_prob", "edge_pct",
    "ev", "kelly_fraction", "position_size_usd", "action",
    "skip_reason", "order_id", "fill_price",
]

_UNMATCHED_FIELDS = [
    "timestamp_utc", "market_ticker", "market_title",
    "event_start_utc", "reason", "best_candidate", "best_score",
]


def export_trade_log_csv(
    jsonl_path: Path,
    csv_path: Path,
    fieldnames: Optional[List[str]] = None,
) -> int:
    """
    Export ``trade_log.jsonl`` to a CSV file.

    Parameters
    ----------
    jsonl_path : Path
        Source JSONL file.
    csv_path : Path
        Destination CSV file (created / overwritten).
    fieldnames : list, optional
        Column order; defaults to ``_TRADE_LOG_FIELDS``.

    Returns
    -------
    int
        Number of data rows written (0 if source missing).
    """
    fields = fieldnames or _TRADE_LOG_FIELDS
    rows = _read_jsonl(jsonl_path)
    return _write_csv(rows, csv_path, fields)


def export_unmatched_csv(
    jsonl_path: Path,
    csv_path: Path,
) -> int:
    """
    Export ``unmatched_markets.jsonl`` to a CSV file.

    Returns number of data rows written.
    """
    rows = _read_jsonl(jsonl_path)
    return _write_csv(rows, csv_path, _UNMATCHED_FIELDS)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read all valid JSON lines from a JSONL file.  Returns [] if missing."""
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return rows


def _write_csv(
    rows: List[Dict[str, Any]],
    csv_path: Path,
    fieldnames: List[str],
) -> int:
    """Write rows to csv_path.  Returns number of data rows written."""
    try:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({f: row.get(f, "") for f in fieldnames})
        return len(rows)
    except OSError as exc:
        log.error("csv_export_error", path=str(csv_path), error=str(exc))
        return 0


# ── Dashboard ─────────────────────────────────────────────────────────────────

class Dashboard:
    """
    Terminal dashboard for the betting agent.

    Parameters
    ----------
    ledger : Ledger
        Live ledger for bankroll / position data.
    trade_log_path : Path, optional
        Path to trade_log.jsonl for the recent-trades section.
    mode : str
        ``"paper"`` or ``"live"`` — shown in the header.
    color : bool
        Enable ANSI colour codes (default True).
    width : int, optional
        Override terminal width.
    max_recent_trades : int
        Number of trade-log rows shown in the trades section.
    _now_fn : callable, optional
        Injectable clock (for tests).
    """

    def __init__(
        self,
        ledger: Ledger,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        color: bool = True,
        width: Optional[int] = None,
        max_recent_trades: int = 10,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._ledger     = ledger
        self._trade_log  = trade_log_path or Path("data/trade_logs/trade_log.jsonl")
        self._mode       = mode
        self._st         = _Style(enabled=color)
        self._width      = width or _terminal_width()
        self._max_trades = max_recent_trades
        self._now        = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        self._scan_count = 0   # incremented by caller via tick()

    @classmethod
    def from_config(
        cls,
        config: dict,
        ledger: Ledger,
        **kwargs,
    ) -> "Dashboard":
        """Construct from a loaded config.yaml dict."""
        paths = config.get("paths", {})
        mode  = config.get("environment", "demo")
        mode  = "live" if mode == "live" else "paper"
        return cls(
            ledger=ledger,
            trade_log_path=Path(
                paths.get("trade_log", "data/trade_logs/trade_log.jsonl")
            ),
            mode=mode,
            **kwargs,
        )

    def tick(self) -> None:
        """Increment the internal scan counter."""
        self._scan_count += 1

    # ── Public rendering API ──────────────────────────────────────────────────

    def render(self) -> str:
        """Render the full dashboard as a string (no side-effects)."""
        parts = [
            self._header(),
            self._account_section(),
            self._positions_section(),
            self._trades_section(),
            "",
        ]
        return "\n".join(parts)

    def print_dashboard(self) -> None:
        """Clear the terminal and print the dashboard."""
        import os
        os.system("cls" if os.name == "nt" else "clear")
        print(self.render(), end="")

    # ── Section renderers ─────────────────────────────────────────────────────

    def _header(self) -> str:
        st = self._st
        now_str = self._now().strftime("%Y-%m-%d %H:%M:%S UTC")
        mode_str = st.yellow("LIVE") if self._mode == "live" else st.cyan("paper")
        bar = st.bold("═" * self._width)
        title = st.bold("  BETTING AGENT DASHBOARD")
        info  = f"  {st.dim(now_str)}  │  mode: {mode_str}  │  scans: {self._scan_count}"
        return f"{bar}\n{title}\n{info}\n{bar}"

    def _account_section(self) -> str:
        st = self._st
        snap = self._ledger.snapshot()
        lines = [_divider("ACCOUNT", self._width, st)]

        bankroll_str  = f"${snap.bankroll:,.2f}"
        peak_str      = f"${snap.peak_equity:,.2f}"
        daily_pnl_str = (
            st.pnl(snap.daily_pnl_usd)
            + f"  ({st.pnl(snap.daily_pnl_pct * 100, '+.2f')}%)"
        )
        drawdown_str  = (
            st.red(f"-${snap.drawdown_usd:,.2f}")
            if snap.drawdown_usd > 0 else st.green("$0.00")
        ) + f"  ({snap.drawdown_pct * 100:.2f}%)"
        exposure_str  = f"${snap.exposure_usd:,.2f}  ({snap.exposure_pct * 100:.1f}%)"
        cooldown_str  = (
            st.yellow(f"YES — until {snap.cooldown_until.strftime('%H:%M:%S')}")
            if snap.in_cooldown and snap.cooldown_until
            else st.green("No")
        )

        w1, w2 = 18, 30
        def row(label: str, val: str) -> str:
            return f"  {label:<{w1}} {val}"

        lines += [
            row("Bankroll:",  st.bold(bankroll_str)),
            row("Peak equity:", peak_str),
            row("Daily P&L:", daily_pnl_str),
            row("Drawdown:",  drawdown_str),
            row("Exposure:",  exposure_str),
            row("Cooldown:",  cooldown_str),
        ]
        return "\n".join(lines)

    def _positions_section(self) -> str:
        st = self._st
        positions = self._ledger.open_positions
        count_label = f"OPEN POSITIONS ({len(positions)})"
        lines = [_divider(count_label, self._width, st)]

        if not positions:
            lines.append(f"  {st.dim('No open positions.')}")
            return "\n".join(lines)

        hdr = (
            f"  {_col('TICKER', 24)}"
            f"{_col('SIDE', 6)}"
            f"{_col('SIZE', 10)}"
            f"{_col('PRICE', 8)}"
            f"OPENED"
        )
        lines.append(st.dim(hdr))
        lines.append(st.dim("  " + "─" * (self._width - 4)))

        for pos in positions:
            opened = pos.opened_at.strftime("%H:%M:%S")
            side_str = st.green(pos.side) if pos.side == "YES" else st.yellow(pos.side)
            line = (
                f"  {_col(pos.market_ticker, 24)}"
                f"{_col(pos.side, 6)}"
                f"{_col(f'${pos.size_usd:.2f}', 10)}"
                f"{_col(f'{pos.kalshi_price:.3f}', 8)}"
                f"{opened}"
            )
            lines.append(line)

        return "\n".join(lines)

    def _trades_section(self) -> str:
        st = self._st
        entries = _read_jsonl(self._trade_log)[-self._max_trades:]
        entries = list(reversed(entries))   # newest first
        count_label = f"RECENT TRADES (last {self._max_trades})"
        lines = [_divider(count_label, self._width, st)]

        if not entries:
            lines.append(f"  {st.dim('No trades logged yet.')}")
            return "\n".join(lines)

        hdr = (
            f"  {_col('TIME', 10)}"
            f"{_col('TICKER', 22)}"
            f"{_col('SIDE', 6)}"
            f"{_col('ACTION', 18)}"
            f"{_col('EDGE', 8)}"
            f"SIZE"
        )
        lines.append(st.dim(hdr))
        lines.append(st.dim("  " + "─" * (self._width - 4)))

        for e in entries:
            ts_raw = e.get("timestamp_utc", "")
            try:
                ts = datetime.fromisoformat(
                    ts_raw.replace("Z", "+00:00")
                ).strftime("%H:%M:%S")
            except (ValueError, AttributeError):
                ts = ts_raw[:8]

            edge_val = e.get("edge_pct", 0.0) or 0.0
            edge_str = st.pnl(edge_val * 100, "+.1f") + "%"
            action_str = st.action(e.get("action", ""))
            size_str = f"${e.get('position_size_usd', 0):.2f}"

            line = (
                f"  {_col(ts, 10)}"
                f"{_col(e.get('market_ticker', '')[:21], 22)}"
                f"{_col(e.get('side', ''), 6)}"
                f"{_col(e.get('action', ''), 18)}"
                f"{_col(edge_str, 8)}"   # NOTE: edge_str may contain ANSI
                f"{size_str}"
            )
            lines.append(line)

        return "\n".join(lines)
