"""
src/backtester.py
─────────────────
Phase 9: replay edge_history.csv records to evaluate strategy performance.

Input
─────
  edge_history.csv  — rows matching data/schema/edge_history.schema.json
                      (date, market_ticker, side, fair_prob, kalshi_prob,
                       edge_pct, ev, position_size_usd, outcome, pnl_usd,
                       fill_price?, sport?)

Key concepts
────────────
  run()       — replay with known outcomes (pnl_usd from CSV)
  simulate()  — resolve PENDING rows with a fair_prob Bernoulli draw
  export_*()  — write equity_curve.csv and per-sport report.csv

Outputs
───────
  BacktestResult  — aggregate statistics dataclass
  equity_curve.csv — running bankroll per resolved trade
  report.csv       — one row per sport + one OVERALL row
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from logger_setup import get_logger

log = get_logger(__name__)

_DEFAULT_FEE_PCT = 0.07
_VALID_OUTCOMES = {"WIN", "LOSS", "VOID", "PENDING"}

# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    """One row from edge_history.csv (or synthesised for simulation)."""
    date: str
    market_ticker: str
    side: str
    fair_prob: float
    kalshi_prob: float
    edge_pct: float
    ev: float
    position_size_usd: float
    outcome: str                # WIN | LOSS | VOID | PENDING
    pnl_usd: float
    fill_price: Optional[float] = None
    sport: Optional[str] = None


@dataclass
class BacktestResult:
    """Aggregated statistics produced by :meth:`Backtester.run`."""
    n_trades: int = 0           # WIN + LOSS only (resolved)
    n_wins: int = 0
    n_losses: int = 0
    n_void: int = 0
    n_pending: int = 0
    win_rate: float = 0.0       # n_wins / (n_wins + n_losses)
    total_pnl_usd: float = 0.0
    roi_pct: float = 0.0        # total_pnl_usd / total_wagered * 100
    total_wagered_usd: float = 0.0
    avg_edge_pct: float = 0.0
    avg_ev: float = 0.0
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0   # fraction of peak equity
    sharpe: float = 0.0             # mean pnl / std pnl per trade
    by_sport: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    equity_curve: List[float] = field(default_factory=list)


# ── Math helpers ───────────────────────────────────────────────────────────────

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float], mean: Optional[float] = None) -> float:
    if len(values) < 2:
        return 0.0
    m = mean if mean is not None else _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _compute_drawdown(curve: List[float]) -> tuple:
    """Return (max_drawdown_usd, max_drawdown_pct) over an equity curve."""
    if len(curve) < 2:
        return 0.0, 0.0
    peak = curve[0]
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for val in curve:
        if val > peak:
            peak = val
        dd_usd = peak - val
        dd_pct = dd_usd / peak if peak > 0 else 0.0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct
    return max_dd_usd, max_dd_pct


# ── Backtester ─────────────────────────────────────────────────────────────────

class Backtester:
    """
    Replay historical trades and compute performance statistics.

    Parameters
    ----------
    initial_bankroll : float
        Starting bankroll for the equity curve.
    fee_pct : float
        Kalshi fee on winnings.  Used only in :meth:`simulate` for PENDING rows.
    """

    def __init__(
        self,
        initial_bankroll: float = 10_000.0,
        fee_pct: float = _DEFAULT_FEE_PCT,
    ) -> None:
        self._initial_bankroll = initial_bankroll
        self._fee_pct = fee_pct

    @classmethod
    def from_config(cls, config: dict) -> "Backtester":
        """Construct from config.yaml dict."""
        bankroll = config.get("risk", {}).get("initial_bankroll", 10_000.0)
        fee_pct  = config.get("edge", {}).get("fee_pct", _DEFAULT_FEE_PCT)
        return cls(initial_bankroll=bankroll, fee_pct=fee_pct)

    # ── Public run API ─────────────────────────────────────────────────────────

    def run(self, trades: List[BacktestTrade]) -> BacktestResult:
        """
        Replay trades that have known outcomes.
        PENDING and VOID rows are counted but do not affect P&L or equity.
        """
        return self._process(trades)

    def run_csv(self, csv_path: Path) -> BacktestResult:
        """Convenience: load edge_history.csv and call :meth:`run`."""
        return self.run(self.load_csv(csv_path))

    def simulate(
        self, trades: List[BacktestTrade], seed: int = 42
    ) -> BacktestResult:
        """
        Replay trades, resolving PENDING rows stochastically.

        Each PENDING trade is resolved by drawing ``random() < fair_prob``
        (WIN) or not (LOSS), then computing the P&L from position_size_usd
        and kalshi_prob.  WIN/LOSS rows keep their existing pnl_usd.
        """
        rng = random.Random(seed)
        resolved: List[BacktestTrade] = []
        for t in trades:
            if t.outcome != "PENDING":
                resolved.append(t)
                continue
            won = rng.random() < t.fair_prob
            p = max(t.kalshi_prob, 1e-6)
            sim_pnl = (
                t.position_size_usd * (1.0 - p) / p * (1.0 - self._fee_pct)
                if won
                else -t.position_size_usd
            )
            resolved.append(BacktestTrade(
                date=t.date,
                market_ticker=t.market_ticker,
                side=t.side,
                fair_prob=t.fair_prob,
                kalshi_prob=t.kalshi_prob,
                edge_pct=t.edge_pct,
                ev=t.ev,
                position_size_usd=t.position_size_usd,
                outcome="WIN" if won else "LOSS",
                pnl_usd=round(sim_pnl, 4),
                fill_price=t.fill_price,
                sport=t.sport,
            ))
        return self._process(resolved)

    # ── Export helpers ─────────────────────────────────────────────────────────

    def export_equity_curve(self, result: BacktestResult, path: Path) -> int:
        """
        Write equity curve to CSV with columns (trade_index, bankroll).
        Returns number of rows written, or 0 on error.
        """
        rows = [
            {"trade_index": i, "bankroll": round(v, 2)}
            for i, v in enumerate(result.equity_curve)
        ]
        if not rows:
            return 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["trade_index", "bankroll"])
                writer.writeheader()
                writer.writerows(rows)
            log.info("equity_curve_exported", path=str(path), rows=len(rows))
            return len(rows)
        except OSError as exc:
            log.warning("equity_curve_write_error", path=str(path), error=str(exc))
            return 0

    def export_report(self, result: BacktestResult, path: Path) -> int:
        """
        Write per-sport summary + OVERALL row to CSV.
        Returns number of rows written (including OVERALL), or 0 on error.
        """
        fieldnames = [
            "sport", "n_trades", "n_wins", "n_losses",
            "win_rate", "total_pnl_usd", "roi_pct", "avg_edge_pct",
        ]
        rows: List[Dict[str, Any]] = []

        for sport, s in result.by_sport.items():
            rows.append({
                "sport":          sport,
                "n_trades":       s["n_trades"],
                "n_wins":         s["n_wins"],
                "n_losses":       s["n_losses"],
                "win_rate":       round(s["win_rate"], 4),
                "total_pnl_usd":  round(s["total_pnl_usd"], 2),
                "roi_pct":        round(s["roi_pct"], 2),
                "avg_edge_pct":   round(s["avg_edge_pct"], 4),
            })

        rows.append({
            "sport":          "OVERALL",
            "n_trades":       result.n_trades,
            "n_wins":         result.n_wins,
            "n_losses":       result.n_losses,
            "win_rate":       round(result.win_rate, 4),
            "total_pnl_usd":  round(result.total_pnl_usd, 2),
            "roi_pct":        round(result.roi_pct, 2),
            "avg_edge_pct":   round(result.avg_edge_pct, 4),
        })

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            log.info("report_exported", path=str(path), rows=len(rows))
            return len(rows)
        except OSError as exc:
            log.warning("report_write_error", path=str(path), error=str(exc))
            return 0

    # ── Static loaders ─────────────────────────────────────────────────────────

    @staticmethod
    def load_csv(path: Path) -> List[BacktestTrade]:
        """
        Load edge_history.csv → list of BacktestTrade.
        Missing file or bad rows are logged and skipped; never raises.
        """
        if not path.exists():
            log.warning("backtest_csv_not_found", path=str(path))
            return []
        trades: List[BacktestTrade] = []
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    try:
                        trades.append(Backtester._parse_row(row))
                    except (KeyError, ValueError) as exc:
                        log.warning("backtest_row_skipped", error=str(exc))
        except OSError as exc:
            log.warning("backtest_csv_read_error", path=str(path), error=str(exc))
        return trades

    @staticmethod
    def _parse_row(row: Dict[str, str]) -> BacktestTrade:
        fill = row.get("fill_price", "")
        return BacktestTrade(
            date=row["date"],
            market_ticker=row["market_ticker"],
            side=row["side"],
            fair_prob=float(row["fair_prob"]),
            kalshi_prob=float(row["kalshi_prob"]),
            edge_pct=float(row["edge_pct"]),
            ev=float(row["ev"]),
            position_size_usd=float(row["position_size_usd"]),
            outcome=row["outcome"],
            pnl_usd=float(row["pnl_usd"]),
            fill_price=float(fill) if fill and fill != "None" else None,
            sport=row.get("sport") or None,
        )

    # ── Internal engine ────────────────────────────────────────────────────────

    def _process(self, trades: List[BacktestTrade]) -> BacktestResult:
        n_wins = n_losses = n_void = n_pending = 0
        total_pnl = total_wagered = 0.0
        edge_vals: List[float] = []
        ev_vals: List[float] = []
        pnl_vals: List[float] = []

        bankroll = self._initial_bankroll
        equity_curve: List[float] = [bankroll]

        # per-sport accumulators: (n_wins, n_losses, pnl, wagered, edge_sum)
        sport_wins:    Dict[str, int]   = {}
        sport_losses:  Dict[str, int]   = {}
        sport_pnl:     Dict[str, float] = {}
        sport_wagered: Dict[str, float] = {}
        sport_edge:    Dict[str, float] = {}  # sum, divide by n_trades at end

        for t in trades:
            outcome = t.outcome.upper() if t.outcome else "PENDING"

            if outcome == "VOID":
                n_void += 1
                equity_curve.append(bankroll)
                continue
            if outcome == "PENDING":
                n_pending += 1
                equity_curve.append(bankroll)
                continue

            # WIN or LOSS (or unrecognised → treat as LOSS)
            is_win = outcome == "WIN"
            if is_win:
                n_wins += 1
            else:
                n_losses += 1

            total_pnl    += t.pnl_usd
            total_wagered += t.position_size_usd
            edge_vals.append(t.edge_pct)
            ev_vals.append(t.ev)
            pnl_vals.append(t.pnl_usd)
            bankroll += t.pnl_usd
            equity_curve.append(bankroll)

            # per-sport
            sp = t.sport or "unknown"
            sport_wins[sp]    = sport_wins.get(sp, 0)    + (1 if is_win else 0)
            sport_losses[sp]  = sport_losses.get(sp, 0)  + (0 if is_win else 1)
            sport_pnl[sp]     = sport_pnl.get(sp, 0.0)   + t.pnl_usd
            sport_wagered[sp] = sport_wagered.get(sp, 0.0) + t.position_size_usd
            sport_edge[sp]    = sport_edge.get(sp, 0.0)   + t.edge_pct

        n_trades = n_wins + n_losses
        win_rate  = n_wins / n_trades if n_trades > 0 else 0.0
        roi_pct   = total_pnl / total_wagered * 100 if total_wagered > 0 else 0.0
        max_dd_usd, max_dd_pct = _compute_drawdown(equity_curve)
        mean_pnl  = _mean(pnl_vals)
        std_pnl   = _std(pnl_vals, mean_pnl)
        sharpe    = mean_pnl / std_pnl if std_pnl > 0 else 0.0

        by_sport: Dict[str, Dict[str, Any]] = {}
        for sp in set(sport_wins) | set(sport_losses):
            nt = sport_wins.get(sp, 0) + sport_losses.get(sp, 0)
            nw = sport_wins.get(sp, 0)
            nl = sport_losses.get(sp, 0)
            pnl  = sport_pnl.get(sp, 0.0)
            wag  = sport_wagered.get(sp, 0.0)
            edge = sport_edge.get(sp, 0.0)
            by_sport[sp] = {
                "n_trades":     nt,
                "n_wins":       nw,
                "n_losses":     nl,
                "win_rate":     nw / nt if nt > 0 else 0.0,
                "total_pnl_usd": round(pnl, 4),
                "roi_pct":      round(pnl / wag * 100 if wag > 0 else 0.0, 4),
                "avg_edge_pct": round(edge / nt if nt > 0 else 0.0, 4),
            }

        return BacktestResult(
            n_trades=n_trades,
            n_wins=n_wins,
            n_losses=n_losses,
            n_void=n_void,
            n_pending=n_pending,
            win_rate=win_rate,
            total_pnl_usd=total_pnl,
            roi_pct=roi_pct,
            total_wagered_usd=total_wagered,
            avg_edge_pct=_mean(edge_vals),
            avg_ev=_mean(ev_vals),
            max_drawdown_usd=max_dd_usd,
            max_drawdown_pct=max_dd_pct,
            sharpe=sharpe,
            by_sport=by_sport,
            equity_curve=equity_curve,
        )
