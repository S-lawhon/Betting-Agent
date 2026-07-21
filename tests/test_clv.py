import pytest
from src.clv import clv_gross, clv_net, clv_record, CLV_FIELDS

def test_clv_gross_positive_when_beat_close():
    # paid 0.46, sharp fair at close 0.51 => beat the line by 5pp
    assert clv_gross(0.46, 0.51) == pytest.approx(0.05)
    assert clv_gross(0.55, 0.50) == pytest.approx(-0.05)

def test_clv_net_subtracts_fee():
    g = clv_gross(0.46, 0.51)
    n = clv_net(0.46, 0.51, maker=True)
    # clv_net = gross - maker fee_per_contract(0.46) = 0.05 - 0.0175*0.46*0.54
    expected = 0.05 - 0.0175 * 0.46 * 0.54
    assert n == pytest.approx(expected)
    assert n < g

def test_clv_record_shape():
    rec = clv_record(0.46, 0.50, 0.51, maker=True, half_spread=0.005)
    for f in ("sharp_fair_entry", "sharp_fair_close", "entry_price", "half_spread", "maker"):
        assert f in rec
    assert "clv_gross" in rec and "clv_net" in rec
    assert rec["clv_gross"] == pytest.approx(0.05)

def test_clv_record_open_market_no_close():
    rec = clv_record(0.46, 0.50, None)
    assert rec["sharp_fair_close"] is None
    assert "clv_gross" not in rec   # can't compute until close
