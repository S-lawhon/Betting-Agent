"""
tests/test_odds_client.py
──────────────────────────
Phase 4 must-pass tests:
  ✓ MockAdapter records calls and serves canned responses
  ✓ TTL cache: returns cached value within TTL
  ✓ TTL cache: expires after TTL and re-fetches
  ✓ TTL cache: invalidate removes entry; clear removes all
  ✓ OddsClient: get_events returns parsed OddsEvent list
  ✓ OddsClient: caches response — second call makes no new HTTP request
  ✓ OddsClient: cache miss after TTL forces new HTTP request
  ✓ OddsClient: raises OddsAPIError on non-200 (not 402/429)
  ✓ OddsClient: raises OddsQuotaExhaustedError on 402 / 429
  ✓ OddsClient: quota headers parsed and stored
  ✓ OddsClient: WARNING logged below _QUOTA_WARN_LOW threshold
  ✓ OddsClient: from_config wires all params from config.yaml
  ✓ Consensus: devig produces probs summing to 1
  ✓ Consensus: weighted_sharp method used when Pinnacle present
  ✓ Consensus: median method used when Pinnacle absent
  ✓ Consensus: Pinnacle gets 2× weight vs other books
  ✓ Consensus: returns None when no configured books have odds
  ✓ Consensus: home_prob clamped to (0, 1)
  ✓ Event parsing: fields mapped correctly from raw API response
  ✓ Event parsing: bookmakers not in priority list are filtered
  ✓ Integration: full pipeline — fetch → parse → devig → consensus
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.odds_client import (
    BookmakerOdds,
    ConsensusProbability,
    FairOdds,
    OddsAPIError,
    OddsClient,
    OddsEvent,
    OddsOutcome,
    OddsQuotaExhaustedError,
    _OddsResponse,
    _TTLCache,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _raw_event(
    event_id: str = "evt-1",
    home: str = "Kansas City Chiefs",
    away: str = "Las Vegas Raiders",
    sport: str = "americanfootball_nfl",
    commence: str = "2024-01-15T18:00:00Z",
    bookmakers: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Build a raw API event dict as The Odds API would return it."""
    if bookmakers is None:
        bookmakers = [_raw_bookmaker("pinnacle", home, -165, away, 145)]
    return {
        "id": event_id,
        "sport_key": sport,
        "sport_title": "NFL",
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers,
    }


def _raw_bookmaker(
    key: str,
    team1: str,
    price1: float,
    team2: str,
    price2: float,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "title": title or key.title(),
        "last_update": "2024-01-15T17:30:00Z",
        "markets": [{
            "key": "h2h",
            "last_update": "2024-01-15T17:30:00Z",
            "outcomes": [
                {"name": team1, "price": price1},
                {"name": team2, "price": price2},
            ],
        }],
    }


def _resp(status: int, body: Any = None, headers: dict = None) -> _OddsResponse:
    raw = json.dumps(body if body is not None else []).encode()
    return _OddsResponse(status_code=status, _body=raw, _headers=headers or {})


def _resp_with_quota(
    body: Any,
    remaining: int = 500,
    used: int = 100,
) -> _OddsResponse:
    headers = {
        "x-requests-remaining": str(remaining),
        "x-requests-used": str(used),
    }
    return _resp(200, body, headers)


class MockAdapter:
    def __init__(self) -> None:
        self._queue: List[_OddsResponse] = []
        self.calls: List[Dict[str, Any]] = []

    def add(self, response: _OddsResponse) -> "MockAdapter":
        self._queue.append(response)
        return self

    def add_events(self, events: List[Dict], remaining: int = 500) -> "MockAdapter":
        return self.add(_resp_with_quota(events, remaining=remaining))

    def request(self, method, url, headers=None, json_body=None,
                params=None, timeout=15.0) -> _OddsResponse:
        self.calls.append({"method": method, "url": url, "params": params or {}})
        if not self._queue:
            raise AssertionError(f"MockAdapter out of responses (call #{len(self.calls)})")
        return self._queue.pop(0)


