"""
src/round_leader_fade_maker.py
──────────────────────────────
P-022: Round-Leader Dead-Heat Fade — PAPER market-making on Kalshi golf
round-leader props (KX*R{1,2,3}LEAD).

Thesis (verified, golf_quirks_research/):
  A tie for the round lead SPLITS the payout — YES pays $1/n rounded DOWN
  (PGAROUNDLEADER contract terms), so a name that "leads" realizes only
  E[payout|led]=$0.63 on average (37% haircut). Retail buys cheap leader
  names as lottery tickets anyway. Selling YES (making NO) on the 5-10c
  names is the mirror image of P-017's top-N tie-inflation.

Validation:
  Phase 1 (REPORT_Golf_Quirks_2026-07.md): 5-10c pre-round band, sell YES,
  +4-6c/ct on settled data, tournament-clustered CI excluding zero.
  Phase 2 (REPORT_Golf_Quirks_Phase2_P022_2026-07.md): tick-print maker-fill
  replay. Adverse selection is REAL (E[settle|filled]=3.2c vs posted 1.8c)
  but does NOT flip the sign: net +2.1c/ct (offset 0) to +4.7c (offset +4c)
  posting EARLY (12-24h pre-round), 16/19 tournaments positive, robust to
  leave-one-out. Post late (6h, round underway) collapses to marginal.
  All target series are `quadratic` -> zero maker fee.

⚠️ Capacity is SMALL (~$140 pnl / $3.8k collateral / month at 25-ct caps) and
the tail is real (sell a 6c YES that leads outright -> lose ~94c; losses
concentrate in tournaments where a faded name leads). This engine exists to
COLLECT live paper fills + markouts over more events, WITH mandatory
collateral caps, before any real money — mirroring P-016/P-017M methodology.
Fills are simulated pessimistically (only prints strictly THROUGH the resting
ask); this client cannot place real orders.

TWO things this engine does that P-017M (golf top-N fade) does NOT:
  1. Post EARLY (24h->12h before close) and rest through the round to close.
     P-017M fades late (36h->6h) on multi-day top-N; here the determining
     event is a single round and the tradeable window is BEFORE it.
  2. Settle `result="scalar"` at settlement_value_dollars — for round-leader
     that scalar IS the $1/n dead-heat payout (NOT a withdrawal void as it is
     for top-N / GOLFFINISH). Booking it as void would zero the exact
     outcome the whole thesis is about.

Design mirrors src/golf_fade_maker.py (standalone fast loop outside the
5-minute engine; run via scripts/run_round_leader_fade.py).
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

# All 13 round-leader series (fee_type=quadratic -> zero maker fee).
DEFAULT_SERIES = (
    "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
    "KXLIVR1LEAD", "KXLIVR2LEAD", "KXLIVR3LEAD",
    "KXLPGAR1LEAD", "KXLPGAR2LEAD", "KXLPGAR3LEAD",
    "KXDPWORLDTOURR1LEAD", "KXDPWORLDTOURR2LEAD", "KXDPWORLDTOURR3LEAD",
    "KXCHAMPTOURR1LEAD",
)


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

    @property
    def collateral(self) -> float:
        """Max loss if this sold-YES contract settles YES=$1."""
        return (1.0 - self.price) * self.qty


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

    @property
    def sold(self) -> float:
        """Total YES contracts sold (open, unsettled)."""
        return sum(f.qty for f in self.fills if not f.settled)

    @property
    def collateral(self) -> float:
        return sum(f.collateral for f in self.fills if not f.settled)


class RoundLeaderFadeMakerEngine:
    """Paper fade-maker over Kalshi round-leader markets (P-022)."""

    def __init__(
        self,
        kalshi: Optional[KalshiPublic] = None,
        series: tuple = DEFAULT_SERIES,
        log_dir: Path = Path("data/trade_logs"),
        # [0.03, 0.12] is the anchor band the locked decision rule §1 names.
        # The pod shipped with (0.03, 0.10) — narrower than the rule it is
        # tested against, which would have quoted a different population from
        # the one Phase 2 measured.
        mid_band: tuple = (0.03, 0.12),
        quote_offset: float = 0.02,
        # ── collateral caps: GATE CONDITIONS, not tuning knobs ──
        # P022_DECISION_RULE.md §7 states these as PERCENTAGES OF BANKROLL and
        # requires sizing on collateral at risk, never contract count. The pod
        # shipped fixed dollars plus a 25-contract per-name cap; at a 5c fade
        # that is 25 x $0.95 = $23.75, i.e. 2.4% of a $1,000 bankroll against a
        # 0.5% limit — a ~5x breach of a cap whose breach EXCLUDES the
        # tournament from T.
        bankroll: float = 1000.0,
        pct_per_name: float = 0.005,        # §7: per-name    <= 0.5%
        pct_per_tournament: float = 0.05,   # §7: per-event   <= 5%
        pct_total: float = 0.15,            # §7: aggregate   <= 15%
        # Secondary bound, kept as defence in depth. Collateral binds first.
        max_contracts_per_name: float = 25.0,
        # ── window: post EARLY, rest through the round to close ──
        fade_start_h: float = 24.0,      # earliest to place a quote
        no_new_quote_h: float = 12.0,    # latest to place a NEW quote
        kill_file: Path = Path("data/KILL_ROUND_LEADER_FADE"),
        # P022_DECISION_RULE.md §7: "Before P-022 can quote, it must be wired
        # into AggregateRiskGuard with RESERVATIONS (reserve_trade), not
        # post-cycle registration. P-017 places its whole book in one scan and
        # the guard rejected nothing until reservations were added; P-022 posts
        # a whole tournament's names in one window and has the identical
        # failure mode."
        #
        # LIMITATION, stated plainly rather than papered over: P-022 runs as its
        # OWN PROCESS (betting-round-leader-fade.service), so a guard passed here
        # is a separate instance from the 5-minute engine's. It enforces P-022's
        # limits against P-022's own book; it does NOT see engine positions and
        # the engine does not see these. True cross-process aggregate risk needs
        # shared state that does not exist yet — see the reconciliation note in
        # golf_quirks_research/P-022_Fade_Pod_Spec.md. Optional-by-getattr at
        # every call site, per the CLAUDE.md convention, so a test double
        # implementing only check_trade still works.
        risk_guard: Optional[Any] = None,
        _now_fn=None,
    ):
        self.kalshi = kalshi or KalshiPublic()
        self.series = tuple(series)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.quotes_log = self.log_dir / "round_leader_fade_quotes.jsonl"
        self.fills_log = self.log_dir / "round_leader_fade_fills.jsonl"
        self.mid_band = mid_band
        self.quote_offset = quote_offset
        self.bankroll = float(bankroll)
        self.pct_per_name = float(pct_per_name)
        self.pct_per_tournament = float(pct_per_tournament)
        self.pct_total = float(pct_total)
        self.max_contracts_per_name = max_contracts_per_name
        # Derived, so a bankroll change moves every cap together and the
        # percentages stay the single source of truth.
        self.max_collateral_per_name = self.bankroll * self.pct_per_name
        self.max_collateral_per_tournament = self.bankroll * self.pct_per_tournament
        self.max_total_collateral = self.bankroll * self.pct_total
        self.fade_start_h = fade_start_h
        self.no_new_quote_h = no_new_quote_h
        self.kill_file = Path(kill_file)
        self.risk_guard = risk_guard
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
            logger.error("P-022: log write failed: %s", exc)

    # ── Collateral bookkeeping (caps) ────────────────────────────────

    def tournament_collateral(self, event_code: str) -> float:
        return sum(b.collateral for b in self.books.values()
                   if b.event_code == event_code)

    def total_collateral(self) -> float:
        return sum(b.collateral for b in self.books.values())

    # ── Discovery ────────────────────────────────────────────────────

    @staticmethod
    def _close_epoch(m: Dict[str, Any]) -> Optional[float]:
        # ``close_time`` is the real round end. ``occurrence_datetime`` is a
        # far-future PLACEHOLDER on this family and must never be preferred.
        #
        # The original comment here asserted the opposite, and it made the pod
        # structurally incapable of ever quoting. Measured 2026-07-26 on one
        # settled market per series, all five tours:
        #
        #   KXPGAR1LEAD  +18.2d   KXPGAR2LEAD  +15.0d   KXPGAR3LEAD  +13.9d
        #   KXLIVR1LEAD  +16.5d   KXLPGAR1LEAD +16.0d   KXLPGAR2LEAD +15.3d
        #   KXLPGAR3LEAD +13.2d   KXDPWORLDTOURR1/2/3LEAD +16.2/+15.2/+14.3d
        #
        # occurrence_datetime ran 13-18 days LATER than close_time on 10 of 10.
        # With close_epoch two weeks late, the [12h, 24h] placement window opens
        # long after the round has ended and settled, and _mid() returns None on
        # a settled book — so the engine placed nothing, ever. It ran live from
        # 2026-07-23 to 2026-07-26 and wrote zero quotes and zero fills.
        #
        # This is the same family of trap as the top-N event-timing quirk in
        # CLAUDE.md, but with the fields REVERSED, which is why reasoning by
        # analogy from top-N produced exactly the wrong preference order.
        raw = (m.get("close_time") or m.get("occurrence_datetime")
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
                logger.error("P-022: discovery failed %s: %s", s, exc)
        # event_ticker -> earliest occurrence/close (event-level close, as
        # GolfTopNPod does — per-market timing is unreliable on Kalshi).
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
                logger.exception("P-022: cycle failed for %s", book.ticker)

    def _mid(self, ticker: str) -> Optional[float]:
        ob = self.kalshi.orderbook(ticker)
        if not ob:
            return None
        b, a = ob.get("yes_bid"), ob.get("yes_ask")
        # Round-leader lottery books are frequently one-sided (bid 0). Accept a
        # one-sided ask as the reference when there is no bid, since that is
        # the price retail lifts; otherwise use the two-sided mid.
        if a is not None and 0 < a < 1 and (b is None or b <= 0):
            return a
        if b is None or a is None or not (0 < b <= a < 1):
            return None
        return (b + a) / 2.0

    def _cycle_book(self, book: MarketBook, killed: bool, now: float) -> None:
        # settle if past close (poll market result)
        if now >= book.close_epoch:
            self._maybe_settle(book)
            return

        hours_to_close = (book.close_epoch - now) / 3600.0
        mid = self._mid(book.ticker)

        # process fills against active quote (quote rests through the round)
        if book.ask_quote is not None:
            self._check_fills(book, mid)

        # markouts
        self._process_markouts(book, mid)

        # A new quote may be PLACED only in the early [fade_start_h,
        # no_new_quote_h] window; once placed it RESTS through the round to
        # close (that is where Phase-2 fills — and their net-positive edge —
        # occurred). Pull only on kill.
        if killed:
            self._pull(book, "kill")
            return
        can_place_new = self.no_new_quote_h <= hours_to_close <= self.fade_start_h
        if not can_place_new:
            # outside the placement window: keep any resting quote, place none.
            return
        if mid is None:
            return
        lo, hi = self.mid_band
        if not (lo <= mid <= hi):
            return

        # ── caps: refuse to widen exposure past any limit ──
        # All three are COLLATERAL limits (§7 sizes on collateral at risk, never
        # contract count); max_contracts_per_name is only a secondary bound.
        room_name_coll = (self.max_collateral_per_name
                          - book.collateral)
        room_name_ct = self.max_contracts_per_name - book.sold
        if room_name_coll <= 0 or room_name_ct <= 0:
            self._pull(book, "cap_per_name")
            return
        room_event = (self.max_collateral_per_tournament
                      - self.tournament_collateral(book.event_code))
        room_total = self.max_total_collateral - self.total_collateral()
        if room_event <= 0 or room_total <= 0:
            self._pull(book, "cap_collateral")
            return

        quote_px = round(mid + self.quote_offset, 2)
        if quote_px <= mid or quote_px >= 0.99:
            return
        # size the quote to the tightest remaining cap (worst-case collateral
        # per contract = 1 - quote_px).
        per_ct_coll = max(1.0 - quote_px, 1e-6)
        size = min(room_name_ct,
                   room_name_coll / per_ct_coll,
                   room_event / per_ct_coll,
                   room_total / per_ct_coll)
        size = float(int(size))            # whole contracts
        if size <= 0:
            self._pull(book, "cap_sized_to_zero")
            return

        prev = book.ask_quote
        if (prev is None or abs(prev.price - quote_px) > 1e-9
                or abs(prev.qty - size) > 1e-9):
            # Reserve BEFORE resting the quote. A reservation is the only thing
            # that makes the guard see a whole tournament's names as they go out
            # in one window, rather than checking each against a stale snapshot.
            # Reserve the worst-case collateral (the quote is a commitment to
            # take it if lifted), not the premium.
            if not self._reserve(book, size * per_ct_coll):
                self._pull(book, "aggregate_risk")
                return
            book.ask_quote = MakerQuote(quote_px, size, now, mid)
            self._write(self.quotes_log, {
                "type": "QUOTE", "pod_id": "P-022", "ticker": book.ticker,
                "event": book.event_code, "ask": quote_px, "mid": mid,
                "size": size, "inventory": book.inventory,
                "hours_to_close": round(hours_to_close, 2),
            })

    def _reserve(self, book: MarketBook, collateral_usd: float) -> bool:
        """Hold this quote's worst-case collateral with the risk guard.

        Optional-by-getattr: a guard exposing only ``check_trade`` still works,
        and no guard at all is a no-op so the in-process collateral caps remain
        the binding constraint.
        """
        guard = self.risk_guard
        if guard is None or collateral_usd <= 0:
            return True
        reserve = getattr(guard, "reserve_trade", None)
        if callable(reserve):
            return bool(reserve("P-022", "kalshi", book.ticker, collateral_usd))
        check = getattr(guard, "check_trade", None)
        if callable(check):
            return bool(check("P-022", "kalshi", collateral_usd))
        return True

    def _release(self, book: MarketBook) -> None:
        guard = self.risk_guard
        if guard is None:
            return
        release = getattr(guard, "release_reservation", None)
        if callable(release):
            release(book.ticker)

    def _pull(self, book: MarketBook, reason: str) -> None:
        if book.ask_quote is not None:
            book.ask_quote = None
            # An abandoned quote must not keep holding exposure, or the pod
            # starves itself one name at a time.
            self._release(book)
            self._write(self.quotes_log, {
                "type": "PULL", "pod_id": "P-022", "ticker": book.ticker,
                "reason": reason,
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
            # our resting ASK (sell YES) fills only when a BUYER lifts it and
            # the print goes strictly THROUGH our price (pessimistic,
            # adverse-selection-inclusive; matches the Phase-2 replay).
            if t.get("taker_side") != "yes":
                continue
            if t["yes_price"] <= q.price + 1e-9:
                continue
            # never fill past the per-name cap
            room_name = self.max_contracts_per_name - book.sold
            qty = min(q.qty, t["count"], room_name)
            if qty <= 0:
                self._pull(book, "cap_per_name")
                break
            q.qty -= qty
            self._fill_seq += 1
            fill = MakerFill(
                fill_id=f"P022-{int(t['epoch'])}-{self._fill_seq}",
                ticker=book.ticker, price=q.price, qty=qty,
                epoch=t["epoch"], mid_at_fill=mid,
            )
            book.inventory -= qty            # sold YES -> short
            book.fills.append(fill)
            self._write(self.fills_log, {
                "type": "FILL", "fill_id": fill.fill_id, "pod_id": "P-022",
                "ticker": book.ticker, "event": book.event_code,
                "side": "sell_yes", "price": q.price, "qty": qty,
                "trade_price": t["yes_price"], "trade_count": t["count"],
                "taker_side": t.get("taker_side"), "mid_at_fill": mid,
                "inventory_after": book.inventory,
                "collateral_after": round(fill.collateral, 4),
            })
            logger.info("P-022 FILL sell_yes %s %.0f @ %.2f (inv %.0f)",
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
                # short YES: favourable if mid falls below fill price
                self._write(self.fills_log, {
                    "type": "MARKOUT", "fill_id": fill.fill_id,
                    "pod_id": "P-022", "ticker": book.ticker,
                    "horizon_s": h, "mid": mid, "fill_price": fill.price,
                    "markout_per_contract": fill.price - mid,
                })

    def _maybe_settle(self, book: MarketBook) -> None:
        """Poll the per-ticker GET and settle. Round-leader settlement has a
        THIRD value beyond yes/no: `result="scalar"` is the $1/n dead-heat
        payout, carried in settlement_value_dollars (verified: the per-ticker
        GET nulls `settlement_value` but populates `settlement_value_dollars`).
        Booking scalar as a void — as the top-N settler correctly does — would
        zero the very outcome this pod trades."""
        data = self.kalshi.get(f"/markets/{book.ticker}")
        if not data:
            return
        m = data.get("market", {})
        res = (m.get("result") or "").lower()
        if res == "yes":
            payout = 1.0
        elif res == "no":
            payout = 0.0
        elif res == "scalar":
            sv = fnum(m.get("settlement_value_dollars"))
            if sv is None:
                logger.warning("P-022: %s scalar with no settlement_value_"
                               "dollars — awaiting", book.ticker)
                return
            payout = sv
        else:
            return  # not settled yet (or unknown) — retry next cycle

        total = 0.0
        for fill in book.fills:
            if fill.settled:
                continue
            fill.settled = True
            # sold YES at price: pnl = price - payout, zero maker fee (quadratic)
            fee = fee_per_contract(fill.price, maker=True,
                                   series_ticker=book.ticker.split("-")[0])
            pnl = (fill.price - payout - fee) * fill.qty
            total += pnl
            self._write(self.fills_log, {
                "type": "SETTLE", "fill_id": fill.fill_id, "pod_id": "P-022",
                "ticker": book.ticker, "event": book.event_code,
                "result": res, "payout": payout, "fill_price": fill.price,
                "qty": fill.qty, "maker_fee_per_contract": fee, "pnl_usd": pnl,
            })
        book.done = True
        book.ask_quote = None
        if book.fills:
            logger.info("P-022 SETTLED %s result=%s payout=%.2f fills=%d "
                        "pnl=$%.2f", book.ticker, res, payout,
                        len(book.fills), total)


@register_pod("P-022")
class RoundLeaderFadeMakerPod:
    """Thin registry wrapper — P-022 runs standalone via
    scripts/run_round_leader_fade.py, NOT inside the 5-minute engine."""

    pod_id = "P-022"
    pod_name = "Round-Leader Dead-Heat Fade Maker (Kalshi golf)"

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "RoundLeaderFadeMakerEngine":
        cfg = (config.get("pods", {}) or {}).get("P-022", {}) or {}
        q = cfg.get("quoting", {}) or {}
        r = cfg.get("risk", {}) or {}
        # Bankroll comes from the shared config, so P-022's caps track the same
        # number every other pod sizes against rather than a private copy.
        # The live key is `risk.initial_bankroll` (config_multi_pod.yaml:34).
        # Reading only `bankroll` would silently fall back to the 1000.0 default
        # — correct today purely by coincidence, and wrong the moment the paper
        # bankroll is changed. That is the same "right by accident" failure the
        # fixed-dollar caps had, so the key list is explicit and ordered.
        _risk = config.get("risk", {}) or {}
        _cap = config.get("capital", {}) or {}
        bankroll = float(
            r.get("bankroll")
            or r.get("initial_bankroll")
            or _risk.get("bankroll")
            or _risk.get("initial_bankroll")
            or _cap.get("bankroll")
            or 1000.0
        )
        return RoundLeaderFadeMakerEngine(
            series=tuple(q.get("series", DEFAULT_SERIES)),
            mid_band=tuple(q.get("mid_band", (0.03, 0.12))),
            quote_offset=float(q.get("quote_offset", 0.02)),
            fade_start_h=float(q.get("fade_start_h", 24.0)),
            no_new_quote_h=float(q.get("no_new_quote_h", 12.0)),
            bankroll=bankroll,
            # Percentages are the configurable surface. Raising any of them is a
            # NEW hypothesis under §8.1 and resets T to 0 under a new pod ID —
            # they are deliberately not expressed as dollars here.
            pct_per_name=float(r.get("pct_per_name", 0.005)),
            pct_per_tournament=float(r.get("pct_per_tournament", 0.05)),
            pct_total=float(r.get("pct_total", 0.15)),
            max_contracts_per_name=float(r.get("max_contracts_per_name", 25)),
            **overrides,
        )
