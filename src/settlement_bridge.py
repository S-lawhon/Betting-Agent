"""
src/settlement_bridge.py
────────────────────────
Bridge between pod trade logs and the portfolio-level settlement layer.

Reads PLACED trades from per-pod JSONL logs, tracks which trades have
been settled, and routes settlement outcomes to CapitalAllocator and
AggregateRiskGuard for P&L accounting.

Architecture
────────────
The pod JSONL logs (data/pods/P-006.jsonl, etc.) record every scan
result — PLACED, SKIPPED_EDGE, SKIPPED_RISK, SKIPPED_DUPLICATE.

When a market resolves (WIN/LOSS/VOID), the caller invokes:
    bridge.settle(fingerprint, pnl)

The bridge:
  1. Looks up the original TradeRecord by fingerprint
  2. Calls capital_allocator.record_settlement(pod_id, pnl)
  3. Calls aggregate_risk.close_position(market_id, pnl)
  4. Persists the settlement to data/settlements.jsonl

Typical flow
────────────
    bridge = SettlementBridge.from_config(config, allocator, guard)

    # At end of day — show open (unsettled) trades:
    open_trades = bridge.get_open_trades()

    # When a market resolves:
    bridge.settle(fingerprint="abc123...", pnl=42.50)

    # Bulk-settle from a list of outcomes:
    bridge.settle_batch([("abc123", 42.50), ("def456", -15.00)])
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Data models ──────────────────────────────────────────────────────


@dataclass
class TradeRecord:
    """A PLACED trade loaded from a pod's JSONL log."""
    fingerprint: str
    pod_id: str
    pod_name: str
    venue: str
    market_id: str
    market_type: str
    event: str
    side: str
    fair_prob: float
    venue_prob: float
    edge_pct: float
    position_size_usd: float
    timestamp_utc: str
    mode: str
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SettlementRecord:
    """A settlement event persisted to the settlement log."""
    fingerprint: str
    pod_id: str
    market_id: str
    pnl: float
    outcome: str           # "WIN", "LOSS", "VOID", "PARTIAL"
    settled_at: str        # ISO timestamp
    position_size_usd: float
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Settlement bridge ────────────────────────────────────────────────


