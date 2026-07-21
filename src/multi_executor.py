"""
src/multi_executor.py
─────────────────────
Venue-agnostic executor that routes ScanResults from any pod
to the correct venue client.

Replaces the existing single-venue Executor for multi-pod operation.
The existing Executor continues to work for P-001 standalone.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.base_pod import BasePod, ScanResult

logger = logging.getLogger(__name__)


@dataclass
class MultiExecutionResult:
    """Result of executing a ScanResult on a venue."""
    success: bool
    pod_id: str
    venue: str
    market_id: str
    mode: str
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    contracts: int = 0
    error: Optional[str] = None
    skipped: bool = False
    position_size_usd: float = 0.0


class MultiExecutor:
    """Routes ScanResults from any pod to the correct venue client.

    Constructor takes a dict mapping venue names to client instances:
        {"kalshi": KalshiClient, "polymarket": PolymarketClient, ...}
    """

    def __init__(
        self,
        venue_clients: Dict[str, Any],
        risk_manager=None,
        aggregate_risk=None,
        mode: str = "paper",
        live_trading_confirmed: bool = False,
        max_scan_workers: Optional[int] = None,
    ):
        self.venue_clients = venue_clients
        self.risk_manager = risk_manager
        self.aggregate_risk = aggregate_risk
        self.mode = mode
        self.live_trading_confirmed = live_trading_confirmed
        # Cap on concurrent pod scans.  None = one thread per pod (the old
        # behaviour).  See run_cycle for why this matters.
        self.max_scan_workers = max_scan_workers

    def execute(self, result: ScanResult) -> MultiExecutionResult:
        """Execute a single ScanResult on its target venue."""
        # Skip non-PLACED results
        if result.action != "PLACED":
            return MultiExecutionResult(
                success=True,
                pod_id=result.pod_id,
                venue=result.venue,
                market_id=result.market_id,
                mode=result.mode,
                skipped=True,
            )

        # Aggregate risk gate — check portfolio/venue/pod exposure limits
        if self.aggregate_risk is not None:
            if not self.aggregate_risk.check_trade(
                pod_id=result.pod_id,
                venue=result.venue,
                position_size_usd=result.position_size_usd,
            ):
                logger.info(
                    "multi_executor.risk_rejected: pod=%s venue=%s market=%s size=%.2f",
                    result.pod_id, result.venue, result.market_id,
                    result.position_size_usd,
                )
                return MultiExecutionResult(
                    success=True,
                    pod_id=result.pod_id,
                    venue=result.venue,
                    market_id=result.market_id,
                    mode=result.mode,
                    skipped=True,
                )

        venue = result.venue
        client = self.venue_clients.get(venue)
        if client is None:
            return MultiExecutionResult(
                success=False,
                pod_id=result.pod_id,
                venue=venue,
                market_id=result.market_id,
                mode=result.mode,
                error="No client for venue: {}".format(venue),
            )

        # Paper mode: record but don't execute
        if self.mode == "paper" or result.mode == "paper":
            logger.info(
                "multi_executor.paper_fill: pod=%s venue=%s market=%s side=%s size=%s",
                result.pod_id, venue, result.market_id, result.side, result.position_size_usd,
            )
            return MultiExecutionResult(
                success=True,
                pod_id=result.pod_id,
                venue=venue,
                market_id=result.market_id,
                mode="paper",
                fill_price=result.venue_prob,
                position_size_usd=result.position_size_usd,
            )

        # Live mode: route to venue-specific execution
        try:
            if venue == "kalshi":
                return self._execute_kalshi(client, result)
            elif venue == "polymarket":
                return self._execute_polymarket(client, result)
            elif venue == "forecastex":
                return self._execute_forecastex(client, result)
            else:
                return MultiExecutionResult(
                    success=False,
                    pod_id=result.pod_id,
                    venue=venue,
                    market_id=result.market_id,
                    mode="live",
                    error="Unknown venue: {}".format(venue),
                )
        except Exception as e:
            logger.error(
                "multi_executor.execution_error: pod=%s venue=%s err=%s",
                result.pod_id, venue, e,
            )
            return MultiExecutionResult(
                success=False,
                pod_id=result.pod_id,
                venue=venue,
                market_id=result.market_id,
                mode="live",
                error=str(e),
            )

    def run_cycle(
        self,
        pods: List[BasePod],
        concurrent: bool = True,
        max_workers: Optional[int] = None,
    ) -> List[MultiExecutionResult]:
        """Run scan_once() on all pods, execute results.

        Args:
            pods:        List of pod instances to scan.
            concurrent:  If True (default), scan pods concurrently using threads.
                         Each pod's scan_once() is I/O-bound (HTTP calls), so
                         threading is highly effective.  Execution of results
                         happens serially to preserve aggregate risk ordering.
            max_workers: Max threads for concurrent scanning.  Falls back to
                         self.max_scan_workers (from config), then to the
                         number of pods.

                         KEEP THIS SMALL.  Kalshi throttles on CONCURRENT
                         CONNECTIONS, not requests/second — 12 threads produce
                         constant 429s while a single connection at ~6.7 req/s
                         produces none.  Unbounded, this opens one connection
                         per active pod and was measured (2026-07-20/21)
                         producing 829 HTTP 429s/24h, ~118/hr sustained at
                         peak, on 6 active pods.  The 5-minute cycle has ample
                         headroom to absorb the serialisation: measured cycles
                         complete in 30-46s of the 300s budget.

        Returns:
            List of MultiExecutionResult for all pods.
        """
        if not concurrent or len(pods) <= 1:
            return self._run_cycle_sequential(pods)

        return self._run_cycle_concurrent(
            pods, max_workers if max_workers is not None
            else self.max_scan_workers
        )

    def _run_cycle_sequential(self, pods: List[BasePod]) -> List[MultiExecutionResult]:
        """Original sequential scan + execute loop."""
        all_results = []
        for pod in pods:
            try:
                scan_results = pod.scan_once()
                for sr in scan_results:
                    exec_result = self.execute(sr)
                    all_results.append(exec_result)
            except Exception as e:
                logger.error(
                    "multi_executor.pod_error: pod=%s err=%s",
                    pod.pod_id, e,
                )
                all_results.append(MultiExecutionResult(
                    success=False,
                    pod_id=pod.pod_id,
                    venue=pod.venue_name(),
                    market_id="",
                    mode=pod.mode,
                    error=str(e),
                ))
        return all_results

    def _run_cycle_concurrent(
        self, pods: List[BasePod], max_workers: Optional[int] = None,
        scan_timeout: float = 120.0,
    ) -> List[MultiExecutionResult]:
        """Scan pods concurrently, then execute results serially.

        The scanning phase (HTTP calls to venue APIs) is the bottleneck and
        is embarrassingly parallel.  Execution is serial to preserve correct
        ordering of aggregate risk checks.

        Args:
            scan_timeout: Max seconds to wait for any single pod's scan_once().
                          Pods that exceed this are logged as timed-out and
                          skipped for this cycle.  Prevents one slow pod from
                          blocking the entire cycle indefinitely.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

        workers = max_workers or len(pods)
        if max_workers is None:
            # Unbounded concurrency is the 429 bug — surface it rather than
            # letting a missing config key silently reintroduce the problem.
            logger.warning(
                "multi_executor: max_scan_workers unset — scanning %d pods "
                "with %d concurrent connections. Kalshi throttles on "
                "concurrency; set execution.max_scan_workers in config.",
                len(pods), workers,
            )
        all_results = []

        # Phase 1: Scan all pods concurrently with per-pod timeout
        pod_scan_results = {}  # id(pod) → (List[ScanResult] | Exception)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_pod = {
                pool.submit(self._safe_scan, pod): pod for pod in pods
            }
            try:
                for future in as_completed(future_to_pod, timeout=scan_timeout + 10):
                    pod = future_to_pod[future]
                    try:
                        pod_scan_results[id(pod)] = future.result(timeout=5.0)
                    except TimeoutError:
                        logger.error(
                            "multi_executor.scan_timeout: pod=%s exceeded timeout",
                            pod.pod_id,
                        )
                        pod_scan_results[id(pod)] = TimeoutError(
                            f"{pod.pod_id} scan timed out"
                        )
                    except Exception as e:
                        pod_scan_results[id(pod)] = e
            except TimeoutError:
                # as_completed itself timed out — mark any uncollected pods
                for future, pod in future_to_pod.items():
                    if id(pod) not in pod_scan_results:
                        logger.error(
                            "multi_executor.cycle_timeout: pod=%s not finished after %.0fs",
                            pod.pod_id, scan_timeout,
                        )
                        pod_scan_results[id(pod)] = TimeoutError(
                            f"{pod.pod_id} exceeded cycle timeout"
                        )
                        future.cancel()

        # Phase 2: Execute results serially (preserves ordering for risk checks)
        for pod in pods:
            result = pod_scan_results.get(id(pod))
            if isinstance(result, Exception):
                logger.error(
                    "multi_executor.pod_error: pod=%s err=%s", pod.pod_id, result,
                )
                all_results.append(MultiExecutionResult(
                    success=False,
                    pod_id=pod.pod_id,
                    venue=pod.venue_name(),
                    market_id="",
                    mode=pod.mode,
                    error=str(result),
                ))
            elif result is not None:
                for sr in result:
                    exec_result = self.execute(sr)
                    all_results.append(exec_result)

        return all_results

    @staticmethod
    def _safe_scan(pod: BasePod):
        """Run pod.scan_once() with exception capture for thread safety."""
        try:
            return pod.scan_once()
        except Exception as e:
            return e

    # ── Venue-specific execution ────────────────────────────────────

    def _execute_kalshi(self, client, result: ScanResult) -> MultiExecutionResult:
        """Execute on Kalshi via KalshiClient.place_order()."""
        import math
        import uuid

        price_cents = max(1, min(99, round(result.venue_prob * 100)))
        cost = result.venue_prob if result.side == "YES" else (1.0 - result.venue_prob)
        contracts = max(1, math.floor(result.position_size_usd / cost)) if cost > 0 else 1

        order = {
            "ticker": result.market_id,
            "action": "buy",
            "side": result.side.lower(),
            "type": "limit",
            "count": contracts,
            "yes_price": price_cents,
            "client_order_id": uuid.uuid4().hex,
        }
        resp = client.place_order(order)
        order_id = resp.get("order", {}).get("order_id") if isinstance(resp, dict) else None

        return MultiExecutionResult(
            success=True,
            pod_id=result.pod_id,
            venue="kalshi",
            market_id=result.market_id,
            mode="live",
            order_id=order_id,
            fill_price=result.venue_prob,
            contracts=contracts,
            position_size_usd=result.position_size_usd,
        )

    def _execute_polymarket(self, client, result: ScanResult) -> MultiExecutionResult:
        """Execute on Polymarket via PolymarketClient.place_order()."""
        size_contracts = result.position_size_usd / result.venue_prob if result.venue_prob > 0 else 0
        order_result = client.place_order(
            token_id=result.market_id,
            side="BUY",
            price=result.venue_prob,
            size=size_contracts,
        )
        return MultiExecutionResult(
            success=order_result.success,
            pod_id=result.pod_id,
            venue="polymarket",
            market_id=result.market_id,
            mode="live",
            order_id=order_result.order_id,
            fill_price=order_result.fill_price,
            error=order_result.error,
        )

    def _execute_forecastex(self, client, result: ScanResult) -> MultiExecutionResult:
        """Execute on IB ForecastEx via ForecastExClient.place_order()."""
        right = "CALL" if result.side in ("YES", "BUY") else "PUT"
        quantity = max(1, round(result.position_size_usd / result.venue_prob)) if result.venue_prob > 0 else 1
        order_result = client.place_order(
            symbol=result.market_id,
            right=right,
            quantity=quantity,
            limit_price=result.venue_prob,
        )
        return MultiExecutionResult(
            success=order_result.success,
            pod_id=result.pod_id,
            venue="forecastex",
            market_id=result.market_id,
            mode="live",
            order_id=str(order_result.order_id) if order_result.order_id else None,
            fill_price=order_result.fill_price,
            error=order_result.error,
        )

    @classmethod
    def from_config(cls, config: dict, venue_clients: Dict[str, Any],
                    risk_manager=None, aggregate_risk=None) -> "MultiExecutor":
        """Build MultiExecutor from config + pre-built venue clients."""
        mode_map = {"demo": "paper", "live": "live"}
        env = config.get("environment", "demo")
        import os
        confirmed = os.environ.get("I_UNDERSTAND_LIVE_TRADING", "").lower() == "true"
        execution = config.get("execution") or {}
        raw_workers = execution.get("max_scan_workers")
        max_scan_workers = None
        if raw_workers is not None:
            try:
                max_scan_workers = max(1, int(raw_workers))
            except (TypeError, ValueError):
                logger.warning(
                    "multi_executor: execution.max_scan_workers=%r is not an "
                    "int — falling back to unbounded concurrency",
                    raw_workers,
                )
        return cls(
            venue_clients=venue_clients,
            risk_manager=risk_manager,
            aggregate_risk=aggregate_risk,
            mode=mode_map.get(env, "paper"),
            live_trading_confirmed=confirmed,
            max_scan_workers=max_scan_workers,
        )
