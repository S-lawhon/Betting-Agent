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


# ── Candidate selection under the position cap ───────────────────────
#
# The band rule qualifies far more names than max_open_positions allows
# (174 candidates vs a cap of 40 on a live 3M Open board), so the
# selection rule decides what the pod actually owns.


def _band_markets(n_top10, n_top20, close, lo=0.08, hi=0.45):
    """Candidates spread evenly across the ask band in both series."""
    out = []
    for series, n in (("KXPGATOP10", n_top10), ("KXPGATOP20", n_top20)):
        for i in range(n):
            ask = lo + (hi - lo) * i / max(n - 1, 1)
            out.append(_market(
                f"{series}-XYZ26-P{i:03d}", f"{series}-XYZ26",
                round(ask - 0.01, 4), round(ask, 4), close,
            ))
    return out


def test_cap_does_not_skim_only_the_cheapest_names():
    """Regression: ranking by net edge == sorting by ask ascending.

    net = edge_bump - taker_fee(ask), and the quadratic fee rises with
    ask, so net edge decreases monotonically in price.  Ranking on it
    would buy only the cheap tail — a higher-variance strategy than the
    band-uniform buy the backtest validated.
    """
    now = _now()
    close = now + timedelta(days=6)
    pod = _pod(_band_markets(60, 60, close), now, max_open_positions=20)
    placed = [r for r in pod.scan_once() if r.action == "PLACED"]

    assert len(placed) == 20
    asks = [r.venue_prob for r in placed]
    # must reach into the expensive half of the band, not stop at the tail
    assert max(asks) > 0.30, f"selection skimmed the cheap tail: max ask {max(asks)}"
    assert min(asks) < 0.15, "selection missed the cheap end entirely"


def test_cap_preserves_both_series():
    """Discovery order spent the whole cap on top-10; top-20 is the
    stronger backtest leg (+8.3c vs +4.9c) and must not be shut out."""
    now = _now()
    close = now + timedelta(days=6)
    pod = _pod(_band_markets(60, 60, close), now, max_open_positions=20)
    placed = [r for r in pod.scan_once() if r.action == "PLACED"]

    series = {r.market_id.split("-")[0] for r in placed}
    assert series == {"KXPGATOP10", "KXPGATOP20"}


def test_series_representation_is_proportional():
    """A 3:1 candidate imbalance should carry into the selection."""
    now = _now()
    close = now + timedelta(days=6)
    pod = _pod(_band_markets(90, 30, close), now, max_open_positions=20)
    placed = [r for r in pod.scan_once() if r.action == "PLACED"]

    n10 = sum(1 for r in placed if r.market_id.startswith("KXPGATOP10"))
    assert 12 <= n10 <= 17, f"expected ~15 of 20 from top-10, got {n10}"


def test_no_selection_when_cap_not_binding():
    """Fewer candidates than slots — everything qualifying gets placed."""
    now = _now()
    close = now + timedelta(days=6)
    pod = _pod(_band_markets(5, 5, close), now, max_open_positions=40)
    placed = [r for r in pod.scan_once() if r.action == "PLACED"]
    assert len(placed) == 10


def test_selection_is_deterministic():
    """No RNG — two identical pods must pick identically (reproducible
    paper runs, and a backtest that can be replayed)."""
    now = _now()
    close = now + timedelta(days=6)
    a = [r.market_id for r in _pod(_band_markets(50, 50, close), now,
                                   max_open_positions=15).scan_once()
         if r.action == "PLACED"]
    b = [r.market_id for r in _pod(_band_markets(50, 50, close), now,
                                   max_open_positions=15).scan_once()
         if r.action == "PLACED"]
    assert a == b


def test_close_utc_recorded_for_settler():
    """The settler's stale guard needs an absolute close reference."""
    now = _now()
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-XYZ26-SCHE", "KXPGATOP20-XYZ26", 0.18, 0.20, close)
    placed = [r for r in _pod([m], now).scan_once() if r.action == "PLACED"]
    assert len(placed) == 1
    assert placed[0].extra["close_utc"].startswith("2026-07-21")


# ── Cap must survive a restart ───────────────────────────────────────

class _StubStore:
    """Minimal TradeStore stand-in: knows which positions are open."""

    def __init__(self, open_market_ids):
        self._open = list(open_market_ids)

    def get_open_trades(self, pod_id=None):
        return [{"pod_id": pod_id or "P-017", "market_id": m}
                for m in self._open]

    # BasePod dedup/open-position hooks
    def is_fingerprint_seen(self, fp):
        return False

    def is_position_open(self, market_id):
        return market_id in self._open

    @property
    def seen_fingerprints(self):
        return set()

    def get_open_market_ids(self):
        return set(self._open)

    def append(self, entry):
        pass


