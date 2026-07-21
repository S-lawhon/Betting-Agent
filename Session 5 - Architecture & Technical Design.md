# Session 5 — Architecture & Technical Design

*Completed February 22, 2026*

---

## 1. Design Principles

Three principles govern the architecture, derived from studying the existing codebase:

1. **Mirror existing patterns.** The codebase uses dependency injection (Scanner takes `kalshi_client`, `odds_client`, `matcher`, etc.), `from_config` class methods, frozen dataclasses for results, JSONL trade logs, and structlog JSON logging. All new code follows these patterns.

2. **Minimal surgery on working code.** P-001's scanner is running paper trades with 750+ tests passing. The refactor extracts an interface from it — it doesn't rewrite it. `scanner.py` becomes `KalshiMoneylinePod` by inheriting from `BasePod` and keeping its internals intact.

3. **Paper-first everywhere.** Every new pod starts in paper mode. Polymarket pods paper-trade (log what *would* happen) until US access opens. Kalshi and IB pods can go live sooner.

---

## 2. BasePod Abstract Class

The existing `Scanner` has a `scan_once()` method that returns a list of trade log entries (dicts). This becomes the pod interface.

```python
# src/base_pod.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import json


@dataclass(frozen=True)
class ScanResult:
    """One opportunity found (or skipped) by a pod's scan cycle."""
    fingerprint: str
    timestamp_utc: str
    pod_id: str              # e.g. "P-001", "P-006"
    pod_name: str            # e.g. "Kalshi Moneyline Value"
    mode: str                # "paper" or "live"
    venue: str               # "kalshi", "polymarket", "forecastex", "multi"
    market_type: str         # "sports_moneyline", "economics_cpi", etc.
    event: str               # Human-readable event description
    market_id: str           # Venue-specific market/ticker ID
    side: str                # "YES", "NO", "BUY", "SELL"
    fair_prob: float         # Pod's estimated fair probability
    venue_prob: float        # Price on the execution venue (0-1)
    edge_pct: float          # (fair - venue) / venue or similar
    ev: float                # Expected value in dollars
    kelly_fraction: float    # Kelly-optimal fraction of bankroll
    position_size_usd: float # Actual dollar size after caps
    action: str              # PLACED, SKIPPED_EDGE, SKIPPED_RISK, SKIPPED_DUPLICATE
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    skip_reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    # extra holds pod-specific data (hedge leg, carry rate, etc.)


class BasePod(ABC):
    """Abstract base class for all betting pods."""

    def __init__(
        self,
        pod_id: str,
        pod_name: str,
        edge_calculator,          # EdgeCalculator instance
        risk_manager,             # RiskManager instance
        trade_log_path: Path,
        mode: str = "paper",      # "paper" or "live"
        _now_fn=None,
    ):
        self.pod_id = pod_id
        self.pod_name = pod_name
        self.edge_calculator = edge_calculator
        self.risk_manager = risk_manager
        self.trade_log_path = trade_log_path
        self.mode = mode
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))
        self._seen_fingerprints: set = set()
        self._load_seen_from_log()

    # ── Abstract methods (each pod implements these) ────────────

    @abstractmethod
    def scan_once(self) -> List[ScanResult]:
        """Run one scan cycle. Return list of ScanResults."""
        ...

    @abstractmethod
    def venue_name(self) -> str:
        """Return primary execution venue name."""
        ...

    # ── Shared infrastructure (inherited by all pods) ───────────

    def make_fingerprint(self, market_id: str, side: str) -> str:
        """SHA-256 fingerprint per market/side/hour (dedup)."""
        now = self._now_fn()
        key = f"{market_id}|{side}|{now.strftime('%Y%m%d%H')}"
        return hashlib.sha256(key.encode()).hexdigest()

    def is_duplicate(self, fingerprint: str) -> bool:
        return fingerprint in self._seen_fingerprints

    def mark_seen(self, fingerprint: str):
        self._seen_fingerprints.add(fingerprint)

    def write_log(self, result: ScanResult):
        """Append ScanResult to JSONL trade log."""
        try:
            with open(self.trade_log_path, "a") as f:
                # Convert dataclass to dict, handling nested dataclasses
                from dataclasses import asdict
                f.write(json.dumps(asdict(result), default=str) + "\n")
        except OSError:
            pass  # Write error does not crash scan_once (matches existing behavior)

    def _load_seen_from_log(self):
        """Reload fingerprints from existing log (restart persistence)."""
        if not self.trade_log_path.exists():
            return
        try:
            now = self._now_fn()
            current_hour = now.strftime("%Y%m%d%H")
            for line in self.trade_log_path.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("action") == "PLACED":
                    self._seen_fingerprints.add(rec["fingerprint"])
        except (OSError, json.JSONDecodeError):
            pass

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict, **overrides) -> "BasePod":
        """Construct pod from YAML config dict."""
        ...
```

