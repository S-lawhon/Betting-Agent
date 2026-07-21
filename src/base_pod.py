"""
src/base_pod.py
───────────────
BasePod abstract class and ScanResult dataclass.

BasePod provides the interface that all betting pods implement:
    - scan_once() → List[ScanResult]
    - venue_name() → str
    - from_config() class method

ScanResult generalises the existing Scanner trade-log schema for
multi-venue, multi-pod operation.  Field mapping from the original:
    sport        → market_type
    market_ticker → market_id
    kalshi_prob   → venue_prob
    (new)         → pod_id, pod_name, venue, extra
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import hashlib
import json
import logging


@dataclass(frozen=True)
class ScanResult:
    """One opportunity found (or skipped) by a pod's scan cycle."""

    fingerprint: str
    timestamp_utc: str
    pod_id: str               # e.g. "P-001", "P-006"
    pod_name: str             # e.g. "Kalshi Moneyline Value"
    mode: str                 # "paper" or "live"
    venue: str                # "kalshi", "polymarket", "forecastex", "multi"
    market_type: str          # "sports_moneyline", "economics_cpi", etc.
    event: str                # Human-readable event description
    market_id: str            # Venue-specific market/ticker ID
    side: str                 # "YES", "NO", "BUY", "SELL"
    fair_prob: float          # Pod's estimated fair probability
    venue_prob: float         # Price on the execution venue (0-1)
    edge_pct: float           # (fair - venue) / venue or similar
    ev: float                 # Expected value as a fraction of stake (EV per $1)
    kelly_fraction: float     # Kelly-optimal fraction of bankroll
    position_size_usd: float  # Actual dollar size after caps
    action: str               # PLACED, SKIPPED_EDGE, SKIPPED_RISK, SKIPPED_DUPLICATE
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    skip_reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to plain dict for JSON serialisation."""
        return asdict(self)


class BasePod(ABC):
    """Abstract base class for all betting pods.

    Each pod scans a specific venue/strategy combination.
    Shared infrastructure (fingerprinting, dedup, logging) lives here;
    strategy-specific logic lives in the concrete subclass.
    """

    def __init__(
        self,
        pod_id: str,
        pod_name: str,
        edge_calculator,
        risk_manager,
        trade_log_path: Path,
        mode: str = "paper",
        _now_fn: Optional[Callable[[], datetime]] = None,
        aggregate_risk=None,
        trade_store=None,
        min_kelly_fraction: Optional[float] = None,
    ):
        self.pod_id = pod_id
        self.pod_name = pod_name
        self.edge_calculator = edge_calculator
        self.risk_manager = risk_manager
        self.trade_log_path = Path(trade_log_path)
        self.mode = mode
        self.aggregate_risk = aggregate_risk
        self._trade_store = trade_store
        self._min_kelly_fraction = min_kelly_fraction  # per-pod override
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))
        self._seen_fingerprints: Set[str] = set()
        self._open_market_ids: Set[str] = set()   # markets with open positions
        self._load_seen_from_log()

    # ── Abstract methods (each pod implements these) ─────────────────

    @abstractmethod
    def scan_once(self) -> List[ScanResult]:
        """Run one scan cycle.  Return list of ScanResults."""
        ...

    @abstractmethod
    def venue_name(self) -> str:
        """Return primary execution venue name."""
        ...

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict, **overrides) -> "BasePod":
        """Construct pod from YAML config dict."""
        ...

    # ── Shared infrastructure (inherited by all pods) ────────────────

    def make_fingerprint(self, market_id: str, side: str) -> str:
        """SHA-256 fingerprint per market/side/hour (dedup window)."""
        now = self._now_fn()
        key = f"{market_id}|{side}|{now.strftime('%Y%m%d%H')}"
        return hashlib.sha256(key.encode()).hexdigest()

    def is_duplicate(self, fingerprint: str) -> bool:
        """Check if this opportunity has already been acted on."""
        if self._trade_store is not None:
            return self._trade_store.is_fingerprint_seen(fingerprint)
        return fingerprint in self._seen_fingerprints

    def has_open_position(self, market_id: str) -> bool:
        """Check if there is already an open position on this market."""
        if self._trade_store is not None:
            return self._trade_store.is_position_open(market_id)
        return market_id in self._open_market_ids

    def mark_seen(self, fingerprint: str) -> None:
        """Record fingerprint as acted-upon."""
        self._seen_fingerprints.add(fingerprint)

    def mark_position_open(self, market_id: str) -> None:
        """Track that we have an open position on this market."""
        self._open_market_ids.add(market_id)

    def mark_position_closed(self, market_id: str) -> None:
        """Remove a market from the open positions set."""
        self._open_market_ids.discard(market_id)

    def get_bankroll(self) -> float:
        """Read current bankroll from the risk manager's ledger.

        Falls back to DEFAULT_BANKROLL if no ledger is available.
        This eliminates the 4-line try/getattr pattern duplicated in every pod.
        """
        from src.constants import DEFAULT_BANKROLL
        if hasattr(self.risk_manager, "ledger"):
            return getattr(self.risk_manager.ledger, "bankroll", DEFAULT_BANKROLL)
        return DEFAULT_BANKROLL

    def compute_position_size(self, kelly_fraction: float) -> float:
        """Compute capped position size from Kelly fraction.

        Uses the bankroll from the risk manager's ledger and caps at
        max_position_usd (set by CapitalAllocator) or max_bet_pct * bankroll.

        Returns:
            Position size in USD.  Returns 0 when Kelly is below the
            min_kelly_fraction threshold (low-conviction filter).
        """
        from src.constants import DEFAULT_MAX_BET_PCT, MIN_KELLY_FRACTION
        bankroll = self.get_bankroll()

        # ── Low-conviction filter ──────────────────────────────────────
        # Per-pod override takes precedence over global default.
        min_kelly = (
            self._min_kelly_fraction
            if self._min_kelly_fraction is not None
            else MIN_KELLY_FRACTION
        )
        if kelly_fraction < min_kelly:
            return 0.0

        max_bet_pct = getattr(self.risk_manager, "max_bet_pct", DEFAULT_MAX_BET_PCT)
        max_pos = getattr(self.risk_manager, "max_position_usd", None)
        if max_pos is None or max_pos <= 0:
            max_pos = bankroll * max_bet_pct

        return min(kelly_fraction * bankroll, max_pos)

    def check_aggregate_risk(
        self, venue: str, position_size_usd: float,
        market_id: Optional[str] = None,
    ) -> bool:
        """Check portfolio-level risk limits before placing a trade.

        Returns True if the trade is approved, False if it would breach limits.
        If no aggregate_risk guard is set, always returns True.

        Args:
            market_id: Pass this when the pod will place the trade right
                after approval.  It RESERVES the exposure, so the rest of
                this scan sees it — without a reservation, a pod placing
                its whole book in one cycle has every trade checked
                against the previous cycle's exposure and the guard
                approves all of them.  Unconverted reservations are
                released at the cycle boundary, so an approval the pod
                later abandons costs nothing.
        """
        if self.aggregate_risk is None:
            return True

        reserve = getattr(self.aggregate_risk, "reserve_trade", None)
        if market_id and callable(reserve):
            return reserve(
                pod_id=self.pod_id,
                venue=venue,
                market_id=market_id,
                position_size_usd=position_size_usd,
            )

        return self.aggregate_risk.check_trade(
            pod_id=self.pod_id,
            venue=venue,
            position_size_usd=position_size_usd,
        )

    def release_aggregate_risk(self, market_id: str) -> None:
        """Release a reservation for a trade the pod decided not to place.

        Optional — ``update_post_cycle`` sweeps unconverted reservations
        anyway.  Use this when a pod abandons a trade and wants the
        capacity back within the SAME scan.
        """
        if self.aggregate_risk is None or not market_id:
            return
        release = getattr(self.aggregate_risk, "release_reservation", None)
        if callable(release):
            release(market_id)

    def write_log(self, result: ScanResult) -> None:
        """Append ScanResult to JSONL trade log.

        When a TradeStore is available, delegates to store.append() which
        provides atomic writes with fsync and in-memory index updates.
        Falls back to direct file append for backward compatibility.

        Includes ``market_ticker`` as an alias for ``market_id`` so the
        legacy Kalshi settler (which keys on ``market_ticker``) can find
        and settle positions placed by BasePod-derived pods.
        """
        entry = result.to_dict()
        # Settler compatibility: it looks for market_ticker, not market_id
        if "market_ticker" not in entry and entry.get("market_id"):
            entry["market_ticker"] = entry["market_id"]
        # Settler uses kalshi_prob for P&L calc; BasePod uses venue_prob
        if "kalshi_prob" not in entry and entry.get("venue_prob") is not None:
            entry["kalshi_prob"] = entry["venue_prob"]
        # Settler uses game_time for auto-void stale detection
        extra = entry.get("extra") or {}
        if "game_time" not in entry and extra.get("game_time"):
            entry["game_time"] = extra["game_time"]
        # Settler _check_scores needs sport at the top level
        if "sport" not in entry and extra.get("sport"):
            entry["sport"] = extra["sport"]
        # Settler _check_scores needs yes_side for winner determination
        if "yes_side" not in entry and extra.get("kalshi_yes_side"):
            entry["yes_side"] = extra["kalshi_yes_side"]

        if self._trade_store is not None:
            try:
                self._trade_store.append(entry)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "base_pod: write_log to TradeStore failed: %s", exc
                )
            return

        try:
            with open(self.trade_log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass  # Write error does not crash scan_once

    def _load_seen_from_log(self) -> None:
        """Reload fingerprints and open positions from existing log.

        When a TradeStore is available, reads directly from its in-memory
        indexes — no disk I/O.  Falls back to full file parse otherwise.
        """
        if self._trade_store is not None:
            self._seen_fingerprints = self._trade_store.seen_fingerprints
            self._open_market_ids = self._trade_store.get_open_market_ids()
            return

        if not self.trade_log_path.exists():
            return
        try:
            placed_markets: dict = {}  # market_id → fingerprint
            settled_markets: Set[str] = set()

            for line in self.trade_log_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                action = (rec.get("action") or "").upper()
                outcome = (rec.get("outcome") or "").upper()

                if action == "PLACED":
                    fp = rec.get("fingerprint")
                    if fp:
                        self._seen_fingerprints.add(fp)
                    mid = rec.get("market_id") or rec.get("market_ticker") or ""
                    if mid:
                        placed_markets[mid] = fp

                # Track settlements to know which positions are closed
                if outcome in ("WIN", "LOSS", "VOID"):
                    mid = rec.get("market_id") or rec.get("market_ticker") or ""
                    if mid:
                        settled_markets.add(mid)

            # Open positions = placed but not settled
            self._open_market_ids = set(placed_markets.keys()) - settled_markets
        except OSError:
            pass
