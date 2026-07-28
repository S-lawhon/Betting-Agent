"""
tests/test_inplay_fade_maker.py
───────────────────────────────
Unit tests for P-018 (Surprise-Gated In-Play Fade Maker) — the
data-independent core: the surprise gate, regime routing, pessimistic
fills, series-aware fees, fade-size decay, and settlement P&L sign.

No network: the win-prob model is deterministic and the gate is pure, so
these tests ARE the measurable form of pre-registered kill-gate #1 (spec
§8 — does the surprise gate actually separate fade from expected/normal?).

Covers spec §6 test list.  The live engine loop is intentionally NOT built
yet (it ships only after the backtest clears the gate), so "kill file →
no quotes" is tested at the gate function that the engine will call.
"""
from __future__ import annotations

import pytest

from src.kalshi_fees import fee_per_contract, series_maker_charges_fee
from src.mlb_win_prob import MlbGameSnapshot, home_win_probability
from src.inplay_sport_adapter import MlbAdapter, NbaAdapter, mlb_event_risk
from src.mlb_statsapi import MlbLiveState
from src.inplay_surprise import (
    Regime, SurpriseParams, SurpriseState, build_quote, fade_size,
    fills_through, select_regime, settle_pnl, update_on_event,
)


# ── helpers ────────────────────────────────────────────────────────────

def _wp(home, away, inning, is_top, outs, b1=False, b2=False, b3=False,
        pregame=0.5):
    snap = MlbGameSnapshot(home_score=home, away_score=away, inning=inning,
                           is_top=is_top, outs=outs, on_first=b1,
                           on_second=b2, on_third=b3)
    return home_win_probability(snap, pregame).home_wp


# ══════════════════════════════════════════════════════════════════════
# Surprise computation (spec §6)
# ══════════════════════════════════════════════════════════════════════

def test_go_ahead_hr_is_high_surprise():
    """A go-ahead solo HR in the 7th moves true value more than surprise_hi."""
    p = SurpriseParams()
    fair_before = _wp(3, 3, 7, is_top=False, outs=0)          # tied, bottom 7
    fair_after = _wp(4, 3, 7, is_top=False, outs=0)           # home now up 1
    st = SurpriseState()
    st.seed("3-3|i7B|o0|b000", fair_before, 0.50)
    ev = update_on_event(st, "4-3|i7B|o0|b000", fair_after, 0.52, now=100.0)
    assert ev is not None
    assert ev.surprise >= p.surprise_hi, ev.surprise


def test_routine_groundout_is_low_surprise():
    """A low-leverage out (blowout, early) stays below surprise_lo.

    NB: leverage matters, not just "an out" — an out in a TIED late inning
    moves WP ~2pp (right at surprise_lo). The gate's "expected noise" case
    is a genuinely low-leverage out; the model reflects that, and it is why
    surprise_lo is a tuned knob, not a constant.
    """
    p = SurpriseParams()
    fair_before = _wp(5, 1, 3, is_top=True, outs=1)          # home up 4, top 3
    fair_after = _wp(5, 1, 3, is_top=True, outs=2)           # just an out
    st = SurpriseState()
    st.seed("1-5|i3T|o1|b000", fair_before, 0.85)
    ev = update_on_event(st, "1-5|i3T|o2|b000", fair_after, 0.85, now=100.0)
    assert ev is not None
    assert ev.surprise < p.surprise_lo, ev.surprise


def test_first_sight_is_not_an_event():
    st = SurpriseState()
    ev = update_on_event(st, "0-0|i1T|o0|b000", 0.54, 0.54, now=1.0)
    assert ev is None
    assert st.last_state_key == "0-0|i1T|o0|b000"


def test_underreaction_sign_and_value():
    st = SurpriseState()
    st.seed("s0", 0.45, 0.45)
    ev = update_on_event(st, "s1", fair_now=0.60, mid_now=0.52, now=10.0)
    assert ev.wp_jump == pytest.approx(0.15)
    assert ev.mkt_jump == pytest.approx(0.07)
    assert ev.underreaction == pytest.approx(0.08)   # model led the market up


# ══════════════════════════════════════════════════════════════════════
# Regime routing (spec §6)
# ══════════════════════════════════════════════════════════════════════

