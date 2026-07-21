import pytest
from src.kalshi_fees import (fee_per_contract, fee_total, fee_fraction_of_stake,
                             net_edge, should_bet, series_maker_charges_fee)

def test_fee_peaks_at_50c():
    # per-contract fee is maximised at P=0.5
    f50 = fee_per_contract(0.5)
    assert f50 == pytest.approx(0.0175)
    assert fee_per_contract(0.5) > fee_per_contract(0.3)
    assert fee_per_contract(0.5) > fee_per_contract(0.7)
    assert fee_per_contract(0.05) < 0.005          # near-zero at tails

def test_maker_is_quarter_of_taker():
    assert fee_per_contract(0.5, maker=True) == pytest.approx(0.0175 * 0.25)

def test_fee_total_rounds_up_to_cent():
    # 100 contracts @ 0.50 => 0.07*100*0.25 = 1.75 exactly
    assert fee_total(100, 0.5) == pytest.approx(1.75)
    # a value needing round-up
    assert fee_total(1, 0.5) == pytest.approx(0.02)   # 0.0175 -> 0.02

def test_fee_fraction_higher_for_dogs():
    assert fee_fraction_of_stake(0.30) > fee_fraction_of_stake(0.70)
    assert fee_fraction_of_stake(0.5) == pytest.approx(0.035)

def test_net_edge_gate():
    # 5pp gross edge at 50c, taker: 0.05 - 0.0175 = 0.0325 > 0
    assert net_edge(0.55, 0.50) == pytest.approx(0.0325)
    # 1pp gross edge does NOT clear taker fee
    assert net_edge(0.51, 0.50) < 0
    # ...but a 55-70c favourite with 4pp edge clears comfortably as maker
    assert should_bet(0.62, 0.58, maker=True)
    assert not should_bet(0.51, 0.50, maker=False)

def test_half_spread_reduces_edge():
    assert net_edge(0.55, 0.50, half_spread=0.01) < net_edge(0.55, 0.50)


# ---------------------------------------------------------------------------
# Series-aware maker fees. Ground truth = per-series `fee_type` from
#   GET https://api.elections.kalshi.com/trade-api/v2/series/?category=Sports
# read live on 2026-07-20. quadratic => maker 0; quadratic_with_maker_fees =>
# maker 0.0175*P*(1-P).
# ---------------------------------------------------------------------------

# fee_type == "quadratic_with_maker_fees"
MLB_MAKER_FEE_SERIES = [
    "KXMLBGAME", "KXMLB", "KXMLBAL", "KXMLBNL", "KXMLBASGAME", "KXMLBHRDERBY",
]
# fee_type == "quadratic"  (ZERO maker fee)
MLB_ZERO_MAKER_SERIES = [
    "KXMLBHIT", "KXMLBKS", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL",
    "KXMLBTB", "KXMLBHR", "KXMLBHRR", "KXMLBSB", "KXMLBRFI", "KXMLBF5",
]


@pytest.mark.parametrize("series", MLB_MAKER_FEE_SERIES)
def test_mlb_outcome_series_charge_maker_fees(series):
    assert series_maker_charges_fee(series) is True
    assert fee_per_contract(0.5, maker=True, series_ticker=series) \
        == pytest.approx(0.0175 * 0.25)


@pytest.mark.parametrize("series", MLB_ZERO_MAKER_SERIES)
def test_mlb_prop_series_are_maker_free(series):
    assert series_maker_charges_fee(series) is False
    assert fee_per_contract(0.5, maker=True, series_ticker=series) == 0.0
    # taker side is unaffected — 0.07*P*(1-P) regardless of series
    assert fee_per_contract(0.5, maker=False, series_ticker=series) \
        == pytest.approx(0.0175)


def test_mlb_prefix_collisions_resolve_by_longest_match():
    """KXMLB < KXMLBHR < KXMLBHRDERBY, and the answer alternates at each step.

    This is the case naive `startswith` matching gets wrong.
    """
    assert series_maker_charges_fee("KXMLB") is True          # league outcome
    assert series_maker_charges_fee("KXMLBHR") is False       # HR prop
    assert series_maker_charges_fee("KXMLBHRR") is False      # HR-allowed prop
    assert series_maker_charges_fee("KXMLBHRDERBY") is True   # derby outcome
    # KXMLBGAME must not be dragged to False by any prop family
    assert series_maker_charges_fee("KXMLBGAME") is True
    # KXMLBTOTAL and KXMLBTEAMTOTAL are distinct families, both maker-free
    assert series_maker_charges_fee("KXMLBTOTAL") is False
    assert series_maker_charges_fee("KXMLBTEAMTOTAL") is False


def test_full_market_series_tickers_resolve_to_their_family():
    # real tickers carry a suffix; prefix match must still land on the family
    assert series_maker_charges_fee("KXMLBHIT-25JUL20") is False
    assert series_maker_charges_fee("KXPGATOP20") is False
    assert series_maker_charges_fee("KXPGATOUR") is True


def test_unknown_series_defaults_to_charging():
    # conservative: over-charging only makes the net-edge gate stricter
    assert series_maker_charges_fee("KXNFLGAME") is True
    assert series_maker_charges_fee("TOTALLY-MADE-UP") is True


def test_golf_classification_unchanged():
    # regression guard on the pre-existing golf behaviour
    assert series_maker_charges_fee("KXPGA") is True
    assert series_maker_charges_fee("KXTHEOPEN") is True
    assert series_maker_charges_fee("KXPGAMAKECUT") is False
    assert series_maker_charges_fee("KXPGA3BALL") is False
    assert series_maker_charges_fee("KXGOLFH2H") is False


def test_no_series_ticker_keeps_the_0175_maker_fallback():
    """P-016 (src/pods/live_maker_pod.py) calls fee_per_contract(price,
    maker=True) with NO series_ticker and depends on this fallback. It makes on
    KXMLBGAME, which genuinely charges, so the fallback is correct — and the
    pod is mid-gate, so changing it would contaminate the running sample.
    """
    assert fee_per_contract(0.5, maker=True) == pytest.approx(0.0175 * 0.25)
    assert fee_per_contract(0.5, maker=True, series_ticker="") \
        == pytest.approx(0.0175 * 0.25)
    # and it agrees with what P-016 actually trades
    assert fee_per_contract(0.5, maker=True) \
        == fee_per_contract(0.5, maker=True, series_ticker="KXMLBGAME")


def test_fee_fraction_and_net_edge_honour_zero_maker_series():
    assert fee_fraction_of_stake(0.5, maker=True, series_ticker="KXMLBTB") == 0.0
    assert fee_fraction_of_stake(0.5, maker=True, series_ticker="KXMLBGAME") \
        == pytest.approx(0.0175 * 0.5)
    # a prop maker edge that the old (over-charging) model would have shaved
    assert net_edge(0.53, 0.50, maker=True, series_ticker="KXMLBHIT") \
        == pytest.approx(0.03)
    assert net_edge(0.53, 0.50, maker=True, series_ticker="KXMLBGAME") \
        == pytest.approx(0.03 - 0.0175 * 0.25)
