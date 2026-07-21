"""
src/nowcast_client.py
─────────────────────
Nowcast data client for macro economic indicators.

Fetches real-time nowcast data from free public sources:
  - Cleveland Fed: CPI nowcast (Inflation Nowcasting)
  - Atlanta Fed: GDPNow
  - FRED: Payrolls, unemployment, etc.

All sources are free, no API key required (FRED key optional for
higher rate limits).

Each source returns a NowcastReading with a point estimate and
confidence interval.  The MacroNowcastPod aggregates these into
fair-value probabilities for Kalshi economic event markets.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NowcastReading:
    """A single nowcast data point from a source."""
    source: str              # "cleveland_fed_cpi", "atlanta_fed_gdp", etc.
    indicator: str           # "cpi_yoy", "gdp_quarterly", "payrolls"
    value: float             # Point estimate (e.g. 3.2 for 3.2% CPI)
    lower_bound: Optional[float] = None   # 90% CI lower
    upper_bound: Optional[float] = None   # 90% CI upper
    as_of_date: Optional[str] = None      # Date of the reading
    unit: str = "percent"    # "percent", "thousands", "index"
    extra: Dict[str, Any] = field(default_factory=dict)


class NowcastClient:
    """Aggregator for free nowcast data sources.

    In production, each source would make HTTP requests.
    For paper mode / testing, returns injectable data.

    The client is designed with an adapter pattern: inject a data provider
    for testing, or use the real HTTP fetchers in production.
    """

    def __init__(
        self,
        adapter: Optional[Any] = None,
        fred_api_key: Optional[str] = None,
        enabled_sources: Optional[List[str]] = None,
    ):
        self._adapter = adapter
        self._fred_api_key = fred_api_key
        self._enabled_sources = enabled_sources or [
            "cleveland_fed_cpi",
            "atlanta_fed_gdp",
            "fred_payrolls",
        ]

    def get_reading(self, source: str) -> Optional[NowcastReading]:
        """Get the latest reading from a specific source."""
        try:
            if self._adapter:
                return self._adapter.get_reading(source)

            # Production: dispatch to source-specific fetcher
            fetchers = {
                "cleveland_fed_cpi": self._fetch_cleveland_cpi,
                "atlanta_fed_gdp": self._fetch_atlanta_gdp,
                "fred_payrolls": self._fetch_fred_payrolls,
            }

            fetcher = fetchers.get(source)
            if not fetcher:
                logger.warning("NowcastClient: unknown source %s", source)
                return None

            return fetcher()
        except Exception as e:
            logger.error("NowcastClient: fetch failed for %s: %s", source, e)
            return None

    def get_all_readings(self) -> Dict[str, NowcastReading]:
        """Get latest readings from all enabled sources."""
        readings = {}
        for source in self._enabled_sources:
            reading = self.get_reading(source)
            if reading is not None:
                readings[source] = reading
        return readings

    def _fetch_cleveland_cpi(self) -> Optional[NowcastReading]:
        """Fetch Cleveland Fed CPI Nowcast.

        In production: GET https://www.clevelandfed.org/en/our-research/indicators-and-data/inflation-nowcasting
        Returns: NowcastReading with CPI year-over-year estimate.
        """
        # Stub — production would parse the Cleveland Fed page
        logger.info("NowcastClient: Cleveland Fed CPI fetch (stub)")
        return None

    def _fetch_atlanta_gdp(self) -> Optional[NowcastReading]:
        """Fetch Atlanta Fed GDPNow.

        In production: GET https://www.atlantafed.org/cqer/research/gdpnow
        Returns: NowcastReading with GDP quarterly growth estimate.
        """
        logger.info("NowcastClient: Atlanta Fed GDP fetch (stub)")
        return None

    def _fetch_fred_payrolls(self) -> Optional[NowcastReading]:
        """Fetch FRED non-farm payrolls consensus.

        In production: GET https://api.stlouisfed.org/fred/series/observations?series_id=PAYEMS
        Returns: NowcastReading with payrolls estimate.
        """
        logger.info("NowcastClient: FRED payrolls fetch (stub)")
        return None

    @classmethod
    def from_config(cls, config: dict) -> "NowcastClient":
        """Build from config dict."""
        pod_cfg = config.get("pods", {}).get("P-012", {})
        return cls(
            fred_api_key=config.get("fred", {}).get("api_key"),
            enabled_sources=pod_cfg.get("data_sources"),
        )