def _event_state(wp_jump, mkt_jump, fair_after=0.60):
    """Build a SurpriseState carrying one just-fired event."""
    st = SurpriseState()
    fair_before = fair_after - wp_jump
    mid_before = fair_before
    st.seed("pre", fair_before, mid_before)
    update_on_event(st, "post", fair_after, mid_before + mkt_jump, now=1000.0)
    return st


def test_high_surprise_in_window_routes_to_fade():
    p = SurpriseParams()
    st = _event_state(wp_jump=0.15, mkt_jump=0.05)   # underreaction 0.10
    r = select_regime(st, now=1000.0 + 30.0, params=p)
    assert r is Regime.FADE


def test_fade_quote_is_one_sided_on_the_correct_side():
    p = SurpriseParams()
    # Model jumped UP more than market → market overshot DOWN → rest a BID.
    st = _event_state(wp_jump=0.15, mkt_jump=0.05, fair_after=0.60)
    ev = st.last_event
    spec = build_quote(Regime.FADE, fair=0.60, mid=0.50,
                       sensitivity=0.02, params=p, secs_since_event=30.0,
                       underreaction=ev.underreaction)
    assert spec.bid is not None and spec.bid_size > 0     # one side...
    assert spec.ask is None                               # ...only.
    assert 0.50 <= spec.bid < 0.60                        # between mid and fair


def test_fade_quote_flips_side_when_market_overshoots_up():
    p = SurpriseParams()
    # Model jumped DOWN more than market → market overshot UP → rest an ASK.
    st = _event_state(wp_jump=-0.15, mkt_jump=-0.05, fair_after=0.40)
    ev = st.last_event
    spec = build_quote(Regime.FADE, fair=0.40, mid=0.50,
                       sensitivity=0.02, params=p, secs_since_event=30.0,
                       underreaction=ev.underreaction)
    assert spec.ask is not None and spec.ask_size > 0
    assert spec.bid is None
    assert 0.40 < spec.ask <= 0.50


def test_low_surprise_routes_to_expected_and_widens():
    p = SurpriseParams()
    st = _event_state(wp_jump=0.005, mkt_jump=0.0)   # surprise < surprise_lo
    r = select_regime(st, now=1000.0 + 10.0, params=p)
    assert r is Regime.EXPECTED
    spec = build_quote(Regime.EXPECTED, fair=0.505, mid=0.50,
                       sensitivity=0.0, params=p)
    assert spec.bid is not None and spec.ask is not None      # two-sided
    normal = build_quote(Regime.NORMAL, fair=0.505, mid=0.50,
                         sensitivity=0.0, params=p)
    # Expected regime quotes strictly wider than normal.
    assert (spec.ask - spec.bid) > (normal.ask - normal.bid)


def test_event_risk_pulls_all_quotes():
    p = SurpriseParams()
    st = _event_state(wp_jump=0.15, mkt_jump=0.05)
    r = select_regime(st, now=1000.0 + 30.0, params=p, event_risk=True)
    assert r is Regime.PULL
    spec = build_quote(Regime.PULL, fair=0.6, mid=0.5, sensitivity=0.0,
                       params=p)
    assert spec.bid is None and spec.ask is None


def test_kill_pulls_all_quotes():
    p = SurpriseParams()
    st = _event_state(wp_jump=0.15, mkt_jump=0.05)
    assert select_regime(st, now=1030.0, params=p, killed=True) is Regime.PULL


def test_stale_event_falls_back_to_normal():
    p = SurpriseParams()
    st = _event_state(wp_jump=0.15, mkt_jump=0.05)
    # Well past the fade window → no longer a fade.
    r = select_regime(st, now=1000.0 + p.fade_window_s + 60.0, params=p)
    assert r is Regime.NORMAL


def test_high_surprise_without_underreaction_does_not_fade():
    p = SurpriseParams()
    # Market moved WITH the model (underreaction ~0) → nothing to fade.
    st = _event_state(wp_jump=0.15, mkt_jump=0.15)
    r = select_regime(st, now=1030.0, params=p)
    assert r is Regime.NORMAL


# ══════════════════════════════════════════════════════════════════════
# Pessimistic fills (spec §6)
# ══════════════════════════════════════════════════════════════════════

def test_print_at_quote_does_not_fill():
    assert fills_through("bid", 0.56, 20, print_price=0.56, print_count=10) == 0.0
    assert fills_through("ask", 0.60, 20, print_price=0.60, print_count=10) == 0.0


def test_print_through_quote_fills():
    assert fills_through("bid", 0.56, 20, print_price=0.55, print_count=10) == 10
    assert fills_through("ask", 0.60, 20, print_price=0.61, print_count=10) == 10


