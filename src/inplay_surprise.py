"""
src/inplay_surprise.py
──────────────────────
P-018 core logic — the "surprise gate" and its supporting pure functions.

Thesis (Deep Research - Durable EV Opportunities 2026-07): in-play
prediction markets UNDERREACT to expected events and OVERREACT to
surprising ones.  Fading the crowd right after a *surprising* in-play
event earned +2.79% at 2 min post-event (Choi & Hui, Betfair in-play
soccer), decaying ~40%/min to insignificance by ~6 min; Kalshi in-play
markets move only ~0.64-for-1 with the true win-prob change (NBA study,
arXiv 2606.07811).

This module is deliberately I/O-FREE and model-agnostic:

  * It takes model fair value / market mid / sensitivity as NUMBERS and
    returns a regime decision plus a one-sided fade quote or a two-sided
    quote spec.  The MLB/NBA specifics live in
    ``src/inplay_sport_adapter.py``; the replay harness and (later) the
    live engine both import THIS.
  * Every branch is a pure function, so the whole gate is unit-testable
    without network, StatsAPI, or Kalshi — which is the point: the gate
    is the pod's reason to exist over P-016, and gate #1 (spec §8) is a
    measurable claim about these functions, not the plumbing around them.

Sign conventions (match P-016 exactly):
  * ``home_wp`` / ``fair`` / ``mid`` are YES-perspective probabilities in
    dollars, [0, 1].
  * ``wp_jump``  = fair_now  - fair_before_event   (signed model move).
  * ``mkt_jump`` = mid_now    - mid_before_event    (signed market move).
  * ``underreaction`` = wp_jump - mkt_jump.  >0 means the market has NOT
    caught up to the model *in the model's direction* — the fade signal:
    if the model jumped up more than the market did, the market has
    "overshot down" relative to fair and we want to REST A BID to buy the
    panic; symmetric on the downside.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.kalshi_fees import fee_per_contract


# ══════════════════════════════════════════════════════════════════════
# Parameters
# ══════════════════════════════════════════════════════════════════════

@dataclass
class SurpriseParams:
    """Tunable knobs for the gate (spec §7 ``quoting`` block).

    Defaults mirror the spec's PAPER-TUNE starting values; the backtest
    overwrites them into ``inplay_research/p018_params.json`` before any
    live paper.  Do NOT treat these as validated — they are priors.
    """
    surprise_hi: float = 0.06        # |wp_jump| >= this  => fade regime
    surprise_lo: float = 0.02        # |wp_jump| <  this  => expected/widen
    min_underreaction: float = 0.015  # require market lag before fading
    fade_window_s: float = 240.0     # seconds post-event to fade (~40%/min decay)
    fade_size: float = 20.0          # contracts, tapered by secs_since_event
    fade_min_size: float = 1.0       # floor before we stop quoting the fade
    expected_widen: float = 0.03     # extra half-width in the expected regime
    base_half_width: float = 0.025   # P-016 default for the normal regime
    max_half_width: float = 0.06
    sens_mult: float = 0.15          # half-width sensitivity loading (P-016)
    flb_skew_coef: float = 0.04      # favorite-longshot lean (P-016)
    inv_skew_coef: float = 0.5       # inventory mean-reversion lean (P-016)
    max_quote_px: float = 0.95
    min_quote_px: float = 0.05
    fade_edge_frac: float = 0.5      # rest the fade this fraction of the way
    #                                  from mid toward fair (0=at mid, 1=at fair)

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "SurpriseParams":
        q = ((cfg or {}).get("quoting", {})) or {}
        base = cls()
        return cls(
            surprise_hi=float(q.get("surprise_hi", base.surprise_hi)),
            surprise_lo=float(q.get("surprise_lo", base.surprise_lo)),
            min_underreaction=float(
                q.get("min_underreaction", base.min_underreaction)),
            fade_window_s=float(q.get("fade_window_s", base.fade_window_s)),
            fade_size=float(q.get("fade_size", base.fade_size)),
            fade_min_size=float(q.get("fade_min_size", base.fade_min_size)),
            expected_widen=float(q.get("expected_widen", base.expected_widen)),
            base_half_width=float(q.get("base_half_width", base.base_half_width)),
            max_half_width=float(q.get("max_half_width", base.max_half_width)),
            sens_mult=float(q.get("sens_mult", base.sens_mult)),
            flb_skew_coef=float(q.get("flb_skew_coef", base.flb_skew_coef)),
            inv_skew_coef=float(q.get("inv_skew_coef", base.inv_skew_coef)),
            max_quote_px=float(q.get("max_quote_px", base.max_quote_px)),
            min_quote_px=float(q.get("min_quote_px", base.min_quote_px)),
            fade_edge_frac=float(q.get("fade_edge_frac", base.fade_edge_frac)),
        )


# ══════════════════════════════════════════════════════════════════════
# Regimes
# ══════════════════════════════════════════════════════════════════════

class Regime(str, enum.Enum):
    FADE = "fade"          # one-sided offer into a surprise overshoot
    EXPECTED = "expected"  # low-surprise: informed-continuation risk → widen
    NORMAL = "normal"      # between events: symmetric P-016-style quoting
    PULL = "pull"          # quote nothing (kill / event risk in flight)


# ══════════════════════════════════════════════════════════════════════
# Event tracking (per book)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class LastEvent:
    surprise: float
    wp_jump: float
    mkt_jump: float
    underreaction: float
    ts: float


@dataclass
class SurpriseState:
    """Per-book memory the gate needs across cycles.

    Distinct from P-016's ``GameBook`` so the same state object can back
    the replay harness and the live engine.  Baselines reset at each
    detected event; ``event_ts`` is when we OBSERVED the state change (not
    the true event time — the same adverse-selection clock P-016 uses).
    """
    last_state_key: str = ""
    fair_before_event: Optional[float] = None
    mid_before_event: Optional[float] = None
    event_ts: Optional[float] = None
    last_event: Optional[LastEvent] = None

    def seed(self, state_key: str, fair: float, mid: Optional[float]) -> None:
        """Initialise baselines on first sight of a book (no event yet)."""
        self.last_state_key = state_key
        self.fair_before_event = fair
        self.mid_before_event = mid


def update_on_event(
    state: SurpriseState,
    state_key: str,
    fair_now: float,
    mid_now: Optional[float],
    now: float,
) -> Optional[LastEvent]:
    """Detect a discrete in-play event (an OBSERVED ``state_key`` change)
    and, if one occurred, record the signed model/market jumps and reset
    the baselines.  Returns the ``LastEvent`` on an event, else ``None``.

    Mirrors P-016's state-change detection (``state_key != last_state_key``)
    but additionally captures the pre-event fair/mid so surprise and
    underreaction are computable.  First sight (empty ``last_state_key``)
    seeds baselines and is NOT an event.
    """
    if not state.last_state_key:
        state.seed(state_key, fair_now, mid_now)
        return None
    if state_key == state.last_state_key:
        # No event; keep a rolling pre-event baseline only if unset.
        if state.fair_before_event is None:
            state.fair_before_event = fair_now
        if state.mid_before_event is None and mid_now is not None:
            state.mid_before_event = mid_now
        state.last_state_key = state_key
        return None

    fair_before = (state.fair_before_event
                   if state.fair_before_event is not None else fair_now)
    mid_before = state.mid_before_event
    wp_jump = fair_now - fair_before
    # If we never saw a pre-event mid, treat the market as having moved
    # WITH the model (mkt_jump == wp_jump) → underreaction 0 → no fade.
    # Refusing to fade on missing data is the conservative choice.
    mkt_jump = ((mid_now - mid_before)
                if (mid_now is not None and mid_before is not None)
                else wp_jump)
    ev = LastEvent(
        surprise=abs(wp_jump),
        wp_jump=wp_jump,
        mkt_jump=mkt_jump,
        underreaction=wp_jump - mkt_jump,
        ts=now,
    )
    state.last_state_key = state_key
    state.last_event = ev
    state.event_ts = now
    state.fair_before_event = fair_now   # reset baseline post-event
    state.mid_before_event = mid_now
    return ev


# ══════════════════════════════════════════════════════════════════════
# Regime selection
# ══════════════════════════════════════════════════════════════════════

def select_regime(
    state: SurpriseState,
    now: float,
    params: SurpriseParams,
    killed: bool = False,
    event_risk: bool = False,
) -> Regime:
    """Choose the quoting regime for this cycle (spec §3).

    Precedence: kill / unresolved event risk always PULL.  Then, if we are
    inside the fade window after a high-surprise event with the market
    still lagging, FADE.  A low-surprise most-recent event means informed
    continuation risk → EXPECTED (widen).  Otherwise NORMAL.
    """
    if killed or event_risk:
        return Regime.PULL

    ev = state.last_event
    if ev is not None and state.event_ts is not None:
        secs = now - state.event_ts
        in_window = (ev.surprise >= params.surprise_hi
                     and 0.0 <= secs <= params.fade_window_s)
        if in_window and abs(ev.underreaction) >= params.min_underreaction:
            return Regime.FADE
        # An event so small it barely moved value is exactly the regime
        # where the informed side runs a maker over: quote wide, not tight.
        if secs <= params.fade_window_s and ev.surprise < params.surprise_lo:
            return Regime.EXPECTED
    return Regime.NORMAL


# ══════════════════════════════════════════════════════════════════════
# Quote construction
# ══════════════════════════════════════════════════════════════════════

@dataclass
class QuoteSpec:
    """A resting quote the engine/harness will post.

    ``side`` is YES-perspective: "bid" = buy YES, "ask" = sell YES.  A
    FADE emits ONE side; NORMAL/EXPECTED emit up to two (``bid`` and
    ``ask`` are both populated).  Prices in dollars.
    """
    regime: Regime
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    fair: float = 0.0
    center: float = 0.0
    half_width: float = 0.0
    meta: Dict[str, float] = field(default_factory=dict)


def _clamp_px(px: float, params: SurpriseParams) -> float:
    return round(max(min(px, params.max_quote_px), params.min_quote_px), 2)


def fade_size(base_size: float, secs_since_event: float,
              fade_window_s: float, floor: float = 1.0) -> float:
    """Taper the fade size linearly to zero across the fade window.

    The documented edge decays ~40%/min, so a fade rested at t=300s must
    be smaller than one at t=60s (spec §3 "size should taper").  Linear
    taper is intentionally cruder than the exponential the literature
    implies — the backtest tunes the window; the taper only needs to be
    monotone-decreasing.  Outside the window returns 0.0.
    """
    if fade_window_s <= 0 or secs_since_event < 0:
        return 0.0
    if secs_since_event >= fade_window_s:
        return 0.0
    frac = 1.0 - (secs_since_event / fade_window_s)
    sized = base_size * frac
    return sized if sized >= floor else 0.0


def _half_width(sensitivity: float, params: SurpriseParams) -> float:
    return min(params.base_half_width + params.sens_mult * sensitivity,
               params.max_half_width)


def _center(fair: float, mid: Optional[float], inventory: float,
            half: float, max_net: float, params: SurpriseParams) -> float:
    """Market-anchored center with a bounded model tilt + FLB + inventory
    skew — identical construction to P-016 ``_compute_quotes`` so the
    NORMAL regime reproduces P-016 behaviour between events."""
    if mid is not None:
        tilt = max(-params.max_half_width,
                   min(params.max_half_width, fair - mid))
        center = mid + tilt
    else:
        center = fair
    center += params.flb_skew_coef * (center - 0.5)
    if max_net > 0:
        center -= params.inv_skew_coef * half * (inventory / max_net)
    return center


def build_quote(
    regime: Regime,
    fair: float,
    mid: Optional[float],
    sensitivity: float,
    params: SurpriseParams,
    *,
    inventory: float = 0.0,
    max_net_contracts: float = 100.0,
    secs_since_event: float = 0.0,
    underreaction: float = 0.0,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
) -> QuoteSpec:
    """Turn a regime decision into a concrete quote spec (spec §3).

    FADE      → one-sided offer on the side the crowd is stampeding away
                from, rested between mid and fair, size tapered by time.
    EXPECTED  → widened two-sided (extra half-width; informed-flow guard).
    NORMAL    → P-016 two-sided.
    PULL      → empty spec.
    """
    if regime is Regime.PULL:
        return QuoteSpec(regime=regime, fair=fair)

    half = _half_width(sensitivity, params)
    if regime is Regime.EXPECTED:
        half = min(half + params.expected_widen, params.max_half_width
                   + params.expected_widen)

    center = _center(fair, mid, inventory, half, max_net_contracts, params)

    if regime is Regime.FADE:
        return _fade_spec(fair, mid, center, sensitivity, underreaction,
                          secs_since_event, params, best_bid, best_ask)

    # NORMAL / EXPECTED: symmetric two-sided around the center.
    bid = center - half
    ask = center + half
    if best_ask is not None:
        bid = min(bid, best_ask - 0.01)
    if best_bid is not None:
        ask = max(ask, best_bid + 0.01)
    bid = _clamp_px(bid, params)
    ask = _clamp_px(ask, params)
    spec = QuoteSpec(regime=regime, fair=fair, center=center, half_width=half)
    # Never quote a side already through our own valuation (P-016 guard).
    if ask > bid:
        if bid < center:
            spec.bid, spec.bid_size = bid, params.fade_size
        if ask > center:
            spec.ask, spec.ask_size = ask, params.fade_size
    return spec


def _fade_spec(
    fair: float,
    mid: Optional[float],
    center: float,
    sensitivity: float,
    underreaction: float,
    secs_since_event: float,
    params: SurpriseParams,
    best_bid: Optional[float],
    best_ask: Optional[float],
) -> QuoteSpec:
    """Rest ONE offer into the overshoot.

    underreaction > 0  ⇒ model above market ⇒ market overshot DOWN ⇒ rest a
                          BID below fair to buy the panic.
    underreaction < 0  ⇒ market overshot UP ⇒ rest an ASK above fair.
    The quote sits ``fade_edge_frac`` of the way from mid toward fair, so we
    still demand a spread cushion rather than paying up to fair.
    """
    size = fade_size(params.fade_size, secs_since_event,
                     params.fade_window_s, params.fade_min_size)
    spec = QuoteSpec(regime=Regime.FADE, fair=fair, center=center,
                     meta={"underreaction": underreaction,
                           "secs_since_event": secs_since_event})
    if size <= 0:
        return spec  # window expired / tapered below floor → nothing to rest

    anchor = mid if mid is not None else fair
    if underreaction > 0:
        # Buy the down-overshoot: bid between mid and fair (fair >= mid here).
        px = anchor + params.fade_edge_frac * (fair - anchor)
        px = min(px, fair)                      # never pay above fair
        if best_ask is not None:
            px = min(px, best_ask - 0.01)       # stay a maker
        px = _clamp_px(px, params)
        if px < fair:                           # only if still +EV vs fair
            spec.bid, spec.bid_size = px, size
    elif underreaction < 0:
        # Sell the up-overshoot: ask between mid and fair (fair <= mid here).
        px = anchor + params.fade_edge_frac * (fair - anchor)
        px = max(px, fair)                      # never sell below fair
        if best_bid is not None:
            px = max(px, best_bid + 0.01)
        px = _clamp_px(px, params)
        if px > fair:
            spec.ask, spec.ask_size = px, size
    return spec


# ══════════════════════════════════════════════════════════════════════
# Pessimistic fill simulation
# ══════════════════════════════════════════════════════════════════════

def fills_through(side: str, quote_price: float, quote_qty: float,
                  print_price: float, print_count: float) -> float:
    """Pessimistic paper-fill rule, identical to P-016.

    A resting quote fills ONLY when a public trade prints strictly THROUGH
    it — below our bid / above our ask.  A print exactly AT our price is
    assumed to fill others ahead of us and does NOT fill.  Fill size is
    capped by both the print and our remaining quote size.

    ``side`` is "bid" (we buy) or "ask" (we sell), YES-perspective.
    Returns the filled contract count (0.0 if no fill).
    """
    if quote_qty <= 0 or print_count <= 0:
        return 0.0
    if side == "bid":
        through = print_price < quote_price - 1e-9
    elif side == "ask":
        through = print_price > quote_price + 1e-9
    else:
        return 0.0
    if not through:
        return 0.0
    return min(quote_qty, print_count)


# ══════════════════════════════════════════════════════════════════════
# Settlement P&L (shared by harness + engine)
# ══════════════════════════════════════════════════════════════════════

def settle_pnl(side: str, price: float, result: float, qty: float,
               series_ticker: str = "") -> float:
    """Net paper P&L for a settled fill, booked NET of the maker fee.

    ``side`` "bid"/"buy" = long YES, "ask"/"sell" = short YES.  ``result``
    is 1.0 if YES settled true (home won) else 0.0.  Fee is series-aware
    (spec §2: KXMLBGAME charges maker; MLB props/totals do not) — pass the
    series so the gate compares against the correct fee-net baseline, the
    same discipline as P-016 / the golf settler.
    """
    sign = 1.0 if side in ("bid", "buy") else -1.0
    fee = fee_per_contract(price, maker=True, series_ticker=series_ticker)
    return (sign * (result - price) - fee) * qty
