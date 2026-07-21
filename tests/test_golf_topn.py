"""
tests/test_golf_topn.py
───────────────────────
Unit tests for P-017 (Golf Top-N) — fees, de-vig, and the taker pod's
decision gate. No network: a stub KalshiPublic returns canned markets.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.kalshi_fees import fee_per_contract, series_maker_charges_fee
from src.devig import devig_power, devig_multiplicative
from src.pods.golf_topn_pod import GolfTopNPod


# ── Fees ─────────────────────────────────────────────────────────────────

def test_prop_series_zero_maker_fee():
    assert series_maker_charges_fee("KXPGATOP20") is False
    assert series_maker_charges_fee("KXPGAMAKECUT") is False
    assert fee_per_contract(0.20, maker=True, series_ticker="KXPGATOP20") == 0.0


def test_winner_series_charges_maker_fee():
    assert series_maker_charges_fee("KXPGATOUR") is True
    assert series_maker_charges_fee("KXTHEOPEN") is True
    assert fee_per_contract(0.20, maker=True, series_ticker="KXPGATOUR") > 0.0


def test_maker_fee_backward_compatible_without_series():
    # P-016 and other callers pass no series_ticker → keep the general
    # 0.0175 maker rate (unchanged behavior after the promotion).
    assert fee_per_contract(0.50, maker=True) == pytest.approx(0.0175 * 0.25)
    assert fee_per_contract(0.50, maker=True, series_ticker="KXPGATOP20") == 0.0


def test_pga_championship_winner_not_confused_with_props():
    # KXPGA (PGA Championship winner) charges maker fees; KXPGATOP20 does not.
    assert series_maker_charges_fee("KXPGA") is True
    assert series_maker_charges_fee("KXPGATOP20") is False
    assert series_maker_charges_fee("KXPGAMAKECUT") is False


def test_taker_fee_peaks_midbook():
    assert fee_per_contract(0.50) > fee_per_contract(0.20) > fee_per_contract(0.05)


def test_power_devig_shrinks_longshots_more():
    raw = [0.30, 0.22, 0.18, 0.15, 0.13, 0.10, 0.08]  # sum 1.16, overround
    assert sum(raw) > 1.0
    pw = devig_power(raw)
    mult = devig_multiplicative(raw)
    assert abs(sum(pw) - 1.0) < 1e-6
    # longshot shrinks more under power than multiplicative
    assert pw[-1] < mult[-1]
    # favorite retains more mass under power
    assert pw[0] > mult[0]


# ── Pod decision gate ─────────────────────────────────────────────────────

class _StubKalshi:
    def __init__(self, markets):
        self._markets = markets

    def open_markets(self, series_ticker, max_pages=10):
        return [m for m in self._markets
                if m["ticker"].startswith(series_ticker)]


def _market(ticker, event, yes_bid, yes_ask, close_dt, ask_size=1000):
    return {
        "ticker": ticker,
        "event_ticker": event,
        "title": ticker,
        "yes_bid_dollars": f"{yes_bid:.4f}",
        "yes_ask_dollars": f"{yes_ask:.4f}",
        "yes_ask_size_fp": str(ask_size),
        "close_time": close_dt.isoformat().replace("+00:00", "Z"),
    }


_LOG_SEQ = [0]


def _pod(markets, now, log_path=None, **kw):
    # unique log path per pod → test isolation (dedup loads from the log)
    if log_path is None:
        _LOG_SEQ[0] += 1
        log_path = Path(f"/tmp/p017_test_{_LOG_SEQ[0]}.jsonl")
        if log_path.exists():
            log_path.unlink()
    return GolfTopNPod(
        kalshi_public=_StubKalshi(markets),
        risk_manager=None,
        trade_log_path=log_path,
        _now_fn=lambda: now,
        **kw,
    )


def _now():
    return datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_places_in_band_and_window():
    now = _now()
    close = now + timedelta(days=6)  # in [4,10]
    m = _market("KXPGATOP20-XYZ26-SCHE", "KXPGATOP20-XYZ26", 0.18, 0.20, close)
    pod = _pod([m], now)
    # bankroll via stub risk manager absent → uses DEFAULT_BANKROLL
    results = pod.scan_once()
    placed = [r for r in results if r.action == "PLACED"]
    assert len(placed) == 1
    r = placed[0]
    assert r.side == "YES" and r.venue_prob == 0.20
    # fair = ask + edge_bump(0.04) = 0.24; net = 0.04 - taker_fee > min_net_edge
    assert r.fair_prob == pytest.approx(0.24, abs=1e-6)
    assert r.extra["net_edge"] > 0.02


def test_skips_outside_window():
    now = _now()
    close = now + timedelta(days=2)  # tournament in progress, < 4 days
    m = _market("KXPGATOP20-XYZ26-SCHE", "KXPGATOP20-XYZ26", 0.18, 0.20, close)
    pod = _pod([m], now)
    assert [r for r in pod.scan_once() if r.action == "PLACED"] == []


def test_skips_out_of_band():
    now = _now()
    close = now + timedelta(days=6)
    # ask 0.60 above cap 0.45
    m = _market("KXPGATOP20-XYZ26-FAVE", "KXPGATOP20-XYZ26", 0.58, 0.60, close)
    pod = _pod([m], now)
    assert [r for r in pod.scan_once() if r.action == "PLACED"] == []


def test_skips_wide_spread():
    now = _now()
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-XYZ26-WIDE", "KXPGATOP20-XYZ26", 0.10, 0.20, close)
    pod = _pod([m], now)  # spread 0.10 > max_spread 0.06
    assert [r for r in pod.scan_once() if r.action == "PLACED"] == []


def test_signature_event_skip():
    now = _now()
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-TRAV26-SCHE", "KXPGATOP20-TRAV26", 0.18, 0.20, close)
    pod = _pod([m], now, skip_events=["TRAV26"])
    assert pod.scan_once() == []  # skipped before any result emitted


def test_no_duplicate_second_scan():
    now = _now()
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-XYZ26-SCHE", "KXPGATOP20-XYZ26", 0.18, 0.20, close)
    pod = _pod([m], now)
    first = [r for r in pod.scan_once() if r.action == "PLACED"]
    second = [r for r in pod.scan_once() if r.action == "PLACED"]
    assert len(first) == 1 and len(second) == 0
