"""
src/capital_allocator.py
────────────────────────
Portfolio-level capital allocation across pods.

The "CIO" layer: decides how much bankroll each pod gets based on
historical performance, risk metrics, and configurable weights.

Allocation strategies:
  - EQUAL: Split bankroll equally across active pods.
  - WEIGHTED: Configurable per-pod weight (from config).
  - PERFORMANCE: Tilt towards pods with better Sharpe / win rate.

The allocator runs pre-cycle (adjusts pod bankrolls) and post-cycle
(updates performance tracking).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.constants import DEFAULT_BANKROLL

logger = logging.getLogger(__name__)


@dataclass
class PodAllocation:
    """Current allocation for a single pod."""
    pod_id: str
    allocation_pct: float     # 0-1, fraction of total bankroll
    allocated_usd: float      # Dollar amount allocated
    max_position_usd: float   # Max single trade size for this pod
    active: bool = True


@dataclass
class PodPerformance:
    """Rolling performance tracker for a single pod."""
    pod_id: str
    pod_name: str = ""
    total_placed: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    consecutive_losses: int = 0
    last_updated: Optional[str] = None

    @property
    def win_rate(self) -> float:
        total = self.total_wins + self.total_losses
        if total == 0:
            return 0.0
        return self.total_wins / total

    @property
    def avg_pnl(self) -> float:
        if self.total_placed == 0:
            return 0.0
        return self.total_pnl / self.total_placed


class CapitalAllocator:
    """Portfolio-level capital allocation across active pods.

    Manages:
      - Total bankroll and per-pod allocation
      - Performance tracking per pod
      - Dynamic rebalancing based on performance
      - Drawdown-based pod throttling

    Usage:
        allocator = CapitalAllocator(total_bankroll=10000, strategy="weighted")
        allocator.pre_cycle(pods)    # Adjust pod bankrolls before scanning
        allocator.post_cycle(report) # Update performance after scanning
    """

    def __init__(
        self,
        total_bankroll: float = DEFAULT_BANKROLL,
        strategy: str = "equal",
        pod_weights: Optional[Dict[str, float]] = None,
        max_pod_pct: float = 0.40,
        min_pod_pct: float = 0.05,
        drawdown_throttle_pct: float = 0.10,
        rebalance_interval: int = 10,
        max_bet_pct: float = 0.06,
        _now_fn: Optional[Callable] = None,
    ):
        self.total_bankroll = total_bankroll
        self.strategy = strategy
        self._pod_weights = pod_weights or {}
        self.max_pod_pct = max_pod_pct
        self.min_pod_pct = min_pod_pct
        self.drawdown_throttle_pct = drawdown_throttle_pct
        self.rebalance_interval = rebalance_interval
        self.max_bet_pct = max_bet_pct
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))

        self._allocations: Dict[str, PodAllocation] = {}
        self._performance: Dict[str, PodPerformance] = {}
        self._settled_fps: set = set()  # fingerprints already counted (dedup)
        self._cycles_since_rebalance = 0

    # ── Pre/post cycle hooks ────────────────────────────────────────

    def pre_cycle(self, pods: list) -> Dict[str, PodAllocation]:
        """Adjust pod bankrolls before a scan cycle.

        Called by PodRunner before each cycle.  Returns the
        current allocation map.
        """
        pod_ids = [p.pod_id for p in pods]

        # Initialize tracking for new pods; always refresh pod_name from the
        # live pod object so config changes (e.g. renaming a pod) take effect
        # without requiring a trade-log rewrite.
        for pod in pods:
            pid = pod.pod_id
            live_name = getattr(pod, "pod_name", "")
            if pid not in self._performance:
                self._performance[pid] = PodPerformance(
                    pod_id=pid,
                    pod_name=live_name,
                )
            elif live_name and self._performance[pid].pod_name != live_name:
                self._performance[pid].pod_name = live_name

        # Rebalance if due
        self._cycles_since_rebalance += 1
        if (not self._allocations or
                self._cycles_since_rebalance >= self.rebalance_interval):
            self._rebalance(pod_ids)
            self._cycles_since_rebalance = 0

        # Apply drawdown throttling
        for pid in pod_ids:
            perf = self._performance.get(pid)
            alloc = self._allocations.get(pid)
            if perf and alloc and perf.max_drawdown >= self.drawdown_throttle_pct:
                # Halve allocation for pods in drawdown
                alloc.allocation_pct = max(
                    self.min_pod_pct,
                    alloc.allocation_pct * 0.5,
                )
                alloc.allocated_usd = alloc.allocation_pct * self.total_bankroll
                alloc.max_position_usd = alloc.allocated_usd * self.max_bet_pct
                logger.warning(
                    "capital_allocator: throttled %s (drawdown %.1f%%)",
                    pid, perf.max_drawdown * 100,
                )

        # Push allocations to pod risk managers where possible.
        #
        # IMPORTANT: Multiple pods may share the SAME Ledger instance
        # (e.g. P-001 and P-002 both use the shared risk_manager built
        # in engine.build_shared_deps).  If we naively set
        #   rm.ledger.bankroll = alloc.allocated_usd
        # the last pod in the loop wins, and the shared Ledger's bankroll
        # shrinks to a single pod's slice (~$143 with 7 pods and $1000).
        # Since ALL open positions count against that tiny bankroll, every
        # pod sees >100% exposure and gets blocked.
        #
        # Fix: track which ledger objects we've seen.  If a ledger is
        # shared by multiple pods, set its bankroll to the TOTAL bankroll
        # (per-pod budget enforcement uses max_position_usd instead).
        seen_ledgers: Dict[int, List[str]] = {}  # id(ledger) → [pod_ids]
        for pod in pods:
            rm = getattr(pod, "risk_manager", None)
            if rm is not None and hasattr(rm, "ledger"):
                lid = id(rm.ledger)
                if lid not in seen_ledgers:
                    seen_ledgers[lid] = []
                seen_ledgers[lid].append(pod.pod_id)

        for pod in pods:
            alloc = self._allocations.get(pod.pod_id)
            rm = getattr(pod, "risk_manager", None)
            if alloc and rm is not None:
                if hasattr(rm, "ledger") and hasattr(rm.ledger, "bankroll"):
                    lid = id(rm.ledger)
                    if len(seen_ledgers.get(lid, [])) > 1:
                        # Shared ledger: use total bankroll so that one
                        # pod's positions don't starve the others.
                        rm.ledger.bankroll = self.total_bankroll
                    else:
                        # Dedicated ledger: safe to set per-pod allocation.
                        rm.ledger.bankroll = alloc.allocated_usd
                # Expose the computed max_position_usd so pods can enforce it
                rm.max_position_usd = alloc.max_position_usd

        return dict(self._allocations)

    def post_cycle(self, report) -> None:
        """Update performance tracking after a scan cycle.

        Called by PodRunner after each cycle with the CycleReport.
        """
        for result in report.results:
            pid = result.pod_id
            if pid not in self._performance:
                self._performance[pid] = PodPerformance(pod_id=pid)

            perf = self._performance[pid]
            if result.skipped:
                continue

            perf.total_placed += 1
            perf.last_updated = self._now_fn().isoformat()

            # Track success/failure (simplified: success=win for now;
            # real P&L comes from settler after event resolution)
            if result.success and not result.error:
                # Note: This is order placement success, not trade outcome.
                # Real win/loss tracking requires settler integration.
                pass

    def record_settlement(
        self,
        pod_id: str,
        pnl: float,
        fingerprint: Optional[str] = None,
    ) -> None:
        """Record a settled trade result (called by settler).

        This is where actual win/loss/P&L tracking happens.

        Parameters
        ----------
        pod_id : str
            Which pod the settlement belongs to.
        pnl : float
            Profit/loss in USD.
        fingerprint : str, optional
            Unique trade fingerprint. When provided, prevents double-counting
            settlements that were already loaded during bootstrap.
        """
        # Dedup: skip if this fingerprint was already counted
        if fingerprint:
            if fingerprint in self._settled_fps:
                return
            self._settled_fps.add(fingerprint)

        if pod_id not in self._performance:
            self._performance[pod_id] = PodPerformance(pod_id=pod_id)

        perf = self._performance[pod_id]
        perf.total_pnl += pnl

        if pnl > 0:
            perf.total_wins += 1
            perf.consecutive_losses = 0
        elif pnl < 0:
            perf.total_losses += 1
            perf.consecutive_losses += 1

        # Update peak and drawdown
        if perf.total_pnl > perf.peak_pnl:
            perf.peak_pnl = perf.total_pnl

        if perf.peak_pnl > 0:
            current_dd = (perf.peak_pnl - perf.total_pnl) / perf.peak_pnl
            perf.max_drawdown = max(perf.max_drawdown, current_dd)

        perf.last_updated = self._now_fn().isoformat()

    def bootstrap_from_store(self, trade_store) -> None:
        """Populate performance counters from a TradeStore (preferred path).

        Uses pre-indexed in-memory data — no disk I/O.

        Args:
            trade_store: A loaded TradeStore instance.
        """
        try:
            placed_entries = trade_store.get_placed_entries()
            settlements = trade_store.get_settlements_for_bootstrap()

            # Count placed per pod and gather pod names
            placed_counts: Dict[str, int] = {}
            pod_names: Dict[str, str] = {}
            for entry in placed_entries:
                pid = entry.get("pod_id", "P-001")
                placed_counts[pid] = placed_counts.get(pid, 0) + 1
                if not pod_names.get(pid):
                    pod_names[pid] = entry.get("pod_name", pid)

            # Deduplicate settlements by fingerprint, tally wins/losses
            settled: Dict[str, Dict] = {}
            for entry in settlements:
                fp = entry.get("fingerprint", "")
                outcome = (entry.get("outcome") or "").upper()
                pnl = float(entry.get("pnl_usd", 0) or entry.get("pnl", 0) or 0)
                pid = entry.get("pod_id", "P-001")
                if fp and outcome in ("WIN", "LOSS"):
                    settled[fp] = {"pod_id": pid, "pnl": pnl, "outcome": outcome}

            # Record bootstrapped fingerprints so record_settlement() won't
            # double-count them if the settler re-discovers the same outcomes.
            self._settled_fps = set(settled.keys())

            win_counts: Dict[str, int] = {}
            loss_counts: Dict[str, int] = {}
            pnl_sums: Dict[str, float] = {}
            for info in settled.values():
                pid = info["pod_id"]
                if info["outcome"] == "WIN":
                    win_counts[pid] = win_counts.get(pid, 0) + 1
                elif info["outcome"] == "LOSS":
                    loss_counts[pid] = loss_counts.get(pid, 0) + 1
                pnl_sums[pid] = pnl_sums.get(pid, 0) + info["pnl"]

            all_pids = set(placed_counts) | set(win_counts) | set(loss_counts)
            for pid in all_pids:
                if pid not in self._performance:
                    self._performance[pid] = PodPerformance(
                        pod_id=pid,
                        pod_name=pod_names.get(pid, pid),
                    )
                perf = self._performance[pid]
                perf.total_placed = placed_counts.get(pid, 0)
                perf.total_wins = win_counts.get(pid, 0)
                perf.total_losses = loss_counts.get(pid, 0)
                perf.total_pnl = pnl_sums.get(pid, 0)
                if not perf.pod_name or perf.pod_name == pid:
                    perf.pod_name = pod_names.get(pid, pid)

            if all_pids:
                logger.info(
                    "capital_allocator: bootstrapped performance for %d pod(s) "
                    "from store (%d total placed, %d settlements)",
                    len(all_pids),
                    sum(placed_counts.values()),
                    len(settled),
                )
        except Exception as exc:
            logger.warning("capital_allocator: bootstrap_from_store failed: %s", exc)

    def bootstrap_from_trade_log(self, log_path) -> None:
        """Populate performance counters from trade log history.

        Reads all PLACED, WIN, LOSS entries from the JSONL trade log AND
        from settlements.jsonl (written by SettlementBridge) so that
        performance metrics survive server restarts.  Settlements are
        deduplicated by fingerprint.

        Note: Prefer bootstrap_from_store() when a TradeStore is available.
        """
        import json as _json
        from pathlib import Path

        log_path = Path(log_path)
        if not log_path.exists():
            return

        try:
            placed_counts: Dict[str, int] = {}
            pod_names: Dict[str, str] = {}
            # Deduplicate settlements by fingerprint
            settled: Dict[str, Dict] = {}  # fp → {pod_id, pnl, outcome}

            with open(log_path, "r") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = _json.loads(raw)
                    except _json.JSONDecodeError:
                        continue

                    pid = rec.get("pod_id", "P-001")
                    action = (rec.get("action") or "").upper()
                    outcome = (rec.get("outcome") or "").upper()

                    if not pod_names.get(pid):
                        pod_names[pid] = rec.get("pod_name", pid)

                    if action == "PLACED":
                        placed_counts[pid] = placed_counts.get(pid, 0) + 1
                    elif outcome in ("WIN", "LOSS"):
                        fp = rec.get("fingerprint") or rec.get("market_ticker") or ""
                        pnl = float(rec.get("pnl_usd", 0) or rec.get("pnl", 0) or 0)
                        if fp:
                            settled[fp] = {"pod_id": pid, "pnl": pnl, "outcome": outcome}

            # Also read from settlements.jsonl (SettlementBridge output)
            settlement_log = log_path.parent / "settlements.jsonl"
            if settlement_log.exists():
                with open(settlement_log, "r") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            rec = _json.loads(raw)
                        except _json.JSONDecodeError:
                            continue
                        fp = rec.get("fingerprint", "")
                        outcome = rec.get("outcome", "").upper()
                        pnl = float(rec.get("pnl", 0))
                        pid = rec.get("pod_id", "P-001")
                        if fp and outcome in ("WIN", "LOSS"):
                            settled[fp] = {"pod_id": pid, "pnl": pnl, "outcome": outcome}

            # Record bootstrapped fingerprints so record_settlement() won't
            # double-count them if the settler re-discovers the same outcomes.
            self._settled_fps = set(settled.keys())

            # Tally wins/losses per pod from deduplicated settlements
            win_counts: Dict[str, int] = {}
            loss_counts: Dict[str, int] = {}
            pnl_sums: Dict[str, float] = {}
            for info in settled.values():
                pid = info["pod_id"]
                if info["outcome"] == "WIN":
                    win_counts[pid] = win_counts.get(pid, 0) + 1
                elif info["outcome"] == "LOSS":
                    loss_counts[pid] = loss_counts.get(pid, 0) + 1
                pnl_sums[pid] = pnl_sums.get(pid, 0) + info["pnl"]

            all_pids = set(placed_counts) | set(win_counts) | set(loss_counts)
            for pid in all_pids:
                if pid not in self._performance:
                    self._performance[pid] = PodPerformance(
                        pod_id=pid,
                        pod_name=pod_names.get(pid, pid),
                    )
                perf = self._performance[pid]
                perf.total_placed = placed_counts.get(pid, 0)
                perf.total_wins = win_counts.get(pid, 0)
                perf.total_losses = loss_counts.get(pid, 0)
                perf.total_pnl = pnl_sums.get(pid, 0)
                if not perf.pod_name or perf.pod_name == pid:
                    perf.pod_name = pod_names.get(pid, pid)

            if all_pids:
                logger.info(
                    "capital_allocator: bootstrapped performance for %d pod(s) "
                    "from trade log (%d total placed, %d settlements)",
                    len(all_pids),
                    sum(placed_counts.values()),
                    len(settled),
                )
        except Exception as exc:
            logger.warning("capital_allocator: bootstrap_from_trade_log failed: %s", exc)

    # ── Allocation strategies ───────────────────────────────────────

    def _rebalance(self, pod_ids: List[str]) -> None:
        """Recompute allocations using the configured strategy."""
        if not pod_ids:
            return

        if self.strategy == "equal":
            weights = self._equal_weights(pod_ids)
        elif self.strategy == "weighted":
            weights = self._configured_weights(pod_ids)
        elif self.strategy == "performance":
            weights = self._performance_weights(pod_ids)
        else:
            weights = self._equal_weights(pod_ids)

        # Normalize and clamp
        total_w = sum(weights.values())
        if total_w <= 0:
            weights = self._equal_weights(pod_ids)
            total_w = sum(weights.values())

        for pid in pod_ids:
            pct = weights.get(pid, 0) / total_w
            pct = max(self.min_pod_pct, min(self.max_pod_pct, pct))

            alloc_usd = pct * self.total_bankroll
            self._allocations[pid] = PodAllocation(
                pod_id=pid,
                allocation_pct=pct,
                allocated_usd=alloc_usd,
                max_position_usd=alloc_usd * self.max_bet_pct,
            )

        logger.info(
            "capital_allocator: rebalanced %d pods, strategy=%s",
            len(pod_ids), self.strategy,
        )

    def _equal_weights(self, pod_ids: List[str]) -> Dict[str, float]:
        return {pid: 1.0 for pid in pod_ids}

    def _configured_weights(self, pod_ids: List[str]) -> Dict[str, float]:
        return {
            pid: self._pod_weights.get(pid, 1.0)
            for pid in pod_ids
        }

    def _performance_weights(self, pod_ids: List[str]) -> Dict[str, float]:
        """Weight pods by historical performance (Sharpe-like)."""
        weights = {}
        for pid in pod_ids:
            perf = self._performance.get(pid)
            if perf is None or perf.total_placed < 5:
                weights[pid] = 1.0  # New pods get base weight
            else:
                # Simple score: win_rate * (1 + avg_pnl) * (1 - max_drawdown)
                score = (
                    max(0.1, perf.win_rate) *
                    max(0.1, 1.0 + perf.avg_pnl) *
                    max(0.1, 1.0 - perf.max_drawdown)
                )
                weights[pid] = score
        return weights

    # ── Accessors ───────────────────────────────────────────────────

    def get_allocation(self, pod_id: str) -> Optional[PodAllocation]:
        return self._allocations.get(pod_id)

    def get_performance(self, pod_id: str) -> Optional[PodPerformance]:
        return self._performance.get(pod_id)

    @property
    def allocations(self) -> Dict[str, PodAllocation]:
        return dict(self._allocations)

    @property
    def performances(self) -> Dict[str, PodPerformance]:
        return dict(self._performance)

    @classmethod
    def from_config(cls, config: dict) -> "CapitalAllocator":
        """Build from config dict."""
        alloc_cfg = config.get("capital_allocator", {})
        risk_cfg = config.get("risk", {})

        pod_weights = {}
        for pod_id, pod_cfg in config.get("pods", {}).items():
            if isinstance(pod_cfg, dict) and "weight" in pod_cfg:
                pod_weights[pod_id] = pod_cfg["weight"]

        return cls(
            total_bankroll=risk_cfg.get("initial_bankroll", DEFAULT_BANKROLL),
            strategy=alloc_cfg.get("strategy", "equal"),
            pod_weights=pod_weights,
            max_pod_pct=alloc_cfg.get("max_pod_pct", 0.40),
            min_pod_pct=alloc_cfg.get("min_pod_pct", 0.05),
            drawdown_throttle_pct=alloc_cfg.get("drawdown_throttle_pct", 0.10),
            rebalance_interval=alloc_cfg.get("rebalance_interval", 10),
            max_bet_pct=risk_cfg.get("max_bet_pct", 0.06),
        )
