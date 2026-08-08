"""Bounded-memory regressions for the P-029 discovery loop."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from combo_research import shadow_public


class FakePublic:
    def __init__(self):
        self.calls = 0

    def get(self, path, params=None):
        self.calls += 1
        return {"market": {
            "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42",
            "status": "open", "volume": 1, "open_interest": 1,
        }}


class DiscoveryPublic:
    def __init__(self):
        self.market_pages = 0

    def get(self, path, params=None):
        assert path == "/markets"
        self.market_pages += 1
        if self.market_pages > 1:
            return {"markets": []}
        return {"markets": [
            {"ticker": "EXISTING", "mve_selected_legs": [
                {"market_ticker": "L1", "side": "yes"},
                {"market_ticker": "L2", "side": "yes"}]},
            {"ticker": "NEW", "mve_selected_legs": [
                {"market_ticker": "L1", "side": "yes"},
                {"market_ticker": "L2", "side": "yes"}]},
        ]}


class DiscoveryPricer:
    def __init__(self):
        self.pruned = 0

    def prune(self):
        self.pruned += 1

    def price_combo(self, legs):
        return 0.20, 0.22, [{"ticker": ticker} for ticker, _ in legs], "cross"


def test_existing_tickers_batches_primary_key_membership_queries():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE combo (market_ticker TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO combo VALUES (?)", ((f"T{i}",) for i in range(750)))
    statements = []
    con.set_trace_callback(statements.append)

    existing = shadow_public._existing_tickers(
        con, [f"T{i}" for i in range(1000)])

    assert existing == {f"T{i}" for i in range(750)}
    selects = [statement for statement in statements
               if statement.startswith("SELECT market_ticker")]
    assert len(selects) == 2
    assert all("WHERE market_ticker IN" in statement for statement in selects)
    assert all(statement != "SELECT market_ticker FROM combo" for statement in selects)


def test_discover_never_executes_an_unbounded_ticker_scan():
    con = shadow_public.open_db(":memory:")
    con.execute("INSERT INTO combo (market_ticker) VALUES ('EXISTING')")
    statements = []
    con.set_trace_callback(statements.append)
    pricer = DiscoveryPricer()

    added = shadow_public.discover(con, DiscoveryPublic(), pricer)

    assert added == 1
    assert pricer.pruned == 1
    assert con.execute(
        "SELECT COUNT(*) FROM combo WHERE market_ticker='NEW'").fetchone()[0] == 1
    ticker_selects = [statement for statement in statements
                      if statement.startswith("SELECT market_ticker FROM combo")]
    assert ticker_selects
    assert all("WHERE market_ticker IN" in statement for statement in ticker_selects)


def test_leg_cache_expires_and_never_exceeds_hard_bound():
    pub = FakePublic()
    pricer = shadow_public.LegPricer(pub)

    with patch.object(shadow_public, "LEG_CACHE_MAX", 3), \
            patch.object(shadow_public.time, "monotonic", return_value=10.0):
        for ticker in ("A", "B", "C", "D"):
            assert pricer.mark(ticker)
        assert list(pricer._cache) == ["B", "C", "D"]

    with patch.object(shadow_public.time, "monotonic", return_value=31.0):
        assert pricer.prune() == 3
        assert not pricer._cache


def test_public_error_history_is_bounded():
    public = shadow_public.Public()
    for i in range(shadow_public.ERROR_HISTORY_MAX + 5):
        public.errors.append((f"/{i}", 400))

    assert len(public.errors) == shadow_public.ERROR_HISTORY_MAX
    assert public.errors[0] == ("/5", 400)
