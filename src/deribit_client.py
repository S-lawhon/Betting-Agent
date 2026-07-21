"""
src/deribit_client.py
─────────────────────
Deribit options chain API client.

Fetches BTC (and ETH) options data from Deribit's public API:
  - Full options chain (all strikes/expiries)
  - Individual instrument orderbooks
  - Implied volatility surface construction

Uses Deribit's public (unauthenticated) API — no API key required for
market data.  Authentication is only needed for trading (Phase 2).

API docs: https://docs.deribit.com/
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from src.constants import HTTP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

DERIBIT_BASE_URL = "https://www.deribit.com/api/v2"
DERIBIT_TESTNET_URL = "https://test.deribit.com/api/v2"

# Rate limiting: Deribit allows ~20 req/s unauthenticated
_MIN_REQUEST_INTERVAL = 0.06  # 60ms between requests


@dataclass(frozen=True)
class OptionInstrument:
    """A single options instrument from Deribit."""
    instrument_name: str        # e.g. "BTC-28MAR26-100000-C"
    underlying: str             # e.g. "BTC"
    strike: float               # e.g. 100000.0
    expiry_ts: int              # Unix timestamp of expiration
    expiry_str: str             # e.g. "28MAR26"
    option_type: str            # "call" or "put"
    mark_price_usd: float      # Mark price in USD
    mark_iv: float              # Mark implied volatility (annualised, 0-1 scale)
    bid_price_usd: float        # Best bid in USD
    ask_price_usd: float        # Best ask in USD
    open_interest: float        # Open interest in contracts
    volume_24h: float           # 24h volume in contracts
    delta: float                # Option delta
    gamma: float                # Option gamma
    vega: float                 # Option vega
    theta: float                # Option theta
    underlying_price: float     # Current BTC price (from index)


@dataclass
class OptionsChain:
    """Complete options chain for a single expiry."""
    underlying: str
    expiry_str: str
    expiry_ts: int
    underlying_price: float
    instruments: List[OptionInstrument] = field(default_factory=list)

    @property
    def calls(self) -> List[OptionInstrument]:
        return [i for i in self.instruments if i.option_type == "call"]

    @property
    def puts(self) -> List[OptionInstrument]:
        return [i for i in self.instruments if i.option_type == "put"]

    @property
    def strikes(self) -> List[float]:
        return sorted(set(i.strike for i in self.instruments))

    def get_call(self, strike: float) -> Optional[OptionInstrument]:
        for i in self.instruments:
            if i.option_type == "call" and i.strike == strike:
                return i
        return None

    def get_put(self, strike: float) -> Optional[OptionInstrument]:
        for i in self.instruments:
            if i.option_type == "put" and i.strike == strike:
                return i
        return None


class DeribitClient:
    """Deribit public API client for options market data.

    Fetches options chains, orderbooks, and Greeks from Deribit's
    unauthenticated API.  No API key required for read-only data.

    Usage:
        client = DeribitClient()
        chains = client.get_all_chains("BTC")
        for chain in chains:
            print(f"{chain.expiry_str}: {len(chain.instruments)} instruments")
    """

    def __init__(
        self,
        base_url: str = DERIBIT_BASE_URL,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        session: Optional[requests.Session] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))
        self._last_request_time = 0.0

    def _request(self, method: str, params: Optional[Dict] = None) -> Any:
        """Make a rate-limited GET request to Deribit API."""
        # Rate limiting
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        url = f"{self.base_url}/{method}"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            self._last_request_time = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                return data["result"]
            return data
        except requests.RequestException as exc:
            logger.error("deribit_client: %s failed: %s", method, exc)
            raise

    # ── Index / Spot ──────────────────────────────────────────────────

    def get_index_price(self, currency: str = "BTC") -> Optional[float]:
        """Get current index price for a currency.

        Returns:
            Current BTC (or ETH) index price in USD, or None on error.
        """
        try:
            result = self._request(
                "public/get_index_price",
                {"index_name": f"{currency.lower()}_usd"},
            )
            return float(result.get("index_price", 0))
        except Exception as exc:
            logger.error("deribit_client: get_index_price failed: %s", exc)
            return None

    # ── Instruments ───────────────────────────────────────────────────

    def get_instruments(
        self,
        currency: str = "BTC",
        kind: str = "option",
        expired: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get all active instruments for a currency.

        Returns raw instrument dicts from Deribit API.
        """
        try:
            return self._request(
                "public/get_instruments",
                {
                    "currency": currency.upper(),
                    "kind": kind,
                    "expired": str(expired).lower(),
                },
            )
        except Exception as exc:
            logger.error("deribit_client: get_instruments failed: %s", exc)
            return []

    # ── Options Chain (Full) ──────────────────────────────────────────

    def get_all_chains(self, currency: str = "BTC") -> List[OptionsChain]:
        """Fetch complete options chain grouped by expiry.

        Makes 2 API calls:
          1. get_instruments — list all active option instruments
          2. get_book_summary_by_currency — batch prices/greeks for all

        Returns:
            List of OptionsChain, one per expiry date, sorted by expiry.
        """
        # Step 1: Get all instruments
        instruments = self.get_instruments(currency, kind="option")
        if not instruments:
            return []

        # Step 2: Get book summaries (prices + greeks in one call)
        try:
            summaries = self._request(
                "public/get_book_summary_by_currency",
                {"currency": currency.upper(), "kind": "option"},
            )
        except Exception as exc:
            logger.error("deribit_client: get_book_summary failed: %s", exc)
            return []

        # Index price (from any summary entry)
        underlying_price = 0.0
        if summaries:
            underlying_price = float(summaries[0].get("underlying_price", 0))

        # Build lookup: instrument_name → summary
        summary_map: Dict[str, Dict] = {}
        for s in summaries:
            summary_map[s.get("instrument_name", "")] = s

        # Step 3: Group by expiry
        expiry_groups: Dict[str, List[OptionInstrument]] = {}
        expiry_ts_map: Dict[str, int] = {}

        for inst in instruments:
            name = inst.get("instrument_name", "")
            summary = summary_map.get(name, {})

            # Parse instrument details
            strike = float(inst.get("strike", 0))
            expiry_ts = int(inst.get("expiration_timestamp", 0)) // 1000
            option_type = inst.get("option_type", "").lower()

            # Parse expiry string from instrument name
            # Format: BTC-28MAR26-100000-C
            parts = name.split("-")
            expiry_str = parts[1] if len(parts) >= 4 else ""

            # Get price data from summary
            mark_price_btc = float(summary.get("mark_price", 0) or 0)
            bid_price_btc = float(summary.get("bid_price", 0) or 0)
            ask_price_btc = float(summary.get("ask_price", 0) or 0)
            mark_iv = float(summary.get("mark_iv", 0) or 0) / 100.0  # Convert to 0-1
            ul_price = float(summary.get("underlying_price", 0) or underlying_price)

            # Convert BTC prices to USD
            mark_price_usd = mark_price_btc * ul_price
            bid_price_usd = bid_price_btc * ul_price
            ask_price_usd = ask_price_btc * ul_price

            # Greeks
            greeks = summary.get("greeks", {}) or {}

            option = OptionInstrument(
                instrument_name=name,
                underlying=currency.upper(),
                strike=strike,
                expiry_ts=expiry_ts,
                expiry_str=expiry_str,
                option_type=option_type,
                mark_price_usd=mark_price_usd,
                mark_iv=mark_iv,
                bid_price_usd=bid_price_usd,
                ask_price_usd=ask_price_usd,
                open_interest=float(summary.get("open_interest", 0) or 0),
                volume_24h=float(summary.get("volume", 0) or 0),
                delta=float(greeks.get("delta", 0) or 0),
                gamma=float(greeks.get("gamma", 0) or 0),
                vega=float(greeks.get("vega", 0) or 0),
                theta=float(greeks.get("theta", 0) or 0),
                underlying_price=ul_price,
            )

            if expiry_str not in expiry_groups:
                expiry_groups[expiry_str] = []
                expiry_ts_map[expiry_str] = expiry_ts
            expiry_groups[expiry_str].append(option)

        # Build OptionsChain objects
        chains = []
        for exp_str in sorted(expiry_groups, key=lambda k: expiry_ts_map.get(k, 0)):
            chain = OptionsChain(
                underlying=currency.upper(),
                expiry_str=exp_str,
                expiry_ts=expiry_ts_map[exp_str],
                underlying_price=underlying_price,
                instruments=expiry_groups[exp_str],
            )
            chains.append(chain)

        logger.info(
            "deribit_client: fetched %d expiries, %d total instruments for %s",
            len(chains),
            sum(len(c.instruments) for c in chains),
            currency,
        )
        return chains

    def get_chain_for_expiry(
        self, currency: str, expiry_str: str
    ) -> Optional[OptionsChain]:
        """Get options chain for a specific expiry.

        Args:
            currency: "BTC" or "ETH"
            expiry_str: e.g. "28MAR26" (Deribit format)

        Returns:
            OptionsChain for matching expiry, or None.
        """
        chains = self.get_all_chains(currency)
        for chain in chains:
            if chain.expiry_str.upper() == expiry_str.upper():
                return chain
        return None

    # ── Single Instrument ─────────────────────────────────────────────

    def get_orderbook(
        self, instrument_name: str, depth: int = 5
    ) -> Optional[Dict[str, Any]]:
        """Get orderbook for a specific instrument."""
        try:
            return self._request(
                "public/get_order_book",
                {"instrument_name": instrument_name, "depth": depth},
            )
        except Exception as exc:
            logger.error(
                "deribit_client: get_orderbook(%s) failed: %s",
                instrument_name, exc,
            )
            return None

    # ── Historical Volatility ─────────────────────────────────────────

    def get_historical_volatility(
        self, currency: str = "BTC"
    ) -> Optional[List[List[float]]]:
        """Get historical volatility data.

        Returns list of [timestamp_ms, volatility] pairs.
        """
        try:
            return self._request(
                "public/get_historical_volatility",
                {"currency": currency.upper()},
            )
        except Exception as exc:
            logger.error(
                "deribit_client: get_historical_volatility failed: %s", exc,
            )
            return None

    # ── Factory ───────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> "DeribitClient":
        """Build from config dict.

        Config keys (under 'deribit'):
          environment: "live" or "testnet"
          timeout: HTTP timeout in seconds
        """
        deribit_cfg = config.get("deribit", {})
        env = deribit_cfg.get("environment", "live")
        base_url = (
            DERIBIT_TESTNET_URL if env == "testnet" else DERIBIT_BASE_URL
        )
        timeout = deribit_cfg.get("timeout", HTTP_TIMEOUT_SECONDS)
        return cls(base_url=base_url, timeout=timeout)
