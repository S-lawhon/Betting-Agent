import pytest
from datetime import datetime, timezone

from soccer_research.laliga_spread_latency import (
    Collector, analyze, extract_key_events, match_kalshi_event, normalize_levels,
)


def summary():
    return {
        "header": {"competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "1", "abbreviation": "ATM"}},
            {"homeAway": "away", "team": {"id": "2", "abbreviation": "MCF"}},
        ]}]},
        "keyEvents": [
            {"id": "g1", "scoringPlay": True,
             "type": {"type": "goal"}, "team": {"id": "1"},
             "wallclock": "2026-08-19T19:10:00Z", "text": "Goal"},
            {"id": "r1", "scoringPlay": False,
             "type": {"type": "red-card"}, "team": {"id": "2"},
             "wallclock": "2026-08-19T19:20:00Z", "text": "Red"},
            {"id": "bad", "scoringPlay": True,
             "type": {"type": "goal"}, "team": {"id": "1"}},
        ],
    }


def test_extract_key_events_requires_wallclock_and_maps_team():
    events = extract_key_events(summary())
    assert [(row["id"], row["kind"], row["team_abbreviation"])
            for row in events] == [("g1", "goal", "ATM"),
                                   ("r1", "red_card", "MCF")]


def test_fixture_match_is_order_independent_and_ambiguous_fails_closed():
    teams = [{"abbreviation": "ATM"}, {"abbreviation": "MCF"}]
    good = {"event_ticker": "E1", "sub_title": "ATM vs MCF (Aug 19)"}
    assert match_kalshi_event(teams, [good])["event_ticker"] == "E1"
    assert match_kalshi_event(teams, [good, dict(good)]) is None


def test_normalize_fp_book_and_analyze_direction_without_lookahead():
    book0 = normalize_levels({"orderbook_fp": {
        "yes_dollars": [["0.4900", "100"]],
        "no_dollars": [["0.5000", "50"]],
    }})
    book1 = normalize_levels({"orderbook_fp": {
        "yes_dollars": [["0.5400", "100"]],
        "no_dollars": [["0.4500", "50"]],
    }})
    event = {"id": "g1", "kind": "goal", "team_abbreviation": "ATM",
             "wallclock": "2026-08-19T19:10:00Z"}

    def cycle(at, book, include_event=True):
        return {"captured_at": at, "matches": [{
            "espn_event_id": "m1", "status": {"type": {"state": "in"}},
            "key_events": [event] if include_event else [],
            "markets": [{"ticker": "KXLALIGASPREAD-X-ATM2",
                         "side_code": "ATM", "book": book, "trades": []}],
        }]}

    result = analyze([
        cycle("2026-08-19T19:09:50Z", book0, False),
        cycle("2026-08-19T19:10:05Z", book0),
        cycle("2026-08-19T19:10:55Z", book1),
    ])
    assert result["qualifying_shocks"] == 1
    assert result["market_rows"] == 1
    assert result["median_gross_move"] == pytest.approx(.05)
    assert result["observations"][0]["direction"] == 1
    assert result["verdict"] == "NO DECISION"


def test_event_discovered_after_post_window_is_excluded():
    event = {"id": "g1", "kind": "goal", "team_abbreviation": "ATM",
             "wallclock": "2026-08-19T19:10:00Z"}
    row = {"captured_at": "2026-08-19T19:11:20Z", "matches": [{
        "espn_event_id": "m1", "key_events": [event], "markets": []}]}
    assert analyze([row])["qualifying_shocks"] == 0


def test_post_match_stays_active_long_enough_to_record_completion():
    event = {
        "date": "2026-08-19T19:00:00Z",
        "status": {"type": {"state": "post"}},
    }
    assert Collector._active(
        event, datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc))
    assert not Collector._active(
        event, datetime(2026, 8, 19, 23, 0, tzinfo=timezone.utc))
