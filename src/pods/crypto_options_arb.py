"""
src/pods/crypto_options_arb.py
──────────────────────────────
P-013: Kalshi-Deribit Crypto Options Arb pod.

Scans Kalshi's crypto binary event contracts and compares their prices
to implied probabilities derived from Deribit's BTC options chain.

Strategy:
  1. Fetch active Kalshi BTC contracts (daily/weekly/monthly/yearly)
  2. Fetch Deribit BTC options chain for matching expiries
  3. For each Kalshi contract, compute Deribit-implied fair probability
     using vertical spread replication and/or BSM N(d2)
  4. If edge > min_edge_pct → place directional bet on Kalshi

Phase 1 (current): Directional — use Deribit as pricing oracle, execute
only on Kalshi.  Deribit's options market is extremely efficient; when
it disagrees with Kalshi, Deribit is almost certainly closer to correct.

Phase 2 (future): Hedged — add Deribit vertical spread as delta-neutral
hedge for true cross-venue arb.

Fee model:
  Kalshi taker: roundup(0.07 × C × P × (1 - P))
  Kalshi maker: roundup(0.0175 × C × P × (1 - P))  (1/4 taker)
  At P=0.14: ~1.4% maker fee, ~5.4% taker fee
  At P=0.50: ~1.75% maker fee, ~7% taker fee
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.base_pod import BasePod, ScanResult
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

# Kalshi maker fee multiplier (limit orders via API)
KALSHI_MAKER_FEE_MULT = 0.0175
# Kalshi taker fee multiplier
KALSHI_TAKER_FEE_MULT = 0.07


@dataclass
class KalshiCryptoContract:
    """A parsed Kalshi crypto binary event contract."""
    ticker: str                  # e.g. "KXBTC-26MAR28-B87750"
    title: str                   # e.g. "Bitcoin above $87,750 on March 28?"
    strike: float                # e.g. 87750.0  (price threshold)
    expiry_date: Optional[str]   # e.g. "2026-03-28"
    contract_type: str           # "above" or "below"
    yes_price: float             # Current YES price (0-1)
    no_price: float              # Current NO price (0-1)
    yes_bid: Optional[float]     # Best YES bid
    yes_ask: Optional[float]     # Best YES ask
    category: str                # "daily", "weekly", "monthly", "yearly"
    underlying: str              # "BTC" or "ETH"


@dataclass
class CryptoArbSignal:
    """An arbitrage signal between Kalshi and Deribit-implied fair value."""
    contract: KalshiCryptoContract
    deribit_fair_prob: float     # Deribit-implied probability
    kalshi_prob: float           # Kalshi market price
    edge_pct: float              # (fair - venue) / venue
    side: str                    # "YES" or "NO" — which to buy on Kalshi
    buy_price: float             # Price to pay on Kalshi
    estimated_fee: float         # Kalshi maker fee as fraction of price
    net_edge_pct: float          # Edge after fees
    confidence: float            # Pricer confidence (0-1)
    method: str                  # Pricing method used


@register_pod("P-013")
class CryptoOptionsArbPod(BasePod):
    """P-013: Kalshi-Deribit Crypto Options Arb.

    Scan cycle:
      1. Fetch all active BTC crypto contracts from Kalshi
      2. Fetch Deribit BTC options chains
      3. For each Kalshi contract, find matching Deribit expiry
      4. Compute Deribit-implied probability for the contract's strike
      5. Compare to Kalshi price — if edge > threshold, generate signal
      6. Place maker limit order on Kalshi at or near fair value

    Uses CryptoOptionsPricer for probability computation.
    """

    # Kalshi crypto series tickers (used for efficient API filtering)
    _BTC_SERIES_TICKERS = (
        "KXBTC",       # BTC price range (base series)
        "KXBTCD",      # BTC daily price
        "KXBTCW",      # BTC weekly price
        "KXBTCM",      # BTC monthly price
        "KXBTCY",      # BTC yearly / end-of-year price
        "KXBTC15M",    # BTC 15-minute price
        "KXBTCMAX",    # BTC milestone (e.g. $100k)
    )

    # Broader prefix matching for any markets we encounter in bulk listing
    _BTC_TICKER_PREFIXES = (
        "KXBTC",       # Catches all BTC variants: KXBTC, KXBTCD, KXBTCW, etc.
    )

    # Map Kalshi expiry patterns to Deribit format
    # Kalshi: "2026-03-28" → Deribit: "28MAR26"
    _MONTH_ABBREV = {
        1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
        5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
        9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
    }

    def __init__(
        self,
        kalshi_client,
        deribit_client,
        pricer,
        edge_calculator=None,
        risk_manager=None,
        min_edge_pct: float = 0.12,
        min_net_edge_pct: float = 0.08,
        max_contracts_per_cycle: int = 3,
        kelly_multiplier: float = 0.15,
        use_maker_orders: bool = True,
        max_plausible_edge: float = 0.20,
        target_categories: Optional[List[str]] = None,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
        aggregate_risk=None,
        trade_store=None,
        min_kelly_fraction: Optional[float] = None,
    ):
        super().__init__(
            pod_id="P-013",
            pod_name="Kalshi-Deribit Crypto Options Arb",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/pods/P-013.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
            aggregate_risk=aggregate_risk,
            trade_store=trade_store,
            min_kelly_fraction=min_kelly_fraction,
        )
        self.kalshi_client = kalshi_client
        self.deribit_client = deribit_client
        self.pricer = pricer
        self.min_edge_pct = min_edge_pct
        self.min_net_edge_pct = min_net_edge_pct
        self.max_contracts_per_cycle = max_contracts_per_cycle
        self.kelly_multiplier = kelly_multiplier
        self.use_maker_orders = use_maker_orders
        self.target_categories = target_categories or [
            "weekly", "monthly", "yearly",
        ]
        self.max_plausible_edge = max_plausible_edge

        # Per-cycle loss limiter: stop placing after N consecutive losses
        # in the same calendar day.  Prevents runaway losses when the
        # pricing model is systematically wrong (e.g. daily IV mismatch).
        self.max_daily_losses: int = 5
        self._daily_loss_count: int = 0
        self._daily_loss_date: Optional[str] = None  # "YYYY-MM-DD"

        # Caches (refreshed each scan cycle)
        self._deribit_chains: Dict[str, Any] = {}  # expiry_str → OptionsChain
        self._btc_spot: Optional[float] = None

        # Log active configuration at startup so we can verify in the
        # journal that the correct thresholds are loaded.
        logger.info(
            "P-013 INIT: mode=%s categories=%s min_edge=%.1f%% "
            "min_net_edge=%.1f%% max_plausible=%.1f%% "
            "max_per_cycle=%d kelly_mult=%.2f maker=%s",
            mode, self.target_categories, min_edge_pct * 100,
            min_net_edge_pct * 100, max_plausible_edge * 100,
            max_contracts_per_cycle, kelly_multiplier, use_maker_orders,
        )

    def record_loss(self) -> None:
        """Called externally (e.g. by settlement) to record a loss.

        Tracks consecutive daily losses so scan_once can pause when
        the model is clearly miscalibrated for today's market.
        """
        today = self._now_fn().strftime("%Y-%m-%d")
        if self._daily_loss_date != today:
            self._daily_loss_date = today
            self._daily_loss_count = 0
        self._daily_loss_count += 1
        if self._daily_loss_count >= self.max_daily_losses:
            logger.warning(
                "P-013: %d losses today (%s) — circuit breaker active, "
                "skipping remaining signals this day",
                self._daily_loss_count, today,
            )

    def scan_once(self) -> List[ScanResult]:
        results: List[ScanResult] = []
        placed_count = 0

        # Daily loss circuit breaker
        today = self._now_fn().strftime("%Y-%m-%d")
        if self._daily_loss_date == today and self._daily_loss_count >= self.max_daily_losses:
            logger.info(
                "P-013: daily loss limit reached (%d losses on %s) — skipping scan",
                self._daily_loss_count, today,
            )
            return []

        # Reset counter on new day
        if self._daily_loss_date != today:
            self._daily_loss_date = today
            self._daily_loss_count = 0

        # 1. Refresh Deribit options data
        try:
            self._refresh_deribit_data()
        except Exception as e:
            logger.error("P-013: Deribit data refresh failed: %s", e)
            return []

        if not self._deribit_chains:
            logger.warning("P-013: no Deribit chains available")
            return []

        logger.info(
            "P-013: Deribit data loaded — %d expiries, BTC spot=$%.0f",
            len(self._deribit_chains),
            self._btc_spot or 0,
        )

        # 2. Fetch Kalshi BTC contracts
        try:
            contracts = self._fetch_kalshi_crypto_contracts()
        except Exception as e:
            logger.error("P-013: Kalshi contract fetch failed: %s", e)
            return []

        if not contracts:
            logger.info("P-013: no active Kalshi BTC contracts found")
            return []

        logger.info("P-013: scanning %d Kalshi BTC contracts", len(contracts))

        # 3. Evaluate each contract
        signals: List[CryptoArbSignal] = []

        for contract in contracts:
            try:
                signal = self._evaluate_contract(contract)
                if signal is not None:
                    signals.append(signal)
            except Exception as e:
                logger.debug(
                    "P-013: error evaluating %s: %s", contract.ticker, e,
                )

        # 4. Sort by net edge (best first) and take top N
        signals.sort(key=lambda s: s.net_edge_pct, reverse=True)
        logger.info(
            "P-013: %d signals found (of %d contracts)",
            len(signals), len(contracts),
        )

        for signal in signals:
            if placed_count >= self.max_contracts_per_cycle:
                break

            try:
                result = self._execute_signal(signal)
            except Exception as e:
                logger.warning(
                    "P-013: error executing signal for %s: %s",
                    signal.contract.ticker, e,
                )
                continue

            if result is not None:
                results.append(result)
                if result.action == "PLACED":
                    placed_count += 1

        logger.info(
            "P-013: cycle complete — %d contracts scanned, %d signals, %d placed",
            len(contracts), len(signals), placed_count,
        )
        return results

    # ── Deribit Data ──────────────────────────────────────────────────

    def _refresh_deribit_data(self) -> None:
        """Fetch fresh Deribit options chains and spot price."""
        self._btc_spot = self.deribit_client.get_index_price("BTC")
        chains = self.deribit_client.get_all_chains("BTC")
        self._deribit_chains = {c.expiry_str: c for c in chains}

    def _find_matching_chain(
        self, kalshi_expiry: Optional[str]
    ) -> Optional[Any]:
        """Find the Deribit options chain closest to a Kalshi expiry.

        Args:
            kalshi_expiry: Date string like "2026-03-28"

        Returns:
            Matching OptionsChain, or nearest available if no exact match.
        """
        if not kalshi_expiry or not self._deribit_chains:
            return None

        # Convert Kalshi date to Deribit format: "2026-03-28" → "28MAR26"
        try:
            dt = datetime.strptime(kalshi_expiry, "%Y-%m-%d")
            deribit_str = f"{dt.day}{self._MONTH_ABBREV[dt.month]}{str(dt.year)[2:]}"
        except (ValueError, KeyError):
            return None

        # Exact match
        if deribit_str in self._deribit_chains:
            return self._deribit_chains[deribit_str]

        # Find nearest expiry that's >= Kalshi expiry
        import time as _time
        target_ts = dt.replace(tzinfo=timezone.utc).timestamp()
        best_chain = None
        best_diff = float("inf")

        for chain in self._deribit_chains.values():
            diff = chain.expiry_ts - target_ts
            if diff >= 0 and diff < best_diff:
                best_diff = diff
                best_chain = chain

        # Allow up to 7 days mismatch for weekly/monthly contracts
        if best_chain and best_diff <= 7 * 86400:
            return best_chain

        return None

    # ── Kalshi Contracts ──────────────────────────────────────────────

    def _fetch_kalshi_crypto_contracts(self) -> List[KalshiCryptoContract]:
        """Fetch active Kalshi BTC binary contracts.

        Strategy:
          1. If the Kalshi client supports series_ticker filtering, query
             each known BTC series directly (efficient, fewer API calls).
          2. Otherwise, paginate all open markets and filter by prefix.

        Parses strike, expiry, and contract type from ticker and title.
        """
        if not hasattr(self.kalshi_client, "list_markets"):
            logger.debug("P-013: Kalshi client has no list_markets method")
            return []

        all_contracts: List[KalshiCryptoContract] = []
        seen_tickers: set = set()

        # --- Strategy 1: Query by series_ticker (efficient) ---
        # Check if list_markets accepts series_ticker
        import inspect
        sig = inspect.signature(self.kalshi_client.list_markets)
        supports_series = "series_ticker" in sig.parameters

        if supports_series:
            for series in self._BTC_SERIES_TICKERS:
                cursor: Optional[str] = None
                while True:
                    try:
                        resp = self.kalshi_client.list_markets(
                            status="open", limit=200, cursor=cursor,
                            series_ticker=series,
                        )
                    except Exception as e:
                        logger.debug(
                            "P-013: series_ticker query failed for %s: %s",
                            series, e,
                        )
                        break

                    page = resp.get("markets") or []
                    for m in page:
                        ticker = m.get("ticker", "")
                        if ticker in seen_tickers:
                            continue
                        seen_tickers.add(ticker)
                        contract = self._parse_kalshi_contract(m)
                        if contract is not None:
                            all_contracts.append(contract)

                    cursor = resp.get("cursor") or None
                    if not cursor or len(page) < 200:
                        break

            if all_contracts:
                logger.info(
                    "P-013: found %d BTC contracts via series_ticker queries",
                    len(all_contracts),
                )
                return all_contracts

            logger.debug(
                "P-013: series_ticker queries returned 0 contracts, "
                "falling back to prefix scan"
            )

        # --- Strategy 2: Bulk scan with prefix filtering (fallback) ---
        cursor = None
        pages_scanned = 0

        while True:
            try:
                resp = self.kalshi_client.list_markets(
                    status="open", limit=200, cursor=cursor,
                )
            except Exception as e:
                logger.error("P-013: Kalshi list_markets error: %s", e)
                break

            page = resp.get("markets") or []
            pages_scanned += 1
            for m in page:
                ticker = m.get("ticker", "")
                if ticker in seen_tickers:
                    continue
                if not any(ticker.startswith(p) for p in self._BTC_TICKER_PREFIXES):
                    continue
                seen_tickers.add(ticker)

                contract = self._parse_kalshi_contract(m)
                if contract is not None:
                    all_contracts.append(contract)

            cursor = resp.get("cursor") or None
            if not cursor or len(page) < 200:
                break

        logger.info(
            "P-013: prefix scan found %d BTC contracts in %d pages",
            len(all_contracts), pages_scanned,
        )
        return all_contracts

    def _parse_kalshi_contract(
        self, market: Dict[str, Any]
    ) -> Optional[KalshiCryptoContract]:
        """Parse a Kalshi market dict into a KalshiCryptoContract.

        Supports both legacy (integer cents) and v2 (dollar decimals) API
        response formats.  Extracts strike from the ``floor_strike`` field
        when available, falling back to ticker/title parsing.
        """
        ticker = market.get("ticker", "")
        title = market.get("title", "") or market.get("subtitle", "")

        # ── Contract type from strike_type field (v2 API) ────────────
        strike_type = market.get("strike_type", "")
        if strike_type == "between":
            return None  # Range contracts aren't directional — skip
        if strike_type == "greater":
            contract_type = "above"
        elif strike_type == "less":
            contract_type = "below"
        else:
            # Fallback: parse from title
            contract_type = "above"
            title_lower = title.lower()
            if "below" in title_lower or "under" in title_lower:
                contract_type = "below"

        # ── Strike price ─────────────────────────────────────────────
        # Prefer floor_strike field (v2 API), fallback to ticker/title
        floor_strike = market.get("floor_strike")
        if floor_strike is not None:
            try:
                strike = float(str(floor_strike))
            except (ValueError, TypeError):
                strike = None
        else:
            strike = None

        if strike is None or strike <= 0:
            strike = self._extract_strike(ticker, title)
        if strike is None or strike <= 0:
            return None

        # ── Expiry date ──────────────────────────────────────────────
        # Use close_time (actual settlement) over expiration_time (final expiry)
        expiry_date = market.get("close_time", "")
        if not expiry_date:
            expiry_date = market.get("expiration_time", "")
        if expiry_date and "T" in expiry_date:
            expiry_date = expiry_date.split("T")[0]

        # ── Category from ticker ─────────────────────────────────────
        ticker_upper = ticker.upper()
        if ticker_upper.startswith("KXBTC15M"):
            category = "daily"
        elif ticker_upper.startswith("KXBTCD"):
            category = "daily"
        elif ticker_upper.startswith("KXBTCW"):
            category = "weekly"
        elif ticker_upper.startswith("KXBTCM"):
            category = "monthly"
        elif ticker_upper.startswith("KXBTCY"):
            category = "yearly"
        elif ticker_upper.startswith("KXBTCMAX"):
            category = "yearly"
        else:
            category = "daily"

        if category not in self.target_categories:
            logger.debug(
                "P-013: skipping %s — category '%s' not in %s",
                ticker, category, self.target_categories,
            )
            return None

        # ── Prices ───────────────────────────────────────────────────
        # v2 API: dollar decimal fields (0.0000 - 1.0000)
        # Legacy: integer cent fields (0 - 100)
        yes_bid_d = market.get("yes_bid_dollars")
        yes_ask_d = market.get("yes_ask_dollars")

        if yes_bid_d is not None and yes_ask_d is not None:
            # v2 dollar format — prices are already in 0-1 range
            yes_bid_f = float(str(yes_bid_d))
            yes_ask_f = float(str(yes_ask_d))
            yes_price = (yes_bid_f + yes_ask_f) / 2.0
        else:
            # Legacy integer cents format
            yes_bid = market.get("yes_bid")
            yes_ask = market.get("yes_ask")
            if yes_bid is not None and yes_ask is not None:
                yes_bid_f = float(yes_bid) / 100.0
                yes_ask_f = float(yes_ask) / 100.0
                yes_price = (yes_bid_f + yes_ask_f) / 2.0
            elif market.get("last_price") is not None:
                yes_price = float(market["last_price"]) / 100.0
                yes_bid_f = None
                yes_ask_f = None
            elif market.get("last_price_dollars") is not None:
                yes_price = float(str(market["last_price_dollars"]))
                yes_bid_f = None
                yes_ask_f = None
            else:
                yes_price = 0.0
                yes_bid_f = None
                yes_ask_f = None

        # ── Liquidity filter ────────────────────────────────────────
        # Skip contracts with no real two-sided market.  When bid=0 and
        # ask=1 the mid-price of 0.50 is meaningless noise — these are
        # illiquid contracts where nobody is actually quoting.
        #
        # Requirements:
        #   1. Both bid and ask must be present and non-degenerate
        #   2. Bid must be > 0 (someone is actually willing to buy YES)
        #   3. Spread must be < 0.50 (tighter than coin-flip noise)
        #   4. Volume or open interest > 0 preferred (soft filter via
        #      last_price check below)
        if yes_bid_f is not None and yes_ask_f is not None:
            spread = yes_ask_f - yes_bid_f
            if yes_bid_f <= 0.0 or spread >= 0.50:
                return None  # No real liquidity — skip

        no_price = 1.0 - yes_price if yes_price > 0 else 0.0

        return KalshiCryptoContract(
            ticker=ticker,
            title=title,
            strike=strike,
            expiry_date=expiry_date if expiry_date else None,
            contract_type=contract_type,
            yes_price=yes_price,
            no_price=no_price,
            yes_bid=yes_bid_f if yes_bid_f is not None else None,
            yes_ask=yes_ask_f if yes_ask_f is not None else None,
            category=category,
            underlying="BTC",
        )

    @staticmethod
    def _extract_strike(ticker: str, title: str) -> Optional[float]:
        """Extract strike price from ticker or title.

        Patterns:
          Ticker: KXBTCD-26MAR28-T87750 → 87750
          Ticker: KXBTC-26JAN15-B100000 → 100000
          Title: "Bitcoin above $87,750" → 87750
          Title: "Will Bitcoin cross $100k" → 100000
        """
        # Try ticker first: T or B followed by digits (possibly with decimal)
        match = re.search(r"-[TB](\d+(?:\.\d+)?)", ticker)
        if match:
            return float(match.group(1))

        # Try title: dollar sign followed by number (with commas/k/K)
        match = re.search(r"\$([0-9,]+\.?\d*)(k|K)?", title)
        if match:
            num_str = match.group(1).replace(",", "")
            try:
                value = float(num_str)
                if match.group(2):  # k/K suffix
                    value *= 1000
                return value
            except ValueError:
                pass

        return None

    # ── Signal Evaluation ─────────────────────────────────────────────

    def _evaluate_contract(
        self, contract: KalshiCryptoContract
    ) -> Optional[CryptoArbSignal]:
        """Evaluate a single Kalshi contract for edge against Deribit.

        Returns CryptoArbSignal if edge exceeds thresholds, None otherwise.
        """
        if contract.yes_price <= 0.02 or contract.yes_price >= 0.98:
            return None  # Too extreme, no liquidity

        # Require real bid/ask (liquidity gate already applied at parse time,
        # but double-check here for contracts built from last_price only)
        if contract.yes_bid is None or contract.yes_ask is None:
            return None
        if contract.yes_bid <= 0.0:
            return None

        # Find matching Deribit chain
        chain = self._find_matching_chain(contract.expiry_date)
        if chain is None:
            return None

        # Compute Deribit-implied probability
        if contract.contract_type == "above":
            estimate = self.pricer.estimate_probability(
                chain, contract.strike,
            )
        else:
            estimate = self.pricer.estimate_probability_below(
                chain, contract.strike,
            )

        if estimate is None:
            return None

        # Reject low-confidence estimates — the pricer couldn't get
        # good data (e.g. illiquid Deribit chain, wide IV gaps).
        # Require at least 0.6 confidence (both methods contributed).
        MIN_CONFIDENCE = 0.6
        if estimate.confidence < MIN_CONFIDENCE:
            logger.debug(
                "P-013: skipping %s — pricer confidence %.2f < %.2f",
                contract.ticker, estimate.confidence, MIN_CONFIDENCE,
            )
            return None

        deribit_prob = estimate.probability
        kalshi_prob = contract.yes_price

        # Edge: how much Kalshi deviates from Deribit fair value
        # Positive edge = Kalshi is overpricing YES → sell YES (buy NO)
        # Negative edge = Kalshi is underpricing YES → buy YES
        raw_edge = deribit_prob - kalshi_prob

        # Determine which side to trade
        if raw_edge > 0:
            # Deribit says higher prob than Kalshi → buy YES
            side = "YES"
            buy_price = kalshi_prob
            edge_pct = abs(raw_edge)
        else:
            # Deribit says lower prob than Kalshi → buy NO
            side = "NO"
            buy_price = 1.0 - kalshi_prob
            edge_pct = abs(raw_edge)

        # Compute Kalshi fee
        fee_mult = (
            KALSHI_MAKER_FEE_MULT if self.use_maker_orders
            else KALSHI_TAKER_FEE_MULT
        )
        fee_pct = fee_mult * buy_price * (1.0 - buy_price)
        net_edge = edge_pct - fee_pct

        # Filter
        if edge_pct < self.min_edge_pct:
            return None
        if net_edge < self.min_net_edge_pct:
            return None

        # Sanity check: edges above the plausible cap are almost certainly
        # data quality issues (illiquid Kalshi mid-price vs efficient
        # Deribit), not real arb.  Real crypto options arb is 5-15%.
        if edge_pct > self.max_plausible_edge:
            logger.info(
                "P-013: REJECTED implausible edge %.1f%% > cap %.1f%% on %s "
                "(kalshi=%.3f deribit=%.3f spread=%.3f)",
                edge_pct * 100, self.max_plausible_edge * 100,
                contract.ticker,
                kalshi_prob, deribit_prob,
                (contract.yes_ask or 0) - (contract.yes_bid or 0),
            )
            return None

        return CryptoArbSignal(
            contract=contract,
            deribit_fair_prob=deribit_prob,
            kalshi_prob=kalshi_prob,
            edge_pct=edge_pct,
            side=side,
            buy_price=buy_price,
            estimated_fee=fee_pct,
            net_edge_pct=net_edge,
            confidence=estimate.confidence,
            method=estimate.method,
        )

    # ── Execution ─────────────────────────────────────────────────────

    def _execute_signal(self, signal: CryptoArbSignal) -> Optional[ScanResult]:
        """Execute an arb signal: place order on Kalshi.

        Phase 1: directional only (no Deribit hedge).
        """
        contract = signal.contract

        # Dedup
        fp = self.make_fingerprint(contract.ticker, signal.side)
        if self.is_duplicate(fp):
            return None
        if self.has_open_position(contract.ticker):
            return None
        self.mark_seen(fp)

        # Kelly sizing: fractional Kelly with confidence scaling
        # binary Kelly: edge / net_odds = net_edge_pct / (1 - buy_price)
        raw_kelly = signal.net_edge_pct / (1.0 - signal.buy_price) if signal.buy_price < 1.0 else 0.0
        kelly_frac = raw_kelly * self.kelly_multiplier * signal.confidence
        kelly_frac = max(0.0, min(kelly_frac, 0.05))  # Cap at 5%

        position_size = self.compute_position_size(kelly_frac)
        if position_size <= 0:
            return ScanResult(
                fingerprint=fp,
                timestamp_utc=self._now_fn().isoformat(),
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=self.mode,
                venue="kalshi",
                market_type="crypto_options_arb",
                event=contract.title,
                market_id=contract.ticker,
                side=signal.side,
                fair_prob=signal.deribit_fair_prob,
                venue_prob=signal.kalshi_prob,
                edge_pct=signal.edge_pct,
                ev=0.0,
                kelly_fraction=kelly_frac,
                position_size_usd=0.0,
                action="SKIPPED_EDGE",
                skip_reason="Position size zero after Kelly/risk caps",
                extra=self._build_extra(signal),
            )

        # Aggregate risk check
        if not self.check_aggregate_risk(
            "kalshi", position_size, market_id=contract.ticker
        ):
            return ScanResult(
                fingerprint=fp,
                timestamp_utc=self._now_fn().isoformat(),
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=self.mode,
                venue="kalshi",
                market_type="crypto_options_arb",
                event=contract.title,
                market_id=contract.ticker,
                side=signal.side,
                fair_prob=signal.deribit_fair_prob,
                venue_prob=signal.kalshi_prob,
                edge_pct=signal.edge_pct,
                ev=signal.net_edge_pct,
                kelly_fraction=kelly_frac,
                position_size_usd=position_size,
                action="SKIPPED_RISK",
                skip_reason="Aggregate risk limit",
                extra=self._build_extra(signal),
            )

        # Place order
        order = self._place_kalshi_order(contract, signal, position_size)
        self.mark_position_open(contract.ticker)

        ev = signal.net_edge_pct
        result = ScanResult(
            fingerprint=fp,
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type="crypto_options_arb",
            event=contract.title,
            market_id=contract.ticker,
            side=signal.side,
            fair_prob=signal.deribit_fair_prob,
            venue_prob=signal.kalshi_prob,
            edge_pct=signal.edge_pct,
            ev=ev,
            kelly_fraction=kelly_frac,
            position_size_usd=position_size,
            action="PLACED",
            order_id=getattr(order, "order_id", None),
            fill_price=getattr(order, "fill_price", None),
            extra=self._build_extra(signal),
        )
        self.write_log(result)

        logger.info(
            "P-013: PLACED %s %s @%.2f | fair=%.2f edge=%.1f%% net=%.1f%% "
            "| $%.2f | %s (conf=%.0f%%)",
            signal.side, contract.ticker, signal.buy_price,
            signal.deribit_fair_prob,
            signal.edge_pct * 100, signal.net_edge_pct * 100,
            position_size,
            signal.method, signal.confidence * 100,
        )

        return result

    def _place_kalshi_order(
        self,
        contract: KalshiCryptoContract,
        signal: CryptoArbSignal,
        position_size: float,
    ):
        """Place order on Kalshi.

        Supports both:
          - Legacy API: place_order(order_dict) — single dict arg
          - Mock/test API: place_order(ticker=, side=, ...) — kwargs
        """
        price_cents = int(signal.buy_price * 100)
        quantity = max(1, int(position_size / signal.buy_price)) if signal.buy_price > 0 else 1

        if hasattr(self.kalshi_client, "place_order"):
            # Build order dict for the real Kalshi API
            order_payload = {
                "ticker": contract.ticker,
                "action": "buy",
                "side": signal.side.lower(),
                "type": "limit",
                "count": quantity,
            }
            if signal.side == "YES":
                order_payload["yes_price"] = price_cents
            else:
                order_payload["no_price"] = price_cents

            try:
                # Real API: place_order(order_dict)
                import inspect
                sig = inspect.signature(self.kalshi_client.place_order)
                params = list(sig.parameters.keys())
                if len(params) == 1 or (len(params) == 2 and "self" in params):
                    return self.kalshi_client.place_order(order_payload)
                else:
                    # Mock/test API with kwargs
                    return self.kalshi_client.place_order(
                        ticker=contract.ticker,
                        side=signal.side.lower(),
                        yes_price=price_cents if signal.side == "YES" else None,
                        no_price=price_cents if signal.side == "NO" else None,
                        count=quantity,
                    )
            except TypeError:
                # Fallback: try dict-based call
                try:
                    return self.kalshi_client.place_order(order_payload)
                except Exception:
                    pass  # Fall through to paper result
            except Exception as e:
                # In paper mode, treat API errors as paper fills
                if self.mode == "paper":
                    logger.debug(
                        "P-013: paper mode — API place_order failed (%s), "
                        "recording as paper fill", e,
                    )
                else:
                    raise

        # Paper mode fallback
        class _PaperResult:
            order_id = f"PAPER-{contract.ticker}-{signal.side}"
            fill_price = signal.buy_price
        return _PaperResult()

    def _build_extra(self, signal: CryptoArbSignal) -> Dict[str, Any]:
        """Build extra metadata dict for ScanResult.

        Includes calibration fields so we can later compare predicted
        probabilities to actual outcomes and detect systematic bias.
        """
        return {
            "arb_type": "crypto_options_directional",
            "strike": signal.contract.strike,
            "expiry_date": signal.contract.expiry_date,
            "category": signal.contract.category,
            "contract_type": signal.contract.contract_type,
            "deribit_fair_prob": round(signal.deribit_fair_prob, 4),
            "kalshi_prob": round(signal.kalshi_prob, 4),
            "raw_edge_pct": round(signal.edge_pct, 4),
            "estimated_fee_pct": round(signal.estimated_fee, 4),
            "net_edge_pct": round(signal.net_edge_pct, 4),
            "confidence": round(signal.confidence, 3),
            "pricing_method": signal.method,
            "btc_spot": self._btc_spot,
            "use_maker": self.use_maker_orders,
            # ── Calibration fields (added 2026-03-30) ──────────────
            # These let us assess whether the pricer is systematically
            # biased.  After settlement, compare predicted_win_prob to
            # actual outcome (1=win, 0=loss) across buckets.
            "calibration": {
                "predicted_win_prob": round(
                    signal.deribit_fair_prob if signal.side == "YES"
                    else 1.0 - signal.deribit_fair_prob, 4
                ),
                "buy_price": round(signal.buy_price, 4),
                "implied_edge": round(signal.net_edge_pct, 4),
                "side": signal.side,
                "kalshi_yes_bid": signal.contract.yes_bid,
                "kalshi_yes_ask": signal.contract.yes_ask,
                "kalshi_mid": round(signal.kalshi_prob, 4),
            },
        }

    # ── BasePod abstract methods ──────────────────────────────────────

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config: dict, **overrides):
        """Build P-013 from config."""
        from src.deribit_client import DeribitClient
        from src.crypto_options_pricer import CryptoOptionsPricer

        pod_cfg = config.get("pods", {}).get("P-013", {})

        # Build Deribit client
        deribit_client = overrides.get("deribit_client")
        if deribit_client is None:
            deribit_client = DeribitClient.from_config(config)

        # Build pricer
        pricer = overrides.get("pricer")
        if pricer is None:
            pricer = CryptoOptionsPricer.from_config(config)

        # Log path
        log_path = overrides.get("trade_log_path")
        if not log_path:
            log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
            log_path = log_dir / "P-013.jsonl"

        env = pod_cfg.get("environment", config.get("environment", "demo"))
        mode_map = {"demo": "paper", "live": "live"}

        return cls(
            kalshi_client=overrides.get("kalshi_client"),
            deribit_client=deribit_client,
            pricer=pricer,
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            min_edge_pct=pod_cfg.get("min_edge_pct", 0.12),
            min_net_edge_pct=pod_cfg.get("min_net_edge_pct", 0.08),
            max_contracts_per_cycle=pod_cfg.get("max_contracts_per_cycle", 3),
            kelly_multiplier=pod_cfg.get("kelly_multiplier", 0.15),
            use_maker_orders=pod_cfg.get("use_maker_orders", True),
            max_plausible_edge=pod_cfg.get("max_plausible_edge", 0.20),
            target_categories=pod_cfg.get("target_categories"),
            trade_log_path=log_path,
            mode=mode_map.get(env, "paper"),
            _now_fn=overrides.get("_now_fn"),
            aggregate_risk=overrides.get("aggregate_risk"),
            trade_store=overrides.get("trade_store"),
            min_kelly_fraction=pod_cfg.get("min_kelly_fraction"),
        )
