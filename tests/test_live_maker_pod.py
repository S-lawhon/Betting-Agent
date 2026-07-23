"""Tests for P-016 Live Maker: quoting, pessimistic paper fills,
markouts, settlement, and the win-prob model it quotes from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest

from src.mlb_statsapi import MlbGame, MlbLiveState
from src.mlb_win_prob import MlbGameSnapshot, home_win_probability
from src.pods.live_maker_pod import LiveMakerEngine, GameBook, Quote


# ── Win-prob model sanity ────────────────────────────────────────────

def test_wp_first_pitch_matches_pregame_anchor():
    for pre in (0.40, 0.50, 0.60, 0.65):
        r = home_win_probability(MlbGameSnapshot(0, 0, 1, True, 0), pre)
        assert abs(r.home_wp - pre) < 0.02, (pre, r.home_wp)


def test_wp_monotonic_in_score():
    wps = [
        home_win_probability(
            MlbGameSnapshot(s, 2, 6, True, 1), 0.5).home_wp
        for s in range(0, 7)
    ]
    assert all(b > a for a, b in zip(wps, wps[1:]))


def test_wp_big_late_lead_near_certain():
    r = home_win_probability(MlbGameSnapshot(8, 1, 8, False, 0), 0.5)
    assert r.home_wp > 0.985


def test_wp_final_state():
    r = home_win_probability(
        MlbGameSnapshot(3, 5, 9, False, 2, is_final=True), 0.5)
    assert r.home_wp == 0.0


def test_wp_sensitivity_rises_late_in_close_game():
    early = home_win_probability(MlbGameSnapshot(2, 2, 2, True, 0), 0.5)
    late = home_win_probability(MlbGameSnapshot(2, 2, 8, True, 0), 0.5)
    assert late.sensitivity > early.sensitivity


# ── Engine fixtures ──────────────────────────────────────────────────

class FakeStats:
    def __init__(self, state):
        self.state = state

    def linescore(self, game_pk, status_hint=""):
        return self.state

    def schedule(self, date_str):
        return []


class FakeKalshi:
    def __init__(self, book=None, trades=None):
        self.book = book or {"yes_bid": 0.48, "yes_ask": 0.52,
                             "bid_qty": 100, "ask_qty": 100}
        self.trades = trades or []

    def orderbook(self, ticker, depth=5):
        return self.book

    def trades_since(self, ticker, min_epoch, max_pages=5):
        return [t for t in self.trades if t["epoch"] > min_epoch]

    def open_markets(self, series):
        return []


def make_engine(tmp_path, state, kalshi=None, now=None, **kw):
    clock = {"t": now or 1000.0}
    eng = LiveMakerEngine(
        statsapi=FakeStats(state),
        kalshi=kalshi or FakeKalshi(),
        log_dir=tmp_path,
        kill_file=tmp_path / "KILL",
        anchor_file=tmp_path / "anchors.json",   # keep tests off real data/
        _now_fn=lambda: clock["t"],
        **kw,
    )
    return eng, clock


def make_book(eng, pregame=0.5):
    g = MlbGame(1, "Miami Marlins", "Houston Astros", "MIA", "HOU",
                "2026-07-20T22:40:00Z", "Live")
    book = GameBook(game=g, ticker="KXMLBGAME-TEST-HOU",
                    pregame_prob=pregame, anchor_source="test")
    eng.books[1] = book
    return book


LIVE_STATE = MlbLiveState(
    game_pk=1, home_score=2, away_score=2, inning=5, is_top=True,
    outs=1, on_first=False, on_second=False, on_third=False,
    is_final=False,
)


# ── Quoting behavior ─────────────────────────────────────────────────

def test_quotes_straddle_fair_and_dont_cross_book(tmp_path):
    eng, _ = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    assert "bid" in book.quotes and "ask" in book.quotes
    bid, ask = book.quotes["bid"].price, book.quotes["ask"].price
    assert bid < ask
    fair = book.quotes["bid"].fair
    assert bid < fair < ask
    # never cross the fake book (0.48 / 0.52)
    assert bid <= 0.51 and ask >= 0.49


def test_no_quotes_past_inning_gate(tmp_path):
    late = MlbLiveState(1, 2, 2, 9, True, 0, False, False, False, False)
    eng, _ = make_engine(tmp_path, late)
    book = make_book(eng)
    eng.cycle()
    assert book.quotes == {}


def test_kill_file_pulls_quotes(tmp_path):
    eng, _ = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    assert book.quotes
    (tmp_path / "KILL").touch()
    eng.cycle()
    assert book.quotes == {}


def test_inventory_cap_stops_one_side(tmp_path):
    eng, _ = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    book.inventory = eng.max_net_contracts   # long-capped
    eng.cycle()
    assert "bid" not in book.quotes
    assert "ask" in book.quotes


# ── Paper fill rules (the load-bearing pessimism) ────────────────────

def test_fill_only_when_traded_through(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()                       # post quotes at t=1000
    bid = book.quotes["bid"].price
    kalshi = eng.kalshi
    kalshi.trades = [
        # AT our bid — must NOT fill (queue priority unknowable)
        {"epoch": 1005.0, "yes_price": bid, "count": 10,
         "taker_side": "no", "trade_id": "t1"},
        # strictly through our bid — fills
        {"epoch": 1006.0, "yes_price": bid - 0.01, "count": 15,
         "taker_side": "no", "trade_id": "t2"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    fills = book.fills
    assert len(fills) == 1
    assert fills[0].side == "buy"
    assert fills[0].price == bid
    assert fills[0].qty == 15
    assert book.inventory == 15


def test_fill_size_capped_by_quote_size(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE, quote_size=10.0)
    book = make_book(eng)
    eng.cycle()
    ask = book.quotes["ask"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": ask + 0.02, "count": 500,
         "taker_side": "yes", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    assert len(book.fills) == 1
    assert book.fills[0].qty == 10.0
    assert book.inventory == -10.0


def test_no_fill_before_quote_active(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()                       # quotes active_from t=1000
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        # print from BEFORE our quote existed
        {"epoch": 999.0, "yes_price": bid - 0.05, "count": 10,
         "taker_side": "no", "trade_id": "t0"},
    ]
    book.last_trade_epoch = 990.0     # force the old print into the window
    clock["t"] = 1012.0
    eng.cycle()
    assert book.fills == []


# ── Markouts & settlement ────────────────────────────────────────────

def test_markouts_written_at_horizons(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.01, "count": 5,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    assert len(book.fills) == 1
    clock["t"] = 1005.0 + 301.0       # past +1m and +5m
    eng.kalshi.trades = []
    eng.cycle()
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    horizons = {r["horizon_s"] for r in recs if r["type"] == "MARKOUT"}
    assert horizons == {60.0, 300.0}


def test_fill_stamps_vpin_telemetry(tmp_path):
    """FILL records carry vpin_at_fill / one_sidedness_at_fill.  The
    triggering print alone can't close a VPIN bucket (default 50), so
    vpin is None but one_sidedness is live off the single classified
    print."""
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.01, "count": 5,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    fill = next(r for r in recs if r["type"] == "FILL")
    assert "vpin_at_fill" in fill and "one_sidedness_at_fill" in fill
    # one print of 5 sells → fully one-sided; not enough to close a bucket
    assert fill["vpin_at_fill"] is None
    assert fill["one_sidedness_at_fill"] == 1.0


def test_next_event_markout_recorded_on_state_change(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()                       # post quotes at LIVE_STATE
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.01, "count": 5,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()                       # fill (buy), no state change yet
    assert len(book.fills) == 1
    fills_before = [json.loads(l) for l in
                    (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    assert not any(r.get("horizon_type") == "next_event"
                   for r in fills_before)
    # Advance the game state (home scores) → observed state change
    eng.statsapi.state = MlbLiveState(1, 2, 3, 5, True, 1, False, False,
                                      False, False)
    eng.kalshi.trades = []
    clock["t"] = 1020.0
    eng.cycle()
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    ne = [r for r in recs if r.get("horizon_type") == "next_event"]
    assert len(ne) == 1
    # mid of the fake book (0.48/0.52) = 0.50; buy sign = +1
    assert abs(ne[0]["markout_per_contract"] - (0.50 - bid)) < 1e-9
    assert ne[0]["secs_elapsed"] == 1020.0 - 1005.0
    # exactly one event-clocked markout per fill (idempotent)
    eng.statsapi.state = MlbLiveState(1, 2, 4, 6, True, 0, False, False,
                                      False, False)
    clock["t"] = 1030.0
    eng.cycle()
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    assert sum(1 for r in recs if r.get("horizon_type") == "next_event") == 1


def test_wallclock_markouts_tagged_horizon_type(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.01, "count": 5,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    clock["t"] = 1005.0 + 301.0
    eng.kalshi.trades = []
    eng.cycle()
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    wc = [r for r in recs if r["type"] == "MARKOUT"
          and r.get("horizon_type") == "wallclock"]
    assert wc and all(r["horizon_s"] in (60.0, 300.0) for r in wc)


def test_settlement_pnl_includes_maker_fee(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.01, "count": 10,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    # Home wins → our long-YES fill pays out
    final = MlbLiveState(1, 5, 2, 9, True, 2, False, False, False, True)
    eng.statsapi.state = final
    eng.kalshi.trades = []
    clock["t"] = 2000.0
    eng.cycle()
    assert book.done
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    settles = [r for r in recs if r["type"] == "SETTLE"]
    assert len(settles) == 1
    s = settles[0]
    expected = (1.0 - bid - s["maker_fee_per_contract"]) * 10
    assert abs(s["pnl_usd"] - expected) < 1e-9
    assert s["maker_fee_per_contract"] > 0


# ── Shadow / counterfactual logging ──────────────────────────────────

def test_inning_gate_produces_shadow_quotes_not_real(tmp_path):
    late = MlbLiveState(1, 2, 2, 9, True, 0, False, False, False, False)
    eng, _ = make_engine(tmp_path, late)
    book = make_book(eng)
    eng.cycle()
    assert book.quotes == {}
    assert set(book.shadow_quotes) == {"bid", "ask"}
    assert book.shadow_reason == "inning_gate"


def test_inventory_cap_side_becomes_shadow(tmp_path):
    eng, _ = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    book.inventory = eng.max_net_contracts
    eng.cycle()
    assert "bid" not in book.quotes
    assert "bid" in book.shadow_quotes
    assert book.shadow_reason == "inventory_cap"


def test_shadow_fill_does_not_move_inventory_or_pnl(tmp_path):
    late = MlbLiveState(1, 2, 2, 9, True, 0, False, False, False, False)
    eng, clock = make_engine(tmp_path, late)
    book = make_book(eng)
    eng.cycle()                        # shadow-only (inning gate)
    bid = book.shadow_quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.02, "count": 10,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    assert len(book.fills) == 1
    f = book.fills[0]
    assert f.shadow is True
    assert f.reason == "inning_gate"
    assert book.inventory == 0.0       # shadow never moves inventory

    # Settlement: shadow fill is logged but excluded from realized P&L
    eng.statsapi.state = MlbLiveState(
        1, 5, 2, 9, True, 2, False, False, False, True)
    eng.kalshi.trades = []
    clock["t"] = 2000.0
    eng.cycle()
    recs = [json.loads(l) for l in
            (tmp_path / "maker_fills.jsonl").read_text().splitlines()]
    settles = [r for r in recs if r["type"] == "SETTLE"]
    assert len(settles) == 1
    assert settles[0]["shadow"] is True


def test_kill_switch_stops_shadow_too(tmp_path):
    late = MlbLiveState(1, 2, 2, 9, True, 0, False, False, False, False)
    eng, _ = make_engine(tmp_path, late)
    book = make_book(eng)
    eng.cycle()
    assert book.shadow_quotes
    (tmp_path / "KILL").touch()
    eng.cycle()
    assert book.shadow_quotes == {}
    assert book.quotes == {}


def test_fill_telemetry_recorded(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    bid = book.quotes["bid"].price
    eng.kalshi.trades = [
        {"epoch": 1005.0, "yes_price": bid - 0.01, "count": 5,
         "taker_side": "no", "trade_id": "t1"},
    ]
    clock["t"] = 1012.0
    eng.cycle()
    f = book.fills[0]
    assert f.inning == 5 and f.is_top is True
    assert f.sens_at_quote > 0
    assert f.quote_dist_from_mid is not None
    assert f.secs_since_state_change is not None


def test_requote_on_state_change_deactivates_old_quotes(tmp_path):
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    old_bid = book.quotes["bid"]
    # Score changes → new state key → fresh quotes with new active_from.
    # The market moves with the score too (a static mid would trip the
    # divergence guard, which is itself covered below).
    eng.statsapi.state = MlbLiveState(
        1, 3, 2, 5, False, 0, False, False, False, False)
    eng.kalshi.book = {"yes_bid": 0.68, "yes_ask": 0.72,
                       "bid_qty": 100, "ask_qty": 100}
    clock["t"] = 1030.0
    eng.cycle()
    assert book.quotes["bid"].active_from == 1030.0
    assert book.quotes["bid"] is not old_bid


# ── Market anchoring, divergence guard, anchor integrity ─────────────

def test_divergence_guard_pulls_quotes(tmp_path):
    """Model far from market ⇒ model is the suspect: stop quoting."""
    eng, clock = make_engine(tmp_path, LIVE_STATE)
    book = make_book(eng)
    eng.cycle()
    assert book.quotes
    # Market stays at 0.50 while home takes a big lead → huge divergence
    eng.statsapi.state = MlbLiveState(
        1, 8, 1, 6, False, 0, False, False, False, False)
    clock["t"] = 1030.0
    eng.cycle()
    assert book.quotes == {}
    recs = [json.loads(l) for l in
            (tmp_path / "maker_quotes.jsonl").read_text().splitlines()]
    assert any(r.get("reason") == "divergence_guard" for r in recs)


def test_quotes_are_market_anchored_not_model_anchored(tmp_path):
    """Quote center tracks mid within max_tilt even when fair is far."""
    eng, _ = make_engine(tmp_path, LIVE_STATE, max_tilt=0.03,
                         max_divergence=0.99)
    book = make_book(eng, pregame=0.5)
    eng.kalshi.book = {"yes_bid": 0.28, "yes_ask": 0.32,
                       "bid_qty": 100, "ask_qty": 100}
    eng.cycle()
    mid = 0.30
    center = (book.quotes["bid"].price + book.quotes["ask"].price) / 2.0
    # Center must stay near mid (tilt + FLB skew), not run off to fair
    assert abs(center - mid) < 0.06, center


def test_implied_anchor_reproduces_live_mid(tmp_path):
    """A game first seen in progress must NOT take the live mid as a
    pregame prior (that double-counts the score)."""
    from src.mlb_win_prob import MlbGameSnapshot, home_win_probability
    state = MlbLiveState(1, 4, 1, 6, False, 1, False, False, False, False)
    eng, _ = make_engine(tmp_path, state)
    book = make_book(eng)
    book.pregame_prob = None          # simulate mid-flight discovery
    book.anchor_source = "pending_implied"
    eng.kalshi.book = {"yes_bid": 0.78, "yes_ask": 0.82,
                       "bid_qty": 100, "ask_qty": 100}
    eng.cycle()
    assert book.anchor_source == "implied_from_live_mid"
    # The recovered prior must reproduce the live mid at that state
    snap = MlbGameSnapshot(4, 1, 6, False, 1, False, False, False)
    got = home_win_probability(snap, book.pregame_prob).home_wp
    assert abs(got - 0.80) < 0.02, got
    # ...and it must be a sane team-strength prior, not the live price
    assert 0.05 <= book.pregame_prob <= 0.95


def test_anchor_cache_survives_restart(tmp_path):
    eng, _ = make_engine(tmp_path, LIVE_STATE)
    eng._anchor_cache["KX-TEST"] = {"pregame_prob": 0.61,
                                    "source": "kalshi_pregame_mid"}
    eng._save_anchors()
    eng2, _ = make_engine(tmp_path, LIVE_STATE)
    assert eng2._anchor_cache["KX-TEST"]["pregame_prob"] == 0.61


def test_no_quote_on_side_that_is_negative_ev_vs_fair(tmp_path):
    """Price clamps must never push a side through our own fair."""
    extreme = MlbLiveState(1, 9, 2, 7, False, 0, False, False, False, False)
    eng, _ = make_engine(tmp_path, extreme, max_divergence=0.99)
    book = make_book(eng, pregame=0.5)
    eng.kalshi.book = {"yes_bid": 0.96, "yes_ask": 0.99,
                       "bid_qty": 100, "ask_qty": 100}
    eng.cycle()
    fair = None
    for q in book.quotes.values():
        fair = q.fair
    if "ask" in book.quotes:
        assert book.quotes["ask"].price > fair
    if "bid" in book.quotes:
        assert book.quotes["bid"].price < fair
