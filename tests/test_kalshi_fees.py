import pytest
from src.kalshi_fees import (fee_per_contract, fee_total, fee_fraction_of_stake,
                             net_edge, should_bet)

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
