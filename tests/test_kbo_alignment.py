from kbo_spread_research.alignment import book_probability, devig_probability


def test_devig_probability_removes_two_way_overround():
    assert round(devig_probability([1.8, 2.1], 0), 6) == 0.538462


def test_book_probability_requires_exact_opposite_spread_line():
    event = {"bookmakers": [{
        "key": "pinnacle",
        "markets": [{"key": "spreads", "outcomes": [
            {"name": "SSG Landers", "point": -1.5, "price": 1.8},
            {"name": "LG Twins", "point": 1.5, "price": 2.1},
        ]}],
    }]}
    result = book_probability(event, target_team="SSG Landers", margin=1.5)
    assert round(result["pinnacle"], 6) == 0.538462
    assert book_probability(event, target_team="SSG Landers", margin=2.5) == {}
