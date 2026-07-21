"""
src/executor.py
───────────────
Trade executor with live guardrails.

Sits between the Scanner (evaluation + sizing) and the Kalshi API (order
submission).  Call ``run_cycle()`` once per polling interval to drive a
full scan-and-execute cycle.

Modes
─────
  paper  — No real orders placed.  The Scanner's PLACED entries are used
            to open paper positions in the Ledger for P&L tracking.

  live   — Real limit orders submitted via ``KalshiClient.place_order()``.
            Two independent gates must BOTH be cleared before any real
            order is placed:

              Gate 1 — Environment variable
                ``I_UNDERSTAND_LIVE_TRADING=true`` must be set in the
                process environment.  The Executor reads this at
                construction time (``from_config``) or it can be injected
                via ``live_trading_confirmed=True``.

              Gate 2 — KalshiClient guard
                ``KalshiClient.live_trading_confirmed`` must be ``True``.
                The Executor sets this only after Gate 1 passes.

            If either gate fails, ``LiveTradingNotConfirmedError`` is
            raised from ``run_cycle()`` before any network call is made.

Order construction
──────────────────
  yes_price  = round(kalshi_prob × 100)          integer cents 1–99
  side       = entry["side"].lower()             "yes" | "no"
  contracts  = max(1, floor(size_usd / cost))   where cost = price of the
                                                 chosen side (0–1)
  client_order_id = uuid4 hex string             idempotency

Ledger integration
──────────────────
After every fill (paper or live) the Executor opens a ``Position`` in
``risk_manager.ledger``.  This keeps exposure, drawdown, and daily P&L
tracking accurate across scan cycles.

Trade log
─────────
Paper mode  — the Scanner already wrote the PLACED record; Executor does
              not append a duplicate.
Live mode   — Executor appends an EXECUTED record (same schema fields as
              PLACED, plus real ``order_id``) to provide an audit trail of
              actual fills alongside the evaluation record.
"""

from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from kalshi_client import KalshiClient, KalshiDemoOnlyError
from logger_setup import get_logger
from risk_manager import Position, RiskManager
from scanner import Scanner, _make_fingerprint

import json

log = get_logger(__name__)

# Environment variable that must equal "true" (case-insensitive) to permit
# live trading.
_LIVE_GATE_ENV_VAR = "I_UNDERSTAND_LIVE_TRADING"


# ── Exceptions ────────────────────────────────────────────────────────────────

class LiveTradingNotConfirmedError(Exception):
    """
    Raised when a live order is attempted without the env-var gate being set.

    Resolution: set ``I_UNDERSTAND_LIVE_TRADING=true`` in your environment
    and ensure ``config.yaml`` has ``environment: live``.
    """


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    """
    Result of executing one PLACED Scanner entry.

    Attributes
    ----------
    entry : dict
        Original PLACED entry from ``Scanner.scan_once()``.
    mode : str
        ``"paper"`` or ``"live"``.
    order_id : str or None
        Kalshi order ID from a live fill; None for paper.
    fill_price : float or None
        Actual fill probability (paper = mid-price; live = mid at submit time).
    contracts : int
        Number of contracts submitted (0 for paper).
    success : bool
        False if order placement raised an exception.
    error : str or None
        Exception message when ``success=False``.
    """
    entry: Dict[str, Any]
    mode: str
    order_id: Optional[str]
    fill_price: Optional[float]
    contracts: int
    success: bool
    error: Optional[str] = None


# ── Order helpers ─────────────────────────────────────────────────────────────

