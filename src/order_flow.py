"""
src/order_flow.py
─────────────────
Order-flow toxicity metrics for the maker pods (P-016, P-017M).

VPIN (Volume-Synchronized Probability of Informed Trading), thin-book
variant, plus a cheaper rolling one-sidedness.  Both are computed from
the public prints the makers ALREADY fetch (``taker_side`` + ``count``
out of ``KalshiPublic.trades_since``), so no new data source is needed.

Rationale (Deep Research 2026-07): single-name in-play Kalshi markets
carry ~2x the informed price impact of broad markets (Stanford 41.6M-
trade study), and one-sided order flow predicts maker losses there.
These metrics MEASURE that toxicity so ``maker_diagnostics.py`` can test
whether high-VPIN fills are adversely selected.  Telemetry only — no pod
consumes them for quoting yet (an optional Phase-2 pull/widen trigger is
a separate change, on a separate sample).

Aggressor classification (Kalshi ``taker_side``):
    taker_side == "yes"  → taker lifted the ask → BUY  pressure
    taker_side == "no"   → taker hit  the bid   → SELL pressure
Prints with any other/blank taker_side are ignored (unclassifiable).

Two numbers, logged side by side so the diagnostics can decide later
which predicts losses better:

* ``vpin`` — mean absolute buy/sell imbalance over the last ``n_buckets``
  EQUAL-VOLUME buckets of ``bucket_size`` contracts.  Volume-clocked, so
  it reacts to trade intensity rather than wall-clock; slow to warm up in
  a thin book (needs a full bucket before it reports anything).
* ``one_sidedness`` — the same imbalance over a rolling window of the last
  ``roll_window`` contracts.  Cheaper and reacts faster; useful precisely
  where buckets fill slowly.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, Iterable, Optional, Tuple


def classify(taker_side: Optional[str]) -> Optional[str]:
    """Aggressor direction of a print, or None if unclassifiable."""
    if taker_side == "yes":
        return "buy"
    if taker_side == "no":
        return "sell"
    return None


class OrderFlowTracker:
    """Stateful per-book order-flow toxicity tracker.

    Feed it public prints with :meth:`update`; read :attr:`vpin` and
    :attr:`one_sidedness` at any time.  Both return ``None`` until enough
    volume has accumulated to be meaningful (a closed bucket / any
    classified volume, respectively).
    """

    def __init__(self, bucket_size: float = 50.0, n_buckets: int = 20,
                 roll_window: float = 200.0):
        if bucket_size <= 0:
            raise ValueError("bucket_size must be positive")
        if n_buckets <= 0:
            raise ValueError("n_buckets must be positive")
        if roll_window <= 0:
            raise ValueError("roll_window must be positive")
        self.bucket_size = float(bucket_size)
        self.n_buckets = int(n_buckets)
        self.roll_window = float(roll_window)
        # Current (partial) volume bucket.
        self._buy = 0.0
        self._sell = 0.0
        self._filled = 0.0
        self._imbalances: Deque[float] = deque(maxlen=self.n_buckets)
        # Rolling window of the last `roll_window` contracts.
        self._roll: Deque[Tuple[str, float]] = deque()
        self._roll_buy = 0.0
        self._roll_sell = 0.0
        self._roll_total = 0.0

    # ── Ingest ───────────────────────────────────────────────────────

    def update(self, prints: Iterable[Dict[str, Any]]) -> None:
        """Absorb a batch of prints (each a ``trades_since`` dict).

        Idempotent only w.r.t. empty batches — the caller must pass each
        print exactly once.  In the pods this is guaranteed by updating
        from the single ``_fetch_trades`` (P-016) / ``_check_fills``
        (P-017M) fetch site, so a print is never double-counted.
        """
        for p in prints:
            side = classify(p.get("taker_side"))
            cnt = float(p.get("count") or 0.0)
            if side is None or cnt <= 0:
                continue
            self._add_bucket(side, cnt)
            self._add_roll(side, cnt)

    def _add_bucket(self, side: str, cnt: float) -> None:
        """Accumulate a single-sided print into equal-volume buckets,
        splitting it across bucket boundaries as needed."""
        remaining = cnt
        while remaining > 0:
            room = self.bucket_size - self._filled
            take = remaining if remaining < room else room
            if side == "buy":
                self._buy += take
            else:
                self._sell += take
            self._filled += take
            remaining -= take
            if self._filled >= self.bucket_size - 1e-9:
                tot = self._buy + self._sell
                if tot > 0:
                    self._imbalances.append(abs(self._buy - self._sell) / tot)
                self._buy = self._sell = self._filled = 0.0

    def _add_roll(self, side: str, cnt: float) -> None:
        """Push into the rolling contract window, trimming the oldest
        contracts (fractionally, if a print straddles the horizon)."""
        self._roll.append((side, cnt))
        if side == "buy":
            self._roll_buy += cnt
        else:
            self._roll_sell += cnt
        self._roll_total += cnt
        while self._roll_total > self.roll_window and self._roll:
            old_side, old_cnt = self._roll[0]
            excess = self._roll_total - self.roll_window
            drop = old_cnt if old_cnt <= excess else excess
            if old_side == "buy":
                self._roll_buy -= drop
            else:
                self._roll_sell -= drop
            self._roll_total -= drop
            if drop >= old_cnt - 1e-9:
                self._roll.popleft()
            else:
                self._roll[0] = (old_side, old_cnt - drop)

    # ── Read ─────────────────────────────────────────────────────────

    @property
    def vpin(self) -> Optional[float]:
        """Mean imbalance over the last ``n_buckets`` closed buckets, or
        None if no bucket has closed yet.  0 = balanced, 1 = fully toxic."""
        if not self._imbalances:
            return None
        return sum(self._imbalances) / len(self._imbalances)

    @property
    def one_sidedness(self) -> Optional[float]:
        """Imbalance over the rolling contract window, or None if empty.
        0 = balanced, 1 = fully one-sided."""
        tot = self._roll_buy + self._roll_sell
        if tot <= 0:
            return None
        return abs(self._roll_buy - self._roll_sell) / tot

    def snapshot(self) -> Tuple[Optional[float], Optional[float]]:
        """(vpin, one_sidedness) — convenience for stamping records."""
        return self.vpin, self.one_sidedness
