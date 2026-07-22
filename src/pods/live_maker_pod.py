"""
src/pods/live_maker_pod.py
──────────────────────────
P-016: Live Maker pod — paper market-making on Kalshi MLB in-game
moneyline markets.

Basis (live_agent_research/REPORT_Live_InPlay_Agent_2026-07-19.md):
in-play taker strategies are refuted (spread absorbs the drift — Kalshi
NBA study, arXiv 2606.07811), while documented positive returns sit on
the maker side.  P-016 measures whether a maker quoting around a live
win-probability model captures spread net of adverse selection.

Paper-fill realism rules (pessimistic by design):
  • A resting quote fills ONLY when a public trade prints strictly
    THROUGH it (below our bid / above our ask) while the quote was
    active — trades AT our price are assumed to fill others first.
  • Quotes are active from the cycle we post them until the cycle we
    pull them; state changes discovered on a poll deactivate quotes at
    OBSERVATION time, not event time — fills in between stand, which is
    exactly the adverse-selection cost the pilot measures.
  • Fill size capped by both the print size and our quote size.

The pilot's verdict comes from markouts (mid at +1/+5/+15 min and
settlement) on ≥500 fills — see scripts/maker_report.py.

P-016 quotes ONLY the home-team YES market of each game, innings 1-8
(the win-prob model is weakest in walk-off states; late-game clutch is
also where pick-off risk peaks).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.kalshi_fees import fee_per_contract
from src.kalshi_public import KalshiPublic, fnum
from src.order_flow import OrderFlowTracker
from src.mlb_statsapi import (MlbGame, MlbLiveState, MlbStatsApi,
                              kalshi_team_code)
from src.mlb_win_prob import (MlbGameSnapshot, home_win_probability,
                              implied_pregame_prob)
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

MARKOUT_HORIZONS = (60.0, 300.0, 900.0)   # seconds after fill


@dataclass
class Quote:
    side: str                 # "bid" or "ask" (YES perspective)
    price: float              # dollars
    qty: float                # contracts remaining
    active_from: float        # epoch
    state_key: str            # game state the quote was priced on
    fair: float
    sensitivity: float = 0.0  # |dWP/drun| when quoted (leverage)
    mid_at_quote: Optional[float] = None
    model_fair: Optional[float] = None   # raw model output (diagnostics)


@dataclass
class Fill:
    fill_id: str
    ticker: str
    game_pk: int
    side: str                 # "buy" (bid hit) or "sell" (ask lifted)
    price: float
    qty: float
    epoch: float
    fair_at_fill: float
    mid_at_fill: Optional[float]
    state_key: str
    markouts_done: set = field(default_factory=set)
    next_event_done: bool = False   # event-clocked markout recorded?
    settled: bool = False
    # ── Telemetry for the Tier-1 diagnostic decomposition ────────────
    shadow: bool = False          # counterfactual (we were NOT quoting)
    reason: str = ""              # why shadow: inning_gate / inventory_cap
    sens_at_quote: float = 0.0
    quote_dist_from_mid: Optional[float] = None
    secs_since_state_change: Optional[float] = None
    inning: int = 0
    is_top: bool = True


@dataclass
class GameBook:
    game: MlbGame
    ticker: str               # home-YES market
    pregame_prob: Optional[float] = None
    anchor_source: str = ""
    quotes: Dict[str, Quote] = field(default_factory=dict)
    # Counterfactual quotes for states where we deliberately do NOT quote
    # (inning gate, inventory cap).  Never affect inventory or P&L — they
    # measure what the guardrails cost.
    shadow_quotes: Dict[str, Quote] = field(default_factory=dict)
    shadow_reason: str = ""
    inventory: float = 0.0    # net YES contracts (+long)
    fills: List[Fill] = field(default_factory=list)
    flow: OrderFlowTracker = field(default_factory=OrderFlowTracker)
    last_trade_epoch: float = 0.0
    last_state_key: str = ""
    state_changed_at: float = 0.0   # when we OBSERVED the last state change
    done: bool = False


class LiveMakerEngine:
    """Standalone quoting/fill/markout engine (no BasePod dependency —
    P-016 runs its own fast loop outside the 5-minute engine)."""

    def __init__(
        self,
        statsapi: Optional[MlbStatsApi] = None,
        kalshi: Optional[KalshiPublic] = None,
        log_dir: Path = Path("data/trade_logs"),
        # quoting parameters
        base_half_width: float = 0.025,
        sens_mult: float = 0.15,
        max_half_width: float = 0.06,
        flb_skew_coef: float = 0.04,
        inv_skew_coef: float = 0.5,
        quote_size: float = 20.0,
        max_net_contracts: float = 100.0,
        quote_max_inning: int = 8,
        min_quote_px: float = 0.05,
        max_quote_px: float = 0.95,
        max_tilt: float = 0.03,
        max_divergence: float = 0.15,
        kill_file: Path = Path("data/KILL_MAKER"),
        anchor_file: Path = Path("data/trade_logs/maker_anchors.json"),
        _now_fn=None,
    ):
        self.statsapi = statsapi or MlbStatsApi()
        self.kalshi = kalshi or KalshiPublic()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.quotes_log = self.log_dir / "maker_quotes.jsonl"
        self.fills_log = self.log_dir / "maker_fills.jsonl"
        self.base_half_width = base_half_width
        self.sens_mult = sens_mult
        self.max_half_width = max_half_width
        self.flb_skew_coef = flb_skew_coef
        self.inv_skew_coef = inv_skew_coef
        self.quote_size = quote_size
        self.max_net_contracts = max_net_contracts
        self.quote_max_inning = quote_max_inning
        self.min_quote_px = min_quote_px
        self.max_quote_px = max_quote_px
        self.max_tilt = max_tilt
        self.max_divergence = max_divergence
        self.kill_file = Path(kill_file)
        self.anchor_file = Path(anchor_file)
        self._now = _now_fn or time.time
        self.books: Dict[int, GameBook] = {}
        self._fill_seq = 0
        self._anchor_cache: Dict[str, dict] = self._load_anchors()

    # ── Anchor persistence ───────────────────────────────────────────
    # A pregame anchor is a TEAM-STRENGTH prior.  It must survive service
    # restarts: re-deriving it from a live mid mid-game double-counts the
    # current score (the live price already reflects it, and the model
    # then applies it again).  That bug caused 8-17pp divergence on
    # 2026-07-20 and is why this cache exists.

    def _load_anchors(self) -> Dict[str, dict]:
        try:
            if self.anchor_file.exists():
                return json.loads(self.anchor_file.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save_anchors(self) -> None:
        try:
            self.anchor_file.parent.mkdir(parents=True, exist_ok=True)
            self.anchor_file.write_text(json.dumps(self._anchor_cache))
        except OSError as exc:
            logger.warning("P-016: anchor save failed: %s", exc)

    # ── Logging ──────────────────────────────────────────────────────

    def _write(self, path: Path, rec: Dict[str, Any]) -> None:
        rec.setdefault("ts", self._now())
        rec.setdefault("iso", datetime.now(timezone.utc).isoformat())
        try:
            with open(path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError as exc:
            logger.error("P-016: log write failed: %s", exc)

    # ── Market discovery ─────────────────────────────────────────────

    @staticmethod
    def _et_hhmm(start_utc: str) -> Optional[str]:
        """Game start as HHMM in ET (Kalshi ticker encoding, UTC-4)."""
        try:
            dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        except ValueError:
            return None
        et = dt.astimezone(timezone(timedelta(hours=-4)))
        return et.strftime("%H%M")

    @staticmethod
    def _et_datecode(start_utc: str) -> Optional[str]:
        """Kalshi ticker date fragment, e.g. '26JUL20' (ET date)."""
        try:
            dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        except ValueError:
            return None
        et = dt.astimezone(timezone(timedelta(hours=-4)))
        return et.strftime("%y%b%d").upper()

    def discover(self, games: List[MlbGame]) -> int:
        """Match schedule games to Kalshi home-YES markets; create books."""
        markets = self.kalshi.open_markets("KXMLBGAME")
        if not markets:
            return 0
        n_new = 0
        for g in games:
            if g.game_pk in self.books:
                continue
            datecode = self._et_datecode(g.start_utc)
            hhmm = self._et_hhmm(g.start_utc)
            if not datecode or not hhmm:
                continue
            home_code = kalshi_team_code(g.home_abbr)
            away_code = kalshi_team_code(g.away_abbr)
            event_frag = f"{datecode}{hhmm}{away_code}{home_code}"
            loose_frag = f"{away_code}{home_code}"
            match = None
            for m in markets:
                tk = m.get("ticker", "")
                if event_frag in tk and tk.endswith(f"-{home_code}"):
                    match = m
                    break
            if match is None:
                # Fallback: same ET date + team pair (start time drifted)
                for m in markets:
                    tk = m.get("ticker", "")
                    if (datecode in tk and loose_frag in tk
                            and tk.endswith(f"-{home_code}")):
                        match = m
                        break
            if match is None:
                logger.info("P-016: no Kalshi market for %s @ %s (%s)",
                            g.away_abbr, g.home_abbr, event_frag)
                continue
            book = GameBook(game=g, ticker=match["ticker"])
            yb = fnum(match.get("yes_bid_dollars"))
            ya = fnum(match.get("yes_ask_dollars"))
            mid = ((yb + ya) / 2.0
                   if yb is not None and ya is not None and 0 < yb <= ya < 1
                   else None)

            cached = self._anchor_cache.get(book.ticker)
            if cached and cached.get("pregame_prob"):
                # Survives restarts — the only fully clean anchor mid-game
                book.pregame_prob = float(cached["pregame_prob"])
                book.anchor_source = cached.get("source", "cached")
            elif g.status == "Preview" and mid is not None:
                book.pregame_prob = mid
                book.anchor_source = "kalshi_pregame_mid"
                self._anchor_cache[book.ticker] = {
                    "pregame_prob": mid, "source": "kalshi_pregame_mid",
                    "captured_utc": datetime.now(timezone.utc).isoformat(),
                }
                self._save_anchors()
            elif mid is not None:
                # Game already in progress and no stored anchor: invert the
                # model to recover the prior implied by the live mid rather
                # than passing the live mid in as a "pregame" prior.
                book.pregame_prob = None       # resolved on first cycle
                book.anchor_source = "pending_implied"
            self.books[g.game_pk] = book
            n_new += 1
            self._write(self.quotes_log, {
                "type": "ANCHOR", "game_pk": g.game_pk,
                "ticker": book.ticker,
                "event": f"{g.away_abbr} @ {g.home_abbr}",
                "pregame_prob": book.pregame_prob,
                "anchor_source": book.anchor_source,
            })
        return n_new

    # ── Quoting ──────────────────────────────────────────────────────

    def _compute_quotes(self, book: GameBook, fair: float,
                        sensitivity: float, best_bid: Optional[float],
                        best_ask: Optional[float],
                        state_key: str,
                        mid: Optional[float] = None,
                        ignore_caps: bool = False) -> Dict[str, Quote]:
        half = min(self.base_half_width + self.sens_mult * sensitivity,
                   self.max_half_width)

        # ── Market-anchored center with a BOUNDED model tilt ──────────
        # A maker must quote AROUND the market, not around a pure model
        # view: when fair sits far from mid, both quotes land on one side
        # of the book and we stop making markets and start accumulating
        # directional risk at the touch (observed 2026-07-20: ~9pp model
        # divergence produced 484 one-way fills in 45 minutes).
        # The research also says the market is already well calibrated
        # (Kalshi in-play Brier ≈ a good model's), so the model's job is a
        # few cents of tilt, not a wholesale re-price.
        if mid is not None:
            tilt = max(-self.max_tilt, min(self.max_tilt, fair - mid))
            center = mid + tilt
        else:
            center = fair
        # Favorite-longshot-bias skew: shade toward owning the favorite.
        center += self.flb_skew_coef * (center - 0.5)
        # Inventory skew: lean quotes to mean-revert inventory.
        if self.max_net_contracts > 0:
            center -= self.inv_skew_coef * half * \
                (book.inventory / self.max_net_contracts)

        bid = center - half
        ask = center + half
        # Never cross the live book (we are a maker).
        if best_ask is not None:
            bid = min(bid, best_ask - 0.01)
        if best_bid is not None:
            ask = max(ask, best_bid + 0.01)
        bid = round(max(min(bid, self.max_quote_px), self.min_quote_px), 2)
        ask = round(max(min(ask, self.max_quote_px), self.min_quote_px), 2)

        quotes: Dict[str, Quote] = {}
        now = self._now()
        long_capped = (not ignore_caps) and \
            book.inventory >= self.max_net_contracts
        short_capped = (not ignore_caps) and \
            book.inventory <= -self.max_net_contracts

        # Never quote a side that is already -EV against our EFFECTIVE
        # valuation (the market-anchored center), not the raw model fair.
        # Having chosen to trust the market over the model, `center` is
        # our valuation; testing against raw model fair would suppress one
        # whole side whenever model and market disagree — reintroducing
        # the one-sided directional quoting this design exists to avoid.
        # The real bug this guards: price clamps pushing a side through
        # our valuation (fair 0.955 with the ask clamped to 0.95).
        if ask <= center:
            short_capped = True
        if bid >= center:
            long_capped = True

        if ask > bid:
            if not long_capped:
                quotes["bid"] = Quote("bid", bid, self.quote_size, now,
                                      state_key, center, sensitivity, mid,
                                      fair)
            if not short_capped:
                quotes["ask"] = Quote("ask", ask, self.quote_size, now,
                                      state_key, center, sensitivity, mid,
                                      fair)
        return quotes

    # ── Fills ────────────────────────────────────────────────────────

    def _fetch_trades(self, book: GameBook) -> List[Dict[str, Any]]:
        """Pull public prints once per cycle (shared by real + shadow)."""
        since = book.last_trade_epoch or (self._now() - 120.0)
        trades = self.kalshi.trades_since(book.ticker, since)
        if trades:
            book.last_trade_epoch = max(t["epoch"] for t in trades)
        # Order-flow toxicity is updated HERE — the single fetch site —
        # so each print feeds VPIN exactly once even though _check_fills
        # runs twice per cycle (real + shadow) on the same batch.
        book.flow.update(trades)
        return trades

    def _check_fills(self, book: GameBook, mid: Optional[float],
                     fair: float, state_key: str,
                     trades: Optional[List[Dict[str, Any]]] = None,
                     quotes: Optional[Dict[str, Quote]] = None,
                     shadow: bool = False, reason: str = "",
                     state: Optional[MlbLiveState] = None) -> None:
        if trades is None:
            trades = self._fetch_trades(book)
        if quotes is None:
            quotes = book.quotes
        for t in trades:
            for side_name in ("bid", "ask"):
                q = quotes.get(side_name)
                if q is None or q.qty <= 0:
                    continue
                if t["epoch"] < q.active_from:
                    continue
                through = (t["yes_price"] < q.price - 1e-9
                           if side_name == "bid"
                           else t["yes_price"] > q.price + 1e-9)
                if not through:
                    continue
                qty = min(q.qty, t["count"])
                q.qty -= qty
                self._fill_seq += 1
                dist = (abs(q.price - q.mid_at_quote)
                        if q.mid_at_quote is not None else None)
                since_state = (t["epoch"] - book.state_changed_at
                               if book.state_changed_at else None)
                fill = Fill(
                    fill_id=f"P016-{'S' if shadow else 'R'}"
                            f"{int(t['epoch'])}-{self._fill_seq}",
                    ticker=book.ticker,
                    game_pk=book.game.game_pk,
                    side="buy" if side_name == "bid" else "sell",
                    price=q.price,
                    qty=qty,
                    epoch=t["epoch"],
                    fair_at_fill=q.fair,
                    mid_at_fill=mid,
                    state_key=q.state_key,
                    shadow=shadow,
                    reason=reason,
                    sens_at_quote=q.sensitivity,
                    quote_dist_from_mid=dist,
                    secs_since_state_change=since_state,
                    inning=state.inning if state else 0,
                    is_top=state.is_top if state else True,
                )
                if not shadow:
                    book.inventory += qty if fill.side == "buy" else -qty
                book.fills.append(fill)
                self._write(self.fills_log, {
                    "type": "FILL", "fill_id": fill.fill_id,
                    "pod_id": "P-016",
                    "ticker": book.ticker, "game_pk": book.game.game_pk,
                    "event": f"{book.game.away_abbr} @ {book.game.home_abbr}",
                    "side": fill.side, "price": fill.price, "qty": qty,
                    "shadow": shadow, "reason": reason,
                    "trade_price": t["yes_price"],
                    "trade_count": t["count"],
                    "taker_side": t.get("taker_side"),
                    "fill_epoch": t["epoch"],
                    "fair_at_fill": fill.fair_at_fill,
                    "model_fair_at_quote": q.model_fair,
                    "mid_at_fill": mid,
                    "current_fair": fair,
                    "state_key_at_quote": fill.state_key,
                    "state_key_now": state_key,
                    "sens_at_quote": fill.sens_at_quote,
                    "quote_dist_from_mid": dist,
                    "secs_since_state_change": since_state,
                    "inning": fill.inning, "is_top": fill.is_top,
                    "inventory_after": book.inventory,
                    "vpin_at_fill": book.flow.vpin,
                    "one_sidedness_at_fill": book.flow.one_sidedness,
                })
                logger.info(
                    "P-016 %sFILL %s %s %.0f @ %.2f (fair %.3f, inv %.0f) %s",
                    "SHADOW " if shadow else "", fill.side, book.ticker, qty,
                    fill.price, fill.fair_at_fill, book.inventory,
                    fill.fill_id,
                )

    # ── Markouts & settlement ────────────────────────────────────────

    def _process_markouts(self, book: GameBook, mid: Optional[float],
                          fair: float) -> None:
        now = self._now()
        for fill in book.fills:
            if fill.settled:
                continue
            for h in MARKOUT_HORIZONS:
                if h in fill.markouts_done or now < fill.epoch + h:
                    continue
                fill.markouts_done.add(h)
                ref = mid if mid is not None else fair
                if ref is None:
                    continue
                sign = 1.0 if fill.side == "buy" else -1.0
                self._write(self.fills_log, {
                    "type": "MARKOUT", "fill_id": fill.fill_id,
                    "pod_id": "P-016", "ticker": book.ticker,
                    "horizon_type": "wallclock",
                    "horizon_s": h, "mid": mid, "fair": fair,
                    "fill_price": fill.price, "side": fill.side,
                    "qty": fill.qty, "shadow": fill.shadow,
                    "markout_per_contract": sign * (ref - fill.price),
                })

    def _process_next_event_markouts(self, book: GameBook,
                                     mid: Optional[float]) -> None:
        """Event-clocked markout: on the first observed state_key change
        at/after a fill, record signed (mid_now − fill_price).

        Wall-clock 60/300/900s markouts miss WHICH game state picked us
        off; this is the at-bat/possession-resolution markout.  A fill
        that looks fine at +5m but is deeply negative here clustered right
        before the next resolving event.  Called from the state-change
        branch of the cycle, so ``mid`` is this cycle's mid at the NEW
        state.  One record per fill (``next_event_done`` guards)."""
        if mid is None:
            return
        now = self._now()
        for fill in book.fills:
            if fill.settled or fill.next_event_done or now < fill.epoch:
                continue
            fill.next_event_done = True
            sign = 1.0 if fill.side == "buy" else -1.0
            self._write(self.fills_log, {
                "type": "MARKOUT", "fill_id": fill.fill_id,
                "pod_id": "P-016", "ticker": book.ticker,
                "horizon_type": "next_event", "horizon_s": None,
                "secs_elapsed": now - fill.epoch,
                "mid": mid, "fill_price": fill.price, "side": fill.side,
                "qty": fill.qty, "shadow": fill.shadow,
                "markout_per_contract": sign * (mid - fill.price),
            })

    def _settle(self, book: GameBook, home_won: bool) -> None:
        result = 1.0 if home_won else 0.0
        total = 0.0
        for fill in book.fills:
            if fill.settled:
                continue
            fill.settled = True
            sign = 1.0 if fill.side == "buy" else -1.0
            fee = fee_per_contract(fill.price, maker=True)
            pnl = (sign * (result - fill.price) - fee) * fill.qty
            self._write(self.fills_log, {
                "type": "SETTLE", "fill_id": fill.fill_id,
                "pod_id": "P-016", "ticker": book.ticker,
                "game_pk": book.game.game_pk,
                "result": result, "side": fill.side,
                "fill_price": fill.price, "qty": fill.qty,
                "shadow": fill.shadow, "reason": fill.reason,
                "fair_at_fill": fill.fair_at_fill,
                "maker_fee_per_contract": fee,
                "pnl_usd": pnl,
            })
            if not fill.shadow:
                total += pnl
        book.done = True
        book.quotes.clear()
        book.shadow_quotes.clear()
        n_real = sum(1 for f in book.fills if not f.shadow)
        logger.info("P-016 SETTLED %s result=%s fills=%d (real) pnl=$%.2f",
                    book.ticker, result, n_real, total)

    # ── Main cycle ───────────────────────────────────────────────────

    def cycle(self) -> None:
        """One pass over all books: state → fills → markouts → re-quote."""
        killed = self.kill_file.exists()
        for book in self.books.values():
            if book.done:
                continue
            try:
                self._cycle_book(book, killed)
            except Exception:
                logger.exception("P-016: cycle failed for %s", book.ticker)

    def _cycle_book(self, book: GameBook, killed: bool) -> None:
        g = book.game
        ls = self.statsapi.linescore(g.game_pk)
        # Refresh status via lightweight schedule call only when the
        # linescore looks terminal-ish; runner refreshes statuses too.
        state: Optional[MlbLiveState] = ls
        if state is None:
            return

        snap = MlbGameSnapshot(
            home_score=state.home_score, away_score=state.away_score,
            inning=state.inning, is_top=state.is_top, outs=state.outs,
            on_first=state.on_first, on_second=state.on_second,
            on_third=state.on_third, is_final=state.is_final,
        )
        state_key = state.state_key()

        if state.is_final:
            ob = self.kalshi.orderbook(book.ticker)
            mid = _mid(ob)
            trades = self._fetch_trades(book)
            self._check_fills(book, mid, 1.0, state_key, trades=trades,
                              state=state)
            if book.shadow_quotes:
                self._check_fills(book, mid, 1.0, state_key, trades=trades,
                                  quotes=book.shadow_quotes, shadow=True,
                                  reason=book.shadow_reason, state=state)
            self._settle(book, home_won=state.home_score > state.away_score)
            return

        # Anchor resolution for games first seen in progress: invert the
        # model so the recovered prior REPRODUCES the live mid given the
        # current state, instead of double-counting the score.
        if book.pregame_prob is None:
            ob0 = self.kalshi.orderbook(book.ticker)
            m0 = _mid(ob0)
            if m0 is not None:
                book.pregame_prob = implied_pregame_prob(snap, m0)
                book.anchor_source = "implied_from_live_mid"
            else:
                book.pregame_prob = 0.54
                book.anchor_source = "default_0.54"
            self._anchor_cache[book.ticker] = {
                "pregame_prob": book.pregame_prob,
                "source": book.anchor_source,
                "captured_utc": datetime.now(timezone.utc).isoformat(),
            }
            self._save_anchors()
            self._write(self.quotes_log, {
                "type": "ANCHOR", "game_pk": g.game_pk,
                "ticker": book.ticker,
                "event": f"{g.away_abbr} @ {g.home_abbr}",
                "pregame_prob": book.pregame_prob,
                "anchor_source": book.anchor_source,
                "live_mid_at_resolve": m0,
            })

        fv = home_win_probability(snap, book.pregame_prob)
        ob = self.kalshi.orderbook(book.ticker)
        mid = _mid(ob)
        best_bid = ob.get("yes_bid") if ob else None
        best_ask = ob.get("yes_ask") if ob else None

        # 1) Process prints once, against BOTH real and shadow quotes
        trades = self._fetch_trades(book)
        self._check_fills(book, mid, fv.home_wp, state_key, trades=trades,
                          state=state)
        if book.shadow_quotes:
            self._check_fills(book, mid, fv.home_wp, state_key,
                              trades=trades, quotes=book.shadow_quotes,
                              shadow=True, reason=book.shadow_reason,
                              state=state)

        # 2) Markouts due (real + shadow, tagged)
        self._process_markouts(book, mid, fv.home_wp)

        # 3) Note observed state changes (drives the pick-off diagnostic).
        #    An observed change is also the event clock for event-clocked
        #    markouts — record them BEFORE bumping state_changed_at, using
        #    this cycle's mid at the new state.
        if state_key != book.last_state_key:
            self._process_next_event_markouts(book, mid)
            book.last_state_key = state_key
            book.state_changed_at = self._now()

        # 4) Quote management.  Kill switch stops everything, including
        #    shadow.  The inning gate stops REAL quotes but keeps shadow
        #    ones so we can price what the guardrail costs.
        if killed:
            if book.quotes or book.shadow_quotes:
                book.quotes.clear()
                book.shadow_quotes.clear()
                self._write(self.quotes_log, {
                    "type": "PULL", "pod_id": "P-016",
                    "ticker": book.ticker, "reason": "kill_file",
                    "state_key": state_key,
                })
            return

        # Divergence guard: a large model-vs-market gap means the model is
        # far more likely broken in this state than the market is wrong.
        # Pull quotes rather than express a huge directional view.
        if mid is not None and abs(fv.home_wp - mid) > self.max_divergence:
            if book.quotes or book.shadow_quotes:
                book.quotes.clear()
                book.shadow_quotes.clear()
            self._write(self.quotes_log, {
                "type": "PULL", "pod_id": "P-016", "ticker": book.ticker,
                "reason": "divergence_guard", "state_key": state_key,
                "fair": round(fv.home_wp, 4), "mid": mid,
                "anchor_source": book.anchor_source,
            })
            return

        past_inning_gate = state.inning > self.quote_max_inning
        real_quotes: Dict[str, Quote] = {}
        shadow_quotes: Dict[str, Quote] = {}
        shadow_reason = ""

        uncapped = self._compute_quotes(
            book, fv.home_wp, fv.sensitivity, best_bid, best_ask,
            state_key, mid, ignore_caps=True,
        )
        if past_inning_gate:
            shadow_quotes = uncapped
            shadow_reason = "inning_gate"
        else:
            real_quotes = self._compute_quotes(
                book, fv.home_wp, fv.sensitivity, best_bid, best_ask,
                state_key, mid,
            )
            # Sides suppressed by the inventory cap become shadow quotes
            missing = {k: v for k, v in uncapped.items()
                       if k not in real_quotes}
            if missing:
                shadow_quotes = missing
                shadow_reason = "inventory_cap"

        def _sig(qs):
            return {k: (q.price, round(q.qty, 2)) for k, q in qs.items()}

        changed = (
            _sig(book.quotes) != _sig(real_quotes)
            or _sig(book.shadow_quotes) != _sig(shadow_quotes)
            or any(q.state_key != state_key for q in book.quotes.values())
            or any(q.state_key != state_key
                   for q in book.shadow_quotes.values())
        )
        if changed:
            book.quotes = real_quotes
            book.shadow_quotes = shadow_quotes
            book.shadow_reason = shadow_reason
            self._write(self.quotes_log, {
                "type": "QUOTE", "pod_id": "P-016", "ticker": book.ticker,
                "game_pk": g.game_pk, "state_key": state_key,
                "fair": round(fv.home_wp, 4),
                "sensitivity": round(fv.sensitivity, 4),
                "bid": real_quotes["bid"].price if "bid" in real_quotes else None,
                "ask": real_quotes["ask"].price if "ask" in real_quotes else None,
                "shadow_bid": shadow_quotes["bid"].price if "bid" in shadow_quotes else None,
                "shadow_ask": shadow_quotes["ask"].price if "ask" in shadow_quotes else None,
                "shadow_reason": shadow_reason,
                "size": self.quote_size,
                "mkt_bid": best_bid, "mkt_ask": best_ask, "mid": mid,
                "inning": state.inning, "is_top": state.is_top,
                "inventory": book.inventory,
                "vpin": book.flow.vpin,
                "one_sidedness": book.flow.one_sidedness,
            })

    def mark_final_from_schedule(self, games: List[MlbGame]) -> None:
        """Settle books whose schedule status turned Final (belt-and-
        braces alongside linescore is_final, which needs the hint)."""
        by_pk = {g.game_pk: g for g in games}
        for pk, book in self.books.items():
            if book.done:
                continue
            g = by_pk.get(pk)
            if g and g.status == "Final":
                ls = self.statsapi.linescore(pk, status_hint="Final")
                if ls:
                    self._check_fills(book, None, 1.0, ls.state_key())
                    self._settle(book, home_won=ls.home_score > ls.away_score)


def _mid(ob: Optional[dict]) -> Optional[float]:
    if not ob:
        return None
    b, a = ob.get("yes_bid"), ob.get("yes_ask")
    if b is None or a is None or not (0 < b <= 1 and 0 < a <= 1):
        return None
    return (b + a) / 2.0


@register_pod("P-016")
class LiveMakerPod:
    """Thin registry wrapper — P-016 runs standalone via
    scripts/run_live_maker.py, not inside the 5-minute engine.  Present
    so pod tooling (registry/dashboard) knows the id."""

    pod_id = "P-016"
    pod_name = "Live Maker (Kalshi MLB)"

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "LiveMakerEngine":
        cfg = (config.get("pods", {}) or {}).get("P-016", {}) or {}
        q = cfg.get("quoting", {}) or {}
        r = cfg.get("risk", {}) or {}
        return LiveMakerEngine(
            base_half_width=q.get("base_half_width", 0.025),
            sens_mult=q.get("sens_mult", 0.15),
            max_half_width=q.get("max_half_width", 0.06),
            flb_skew_coef=q.get("flb_skew_coef", 0.04),
            inv_skew_coef=q.get("inv_skew_coef", 0.5),
            quote_size=q.get("quote_size", 20),
            quote_max_inning=q.get("quote_max_inning", 8),
            min_quote_px=q.get("min_quote_px", 0.05),
            max_quote_px=q.get("max_quote_px", 0.95),
            max_net_contracts=r.get("max_net_contracts", 100),
            **overrides,
        )