class SettlementBridge:
    """Connects pod trade logs to portfolio-level settlement accounting.

    Args:
        pod_log_dir:      Path to the directory containing per-pod JSONL logs.
        settlement_log:   Path to the running settlement log file.
        capital_allocator: CapitalAllocator instance to notify on settlement.
        aggregate_risk:   AggregateRiskGuard instance to notify on settlement.
        _now_fn:          Injectable clock for testing.
    """

    def __init__(
        self,
        pod_log_dir: Path,
        settlement_log: Path,
        capital_allocator=None,
        aggregate_risk=None,
        _now_fn: Optional[Callable] = None,
        trade_store=None,
    ):
        self._pod_log_dir = Path(pod_log_dir)
        self._settlement_log = Path(settlement_log)
        self._capital_allocator = capital_allocator
        self._aggregate_risk = aggregate_risk
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))
        self._trade_store = trade_store

        # In-memory caches — loaded lazily
        self._trades: Optional[Dict[str, TradeRecord]] = None      # fp → TradeRecord
        self._settled_fps: Optional[set] = None                    # set of settled fps

    # ── Public API ───────────────────────────────────────────────────

    def get_open_trades(self) -> List[TradeRecord]:
        """Return all PLACED trades that have not yet been settled.

        Reads (or re-reads) all pod JSONL logs from the pod log directory.

        Returns:
            List of TradeRecord ordered by timestamp ascending.
        """
        self._load_trades()
        settled = self._load_settled_fps()
        return sorted(
            [t for fp, t in self._trades.items() if fp not in settled],
            key=lambda t: t.timestamp_utc,
        )

    def settle(
        self,
        fingerprint: str,
        pnl: float,
        outcome: str = "WIN",
        notes: Optional[str] = None,
    ) -> bool:
        """Record settlement for a trade.

        Args:
            fingerprint: Unique fingerprint from the original ScanResult.
            pnl:         Profit or loss in USD (negative for a loss).
            outcome:     "WIN", "LOSS", "VOID", or "PARTIAL".
            notes:       Optional human notes about the settlement.

        Returns:
            True if the trade was found and settled, False otherwise.
        """
        self._load_trades()
        settled = self._load_settled_fps()

        if fingerprint in settled:
            logger.warning(
                "settlement_bridge: fingerprint %s already settled", fingerprint[:16]
            )
            return False

        trade = self._trades.get(fingerprint)
        if trade is None:
            logger.warning(
                "settlement_bridge: fingerprint %s not found in trade logs",
                fingerprint[:16],
            )
            return False

        # Determine outcome from pnl if caller passes "WIN" but pnl is negative
        if outcome == "WIN" and pnl < 0:
            outcome = "LOSS"
        elif outcome == "WIN" and pnl == 0:
            outcome = "VOID"

        record = SettlementRecord(
            fingerprint=fingerprint,
            pod_id=trade.pod_id,
            market_id=trade.market_id,
            pnl=pnl,
            outcome=outcome,
            settled_at=self._now_fn().isoformat(),
            position_size_usd=trade.position_size_usd,
            notes=notes,
        )

        # Notify portfolio layers
        self._notify_settlement(trade, pnl)

        # Persist
        self._write_settlement(record)
        settled.add(fingerprint)

        logger.info(
            "settlement_bridge: settled %s | pod=%s market=%s pnl=%.2f outcome=%s",
            fingerprint[:16], trade.pod_id, trade.market_id, pnl, outcome,
        )
        return True

    def settle_batch(
        self, outcomes: List[Tuple[str, float]]
    ) -> Tuple[int, int]:
        """Settle multiple trades.

        Args:
            outcomes: List of (fingerprint, pnl) tuples.

        Returns:
            (succeeded, failed) counts.
        """
        ok = 0
        fail = 0
        for fp, pnl in outcomes:
            outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "VOID")
            if self.settle(fp, pnl, outcome=outcome):
                ok += 1
            else:
                fail += 1
        return ok, fail

    def get_settlement_history(self) -> List[SettlementRecord]:
        """Load and return all settlement records from the settlement log.

        Returns:
            List of SettlementRecord objects, chronological order.
        """
        if not self._settlement_log.exists():
            return []

        records = []
        try:
            with open(self._settlement_log) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        records.append(SettlementRecord(**data))
                    except (json.JSONDecodeError, TypeError, KeyError) as exc:
                        logger.warning(
                            "settlement_bridge: bad settlement log line: %s", exc
                        )
        except OSError as exc:
            logger.warning("settlement_bridge: could not read settlement log: %s", exc)

        return records

    def summary(self) -> Dict[str, Any]:
        """Return a P&L summary across all settled trades.

        Returns:
            Dict with keys: total_pnl, total_settled, wins, losses, voids,
            win_rate, per_pod (dict of pod_id → pnl).
        """
        history = self.get_settlement_history()
        if not history:
            return {
                "total_pnl": 0.0, "total_settled": 0, "wins": 0,
                "losses": 0, "voids": 0, "win_rate": 0.0, "per_pod": {},
            }

        wins = sum(1 for r in history if r.pnl > 0)
        losses = sum(1 for r in history if r.pnl < 0)
        voids = sum(1 for r in history if r.pnl == 0)
        total_pnl = sum(r.pnl for r in history)
        resolved = wins + losses
        win_rate = wins / resolved if resolved > 0 else 0.0

        per_pod: Dict[str, float] = {}
        for r in history:
            per_pod[r.pod_id] = per_pod.get(r.pod_id, 0.0) + r.pnl

        return {
            "total_pnl": round(total_pnl, 2),
            "total_settled": len(history),
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "win_rate": round(win_rate, 4),
            "per_pod": {k: round(v, 2) for k, v in per_pod.items()},
        }

    # ── Internal helpers ─────────────────────────────────────────────

    def _load_trades(self) -> None:
        """Load all PLACED trades from pod JSONL logs (once per session).

        When a TradeStore is available, reads from its in-memory indexes
        instead of parsing pod log files from disk.
        """
        if self._trades is not None:
            return

        self._trades = {}

        if self._trade_store is not None:
            for entry in self._trade_store.get_placed_entries():
                fp = entry.get("fingerprint", "")
                if not fp:
                    continue
                self._trades[fp] = TradeRecord(
                    fingerprint=fp,
                    pod_id=entry.get("pod_id", ""),
                    pod_name=entry.get("pod_name", ""),
                    venue=entry.get("venue", ""),
                    market_id=entry.get("market_id", ""),
                    market_type=entry.get("market_type", ""),
                    event=entry.get("event", ""),
                    side=entry.get("side", ""),
                    fair_prob=float(entry.get("fair_prob", 0)),
                    venue_prob=float(entry.get("venue_prob", 0)),
                    edge_pct=float(entry.get("edge_pct", 0)),
                    position_size_usd=float(entry.get("position_size_usd", 0)),
                    timestamp_utc=entry.get("timestamp_utc", ""),
                    mode=entry.get("mode", "paper"),
                    order_id=entry.get("order_id"),
                    fill_price=entry.get("fill_price"),
                    extra=entry.get("extra", {}),
                )
            return

        if not self._pod_log_dir.exists():
            logger.info(
                "settlement_bridge: pod log dir not found: %s", self._pod_log_dir
            )
            return

        for jsonl_path in sorted(self._pod_log_dir.glob("*.jsonl")):
            self._load_pod_log(jsonl_path)

    def _load_pod_log(self, path: Path) -> None:
        """Load PLACED trades from one pod log file."""
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("action") != "PLACED":
                            continue
                        fp = data.get("fingerprint", "")
                        if not fp:
                            continue

                        self._trades[fp] = TradeRecord(
                            fingerprint=fp,
                            pod_id=data.get("pod_id", ""),
                            pod_name=data.get("pod_name", ""),
                            venue=data.get("venue", ""),
                            market_id=data.get("market_id", ""),
                            market_type=data.get("market_type", ""),
                            event=data.get("event", ""),
                            side=data.get("side", ""),
                            fair_prob=float(data.get("fair_prob", 0)),
                            venue_prob=float(data.get("venue_prob", 0)),
                            edge_pct=float(data.get("edge_pct", 0)),
                            position_size_usd=float(data.get("position_size_usd", 0)),
                            timestamp_utc=data.get("timestamp_utc", ""),
                            mode=data.get("mode", "paper"),
                            order_id=data.get("order_id"),
                            fill_price=data.get("fill_price"),
                            extra=data.get("extra", {}),
                        )
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        logger.warning(
                            "settlement_bridge: bad line in %s: %s", path.name, exc
                        )
        except OSError as exc:
            logger.warning(
                "settlement_bridge: could not read %s: %s", path.name, exc
            )

    def _load_settled_fps(self) -> set:
        """Load the set of already-settled fingerprints (cached).

        When a TradeStore is available, reads from its settlement index.
        """
        if self._settled_fps is not None:
            return self._settled_fps

        fps: set = set()

        if self._trade_store is not None:
            settlements = self._trade_store.get_settlements_for_bootstrap()
            for entry in settlements:
                fp = entry.get("fingerprint", "")
                if fp:
                    fps.add(fp)
            self._settled_fps = fps
            return fps

        if self._settlement_log.exists():
            try:
                with open(self._settlement_log) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            fp = data.get("fingerprint", "")
                            if fp:
                                fps.add(fp)
                        except (json.JSONDecodeError, TypeError) as exc:
                            logger.warning(
                                "settlement_bridge: skipping malformed settlement line: %s", exc
                            )
            except OSError as exc:
                logger.warning(
                    "settlement_bridge: could not read settlement log: %s", exc
                )

        self._settled_fps = fps
        return fps

    def _notify_settlement(self, trade: TradeRecord, pnl: float) -> None:
        """Notify CapitalAllocator and AggregateRiskGuard of a settlement."""
        if self._capital_allocator:
            try:
                self._capital_allocator.record_settlement(
                    trade.pod_id, pnl, fingerprint=trade.fingerprint,
                )
            except Exception as exc:
                logger.error(
                    "settlement_bridge: capital_allocator.record_settlement failed: %s", exc
                )

        if self._aggregate_risk:
            try:
                self._aggregate_risk.close_position(trade.market_id, pnl)
            except Exception as exc:
                logger.error(
                    "settlement_bridge: aggregate_risk.close_position failed: %s", exc
                )

    def _write_settlement(self, record: SettlementRecord) -> None:
        """Append a settlement record to the settlement log.

        When a TradeStore is available, also records the settlement in the
        store's in-memory indexes via record_settlement().
        """
        if self._trade_store is not None:
            try:
                self._trade_store.record_settlement(
                    fingerprint=record.fingerprint,
                    outcome=record.outcome,
                    pnl=record.pnl,
                    settled_at=record.settled_at,
                )
            except Exception as exc:
                logger.error(
                    "settlement_bridge: trade_store.record_settlement failed: %s", exc
                )

        # Always persist to the settlement log file (separate from trade log)
        try:
            self._settlement_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._settlement_log, "a") as fh:
                fh.write(json.dumps(record.to_dict()) + "\n")
        except OSError as exc:
            logger.error("settlement_bridge: could not write settlement: %s", exc)

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        capital_allocator=None,
        aggregate_risk=None,
        trade_store=None,
    ) -> "SettlementBridge":
        """Build SettlementBridge from merged config dict.

        Reads path configuration from config['paths']:
          - pod_logs:    directory for per-pod JSONL files
          - trade_log:   main trade log (used to derive settlement log path)

        Args:
            config:           Merged config dict.
            capital_allocator: Optional CapitalAllocator to notify on settlement.
            aggregate_risk:   Optional AggregateRiskGuard to notify on settlement.
            trade_store:      Optional TradeStore for in-memory indexed reads.

        Returns:
            Configured SettlementBridge.
        """
        paths_cfg = config.get("paths", {})
        pod_log_dir = Path(paths_cfg.get("pod_logs", "data/pods"))
        # Settlement log lives alongside the trade log
        trade_log = Path(paths_cfg.get("trade_log", "data/trade_logs/trade_log.jsonl"))
        settlement_log = trade_log.parent / "settlements.jsonl"

        return cls(
            pod_log_dir=pod_log_dir,
            settlement_log=settlement_log,
            capital_allocator=capital_allocator,
            aggregate_risk=aggregate_risk,
            trade_store=trade_store,
        )
