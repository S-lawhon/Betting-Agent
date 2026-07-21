"""
src/trade_store.py
──────────────────
Centralised in-memory trade store with indexed views and atomic writes.

Replaces the pattern of re-parsing the entire 62K+ line trade_log.jsonl
from disk in 7+ separate components every scan cycle.  All components
now read from this single shared instance instead.

Architecture
────────────
    store = TradeStore.from_log(Path("data/trade_logs/trade_log.jsonl"))

    # O(1) lookups
    store.get_by_fingerprint("abc123...")
    store.get_open_trades()
    store.get_trades_for_pod("P-006")

    # Atomic append (memory + disk)
    store.append(entry_dict)

    # Settlement updates
    store.record_settlement(fingerprint, outcome, pnl, settled_at)

Thread Safety
─────────────
All mutations are protected by a threading.Lock so the store can be
shared between the main loop, settlers, and the dashboard server thread.

Malformed Line Recovery
───────────────────────
On load, any line that fails JSON parsing is logged at WARNING level
and skipped — the store never crashes on a truncated/corrupt entry.
"""

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Field normalisation ──────────────────────────────────────────────

# Legacy entries use different field names for the same concept.
# This map standardises them so all downstream code can use canonical keys.
_FIELD_ALIASES = {
    "market_ticker": "market_id",
    "kalshi_prob":   "venue_prob",
}

# Fields that may appear under different names for outcome/pnl
_OUTCOME_FIELDS = ("outcome", "action")
_PNL_FIELDS     = ("pnl_usd", "pnl")


def normalize_entry(entry: dict) -> dict:
    """Return a copy of *entry* with canonical field names.

    - ``market_ticker`` → ``market_id`` (kept as alias too)
    - ``kalshi_prob`` → ``venue_prob``
    - Ensures ``pod_id`` / ``pod_name`` have defaults for legacy entries
    - Ensures ``pnl_usd`` is float if present
    """
    out = dict(entry)

    # Alias mapping (keep originals for backward compat, add canonical)
    for old_key, new_key in _FIELD_ALIASES.items():
        if old_key in out and new_key not in out:
            out[new_key] = out[old_key]

    # Legacy P-001 defaults
    if "pod_id" not in out:
        out["pod_id"] = "P-001"
    if "pod_name" not in out:
        out["pod_name"] = "Kalshi vs Sharp's Strategy"

    return out


# ── TradeStore ───────────────────────────────────────────────────────


