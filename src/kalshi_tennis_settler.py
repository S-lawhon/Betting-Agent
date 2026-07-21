"""
src/kalshi_tennis_settler.py
────────────────────────────
Settles P-015 tennis trades by polling Kalshi's public API for market
results.  Unlike P-001 (which resolves via Odds API /scores because the
demo Kalshi env never settles sports), tennis match markets on the
production API settle normally: `status` becomes settled/finalized and
`result` reports "yes"/"no".

Follows the PolymarketSettler pattern: reads the pod's JSONL log for
unresolved PLACED entries, writes WIN/LOSS/VOID records back to the same
log, and returns them so the engine loop can sync the allocator, risk
guard, and trade store.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.kalshi_tennis_client import KalshiTennisClient

logger = logging.getLogger(__name__)

SETTLED_STATUSES = ("settled", "finalized", "determined")


def _calc_outcome_pnl(
    side: str, result: str, size_usd: float, fill_price: float,
) -> Tuple[str, float]:
    """Outcome label and net P&L for a settled Kalshi binary.

    P-015 only buys YES, but handle NO for completeness.  fill_price is
    the YES price paid.  WIN pnl is profit only (stake excluded), LOSS
    is -stake, VOID is 0 — matching the PolymarketSettler convention.
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


class KalshiTennisSettler:
    """Polls Kalshi public API to settle P-015 tennis positions."""

    # Auto-void if unresolved this long after scheduled match start
    # (matches Kalshi's own "postponed matches stay open two weeks" rule,
    # with margin).
    STALE_HOURS_AFTER_START = 14 * 24

    def __init__(
        self,
        client: Optional[KalshiTennisClient] = None,
        log_path: Path = Path("data/trade_logs/trade_log.jsonl"),
        pod_id: str = "P-015",
        _now_fn: Optional[Callable[[], datetime]] = None,
        trade_store=None,
    ) -> None:
        self._client = client or KalshiTennisClient()
        self._log_path = Path(log_path)
        self._pod_id = pod_id
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        # Set post-construction by main.py — the TradeStore is built after
        # build_shared_deps(). See _open_placed_entries.
        self.trade_store = trade_store

    # ── Public API (called from engine loop) ─────────────────────────

    def settle_cycle(self) -> List[Dict[str, Any]]:
        """Check open P-015 positions against Kalshi results."""
        open_entries = self._open_placed_entries()
        if not open_entries:
            logger.debug("kalshi_tennis_settler: no open P-015 positions")
            return []

        now = self._now()
        settled: List[Dict[str, Any]] = []

        for market_id, entry in open_entries.items():
            market = self._client.get_market(market_id)
            if market is not None:
                status = str(market.get("status") or "").lower()
                result = str(market.get("result") or "").strip().lower()
                if result in ("yes", "no") or status in SETTLED_STATUSES:
                    rec = self._write_settlement(
                        market_id, entry, result or "void", "kalshi_api"
                    )
                    settled.append(rec)
                    continue

            # stale guard: scheduled start long past, still unresolved
            start_str = (entry.get("extra") or {}).get("scheduled_start_utc", "")
            try:
                if start_str:
                    start = datetime.fromisoformat(
                        start_str.replace("Z", "+00:00")
                    )
                    hours = (now - start).total_seconds() / 3600.0
                    if hours >= self.STALE_HOURS_AFTER_START:
                        logger.warning(
                            "kalshi_tennis_settler: auto-void stale %s "
                            "(%.0fh after start)", market_id, hours,
                        )
                        rec = self._write_settlement(
                            market_id, entry, "void", "stale_timeout"
                        )
                        settled.append(rec)
            except (ValueError, TypeError):
                pass

        logger.info(
            "kalshi_tennis_settler: cycle done — checked=%d settled=%d",
            len(open_entries), len(settled),
        )
        return settled

    # ── Internals ────────────────────────────────────────────────────

    def _open_placed_entries(self) -> Dict[str, Dict[str, Any]]:
        """Unresolved PLACED entries for this pod.

        Prefer the TradeStore. This settler previously read
        data/pods/P-015.jsonl, which is NEVER written when a TradeStore
        is attached: BasePod.write_log delegates to store.append() and
        everything lands in the shared trade_log.jsonl instead. A
        file-reading settler therefore finds zero positions and settles
        nothing, silently. P-015 had not yet placed a trade, so the bug
        was latent; P-017 hit it in production (27 placements, empty
        data/pods/) which is how it surfaced. PolymarketSettler avoided
        it by pointing at paths.trade_log all along.

        The file path (now defaulting to the shared log, not the per-pod
        one) remains as a fallback for standalone/offline use.
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
            logger.warning("kalshi_tennis_settler: log read error: %s", exc)
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
        side = placed_entry.get("side", "YES")
        size_usd = float(placed_entry.get("position_size_usd") or 0)
        fill_price = float(
            placed_entry.get("fill_price")
            or placed_entry.get("venue_prob")
            or 0.5
        )
        outcome, pnl_usd = _calc_outcome_pnl(side, result, size_usd, fill_price)

        now = self._now()
        record: Dict[str, Any] = {
            **placed_entry,
            "action": outcome,
            "outcome": outcome,
            "result": result,
            "pnl_usd": pnl_usd,
            "settled_at_utc": now.isoformat(),
            "timestamp_utc": now.isoformat(),
            "resolution_source": resolution_source,
        }
        # Persist where the placement came from. append() indexes and
        # fsyncs to the shared log, so the settlement survives a restart;
        # writing to data/pods/ would not, since nothing reads it back.
        if self.trade_store is not None:
            self.trade_store.append(record)
        else:
            self._append_log(record)
        logger.info(
            "kalshi_tennis_settler: settled %s | result=%s outcome=%s pnl=%.2f",
            market_id, result, outcome, pnl_usd,
        )
        return record

    def _append_log(self, record: Dict[str, Any]) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error("kalshi_tennis_settler: log write failed: %s", exc)

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "KalshiTennisSettler":
        # paths.trade_log (shared), NOT paths.pod_logs — the per-pod file
        # is never written when a TradeStore is attached. Matches the
        # PolymarketSettler convention.
        default_log = Path(
            config.get("paths", {}).get(
                "trade_log", "data/trade_logs/trade_log.jsonl"
            )
        )
        return cls(
            client=overrides.get("client") or KalshiTennisClient.from_config(config),
            log_path=Path(overrides.get("log_path") or default_log),
            _now_fn=overrides.get("_now_fn"),
        )