def test_fill_size_is_min_of_quote_and_print():
    assert fills_through("bid", 0.56, 5, print_price=0.55, print_count=10) == 5
    assert fills_through("bid", 0.56, 20, print_price=0.55, print_count=8) == 8


# ══════════════════════════════════════════════════════════════════════
# Fees (spec §6 — must pass series_ticker)
# ══════════════════════════════════════════════════════════════════════

def test_moneyline_series_charges_maker_fee():
    assert series_maker_charges_fee("KXMLBGAME") is True
    assert fee_per_contract(0.50, maker=True, series_ticker="KXMLBGAME") > 0.0


def test_totals_series_zero_maker_fee():
    assert series_maker_charges_fee("KXMLBTOTAL") is False
    assert fee_per_contract(0.50, maker=True, series_ticker="KXMLBTOTAL") == 0.0


def test_adapter_series_ticker_is_moneyline():
    assert MlbAdapter.series_ticker == "KXMLBGAME"


# ══════════════════════════════════════════════════════════════════════
# Fade-size decay (spec §6)
# ══════════════════════════════════════════════════════════════════════

def test_fade_size_tapers_with_time():
    early = fade_size(20, secs_since_event=60, fade_window_s=600)
    late = fade_size(20, secs_since_event=300, fade_window_s=600)
    assert late < early
    assert early > 0


def test_fade_size_zero_outside_window():
    assert fade_size(20, secs_since_event=300, fade_window_s=240) == 0.0
    assert fade_size(20, secs_since_event=-5, fade_window_s=240) == 0.0


def test_fade_quote_smaller_late_in_window():
    p = SurpriseParams(fade_window_s=600)
    early = build_quote(Regime.FADE, fair=0.60, mid=0.50, sensitivity=0.02,
                        params=p, secs_since_event=60, underreaction=0.10)
    late = build_quote(Regime.FADE, fair=0.60, mid=0.50, sensitivity=0.02,
                       params=p, secs_since_event=300, underreaction=0.10)
    assert late.bid_size < early.bid_size


# ══════════════════════════════════════════════════════════════════════
# Settlement P&L sign (spec §6)
# ══════════════════════════════════════════════════════════════════════

def test_faded_buy_that_settles_yes_is_positive():
    pnl = settle_pnl("buy", price=0.56, result=1.0, qty=10,
                     series_ticker="KXMLBGAME")
    assert pnl > 0


def test_faded_buy_that_settles_no_is_negative():
    pnl = settle_pnl("buy", price=0.56, result=0.0, qty=10,
                     series_ticker="KXMLBGAME")
    assert pnl < 0


def test_faded_sell_that_settles_no_is_positive():
    pnl = settle_pnl("sell", price=0.44, result=0.0, qty=10,
                     series_ticker="KXMLBGAME")
    assert pnl > 0


def test_settlement_is_net_of_series_fee():
    gross = (1.0 - 0.56) * 10
    net = settle_pnl("buy", price=0.56, result=1.0, qty=10,
                     series_ticker="KXMLBGAME")
    assert net < gross                                   # fee taken out
    net_free = settle_pnl("buy", price=0.56, result=1.0, qty=10,
                          series_ticker="KXMLBTOTAL")
    assert net_free == pytest.approx(gross)              # props are maker-free


# ══════════════════════════════════════════════════════════════════════
# MLB event-risk gate
# ══════════════════════════════════════════════════════════════════════

def _ls(home, away, inning, is_top, outs, b1=False, b2=False, b3=False,
        final=False):
    return MlbLiveState(game_pk=1, home_score=home, away_score=away,
                        inning=inning, is_top=is_top, outs=outs,
                        on_first=b1, on_second=b2, on_third=b3, is_final=final)


def test_event_risk_true_for_risp_under_two_outs():
    assert mlb_event_risk(_ls(1, 1, 6, True, 1, b2=True)) is True


def test_event_risk_false_for_bases_empty_low_leverage():
    assert mlb_event_risk(_ls(5, 1, 3, True, 1)) is False


def test_event_risk_false_when_final():
    assert mlb_event_risk(_ls(4, 3, 9, False, 0, final=True)) is False


def test_nba_adapter_refuses_until_model_exists():
    with pytest.raises(NotImplementedError):
        NbaAdapter().discover("2026-07-22")
