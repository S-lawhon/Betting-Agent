"""
src/nowcast_http.py
──────────────────
Real HTTP adapters for the NowcastClient.

Implements concrete fetchers for free public macro-economic data sources:
  - ClevelandFedAdapter  — CPI year-over-year nowcast (Cleveland Fed)
  - AtlantaFedAdapter    — GDP quarterly growth nowcast (Atlanta Fed GDPNow)
  - FredAdapter          — Non-farm payrolls latest release (FRED API)
  - CompositeNowcastAdapter — routes get_reading(source) to the right adapter

All HTTP is done via stdlib urllib.request (no pip installs required).
Each adapter is injectable for testing: pass a `_http_get` callable
that returns (status_code, body_bytes) to mock network calls.

Data sources
───────────
Cleveland Fed CPI:
  https://www.clevelandfed.org/-/media/research/data-sets/inflation-nowcasting.csv
  Monthly CPI nowcast with 90% confidence intervals.

Atlanta Fed GDPNow:
  https://www.atlantafed.org/-/media/documents/cqer/researchcqi/gdpnow/GDPNow-model-data.csv
  Updated several times per quarter with current-quarter GDP estimate.

FRED (St Louis Fed) — Non-Farm Payrolls:
  https://api.stlouisfed.org/fred/series/observations?series_id=PAYEMS
  Requires a free FRED API key.  Returns latest monthly payroll count (thousands).
"""

import csv
import io
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from src.nowcast_client import NowcastClient, NowcastReading

logger = logging.getLogger(__name__)

# ── Types ────────────────────────────────────────────────────────────
# Signature for an injectable HTTP fetcher:
# _HttpGet = Callable[[str], Tuple[int, bytes]]
#   - Takes a URL string
#   - Returns (status_code: int, body: bytes)

_DEFAULT_TIMEOUT = 10  # seconds


