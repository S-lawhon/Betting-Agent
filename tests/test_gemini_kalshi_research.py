from copy import deepcopy

from src.gemini_kalshi_research import (
    evaluate_match, gemini_fee_total, match_events, normalize_gemini_events,
    normalize_kalshi_events, normalize_mlb_team,
)


def _gemini(start="2026-08-02T13:40:00-04:00", event_id="gem-1"):
    return {
        "eventId": event_id, "title": "Pittsburgh at Cincinnati",
        "startTime": start,
        "sportsMarket": {"sport": "baseball", "type": "moneyline"},
        "contracts": [
            {"contractId": "pit", "title": "Pittsburgh Pirates",
             "prices": {"bestBid": "0.05", "bestAsk": "0.06",
                        "buy": {"yes": "0.06"}, "sell": {"yes": "0.05"}}},
            {"contractId": "cin", "title": "Cincinnati Reds", "bid": .94, "ask": .95},
        ],
    }


def _kalshi(ticker="KXMLBGAME-26AUG021340PITCIN"):
    return {
        "event_ticker": ticker, "title": "Pittsburgh at Cincinnati Winner?",
        "markets": [
            {"ticker": ticker + "-PIT", "yes_sub_title": "Pittsburgh",
             "yes_bid_dollars": "0.0500", "yes_ask_dollars": "0.0600"},
            {"ticker": ticker + "-CIN", "yes_sub_title": "Cincinnati",
             "yes_bid_dollars": "0.9400", "yes_ask_dollars": "0.9500"},
        ],
    }


def _match():
    gemini, _ = normalize_gemini_events([_gemini()])
    kalshi, _ = normalize_kalshi_events([_kalshi()])
    matches, _ = match_events(gemini, kalshi)
    return matches[0]


def test_normalizes_aliases_and_exact_event():
    assert normalize_mlb_team("Pittsburgh Pirates") == "pirates"
    assert normalize_mlb_team("PIT") == "pirates"
    gemini, rejected_g = normalize_gemini_events([_gemini()])
    kalshi, rejected_k = normalize_kalshi_events([_kalshi()])

    matches, counts = match_events(gemini, kalshi)

    assert not rejected_g and not rejected_k
    assert counts == {"shared_keys": 1, "ambiguous_keys": 0,
                      "schedule_mismatches": 0, "matched": 1}
    assert matches[0]["terms_equivalence"] == "unverified"
    assert matches[0]["teams"] == ["pirates", "reds"]
    assert matches[0]["gemini"]["scheduled_start_at"] == "2026-08-02T17:40:00Z"
    assert matches[0]["kalshi"]["scheduled_start_at"] == "2026-08-02T17:40:00Z"


def test_same_teams_on_different_dates_do_not_match():
    gemini, _ = normalize_gemini_events([_gemini(start="2026-08-03T17:40:00-04:00")])
    kalshi, _ = normalize_kalshi_events([_kalshi()])

    matches, counts = match_events(gemini, kalshi)

    assert matches == []
    assert counts["shared_keys"] == 0


def test_same_teams_and_date_with_different_start_times_do_not_match():
    gemini, _ = normalize_gemini_events([
        _gemini(start="2026-08-02T17:40:00-04:00")])
    kalshi, _ = normalize_kalshi_events([_kalshi()])

    matches, counts = match_events(gemini, kalshi)

    assert matches == []
    assert counts["schedule_mismatches"] == 1


def test_duplicate_event_key_is_rejected_as_ambiguous():
    gemini, _ = normalize_gemini_events([_gemini(), _gemini(event_id="gem-2")])
    kalshi, _ = normalize_kalshi_events([_kalshi()])

    matches, counts = match_events(gemini, kalshi)

    assert matches == []
    assert counts["ambiguous_keys"] == 1


def test_actual_fee_rounds_up_to_a_cent():
    assert gemini_fee_total(1, .06) == .01


def test_positive_dislocation_is_not_actionable_without_terms_approval():
    match = _match()
    match["gemini"]["outcomes"]["pirates"]["ask"] = .40
    match["kalshi"]["outcomes"]["reds"]["ask"] = .50

    evaluated = evaluate_match(match)
    path = next(p for p in evaluated["paths"]
                if p["gemini_yes_team"] == "pirates")

    assert path["hypothetical_positive"] is True
    assert path["actionable"] is False
    assert "terms_unverified" in path["blockers"]


def test_explicit_non_equivalence_is_visible_in_blocker():
    gemini, _ = normalize_gemini_events([_gemini()])
    kalshi, _ = normalize_kalshi_events([_kalshi()])
    matches, _ = match_events(gemini, kalshi, terms_policy={
        "id": "reviewed", "status": "not_equivalent",
        "reason": "void rules differ", "actionable_allowed": False,
    })

    evaluated = evaluate_match(matches[0])

    assert evaluated["terms_equivalence"] == "not_equivalent"
    assert all("terms_not_equivalent" in path["blockers"]
               for path in evaluated["paths"])


def test_verified_terms_and_synchronized_positive_quotes_can_be_actionable():
    match = deepcopy(_match())
    match["terms_equivalence"] = "verified"
    match["gemini"]["outcomes"]["pirates"]["ask"] = .40
    match["kalshi"]["outcomes"]["reds"]["ask"] = .50

    evaluated = evaluate_match(match, quote_skew_seconds=2)
    path = next(p for p in evaluated["paths"]
                if p["gemini_yes_team"] == "pirates")

    assert path["actionable"] is True
    assert path["net_edge_usd"] > 0


def test_missing_ask_is_explicitly_blocked():
    match = _match()
    match["gemini"]["outcomes"]["pirates"]["ask"] = None

    evaluated = evaluate_match(match)
    path = next(p for p in evaluated["paths"]
                if p["gemini_yes_team"] == "pirates")

    assert path["net_edge_usd"] is None
    assert "missing_ask" in path["blockers"]