### Why ScanResult extends the existing trade log schema

The existing trade log entry has these fields: `fingerprint, timestamp_utc, mode, sport, event, market_ticker, side, fair_prob, kalshi_prob, edge_pct, ev, kelly_fraction, position_size_usd, action, order_id, fill_price`.

`ScanResult` preserves all of them while generalizing:
- `sport` → `market_type` (works for economics, politics, crypto too)
- `market_ticker` → `market_id` (venue-agnostic)
- `kalshi_prob` → `venue_prob` (works for any venue's price)
- Adds `pod_id`, `pod_name`, `venue` for multi-pod routing
- Adds `extra` dict for pod-specific data (hedge info, carry rates, etc.)

### KalshiMoneylinePod (P-001 refactored)

```python
# src/pods/kalshi_moneyline.py

class KalshiMoneylinePod(BasePod):
    """P-001: Existing scanner refactored into BasePod interface.

    Internally delegates to the existing Scanner.scan_once() to avoid
    rewriting 88 tests worth of battle-tested logic.
    """

    def __init__(self, scanner, **base_kwargs):
        super().__init__(pod_id="P-001", pod_name="Kalshi Moneyline Value", **base_kwargs)
        self._scanner = scanner  # existing Scanner instance

    def scan_once(self) -> List[ScanResult]:
        # Delegate to existing scanner, convert output to ScanResult
        raw_entries = self._scanner.scan_once()
        results = []
        for entry in raw_entries:
            result = ScanResult(
                fingerprint=entry["fingerprint"],
                timestamp_utc=entry["timestamp_utc"],
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=entry["mode"],
                venue="kalshi",
                market_type=f"sports_{entry.get('sport', 'unknown')}",
                event=entry["event"],
                market_id=entry["market_ticker"],
                side=entry["side"],
                fair_prob=entry["fair_prob"],
                venue_prob=entry["kalshi_prob"],
                edge_pct=entry["edge_pct"],
                ev=entry["ev"],
                kelly_fraction=entry["kelly_fraction"],
                position_size_usd=entry["position_size_usd"],
                action=entry["action"],
                order_id=entry.get("order_id"),
                fill_price=entry.get("fill_price"),
                skip_reason=entry.get("skip_reason"),
            )
            results.append(result)
        return results

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config, **overrides):
        # Build existing Scanner from config, wrap in pod
        scanner = Scanner.from_config(config)
        return cls(
            scanner=scanner,
            edge_calculator=scanner.edge_calculator,
            risk_manager=scanner.risk_manager,
            trade_log_path=scanner.trade_log_path,
            mode=scanner.mode,
        )
```

**Key design decision**: P-001 wraps the existing Scanner rather than rewriting it. This preserves all 88 tests and lets us run both the old `Scanner` interface and the new `BasePod` interface simultaneously during transition.

---

## 3. Polymarket CLOB Client

Mirrors the structure of the existing `kalshi_client.py`: a thin wrapper with rate limiting, error handling, and paper-mode support.

```python
# src/polymarket_client.py

from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum


class PolymarketEnvironment(Enum):
    PAPER = "paper"     # Log trades but don't execute
    LIVE = "live"       # Execute on-chain (requires USDC + Polygon gas)


@dataclass(frozen=True)
class PolymarketMarket:
    """Normalized market representation from Polymarket."""
    condition_id: str
    token_id_yes: str       # ERC1155 token ID for YES outcome
    token_id_no: str        # ERC1155 token ID for NO outcome
    question: str           # "Will X happen?"
    slug: str               # URL slug
    active: bool
    closed: bool
    end_date: Optional[str]
    game_start_time: Optional[str]  # For sports markets
    min_order_size: float
    min_tick_size: float    # 0.01, 0.001, etc.


@dataclass(frozen=True)
class PolymarketBook:
    """Order book snapshot."""
    token_id: str
    bids: List[Dict]    # [{"price": 0.55, "size": 1000}, ...]
    asks: List[Dict]    # [{"price": 0.58, "size": 500}, ...]
    best_bid: Optional[float]
    best_ask: Optional[float]
    midpoint: Optional[float]


@dataclass(frozen=True)
class PolymarketOrderResult:
    """Result of placing an order."""
    success: bool
    order_id: Optional[str]
    error: Optional[str]
    fill_price: Optional[float]


class PolymarketClient:
    """
    Wrapper around py-clob-client with rate limiting, paper mode,
    and error handling matching kalshi_client.py patterns.

    Constructor mirrors KalshiClient:
        - host, private_key, chain_id for auth
        - environment for paper/live mode
        - rate_limiter for throttling
        - adapter for test injection
    """

    def __init__(
        self,
        host: str = "https://clob.polymarket.com",
        private_key: Optional[str] = None,
        chain_id: int = 137,
        environment: PolymarketEnvironment = PolymarketEnvironment.PAPER,
        rate_limiter=None,
        adapter=None,  # Injectable for testing (replaces ClobClient)
    ):
        self.host = host
        self.environment = environment
        self._rate_limiter = rate_limiter

        if adapter:
            self._client = adapter
        elif private_key:
            from py_clob_client.client import ClobClient
            self._client = ClobClient(host, key=private_key, chain_id=chain_id)
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
        else:
            # Read-only mode (no trading, just price data)
            from py_clob_client.client import ClobClient
            self._client = ClobClient(host)

    # ── Market data (no auth required) ──────────────────────────

    def get_markets(self, next_cursor=None) -> Dict:
        """Paginated market listing. Returns {'data': [...], 'next_cursor': ...}."""
        if self._rate_limiter:
            self._rate_limiter.wait()
        return self._client.get_markets(next_cursor=next_cursor)

    def get_book(self, token_id: str) -> PolymarketBook:
        """Get order book for a token. Use get_price() for fresher data."""
        if self._rate_limiter:
            self._rate_limiter.wait()
        raw = self._client.get_order_book(token_id)
        bids = [{"price": float(o.price), "size": float(o.size)} for o in raw.bids]
        asks = [{"price": float(o.price), "size": float(o.size)} for o in raw.asks]
        best_bid = bids[0]["price"] if bids else None
        best_ask = asks[0]["price"] if asks else None
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
        return PolymarketBook(
            token_id=token_id, bids=bids, asks=asks,
            best_bid=best_bid, best_ask=best_ask, midpoint=mid,
        )

    def get_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """Get best price. More reliable than get_book() (avoids ghost market issue)."""
        if self._rate_limiter:
            self._rate_limiter.wait()
        try:
            return float(self._client.get_price(token_id, side))
        except Exception:
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        if self._rate_limiter:
            self._rate_limiter.wait()
        try:
            return float(self._client.get_midpoint(token_id))
        except Exception:
            return None

    # ── Trading (auth required) ─────────────────────────────────

    def place_order(
        self,
        token_id: str,
        side: str,          # "BUY" or "SELL"
        price: float,       # 0.00 - 1.00
        size: float,        # Number of contracts
        order_type: str = "GTC",
    ) -> PolymarketOrderResult:
        """Place an order. In paper mode, logs but doesn't execute."""

        if self.environment == PolymarketEnvironment.PAPER:
            return PolymarketOrderResult(
                success=True,
                order_id=None,
                error=None,
                fill_price=price,
            )

        # Live mode
        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            order_args = {
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": BUY if side.upper() == "BUY" else SELL,
            }
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, order_type=order_type)
            return PolymarketOrderResult(
                success=resp.get("success", False) if isinstance(resp, dict) else True,
                order_id=resp.get("orderID") if isinstance(resp, dict) else str(resp),
                error=resp.get("errorMsg") if isinstance(resp, dict) else None,
                fill_price=price,
            )
        except Exception as e:
            return PolymarketOrderResult(
                success=False, order_id=None, error=str(e), fill_price=None,
            )

    def cancel_order(self, order_id: str) -> bool:
        if self.environment == PolymarketEnvironment.PAPER:
            return True
        try:
            return self._client.cancel(order_id)
        except Exception:
            return False

    # ── Config constructor ──────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> "PolymarketClient":
        poly_cfg = config.get("polymarket", {})
        env_str = poly_cfg.get("environment", "paper")
        env = PolymarketEnvironment.LIVE if env_str == "live" else PolymarketEnvironment.PAPER
        return cls(
            host=poly_cfg.get("host", "https://clob.polymarket.com"),
            private_key=poly_cfg.get("private_key") or os.environ.get("POLYMARKET_PRIVATE_KEY"),
            chain_id=poly_cfg.get("chain_id", 137),
            environment=env,
        )
```

### Key design decisions:

1. **Paper mode built in.** Since you can't deposit on Polymarket US yet, paper mode is the default. `place_order()` returns a simulated success. The pod's `scan_once()` logs what it would have traded.

2. **`get_price()` preferred over `get_book()`.** Research found Polymarket's `/book` endpoint returns stale "ghost market" data (0.01/0.99). The `/price` endpoint is more reliable.

3. **Injectable adapter.** Just like `KalshiClient` takes an `adapter` parameter for testing, `PolymarketClient` accepts one too. Tests inject a mock instead of hitting the real API.

---

## 4. Polymarket Matcher

Extends the existing matcher pattern for Polymarket's different naming conventions.

```python
# src/polymarket_matcher.py

@dataclass(frozen=True)
class PolymarketMatchResult:
    """Result of matching an Odds API event to a Polymarket market."""
    polymarket_condition_id: str
    polymarket_question: str
    token_id_yes: str
    token_id_no: str
    odds_event: OddsEvent
    fuzzy_score: float
    time_delta_minutes: Optional[float]


class PolymarketMatcher:
    """
    Matches Odds API events to Polymarket markets.

    Mirrors Matcher interface: match_all(markets, events, sport) → [PolymarketMatchResult]

    Key differences from Kalshi matcher:
    - Polymarket uses full questions ("Will the Kansas City Chiefs beat the...?")
      vs Kalshi titles ("Kansas City Chiefs vs Las Vegas Raiders")
    - Polymarket has condition_id + two token_ids (YES/NO)
    - No subtitle/ticker suffix logic needed
    """

    def __init__(
        self,
        fuzzy_threshold: float = 80.0,   # Slightly lower than Kalshi (85)
        time_window_minutes: float = 60.0,  # Wider window (Polymarket less precise on timing)
        configured_sports: Optional[List[str]] = None,
        unmatched_log_path: Optional[Path] = None,
    ):
        ...

    def match_all(
        self,
        polymarket_markets: List[PolymarketMarket],
        odds_events: List[OddsEvent],
        sport: Optional[str] = None,
    ) -> List[PolymarketMatchResult]:
        """Match Polymarket markets to Odds API events."""
        ...

    @classmethod
    def from_config(cls, config: dict) -> "PolymarketMatcher":
        ...
```

---

## 5. P-006: Sportsbook-Polymarket Consensus Pod

The first new pod to build — reuses the most existing infrastructure.

```python
# src/pods/polymarket_consensus.py

class PolymarketConsensusPod(BasePod):
    """
    P-006: Uses Odds API multi-book consensus as fair value,
    scans Polymarket sports markets for mispricing.

    Identical logic to P-001 (Kalshi Moneyline Value) but:
    - Execution venue: Polymarket (0% fees)
    - Lower edge threshold (no Kalshi fee drag)
    - Different matching rules (Polymarket naming conventions)
    """

    def __init__(
        self,
        polymarket_client: PolymarketClient,
        odds_client,            # Existing OddsClient
        matcher: PolymarketMatcher,
        edge_calculator,        # EdgeCalculator with fee_pct=0.0
        risk_manager,
        sports: List[str],
        trade_log_path: Path,
        mode: str = "paper",
        _now_fn=None,
    ):
        super().__init__(
            pod_id="P-006",
            pod_name="Sportsbook-Polymarket Consensus",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path,
            mode=mode,
            _now_fn=_now_fn,
        )
        self.polymarket_client = polymarket_client
        self.odds_client = odds_client
        self.matcher = matcher
        self.sports = sports

    def scan_once(self) -> List[ScanResult]:
        results = []
        for sport in self.sports:
            # 1. Fetch Polymarket sports markets
            poly_markets = self._fetch_sport_markets(sport)

            # 2. Fetch Odds API events + consensus
            try:
                odds_events = self.odds_client.get_events(sport)
            except Exception:
                continue

            # 3. Match
            matches = self.matcher.match_all(poly_markets, odds_events, sport)

            # 4. Evaluate each match
            for match in matches:
                consensus = self.odds_client.get_consensus(match.odds_event)
                if not consensus:
                    continue

                # Get Polymarket price
                mid = self.polymarket_client.get_midpoint(match.token_id_yes)
                if mid is None or mid <= 0 or mid >= 1:
                    continue

                # Determine which side is YES (home or away)
                fair_prob = self._infer_fair_prob(match, consensus)

                # Evaluate edge (fee_pct=0 for Polymarket)
                edge_result = self.edge_calculator.best_side(fair_prob, mid)
                if edge_result is None:
                    # Log SKIPPED_EDGE
                    ...
                    continue

                # Risk check
                size = self.risk_manager.approve(edge_result)
                if size is None:
                    # Log SKIPPED_RISK
                    ...
                    continue

                # Dedup
                fp = self.make_fingerprint(match.polymarket_condition_id, edge_result.side)
                if self.is_duplicate(fp):
                    continue
                self.mark_seen(fp)

                # Place (paper or live)
                order_result = self.polymarket_client.place_order(
                    token_id=match.token_id_yes if edge_result.side == "YES" else match.token_id_no,
                    side="BUY",
                    price=mid,
                    size=size / mid,  # Convert USD to contracts
                )

                result = ScanResult(
                    fingerprint=fp,
                    timestamp_utc=self._now_fn().isoformat(),
                    pod_id=self.pod_id,
                    pod_name=self.pod_name,
                    mode=self.mode,
                    venue="polymarket",
                    market_type=f"sports_{sport}",
                    event=match.polymarket_question,
                    market_id=match.polymarket_condition_id,
                    side=edge_result.side,
                    fair_prob=fair_prob,
                    venue_prob=mid,
                    edge_pct=edge_result.edge_pct,
                    ev=edge_result.ev,
                    kelly_fraction=edge_result.kelly_fraction,
                    position_size_usd=size,
                    action="PLACED",
                    order_id=order_result.order_id,
                    fill_price=order_result.fill_price,
                )
                self.write_log(result)
                results.append(result)

        return results

    def venue_name(self) -> str:
        return "polymarket"

    def _fetch_sport_markets(self, sport: str) -> List[PolymarketMarket]:
        """Fetch active Polymarket markets for a sport category."""
        # Polymarket markets have tags/categories; filter by sport
        ...

    def _infer_fair_prob(self, match, consensus) -> float:
        """Determine fair YES probability from consensus + match."""
        # Similar to _infer_yes_side in existing scanner
        ...

    @classmethod
    def from_config(cls, config, **overrides):
        poly_client = PolymarketClient.from_config(config)
        odds_client = OddsClient.from_config(config)
        matcher = PolymarketMatcher.from_config(config)
        edge_calc = EdgeCalculator(
            fee_pct=0.0,  # Polymarket 0% fees
            kelly_fraction=config.get("kelly_fraction", 0.25),
            max_bet_pct=config.get("max_bet_pct", 0.03),
            min_edge_pct=config.get("polymarket", {}).get("min_edge_pct", 0.01),  # Lower threshold
            min_ev=config.get("min_ev", 0.01),
        )
        ...
```

---

## 6. IB ForecastEx Connector

For P-004 (ForecastEx-Kalshi Econ Arb) and P-005 (Yield Carry).

```python
# src/forecastex_client.py

@dataclass(frozen=True)
class ForecastExContract:
    """Normalized ForecastEx contract."""
    symbol: str
    description: str
    expiry: str
    exchange: str           # Always "FORECASTX"
    sec_type: str           # Always "OPT"
    right: str              # "CALL" (YES) or "PUT" (NO)
    strike: float
    yes_price: Optional[float]
    no_price: Optional[float]
    last_price: Optional[float]


@dataclass(frozen=True)
class ForecastExOrderResult:
    success: bool
    order_id: Optional[int]
    error: Optional[str]
    fill_price: Optional[float]


class ForecastExClient:
    """
    IB ForecastEx connector via ib_insync.

    Key constraints:
    - BUY only (no SELL; buy opposing contract to exit)
    - secType="OPT", exchange="FORECASTX"
    - right="CALL" for YES, "PUT" for NO
    - No symbol discovery API (must know symbols in advance)
    - 3.14% APY coupon accrues on daily closing value
    """

    def __init__(
        self,
        ib=None,               # ib_insync.IB instance (injectable)
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 10,
        environment: str = "paper",  # "paper" or "live"
    ):
        self.environment = environment
        if ib:
            self._ib = ib
        else:
            from ib_insync import IB
            self._ib = IB()
            # Connection happens on first use or explicit connect()

    def connect(self):
        if not self._ib.isConnected():
            self._ib.connect(self.host, self.port, clientId=self.client_id)

    def get_price(self, symbol: str, right: str = "CALL") -> Optional[float]:
        """Get current price for a ForecastEx contract."""
        from ib_insync import Contract
        contract = Contract(
            symbol=symbol,
            secType="OPT",
            exchange="FORECASTX",
            currency="USD",
            right=right,
        )
        self._ib.qualifyContracts(contract)
        ticker = self._ib.reqMktData(contract)
        self._ib.sleep(2)  # Wait for data
        if ticker.midpoint() and ticker.midpoint() != float('nan'):
            return ticker.midpoint()
        return None

    def place_order(
        self,
        symbol: str,
        right: str,         # "CALL" (YES) or "PUT" (NO)
        quantity: int,
        limit_price: float,
    ) -> ForecastExOrderResult:
        """Place a BUY order. ForecastEx only supports BUY."""
        if self.environment == "paper":
            return ForecastExOrderResult(
                success=True, order_id=None, error=None, fill_price=limit_price,
            )

        from ib_insync import Contract, LimitOrder
        contract = Contract(
            symbol=symbol, secType="OPT", exchange="FORECASTX",
            currency="USD", right=right,
        )
        order = LimitOrder("BUY", quantity, limit_price)
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(5)

        if trade.orderStatus.status in ("Filled", "Submitted"):
            return ForecastExOrderResult(
                success=True,
                order_id=trade.order.orderId,
                error=None,
                fill_price=trade.orderStatus.avgFillPrice or limit_price,
            )
        return ForecastExOrderResult(
            success=False, order_id=None,
            error=trade.orderStatus.status, fill_price=None,
        )

    @classmethod
    def from_config(cls, config: dict) -> "ForecastExClient":
        ib_cfg = config.get("interactive_brokers", {})
        return cls(
            host=ib_cfg.get("host", "127.0.0.1"),
            port=ib_cfg.get("port", 7497),
            client_id=ib_cfg.get("client_id", 10),
            environment=ib_cfg.get("environment", "paper"),
        )
```

---

## 7. Venue-Agnostic Executor

Extends the existing `Executor` to route trades to any venue.

```python
# src/multi_executor.py

class MultiExecutor:
    """
    Routes ScanResults from any pod to the correct venue client.

    Replaces the existing single-venue Executor for multi-pod operation.
    The existing Executor continues to work for P-001 standalone.
    """

    def __init__(
        self,
        venue_clients: Dict[str, Any],  # {"kalshi": KalshiClient, "polymarket": PolymarketClient, ...}
        risk_manager: RiskManager,
        mode: str = "paper",
        live_trading_confirmed: bool = False,
    ):
        self.venue_clients = venue_clients
        self.risk_manager = risk_manager
        self.mode = mode
        self.live_trading_confirmed = live_trading_confirmed

    def execute(self, result: ScanResult) -> ExecutionResult:
        """Execute a ScanResult on its target venue."""
        if result.action != "PLACED":
            return ExecutionResult(success=True, skipped=True)

        venue = result.venue
        client = self.venue_clients.get(venue)
        if not client:
            return ExecutionResult(success=False, error=f"No client for venue: {venue}")

        if self.mode == "paper":
            self.risk_manager.ledger.open_position(...)
            return ExecutionResult(success=True, mode="paper", order_id=None)

        # Live mode: route to venue-specific execution
        if venue == "kalshi":
            return self._execute_kalshi(client, result)
        elif venue == "polymarket":
            return self._execute_polymarket(client, result)
        elif venue == "forecastex":
            return self._execute_forecastex(client, result)

        return ExecutionResult(success=False, error=f"Unknown venue: {venue}")

    def run_cycle(self, pods: List[BasePod]) -> List[ExecutionResult]:
        """Run scan_once() on all pods, execute results."""
        all_results = []
        for pod in pods:
            try:
                scan_results = pod.scan_once()
                for sr in scan_results:
                    exec_result = self.execute(sr)
                    all_results.append(exec_result)
            except Exception as e:
                all_results.append(ExecutionResult(success=False, error=str(e)))
        return all_results
```

---

## 8. Event Matcher (Cross-Venue)

For P-002 (Kalshi-Polymarket Scanner): matches the same event across venues.

```python
# src/cross_venue_matcher.py

@dataclass(frozen=True)
class CrossVenueMatch:
    """An event identified on both Kalshi and Polymarket."""
    kalshi_ticker: str
    kalshi_title: str
    polymarket_condition_id: str
    polymarket_question: str
    polymarket_token_yes: str
    polymarket_token_no: str
    fuzzy_score: float
    settlement_aligned: bool  # True if resolution criteria verified to match


class CrossVenueMatcher:
    """
    Matches events across Kalshi and Polymarket.

    Two-pass matching:
    1. Fuzzy title matching (existing _fuzzy_score logic)
    2. Settlement source verification (flags mismatches)

    Settlement mismatches are flagged but not auto-rejected —
    some mismatches are acceptable (same underlying, slightly
    different resolution timing). The pod decides.
    """

    def __init__(
        self,
        fuzzy_threshold: float = 80.0,
        settlement_check: bool = True,
    ):
        ...

    def match_all(
        self,
        kalshi_markets: List[Dict],
        polymarket_markets: List[PolymarketMarket],
    ) -> List[CrossVenueMatch]:
        ...
```

---

## 9. Config Extension

The existing `config.yaml` gets new sections:

```yaml
# Existing (unchanged)
kalshi:
  environment: demo
  base_url: https://demo-api.kalshi.co/trade-api/v2

odds_api:
  sports: ["americanfootball_nfl", "basketball_nba"]

# New sections
polymarket:
  environment: paper          # paper until US access opens
  host: https://clob.polymarket.com
  chain_id: 137
  # private_key: from POLYMARKET_PRIVATE_KEY env var
  min_edge_pct: 0.01         # Lower threshold (0% fees)

interactive_brokers:
  environment: paper
  host: 127.0.0.1
  port: 7497
  client_id: 10

pods:
  active:
    - P-001                   # Kalshi Moneyline Value (existing)
    - P-006                   # Sportsbook-Polymarket Consensus (paper)

  P-006:
    sports: ["americanfootball_nfl", "basketball_nba"]
    min_edge_pct: 0.01
    fee_pct: 0.0

paths:
  trade_log: data/trade_log.jsonl
  pod_logs: data/pods/       # Per-pod logs: data/pods/P-006.jsonl
```

---

## 10. Adjusted Build Order (Polymarket Paper, Kalshi/IB Live-First)

Given Polymarket US waitlist status:

```
IMMEDIATE (can generate revenue now):
  Week 1:  P-009 setup (bonus conversion tracker — manual + edge_calculator)
  Week 1:  P-010 setup (boost scanner — OddsJam integration)

WEEKS 2-3 (infrastructure + paper):
  Week 2:  Polymarket Client (paper mode)
  Week 2:  Polymarket Matcher
  Week 3:  P-006 paper trading (validates matching + edge detection)
  Week 3:  BasePod Framework (refactor scanner → KalshiMoneylinePod)

WEEKS 4-6 (live-capable pods on existing venues):
  Week 4:  IB ForecastEx Connector
  Week 5:  P-004 (ForecastEx-Kalshi Econ Arb) — live-capable immediately
  Week 6:  Nowcast Pipeline + P-012 (Macro Nowcast on Kalshi) — live-capable

WEEKS 7-8 (cross-venue):
  Week 7:  Cross-Venue Matcher + Dual-Leg Executor
  Week 8:  P-002 (Kalshi-Polymarket Scanner) — paper until Polymarket opens

WHEN POLYMARKET US OPENS:
  Switch P-006, P-002 from paper → live
  Deploy with USDC + Polygon gas
```

**Revenue timeline:**
- Week 1: $500-2K from promo pods (P-009, P-010)
- Week 5: First automated live trades on Kalshi+ForecastEx (P-004)
- Week 6: Kalshi economic event trades (P-012)
- Polymarket opens: P-006 + P-002 go live immediately (infrastructure pre-built)

---

## 11. Testing Strategy

Each new module gets tests mirroring existing patterns:

| Module | Test Count (est.) | Key Test Areas |
|--------|-------------------|----------------|
| `base_pod.py` | 20 | fingerprint, dedup, log write, _load_seen |
| `polymarket_client.py` | 35 | paper mode, live mode, get_price, get_book, place_order, cancel, from_config |
| `polymarket_matcher.py` | 30 | fuzzy matching, time window, sport filtering, rejection paths |
| `forecastex_client.py` | 25 | paper mode, live mode, BUY-only constraint, from_config |
| `pods/kalshi_moneyline.py` | 10 | Wrapper delegates to Scanner correctly, ScanResult conversion |
| `pods/polymarket_consensus.py` | 40 | Full scan_once pipeline, edge detection, risk checks, dedup, paper/live |
| `cross_venue_matcher.py` | 25 | Cross-venue matching, settlement verification, fuzzy scoring |
| `multi_executor.py` | 20 | Venue routing, paper mode, live mode per venue |

**Total: ~205 new tests**, bringing the project to ~955 total.

All tests use the existing pattern: mock clients with injectable adapters, `tempfile` for log paths, `_now_fn` for deterministic time.

---

## 12. File Structure

```
src/
├── base_pod.py                    # BasePod ABC + ScanResult dataclass
├── polymarket_client.py           # Polymarket CLOB wrapper
├── polymarket_matcher.py          # Odds API → Polymarket matching
├── forecastex_client.py           # IB ForecastEx wrapper
├── cross_venue_matcher.py         # Kalshi ↔ Polymarket event matching
├── multi_executor.py              # Venue-agnostic execution router
├── pods/
│   ├── __init__.py
│   ├── kalshi_moneyline.py        # P-001 (wraps existing Scanner)
│   ├── polymarket_consensus.py    # P-006
│   ├── forecastex_kalshi_arb.py   # P-004
│   └── macro_nowcast.py           # P-012
├── scanner.py                     # UNCHANGED (existing, still works standalone)
├── executor.py                    # UNCHANGED (existing, still works standalone)
├── matcher.py                     # UNCHANGED
├── edge_calculator.py             # UNCHANGED
├── risk_manager.py                # UNCHANGED
├── ... (all existing modules)
```

**Key principle**: No existing file is modified. New code lives in new files. `scanner.py` continues to work exactly as before. The `KalshiMoneylinePod` wrapper simply delegates to it.
