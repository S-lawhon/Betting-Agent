"""
src/pod_runner.py
─────────────────
Unified pod runner: loads config, instantiates all active pods with
their dependencies, and runs scan cycles via MultiExecutor.

This is the "main loop" of the Betting Pod Shop.  It:
  1. Reads config_multi_pod.yaml
  2. Builds venue clients (Kalshi, Polymarket, ForecastEx)
  3. Builds shared infrastructure (EdgeCalculator, RiskManager, etc.)
  4. Instantiates each active pod via its from_config()
  5. Runs MultiExecutor.run_cycle() on all pods
  6. Collects cycle results and feeds them to the capital allocator

Designed for both single-shot (`run_once`) and scheduled (`run_loop`)
operation.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.multi_executor import MultiExecutor, MultiExecutionResult

logger = logging.getLogger(__name__)


@dataclass
class CycleReport:
    """Summary of one scan cycle across all pods."""
    cycle_number: int
    timestamp_utc: str
    duration_seconds: float
    pods_scanned: int
    total_results: int
    placed_count: int
    skipped_count: int
    error_count: int
    results: List[MultiExecutionResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_results == 0:
            return 1.0
        return (self.total_results - self.error_count) / self.total_results


# ── Pod registry ────────────────────────────────────────────────────

from src.pod_registry import discover_pods, get_pod_class

# Trigger auto-discovery on module load so @register_pod decorators fire.
# The old hardcoded registry is preserved in pod_registry.py as a fallback.
discover_pods()

# Backward-compat alias — external code may reference this.
POD_REGISTRY = {
    "P-001": ("src.pods.kalshi_moneyline", "KalshiMoneylinePod"),
    "P-002": ("src.pods.cross_venue_arb", "CrossVenueArbPod"),
    "P-004": ("src.pods.forecastex_kalshi_arb", "ForecastExKalshiArbPod"),
    "P-006": ("src.pods.polymarket_consensus", "PolymarketConsensusPod"),
    "P-009": ("src.pods.signup_bonus_pod", "SignupBonusPod"),
    "P-010": ("src.pods.boost_scanner_pod", "BoostScannerPod"),
    "P-012": ("src.pods.macro_nowcast", "MacroNowcastPod"),
    "P-014": ("src.pods.live_game_pod", "LiveGamePod"),
}


def _import_pod_class(pod_id: str):
    """Look up a pod class by ID using the decorator registry.

    Falls back to legacy import if the decorator registry doesn't have it.
    """
    cls = get_pod_class(pod_id)
    if cls is None:
        raise ValueError("Unknown pod: {}".format(pod_id))
    return cls


# ── PodRunner ───────────────────────────────────────────────────────

class PodRunner:
    """Orchestrates multi-pod scan cycles.

    Usage:
        runner = PodRunner.from_config(config, venue_clients, shared_deps)
        report = runner.run_once()    # single cycle
        runner.run_loop(interval=60)  # continuous scanning
    """

    def __init__(
        self,
        pods: List[BasePod],
        executor: MultiExecutor,
        capital_allocator=None,
        cycle_callback: Optional[Callable] = None,
        _now_fn: Optional[Callable] = None,
    ):
        self.pods = pods
        self.executor = executor
        self.capital_allocator = capital_allocator
        self._cycle_callback = cycle_callback
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))
        self._cycle_count = 0
        self._history: List[CycleReport] = []

    def run_once(self) -> CycleReport:
        """Run one scan cycle across all active pods."""
        self._cycle_count += 1
        start = time.monotonic()

        # Pre-cycle: let capital allocator adjust pod bankrolls
        if self.capital_allocator:
            try:
                self.capital_allocator.pre_cycle(self.pods)
            except Exception as e:
                logger.error(
                    "pod_runner: capital_allocator.pre_cycle failed: %s "
                    "(pods will use previous allocations)", e, exc_info=True
                )

        # Run all pods through the executor
        results = self.executor.run_cycle(self.pods)

        duration = time.monotonic() - start

        # Tally results
        placed = sum(1 for r in results if not r.skipped and r.success and not r.error)
        skipped = sum(1 for r in results if r.skipped)
        errors = sum(1 for r in results if not r.success or r.error)

        report = CycleReport(
            cycle_number=self._cycle_count,
            timestamp_utc=self._now_fn().isoformat(),
            duration_seconds=round(duration, 3),
            pods_scanned=len(self.pods),
            total_results=len(results),
            placed_count=placed,
            skipped_count=skipped,
            error_count=errors,
            results=results,
        )

        self._history.append(report)

        # Post-cycle: update capital allocator with results
        if self.capital_allocator:
            try:
                self.capital_allocator.post_cycle(report)
            except Exception as e:
                logger.error(
                    "pod_runner: capital_allocator.post_cycle failed: %s", e, exc_info=True
                )

        # Fire callback (dashboard update, etc.)
        if self._cycle_callback:
            try:
                self._cycle_callback(report)
            except Exception as e:
                logger.error("pod_runner: cycle_callback failed: %s", e)

        logger.info(
            "pod_runner.cycle_complete: cycle=%d pods=%d placed=%d skipped=%d "
            "errors=%d duration=%.3fs",
            report.cycle_number, report.pods_scanned, report.placed_count,
            report.skipped_count, report.error_count, report.duration_seconds,
        )

        return report

    def run_loop(
        self,
        interval_seconds: float = 60.0,
        max_cycles: Optional[int] = None,
        stop_on_error: bool = False,
    ) -> List[CycleReport]:
        """Run scan cycles in a loop with configurable interval.

        Args:
            interval_seconds: Seconds between cycle starts.
            max_cycles: Stop after this many cycles (None = run forever).
            stop_on_error: If True, stop loop on first cycle with errors.

        Returns:
            List of CycleReports from all cycles run.
        """
        reports = []
        cycle = 0

        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            cycle_start = time.monotonic()

            try:
                report = self.run_once()
                reports.append(report)

                if stop_on_error and report.error_count > 0:
                    logger.warning(
                        "pod_runner: stopping loop after errors in cycle %d",
                        report.cycle_number,
                    )
                    break

            except Exception as e:
                logger.error("pod_runner: cycle %d crashed: %s", cycle, e)
                if stop_on_error:
                    break

            # Sleep for remaining interval
            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0, interval_seconds - elapsed)
            if sleep_time > 0 and (max_cycles is None or cycle < max_cycles):
                time.sleep(sleep_time)

        return reports

    @property
    def history(self) -> List[CycleReport]:
        return list(self._history)

    @property
    def total_placed(self) -> int:
        return sum(r.placed_count for r in self._history)

    @property
    def total_errors(self) -> int:
        return sum(r.error_count for r in self._history)

    # ── Factory ─────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        config: dict,
        venue_clients: Optional[Dict[str, Any]] = None,
        shared_deps: Optional[Dict[str, Any]] = None,
        capital_allocator=None,
        cycle_callback=None,
    ) -> "PodRunner":
        """Build PodRunner from config + pre-built dependencies.

        Args:
            config: Merged config dict (base + multi_pod).
            venue_clients: {"kalshi": ..., "polymarket": ..., "forecastex": ...}
            shared_deps: {"edge_calculator": ..., "risk_manager": ...,
                          "odds_client": ..., "nowcast_client": ..., etc.}
            capital_allocator: Optional CapitalAllocator instance.
            cycle_callback: Optional callback(CycleReport) after each cycle.
        """
        venue_clients = venue_clients or {}
        shared_deps = shared_deps or {}

        # Build executor
        executor = MultiExecutor.from_config(
            config, venue_clients=venue_clients,
            risk_manager=shared_deps.get("risk_manager"),
            aggregate_risk=shared_deps.get("aggregate_risk"),
        )

        # Instantiate active pods
        active_ids = config.get("pods", {}).get("active", [])
        pods = []

        for pod_id in active_ids:
            pod_cfg = config.get("pods", {}).get(pod_id, {})
            if pod_cfg.get("enabled") is False:
                logger.info("pod_runner: skipping disabled pod %s", pod_id)
                continue

            try:
                pod_class = _import_pod_class(pod_id)

                # Build overrides from shared deps + venue clients
                overrides = dict(shared_deps)
                overrides.update(venue_clients)

                # Pod-specific client mapping
                if pod_id == "P-001":
                    overrides["scanner"] = shared_deps.get("scanner")
                elif pod_id == "P-002":
                    overrides["kalshi_client"] = venue_clients.get("kalshi")
                    overrides["polymarket_client"] = venue_clients.get("polymarket")
                    overrides["cross_venue_matcher"] = shared_deps.get("cross_venue_matcher")
                    # Route P-002 to the shared trade log so dual-leg trades
                    # appear on the dashboard alongside P-001/P-006 entries.
                    overrides["trade_log_path"] = Path(
                        config.get("paths", {}).get(
                            "trade_log", "data/trade_logs/trade_log.jsonl"
                        )
                    )
                elif pod_id == "P-004":
                    overrides["forecastex_client"] = venue_clients.get("forecastex")
                    overrides["kalshi_client"] = venue_clients.get("kalshi")
                elif pod_id == "P-006":
                    overrides["polymarket_client"] = venue_clients.get("polymarket")
                    # Route P-006 to the shared trade log so its trades appear
                    # on the dashboard alongside P-001 entries.
                    overrides["trade_log_path"] = Path(
                        config.get("paths", {}).get(
                            "trade_log", "data/trade_logs/trade_log.jsonl"
                        )
                    )
                elif pod_id == "P-012":
                    overrides["nowcast_client"] = shared_deps.get("nowcast_client")
                    overrides["kalshi_client"] = venue_clients.get("kalshi")
                elif pod_id == "P-013":
                    overrides["kalshi_client"] = venue_clients.get("kalshi")
                elif pod_id == "P-014":
                    overrides["kalshi_client"] = venue_clients.get("kalshi")
                    overrides["trade_log_path"] = Path(
                        config.get("paths", {}).get(
                            "trade_log", "data/trade_logs/trade_log.jsonl"
                        )
                    )

                pod = pod_class.from_config(config, **overrides)
                pods.append(pod)
                logger.info("pod_runner: loaded pod %s (%s)", pod_id, pod.pod_name)

            except Exception as e:
                logger.error("pod_runner: failed to load pod %s: %s", pod_id, e)

        return cls(
            pods=pods,
            executor=executor,
            capital_allocator=capital_allocator,
            cycle_callback=cycle_callback,
        )
