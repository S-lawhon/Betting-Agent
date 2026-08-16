from datetime import datetime, timezone

from kbo_spread_research.feasibility import (
    contract_target,
    latest_distinct_events,
    latest_quote_before,
    scheduled_start,
)


def test_scheduled_start_uses_market_rules_and_dst():
    market = {
        "rules_primary": (
            "If X in the game originally scheduled for Aug 16, 2026 at "
            "6:00 AM EDT, then Yes."
        )
    }
    assert scheduled_start(market) == datetime(
        2026, 8, 16, 10, 0, tzinfo=timezone.utc
    )


def test_contract_target_parses_team_and_margin():
    assert contract_target({
        "title": "Will the SSG Landers win by over 2.5 runs?"
    }) == ("SSG Landers", 2.5)


def test_latest_quote_requires_real_two_sided_touch_in_age_window():
    candles = [
        {"end_period_ts": 100, "yes_bid": {"close_dollars": "0.00"},
         "yes_ask": {"close_dollars": "1.00"}},
        {"end_period_ts": 200, "yes_bid": {"close_dollars": "0.40"},
         "yes_ask": {"close_dollars": "0.44"}},
    ]
    assert latest_quote_before(candles, 500, max_age_minutes=10) == {
        "bid": 0.4, "ask": 0.44, "spread": 0.04, "age_minutes": 5.0,
    }
    assert latest_quote_before(candles, 900, max_age_minutes=10) is None


def test_event_limit_keeps_all_legs_of_selected_events():
    markets = [
        {"ticker": "a1", "event_ticker": "a"},
        {"ticker": "a2", "event_ticker": "a"},
        {"ticker": "b1", "event_ticker": "b"},
        {"ticker": "c1", "event_ticker": "c"},
    ]
    assert [row["ticker"] for row in latest_distinct_events(
        markets, event_limit=2
    )] == ["a1", "a2", "b1"]