def _default_http_get(url: str) -> Tuple[int, bytes]:
    """Production HTTP GET using urllib.request."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "BettingPodShop/1.0 (nowcast data fetcher)"},
    )
    with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
        return resp.status, resp.read()


# ── Cleveland Fed CPI Nowcast ────────────────────────────────────────

class ClevelandFedAdapter:
    """Fetches CPI year-over-year nowcast from the Cleveland Fed.

    The Cleveland Fed publishes monthly CPI nowcast estimates with
    90% confidence intervals as a CSV download.

    Expected CSV format (approximate, header row first):
        date, cpi_yoy_nowcast, ci_lower_90, ci_upper_90, ...

    Returns:
        NowcastReading(source="cleveland_fed_cpi", indicator="cpi_yoy",
                       value=3.2, lower_bound=2.8, upper_bound=3.6, ...)
    """

    DATA_URL = (
        "https://www.clevelandfed.org/-/media/research/data-sets/"
        "inflation-nowcasting.csv"
    )

    def __init__(
        self,
        data_url: Optional[str] = None,
        _http_get: Optional[Callable[[str], Tuple[int, bytes]]] = None,
    ):
        self._url = data_url or self.DATA_URL
        self._http_get = _http_get or _default_http_get

    def get_reading(self, source: str) -> Optional[NowcastReading]:
        """Fetch and parse the latest CPI nowcast.

        Args:
            source: Expected to be "cleveland_fed_cpi".

        Returns:
            NowcastReading or None on any error.
        """
        if source != "cleveland_fed_cpi":
            return None

        try:
            status, body = self._http_get(self._url)
        except Exception as exc:
            logger.error("ClevelandFedAdapter: HTTP error — %s", exc)
            return None

        if status != 200:
            logger.warning("ClevelandFedAdapter: unexpected status %d", status)
            return None

        return self._parse_csv(body)

    def _parse_csv(self, body: bytes) -> Optional[NowcastReading]:
        """Parse the Cleveland Fed CSV body and return the latest reading."""
        try:
            text = body.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            if not rows:
                logger.warning("ClevelandFedAdapter: empty CSV")
                return None

            # Take the last non-empty row as the latest estimate
            latest = rows[-1]

            # Column names vary — try common variants
            value = _parse_float_any(latest, ["cpi_yoy", "nowcast", "estimate", "value"])
            lower = _parse_float_any(latest, ["ci_lower_90", "lower_90", "lower", "ci_lo"])
            upper = _parse_float_any(latest, ["ci_upper_90", "upper_90", "upper", "ci_hi"])
            as_of = _pick_any(latest, ["date", "as_of", "month"])

            if value is None:
                # Fallback: try to use first numeric column after the date column
                keys = list(latest.keys())
                for k in keys[1:]:
                    v = _try_float(latest.get(k, ""))
                    if v is not None:
                        value = v
                        break

            if value is None:
                logger.warning("ClevelandFedAdapter: could not parse value from %s", latest)
                return None

            return NowcastReading(
                source="cleveland_fed_cpi",
                indicator="cpi_yoy",
                value=value,
                lower_bound=lower,
                upper_bound=upper,
                as_of_date=as_of,
                unit="percent",
            )

        except Exception as exc:
            logger.error("ClevelandFedAdapter: parse error — %s", exc)
            return None


# ── Atlanta Fed GDPNow ───────────────────────────────────────────────

class AtlantaFedAdapter:
    """Fetches GDP quarterly growth nowcast from Atlanta Fed GDPNow.

    GDPNow is updated several times per quarter and provides the
    latest estimate of real GDP growth for the current quarter.

    Expected CSV format (latest row = most recent estimate):
        date_of_release, gdpnow_forecast, ...

    Returns:
        NowcastReading(source="atlanta_fed_gdp", indicator="gdp_quarterly",
                       value=2.5, ...)
    """

    DATA_URL = (
        "https://www.atlantafed.org/-/media/documents/cqer/researchcqi/"
        "gdpnow/GDPNow-model-data.csv"
    )

    def __init__(
        self,
        data_url: Optional[str] = None,
        _http_get: Optional[Callable[[str], Tuple[int, bytes]]] = None,
    ):
        self._url = data_url or self.DATA_URL
        self._http_get = _http_get or _default_http_get

    def get_reading(self, source: str) -> Optional[NowcastReading]:
        """Fetch and parse the latest GDPNow estimate.

        Args:
            source: Expected to be "atlanta_fed_gdp".

        Returns:
            NowcastReading or None on any error.
        """
        if source != "atlanta_fed_gdp":
            return None

        try:
            status, body = self._http_get(self._url)
        except Exception as exc:
            logger.error("AtlantaFedAdapter: HTTP error — %s", exc)
            return None

        if status != 200:
            logger.warning("AtlantaFedAdapter: unexpected status %d", status)
            return None

        return self._parse_csv(body)

    def _parse_csv(self, body: bytes) -> Optional[NowcastReading]:
        """Parse the GDPNow CSV body and return the latest reading."""
        try:
            text = body.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            if not rows:
                logger.warning("AtlantaFedAdapter: empty CSV")
                return None

            latest = rows[-1]

            # Try common column name variants for the forecast value
            value = _parse_float_any(
                latest,
                ["gdpnow", "gdp_forecast", "model_gdp", "forecast", "estimate", "value"],
            )
            as_of = _pick_any(latest, ["date", "date_of_release", "as_of", "release_date"])

            if value is None:
                # Fallback: use first numeric column after the date
                keys = list(latest.keys())
                for k in keys[1:]:
                    v = _try_float(latest.get(k, ""))
                    if v is not None:
                        value = v
                        break

            if value is None:
                logger.warning("AtlantaFedAdapter: could not parse value from %s", latest)
                return None

            return NowcastReading(
                source="atlanta_fed_gdp",
                indicator="gdp_quarterly",
                value=value,
                lower_bound=None,
                upper_bound=None,
                as_of_date=as_of,
                unit="percent",
            )

        except Exception as exc:
            logger.error("AtlantaFedAdapter: parse error — %s", exc)
            return None


# ── FRED Adapter ─────────────────────────────────────────────────────

class FredAdapter:
    """Fetches economic series from the FRED API (St. Louis Fed).

    Requires a free FRED API key (register at fred.stlouisfed.org).
    If no API key is provided, returns None silently.

    Supports any FRED series ID.  Non-Farm Payrolls (PAYEMS) is the
    primary use case, but the adapter is generic.

    Returns:
        NowcastReading with the most recent observation value.
    """

    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(
        self,
        series_id: str = "PAYEMS",
        source_name: str = "fred_payrolls",
        indicator_name: str = "payrolls",
        unit: str = "thousands",
        api_key: Optional[str] = None,
        _http_get: Optional[Callable[[str], Tuple[int, bytes]]] = None,
    ):
        self.series_id = series_id
        self._source_name = source_name
        self._indicator_name = indicator_name
        self._unit = unit
        self._api_key = api_key
        self._http_get = _http_get or _default_http_get

    def get_reading(self, source: str) -> Optional[NowcastReading]:
        """Fetch the latest observation for the configured FRED series.

        Args:
            source: Expected to match self._source_name.

        Returns:
            NowcastReading or None if no API key or fetch fails.
        """
        if source != self._source_name:
            return None

        if not self._api_key:
            logger.debug("FredAdapter: no API key configured, skipping %s", source)
            return None

        url = (
            "{base}?series_id={sid}&api_key={key}"
            "&file_type=json&sort_order=desc&limit=2"
        ).format(base=self.BASE_URL, sid=self.series_id, key=self._api_key)

        try:
            status, body = self._http_get(url)
        except Exception as exc:
            logger.error("FredAdapter: HTTP error for %s — %s", self.series_id, exc)
            return None

        if status != 200:
            logger.warning("FredAdapter: status %d for %s", status, self.series_id)
            return None

        return self._parse_json(body)

    def _parse_json(self, body: bytes) -> Optional[NowcastReading]:
        """Parse FRED API JSON response."""
        try:
            data = json.loads(body.decode("utf-8"))
            observations = data.get("observations", [])
            if not observations:
                logger.warning("FredAdapter: no observations in %s response", self.series_id)
                return None

            # observations are in reverse chrono order (sort_order=desc)
            latest = observations[0]
            raw_val = latest.get("value", "")
            value = _try_float(raw_val)

            # FRED uses "." to indicate missing values
            if value is None or raw_val == ".":
                # Try the second observation (revision lag)
                if len(observations) > 1:
                    value = _try_float(observations[1].get("value", ""))

            if value is None:
                logger.warning("FredAdapter: could not parse value for %s", self.series_id)
                return None

            return NowcastReading(
                source=self._source_name,
                indicator=self._indicator_name,
                value=value,
                lower_bound=None,
                upper_bound=None,
                as_of_date=latest.get("date"),
                unit=self._unit,
            )

        except Exception as exc:
            logger.error("FredAdapter: parse error for %s — %s", self.series_id, exc)
            return None


# ── Composite adapter (dispatches by source name) ────────────────────

class CompositeNowcastAdapter:
    """Routes get_reading(source) to the appropriate source adapter.

    This is the adapter injected into NowcastClient in production.

    Usage:
        adapter = CompositeNowcastAdapter.from_config(config)
        client  = NowcastClient(adapter=adapter)
        reading = client.get_reading("cleveland_fed_cpi")
    """

    def __init__(self, adapters: Optional[Dict[str, Any]] = None):
        self._adapters: Dict[str, Any] = adapters or {}

    def get_reading(self, source: str) -> Optional[NowcastReading]:
        """Dispatch to the adapter registered for source.

        Returns None if no adapter is registered for the given source.
        """
        adapter = self._adapters.get(source)
        if adapter is None:
            logger.debug("CompositeNowcastAdapter: no adapter for source=%s", source)
            return None
        return adapter.get_reading(source)

    def register(self, source: str, adapter: Any) -> None:
        """Register an adapter for a source name."""
        self._adapters[source] = adapter

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "CompositeNowcastAdapter":
        """Build a composite adapter with all enabled sources wired up.

        Reads FRED API key from config (optional).  Cleveland Fed and
        Atlanta Fed adapters require no credentials.

        Args:
            config: Merged config dict.

        Returns:
            CompositeNowcastAdapter with adapters for all three sources.
        """
        fred_key = config.get("nowcast", {}).get("fred_api_key")

        adapters: Dict[str, Any] = {
            "cleveland_fed_cpi": ClevelandFedAdapter(),
            "atlanta_fed_gdp": AtlantaFedAdapter(),
            "fred_payrolls": FredAdapter(
                series_id="PAYEMS",
                source_name="fred_payrolls",
                indicator_name="payrolls",
                unit="thousands",
                api_key=fred_key,
            ),
        }

        return cls(adapters=adapters)


# ── Parsing helpers ──────────────────────────────────────────────────

def _try_float(s: Any) -> Optional[float]:
    """Try to parse s as float.  Returns None on failure."""
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return None


def _parse_float_any(
    row: Dict[str, Any], candidates: list
) -> Optional[float]:
    """Try each candidate column name and return the first parseable float."""
    for name in candidates:
        # Try exact match, then case-insensitive prefix match
        val = row.get(name)
        if val is not None:
            f = _try_float(val)
            if f is not None:
                return f
        # Case-insensitive scan
        for k, v in row.items():
            if k.strip().lower().startswith(name.lower()):
                f = _try_float(v)
                if f is not None:
                    return f
    return None


def _pick_any(row: Dict[str, Any], candidates: list) -> Optional[str]:
    """Return the first non-empty candidate column value as a string."""
    for name in candidates:
        val = row.get(name, "").strip()
        if val:
            return val
        for k, v in row.items():
            if k.strip().lower() == name.lower() and str(v).strip():
                return str(v).strip()
    return None