def test_cap_counts_positions_from_store_not_process_counter():
    """Regression: _open_count is process-local and resets on restart.

    Observed in production — two deploys in one day took P-017 from 27
    positions/$247 to 36/$305, past the 25% per-pod exposure policy,
    because each restart handed the pod a fresh full cap while the real
    positions lived on in the TradeStore.
    """
    now = _now()
    close = now + timedelta(days=6)
    # 30 positions already open from before the "restart"
    store = _StubStore([f"KXPGATOP10-XYZ26-OLD{i:03d}" for i in range(30)])
    pod = _pod(_band_markets(60, 60, close), now,
               max_open_positions=40, trade_store=store)

    placed = [r for r in pod.scan_once() if r.action == "PLACED"]
    # only 10 slots left, not a fresh 40
    assert len(placed) == 10, f"expected 10 remaining slots, got {len(placed)}"


def test_cap_blocks_entirely_when_store_already_full():
    now = _now()
    close = now + timedelta(days=6)
    store = _StubStore([f"KXPGATOP10-XYZ26-OLD{i:03d}" for i in range(40)])
    pod = _pod(_band_markets(60, 60, close), now,
               max_open_positions=40, trade_store=store)
    assert [r for r in pod.scan_once() if r.action == "PLACED"] == []


def test_cap_falls_back_to_counter_without_store():
    """No store attached (standalone/backtest) — old behaviour holds."""
    now = _now()
    close = now + timedelta(days=6)
    pod = _pod(_band_markets(60, 60, close), now, max_open_positions=12)
    assert len([r for r in pod.scan_once() if r.action == "PLACED"]) == 12

# ── Aggregate risk actually binds intra-cycle ────────────────────────────
# P-017 places its whole book in ONE scan (every candidate is visible at
# once, 4-10 days before close).  The guard only registers exposure in
# post_cycle, so before reservations existed every name in the burst was
# checked against zero pod exposure and approved — the guard rejected
# nothing, and only the pod's own max_open_positions prevented a breach.

def _guard(bankroll=1_000.0, max_pod_exposure_pct=0.25):
    from src.aggregate_risk import AggregateRiskGuard
    return AggregateRiskGuard(
        bankroll=bankroll,
        max_pod_exposure_pct=max_pod_exposure_pct,
        max_total_exposure_pct=1.0,
        max_venue_exposure_pct=1.0,
        max_open_positions=10_000,
    )


def test_burst_respects_pod_exposure_cap():
    now = _now()
    close = now + timedelta(days=6)
    guard = _guard()
    pod = _pod(_band_markets(90, 90, close), now,
               max_open_positions=500, aggregate_risk=guard)

    results = pod.scan_once()
    placed = [r for r in results if r.action == "PLACED"]
    exposure = sum(r.position_size_usd for r in placed)

    # The 25% per-pod cap on a $1000 bankroll is $250.
    assert exposure <= 250.0
    # ...and the guard is what stopped it, not the pod's own cap.
    assert len(placed) < 180
    assert any(r.skip_reason == "aggregate risk guard"
               for r in results if r.action == "SKIPPED_RISK")


def test_burst_exposure_scales_with_the_cap_not_the_position_count():
    """Raising max_open_positions must no longer raise exposure.

    Measured before the fix, on a live board with a $1000 bankroll:
        cap 40  ->  28 placed, $247   (24.7% of the fund)
        cap 93  ->  71 placed, $612   (61%)
        cap 174 -> 134 placed, $1122  (112%)
    The guard rejected nothing in any of those runs.
    """
    now = _now()
    close = now + timedelta(days=6)

    def _exposure(cap):
        pod = _pod(_band_markets(90, 90, close), now,
                   max_open_positions=cap, aggregate_risk=_guard())
        return sum(r.position_size_usd
                   for r in pod.scan_once() if r.action == "PLACED")

    for cap in (40, 93, 174):
        assert _exposure(cap) <= 250.0


def test_positions_dropped_after_approval_do_not_leak_reservations():
    """Sub-$1 sizes are dropped BEFORE the guard is consulted.

    P-017 applies the depth cap and the $1 floor first, so an abandoned
    candidate never holds a reservation.  Whatever the pod did reserve is
    exactly what it placed.
    """
    now = _now()
    close = now + timedelta(days=6)
    guard = _guard()
    # ask_size=1 → depth cap crushes most sizes under the $1 floor.
    markets = [
        _market(f"KXPGATOP20-XYZ26-P{i:03d}", "KXPGATOP20-XYZ26",
                0.18, 0.20, close, ask_size=1)
        for i in range(40)
    ]
    pod = _pod(markets, now, max_open_positions=500, aggregate_risk=guard)
    results = pod.scan_once()

    placed = [r for r in results if r.action == "PLACED"]
    reserved = sum(res["usd"] for res in guard._reservations.values())
    assert reserved == pytest.approx(sum(r.position_size_usd for r in placed))
    assert len(guard._reservations) == len(placed)
