"""
tests/test_golf_backtest_weighting.py
─────────────────────────────────────
Regression tests for the contract-weighting fix in
`golf_research/backtest/backtest_golf.py::leg_fade_maker`.

The original code appended one `per_bet` entry per through-print FILL EVENT
regardless of how many contracts that print filled, then overwrote the
correctly weighted mean with that unweighted fill-event mean. A 1-contract
fill counted as much as a 500-contract fill, which overstated Leg B by
~1.6c on 6 events (+4.90c reported vs +3.34c actual).

These tests pin the weighted estimator so the bug cannot silently return.
No network, no data files: the bootstrap is exercised directly.
"""
import sys
from pathlib import Path

import pytest

_BT = Path(__file__).resolve().parents[1] / "golf_research" / "backtest"
sys.path.insert(0, str(_BT))

bg = pytest.importorskip("backtest_golf")


# ── The estimator itself ─────────────────────────────────────────────────

def test_weighted_mean_respects_fill_size():
    """One big losing fill must outweigh many small winning fills."""
    per_bet = [("EV1", +0.10, 1.0)] * 9 + [("EV1", -0.10, 91.0)]
    mean, _, _ = bg.bootstrap_ci_weighted(per_bet)
    # weighted: (9*0.10 - 91*0.10)/100 = -0.082
    assert mean == pytest.approx(-0.082, abs=1e-9)

    # the unweighted estimator gets the SIGN wrong on the same data
    unweighted, _, _ = bg.bootstrap_ci([(ev, p) for ev, p, _ in per_bet])
    assert unweighted > 0


def test_weighted_matches_unweighted_when_sizes_equal():
    """With uniform fill sizes the two estimators must agree."""
    per_bet = [("EV1", 0.10, 5.0), ("EV1", -0.04, 5.0), ("EV2", 0.02, 5.0)]
    w, _, _ = bg.bootstrap_ci_weighted(per_bet)
    u, _, _ = bg.bootstrap_ci([(ev, p) for ev, p, _ in per_bet])
    assert w == pytest.approx(u, abs=1e-12)


def test_weighted_bootstrap_clusters_by_tournament():
    """CI must come from resampling EVENTS, not individual fills.

    Two events with opposite signs: a fill-level bootstrap would give a
    tight CI, an event-clustered one must span both event means.
    """
    per_bet = ([("WIN", +0.20, 10.0)] * 50 +
               [("LOSE", -0.20, 10.0)] * 50)
    mean, lo, hi = bg.bootstrap_ci_weighted(per_bet)
    assert mean == pytest.approx(0.0, abs=1e-9)
    # resampling 2 clusters yields means of exactly -0.20, 0.0, or +0.20
    assert lo == pytest.approx(-0.20, abs=1e-9)
    assert hi == pytest.approx(+0.20, abs=1e-9)


def test_weighted_bootstrap_is_deterministic():
    per_bet = [("A", 0.05, 3.0), ("B", -0.02, 7.0), ("C", 0.11, 1.0)]
    assert bg.bootstrap_ci_weighted(per_bet) == bg.bootstrap_ci_weighted(per_bet)


def test_single_event_returns_nan_ci():
    mean, lo, hi = bg.bootstrap_ci_weighted([("ONLY", 0.05, 4.0)])
    assert mean == pytest.approx(0.05)
    assert lo != lo and hi != hi          # NaN, as with bootstrap_ci


def test_empty_and_zero_weight_are_safe():
    assert bg.bootstrap_ci_weighted([]) == (0.0, 0.0, 0.0)
    mean, lo, hi = bg.bootstrap_ci_weighted([("A", 0.5, 0.0)])
    assert mean == 0.0 and lo != lo and hi != hi


# ── leg_fade_maker wiring ────────────────────────────────────────────────

def _fill_rec(event, ticker, result, prints):
    """One market whose prints all sit inside the 36h->6h fade window."""
    close = 1_700_000_000.0
    return {
        "ticker": ticker,
        "event": f"{ticker.split('-')[0]}-{event}-XX",
        "close_time": "2023-11-14T22:13:20+00:00",   # == close epoch
        "result": result,
        "trades": [
            {"created_time": _iso(close - h * 3600.0),
             "yes_price_dollars": px, "count_fp": cnt}
            for h, px, cnt in prints
        ],
    }


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _candle_rec(ticker, bid, ask, epoch):
    return {"ticker": ticker,
            "candles": [{"end_period_ts": epoch,
                         "yes_bid": {"close_dollars": bid},
                         "yes_ask": {"close_dollars": ask}}]}


def test_leg_fade_maker_weights_by_contract_count():
    """Two events, identical prices, differing only in FILL SIZE.

    A tiny winning fill and a large losing fill: the reported headline must
    follow the contracts, not the fill count.
    """
    close = 1_700_000_000.0
    t_at = close - 36.0 * 3600.0
    tickers = ["KXPGATOP20-A-X", "KXPGATOP20-B-Y"]
    # mid = 0.20 -> quote 0.23; prints at 0.30 fill THROUGH the ask
    candle_index = {t: _candle_rec(t, 0.18, 0.22, t_at) for t in tickers}

    trades = [
        # event A: 1 contract, result no  -> +0.23/contract
        _fill_rec("AAA", tickers[0], "no", [(20.0, 0.30, 1.0)]),
        # event B: 25 contracts, result yes -> -0.77/contract
        _fill_rec("BBB", tickers[1], "yes", [(20.0, 0.30, 25.0)]),
    ]

    row = bg.leg_fade_maker(trades, candle_index, ("KXPGATOP20",),
                            band=(0.08, 0.40), quote_offset=0.03,
                            quote_size=25.0, fade_start_h=36.0,
                            fade_end_h=6.0)

    assert row["total_contracts_filled"] == pytest.approx(26.0)
    assert row["n_fill_events"] == 2

    # contract-weighted: (1*0.23 + 25*-0.77)/26
    expected = (1 * 0.23 + 25 * -0.77) / 26
    assert row["mean_pnl_per_contract"] == pytest.approx(round(expected, 4))

    # the old headline was the mean over fill events: (0.23 - 0.77)/2 = -0.27,
    # materially different -- and it must no longer be the headline
    assert row["mean_pnl_per_fill_event_unweighted"] == pytest.approx(-0.27)
    assert row["mean_pnl_per_contract"] != row["mean_pnl_per_fill_event_unweighted"]


def test_leg_fade_maker_per_event_breakdown_is_weighted():
    close = 1_700_000_000.0
    t_at = close - 36.0 * 3600.0
    tk = "KXPGATOP10-A-X"
    candle_index = {tk: _candle_rec(tk, 0.18, 0.22, t_at)}
    # two prints in the same market: 2 then 8 contracts, market resolves no
    trades = [_fill_rec("AAA", tk, "no", [(20.0, 0.30, 2.0), (18.0, 0.30, 8.0)])]

    row = bg.leg_fade_maker(trades, candle_index, ("KXPGATOP10",),
                            band=(0.08, 0.40), quote_offset=0.03,
                            quote_size=25.0, fade_start_h=36.0,
                            fade_end_h=6.0)

    assert row["per_event"]["AAA"]["contracts"] == pytest.approx(10.0)
    assert row["per_event"]["AAA"]["net_per_contract"] == pytest.approx(0.23)