def _build_order(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a Kalshi limit-order dict from a Scanner PLACED entry.

    Parameters
    ----------
    entry : dict
        A PLACED trade-log entry with keys ``market_ticker``, ``side``,
        ``kalshi_prob``, and ``position_size_usd``.

    Returns
    -------
    Order dict suitable for ``KalshiClient.place_order()``.
    """
    side = entry["side"].lower()               # "yes" | "no"
    yes_prob = entry["kalshi_prob"]            # YES mid-price (0-1)
    size_usd = entry["position_size_usd"]

    # Effective per-contract cost depends on which side we're buying
    if side == "yes":
        cost_per_contract = yes_prob
    else:
        cost_per_contract = 1.0 - yes_prob

    # Whole number of contracts; at least 1
    contracts = max(1, math.floor(size_usd / cost_per_contract))

    # Kalshi API always takes yes_price in integer cents
    yes_price_cents = round(yes_prob * 100)
    yes_price_cents = max(1, min(99, yes_price_cents))   # clamp 1–99

    return {
        "ticker":           entry["market_ticker"],
        "client_order_id":  uuid.uuid4().hex,
        "type":             "limit",
        "action":           "buy",
        "side":             side,
        "count":            contracts,
        "yes_price":        yes_price_cents,
    }


# ── Executor ──────────────────────────────────────────────────────────────────

class Executor:
    """
    Orchestrates scan-and-execute cycles with live trading guardrails.

    Parameters
    ----------
    scanner : Scanner
        Pre-built Scanner instance; ``run_cycle()`` calls its
        ``scan_once()`` method.
    kalshi_client : KalshiClient
        Used only in live mode to submit orders.
    risk_manager : RiskManager
        Ledger positions are opened here after every fill (paper + live).
    mode : str
        ``"paper"`` or ``"live"``.
    live_trading_confirmed : bool
        Set ``True`` only after verifying the env-var gate.  The Executor
        also sets ``kalshi_client.live_trading_confirmed`` to the same value.
    trade_log_path : Path, optional
        Where to append EXECUTED records in live mode.
    _now_fn : callable, optional
        Returns current UTC datetime — injectable for tests.
    """

    def __init__(
        self,
        scanner: Scanner,
        kalshi_client: KalshiClient,
        risk_manager: RiskManager,
        mode: str = "paper",
        live_trading_confirmed: bool = False,
        trade_log_path: Optional[Path] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._scanner  = scanner
        self._kalshi   = kalshi_client
        self._risk     = risk_manager
        self._mode     = mode
        self._confirmed = live_trading_confirmed
        self._log_path  = trade_log_path
        self._now       = _now_fn or (lambda: datetime.now(tz=timezone.utc))

        # Propagate the confirmation flag to the KalshiClient's guard.
        if live_trading_confirmed:
            self._kalshi.live_trading_confirmed = True

    @classmethod
    def from_config(
        cls,
        config: dict,
        scanner: Scanner,
        kalshi_client: KalshiClient,
        risk_manager: RiskManager,
        **kwargs,
    ) -> "Executor":
        """
        Construct from a loaded config.yaml dict + pre-built components.

        Reads ``I_UNDERSTAND_LIVE_TRADING`` from the process environment.
        If the config ``environment`` is ``"live"`` and the env var is not
        set, construction succeeds but ``run_cycle()`` will raise
        ``LiveTradingNotConfirmedError`` for any live execution attempt.
        """
        raw_mode = config.get("environment", "demo")
        mode = "live" if raw_mode == "live" else "paper"

        live_confirmed = (
            os.environ.get(_LIVE_GATE_ENV_VAR, "").lower() == "true"
        )

        log_path = Path(
            config.get("paths", {}).get(
                "trade_log", "data/trade_logs/trade_log.jsonl"
            )
        )

        return cls(
            scanner=scanner,
            kalshi_client=kalshi_client,
            risk_manager=risk_manager,
            mode=mode,
            live_trading_confirmed=live_confirmed,
            trade_log_path=log_path,
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run_cycle(self) -> List[ExecutionResult]:
        """
        Run one scan cycle and execute all PLACED decisions.

        Returns
        -------
        List of ExecutionResult — one per PLACED entry processed.
        """
        entries = self._scanner.scan_once()
        results: List[ExecutionResult] = []

        for entry in entries:
            if entry.get("action") == "PLACED":
                result = self._execute(entry)
                results.append(result)

        log.info(
            "executor_cycle_done",
            mode=self._mode,
            placed=len(results),
            successful=sum(1 for r in results if r.success),
        )
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _execute(self, entry: Dict[str, Any]) -> ExecutionResult:
        """Dispatch to paper or live execution."""
        if self._mode == "live":
            return self._execute_live(entry)
        return self._execute_paper(entry)

    def _execute_paper(self, entry: Dict[str, Any]) -> ExecutionResult:
        """
        Simulate a paper fill: open a Ledger position, return result.
        No real order is placed; the Scanner already wrote the log entry.
        """
        fill_price = entry.get("kalshi_prob", 0.0)
        size_usd   = entry.get("position_size_usd", 0.0)

        if size_usd > 0:
            pos = Position.new(
                market_ticker=entry["market_ticker"],
                side=entry["side"],
                size_usd=size_usd,
                kalshi_price=fill_price,
                opened_at=self._now(),
            )
            self._risk.ledger.open_position(pos)
            log.info(
                "executor_paper_fill",
                ticker=entry["market_ticker"],
                side=entry["side"],
                size_usd=round(size_usd, 2),
                fill_price=round(fill_price, 4),
            )

        return ExecutionResult(
            entry=entry,
            mode="paper",
            order_id=None,
            fill_price=fill_price,
            contracts=0,
            success=True,
        )

    def _execute_live(self, entry: Dict[str, Any]) -> ExecutionResult:
        """
        Place a real limit order on Kalshi, open a Ledger position, and
        append an EXECUTED record to the trade log.

        Raises
        ------
        LiveTradingNotConfirmedError
            If ``I_UNDERSTAND_LIVE_TRADING`` was not set at construction.
        """
        # ── Gate 1: env-var confirmation ──────────────────────────────────────
        if not self._confirmed:
            raise LiveTradingNotConfirmedError(
                f"Set {_LIVE_GATE_ENV_VAR}=true to enable live trading."
            )

        order = _build_order(entry)
        contracts = order["count"]
        fill_price = entry.get("kalshi_prob", 0.0)

        # ── Gate 2: place order (KalshiClient guard as second layer) ──────────
        try:
            resp = self._kalshi.place_order(order)
        except KalshiDemoOnlyError as exc:
            log.error(
                "executor_demo_guard_triggered",
                ticker=entry["market_ticker"],
                error=str(exc),
            )
            return ExecutionResult(
                entry=entry, mode="live",
                order_id=None, fill_price=None,
                contracts=contracts, success=False, error=str(exc),
            )
        except Exception as exc:
            log.error(
                "executor_order_error",
                ticker=entry["market_ticker"],
                error=str(exc),
            )
            return ExecutionResult(
                entry=entry, mode="live",
                order_id=None, fill_price=None,
                contracts=contracts, success=False, error=str(exc),
            )

        order_id = (
            resp.get("order", {}).get("order_id")
            or resp.get("order_id")
        )

        # ── Open Ledger position ──────────────────────────────────────────────
        size_usd = entry.get("position_size_usd", 0.0)
        if size_usd > 0:
            pos = Position.new(
                market_ticker=entry["market_ticker"],
                side=entry["side"],
                size_usd=size_usd,
                kalshi_price=fill_price,
                opened_at=self._now(),
            )
            self._risk.ledger.open_position(pos)

        # ── Append EXECUTED record to trade log ───────────────────────────────
        now = self._now()
        exec_record = {
            **entry,
            "fingerprint": _make_fingerprint(
                entry["market_ticker"], entry["side"], now
            ),
            "timestamp_utc": now.isoformat(),
            "action":        "EXECUTED",
            "order_id":      order_id,
            "fill_price":    round(fill_price, 6),
        }
        self._append_log(exec_record)

        log.info(
            "executor_live_fill",
            ticker=entry["market_ticker"],
            side=entry["side"],
            order_id=order_id,
            contracts=contracts,
            fill_price=round(fill_price, 4),
        )
        return ExecutionResult(
            entry=entry, mode="live",
            order_id=order_id, fill_price=fill_price,
            contracts=contracts, success=True,
        )

    def _append_log(self, record: Dict[str, Any]) -> None:
        """Append one JSON line to the trade log (live EXECUTED records)."""
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error(
                "executor_log_write_error",
                path=str(self._log_path),
                error=str(exc),
            )
