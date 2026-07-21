"""
src/golf_fade_maker.py
──────────────────────
P-017M: Golf fade-maker — PAPER market-making on Kalshi top-N golf props
in the late-tournament overpricing window.

Basis (backtest/REPORT_Golf_TopN_2026-07.md): once a tournament is under
way, holders of mid-tier top-N YES contracts don't mark down fast enough
as players fall off the pace ("hope premium"). Resting an offer (selling
YES) in the 36h→6h-before-close window, filling only on prints that go
strictly THROUGH the quote (pessimistic, adverse-selection-inclusive),
returned +9.1c/contract net — zero maker fee (prop series are the
`quadratic` fee type). CRITICAL: the edge is Sat→Sun-AM only; the
48h→24h slice is NEGATIVE (YES still climbing Friday). Hence the 36h
start gate.

⚠️ Backtest rests on only 4 tournaments of tick data. This engine exists
to COLLECT live paper fills + markouts over more events and confirm the
edge before any real money — mirroring P-016's methodology, not a
validated strategy. Fills are simulated pessimistically; this client
cannot place real orders.

Design mirrors src/pods/live_maker_pod.py (LiveMakerEngine): standalone
fast loop outside the 5-minute engine, quote/fill/markout/settle with
JSONL logs; a report script scores markouts on >=500 fills.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.kalshi_fees import fee_per_contract
from src.kalshi_public import KalshiPublic, fnum
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

MARKOUT_HORIZONS = (300.0, 900.0, 3600.0)   # seconds after fill
DEFAULT_SERIES = ("KXPGATOP10", "KXPGATOP20")


@dataclass
class MakerQuote:
    price: float
    qty: float
    active_from: float
    mid_at_quote: float


@dataclass
class MakerFill:
    fill_id: str
    ticker: str
    price: float
    qty: float
    epoch: float
    mid_at_fill: Optional[float]
    markouts_done: set = field(default_factory=set)
    settled: bool = False


@dataclass
class MarketBook:
    ticker: str
    event_code: str
    close_epoch: float
    ask_quote: Optional[MakerQuote] = None
    fills: List[MakerFill] = field(default_factory=list)
    inventory: float = 0.0        # net YES (negative = short from selling)
    last_trade_epoch: float = 0.0
    done: bool = False


class GolfFadeMakerEngine:
    """Paper fade-maker over Kalshi top-N golf markets."""

    def __init__(
        self,
        kalshi: Optional[KalshiPublic] = None,
        series: tuple = DEFAULT_SERIES,
        log_dir: Path = Path("data/trade_logs"),
        mid_band: tuple = (0.08, 0.40),
        quote_offset: float = 0.03,
        quote_size: float = 25.0,
        max_net_contracts: float = 200.0,
        fade_start_h: float = 36.0,
        fade_end_h: float = 6.0,
        kill_file: Path = Path("data/KILL_GOLF_MAKER"),
        _now_fn=None,
    ):
        self.kalshi = kalshi or KalshiPublic()
        self.series = tuple(series)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.quotes_log = self.log_dir / "golf_maker_quotes.jsonl"
        self.fills_log = self.log_dir / "golf_maker_fills.jsonl"
        self.mid_band = mid_band
        self.quote_offset = quote_offset
        self.quote_size = quote_size
        self.max_net_contracts = max_net_contracts
        self.fade_start_h = fade_start_h
        self.fade_end_h = fade_end_h
        self.kill_file = Path(kill_file)
        self._now = _now_fn or time.time
        self.books: Dict[str, MarketBook] = {}
        self._fill_seq = 0

    # ── Logging ──────────────────────────────────────────────────────

    def _write(self, path: Path, rec: Dict[str, Any]) -> None:
        rec.setdefault("ts", self._now())
        rec.setdefault("iso", datetime.now(timezone.utc).isoformat())
        try:
            with open(path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError as exc:
            logger.error("P-017M: log write failed: %s", exc)

    # ── Discovery ────────────────────────────────────────────────────

    @staticmethod
    def _close_epoch(m: Dict[str, Any]) -> Optional[float]:
        # Prefer occurrence_datetime (real event end); Kalshi's close_time /
        # expiration_time are a far-future conservative date (can_close_early).
        # NOTE: occurrence_datetime is a date-granularity marker and can be
        # several hours off the true final-round finish — fine for a paper
        # pilot; refine against the round schedule before real money.
        raw = (m.get("occurrence_datetime") or m.get("close_time")
               or m.get("expected_expiration_time"))
        if not raw:
            return None
        try:
            return datetime.fromisoformat(
                str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _event_code(m: Dict[str, Any]) -> str:
        ev = m.get("event_ticker") or ""
        parts = ev.split("-")
        return parts[1] if len(parts) >= 2 else ev

    def discover(self) -> int:
        n_new = 0
        all_markets: List[Dict[str, Any]] = []
        for s in self.series:
            try:
                all_markets.extend(self.kalshi.open_markets(s))
            except Exception as exc:
                logger.error("P-017M: discovery failed %s: %s", s, exc)
        # event_ticker → earliest occurrence/close (see GolfTopNPod for why)
        event_close: Dict[str, float] = {}
        for m in all_markets:
            ev = m.get("event_ticker") or ""
            ep = self._close_epoch(m)
            if ep is None:
                continue
            if ev not in event_close or ep < event_close[ev]:
                event_close[ev] = ep
        for m in all_markets:
            tk = m.get("ticker", "")
            if not tk or tk in self.books:
                continue
            close = event_close.get(m.get("event_ticker") or "")
            if close is None:
                continue
            self.books[tk] = MarketBook(
                ticker=tk, event_code=self._event_code(m), close_epoch=close)
            n_new += 1
        return n_new

    # ── Cycle ────────────────────────────────────────────────────────

    def cycle(self) -> None:
        killed = self.kill_file.exists()
        now = self._now()
        for book in list(self.books.values()):
            if book.done:
                continue
            try:
                self._cycle_book(book, killed, now)
            except Exception:
                logger.exception("P-017M: cycle failed for %s", book.ticker)

    def _mid(self, ticker: str) -> Optional[float]:
        ob = self.kalshi.orderbook(ticker)
        if not ob:
            return None
        b, a = ob.get("yes_bid"), ob.get("yes_ask")
        if b is None or a is None or not (0 < b <= a < 1):
            return None
        return (b + a) / 2.0

    def _cycle_book(self, book: MarketBook, killed: bool, now: float) -> None:
        t_start = book.close_epoch - self.fade_start_h * 3600.0
        t_end = book.close_epoch - self.fade_end_h * 3600.0

        # settle if past close (poll market result)
        if now >= book.close_epoch:
            self._maybe_settle(book)
            return

        in_window = t_start <= now <= t_end
        mid = self._mid(book.ticker)

        # process fills against active quote
        if book.ask_quote is not None:
            self._check_fills(book, mid)

        # markouts
        self._process_markouts(book, mid)

        if killed or not in_window or mid is None:
            if book.ask_quote is not None:
                book.ask_quote = None
                self._write(self.quotes_log, {
                    "type": "PULL", "pod_id": "P-017M", "ticker": book.ticker,
                    "reason": "kill" if killed else
                              ("out_of_window" if not in_window else "no_mid"),
                })
            return

        lo, hi = self.mid_band
        if not (lo <= mid <= hi):
            return
        short_capped = book.inventory <= -self.max_net_contracts
        if short_capped:
            return

        quote_px = round(mid + self.quote_offset, 2)
        if quote_px <= mid or quote_px >= 0.99:
            return
        prev = book.ask_quote
        if prev is None or abs(prev.price - quote_px) > 1e-9:
            book.ask_quote = MakerQuote(quote_px, self.quote_size, now, mid)
            self._write(self.quotes_log, {
                "type": "QUOTE", "pod_id": "P-017M", "ticker": book.ticker,
                "event": book.event_code, "ask": quote_px, "mid": mid,
                "size": self.quote_size, "inventory": book.inventory,
                "hours_to_close": round((book.close_epoch - now) / 3600.0, 2),
            })

    def _check_fills(self, book: MarketBook, mid: Optional[float]) -> None:
        q = book.ask_quote
        if q is None or q.qty <= 0:
            return
        since = book.last_trade_epoch or (self._now() - 3600.0)
        trades = self.kalshi.trades_since(book.ticker, since)
        if trades:
            book.last_trade_epoch = max(t["epoch"] for t in trades)
        for t in trades:
            if t["epoch"] < q.active_from:
                continue
            # our resting ASK (sell YES) fills only when a print goes
            # strictly THROUGH it (price above our ask)
            if t["yes_price"] <= q.price + 1e-9:
                continue
            qty = min(q.qty, t["count"])
            if qty <= 0:
                continue
            q.qty -= qty
            self._fill_seq += 1
            fill = MakerFill(
                fill_id=f"P017M-{int(t['epoch'])}-{self._fill_seq}",
                ticker=book.ticker, price=q.price, qty=qty,
                epoch=t["epoch"], mid_at_fill=mid,
            )
            book.inventory -= qty            # sold YES → short
            book.fills.append(fill)
            self._write(self.fills_log, {
                "type": "FILL", "fill_id": fill.fill_id, "pod_id": "P-017M",
                "ticker": book.ticker, "event": book.event_code,
                "side": "sell_yes", "price": q.price, "qty": qty,
                "trade_price": t["yes_price"], "trade_count": t["count"],
                "taker_side": t.get("taker_side"), "mid_at_fill": mid,
                "inventory_after": book.inventory,
            })
            logger.info("P-017M FILL sell_yes %s %.0f @ %.2f (inv %.0f)",
                        book.ticker, qty, q.price, book.inventory)
            if q.qty <= 0:
                book.ask_quote = None
                break

    def _process_markouts(self, book: MarketBook, mid: Optional[float]) -> None:
        if mid is None:
            return
        now = self._now()
        for fill in book.fills:
            if fill.settled:
                continue
            for h in MARKOUT_HORIZONS:
                if h in fill.markouts_done or now < fill.epoch + h:
                    continue
                fill.markouts_done.add(h)
                # short YES: profit if mid falls below fill price
                self._write(self.fills_log, {
                    "type": "MARKOUT", "fill_id": fill.fill_id,
                    "pod_id": "P-017M", "ticker": book.ticker,
                    "horizon_s": h, "mid": mid, "fill_price": fill.price,
                    "markout_per_contract": fill.price - mid,
                })

    def _maybe_settle(self, book: MarketBook) -> None:
        data = self.kalshi.get(f"/markets/{book.ticker}")
        result = None
        if data:
            m = data.get("market", {})
            res = (m.get("result") or "").lower()
            if res in ("yes", "no"):
                result = 1.0 if res == "yes" else 0.0
        if result is None:
            return  # not settled yet; try again next cycle
        total = 0.0
        for fill in book.fills:
            if fill.settled:
                continue
            fill.settled = True
            # sold YES at price: pnl = price - result, zero maker fee (prop)
            fee = fee_per_contract(fill.price, maker=True,
                                   series_ticker=book.ticker.split("-")[0])
            pnl = (fill.price - result - fee) * fill.qty
            total += pnl
            self._write(self.fills_log, {
                "type": "SETTLE", "fill_id": fill.fill_id, "pod_id": "P-017M",
                "ticker": book.ticker, "event": book.event_code,
                "result": result, "fill_price": fill.price, "qty": fill.qty,
                "maker_fee_per_contract": fee, "pnl_usd": pnl,
            })
        book.done = True
        book.ask_quote = None
        if book.fills:
            logger.info("P-017M SETTLED %s result=%s fills=%d pnl=$%.2f",
                        book.ticker, result, len(book.fills), total)


@register_pod("P-017M")
class GolfFadeMakerPod:
    """Thin registry wrapper — P-017M runs standalone via
    scripts/run_golf_maker.py, not inside the 5-minute engine."""

    pod_id = "P-017M"
    pod_name = "Golf Fade Maker (Kalshi top-N)"

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "GolfFadeMakerEngine":
        cfg = (config.get("pods", {}) or {}).get("P-017M", {}) or {}
        q = cfg.get("quoting", {}) or {}
        r = cfg.get("risk", {}) or {}
        return GolfFadeMakerEngine(
            series=tuple(q.get("series", DEFAULT_SERIES)),
            mid_band=tuple(q.get("mid_band", (0.08, 0.40))),
            quote_offset=float(q.get("quote_offset", 0.03)),
            quote_size=float(q.get("quote_size", 25)),
            fade_start_h=float(q.get("fade_start_h", 36.0)),
            fade_end_h=float(q.get("fade_end_h", 6.0)),
            max_net_contracts=float(r.get("max_net_contracts", 200)),
            **overrides,
        )