class TradeStore:
    """Centralised in-memory trade log with indexed views.

    Args:
        log_path:   Path to the JSONL trade log file (for appends and initial load).
        _now_fn:    Injectable clock for testing.
        max_entries: Optional cap on how many *recent* full entries to hold in
            memory at load time (defence against OOM on very large logs).  When
            set, the loader streams the whole file but only retains the last
            ``max_entries`` entries as full dicts PLUS every still-open position,
            while keeping the complete fingerprint set for dedup.  ``None`` (the
            default) loads everything, preserving the original behaviour.
    """

    def __init__(
        self,
        log_path: Path,
        _now_fn: Optional[Callable[[], datetime]] = None,
        max_entries: Optional[int] = None,
    ):
        self._log_path = Path(log_path)
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))
        self._max_entries = max_entries
        self._lock = threading.Lock()

        # Primary storage — chronological order
        self._all: List[dict] = []

        # Indexes
        self._by_fingerprint: Dict[str, dict] = {}       # fp → entry
        self._by_ticker: Dict[str, List[dict]] = {}       # ticker → [entries]
        self._by_pod: Dict[str, List[dict]] = {}           # pod_id → [entries]
        self._placed: Dict[str, dict] = {}                 # fp → PLACED entries only
        self._settlements: Dict[str, dict] = {}            # fp → settlement entries
        self._settlement_by_ticker: Dict[str, dict] = {}   # ticker → settlement (fallback)
        self._seen_fingerprints: Set[str] = set()           # all fps for dedup

        # Open positions: fingerprints of PLACED entries with no settlement
        self._open_fps: Set[str] = set()

        # Stats
        self._load_errors = 0
        self._bounded = False        # True if last load applied a tail cap
        self._total_scanned = 0      # total non-blank lines streamed on last load

    # ── Loading ──────────────────────────────────────────────────────

    def load(self, max_entries: Optional[int] = None) -> "TradeStore":
        """Load entries from the JSONL log into memory.

        Malformed lines are skipped with a warning.  Returns self for chaining.

        Args:
            max_entries: Tail cap on full entries retained in memory.  Falls
                back to the instance-level ``max_entries`` (from ``__init__``)
                when omitted.  ``None``/0 means load everything.
        """
        if max_entries is None:
            max_entries = self._max_entries

        if not self._log_path.exists():
            logger.info("trade_store: log file not found, starting empty: %s", self._log_path)
            return self

        with self._lock:
            self._all.clear()
            self._by_fingerprint.clear()
            self._by_ticker.clear()
            self._by_pod.clear()
            self._placed.clear()
            self._settlements.clear()
            self._settlement_by_ticker.clear()
            self._seen_fingerprints.clear()
            self._open_fps.clear()
            self._load_errors = 0
            self._total_scanned = 0
            self._bounded = bool(max_entries and max_entries > 0)

            if self._bounded:
                self._load_bounded_unlocked(int(max_entries))
            else:
                self._load_full_unlocked()

            # Compute open positions: PLACED fps that have no settlement
            self._open_fps = set(self._placed.keys()) - set(self._settlements.keys())

            logger.info(
                "trade_store: loaded %d entries (%d placed, %d settlements, "
                "%d open, %d errors skipped)%s",
                len(self._all),
                len(self._placed),
                len(self._settlements),
                len(self._open_fps),
                self._load_errors,
                (
                    f" [bounded: retained {len(self._all)} of {self._total_scanned} "
                    f"scanned, full dedup set of {len(self._seen_fingerprints)} fps]"
                    if self._bounded else ""
                ),
            )

        return self

    def _load_full_unlocked(self) -> None:
        """Stream the entire log and index every entry.  Caller holds the lock."""
        try:
            with open(self._log_path, "r") as fh:
                for line_no, raw in enumerate(fh, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    self._total_scanned += 1
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        self._load_errors += 1
                        logger.warning(
                            "trade_store: skipping malformed line %d: %s",
                            line_no, exc,
                        )
                        continue
                    self._index_entry(normalize_entry(entry))
        except OSError as exc:
            logger.error("trade_store: could not read %s: %s", self._log_path, exc)

    def _load_bounded_unlocked(self, max_entries: int) -> None:
        """Stream the whole log but retain only a bounded working set.

        Retained in memory:
          * the most recent ``max_entries`` entries (full dicts), and
          * every still-open PLACED position regardless of age, so settlement
            and risk bootstrap remain correct.

        The complete set of fingerprints is always retained (as strings) so
        deduplication still covers the full history of the active log.  Caller
        holds the lock.
        """
        tail: Deque[dict] = deque(maxlen=max_entries)
        open_candidates: Dict[str, dict] = {}   # fp → full PLACED entry, still open
        all_fps: Set[str] = set()

        try:
            with open(self._log_path, "r") as fh:
                for line_no, raw in enumerate(fh, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    self._total_scanned += 1
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        self._load_errors += 1
                        logger.warning(
                            "trade_store: skipping malformed line %d: %s",
                            line_no, exc,
                        )
                        continue

                    entry = normalize_entry(entry)
                    fp = entry.get("fingerprint", "")
                    action = (entry.get("action") or "").upper()
                    outcome = (entry.get("outcome") or "").upper()

                    if fp:
                        all_fps.add(fp)
                        if action == "PLACED":
                            open_candidates[fp] = entry
                        if outcome in ("WIN", "LOSS", "VOID"):
                            open_candidates.pop(fp, None)

                    tail.append(entry)
        except OSError as exc:
            logger.error("trade_store: could not read %s: %s", self._log_path, exc)

        # Retained working set = tail entries ∪ still-open positions.
        retained: List[dict] = list(tail)
        retained_ids = {id(e) for e in retained}
        for entry in open_candidates.values():
            if id(entry) not in retained_ids:
                retained.append(entry)
                retained_ids.add(id(entry))

        # Index in chronological order for stable _all ordering.
        retained.sort(key=lambda e: e.get("timestamp_utc", ""))
        for entry in retained:
            self._index_entry(entry)

        # Dedup must reflect the ENTIRE scanned history, not just the tail.
        self._seen_fingerprints = all_fps

    def _index_entry(self, entry: dict) -> None:
        """Add a single entry to all indexes.  Caller must hold self._lock."""
        self._all.append(entry)

        fp = entry.get("fingerprint", "")
        ticker = entry.get("market_id") or entry.get("market_ticker", "")
        pod_id = entry.get("pod_id", "")
        action = (entry.get("action") or "").upper()
        outcome = (entry.get("outcome") or "").upper()

        if fp:
            self._by_fingerprint[fp] = entry
            self._seen_fingerprints.add(fp)

        if ticker:
            self._by_ticker.setdefault(ticker, []).append(entry)

        if pod_id:
            self._by_pod.setdefault(pod_id, []).append(entry)

        if action == "PLACED":
            if fp:
                self._placed[fp] = entry

        if outcome in ("WIN", "LOSS", "VOID"):
            if fp:
                self._settlements[fp] = entry
            if ticker:
                self._settlement_by_ticker[ticker] = entry

    # ── Queries (all O(1) or O(k) where k = result set size) ────────

    def get_by_fingerprint(self, fp: str) -> Optional[dict]:
        """Return entry with this fingerprint, or None."""
        with self._lock:
            return self._by_fingerprint.get(fp)

    def get_by_ticker(self, ticker: str) -> List[dict]:
        """Return all entries for this market ticker/id."""
        with self._lock:
            return list(self._by_ticker.get(ticker, []))

    def get_trades_for_pod(self, pod_id: str) -> List[dict]:
        """Return all entries for a specific pod."""
        with self._lock:
            return list(self._by_pod.get(pod_id, []))

    def get_placed_entries(self, pod_id: Optional[str] = None) -> List[dict]:
        """Return all PLACED entries, optionally filtered by pod_id."""
        with self._lock:
            if pod_id:
                return [e for e in self._placed.values() if e.get("pod_id") == pod_id]
            return list(self._placed.values())

    def get_open_trades(self, pod_id: Optional[str] = None) -> List[dict]:
        """Return PLACED entries that have no settlement record.

        Args:
            pod_id: Optional — filter to a single pod.

        Returns:
            List of entry dicts, chronological order.
        """
        with self._lock:
            entries = [self._placed[fp] for fp in self._open_fps if fp in self._placed]
            if pod_id:
                entries = [e for e in entries if e.get("pod_id") == pod_id]
            return sorted(entries, key=lambda e: e.get("timestamp_utc", ""))

    def get_settlement(self, fp: str) -> Optional[dict]:
        """Return the settlement record for a fingerprint, or None."""
        with self._lock:
            return self._settlements.get(fp)

    def get_settlement_by_ticker(self, ticker: str) -> Optional[dict]:
        """Fallback: return the most recent settlement for a ticker."""
        with self._lock:
            return self._settlement_by_ticker.get(ticker)

    def is_fingerprint_seen(self, fp: str) -> bool:
        """Check if this fingerprint has been seen (placed or settled)."""
        with self._lock:
            return fp in self._seen_fingerprints

    def is_position_open(self, market_id: str) -> bool:
        """Check if there's an unsettled PLACED trade for this market."""
        with self._lock:
            for fp in self._open_fps:
                entry = self._placed.get(fp, {})
                mid = entry.get("market_id") or entry.get("market_ticker", "")
                if mid == market_id:
                    return True
            return False

    def get_open_market_ids(self) -> Set[str]:
        """Return set of market IDs with unsettled positions."""
        with self._lock:
            ids = set()
            for fp in self._open_fps:
                entry = self._placed.get(fp, {})
                mid = entry.get("market_id") or entry.get("market_ticker", "")
                if mid:
                    ids.add(mid)
            return ids

    @property
    def seen_fingerprints(self) -> Set[str]:
        """Return a copy of all seen fingerprints (for dedup)."""
        with self._lock:
            return set(self._seen_fingerprints)

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._all)

    @property
    def placed_count(self) -> int:
        with self._lock:
            return len(self._placed)

    @property
    def open_count(self) -> int:
        with self._lock:
            return len(self._open_fps)

    @property
    def settlement_count(self) -> int:
        with self._lock:
            return len(self._settlements)

    @property
    def bounded(self) -> bool:
        """True if the last load applied a tail cap (max_entries)."""
        return self._bounded

    @property
    def total_scanned(self) -> int:
        """Total non-blank lines streamed from disk on the last load."""
        return self._total_scanned

    # ── Mutations (append + settle) ──────────────────────────────────

    def append(self, entry: dict) -> None:
        """Add a new entry to the store and atomically persist to disk.

        The entry is normalised, indexed in memory, and then flushed to
        the JSONL file with ``fsync()`` to ensure durability.

        Args:
            entry: Trade log entry dict (PLACED, SKIPPED_*, WIN, LOSS, VOID, etc.)
        """
        normalised = normalize_entry(entry)

        with self._lock:
            self._index_entry(normalised)

            # Update open positions set
            fp = normalised.get("fingerprint", "")
            action = (normalised.get("action") or "").upper()
            outcome = (normalised.get("outcome") or "").upper()

            if action == "PLACED" and fp:
                self._open_fps.add(fp)
            elif outcome in ("WIN", "LOSS", "VOID") and fp:
                self._open_fps.discard(fp)

            # Atomic persist: write + flush + fsync
            self._persist_entry(normalised)

    def index_only(self, entry: dict) -> None:
        """Index an entry in memory WITHOUT persisting to disk.

        Use this when the entry has already been written to the JSONL file
        by another component (e.g. the Settler) and we just need the
        in-memory indexes updated so the dashboard sees the change.
        """
        normalised = normalize_entry(entry)
        with self._lock:
            self._index_entry(normalised)
            fp = normalised.get("fingerprint", "")
            action = (normalised.get("action") or "").upper()
            outcome = (normalised.get("outcome") or "").upper()
            if action == "PLACED" and fp:
                self._open_fps.add(fp)
            elif outcome in ("WIN", "LOSS", "VOID") and fp:
                self._open_fps.discard(fp)

    def record_settlement(
        self,
        fingerprint: str,
        outcome: str,
        pnl: float,
        settled_at: Optional[str] = None,
    ) -> bool:
        """Record a settlement for an existing PLACED trade.

        Creates a settlement entry in the log and updates all indexes.

        Args:
            fingerprint: Fingerprint of the original PLACED trade.
            outcome:     "WIN", "LOSS", or "VOID".
            pnl:         Profit or loss in USD (negative for losses).
            settled_at:  ISO timestamp (defaults to now).

        Returns:
            True if the trade was found and settled, False if already
            settled or fingerprint not found.
        """
        with self._lock:
            if fingerprint in self._settlements:
                logger.warning(
                    "trade_store: fingerprint %s already settled", fingerprint[:16]
                )
                return False

            placed = self._placed.get(fingerprint)
            if placed is None:
                logger.warning(
                    "trade_store: fingerprint %s not found in placed trades",
                    fingerprint[:16],
                )
                return False

            ts = settled_at or self._now_fn().isoformat()
            settlement_entry = {
                "fingerprint": fingerprint,
                "pod_id": placed.get("pod_id", ""),
                "market_id": placed.get("market_id", ""),
                "market_ticker": placed.get("market_ticker", placed.get("market_id", "")),
                "outcome": outcome,
                "pnl_usd": pnl,
                "pnl": pnl,
                "settled_at_utc": ts,
                "timestamp_utc": ts,
                "action": outcome,  # legacy compat
            }

            self._index_entry(settlement_entry)
            self._open_fps.discard(fingerprint)
            self._persist_entry(settlement_entry)

            logger.info(
                "trade_store: settled %s → %s pnl=%.2f",
                fingerprint[:16], outcome, pnl,
            )
            return True

    def _persist_entry(self, entry: dict) -> None:
        """Append entry to disk with fsync for durability.  Caller holds lock."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                str(self._log_path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
            try:
                line = json.dumps(entry, default=str) + "\n"
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.error(
                "trade_store: CRITICAL — failed to persist entry: %s", exc
            )

    # ── Dashboard helpers ────────────────────────────────────────────

    def get_recent_placed_with_status(self, max_trades: int = 2000) -> List[dict]:
        """Return PLACED entries annotated with settlement status.

        Mirrors the logic of main.py's ``_read_recent_placed_trades()``
        but reads entirely from memory — no disk I/O.

        Each entry gets:
          - ``status``: "OPEN", "WIN", "LOSS", or "VOID"
          - ``pnl_usd``: float (0.0 if OPEN)
          - ``settled_at_utc``: str (empty if OPEN)

        Returns:
            List of entry dicts, newest-first, capped at *max_trades*.
        """
        with self._lock:
            placed = list(self._placed.values())

        result = []
        for entry in placed:
            annotated = dict(entry)
            fp = entry.get("fingerprint", "")
            ticker = entry.get("market_id") or entry.get("market_ticker", "")

            # Resolve: fingerprint first, ticker fallback (with pod_id guard)
            settlement = None
            pod_id = entry.get("pod_id", "")
            with self._lock:
                if fp and fp in self._settlements:
                    settlement = self._settlements[fp]
                elif ticker and ticker in self._settlement_by_ticker:
                    candidate = self._settlement_by_ticker[ticker]
                    # Only use ticker-based match if pod_id matches (or
                    # the settlement has no pod_id for legacy records).
                    candidate_pod = candidate.get("pod_id", "")
                    if not candidate_pod or not pod_id or candidate_pod == pod_id:
                        settlement = candidate

            if settlement:
                annotated["status"] = (
                    settlement.get("outcome")
                    or settlement.get("action", "")
                ).upper()
                annotated["pnl_usd"] = float(
                    settlement.get("pnl_usd", 0)
                    or settlement.get("pnl", 0)
                    or 0
                )
                annotated["settled_at_utc"] = (
                    settlement.get("settled_at_utc")
                    or settlement.get("timestamp_utc", "")
                )
            else:
                annotated["status"] = "OPEN"
                annotated["pnl_usd"] = 0.0
                annotated["settled_at_utc"] = ""

            result.append(annotated)

        # newest-first, capped
        result.sort(key=lambda e: e.get("timestamp_utc", ""), reverse=True)
        return result[:max_trades]

    def compute_settlement_summary(
        self,
        trades: Optional[List[dict]] = None,
    ) -> dict:
        """Compute settlement P&L summary from in-memory data.

        Mirrors ``_compute_settlement_summary()`` from main.py.

        Args:
            trades: Optional pre-annotated trades list.  If None, calls
                    ``get_recent_placed_with_status()``.

        Returns:
            Dict with: total_pnl, total_settled, wins, losses, voids, win_rate.
        """
        if trades is None:
            trades = self.get_recent_placed_with_status()

        wins = 0
        losses = 0
        voids = 0
        total_pnl = 0.0

        for t in trades:
            status = t.get("status", "OPEN")
            pnl = float(t.get("pnl_usd", 0) or 0)
            if status == "WIN":
                wins += 1
                total_pnl += pnl
            elif status == "LOSS":
                losses += 1
                total_pnl += pnl
            elif status == "VOID":
                voids += 1

        resolved = wins + losses
        win_rate = wins / resolved if resolved > 0 else 0.0

        return {
            "total_pnl": round(total_pnl, 2),
            "total_settled": resolved + voids,
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "win_rate": round(win_rate, 4),
        }

    # ── Bootstrap helpers (for AggregateRiskGuard + CapitalAllocator) ─

    def get_open_positions_for_bootstrap(self) -> List[dict]:
        """Return open positions in the format expected by AggregateRiskGuard.

        Each entry has: pod_id, venue, market_id/market_ticker,
        position_size_usd, and mode (so the risk guard can exclude
        paper-mode trades from aggregate limits).
        """
        with self._lock:
            result = []
            for fp in self._open_fps:
                entry = self._placed.get(fp, {})
                result.append({
                    "pod_id": entry.get("pod_id", "P-001"),
                    "venue": entry.get("venue", "kalshi"),
                    "market_id": entry.get("market_id") or entry.get("market_ticker", ""),
                    "market_ticker": entry.get("market_ticker") or entry.get("market_id", ""),
                    "position_size_usd": float(entry.get("position_size_usd", 0)),
                    "mode": entry.get("mode", ""),
                })
            return result

    def get_settlements_for_bootstrap(self) -> List[dict]:
        """Return all settlement entries for CapitalAllocator bootstrap."""
        with self._lock:
            return list(self._settlements.values())

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_log(
        cls,
        log_path: Path,
        _now_fn: Optional[Callable[[], datetime]] = None,
        max_entries: Optional[int] = None,
    ) -> "TradeStore":
        """Create and load a TradeStore from a JSONL file.

        Args:
            log_path: Path to trade_log.jsonl.
            _now_fn:  Injectable clock for testing.
            max_entries: Optional tail cap on full entries retained in memory
                (see ``__init__``).  Open positions and the full fingerprint
                set are always preserved.

        Returns:
            Loaded TradeStore ready for queries.
        """
        store = cls(log_path=log_path, _now_fn=_now_fn, max_entries=max_entries)
        store.load()
        return store
