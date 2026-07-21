"""
src/forecastex_client.py
────────────────────────
IB ForecastEx connector via ib_insync.

Key constraints:
  - BUY only (no SELL; buy opposing contract to exit)
  - secType="OPT", exchange="FORECASTEX"
  - right="CALL" for YES, "PUT" for NO
  - No symbol discovery API (must know symbols in advance)
  - 3.14% APY coupon accrues on daily closing value
  - 0% commission
  - Paper mode default (injectable IB instance for testing)
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForecastExContract:
    """Normalised ForecastEx contract."""
    symbol: str
    description: str
    expiry: str
    exchange: str = "FORECASTEX"
    sec_type: str = "OPT"
    right: str = "CALL"  # "CALL" (YES) or "PUT" (NO)
    strike: float = 0.0
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    last_price: Optional[float] = None


@dataclass(frozen=True)
class ForecastExOrderResult:
    """Result of placing an order."""
    success: bool
    order_id: Optional[int]
    error: Optional[str]
    fill_price: Optional[float]


class ForecastExClient:
    """IB ForecastEx connector.

    Constructor takes an injectable IB instance (for testing) or
    creates one lazily.  Paper mode simulates orders without IB.
    """

    def __init__(
        self,
        ib=None,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 10,
        environment: str = "paper",
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.environment = environment
        self._ib = ib  # Injectable: IB() instance or mock
        self._connected = False

    def connect(self) -> bool:
        """Connect to IB TWS/Gateway.  No-op if already connected."""
        if self._ib is None:
            try:
                from ib_insync import IB
                self._ib = IB()
            except ImportError:
                logger.warning("ib_insync not installed; ForecastEx in offline mode")
                return False

        if not self._connected:
            try:
                if hasattr(self._ib, "isConnected") and self._ib.isConnected():
                    self._connected = True
                    return True
                self._ib.connect(self.host, self.port, clientId=self.client_id)
                self._connected = True
            except Exception as e:
                logger.error("ForecastEx connect failed: %s", e)
                return False
        return True

    def disconnect(self) -> None:
        """Disconnect from IB."""
        if self._ib and self._connected:
            try:
                self._ib.disconnect()
            except Exception as exc:
                logger.debug("ForecastEx disconnect cleanup: %s", exc)
            self._connected = False

    def get_price(self, symbol: str, right: str = "CALL") -> Optional[float]:
        """Get current price for a ForecastEx contract.

        Args:
            symbol: ForecastEx symbol (e.g. "FED.RATE.25JUN")
            right: "CALL" (YES) or "PUT" (NO)

        Returns:
            Midpoint price (0-1 scale) or None if unavailable.
        """
        if self.environment == "paper" and self._ib is None:
            return None

        if self._ib is None:
            return None

        try:
            contract = self._make_contract(symbol, right)
            self._ib.qualifyContracts(contract)
            ticker = self._ib.reqMktData(contract)

            # Wait for data (real IB needs sleep; mocks return immediately)
            if hasattr(self._ib, "sleep"):
                self._ib.sleep(2)

            mid = ticker.midpoint()
            if mid is not None and mid == mid:  # NaN check
                return float(mid)
            return None
        except Exception as e:
            logger.error("ForecastEx get_price failed: symbol=%s err=%s", symbol, e)
            return None

    def place_order(
        self,
        symbol: str,
        right: str,
        quantity: int,
        limit_price: float,
    ) -> ForecastExOrderResult:
        """Place a BUY order.  ForecastEx only supports BUY.

        Args:
            symbol: ForecastEx symbol
            right: "CALL" (YES) or "PUT" (NO)
            quantity: Number of contracts
            limit_price: Limit price (0-1)

        Returns:
            ForecastExOrderResult
        """
        if self.environment == "paper":
            logger.info(
                "ForecastEx paper order: symbol=%s right=%s qty=%d price=%s",
                symbol, right, quantity, limit_price,
            )
            return ForecastExOrderResult(
                success=True,
                order_id=None,
                error=None,
                fill_price=limit_price,
            )

        if self._ib is None:
            return ForecastExOrderResult(
                success=False, order_id=None,
                error="No IB connection", fill_price=None,
            )

        try:
            contract = self._make_contract(symbol, right)
            order = self._make_limit_order(quantity, limit_price)
            trade = self._ib.placeOrder(contract, order)

            if hasattr(self._ib, "sleep"):
                self._ib.sleep(5)

            status = getattr(trade, "orderStatus", None)
            if status and getattr(status, "status", "") in ("Filled", "Submitted", "PreSubmitted"):
                return ForecastExOrderResult(
                    success=True,
                    order_id=getattr(trade.order, "orderId", None),
                    error=None,
                    fill_price=getattr(status, "avgFillPrice", None) or limit_price,
                )
            return ForecastExOrderResult(
                success=False,
                order_id=None,
                error=getattr(status, "status", "Unknown") if status else "No status",
                fill_price=None,
            )
        except Exception as e:
            logger.error("ForecastEx place_order failed: %s", e)
            return ForecastExOrderResult(
                success=False, order_id=None, error=str(e), fill_price=None,
            )

    def _make_contract(self, symbol: str, right: str) -> Any:
        """Build an IB contract object for ForecastEx."""
        if self._ib is None:
            raise RuntimeError("No IB connection")

        try:
            from ib_insync import Contract
            return Contract(
                symbol=symbol,
                secType="OPT",
                exchange="FORECASTEX",
                currency="USD",
                right=right,
            )
        except ImportError:
            # Fallback: create a simple mock-compatible object
            class _Contract:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
            return _Contract(
                symbol=symbol, secType="OPT",
                exchange="FORECASTEX", currency="USD", right=right,
            )

    def _make_limit_order(self, quantity: int, limit_price: float) -> Any:
        """Build an IB LimitOrder for BUY."""
        try:
            from ib_insync import LimitOrder
            return LimitOrder("BUY", quantity, limit_price)
        except ImportError:
            class _Order:
                def __init__(self, action, qty, price):
                    self.action = action
                    self.totalQuantity = qty
                    self.lmtPrice = price
                    self.orderId = None
            return _Order("BUY", quantity, limit_price)

    @classmethod
    def from_config(cls, config: dict) -> "ForecastExClient":
        """Build from YAML config dict."""
        ib_cfg = config.get("interactive_brokers", {})
        return cls(
            host=ib_cfg.get("host", "127.0.0.1"),
            port=ib_cfg.get("port", 7497),
            client_id=ib_cfg.get("client_id", 10),
            environment=ib_cfg.get("environment", "paper"),
        )
