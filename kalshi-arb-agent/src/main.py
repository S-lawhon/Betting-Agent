"""Main entry point: orchestrates the Kalshi arbitrage agent loop."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Any

import yaml
from dotenv import load_dotenv

from src.edge_calculator import evaluate_opportunity
from src.executor import Executor
from src.kalshi_client import KalshiClient
from src.learner import Learner
from src.logger_setup import get_logger, setup_logging
from src.matcher import MarketMatcher
from src.odds_client import OddsClient
from src.risk_manager import RiskManager
from src.utils import TradeOpportunity, utcnow

# Load .env before anything else
load_dotenv()

logger = get_logger("main")


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    """Load configuration from YAML file."""
    if not os.path.exists(path):
        logger.error(f"Config file not found: {path}")
        sys.exit(1)

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    logger.info(f"Config loaded from {path}")
    return config


def validate_env() -> None:
    """Validate that required environment variables are set."""
    required = ["KALSHI_API_KEY_ID", "ODDS_API_KEY"]
    missing = [v for v in required if not os.environ.get(v)]

    if missing:
        logger.error(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill in your keys."
        )
        sys.exit(1)


def confirm_live_mode() -> bool:
    """Prompt user to confirm live trading mode."""
    print("\n" + "=" * 60)
    print("  WARNING: LIVE TRADING MODE")
    print("  Real money will be used for trades!")
    print("=" * 60)
    print("\nAre you sure you want to enable live trading?")
    response = input("Type 'YES I UNDERSTAND' to confirm: ").strip()
    return response == "YES I UNDERSTAND"


class Agent:
    """Main agent class that orchestrates the scanning/trading loop."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.running = False

        trading = config.get("trading", {})
        filters = config.get("filters", {})
        odds_cfg = config.get("odds_api", {})
        learning_cfg = config.get("learning", {})
        execution_cfg = config.get("execution", {})
        scanning_cfg = config.get("scanning", {})

        self.mode = trading.get("mode", "paper")
        self.min_edge = trading.get("min_edge_pct", 3.0)
        self.max_edge = trading.get("max_edge_pct", 25.0)
        self.scan_interval = scanning_cfg.get("interval_seconds", 60)

        # Initialize components
        self.kalshi = KalshiClient(
            env=os.environ.get("KALSHI_ENV", "demo"),
            rate_limit_per_min=scanning_cfg.get("kalshi_rate_limit_per_min", 30),
        )

        self.odds = OddsClient(
            regions=odds_cfg.get("regions", ["us", "us2"]),
            markets=odds_cfg.get("markets", ["h2h", "spreads", "totals"]),
            bookmakers_priority=odds_cfg.get("bookmakers_priority", []),
        )

        self.matcher = MarketMatcher(
            odds_client=self.odds,
            event_horizon_hours=filters.get("event_horizon_hours", 168),
        )

        self.risk_mgr = RiskManager(
            max_position_size_usd=trading.get("max_position_size_usd", 500),
            max_total_exposure_usd=trading.get("max_total_exposure_usd", 5000),
            max_positions=trading.get("max_positions", 20),
            max_daily_loss_usd=trading.get("max_daily_loss_usd", 500),
            cooldown_after_loss_minutes=trading.get("cooldown_after_loss_minutes", 30),
        )

        self.executor = Executor(
            kalshi_client=self.kalshi,
            mode=self.mode,
            order_type=execution_cfg.get("order_type", "limit"),
            limit_offset_cents=execution_cfg.get("limit_offset_cents", 1),
            max_slippage_cents=execution_cfg.get("max_slippage_cents", 2),
            retry_attempts=execution_cfg.get("retry_attempts", 3),
            retry_delay_seconds=execution_cfg.get("retry_delay_seconds", 5),
        )

        self.learner = Learner(
            enabled=learning_cfg.get("enabled", True),
            method=learning_cfg.get("edge_adjustment_method", "bayesian"),
            min_trades_before_adjust=learning_cfg.get("min_trades_before_adjust", 50),
            recalibrate_interval_hours=learning_cfg.get("recalibrate_interval_hours", 24),
        )

        self.sports = filters.get("sports", ["basketball_nba"])
        self.min_volume = trading.get("min_market_volume", 100000)
        self.min_books = filters.get("min_sportsbook_consensus", 3)
        self.exclude_live = filters.get("exclude_live_markets", True)
        self.batch_size = scanning_cfg.get("batch_size", 50)

        # Stats
        self.scan_count = 0
        self.opportunities_found = 0
        self.trades_executed = 0

    async def run(self) -> None:
        """Main agent loop."""
        self.running = True
        logger.info(
            f"Agent starting in {self.mode.upper()} mode. "
            f"Environment: {os.environ.get('KALSHI_ENV', 'demo')}. "
            f"Sports: {self.sports}"
        )

        if self.mode == "live":
            if not confirm_live_mode():
                logger.info("Live mode not confirmed. Exiting.")
                return

        try:
            while self.running:
                try:
                    await self._scan_cycle()
                except Exception as exc:
                    logger.error(f"Error in scan cycle: {exc}", exc_info=True)

                # Check if learner needs recalibration
                if self.learner.should_recalibrate():
                    logger.info("Recalibrating learner...")
                    adjustments = self.learner.recalibrate()
                    if adjustments:
                        logger.info(f"New edge adjustments: {adjustments}")

                logger.info(
                    f"Scan #{self.scan_count} complete. "
                    f"Next scan in {self.scan_interval}s. "
                    f"Opportunities: {self.opportunities_found}, "
                    f"Trades: {self.trades_executed}"
                )

                await asyncio.sleep(self.scan_interval)

        except asyncio.CancelledError:
            logger.info("Agent cancelled")
        finally:
            await self._shutdown()

    async def _scan_cycle(self) -> None:
        """Execute one complete scan cycle."""
        self.scan_count += 1
        cycle_start = utcnow()

        logger.info(f"=== Scan cycle #{self.scan_count} starting ===")

        # Step 1: Fetch Kalshi markets
        logger.info("Fetching Kalshi markets...")
        kalshi_markets = await self.kalshi.get_all_sports_markets(
            batch_size=self.batch_size
        )

        if not kalshi_markets:
            logger.info("No Kalshi markets found")
            return

        # Filter by volume
        kalshi_markets = [
            m for m in kalshi_markets if m.volume >= self.min_volume
        ]
        logger.info(f"After volume filter: {len(kalshi_markets)} markets")

        # Step 2: Fetch sportsbook odds
        logger.info("Fetching sportsbook odds...")
        sportsbook_events = await self.odds.get_all_odds(self.sports)

        total_events = sum(len(v) for v in sportsbook_events.values())
        logger.info(f"Fetched {total_events} sportsbook events")

        if total_events == 0:
            logger.info("No sportsbook events found")
            return

        # Step 3: Match markets
        logger.info("Matching markets...")
        matched = self.matcher.match_markets(kalshi_markets, sportsbook_events)
        logger.info(f"Matched {len(matched)} markets")

        # Step 4: Calculate edge and find opportunities
        opportunities: list[TradeOpportunity] = []

        for mm in matched:
            # Get adjusted min edge from learner
            adjusted_min_edge = self.learner.get_adjusted_min_edge(
                mm.sportsbook_event.sport, mm.match_type, self.min_edge
            )

            opp = evaluate_opportunity(
                mm,
                min_edge_pct=adjusted_min_edge,
                max_edge_pct=self.max_edge,
                min_books=self.min_books,
            )

            if opp:
                # Calculate position size
                size = self.risk_mgr.calculate_position_size(opp)
                opp.recommended_size_usd = size
                if size > 0:
                    opportunities.append(opp)

        self.opportunities_found += len(opportunities)
        logger.info(f"Found {len(opportunities)} trading opportunities")

        if not opportunities:
            return

        # Step 5: Rank by EV
        opportunities.sort(key=lambda o: o.expected_value, reverse=True)

        # Step 6: Execute top opportunities
        for opp in opportunities:
            km = opp.matched_market.kalshi_market

            # Idempotency check
            existing = self.executor.get_trade_count_for_ticker(km.ticker)
            if existing > 0:
                logger.debug(f"Skip {km.ticker}: already have active trade")
                continue

            # Risk check
            can_trade, reason = self.risk_mgr.can_trade(opp)
            if not can_trade:
                logger.info(
                    f"Risk blocked {km.ticker}: {reason}",
                    extra={"ticker": km.ticker, "reason": reason},
                )
                continue

            # Execute
            logger.info(
                f"Executing trade: {km.ticker} {opp.side.upper()} "
                f"edge={opp.edge_pct:.2f}% size=${opp.recommended_size_usd:.2f}"
            )

            record = await self.executor.execute_trade(opp)
            if record:
                self.trades_executed += 1
                self._send_alert(opp, record)

        # Step 7: Check settled positions and update learner
        settled = await self.executor.check_settled_positions()
        for trade, outcome, pnl in settled:
            self.risk_mgr.record_trade_result(trade)
            self.learner.record_outcome(trade)

        # Step 8: Update risk state
        try:
            positions = await self.kalshi.get_positions()
            balance_data = await self.kalshi.get_balance()
            balance = balance_data.get("balance", 0) / 100.0
            self.risk_mgr.update_positions(positions, balance)
        except Exception as exc:
            logger.debug(f"Failed to update risk state: {exc}")

        # Step 9: Save market snapshot periodically
        if self.scan_count % 10 == 0:
            self._save_snapshot(kalshi_markets, matched, opportunities)

        elapsed = (utcnow() - cycle_start).total_seconds()
        logger.info(f"Scan cycle #{self.scan_count} completed in {elapsed:.1f}s")

    def _send_alert(
        self, opportunity: TradeOpportunity, record: Any
    ) -> None:
        """Send alert for executed trade."""
        alerts = self.config.get("alerts", {})
        if not alerts.get("enabled", False):
            return
        if not alerts.get("alert_on_trade", True):
            return

        mm = opportunity.matched_market
        msg = (
            f"Trade executed: {mm.kalshi_market.ticker} "
            f"{opportunity.side.upper()} "
            f"edge={opportunity.edge_pct:.2f}% "
            f"size=${opportunity.recommended_size_usd:.2f}"
        )

        method = alerts.get("method", "console")
        if method == "console":
            print(f"\n[ALERT] {msg}\n")
        elif method == "file":
            with open("alerts.log", "a") as f:
                f.write(f"{utcnow().isoformat()} {msg}\n")

        # Edge alert
        edge_threshold = alerts.get("alert_on_edge_gt", 8.0)
        if opportunity.edge_pct > edge_threshold:
            print(
                f"\n[HIGH EDGE ALERT] {mm.kalshi_market.ticker}: "
                f"{opportunity.edge_pct:.2f}% edge!\n"
            )

    def _save_snapshot(
        self, markets: Any, matched: Any, opportunities: Any
    ) -> None:
        """Save a periodic snapshot of market state."""
        try:
            snapshot_dir = os.path.join("data", "market_snapshots")
            os.makedirs(snapshot_dir, exist_ok=True)

            ts = utcnow().strftime("%Y%m%d_%H%M%S")
            snapshot = {
                "timestamp": utcnow().isoformat(),
                "scan_count": self.scan_count,
                "total_kalshi_markets": len(markets),
                "matched_markets": len(matched),
                "opportunities": len(opportunities),
                "risk_summary": self.risk_mgr.get_risk_summary(),
                "learner_stats": self.learner.get_stats(),
            }

            path = os.path.join(snapshot_dir, f"snapshot_{ts}.json")
            with open(path, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)

        except Exception as exc:
            logger.debug(f"Failed to save snapshot: {exc}")

    async def _shutdown(self) -> None:
        """Clean shutdown."""
        logger.info("Shutting down agent...")
        self.learner._save_state()
        await self.kalshi.close()
        await self.odds.close()
        logger.info("Agent stopped.")

    def stop(self) -> None:
        """Signal the agent to stop."""
        self.running = False


async def main() -> None:
    """Entry point."""
    setup_logging()
    logger.info("Kalshi Arbitrage Agent starting...")

    validate_env()
    config = load_config()

    agent = Agent(config)

    # Handle signals for graceful shutdown
    loop = asyncio.get_event_loop()

    def handle_signal() -> None:
        logger.info("Received shutdown signal")
        agent.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
