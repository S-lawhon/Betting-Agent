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

from src.aggregate_risk import AggregateRiskGuard
from src.golf_schedule import GolfScheduleResolver, RoundClose
from src.kalshi_fees import fee_per_contract
from src.kalshi_public import KalshiPublic, fnum
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

MARKOUT_HORIZONS = (300.0, 900.0, 3600.0)   # seconds after fill

# Settlement is polled per-ticker, and the resolved close is deliberately
# EARLY (see src/golf_schedule.py), so polling starts before the real close and
# would otherwise hammer /markets/{t} once per cycle per book — ~350 books at a
# 20s cycle. Poll no more often than this per book.
SETTLE_POLL_INTERVAL_S = 300.0

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

    @property
    def collateral(self) -> float:
        """Worst-case collateral if this resting sell-YES quote is fully lifted.

        A resting quote is an unconditional obligation: it is consumed the
        moment it rests, not when it fills. §7's caps are on "collateral at
        risk", and a quote that can be lifted at any instant is at risk.
        """
        return (1.0 - self.price) * self.qty


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
    # Which golf_schedule source produced close_epoch (tee_times /
    # r1_tee_anchor / tour_day_offset). Logged on every quote so a fill can be
    # attributed to the timing source that placed it.
    close_source: str = "unknown"
    event_ticker: str = ""
    ask_quote: Optional[MakerQuote] = None
    fills: List[MakerFill] = field(default_factory=list)
    inventory: float = 0.0        # net YES (negative = short from selling)
    last_trade_epoch: float = 0.0
    last_settle_poll: float = 0.0
    done: bool = False

    @property
    def sold(self) -> float:
        """Total YES contracts sold (open, unsettled)."""
        return sum(f.qty for f in self.fills if not f.settled)

    @property
    def collateral(self) -> float:
        """FILLED collateral only. Kept as its own quantity on purpose — a
        fill is realised exposure and a quote is contingent exposure, and the
        settle/release paths key off the filled figure."""
        return sum(f.collateral for f in self.fills if not f.settled)

    @property
    def quoted_collateral(self) -> float:
        """Worst-case collateral held by the resting quote, if any."""
        q = self.ask_quote
        return q.collateral if q is not None else 0.0

    @property
    def exposure(self) -> float:
        """§7's "collateral at risk": filled PLUS resting-quote worst case.

        This is the quantity the caps bind against. It is invariant across a
        fill — a fill converts quoted collateral into filled collateral at the
        same price and quantity — so the cap cannot be breached by the passage
        of a quote into a position, only by placing or re-pricing a quote.
        """
        return self.collateral + self.quoted_collateral


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
        # ── the close-time resolver: MANDATORY, no silent fallback ──
        # Kalshi publishes NO field carrying the real close while a market is
        # open (all six collapse to a ~20-day placeholder), so the placement
        # window can only be computed against an external schedule. There is
        # deliberately no "use the Kalshi field if the resolver is missing"
        # path: that fallback is exactly what held T at 0 for five days, and a
        # wrong round time is worse than no quote — it produces fills outside
        # the validated window, and §7 would exclude those tournaments from T
        # anyway. Constructing the engine without a schedule raises.
        schedule: Optional[GolfScheduleResolver] = None,
        _now_fn=None,
    ):
        if schedule is None:
            raise ValueError(
                "RoundLeaderFadeMakerEngine requires a schedule resolver — "
                "Kalshi does not publish the real close of an open "
                "round-leader market. Pass GolfScheduleResolver() (or a "
                "double exposing .resolve()).")
        self.schedule = schedule
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
        # Tickers this engine currently holds a guard reservation on. Tracked
        # here rather than read off the guard so the sweep never reaches into
        # another pod's reservations or the guard's private state.
        self._reserved_ids: set = set()
        # event_ticker -> Kalshi /events product_metadata (competition name,
        # competition_scope). Immutable for a listed event, so cached forever.
        self._event_meta: Dict[str, Dict[str, Any]] = {}
        # event_ticker -> reason it could not be resolved. Surfaced by the
        # window detector so "fail-closed and silent" is still observable.
        self.unresolved_events: Dict[str, str] = {}
        # (event_code, cap_kind) already recorded, so a breach is written once
        # rather than every cycle. Repopulated from the log on restart.
        self._breaches: set = set()
        # ticker -> the book snapshot behind the most recent _mid() call, so a
        # QUOTE row can record what it was priced off. Observability only.
        self._last_book: Dict[str, Dict[str, Any]] = {}

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

    # `*_collateral` is FILLED only; `*_exposure` is filled + resting quotes.
    # §7's caps bind on EXPOSURE. Both are public because the two questions are
    # genuinely different — "what have I got on?" and "what could I have on
    # before I can cancel anything?" — and collapsing them into one accessor is
    # how the caps came to be enforced against the wrong quantity.

    def tournament_collateral(self, event_code: str) -> float:
        return sum(b.collateral for b in self.books.values()
                   if b.event_code == event_code)

    def total_collateral(self) -> float:
        return sum(b.collateral for b in self.books.values())

    def tournament_exposure(self, event_code: str) -> float:
        return sum(b.exposure for b in self.books.values()
                   if b.event_code == event_code)

    def total_exposure(self) -> float:
        return sum(b.exposure for b in self.books.values())

    # ── §7 cap-breach recording ──────────────────────────────────────
    #
    # P022_DECISION_RULE.md §7: "A tournament run with any cap breached is
    # EXCLUDED from T." That was unimplementable, because a breach was
    # recorded nowhere: `p022_checkpoint.py` had no breach concept at all, and
    # a gate condition only checkable retrospectively is not a gate condition.
    # Breaches are now written to the fills log — the same file the checkpoint
    # already reads — at the moment they are observed.

    def _record_breach(self, event_code: str, kind: str,
                       observed: float, limit: float) -> None:
        key = (event_code, kind)
        if key in self._breaches:
            return                      # once per tournament per cap kind
        self._breaches.add(key)
        logger.error("P-022 CAP BREACH %s %s: %.4f > %.4f — tournament %s is "
                     "EXCLUDED from T (§7)", event_code, kind, observed, limit,
                     event_code)
        self._write(self.fills_log, {
            "type": "CAP_BREACH", "pod_id": "P-022", "event": event_code,
            "kind": kind, "observed": round(observed, 4),
            "limit": round(limit, 4),
        })

    def check_caps(self) -> None:
        """Observe every §7 cap and record any breach.

        Run every cycle, not only at quote time, because the breach path that
        actually exists is a RESTART: books used to be rebuilt from nothing,
        so all three caps re-armed from zero while the paper exposure
        persisted, and a restart mid-tournament could silently double the
        per-name and per-tournament collateral. `rebuild_from_log()` closes
        that hole; this records it if anything else opens one.

        Observes ``exposure`` (filled + resting quotes), which is the quantity
        the caps are sized against. Observing only ``collateral`` was the
        defect: 24 quotes carrying $111.65 against a $50 per-tournament limit
        recorded no breach at all, because none of them had filled yet.
        """
        per_event: Dict[str, float] = {}
        total = 0.0
        for b in self.books.values():
            coll = b.exposure
            if coll <= 0:
                continue
            total += coll
            per_event[b.event_code] = per_event.get(b.event_code, 0.0) + coll
            if coll > self.max_collateral_per_name + 1e-9:
                self._record_breach(b.event_code, "per_name", coll,
                                    self.max_collateral_per_name)
        for ev, coll in per_event.items():
            if coll > self.max_collateral_per_tournament + 1e-9:
                self._record_breach(ev, "per_tournament", coll,
                                    self.max_collateral_per_tournament)
        if total > self.max_total_collateral + 1e-9:
            # An aggregate breach is not attributable to one tournament, so it
            # excludes every tournament with live exposure at the time.
            for ev in per_event:
                self._record_breach(ev, "aggregate", total,
                                    self.max_total_collateral)

    @property
    def breached_events(self) -> set:
        return {ev for ev, _ in self._breaches}

    # ── Restart safety ───────────────────────────────────────────────

    def rebuild_from_log(self) -> int:
        """Restore unsettled fills so the caps do not re-arm from zero.

        §7's three collateral caps are GATE CONDITIONS. `self.books = {}` on
        init and nothing ever read the fills log back, so after any restart —
        deploy, reboot, crash — `book.collateral` was 0 for names the pod was
        still short. The service was restarted twice in the week this was
        found. Under §7 that excludes the tournament from T, and nothing
        detected it.

        The close reference is taken from the last QUOTE row for the ticker
        when one exists, and 0.0 otherwise, which means "already past close —
        poll settlement". That is the safe default: a restored book with no
        schedule anchor must never be treated as quotable. `discover()`
        re-resolves it against the external schedule if the market is still
        open.
        """
        close_ref: Dict[str, float] = {}
        try:
            with open(self.quotes_log, "r", encoding="utf-8") as fh:
                for raw in fh:
                    try:
                        rec = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if rec.get("type") == "QUOTE" and rec.get("close_ref"):
                        close_ref[rec.get("ticker")] = float(rec["close_ref"])
        except OSError:
            pass

        settled: set = set()
        fills: Dict[str, Dict[str, Any]] = {}
        events: Dict[str, str] = {}
        try:
            with open(self.fills_log, "r", encoding="utf-8") as fh:
                for raw in fh:
                    try:
                        rec = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    typ, fid = rec.get("type"), rec.get("fill_id")
                    if typ == "CAP_BREACH" and rec.get("event"):
                        self._breaches.add((rec["event"],
                                            rec.get("kind") or "unknown"))
                        continue
                    if not fid:
                        continue
                    if typ == "SETTLE":
                        settled.add(fid)
                    elif typ == "FILL":
                        fills[fid] = rec            # last write wins
                        if rec.get("ticker"):
                            events[rec["ticker"]] = rec.get("event") or ""
        except OSError:
            return 0

        n = 0
        for fid, rec in fills.items():
            if fid in settled:
                continue
            tk = rec.get("ticker")
            if not tk:
                continue
            book = self.books.get(tk)
            if book is None:
                book = MarketBook(
                    ticker=tk, event_code=events.get(tk, ""),
                    close_epoch=close_ref.get(tk, 0.0),
                    close_source="restored")
                self.books[tk] = book
            if any(f.fill_id == fid for f in book.fills):
                continue
            qty = float(rec.get("qty") or 0)
            book.fills.append(MakerFill(
                fill_id=fid, ticker=tk, price=float(rec.get("price") or 0),
                qty=qty, epoch=float(rec.get("ts") or 0),
                mid_at_fill=rec.get("mid_at_fill")))
            book.inventory -= qty
            n += 1
        if n:
            logger.info("P-022: restored %d unsettled fill(s) across %d book(s) "
                        "— §7 caps re-arm from real exposure, not zero",
                        n, len(self.books))
        self.check_caps()
        return n

    # ── Discovery ────────────────────────────────────────────────────

    @staticmethod
    def _event_code(m: Dict[str, Any]) -> str:
        ev = m.get("event_ticker") or ""
        parts = ev.split("-")
        return parts[1] if len(parts) >= 2 else ev

    @staticmethod
    def _series_of(event_ticker: str) -> str:
        return (event_ticker or "").split("-")[0]

    def _product_metadata(self, event_ticker: str) -> Dict[str, Any]:
        """`/events/{ticker}` product_metadata, cached.

        Carries the only handle Kalshi gives on WHICH tournament and round a
        listed event is:
            {"competition": "Rocket Classic",
             "competition_scope": "Round 1 Leader"}
        """
        hit = self._event_meta.get(event_ticker)
        if hit is not None:
            return hit
        data = self.kalshi.get(f"/events/{event_ticker}")
        ev = (data or {}).get("event", {}) or {}
        pm = ev.get("product_metadata") or {}
        meta = {"competition": pm.get("competition"),
                "competition_scope": pm.get("competition_scope")}
        # Only cache a usable answer; a transient failure must be retried.
        if meta["competition"]:
            self._event_meta[event_ticker] = meta
        return meta

    def resolve_event_close(self, event_ticker: str) -> Optional[RoundClose]:
        """The real round end for a listed event, or None (= do not quote).

        Public because ``scripts/p022_window_check.py`` imports it rather than
        reimplementing it — the detector must track whatever the pod actually
        does, and then ask an INDEPENDENT question about the answer.
        """
        meta = self._product_metadata(event_ticker)
        comp = meta.get("competition")
        if not comp:
            self.unresolved_events[event_ticker] = "no product_metadata.competition"
            return None
        try:
            rc = self.schedule.resolve(
                comp, self._series_of(event_ticker),
                scope=meta.get("competition_scope"))
        except Exception:                          # noqa: BLE001
            logger.exception("P-022: schedule resolve failed for %s", event_ticker)
            self.unresolved_events[event_ticker] = "resolver raised"
            return None
        if rc is None:
            self.unresolved_events[event_ticker] = f"no schedule match for {comp!r}"
            return None
        self.unresolved_events.pop(event_ticker, None)
        return rc

    def discover(self) -> int:
        """List open markets and attach an EXTERNALLY resolved round close.

        Two things this does that the pre-2026-07-28 version did not:

        * The close comes from ``src/golf_schedule``, never from a Kalshi
          field. Every Kalshi time field on an open round-leader market is the
          same ~20-day placeholder, so the old event-level MIN was a MIN over
          identical placeholders and the window opened ~2.5 weeks after the
          round had settled.
        * Existing books are RE-RESOLVED on every pass. ESPN publishes no tee
          times three days out (checked against all three listed tournaments
          on 2026-07-27: zero competitors), so a market discovered at listing
          gets the coarse per-tour day offset and must be upgraded to the
          precise tee-time close as soon as ESPN publishes it. A close
          resolved once at discovery would freeze the coarse answer.
        """
        n_new = 0
        all_markets: List[Dict[str, Any]] = []
        for s in self.series:
            try:
                all_markets.extend(self.kalshi.open_markets(s))
            except Exception as exc:
                logger.error("P-022: discovery failed %s: %s", s, exc)

        event_close: Dict[str, RoundClose] = {}
        for ev in {m.get("event_ticker") or "" for m in all_markets}:
            if not ev:
                continue
            rc = self.resolve_event_close(ev)
            if rc is not None:
                event_close[ev] = rc

        for m in all_markets:
            tk = m.get("ticker", "")
            ev = m.get("event_ticker") or ""
            if not tk:
                continue
            rc = event_close.get(ev)
            book = self.books.get(tk)
            if book is not None:
                if rc is not None and (
                        abs(book.close_epoch - rc.close_epoch) > 1.0
                        or book.close_source != rc.source):
                    logger.info(
                        "P-022: %s close re-resolved %s -> %s (%s -> %s)",
                        ev, datetime.fromtimestamp(book.close_epoch,
                                                   timezone.utc).isoformat(),
                        rc.close_iso, book.close_source, rc.source)
                    book.close_epoch = rc.close_epoch
                    book.close_source = rc.source
                continue
            if rc is None:
                continue
            self.books[tk] = MarketBook(
                ticker=tk, event_code=self._event_code(m),
                close_epoch=rc.close_epoch, close_source=rc.source,
                event_ticker=ev)
            n_new += 1
        if self.unresolved_events:
            logger.warning("P-022: %d event(s) UNRESOLVED, not quoting: %s",
                           len(self.unresolved_events),
                           dict(list(self.unresolved_events.items())[:5]))
        return n_new

    # ── Cycle ────────────────────────────────────────────────────────

    def cycle(self) -> None:
        killed = self.kill_file.exists()
        now = self._now()
        # ── the allocation rule, stated rather than inherited ──
        # A tournament's names all become quotable in the same window and
        # together they can want more collateral than §7's per-tournament cap
        # allows, so the cap decides WHICH names get quoted. That decision must
        # be a rule. Books are walked in ticker order: greedy first-come, where
        # "first" is lexicographic on the Kalshi ticker.
        #
        # Ticker order is arbitrary with respect to edge — it is the golfer's
        # name — which is the point. The alternatives were rejected on purpose:
        # dict order is insertion order and therefore an artifact of the API's
        # response ordering, which is not a rule; and best-edge-first would let
        # the cap select the sample on a quantity correlated with the outcome
        # being measured, which is the fitting §8.2 forbids. Pro-rata across
        # all in-band names would keep more names at a smaller size, but it
        # needs a price-everything-then-size pass the cycle does not have, and
        # it is a larger change than a cap fix should be.
        for book in sorted(self.books.values(), key=lambda b: b.ticker):
            if book.done:
                continue
            try:
                self._cycle_book(book, killed, now)
            except Exception:
                logger.exception("P-022: cycle failed for %s", book.ticker)
        self.check_caps()
        self._sweep_reservations()

    def _mid(self, ticker: str) -> Optional[float]:
        ob = self.kalshi.orderbook(ticker)
        if not ob:
            self._last_book[ticker] = {"book_side": "no_book"}
            return None
        b, a = ob.get("yes_bid"), ob.get("yes_ask")
        # OBSERVABILITY ONLY — the reference price and every branch below are
        # unchanged. The sidedness of each reference is recorded so a fill can
        # be attributed to the book it was quoted off. Measured 2026-07-28 on
        # AIGWO26 R1: 143 of 146 markets in the event carry NO resting YES bid
        # at any price, so the one-sided branch drives essentially every
        # placement. A quote whose provenance was never recorded cannot be
        # adjudicated later, and this is the first tournament of a
        # 24-tournament gate.
        snap = {"yes_bid": b, "yes_ask": a,
                "bid_qty": ob.get("bid_qty"), "ask_qty": ob.get("ask_qty")}
        # Round-leader lottery books are frequently one-sided (bid 0). Accept a
        # one-sided ask as the reference when there is no bid, since that is
        # the price retail lifts; otherwise use the two-sided mid.
        if a is not None and 0 < a < 1 and (b is None or b <= 0):
            self._last_book[ticker] = snap | {"book_side": "one_sided_ask"}
            return a
        if b is None or a is None or not (0 < b <= a < 1):
            self._last_book[ticker] = snap | {"book_side": "unpriceable"}
            return None
        self._last_book[ticker] = snap | {"book_side": "two_sided"}
        return (b + a) / 2.0

    def _cycle_book(self, book: MarketBook, killed: bool, now: float) -> None:
        # Past the resolved close: try to settle, but DO NOT stop working the
        # book if settlement has not happened yet.
        #
        # The resolved close is deliberately conservative — biased early, by a
        # measured median of +1.6h on tee times and +5.6h on the day offset,
        # and by up to +52h when a round is suspended for weather. The old
        # code `return`ed here, which would have stopped processing fills for
        # the whole remainder of the round on a quote that is supposed to REST
        # through it. That is where Phase 2's fills — and its edge — come
        # from, so throwing them away would have quietly changed the strategy
        # being validated.
        if now >= book.close_epoch:
            if now - book.last_settle_poll >= SETTLE_POLL_INTERVAL_S:
                book.last_settle_poll = now
                self._maybe_settle(book)
            if book.done:
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
        #
        # The room is measured against EXPOSURE — filled collateral PLUS every
        # resting quote — not against fills alone. A resting sell-YES quote is
        # an unconditional obligation to take the position if it is lifted, so
        # it consumes the cap the moment it rests. Sizing against fills alone
        # let a whole tournament's names go out in one window and each one see a
        # book that looked empty: 24 quotes, $111.65, against a $50 limit, with
        # no breach recorded because nothing had filled.
        #
        # THIS book's own resting quote is excluded from the event and total
        # figures, because the quote computed below REPLACES it. Counting it
        # would make each re-price shrink the quote against itself and ratchet
        # the book to zero over successive cycles. The per-name room needs no
        # such subtraction: it is measured against filled collateral, which the
        # resting quote is not part of.
        own_quote_coll = book.quoted_collateral
        room_name_coll = (self.max_collateral_per_name
                          - book.collateral)
        room_name_ct = self.max_contracts_per_name - book.sold
        if room_name_coll <= 0 or room_name_ct <= 0:
            self._pull(book, "cap_per_name")
            return
        room_event = (self.max_collateral_per_tournament
                      - (self.tournament_exposure(book.event_code)
                         - own_quote_coll))
        room_total = (self.max_total_collateral
                      - (self.total_exposure() - own_quote_coll))
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
            # take it if lifted), not the premium — and include what this
            # market already owes on filled contracts, since reservations are
            # idempotent-by-market_id and a bare quote figure would replace,
            # and so erase, the exposure of everything already filled here.
            if not self._reserve(book, book.collateral + size * per_ct_coll):
                self._pull(book, "aggregate_risk")
                return
            book.ask_quote = MakerQuote(quote_px, size, now, mid)
            self._write(self.quotes_log, {
                "type": "QUOTE", "pod_id": "P-022", "ticker": book.ticker,
                "event": book.event_code, "ask": quote_px, "mid": mid,
                "size": size, "inventory": book.inventory,
                "hours_to_close": round(hours_to_close, 2),
                # Which schedule source timed this quote, so a fill can be
                # attributed to it later. tour_day_offset is the coarse path
                # (median +5.6h conservative); tee_times is the precise one
                # (median +1.6h).
                "close_source": book.close_source,
                "close_ref": book.close_epoch,
                # The book this quote was priced off. `book_side` is the branch
                # of `_mid` that produced `mid`; the raw levels are kept so the
                # classification can be re-derived rather than trusted, and so
                # the depth question ("how much rests at a better price than
                # ours?") is answerable from the log — the pod screens on
                # neither, and that omission is now visible rather than
                # inferred.
                **{k: v for k, v in self._last_book.get(book.ticker, {}).items()},
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
            ok = bool(reserve("P-022", "kalshi", book.ticker, collateral_usd))
            if ok:
                self._reserved_ids.add(book.ticker)
            return ok
        check = getattr(guard, "check_trade", None)
        if callable(check):
            return bool(check("P-022", "kalshi", collateral_usd))
        return True

    def _release(self, ticker: str) -> None:
        self._reserved_ids.discard(ticker)
        guard = self.risk_guard
        if guard is None:
            return
        release = getattr(guard, "release_reservation", None)
        if callable(release):
            release(ticker)

    def _sweep_reservations(self) -> None:
        """Drop reservations on markets that no longer owe anything.

        The 5-minute engine drains reservations in ``update_post_cycle``;
        a standalone loop has no such callback. Without this sweep a settled
        tournament's collateral would sit in the guard forever and P-022
        would starve itself one event at a time — a wiring that mutes the pod
        is worse than no wiring, and muting is the exact failure this whole
        workstream exists to stop.

        Release-only by construction: it never asks the guard to approve
        anything, so it cannot itself be refused.
        """
        if not self._reserved_ids:
            return
        for ticker in list(self._reserved_ids):
            book = self.books.get(ticker)
            if (book is None or book.done
                    or (book.ask_quote is None and book.collateral <= 0)):
                self._release(ticker)

    def _pull(self, book: MarketBook, reason: str) -> None:
        if book.ask_quote is not None:
            book.ask_quote = None
            # An abandoned quote must not keep holding exposure, or the pod
            # starves itself one name at a time. Filled contracts still owe
            # their collateral, so only a book with nothing outstanding is
            # fully released; the sweep handles the rest.
            if book.collateral <= 0:
                self._release(book.ticker)
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
        # Settled: this market owes nothing further. Release rather than
        # ``close_position`` — see the note on the daily-loss halt in
        # ``from_config``.
        self._release(book.ticker)
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
        # ── §7's precondition, actually wired ──
        # "Before P-022 can quote, it must be wired into AggregateRiskGuard
        # with reservations." The mechanism existed and was unit-tested, but
        # NOTHING EVER CONSTRUCTED A GUARD: the runner calls from_config, and
        # from_config never passed one, so `risk_guard` was None in the live
        # process and _reserve() was an unconditional True. §11c recorded the
        # requirement as satisfied. Built here rather than in the runner so
        # the pod is wired wherever it is constructed — "implemented in one
        # path, absent from the one that runs" is the failure this pod has
        # now produced three times. Pass risk_guard=None explicitly to opt out.
        #
        # Behaviour-neutral by arithmetic, verified against the merged config
        # on the droplet (2026-07-27): P-022's own §7 caps are strictly tighter
        # than every guard exposure limit — aggregate 15% of bankroll vs the
        # guard's 25% per pod, 30% per venue, 50% total — so the §7 caps still
        # bind first and the quoted population is unchanged. The one guard
        # limit that could bind first is max_open_positions (200 live); P-022
        # holds at most ~30 reservations, since the per-name cap is 0.5% of a
        # 15% aggregate.
        #
        # DELIBERATELY NOT WIRED: settled P&L is released with
        # release_reservation, not close_position. close_position calls
        # record_pnl, which halts the guard at a 5% daily loss — and the
        # cooldown that clears a halt is applied in check_pre_cycle, which a
        # standalone loop never calls. A halt would therefore be PERMANENT
        # until restart, and would silently exclude tournaments from T. A
        # daily-loss halt for P-022 is a gate-affecting behaviour that the
        # locked rule does not register; §7 asks for reservations and that is
        # what this provides. Raising it is a separate decision.
        if "risk_guard" in overrides:
            guard = overrides.pop("risk_guard")
        else:
            try:
                guard = AggregateRiskGuard.from_config(config)
            except Exception:                      # noqa: BLE001
                logger.exception(
                    "P-022: could not build AggregateRiskGuard — running with "
                    "the in-process collateral caps only")
                guard = None
        # The external round-close resolver. Mandatory (see the engine's
        # docstring); overridable for tests, never absent in production.
        schedule = overrides.pop("schedule", None) or GolfScheduleResolver()
        return RoundLeaderFadeMakerEngine(
            risk_guard=guard,
            schedule=schedule,
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
