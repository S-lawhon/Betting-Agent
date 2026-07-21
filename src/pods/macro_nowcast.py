"""
src/pods/macro_nowcast.py
─────────────────────────
P-012: Macro Economic Nowcast pod.

Uses free nowcast data (Cleveland Fed CPI, Atlanta Fed GDP, FRED)
to build fair-value probabilities for Kalshi economic event markets.

Strategy:
  1. Fetch latest nowcast readings (CPI, GDP, payrolls)
  2. Convert nowcast point estimate + CI to probability for each Kalshi threshold
  3. Compare fair-value prob to Kalshi market price
  4. If edge > threshold → place trade on Kalshi

Key insight from research: Kalshi beat Bloomberg on CPI prediction
11 out of 13 months.  Nowcast data provides an independent signal
to identify when Kalshi is mispriced relative to fundamentals.
"""

import logging
import math
from pathlib import Path
from typing import Any, cast
from typing import Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.nowcast_client import NowcastClient, NowcastReading

logger = logging.getLogger(__name__)


def _normal_cdf(x: float) -> float:
    """Standard normal CDF using the error function (stdlib math)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def nowcast_to_probability(
    reading: NowcastReading,
    threshold: float,
    direction: str = "above",
) -> Optional[float]:
    """Convert a nowcast reading to probability of crossing a threshold.

    Uses a Gaussian approximation:  if nowcast has a point estimate and
    confidence interval, derive sigma from the CI.  Then compute
    P(indicator > threshold) or P(indicator < threshold).

    Args:
        reading: NowcastReading with value, optional lower/upper bounds.
        threshold: The Kalshi contract threshold (e.g. 3.5 for CPI > 3.5%).
        direction: "above" → P(value > threshold), "below" → P(value < threshold).

    Returns:
        Probability in [0, 1], or None if estimation fails.
    """
    value = reading.value

    # Estimate sigma from confidence interval (assume 90% CI → 1.645 z)
    sigma = None
    if reading.upper_bound is not None and reading.lower_bound is not None:
        ci_width = reading.upper_bound - reading.lower_bound
        if ci_width > 0:
            sigma = ci_width / (2 * 1.645)

    # Fallback: use 10% of value as sigma (rough heuristic)
    if sigma is None or sigma <= 0:
        sigma = max(abs(value) * 0.10, 0.01)

    z = (threshold - value) / sigma

    if direction == "above":
        return 1.0 - _normal_cdf(z)
    else:
        return _normal_cdf(z)


# Default Kalshi market mappings for economic indicators
DEFAULT_ECON_MARKETS = [
    {
        "source": "cleveland_fed_cpi",
        "indicator": "cpi_yoy",
        "kalshi_tickers": [
            {"ticker": "CPI-25JUN-T3.0", "threshold": 3.0, "direction": "above"},
            {"ticker": "CPI-25JUN-T3.5", "threshold": 3.5, "direction": "above"},
            {"ticker": "CPI-25JUN-T4.0", "threshold": 4.0, "direction": "above"},
        ],
    },
    {
        "source": "atlanta_fed_gdp",
        "indicator": "gdp_quarterly",
        "kalshi_tickers": [
            {"ticker": "GDP-25Q2-T1.0", "threshold": 1.0, "direction": "above"},
            {"ticker": "GDP-25Q2-T2.0", "threshold": 2.0, "direction": "above"},
            {"ticker": "GDP-25Q2-T3.0", "threshold": 3.0, "direction": "above"},
        ],
    },
    {
        "source": "fred_payrolls",
        "indicator": "payrolls",
        "kalshi_tickers": [
            {"ticker": "JOBS-25JUN-T150", "threshold": 150.0, "direction": "above"},
            {"ticker": "JOBS-25JUN-T200", "threshold": 200.0, "direction": "above"},
        ],
    },
]

from src.pod_registry import register_pod


@register_pod("P-012")
class MacroNowcastPod(BasePod):
    """P-012: Macro Economic Nowcast.

    Scan cycle:
      1. Fetch latest nowcast readings
      2. For each reading, compute fair probabilities for each Kalshi threshold
      3. Compare to Kalshi market prices
      4. If edge > threshold → trade
    """

    def __init__(
        self,
        nowcast_client: NowcastClient,
        kalshi_client,
        edge_calculator,
        risk_manager,
        econ_markets: Optional[List[Dict]] = None,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
    ):
        super().__init__(
            pod_id="P-012",
            pod_name="Macro Economic Nowcast",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/pods/P-012.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
        )
        self.nowcast_client = nowcast_client
        self.kalshi_client = kalshi_client
        self.econ_markets = econ_markets or DEFAULT_ECON_MARKETS

    def scan_once(self) -> List[ScanResult]:
        results = []

        # Fetch all nowcast readings
        readings = self.nowcast_client.get_all_readings()

        if not readings:
            logger.debug("P-012: No nowcast readings available")
            return []

        for market_group in self.econ_markets:
            source = str(market_group["source"])
            reading = readings.get(source)
            if reading is None:
                continue

            tickers = list(market_group["kalshi_tickers"])  # type: ignore[arg-type]
            for ticker_cfg in tickers:
                try:
                    result = self._evaluate_ticker(reading, ticker_cfg)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(
                        "P-012: Error evaluating %s: %s",
                        ticker_cfg.get("ticker", "?"), e,
                    )

        return results

    def _evaluate_ticker(
        self,
        reading: NowcastReading,
        ticker_cfg: Dict,
    ) -> Optional[ScanResult]:
        """Evaluate a single Kalshi ticker against nowcast data."""
        ticker = ticker_cfg["ticker"]
        threshold = ticker_cfg["threshold"]
        direction = ticker_cfg.get("direction", "above")

        # 1. Compute fair probability from nowcast
        fair_prob = nowcast_to_probability(reading, threshold, direction)
        if fair_prob is None:
            return None

        # 2. Get Kalshi price
        kalshi_yes = self._get_kalshi_price(ticker)
        if kalshi_yes is None:
            return None

        # 3. Evaluate edge
        edge_result = self.edge_calculator.best_side(
            fair_yes_prob=fair_prob,
            kalshi_yes_price=kalshi_yes,
        )

        market_id = ticker
        event_desc = "{} {} {}".format(
            reading.source, direction, threshold
        )

        if edge_result is None:
            fp = self.make_fingerprint(market_id, "NONE")
            result = ScanResult(
                fingerprint=fp,
                timestamp_utc=self._now_fn().isoformat(),
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=self.mode,
                venue="kalshi",
                market_type="economics_{}".format(reading.source.split("_")[-1]),
                event=event_desc,
                market_id=market_id,
                side="NONE",
                fair_prob=fair_prob,
                venue_prob=kalshi_yes,
                edge_pct=0.0,
                ev=0.0,
                kelly_fraction=0.0,
                position_size_usd=0.0,
                action="SKIPPED_EDGE",
                skip_reason="Below edge threshold",
                extra={
                    "nowcast_value": reading.value,
                    "nowcast_source": reading.source,
                    "threshold": threshold,
                    "direction": direction,
                },
            )
            self.write_log(result)
            return result

        # Dedup
        fp = self.make_fingerprint(market_id, edge_result.side)
        if self.is_duplicate(fp):
            return None
        self.mark_seen(fp)

        # Risk check — position sizing (via BasePod helper)
        position_size = self.compute_position_size(edge_result.kelly_fractional)

        if position_size <= 0:
            return None

        # Place on Kalshi
        order_result = self._place_kalshi(
            ticker, edge_result.side, kalshi_yes, position_size
        )

        result = ScanResult(
            fingerprint=fp,
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type="economics_{}".format(reading.source.split("_")[-1]),
            event=event_desc,
            market_id=market_id,
            side=edge_result.side,
            fair_prob=fair_prob,
            venue_prob=kalshi_yes,
            edge_pct=edge_result.raw_edge,
            ev=edge_result.ev,
            kelly_fraction=edge_result.kelly_fractional,
            position_size_usd=position_size,
            action="PLACED",
            order_id=getattr(order_result, "order_id", None),
            fill_price=getattr(order_result, "fill_price", None),
            extra={
                "nowcast_value": reading.value,
                "nowcast_source": reading.source,
                "threshold": threshold,
                "direction": direction,
                "nowcast_lower": reading.lower_bound,
                "nowcast_upper": reading.upper_bound,
            },
        )
        self.write_log(result)
        return result

    def _get_kalshi_price(self, ticker: str) -> Optional[float]:
        """Get Kalshi midpoint price."""
        try:
            if hasattr(self.kalshi_client, "get_market"):
                market = self.kalshi_client.get_market(ticker)
                if market is None:
                    return None
                yes_bid = market.get("yes_bid")
                yes_ask = market.get("yes_ask")
                if yes_bid is not None and yes_ask is not None:
                    return (float(yes_bid) + float(yes_ask)) / 200.0
                return None
            elif hasattr(self.kalshi_client, "get_price"):
                return self.kalshi_client.get_price(ticker)
        except Exception as e:
            logger.error("P-012: Kalshi price failed for %s: %s", ticker, e)
        return None

    def _place_kalshi(
        self, ticker: str, side: str, price: float, position_size: float,
    ):
        """Place order on Kalshi."""
        quantity = max(1, int(position_size / price)) if price > 0 else 1

        if hasattr(self.kalshi_client, "place_order"):
            return self.kalshi_client.place_order(
                ticker=ticker,
                side=side.lower(),
                yes_price=int(price * 100) if side == "YES" else None,
                no_price=int((1 - price) * 100) if side == "NO" else None,
                count=quantity,
            )

        class _MockResult:
            order_id = None
            fill_price = price
        return _MockResult()

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config: dict, **overrides):
        """Build P-012 from config."""
        pod_cfg = config.get("pods", {}).get("P-012", {})

        nowcast = overrides.get("nowcast_client") or NowcastClient.from_config(config)

        log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
        log_path = overrides.get("trade_log_path") or log_dir / "P-012.jsonl"

        env = pod_cfg.get("environment", config.get("environment", "demo"))
        mode_map = {"demo": "paper", "live": "live"}

        return cls(
            nowcast_client=nowcast,
            kalshi_client=overrides.get("kalshi_client"),
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            econ_markets=pod_cfg.get("econ_markets"),
            trade_log_path=log_path,
            mode=mode_map.get(env, "paper"),
            _now_fn=overrides.get("_now_fn"),
        )
