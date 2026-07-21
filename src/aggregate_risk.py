"""
src/aggregate_risk.py
─────────────────────
Cross-pod aggregate risk controls.

Sits above individual pod risk managers and enforces portfolio-level
constraints:
  - Total exposure across all pods
  - Daily P&L drawdown halt
  - Per-venue exposure caps
  - Correlation-aware concentration limits
  - Emergency kill switch

The AggregateRiskGuard wraps around PodRunner: it can veto scan
cycles, throttle individual pods, or halt all trading.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from src.constants import DEFAULT_BANKROLL

logger = logging.getLogger(__name__)


@dataclass
class RiskSnapshot:
    """Point-in-time snapshot of aggregate risk metrics."""
    timestamp_utc: str
    total_exposure_usd: float
    total_exposure_pct: float   # as fraction of bankroll
    venue_exposure: Dict[str, float]   # venue → USD exposed
    pod_exposure: Dict[str, float]     # pod_id → USD exposed
    daily_pnl: float
    daily_pnl_pct: float       # as fraction of bankroll
    open_positions: int
    halted: bool
    halt_reason: Optional[str] = None


class AggregateRiskGuard:
    """Portfolio-level risk controls across all pods.

    Enforces:
      1. max_total_exposure_pct: Total USD at risk / bankroll cap
      2. max_daily_loss_pct: Daily drawdown halt threshold
      3. max_venue_exposure_pct: Per-venue concentration cap
      4. max_open_positions: Total open positions across all pods
      5. cooldown_minutes: Lockout after daily loss halt

    Usage:
        guard = AggregateRiskGuard(bankroll=10000)
        if guard.check_pre_cycle():
            report = runner.run_once()
            guard.update_post_cycle(report)
    """

    def __init__(
        self,
        bankroll: float = DEFAULT_BANKROLL,
        max_total_exposure_pct: float = 0.50,
        max_daily_loss_pct: float = 0.05,
        max_venue_exposure_pct: float = 0.30,
        max_pod_exposure_pct: float = 0.25,
        max_open_positions: int = 20,
        cooldown_minutes: float = 60.0,
        exempt_pods: Optional[List[str]] = None,
        _now_fn: Optional[Callable] = None,
    ):
        self.bankroll = bankroll
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_venue_exposure_pct = max_venue_exposure_pct
        self.max_pod_exposure_pct = max_pod_exposure_pct
        self.max_open_positions = max_open_positions
        self.cooldown_minutes = cooldown_minutes
        self.exempt_pods: set = set(exempt_pods or [])
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))

        # State
        self._halted = False
        self._halt_reason: Optional[str] = None
        self._halt_time: Optional[datetime] = None
        self._daily_pnl = 0.0
        self._daily_date: Optional[str] = None
        self._open_positions: Dict[str, float] = {}  # market_id → usd
        self._venue_exposure: Dict[str, float] = {}  # venue → usd
        self._pod_exposure: Dict[str, float] = {}    # pod_id → usd
        # market_id → (pod_id, venue), so close_position can unwind the
        # venue/pod buckets it credited in _add_position.
        self._position_meta: Dict[str, tuple] = {}

        # ── Intra-cycle reservations ────────────────────────────────
        # Positions are only registered in post_cycle, so without this a
        # pod that places its whole book in one scan has every trade
        # checked against last cycle's (often zero) exposure.  A trade
        # approved by reserve_trade() holds capacity here until
        # update_post_cycle() either converts it to a real position or
        # sweeps it away.
        self._reservations: Dict[str, Dict] = {}  # market_id → {pod,venue,usd}

    # ── Pre-cycle check ─────────────────────────────────────────────

    def check_pre_cycle(self) -> bool:
        """Check if trading is allowed. Returns True if safe to scan.

        Checks halt status, cooldown, and exposure limits.
        """
        now = self._now_fn()

        # Backstop: reservations belong to one cycle.  update_post_cycle
        # normally clears them, but a cycle that dies before its callback
        # runs must not carry stale holds into the next scan.
        leaked = self.clear_reservations()
        if leaked:
            logger.warning(
                "aggregate_risk: cleared %d stale reservation(s) from a "
                "cycle that never reported", leaked,
            )

        # Reset daily P&L at midnight
        today = now.strftime("%Y-%m-%d")
        if self._daily_date != today:
            self._daily_pnl = 0.0
            self._daily_date = today

        # Check cooldown expiry
        if self._halted and self._halt_time:
            elapsed = (now - self._halt_time).total_seconds() / 60.0
            if elapsed >= self.cooldown_minutes:
                self._halted = False
                self._halt_reason = None
                self._halt_time = None
                logger.info("aggregate_risk: cooldown expired, trading resumed")

        if self._halted:
            logger.warning(
                "aggregate_risk: trading halted — %s",
                self._halt_reason or "unknown reason",
            )
            return False

        # Check daily loss
        if self.bankroll > 0:
            daily_loss_pct = abs(min(0, self._daily_pnl)) / self.bankroll
            if daily_loss_pct >= self.max_daily_loss_pct:
                self._halt("daily_loss_limit: {:.1%}".format(daily_loss_pct))
                return False

        # Check total exposure
        total_exp = sum(self._open_positions.values())
        if self.bankroll > 0 and total_exp / self.bankroll > self.max_total_exposure_pct:
            logger.warning(
                "aggregate_risk: total exposure %.1f%% exceeds limit %.1f%%",
                total_exp / self.bankroll * 100,
                self.max_total_exposure_pct * 100,
            )
            # Don't halt — just warn (individual trades still get risk-checked)

        return True

    # ── Post-cycle update ───────────────────────────────────────────

    def update_post_cycle(self, report) -> None:
        """Update risk state after a scan cycle.

        Converts this cycle's reservations into real positions using the
        ACTUAL sizes from the report, then releases every reservation that
        did not become a position (rejected, errored, or dropped by the
        pod after approval — e.g. P-017 drops positions under $1 once
        depth caps apply).  Reservations therefore cannot leak across
        cycles and starve a pod.
        """
        for result in report.results:
            if result.skipped:
                continue

            if result.success and not result.error:
                # Track new position using actual USD at risk
                usd = getattr(result, "position_size_usd", 0.0) or 0.0
                self._add_position(
                    result.pod_id, result.venue, result.market_id,
                    usd,
                )

        # Anything still reserved never became a position — release it.
        leaked = self.clear_reservations()
        if leaked:
            logger.debug(
                "aggregate_risk: released %d unconverted reservation(s)", leaked,
            )

    def record_pnl(self, pnl: float) -> None:
        """Record a settled P&L result."""
        self._daily_pnl += pnl

        # Check daily loss after recording
        if self.bankroll > 0:
            daily_loss_pct = abs(min(0, self._daily_pnl)) / self.bankroll
            if daily_loss_pct >= self.max_daily_loss_pct:
                self._halt("daily_loss_limit: {:.1%}".format(daily_loss_pct))

    def close_position(self, market_id: str, pnl: float = 0.0) -> None:
        """Remove a closed/settled position from tracking.

        Credits the venue and pod exposure buckets back.  They used to
        only ever go up, which meant a pod's exposure ratcheted toward
        max_pod_exposure_pct and stuck there even as its positions
        settled — silently muting the pod.
        """
        self._remove_position(market_id)
        self._reservations.pop(market_id, None)
        self.record_pnl(pnl)

    # ── Risk checks for individual trades ───────────────────────────

    def _reserved(
        self,
        pod_id: Optional[str] = None,
        venue: Optional[str] = None,
        exclude: Optional[str] = None,
    ) -> float:
        """Sum of live reservations, optionally filtered by pod or venue.

        ``exclude`` drops one market_id, so re-checking a trade that
        already holds a reservation does not count it against itself.
        """
        total = 0.0
        for market_id, res in self._reservations.items():
            if market_id == exclude:
                continue
            if pod_id is not None and res["pod_id"] != pod_id:
                continue
            if venue is not None and res["venue"] != venue:
                continue
            total += res["usd"]
        return total

    def _position_count(self, exclude: Optional[str] = None) -> int:
        """Open positions plus reservations that aren't already positions."""
        pending = sum(
            1 for m in self._reservations
            if m != exclude and m not in self._open_positions
        )
        return len(self._open_positions) + pending

    def check_trade(
        self, pod_id: str, venue: str, position_size_usd: float,
        market_id: Optional[str] = None,
    ) -> bool:
        """Check if an individual trade is within aggregate limits.

        Called by pods or executor before placing an order.
        Returns True if trade is approved.

        Exposure includes reservations held by trades approved earlier in
        this cycle (see ``reserve_trade``), so a pod placing its whole
        book in a single scan is capped at the same limits as one
        trickling positions out across cycles.  This is a read-only
        check — call ``reserve_trade`` to hold the capacity.

        Args:
            market_id: If this trade already holds a reservation, pass its
                market_id so the reservation isn't counted against itself.
        """
        if self._halted:
            return False

        is_exempt = pod_id in self.exempt_pods

        # Check total exposure with this trade (exempt pods skip this)
        if not is_exempt:
            total_exp = (
                sum(self._open_positions.values())
                + self._reserved(exclude=market_id)
                + position_size_usd
            )
            if self.bankroll > 0 and total_exp / self.bankroll > self.max_total_exposure_pct:
                logger.debug(
                    "aggregate_risk: trade rejected — total exposure would be %.1f%%",
                    total_exp / self.bankroll * 100,
                )
                return False

        # Check venue exposure
        venue_exp = (
            self._venue_exposure.get(venue, 0)
            + self._reserved(venue=venue, exclude=market_id)
            + position_size_usd
        )
        if self.bankroll > 0 and venue_exp / self.bankroll > self.max_venue_exposure_pct:
            logger.debug(
                "aggregate_risk: trade rejected — %s exposure would be %.1f%%",
                venue, venue_exp / self.bankroll * 100,
            )
            return False

        # Check per-pod exposure
        pod_exp = (
            self._pod_exposure.get(pod_id, 0)
            + self._reserved(pod_id=pod_id, exclude=market_id)
            + position_size_usd
        )
        if self.bankroll > 0 and pod_exp / self.bankroll > self.max_pod_exposure_pct:
            logger.debug(
                "aggregate_risk: trade rejected — %s exposure would be %.1f%% "
                "(max %.1f%%)",
                pod_id, pod_exp / self.bankroll * 100,
                self.max_pod_exposure_pct * 100,
            )
            return False

        # Check total open positions
        count = self._position_count(exclude=market_id)
        if count >= self.max_open_positions:
            logger.debug(
                "aggregate_risk: trade rejected — %d open positions (max %d)",
                count, self.max_open_positions,
            )
            return False

        return True

    # ── Intra-cycle reservations ────────────────────────────────────

    def reserve_trade(
        self, pod_id: str, venue: str, market_id: str,
        position_size_usd: float,
    ) -> bool:
        """Check a trade and, if approved, hold its exposure for this cycle.

        This is the call site that actually enforces limits within a
        single scan: the reservation makes the next ``check_trade`` in the
        same cycle see this trade's exposure, instead of waiting for
        ``update_post_cycle``.

        Reservations are keyed by market_id and are idempotent — reserving
        the same market twice (e.g. pod pre-check then executor) replaces
        rather than compounds.

        A reservation that never becomes a position is released by
        ``update_post_cycle``, so callers do not have to unwind approvals
        they later abandon.

        Returns:
            True if the trade is approved and reserved.
        """
        if not market_id:
            # Nothing to key a reservation on — fall back to a plain check.
            return self.check_trade(pod_id, venue, position_size_usd)

        if not self.check_trade(pod_id, venue, position_size_usd, market_id=market_id):
            return False

        self._reservations[market_id] = {
            "pod_id": pod_id, "venue": venue, "usd": position_size_usd,
        }
        return True

    def release_reservation(self, market_id: str) -> None:
        """Drop a single reservation (trade abandoned before placement)."""
        self._reservations.pop(market_id, None)

    def clear_reservations(self) -> int:
        """Drop all live reservations. Returns how many were released."""
        n = len(self._reservations)
        self._reservations.clear()
        return n

    # ── Internal ────────────────────────────────────────────────────

    def _halt(self, reason: str) -> None:
        """Halt all trading."""
        self._halted = True
        self._halt_reason = reason
        self._halt_time = self._now_fn()
        logger.warning("aggregate_risk: HALTED — %s", reason)

    def _add_position(
        self, pod_id: str, venue: str, market_id: str, usd: float,
    ) -> None:
        """Track a new open position.

        Idempotent by market_id: re-adding a market replaces its
        contribution rather than double-counting the venue/pod buckets
        (``_open_positions`` was already a dict assignment, but the
        buckets used ``+=``).  Consumes any reservation on the market.
        """
        # Unwind a previous registration of this same market first.
        if market_id in self._position_meta:
            self._remove_position(market_id)

        self._open_positions[market_id] = usd
        self._venue_exposure[venue] = self._venue_exposure.get(venue, 0) + usd
        self._pod_exposure[pod_id] = self._pod_exposure.get(pod_id, 0) + usd
        self._position_meta[market_id] = (pod_id, venue)
        # The position is now tracked for real — the reservation is spent.
        self._reservations.pop(market_id, None)

    def _remove_position(self, market_id: str) -> None:
        """Untrack a position and credit back its venue/pod exposure."""
        usd = self._open_positions.pop(market_id, 0.0)
        meta = self._position_meta.pop(market_id, None)
        if meta is None:
            return
        pod_id, venue = meta
        self._venue_exposure[venue] = max(
            0.0, self._venue_exposure.get(venue, 0.0) - usd
        )
        self._pod_exposure[pod_id] = max(
            0.0, self._pod_exposure.get(pod_id, 0.0) - usd
        )

    def resume(self) -> None:
        """Manually resume trading after a halt."""
        self._halted = False
        self._halt_reason = None
        self._halt_time = None
        logger.info("aggregate_risk: manually resumed")

    def emergency_halt(self, reason: str = "manual") -> None:
        """Emergency halt — ignores cooldown, requires manual resume()."""
        self._halt(reason)
        self.cooldown_minutes = float("inf")  # Requires manual resume

    # ── Snapshot ────────────────────────────────────────────────────

    def snapshot(self) -> RiskSnapshot:
        """Take a point-in-time snapshot of risk metrics."""
        total_exp = sum(self._open_positions.values())
        return RiskSnapshot(
            timestamp_utc=self._now_fn().isoformat(),
            total_exposure_usd=total_exp,
            total_exposure_pct=total_exp / self.bankroll if self.bankroll > 0 else 0,
            venue_exposure=dict(self._venue_exposure),
            pod_exposure=dict(self._pod_exposure),
            daily_pnl=self._daily_pnl,
            daily_pnl_pct=self._daily_pnl / self.bankroll if self.bankroll > 0 else 0,
            open_positions=len(self._open_positions),
            halted=self._halted,
            halt_reason=self._halt_reason,
        )

    @property
    def is_halted(self) -> bool:
        return self._halted

    def _skip_paper(self, pod_id: str, settled_pod_ids: Optional[set]) -> bool:
        """Whether a paper position from this pod should be left untracked.

        Paper trades used to be skipped wholesale, because a pod with no
        settler never closes its positions: they would pile up forever and
        eventually block trading via max_open_positions.  That reasoning
        only holds for pods that cannot settle.  P-006, P-015 and P-017
        all have settlers now, and the engine calls close_position for
        each settlement, so their paper positions DO drain — and since the
        whole engine runs mode: paper, excluding them meant the guard
        started every process believing it had zero exposure.

        So: skip a paper position only when its pod has no registered
        settler.  ``settled_pod_ids=None`` keeps the old skip-everything
        behaviour, for callers that can't say which pods settle.
        """
        if settled_pod_ids is None:
            return True
        return pod_id not in settled_pod_ids

    def bootstrap_from_store(
        self, trade_store, settled_pod_ids: Optional[set] = None,
    ) -> None:
        """Rebuild open positions from a TradeStore (preferred path).

        Uses pre-indexed in-memory data — no disk I/O.

        Args:
            trade_store: A loaded TradeStore instance.
            settled_pod_ids: Pod IDs that have a registered settler. Paper
                positions from these pods ARE tracked (they will settle
                and be closed); paper positions from any other pod are
                skipped.  None (default) skips all paper positions.
        """
        positions = trade_store.get_open_positions_for_bootstrap()
        open_count = 0
        skipped_paper = 0
        for pos in positions:
            pod_id = pos.get("pod_id", "P-001")
            if pos.get("mode", "") == "paper" and self._skip_paper(
                pod_id, settled_pod_ids
            ):
                skipped_paper += 1
                continue
            usd = pos.get("position_size_usd", 0)
            if usd > 0:
                self._add_position(
                    pod_id,
                    pos.get("venue", "kalshi"),
                    pos.get("market_id") or pos.get("market_ticker", ""),
                    usd,
                )
                open_count += 1

        logger.info(
            "aggregate_risk: bootstrapped %d open positions from store "
            "(total exposure $%.2f, %d paper-mode positions excluded"
            " — settling pods: %s)",
            open_count, sum(self._open_positions.values()), skipped_paper,
            ",".join(sorted(settled_pod_ids)) if settled_pod_ids else "none",
        )

    def bootstrap_from_trade_log(
        self, log_path, settled_pod_ids: Optional[set] = None,
    ) -> None:
        """Rebuild open positions from the trade log (survives restarts).

        Reads all PLACED entries and removes any that have been settled
        (WIN/LOSS/VOID).  The remaining entries are tracked as open positions.

        ``settled_pod_ids`` has the same meaning as in
        ``bootstrap_from_store``.

        Note: Prefer bootstrap_from_store() when a TradeStore is available.
        """
        import json as _json
        from pathlib import Path

        log_path = Path(log_path)
        if not log_path.exists():
            return

        placed = {}   # market_id → {pod_id, venue, usd}
        settled = set()

        try:
            with open(log_path, "r") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = _json.loads(raw)
                    except _json.JSONDecodeError:
                        continue

                    action = (rec.get("action") or rec.get("outcome") or "").upper()
                    ticker = rec.get("market_ticker") or rec.get("market_id") or ""

                    if action == "PLACED" and ticker:
                        placed[ticker] = {
                            "pod_id": rec.get("pod_id", "P-001"),
                            "venue": rec.get("venue", "kalshi"),
                            "usd": float(rec.get("position_size_usd", 0)),
                            "mode": rec.get("mode", ""),
                        }
                    elif action in ("WIN", "LOSS", "VOID") and ticker:
                        settled.add(ticker)

            # Only track positions that haven't been settled.  Paper
            # trades count only for pods that can actually settle them.
            open_count = 0
            skipped_paper = 0
            for ticker, info in placed.items():
                if ticker in settled:
                    continue
                if info.get("mode") == "paper" and self._skip_paper(
                    info["pod_id"], settled_pod_ids
                ):
                    skipped_paper += 1
                    continue
                if info["usd"] > 0:
                    self._add_position(
                        info["pod_id"], info["venue"], ticker, info["usd"],
                    )
                    open_count += 1

            logger.info(
                "aggregate_risk: bootstrapped %d open positions from trade log "
                "(total exposure $%.2f, %d paper-mode excluded)",
                open_count, sum(self._open_positions.values()), skipped_paper,
            )
        except Exception as exc:
            logger.warning("aggregate_risk: bootstrap_from_trade_log failed: %s", exc)

    @classmethod
    def from_config(cls, config: dict) -> "AggregateRiskGuard":
        """Build from config dict."""
        risk_cfg = config.get("risk", {})
        agg_cfg = config.get("aggregate_risk", {})
        return cls(
            bankroll=risk_cfg.get("initial_bankroll", DEFAULT_BANKROLL),
            max_total_exposure_pct=agg_cfg.get("max_total_exposure_pct", 0.50),
            max_daily_loss_pct=agg_cfg.get("max_daily_loss_pct", 0.05),
            max_venue_exposure_pct=agg_cfg.get("max_venue_exposure_pct", 0.30),
            max_pod_exposure_pct=agg_cfg.get("max_pod_exposure_pct", 0.25),
            max_open_positions=agg_cfg.get("max_open_positions", 20),
            cooldown_minutes=agg_cfg.get("cooldown_minutes", 60.0),
            exempt_pods=agg_cfg.get("exempt_pods", []),
        )
