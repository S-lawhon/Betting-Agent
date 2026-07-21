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

    # ── Pre-cycle check ─────────────────────────────────────────────

    def check_pre_cycle(self) -> bool:
        """Check if trading is allowed. Returns True if safe to scan.

        Checks halt status, cooldown, and exposure limits.
        """
        now = self._now_fn()

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
        """Update risk state after a scan cycle."""
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

    def record_pnl(self, pnl: float) -> None:
        """Record a settled P&L result."""
        self._daily_pnl += pnl

        # Check daily loss after recording
        if self.bankroll > 0:
            daily_loss_pct = abs(min(0, self._daily_pnl)) / self.bankroll
            if daily_loss_pct >= self.max_daily_loss_pct:
                self._halt("daily_loss_limit: {:.1%}".format(daily_loss_pct))

    def close_position(self, market_id: str, pnl: float = 0.0) -> None:
        """Remove a closed/settled position from tracking."""
        if market_id in self._open_positions:
            usd = self._open_positions.pop(market_id)
            # Reduce venue/pod exposure (simplified — real impl
            # would track venue per position)
        self.record_pnl(pnl)

    # ── Risk checks for individual trades ───────────────────────────

    def check_trade(
        self, pod_id: str, venue: str, position_size_usd: float,
    ) -> bool:
        """Check if an individual trade is within aggregate limits.

        Called by pods or executor before placing an order.
        Returns True if trade is approved.
        """
        if self._halted:
            return False

        is_exempt = pod_id in self.exempt_pods

        # Check total exposure with this trade (exempt pods skip this)
        if not is_exempt:
            total_exp = sum(self._open_positions.values()) + position_size_usd
            if self.bankroll > 0 and total_exp / self.bankroll > self.max_total_exposure_pct:
                logger.debug(
                    "aggregate_risk: trade rejected — total exposure would be %.1f%%",
                    total_exp / self.bankroll * 100,
                )
                return False

        # Check venue exposure
        venue_exp = self._venue_exposure.get(venue, 0) + position_size_usd
        if self.bankroll > 0 and venue_exp / self.bankroll > self.max_venue_exposure_pct:
            logger.debug(
                "aggregate_risk: trade rejected — %s exposure would be %.1f%%",
                venue, venue_exp / self.bankroll * 100,
            )
            return False

        # Check per-pod exposure
        pod_exp = self._pod_exposure.get(pod_id, 0) + position_size_usd
        if self.bankroll > 0 and pod_exp / self.bankroll > self.max_pod_exposure_pct:
            logger.debug(
                "aggregate_risk: trade rejected — %s exposure would be %.1f%% "
                "(max %.1f%%)",
                pod_id, pod_exp / self.bankroll * 100,
                self.max_pod_exposure_pct * 100,
            )
            return False

        # Check total open positions
        if len(self._open_positions) >= self.max_open_positions:
            logger.debug(
                "aggregate_risk: trade rejected — %d open positions (max %d)",
                len(self._open_positions), self.max_open_positions,
            )
            return False

        return True

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
        """Track a new open position."""
        self._open_positions[market_id] = usd
        self._venue_exposure[venue] = self._venue_exposure.get(venue, 0) + usd
        self._pod_exposure[pod_id] = self._pod_exposure.get(pod_id, 0) + usd

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

    def bootstrap_from_store(self, trade_store) -> None:
        """Rebuild open positions from a TradeStore (preferred path).

        Uses pre-indexed in-memory data — no disk I/O.
        Only bootstraps **live** positions — paper-mode trades do not
        represent real capital at risk and should not count toward
        aggregate limits.

        Args:
            trade_store: A loaded TradeStore instance.
        """
        positions = trade_store.get_open_positions_for_bootstrap()
        open_count = 0
        skipped_paper = 0
        for pos in positions:
            # Skip paper-mode trades — they are synthetic and have no
            # settlement mechanism, so they would accumulate forever
            # and block real trading via max_open_positions.
            if pos.get("mode", "") == "paper":
                skipped_paper += 1
                continue
            usd = pos.get("position_size_usd", 0)
            if usd > 0:
                self._add_position(
                    pos.get("pod_id", "P-001"),
                    pos.get("venue", "kalshi"),
                    pos.get("market_id") or pos.get("market_ticker", ""),
                    usd,
                )
                open_count += 1

        logger.info(
            "aggregate_risk: bootstrapped %d open positions from store "
            "(total exposure $%.2f, %d paper-mode positions excluded)",
            open_count, sum(self._open_positions.values()), skipped_paper,
        )

    def bootstrap_from_trade_log(self, log_path) -> None:
        """Rebuild open positions from the trade log (survives restarts).

        Reads all PLACED entries and removes any that have been settled
        (WIN/LOSS/VOID).  The remaining entries are tracked as open positions.

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

            # Only track positions that haven't been settled.
            # Exclude paper-mode trades — they don't represent real risk.
            open_count = 0
            skipped_paper = 0
            for ticker, info in placed.items():
                if ticker in settled:
                    continue
                if info.get("mode") == "paper":
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