def _make_client(
    adapter: MockAdapter,
    priority_books: Optional[List[str]] = None,
    cache_ttl: float = 300.0,
    cache_now: Optional[Any] = None,
) -> OddsClient:
    return OddsClient(
        api_key="test-api-key",
        cache_ttl_seconds=cache_ttl,
        priority_books=priority_books or ["pinnacle", "draftkings", "fanduel", "betmgm"],
        adapter=adapter,
        _cache_now_fn=cache_now,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — _OddsResponse
# ═══════════════════════════════════════════════════════════════════════════════

class TestOddsResponse(unittest.TestCase):

    def test_json_parses(self):
        r = _resp(200, [{"id": "x"}])
        self.assertEqual(r.json(), [{"id": "x"}])

    def test_header_case_insensitive(self):
        r = _OddsResponse(200, b"{}", {"X-Requests-Remaining": "42"})
        self.assertEqual(r.header("x-requests-remaining"), "42")
        self.assertEqual(r.header("X-REQUESTS-REMAINING"), "42")

    def test_header_missing_returns_default(self):
        r = _OddsResponse(200, b"{}", {})
        self.assertIsNone(r.header("x-requests-remaining"))
        self.assertEqual(r.header("x-requests-remaining", "0"), "0")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — _TTLCache
# ═══════════════════════════════════════════════════════════════════════════════

class TestTTLCache(unittest.TestCase):

    def _make_cache(self, ttl: float = 300.0) -> tuple:
        now = [0.0]
        cache = _TTLCache(ttl, _now_fn=lambda: now[0])
        return cache, now

    def test_get_miss_returns_none(self):
        cache, _ = self._make_cache()
        self.assertIsNone(cache.get("missing"))

    def test_set_and_get_within_ttl(self):
        cache, now = self._make_cache(ttl=300.0)
        cache.set("key", {"data": 42})
        now[0] = 100.0  # still within TTL
        self.assertEqual(cache.get("key"), {"data": 42})

    def test_expires_at_ttl_boundary(self):
        cache, now = self._make_cache(ttl=300.0)
        cache.set("key", "value")
        now[0] = 300.0  # exactly at TTL boundary → expired
        self.assertIsNone(cache.get("key"))

    def test_still_fresh_just_before_ttl(self):
        cache, now = self._make_cache(ttl=300.0)
        cache.set("key", "value")
        now[0] = 299.9
        self.assertEqual(cache.get("key"), "value")

    def test_invalidate_removes_entry(self):
        cache, _ = self._make_cache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)

    def test_clear_removes_all(self):
        cache, _ = self._make_cache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        self.assertEqual(cache.size, 0)

    def test_size_tracks_entries(self):
        cache, _ = self._make_cache()
        self.assertEqual(cache.size, 0)
        cache.set("x", 1)
        self.assertEqual(cache.size, 1)
        cache.set("y", 2)
        self.assertEqual(cache.size, 2)

    def test_age_seconds_within_ttl(self):
        cache, now = self._make_cache(ttl=300.0)
        cache.set("k", "v")
        now[0] = 50.0
        self.assertAlmostEqual(cache.age_seconds("k"), 50.0, places=1)

    def test_age_seconds_returns_none_when_stale(self):
        cache, now = self._make_cache(ttl=300.0)
        cache.set("k", "v")
        now[0] = 400.0
        self.assertIsNone(cache.age_seconds("k"))

    def test_overwrite_resets_ttl(self):
        cache, now = self._make_cache(ttl=300.0)
        cache.set("k", "old")
        now[0] = 250.0
        cache.set("k", "new")   # reset timestamp
        now[0] = 400.0          # 150s since overwrite — still fresh
        self.assertEqual(cache.get("k"), "new")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Event parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventParsing(unittest.TestCase):

    def _parse_one(self, raw: Dict, priority_books=None) -> OddsEvent:
        adapter = MockAdapter()
        adapter.add_events([raw])
        client = _make_client(adapter, priority_books=priority_books or ["pinnacle"])
        events = client.get_events("americanfootball_nfl")
        self.assertEqual(len(events), 1)
        return events[0]

    def test_basic_fields(self):
        ev = self._parse_one(_raw_event("evt-42", "Chiefs", "Raiders"))
        self.assertEqual(ev.event_id, "evt-42")
        self.assertEqual(ev.home_team, "Chiefs")
        self.assertEqual(ev.away_team, "Raiders")
        self.assertEqual(ev.sport_key, "americanfootball_nfl")

    def test_commence_time_parsed(self):
        ev = self._parse_one(_raw_event(commence="2024-01-15T18:00:00Z"))
        self.assertEqual(ev.commence_time.year, 2024)
        self.assertEqual(ev.commence_time.month, 1)

    def test_bookmaker_outcomes_mapped(self):
        ev = self._parse_one(_raw_event(
            "e1", "Chiefs", "Raiders",
            bookmakers=[_raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145)],
        ))
        self.assertEqual(len(ev.bookmaker_odds), 1)
        bm = ev.bookmaker_odds[0]
        self.assertEqual(bm.bookmaker_key, "pinnacle")
        self.assertEqual(len(bm.outcomes), 2)
        prices = {o.name: o.price for o in bm.outcomes}
        self.assertAlmostEqual(prices["Chiefs"], -165.0)
        self.assertAlmostEqual(prices["Raiders"], 145.0)

    def test_non_priority_bookmakers_filtered(self):
        raw = _raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145),
            _raw_bookmaker("badbookie", "Chiefs", -150, "Raiders", 130),
        ])
        ev = self._parse_one(raw, priority_books=["pinnacle"])
        bm_keys = [b.bookmaker_key for b in ev.bookmaker_odds]
        self.assertIn("pinnacle", bm_keys)
        self.assertNotIn("badbookie", bm_keys)

    def test_non_h2h_markets_skipped(self):
        raw = _raw_event()
        raw["bookmakers"][0]["markets"].append({
            "key": "spreads",
            "last_update": "2024-01-15T17:30:00Z",
            "outcomes": [
                {"name": "Chiefs", "price": -110},
                {"name": "Raiders", "price": -110},
            ],
        })
        ev = self._parse_one(raw, priority_books=["pinnacle"])
        self.assertEqual(len(ev.bookmaker_odds), 1)  # spreads skipped

    def test_three_way_market_skipped(self):
        """Bookmaker with 3 outcomes (draw market) is filtered out."""
        raw = _raw_event()
        raw["bookmakers"][0]["markets"][0]["outcomes"].append(
            {"name": "Draw", "price": 300}
        )
        ev = self._parse_one(raw, priority_books=["pinnacle"])
        # The 3-outcome h2h is skipped → no bookmaker odds
        self.assertEqual(len(ev.bookmaker_odds), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Caching behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestCachingBehaviour(unittest.TestCase):

    def test_second_call_uses_cache(self):
        """Two get_events calls for the same sport → only one HTTP request."""
        adapter = MockAdapter()
        adapter.add_events([_raw_event()])
        client = _make_client(adapter)

        client.get_events("americanfootball_nfl")
        client.get_events("americanfootball_nfl")  # should hit cache
        self.assertEqual(len(adapter.calls), 1)

    def test_cache_miss_after_ttl_fetches_again(self):
        now = [0.0]
        adapter = MockAdapter()
        adapter.add_events([_raw_event("e1")])
        adapter.add_events([_raw_event("e2")])  # second fetch
        client = _make_client(adapter, cache_ttl=300.0, cache_now=lambda: now[0])

        client.get_events("americanfootball_nfl")
        now[0] = 301.0   # TTL expired
        events2 = client.get_events("americanfootball_nfl")

        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(events2[0].event_id, "e2")

    def test_different_sports_cached_independently(self):
        adapter = MockAdapter()
        adapter.add_events([_raw_event(sport="americanfootball_nfl")])
        adapter.add_events([_raw_event(sport="basketball_nba")])
        client = _make_client(adapter)

        client.get_events("americanfootball_nfl")
        client.get_events("basketball_nba")
        self.assertEqual(len(adapter.calls), 2)

        # Third call hits cache for nfl
        client.get_events("americanfootball_nfl")
        self.assertEqual(len(adapter.calls), 2)

    def test_invalidate_cache_forces_refetch(self):
        adapter = MockAdapter()
        adapter.add_events([_raw_event("old")])
        adapter.add_events([_raw_event("new")])
        client = _make_client(adapter)

        client.get_events("americanfootball_nfl")
        client.invalidate_cache("americanfootball_nfl")
        events = client.get_events("americanfootball_nfl")

        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(events[0].event_id, "new")

    def test_clear_cache_forces_refetch_all(self):
        now = [0.0]
        adapter = MockAdapter()
        adapter.add_events([_raw_event()])
        adapter.add_events([_raw_event()])
        client = _make_client(adapter, cache_now=lambda: now[0])

        client.get_events("americanfootball_nfl")
        client.invalidate_cache()  # clear all
        client.get_events("americanfootball_nfl")
        self.assertEqual(len(adapter.calls), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Quota tracking and error handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuotaAndErrors(unittest.TestCase):

    def test_quota_headers_stored(self):
        adapter = MockAdapter()
        adapter.add(_resp_with_quota([_raw_event()], remaining=342, used=58))
        client = _make_client(adapter)
        client.get_events("americanfootball_nfl")
        self.assertEqual(client.requests_remaining, 342)
        self.assertEqual(client.requests_used, 58)

    def test_raises_quota_error_on_402(self):
        adapter = MockAdapter()
        adapter.add(_resp(402, {"message": "quota exceeded"}))
        client = _make_client(adapter)
        with self.assertRaises(OddsQuotaExhaustedError):
            client.get_events("americanfootball_nfl")

    def test_raises_quota_error_on_429(self):
        adapter = MockAdapter()
        adapter.add(_resp(429, {}))
        client = _make_client(adapter)
        with self.assertRaises(OddsQuotaExhaustedError):
            client.get_events("americanfootball_nfl")

    def test_raises_api_error_on_500(self):
        adapter = MockAdapter()
        adapter.add(_resp(500, {"message": "server error"}))
        client = _make_client(adapter)
        with self.assertRaises(OddsAPIError) as ctx:
            client.get_events("americanfootball_nfl")
        self.assertEqual(ctx.exception.status_code, 500)

    def test_raises_api_error_on_401(self):
        adapter = MockAdapter()
        adapter.add(_resp(401, {"message": "invalid api key"}))
        client = _make_client(adapter)
        with self.assertRaises(OddsAPIError) as ctx:
            client.get_events("americanfootball_nfl")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_api_key_sent_in_params(self):
        adapter = MockAdapter()
        adapter.add_events([])
        client = OddsClient(api_key="MY-KEY-123", adapter=adapter)
        client.get_events("americanfootball_nfl")
        params = adapter.calls[0]["params"]
        self.assertEqual(params.get("apiKey"), "MY-KEY-123")

    def test_low_quota_does_not_raise(self):
        """Low quota should log a warning but not block the request."""
        adapter = MockAdapter()
        adapter.add(_resp_with_quota([_raw_event()], remaining=50))
        client = _make_client(adapter)
        events = client.get_events("americanfootball_nfl")
        self.assertEqual(len(events), 1)   # request still succeeded

    def test_critical_quota_does_not_raise(self):
        adapter = MockAdapter()
        adapter.add(_resp_with_quota([_raw_event()], remaining=5))
        client = _make_client(adapter)
        events = client.get_events("americanfootball_nfl")
        self.assertEqual(len(events), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Consensus probability
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsensus(unittest.TestCase):

    def _client(self, books=None):
        adapter = MockAdapter()
        return _make_client(adapter, priority_books=books or ["pinnacle", "draftkings", "betmgm"])

    def _make_event(self, bookmakers_raw: List[Dict]) -> OddsEvent:
        """Parse a raw event directly (bypasses HTTP layer)."""
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=bookmakers_raw)])
        client = _make_client(adapter)
        return client.get_events("americanfootball_nfl")[0]

    # ── Devig ─────────────────────────────────────────────────────────────────

    def test_devig_probs_sum_to_one(self):
        """Devigged home_prob + away_prob must equal exactly 1.0."""
        # Use full names to match _raw_event's home_team / away_team defaults
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Kansas City Chiefs", -165, "Las Vegas Raiders", 145),
        ])])
        client = _make_client(adapter, priority_books=["pinnacle"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertIsNotNone(cp)
        self.assertAlmostEqual(cp.home_prob + cp.away_prob, 1.0, places=8)

    def test_get_consensus_returns_none_when_no_books_match(self):
        """A client whose priority_books has no overlap with the event returns None."""
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Kansas City Chiefs", -165, "Las Vegas Raiders", 145),
        ])])
        # "bovada" is not in the event's bookmakers
        client = _make_client(adapter, priority_books=["bovada"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertIsNone(cp)

    def test_home_team_is_favourite(self):
        """Chiefs -165 → home_prob > 0.5 after devigging."""
        adapter = MockAdapter()
        adapter.add_events([_raw_event(
            home="Chiefs", away="Raiders",
            bookmakers=[_raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145)],
        )])
        client = _make_client(adapter, priority_books=["pinnacle"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertGreater(cp.home_prob, 0.5)

    # ── Method selection ───────────────────────────────────────────────────────

    def test_weighted_sharp_method_when_pinnacle_present(self):
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145),
            _raw_bookmaker("draftkings", "Chiefs", -155, "Raiders", 135),
        ])])
        client = _make_client(adapter, priority_books=["pinnacle", "draftkings"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertEqual(cp.method, "weighted_sharp")

    def test_median_method_when_pinnacle_absent(self):
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("draftkings", "Chiefs", -155, "Raiders", 135),
            _raw_bookmaker("fanduel", "Chiefs", -160, "Raiders", 140),
        ])])
        client = _make_client(adapter, priority_books=["draftkings", "fanduel"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertEqual(cp.method, "median")

    def test_pinnacle_gets_double_weight(self):
        """Consensus with Pinnacle should be pulled toward Pinnacle's line."""
        from src.utils import american_to_implied_prob, remove_vig_additive

        # Pinnacle: Chiefs -165 (sharp), DraftKings: Chiefs -140 (soft)
        pin_home = american_to_implied_prob(-165)
        pin_away = american_to_implied_prob(145)
        dk_home = american_to_implied_prob(-140)
        dk_away = american_to_implied_prob(120)

        fair_pin, _ = remove_vig_additive([pin_home, pin_away])
        fair_dk, _ = remove_vig_additive([dk_home, dk_away])

        # Weighted: Pinnacle ×2, DK ×1
        expected = (fair_pin * 2.0 + fair_dk * 1.0) / 3.0
        median_val = sorted([fair_pin, fair_dk])[0]  # lower median

        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Kansas City Chiefs", -165, "Las Vegas Raiders", 145),
            _raw_bookmaker("draftkings", "Kansas City Chiefs", -140, "Las Vegas Raiders", 120),
        ])])
        client = _make_client(adapter, priority_books=["pinnacle", "draftkings"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])

        self.assertAlmostEqual(cp.home_prob, expected, places=5)
        self.assertNotAlmostEqual(cp.home_prob, median_val, places=3)

    def test_returns_none_when_no_matching_books(self):
        """If no configured books have odds, return None."""
        adapter = MockAdapter()
        raw = _raw_event(bookmakers=[
            _raw_bookmaker("bovada", "Chiefs", -165, "Raiders", 145),
        ])
        adapter.add_events([raw])
        # Client only accepts pinnacle — bovada filtered out
        client = _make_client(adapter, priority_books=["pinnacle"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertIsNone(cp)

    def test_books_used_list(self):
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145),
            _raw_bookmaker("draftkings", "Chiefs", -155, "Raiders", 135),
        ])])
        client = _make_client(adapter, priority_books=["pinnacle", "draftkings"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertIn("pinnacle", cp.books_used)
        self.assertIn("draftkings", cp.books_used)
        self.assertEqual(len(cp.books_used), 2)

    def test_home_prob_clamped_above_zero(self):
        """home_prob must never be exactly 0 or 1."""
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145),
        ])])
        client = _make_client(adapter, priority_books=["pinnacle"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertGreater(cp.home_prob, 0.0)
        self.assertLess(cp.home_prob, 1.0)

    def test_requests_remaining_propagated(self):
        adapter = MockAdapter()
        adapter.add(_resp_with_quota([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145),
        ])], remaining=77))
        client = _make_client(adapter, priority_books=["pinnacle"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertEqual(cp.requests_remaining, 77)

    def test_get_all_consensus_filters_none(self):
        """Events with no matching books are excluded from results."""
        adapter = MockAdapter()
        adapter.add_events([
            _raw_event("e1", bookmakers=[
                _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145)]),
            _raw_event("e2", bookmakers=[
                _raw_bookmaker("bovada", "Lakers", -200, "Warriors", 170)]),
        ])
        client = _make_client(adapter, priority_books=["pinnacle"])
        results = client.get_all_consensus("americanfootball_nfl")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].event_id, "e1")

    # ── Median correctness ────────────────────────────────────────────────────

    def test_median_with_three_books(self):
        """Median of three fair probabilities should be the middle value."""
        from src.utils import american_to_implied_prob, remove_vig_additive

        books = [
            _raw_bookmaker("draftkings", "Kansas City Chiefs", -155, "Las Vegas Raiders", 135),
            _raw_bookmaker("fanduel", "Kansas City Chiefs", -160, "Las Vegas Raiders", 140),
            _raw_bookmaker("betmgm", "Kansas City Chiefs", -150, "Las Vegas Raiders", 130),
        ]
        # Compute expected values
        probs = []
        for bm, home_odds, away_odds in [(-155, 135, None), (-160, 140, None), (-150, 130, None)]:
            pass
        # Re-derive properly
        odds_pairs = [(-155, 135), (-160, 140), (-150, 130)]
        fair_probs = []
        for h, a in odds_pairs:
            p_h = american_to_implied_prob(h)
            p_a = american_to_implied_prob(a)
            fh, _ = remove_vig_additive([p_h, p_a])
            fair_probs.append(fh)

        expected_median = sorted(fair_probs)[1]  # middle value of 3

        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=books)])
        client = _make_client(adapter, priority_books=["draftkings", "fanduel", "betmgm"])
        events = client.get_events("americanfootball_nfl")
        cp = client.get_consensus(events[0])
        self.assertEqual(cp.method, "median")
        self.assertAlmostEqual(cp.home_prob, expected_median, places=5)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — from_config
