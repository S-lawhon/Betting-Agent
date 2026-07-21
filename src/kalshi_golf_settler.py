"""
src/kalshi_golf_settler.py
──────────────────────────
Settles P-017 golf top-N trades by polling Kalshi's public API.

Follows the KalshiTennisSettler / PolymarketSettler pattern: read the
pod's JSONL log for unresolved PLACED entries, write WIN/LOSS/VOID
records back to the same log, return them so the engine loop can sync
the allocator, risk guard, trade store, and the pod's open-position slot.

Three golf-specific behaviours that the tennis settler does NOT have
(each verified against the live API, 2026-07-20):

1.  ``status="closed"`` is NOT settled.  When a tournament ends, top-N
    markets sit in ``closed`` with ``result=""`` for up to ~a day before
    Kalshi finalizes them (observed: 156/156 closed KXPGATOP20 markets
    had an empty result).  The tennis settler's ``result or "void"``
    fallback would book every one of those as a VOID and silently zero
    the P&L for a whole tournament.  We settle ONLY on a populated
    ``result`` and let the stale guard be the backstop.

2.  ``result="scalar"`` is a real, recurring third value (9/200 settled
    markets).  These are competitors who never teed off — the market
    carries an empty ``expiration_value`` and zero open interest, i.e.
    it was cancelled rather than resolved.  Correct treatment is VOID
    (stake refunded), but we classify it explicitly so it shows up in
    the log as ``withdrawn`` instead of being swept into a generic
    unknown-result bucket.

3.  P&L is booked NET of the taker fee already paid at placement.  The
    P-017 validation gate is "realized net edge vs the +6.8c/contract
    backtest baseline", and that baseline is net of fees — so a gross
    number here would not be comparable to the thing it is measured
    against.  ``pnl_usd`` is net; ``pnl_gross_usd`` and ``fees_usd`` are
    recorded alongside it.

Note on ties: Kalshi top-N pays a tie in FULL (a 5-way T-8 settles all
five YES).  That is the whole economic thesis behind P-017, and it needs
no special handling here — the per-market ``result`` already reflects
it.  What it does mean is that an event can settle far more than N
markets YES, so nothing in this settler may assume a fixed YES count.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.kalshi_public import KalshiPublic

logger = logging.getLogger(__name__)

# Record-level status values (the query param vocabulary differs:
# open/closed/settled are accepted as filters, records report
# active/closed/finalized).
RESOLVED_STATUSES = ("settled", "finalized", "determined")
PENDING_STATUSES = ("closed",)

# Kalshi reports this instead of yes/no for a market that was cancelled
# rather than resolved (golfer withdrew before teeing off).
SCALAR_RESULT = "scalar"


def _calc_outcome_pnl(
    side: str, result: str, size_usd: float, fill_price: float,
) -> Tuple[str, float]:
    """Outcome label and GROSS P&L for a settled Kalshi binary.

    P-017 only buys YES, but NO is handled for completeness.  fill_price
    is the YES price paid.  WIN pnl is profit only (stake excluded),
    LOSS is -stake, VOID is 0 — matching the tennis/Polymarket
    convention so downstream accounting stays uniform.
    """
    result_l = (result or "").strip().lower()
    side_u = (side or "YES").strip().upper()

    if result_l not in ("yes", "no"):
        return "VOID", 0.0

    won = (result_l == "yes") == (side_u == "YES")
    if won:
        if side_u == "YES":
            pnl = size_usd * (1.0 - fill_price) / max(fill_price, 1e-9)
        else:
            no_price = 1.0 - fill_price
            pnl = size_usd * fill_price / max(no_price, 1e-9)
        return "WIN", round(pnl, 2)
    return "LOSS", -round(size_usd, 2)


class KalshiGolfSettler:
    """Polls Kalshi public API to settle P-017 golf top-N positions."""

    # Void a position still unresolved this long after its event close.
    # Tournaments finalize within ~a day; 10 days is a wide margin that
    # still stops a stuck market from pinning an open-position slot
    # forever.
    STALE_DAYS_AFTER_CLOSE = 10.0

    def __init__(
        self,
        client: Optional[KalshiPublic] = None,
        log_path: Path = Path("data/pods/P-017.jsonl"),
        pod_id: str = "P-017",
        _now_fn: Optional[Callable[[], datetime]] = None,
        trade_store=None,
    ) -> None:
        self._client = client or KalshiPublic()
        self._log_path = Path(log_path)
        self._pod_id = pod_id
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        # Set post-construction by main.py — the TradeStore is built after
        # build_shared_deps(). See the note on _open_placed_entries.
        self.trade_store = trade_store

    # ── Public API (called from engine loop) ─────────────────────────

    def settle_cycle(self) -> List[Dict[str, Any]]:
        """Check open P-017 positions against Kalshi results."""
        open_entries = self._open_placed_entries()
        if not open_entries:
            logger.debug("kalshi_golf_settler: no open P-017 positions")
            return []

        settled: List[Dict[str, Any]] = []
        pending = 0

        for market_id, entry in open_entries.items():
            market = self._client.get_market(market_id)
            if market is not None:
                result = str(market.get("result") or "").strip().lower()
                status = str(market.get("status") or "").strip().lower()

                if result in ("yes", "no"):
                    settled.append(
                        self._write_settlement(
                            market_id, entry, result, "kalshi_api"
                        )
                    )
                    continue

                if result == SCALAR_RESULT:
                    # Competitor never teed off — market cancelled, not
                    # resolved.  Refund.
                    logger.info(
                        "kalshi_golf_settler: %s cancelled (result=scalar, "
                        "competitor withdrew) — voiding", market_id,
                    )
                    settled.append(
                        self._write_settlement(
                            market_id, entry, "void", "kalshi_withdrawn"
                        )
                    )
                    continue

                if result:
                    # Unexpected fourth value.  Do NOT guess — leave the
                    # position open and let the stale guard handle it, so
                    # a new Kalshi result vocabulary surfaces as a warning
                    # rather than as silently-zeroed P&L.
                    logger.warning(
                        "kalshi_golf_settler: %s unknown result=%r "
                        "(status=%s) — leaving open",
                        market_id, result, status,
                    )
                elif status in PENDING_STATUSES:
                    # Tournament over, Kalshi has not finalized yet.
                    # This is the normal ~1-day lag, not a problem.
                    pending += 1
                    logger.debug(
                        "kalshi_golf_settler: %s closed, awaiting result",
                        market_id,
                    )

            # stale guard — event close long past, still unresolved
            stale = self._stale_record(market_id, entry)
            if stale is not None:
                settled.append(stale)

        logger.info(
            "kalshi_golf_settler: cycle done — checked=%d settled=%d "
            "awaiting_result=%d",
            len(open_entries), len(settled), pending,
        )
        return settled

    # ── Internals ────────────────────────────────────────────────────

    def _stale_record(
        self, market_id: str, entry: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """VOID a position whose event closed long ago and never resolved."""
        close_str = (entry.get("extra") or {}).get("close_utc", "")
        if not close_str:
            return None
        try:
            close = datetime.fromisoformat(str(close_str).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        days = (self._now() - close).total_seconds() / 86400.0
        if days < self.STALE_DAYS_AFTER_CLOSE:
            return None
        logger.warning(
            "kalshi_golf_settler: auto-void stale %s (%.1f days after close)",
            market_id, days,
        )
        return self._write_settlement(
            market_id, entry, "void", "stale_timeout"
        )

    def _open_placed_entries(self) -> Dict[str, Dict[str, Any]]:
        """Unresolved PLACED entries for this pod.

        Prefer the TradeStore. When one is attached, BasePod.write_log
        delegates to store.append() and NOTHING is ever written to
        data/pods/<pod>.jsonl — the per-pod file stays empty and a
        file-reading settler silently finds zero positions to settle.
        (Verified in production: 27 P-017 placements landed in the shared
        trade_log.jsonl; data/pods/ was empty.) The store is also the only
        source that survives log rotation, which matters here because
        golf positions are opened 4-10 days before close and the active
        log holds ~1.5 days.

        The file path remains as a fallback for standalone/offline use.
        """
        store = self.trade_store
        if store is not None:
            out: Dict[str, Dict[str, Any]] = {}
            for e in store.get_open_trades(self._pod_id):
                mid = e.get("market_id") or e.get("market_ticker", "")
                if mid and float(e.get("position_size_usd") or 0) > 0:
                    out[mid] = e
            return out

        if not self._log_path.exists():
            return {}
        placed: Dict[str, Dict[str, Any]] = {}
        resolved: set = set()
        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("kalshi_golf_settler: log read error: %s", exc)
            return {}
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("pod_id") != self._pod_id:
                continue
            action = (rec.get("action") or "").upper()
            outcome = (rec.get("outcome") or "").upper()
            market_id = rec.get("market_id", "")
            if not market_id:
                continue
            if action == "BANKROLL_RESET":
                placed.clear()
                resolved.clear()
                continue
            if action == "PLACED" and float(rec.get("position_size_usd") or 0) > 0:
                placed[market_id] = rec
            if outcome in ("WIN", "LOSS", "VOID"):
                resolved.add(market_id)
        return {mid: r for mid, r in placed.items() if mid not in resolved}

    def _write_settlement(
        self,
        market_id: str,
        placed_entry: Dict[str, Any],
        result: str,
        resolution_source: str,
    ) -> Dict[str, Any]:
        extra = placed_entry.get("extra") or {}
        side = placed_entry.get("side", "YES")
        size_usd = float(placed_entry.get("position_size_usd") or 0)
        fill_price = float(
            placed_entry.get("fill_price")
            or placed_entry.get("venue_prob")
            or 0.5
        )
        outcome, pnl_gross = _calc_outcome_pnl(
            side, result, size_usd, fill_price
        )

        # Taker fee was charged at placement and is sunk on WIN and LOSS
        # alike.  A VOID refunds the stake; Kalshi does not refund the
        # trade fee, so it stays booked.
        contracts = float(extra.get("contracts") or 0)
        fee_per_ct = float(extra.get("taker_fee") or 0)
        fees_usd = round(contracts * fee_per_ct, 4)
        pnl_net = round(pnl_gross - fees_usd, 2)

        now = self._now()
        record: Dict[str, Any] = {
            **placed_entry,
            "action": outcome,
            "outcome": outcome,
            "result": result,
            "pnl_usd": pnl_net,
            "pnl_gross_usd": pnl_gross,
            "fees_usd": fees_usd,
            "settled_at_utc": now.isoformat(),
            "timestamp_utc": now.isoformat(),
            "resolution_source": resolution_source,
        }
        # Persist to the same place the placement came from. append()
        # both indexes and fsyncs to the shared log, so the settlement
        # survives a restart — writing to data/pods/ would not, since
        # nothing reads it back.
        if self.trade_store is not None:
            self.trade_store.append(record)
        else:
            self._append_log(record)
        logger.info(
            "kalshi_golf_settler: settled %s | result=%s outcome=%s "
            "pnl_net=%.2f (gross=%.2f fees=%.2f)",
            market_id, result, outcome, pnl_net, pnl_gross, fees_usd,
        )
        return record

    def _append_log(self, record: Dict[str, Any]) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error("kalshi_golf_settler: log write failed: %s", exc)

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "KalshiGolfSettler":
        log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
        return cls(
            client=overrides.get("client") or KalshiPublic(),
            log_path=Path(overrides.get("log_path") or log_dir / "P-017.jsonl"),
            _now_fn=overrides.get("_now_fn"),
        )
