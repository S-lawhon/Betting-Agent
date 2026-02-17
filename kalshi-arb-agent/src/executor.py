"""Order execution: places orders, handles fills, retries, and paper trading."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.kalshi_client import KalshiClient
from src.logger_setup import get_logger
from src.utils import TradeOpportunity, TradeRecord, utcnow

logger = get_logger("executor")

TRADE_LOG_PATH = os.path.join("data", "trade_log.json")


class Executor:
    """Handles order placement, confirmation, and paper trading simulation."""

    def __init__(
        self,
        kalshi_client: KalshiClient,
        mode: str = "paper",
        order_type: str = "limit",
        limit_offset_cents: int = 1,
        max_slippage_cents: int = 2,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 5.0,
        trade_log_path: str = TRADE_LOG_PATH,
    ):
        self.kalshi = kalshi_client
        self.mode = mode
        self.order_type = order_type
        self.limit_offset_cents = limit_offset_cents
        self.max_slippage_cents = max_slippage_cents
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay_seconds
        self.trade_log_path = trade_log_path

        self._pending_orders: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Execute a trade
    # ------------------------------------------------------------------

    async def execute_trade(
        self, opportunity: TradeOpportunity
    ) -> Optional[TradeRecord]:
        """Execute a trade for the given opportunity.

        In paper mode, simulates the fill and logs it.
        In live mode, places a real order on Kalshi.

        Returns:
            TradeRecord on success, None on failure.
        """
        mm = opportunity.matched_market
        km = mm.kalshi_market
        side = opportunity.side
        size_usd = opportunity.recommended_size_usd

        if size_usd <= 0:
            logger.warning(f"Skip {km.ticker}: position size is $0")
            return None

        trade_id = str(uuid.uuid4())[:12]

        # Compute contracts count (each contract is worth $1 at resolution)
        price = km.yes_price if side == "yes" else km.no_price
        if price <= 0:
            price = 0.50
        contracts = max(1, int(size_usd / price))

        # Time to event
        time_to_event = 0.0
        if km.close_time:
            delta = (km.close_time - utcnow()).total_seconds()
            time_to_event = max(0, delta / 3600.0)

        if self.mode == "paper":
            return await self._paper_trade(
                trade_id, opportunity, contracts, price, time_to_event
            )
        else:
            return await self._live_trade(
                trade_id, opportunity, contracts, price, time_to_event
            )

    # ------------------------------------------------------------------
    # Paper trading
    # ------------------------------------------------------------------

    async def _paper_trade(
        self,
        trade_id: str,
        opportunity: TradeOpportunity,
        contracts: int,
        fill_price: float,
        time_to_event: float,
    ) -> TradeRecord:
        """Simulate a fill at current market price."""
        mm = opportunity.matched_market
        km = mm.kalshi_market

        record = TradeRecord(
            trade_id=trade_id,
            timestamp=utcnow().isoformat(),
            ticker=km.ticker,
            sport=mm.sportsbook_event.sport,
            market_type=mm.match_type,
            teams=f"{mm.sportsbook_event.home_team} vs {mm.sportsbook_event.away_team}",
            event_time=mm.sportsbook_event.commence_time.isoformat(),
            side=opportunity.side,
            kalshi_price_at_entry=fill_price,
            sharp_prob_at_entry=mm.sharp_probability,
            edge_at_entry=opportunity.edge_pct,
            position_size_usd=round(contracts * fill_price, 2),
            kalshi_volume=km.volume,
            num_books=mm.num_books_agreeing,
            time_to_event_hours=time_to_event,
            line_movement=mm.line_movement,
            order_type="paper",
            mode="paper",
            hour_of_day=utcnow().hour,
        )

        logger.info(
            f"[PAPER] Trade {trade_id}: BUY {contracts}x {opportunity.side.upper()} "
            f"on {km.ticker} @ {fill_price:.2f} "
            f"(edge={opportunity.edge_pct:.2f}%, EV={opportunity.expected_value:.4f})",
            extra={
                "trade_id": trade_id,
                "ticker": km.ticker,
                "side": opportunity.side,
                "action": "paper_trade",
                "edge": opportunity.edge_pct,
            },
        )

        self._append_trade_log(record)
        return record

    # ------------------------------------------------------------------
    # Live trading
    # ------------------------------------------------------------------

    async def _live_trade(
        self,
        trade_id: str,
        opportunity: TradeOpportunity,
        contracts: int,
        target_price: float,
        time_to_event: float,
    ) -> Optional[TradeRecord]:
        """Place a real order on Kalshi with retry logic."""
        mm = opportunity.matched_market
        km = mm.kalshi_market
        side = opportunity.side

        # Calculate limit price
        if self.order_type == "limit":
            # Place limit slightly better than current price
            offset = self.limit_offset_cents / 100.0
            if side == "yes":
                limit_price_cents = int((target_price + offset) * 100)
                limit_price_cents = min(limit_price_cents, 99)
            else:
                limit_price_cents = int(((1 - target_price) + offset) * 100)
                limit_price_cents = min(limit_price_cents, 99)
        else:
            limit_price_cents = None

        last_error: Optional[Exception] = None
        order_id: Optional[str] = None

        for attempt in range(self.retry_attempts):
            try:
                # Check for slippage before placing
                if attempt > 0:
                    current = await self.kalshi.get_market(km.ticker)
                    market_data = current.get("market", {})
                    current_price = (market_data.get("yes_bid", 0) or 0) / 100.0
                    if current_price > 1:
                        current_price = current_price / 100.0
                    slippage = abs(current_price - target_price) * 100
                    if slippage > self.max_slippage_cents:
                        logger.warning(
                            f"Slippage too high ({slippage:.1f}c > {self.max_slippage_cents}c) "
                            f"on {km.ticker}. Cancelling.",
                            extra={"ticker": km.ticker, "action": "slippage_cancel"},
                        )
                        return None

                # Place the order
                kwargs: dict[str, Any] = {
                    "ticker": km.ticker,
                    "side": side,
                    "action": "buy",
                    "order_type": self.order_type,
                    "count": contracts,
                }
                if self.order_type == "limit" and limit_price_cents:
                    if side == "yes":
                        kwargs["yes_price"] = limit_price_cents
                    else:
                        kwargs["no_price"] = limit_price_cents

                result = await self.kalshi.place_order(**kwargs)
                order_id = result.get("order", {}).get("order_id")

                if not order_id:
                    logger.error(f"No order_id in response: {result}")
                    continue

                logger.info(
                    f"[LIVE] Order placed: {order_id} — BUY {contracts}x "
                    f"{side.upper()} on {km.ticker}",
                    extra={
                        "order_id": order_id,
                        "ticker": km.ticker,
                        "action": "order_placed",
                    },
                )

                # Wait for fill
                filled = await self._wait_for_fill(order_id, timeout_seconds=30)
                if filled:
                    fill_price = filled.get("average_price", target_price * 100) / 100.0
                    record = TradeRecord(
                        trade_id=trade_id,
                        timestamp=utcnow().isoformat(),
                        ticker=km.ticker,
                        sport=mm.sportsbook_event.sport,
                        market_type=mm.match_type,
                        teams=f"{mm.sportsbook_event.home_team} vs {mm.sportsbook_event.away_team}",
                        event_time=mm.sportsbook_event.commence_time.isoformat(),
                        side=side,
                        kalshi_price_at_entry=fill_price,
                        sharp_prob_at_entry=mm.sharp_probability,
                        edge_at_entry=opportunity.edge_pct,
                        position_size_usd=round(contracts * fill_price, 2),
                        kalshi_volume=km.volume,
                        num_books=mm.num_books_agreeing,
                        time_to_event_hours=time_to_event,
                        line_movement=mm.line_movement,
                        order_type=self.order_type,
                        mode="live",
                        order_id=order_id,
                        hour_of_day=utcnow().hour,
                    )
                    self._append_trade_log(record)
                    return record
                else:
                    # Order not filled, cancel it
                    logger.warning(
                        f"Order {order_id} not filled within timeout, cancelling",
                        extra={"order_id": order_id, "action": "cancel_unfilled"},
                    )
                    try:
                        await self.kalshi.cancel_order(order_id)
                    except Exception:
                        pass
                    return None

            except Exception as exc:
                last_error = exc
                logger.error(
                    f"Order attempt {attempt + 1}/{self.retry_attempts} failed: {exc}",
                    extra={"ticker": km.ticker, "error": str(exc)},
                )
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)

        logger.error(
            f"All {self.retry_attempts} order attempts failed for {km.ticker}: {last_error}",
            extra={"ticker": km.ticker, "action": "order_failed"},
        )
        return None

    async def _wait_for_fill(
        self, order_id: str, timeout_seconds: float = 30
    ) -> Optional[dict[str, Any]]:
        """Poll order status until filled or timeout."""
        start = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start) < timeout_seconds:
            try:
                order_data = await self.kalshi.get_order(order_id)
                order = order_data.get("order", order_data)
                status = order.get("status", "")

                if status == "executed":
                    logger.info(f"Order {order_id} filled", extra={"order_id": order_id})
                    return order
                elif status in ("canceled", "cancelled", "expired"):
                    logger.info(
                        f"Order {order_id} {status}",
                        extra={"order_id": order_id},
                    )
                    return None
                elif status == "resting":
                    # Partial fill check
                    filled = order.get("filled_count", 0)
                    total = order.get("count", 0)
                    if filled > 0 and filled >= total:
                        return order

            except Exception as exc:
                logger.debug(f"Error checking order {order_id}: {exc}")

            await asyncio.sleep(2.0)

        return None

    # ------------------------------------------------------------------
    # Settled positions
    # ------------------------------------------------------------------

    async def check_settled_positions(
        self,
    ) -> list[tuple[TradeRecord, str, float]]:
        """Check for settled positions and determine outcomes.

        Returns:
            List of (original_trade, outcome, pnl) tuples.
        """
        settled: list[tuple[TradeRecord, str, float]] = []

        try:
            positions = await self.kalshi.get_positions()
        except Exception as exc:
            logger.error(f"Failed to fetch positions for settlement check: {exc}")
            return settled

        for pos in positions:
            ticker = pos.get("ticker", "")
            result = pos.get("result")  # "yes", "no", or None
            if result is None:
                continue

            # Find the original trade record
            trade = self._find_trade_by_ticker(ticker)
            if not trade:
                continue

            # Calculate P&L
            if trade.side == result:
                # Won
                payout = (1.0 - trade.kalshi_price_at_entry) * trade.position_size_usd / trade.kalshi_price_at_entry
                # Subtract fee on profit
                fee = payout * 0.07
                pnl = payout - fee
                outcome = "win"
            else:
                # Lost
                pnl = -trade.position_size_usd
                outcome = "loss"

            trade.outcome = outcome
            trade.pnl = round(pnl, 2)
            settled.append((trade, outcome, pnl))

            logger.info(
                f"Position settled: {ticker} — {outcome.upper()} "
                f"(PnL: ${pnl:.2f})",
                extra={
                    "ticker": ticker,
                    "pnl": pnl,
                    "action": "position_settled",
                },
            )

        return settled

    def _find_trade_by_ticker(self, ticker: str) -> Optional[TradeRecord]:
        """Find the most recent unsettled trade for a ticker."""
        trades = self._load_trade_log()
        for trade_dict in reversed(trades):
            if (
                trade_dict.get("ticker") == ticker
                and trade_dict.get("outcome") is None
            ):
                return TradeRecord(**{
                    k: v for k, v in trade_dict.items()
                    if k in TradeRecord.__dataclass_fields__
                })
        return None

    # ------------------------------------------------------------------
    # Trade log persistence
    # ------------------------------------------------------------------

    def _append_trade_log(self, record: TradeRecord) -> None:
        """Append a trade record to the JSON trade log."""
        trades = self._load_trade_log()

        # Convert to dict
        trade_dict = {
            "trade_id": record.trade_id,
            "timestamp": record.timestamp,
            "ticker": record.ticker,
            "sport": record.sport,
            "market_type": record.market_type,
            "teams": record.teams,
            "event_time": record.event_time,
            "side": record.side,
            "kalshi_price_at_entry": record.kalshi_price_at_entry,
            "sharp_prob_at_entry": record.sharp_prob_at_entry,
            "edge_at_entry": record.edge_at_entry,
            "position_size_usd": record.position_size_usd,
            "kalshi_volume": record.kalshi_volume,
            "num_books": record.num_books,
            "time_to_event_hours": record.time_to_event_hours,
            "line_movement": record.line_movement,
            "order_type": record.order_type,
            "mode": record.mode,
            "outcome": record.outcome,
            "pnl": record.pnl,
            "order_id": record.order_id,
            "hour_of_day": record.hour_of_day,
        }
        trades.append(trade_dict)

        os.makedirs(os.path.dirname(self.trade_log_path) or ".", exist_ok=True)
        with open(self.trade_log_path, "w") as f:
            json.dump(trades, f, indent=2, default=str)

    def _load_trade_log(self) -> list[dict]:
        """Load the trade log from disk."""
        if not os.path.exists(self.trade_log_path):
            return []
        try:
            with open(self.trade_log_path, "r") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            return []

    def get_trade_count_for_ticker(self, ticker: str) -> int:
        """Check how many times we've traded this ticker (idempotency check)."""
        trades = self._load_trade_log()
        return sum(1 for t in trades if t.get("ticker") == ticker and t.get("outcome") is None)