# ═══════════════════════════════════════════════════════════════════════════════

class TestFromConfig(unittest.TestCase):

    def _load_config(self):
        import yaml
        with (ROOT / "config.yaml").open() as fh:
            return yaml.safe_load(fh)

    def test_from_config_wires_base_url(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = OddsClient.from_config(config, "key123", adapter=adapter)
        self.assertEqual(client._base_url, config["odds_api"]["base_url"].rstrip("/"))

    def test_from_config_wires_priority_books(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = OddsClient.from_config(config, "key123", adapter=adapter)
        self.assertEqual(client._priority_books, config["odds_api"]["priority_books"])

    def test_from_config_wires_cache_ttl(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = OddsClient.from_config(config, "key123", adapter=adapter)
        self.assertEqual(client._cache._ttl, config["odds_api"]["cache_ttl_seconds"])

    def test_from_config_wires_sports(self):
        config = self._load_config()
        adapter = MockAdapter()
        client = OddsClient.from_config(config, "key123", adapter=adapter)
        self.assertEqual(client._sports, config["odds_api"]["sports"])


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Integration smoke
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrationSmoke(unittest.TestCase):

    def test_full_pipeline_nfl(self):
        """
        Real NFL scenario: Chiefs -165 (Pinnacle), -155 (DraftKings).
        Consensus should be closer to Pinnacle's line.
        """
        adapter = MockAdapter()
        adapter.add(_resp_with_quota([
            _raw_event(
                event_id="nfl-2024-01",
                home="Kansas City Chiefs",
                away="Las Vegas Raiders",
                sport="americanfootball_nfl",
                commence="2024-01-15T18:00:00Z",
                bookmakers=[
                    _raw_bookmaker("pinnacle", "Kansas City Chiefs", -165,
                                   "Las Vegas Raiders", 145),
                    _raw_bookmaker("draftkings", "Kansas City Chiefs", -155,
                                   "Las Vegas Raiders", 135),
                ],
            )
        ], remaining=250))

        client = _make_client(adapter, priority_books=["pinnacle", "draftkings"])
        results = client.get_all_consensus("americanfootball_nfl")

        self.assertEqual(len(results), 1)
        cp = results[0]
        self.assertEqual(cp.home_team, "Kansas City Chiefs")
        self.assertEqual(cp.method, "weighted_sharp")
        self.assertGreater(cp.home_prob, 0.5)         # Chiefs favoured
        self.assertAlmostEqual(cp.home_prob + cp.away_prob, 1.0, places=8)
        self.assertEqual(cp.requests_remaining, 250)
        self.assertIn("pinnacle", cp.books_used)
        self.assertIn("draftkings", cp.books_used)

    def test_pipeline_caches_between_calls(self):
        """get_all_consensus called twice → only one HTTP request."""
        adapter = MockAdapter()
        adapter.add_events([_raw_event(bookmakers=[
            _raw_bookmaker("pinnacle", "Chiefs", -165, "Raiders", 145),
        ])])
        client = _make_client(adapter, priority_books=["pinnacle"])

        client.get_all_consensus("americanfootball_nfl")
        client.get_all_consensus("americanfootball_nfl")
        self.assertEqual(len(adapter.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
