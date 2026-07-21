import math, pytest
from src.devig import (american_to_prob, decimal_to_prob,
                       devig_multiplicative, devig_two_way, fair_from_american)

def test_american_to_prob():
    assert american_to_prob(100) == pytest.approx(0.5)
    assert american_to_prob(-110) == pytest.approx(0.5238, abs=1e-3)
    assert american_to_prob(150) == pytest.approx(0.4)
    assert american_to_prob(-200) == pytest.approx(0.6667, abs=1e-3)

def test_decimal_to_prob():
    assert decimal_to_prob(2.0) == pytest.approx(0.5)
    assert decimal_to_prob(4.0) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        decimal_to_prob(1.0)

def test_devig_sums_to_one():
    fair = devig_multiplicative([0.5238, 0.5238])
    assert sum(fair) == pytest.approx(1.0)
    assert fair[0] == pytest.approx(0.5)

def test_devig_two_way_removes_vig():
    # -110/-110 book => each raw 0.5238, fair 0.5
    assert devig_two_way(0.5238, 0.5238) == pytest.approx(0.5, abs=1e-3)

def test_fair_from_american_favorite():
    # -150 fav vs +130 dog
    f = fair_from_american(-150, 130)
    assert 0.55 < f < 0.62          # favourite fair prob, vig removed
    assert f + fair_from_american(130, -150) == pytest.approx(1.0)

def test_devig_rejects_nonpositive():
    with pytest.raises(ValueError):
        devig_multiplicative([0.0, 0.0])
