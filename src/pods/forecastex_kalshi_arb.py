"""
src/pods/forecastex_kalshi_arb.py
─────────────────────────────────
P-004: ForecastEx-Kalshi Economic Arb pod.

Scans for pricing discrepancies on the same economic event
(Fed rate, CPI, GDP) across Interactive Brokers ForecastEx and Kalshi.

Both venues are live-capable (Kalshi active, IB active).
ForecastEx: 0% commission + 3.14% APY coupon.
Kalshi: 7% fee on profit.

Strategy:
  1. For each configured economic symbol, get ForecastEx YES price
  2. Find matching Kalshi market (by symbol mapping)
  3. Compare prices; if spread exceeds min_arb_pct, trade the cheaper side
  4. Can be single-leg (buy underpriced) or dual-leg (buy cheap, sell rich)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EconSymbolMapping:
    """Maps a ForecastEx symbol to its Kalshi equivalent."""
    forecastex_symbol: str    # e.g. "FED.RATE.25JUN"
    kalshi_ticker: str        # e.g. "FED-25JUN-T4.75"
    description: str          # Human-readable: "Fed Funds Rate June 2025"
    category: str             # "fed_rate", "cpi", "gdp", "payrolls"


# Default symbol mappings (extended via config)
DEFAULT_SYMBOL_MAPPINGS = [
    EconSymbolMapping("FED.RATE.25JUN", "FED-25JUN-T4.75", "Fed Rate June 2025", "fed_rate"),
    EconSymbolMapping("CPI.25JUN", "CPI-25JUN-T3.5", "CPI June 2025", "cpi"),
    EconSymbolMapping("GDP.25Q2", "GDP-25Q2-T2.0", "GDP Q2 2025", "gdp"),
]

from src.pod_registry import register_pod


@register_pod("P-004")
class ForecastExKalshiArbPod(BasePod):
    """P-004: ForecastEx-Kalshi Economic Arb.

    Scan cycle:
      1. For each symbol mapping, get ForecastEx price (YES + NO)
      2. Get Kalshi price for matching market
      3. Compute spread; if above min_arb_pct → edge exists
      4. Decide venue: buy underpriced side on cheaper venue
      5. Risk check + dedup
      6. Place (paper or live)
    """

    def __init__(
        self,
        forecastex_client,
        kalshi_client,
        edge_calculator,
        risk_manager,
        symbol_mappings: Optional[List[EconSymbolMapping]] = None,
        min_arb_pct: float = 0.02,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
    ):
        super().__init__(
            pod_id="P-004",
            pod_name="ForecastEx-Kalshi Econ Arb",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/pods/P-004.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
        )
        self.forecastex_client = forecastex_client
        self.kalshi_client = kalshi_client
        self.symbol_mappings = symbol_mappings or DEFAULT_SYMBOL_MAPPINGS
        self.min_arb_pct = min_arb_pct

    def scan_once(self) -> List[ScanResult]:
        results = []
        for mapping in self.symbol_mappings:
            try:
                scan_results = self._scan_symbol(mapping)
                results.extend(scan_results)
            except Exception as e:
                logger.error(
                    "P-004: Error scanning %s: %s",
                    mapping.forecastex_symbol, e,
                )
        return results

    def _scan_symbol(self, mapping: EconSymbolMapping) -> List[ScanResult]:
        """Scan a single symbol pair for arb opportunity."""
        results = []

        # 1. Get ForecastEx price
        fex_yes = self.forecastex_client.get_price(
            mapping.forecastex_symbol, right="CALL"
        )
        if fex_yes is None:
            logger.debug("P-004: No ForecastEx price for %s", mapping.forecastex_symbol)
            return []

        fex_no = 1.0 - fex_yes  # ForecastEx YES + NO = 1.00

        # 2. Get Kalshi price
        kalshi_yes = self._get_kalshi_price(mapping.kalshi_ticker)
        if kalshi_yes is None:
            logger.debug("P-004: No Kalshi price for %s", mapping.kalshi_ticker)
            return []

        kalshi_no = 1.0 - kalshi_yes

        # 3. Compute spreads (positive spread = arb opportunity)
        # Buy YES on cheaper venue, or buy NO on cheaper venue
        yes_spread = abs(fex_yes - kalshi_yes)
        no_spread = abs(fex_no - kalshi_no)  # Should be same as yes_spread

        # Check both directions
        for side, fex_price, kalshi_price in [
            ("YES", fex_yes, kalshi_yes),
            ("NO", fex_no, kalshi_no),
        ]:
            spread = abs(fex_price - kalshi_price)

            if spread < self.min_arb_pct:
                continue

            # Determine which venue is cheaper
            if fex_price < kalshi_price:
                buy_venue = "forecastex"
                buy_price = fex_price
                sell_venue = "kalshi"
                sell_price = kalshi_price
            else:
                buy_venue = "kalshi"
                buy_price = kalshi_price
                sell_venue = "forecastex"
                sell_price = fex_price

            # Use edge_calculator for sizing (treat cheaper price as venue_prob,
            # more expensive price as fair_prob proxy)
            edge_result = self.edge_calculator.best_side(
                fair_yes_prob=sell_price if side == "YES" else (1.0 - sell_price),
                kalshi_yes_price=buy_price if side == "YES" else (1.0 - buy_price),
            )

            # If edge_calculator returns None, still consider the raw spread
            if edge_result is None and spread >= self.min_arb_pct:
                # Build SKIPPED_EDGE result
                fp = self.make_fingerprint(
                    mapping.forecastex_symbol + "|" + mapping.kalshi_ticker, side
                )
                result = ScanResult(
                    fingerprint=fp,
                    timestamp_utc=self._now_fn().isoformat(),
                    pod_id=self.pod_id,
                    pod_name=self.pod_name,
                    mode=self.mode,
                    venue="multi",
                    market_type="economics_{}".format(mapping.category),
                    event=mapping.description,
                    market_id=mapping.forecastex_symbol,
                    side=side,
                    fair_prob=sell_price,
                    venue_prob=buy_price,
                    edge_pct=spread,
                    ev=0.0,
                    kelly_fraction=0.0,
                    position_size_usd=0.0,
                    action="SKIPPED_EDGE",
                    skip_reason="Edge calculator rejected but spread {:.1%}".format(spread),
                    extra={
                        "forecastex_yes": fex_yes,
                        "kalshi_yes": kalshi_yes,
                        "spread": spread,
                        "buy_venue": buy_venue,
                        "sell_venue": sell_venue,
                    },
                )
                self.write_log(result)
                results.append(result)
                continue

            if edge_result is None:
                continue

            # Dedup
            fp = self.make_fingerprint(
                mapping.forecastex_symbol + "|" + mapping.kalshi_ticker,
                edge_result.side,
            )
            if self.is_duplicate(fp):
                continue
            self.mark_seen(fp)

            # Risk check — position sizing (via BasePod helper)
            position_size = self.compute_position_size(edge_result.kelly_fractional)

            if position_size <= 0:
                continue

            # Place order on buy venue
            order_result = self._place_on_venue(
                buy_venue, mapping, side, buy_price, position_size
            )

            result = ScanResult(
                fingerprint=fp,
                timestamp_utc=self._now_fn().isoformat(),
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=self.mode,
                venue="multi",
                market_type="economics_{}".format(mapping.category),
                event=mapping.description,
                market_id=mapping.forecastex_symbol,
                side=edge_result.side,
                fair_prob=sell_price,
                venue_prob=buy_price,
                edge_pct=edge_result.raw_edge,
                ev=edge_result.ev,
                kelly_fraction=edge_result.kelly_fractional,
                position_size_usd=position_size,
                action="PLACED",
                order_id=getattr(order_result, "order_id", None),
                fill_price=getattr(order_result, "fill_price", None),
                extra={
                    "forecastex_yes": fex_yes,
                    "kalshi_yes": kalshi_yes,
                    "spread": spread,
                    "buy_venue": buy_venue,
                    "sell_venue": sell_venue,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "category": mapping.category,
                },
            )
            self.write_log(result)
            results.append(result)

        return results

    def _get_kalshi_price(self, ticker: str) -> Optional[float]:
        """Get Kalshi midpoint price for a ticker."""
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
            logger.error("P-004: Kalshi price fetch failed for %s: %s", ticker, e)
        return None

    def _place_on_venue(
        self, venue: str, mapping: EconSymbolMapping, side: str,
        price: float, position_size: float,
    ):
        """Place order on the specified venue."""
        right = "CALL" if side == "YES" else "PUT"
        quantity = max(1, int(position_size / price)) if price > 0 else 1

        if venue == "forecastex":
            return self.forecastex_client.place_order(
                symbol=mapping.forecastex_symbol,
                right=right,
                quantity=quantity,
                limit_price=price,
            )
        else:
            # Kalshi order
            if hasattr(self.kalshi_client, "place_order"):
                return self.kalshi_client.place_order(
                    ticker=mapping.kalshi_ticker,
                    side=side.lower(),
                    yes_price=int(price * 100) if side == "YES" else None,
                    no_price=int((1 - price) * 100) if side == "NO" else None,
                    count=quantity,
                )
            # Fallback for mock clients
            class _MockResult:
                order_id = None
                fill_price = price
            return _MockResult()

    def venue_name(self) -> str:
        return "multi"

    @classmethod
    def from_config(cls, config: dict, **overrides):
        """Build P-004 from config.

        Requires forecastex_client and kalshi_client in overrides.
        """
        pod_cfg = config.get("pods", {}).get("P-004", {})

        # Build symbol mappings from config
        mappings = None
        symbols = pod_cfg.get("symbols", [])
        if symbols and isinstance(symbols, list) and isinstance(symbols[0], dict):
            mappings = [
                EconSymbolMapping(
                    forecastex_symbol=s["forecastex_symbol"],
                    kalshi_ticker=s["kalshi_ticker"],
                    description=s.get("description", ""),
                    category=s.get("category", "economics"),
                )
                for s in symbols
            ]

        log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
        log_path = overrides.get("trade_log_path") or log_dir / "P-004.jsonl"

        env = pod_cfg.get("environment",
              config.get("environment", "demo"))
        mode_map = {"demo": "paper", "live": "live"}

        return cls(
            forecastex_client=overrides.get("forecastex_client"),
            kalshi_client=overrides.get("kalshi_client"),
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            symbol_mappings=mappings,
            min_arb_pct=pod_cfg.get("min_arb_pct", 0.02),
            trade_log_path=log_path,
            mode=mode_map.get(env, "paper"),
            _now_fn=overrides.get("_now_fn"),
        )
