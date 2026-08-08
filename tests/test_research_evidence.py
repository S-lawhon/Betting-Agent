from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from src.research_evidence import (
    CFTCFilingEvidenceResolver,
    KalshiEvidenceResolver,
    build_resolvers,
    resolve_evidence,
    supplied_evidence_keys,
)


NOW = datetime(2026, 8, 8, 20, 0, tzinfo=timezone.utc)


class FakeKalshi:
    """Stands in for KalshiPublic; records every path it was asked for."""

    def __init__(self, markets=None, books=None, series=None, fail=None):
        self.markets = markets if markets is not None else []
        self.books = books or {}
        self.series = series
        self.fail = fail or set()
        self.calls = []

    def open_markets(self, series_ticker, max_pages=10):
        self.calls.append(("open_markets", series_ticker))
        if "open_markets" in self.fail:
            raise RuntimeError("kalshi is down")
        return self.markets

    def orderbook(self, ticker, depth=5):
        self.calls.append(("orderbook", ticker))
        if "orderbook" in self.fail:
            raise RuntimeError("kalshi is down")
        return self.books.get(ticker)

    def get(self, path, params=None):
        self.calls.append(("get", path))
        if "series" in self.fail:
            raise RuntimeError("kalshi is down")
        return self.series


def _kalshi_source(**changes):
    payload = {
        "id": "src_1", "source_type": "venue_market",
        "source_name": "Kalshi market census", "venue_ids": ["kalshi"],
        "title": "Spread", "product_family": "sports",
        "metadata": {"market_family": "KXASEANSPREAD",
                     "census_run_id": "census_1", "rank": 2},
    }
    payload.update(changes)
    return payload


def _kalshi_assignment(**changes):
    payload = {
        "id": "a1", "source_item_id": "src_1", "source_type": "venue_market",
        "venue_ids": ["kalshi"], "title": "Spread", "lane": "information_latency",
    }
    payload.update(changes)
    return payload


class TestKalshiEvidence(TestCase):
    def test_measures_fees_quote_and_contract_terms(self):
        client = FakeKalshi(
            markets=[
                {"ticker": "KXASEANSPREAD-A", "volume": 10},
                {"ticker": "KXASEANSPREAD-B", "volume": 900},
            ],
            books={
                "KXASEANSPREAD-B": {"yes_bid": 0.42, "yes_ask": 0.47,
                                    "bid_qty": 120, "ask_qty": 80},
            },
            series={"series": {"contract_terms_url": "https://kalshi.test/t.pdf",
                               "category": "Sports", "fee_type": "quadratic"}},
        )
        pack = KalshiEvidenceResolver(client).resolve(
            _kalshi_assignment(), _kalshi_source(), now=NOW)
        self.assertEqual(pack["status"], "measured")
        facts = pack["facts"]
        self.assertEqual(facts["series_ticker"], "KXASEANSPREAD")
        self.assertEqual(facts["markets"]["open_count"], 2)
        # The busiest market is quoted, not whichever sorted first.
        quote = facts["representative_quote"]
        self.assertEqual(quote["ticker"], "KXASEANSPREAD-B")
        self.assertAlmostEqual(quote["spread"], 0.05, places=6)
        self.assertAlmostEqual(quote["half_spread"], 0.025, places=6)
        self.assertAlmostEqual(quote["mid"], 0.445, places=6)
        # Round-trip friction = half-spread + taker fee at the mid.
        self.assertAlmostEqual(
            quote["round_trip_cost_at_mid_usd"],
            0.025 + 0.07 * 0.445 * (1 - 0.445), places=6)
        self.assertEqual(facts["contract_terms"]["contract_terms_url"],
                         "https://kalshi.test/t.pdf")
        self.assertEqual(pack["measured_at"], "2026-08-08T20:00:00Z")
        # An observation is never an edge or a capacity claim.
        self.assertIs(pack["semantics"]["is_edge_evidence"], False)
        self.assertIs(pack["semantics"]["capacity_measured"], False)

    def test_fee_class_is_resolved_offline_even_when_kalshi_is_down(self):
        client = FakeKalshi(fail={"open_markets"})
        pack = KalshiEvidenceResolver(client).resolve(
            _kalshi_assignment(), _kalshi_source(), now=NOW)
        self.assertEqual(pack["status"], "partial")
        fees = pack["facts"]["fees"]
        self.assertIn("maker_charges_fee", fees)
        self.assertAlmostEqual(fees["fee_at_50c_taker_usd"], 0.0175, places=6)
        self.assertIn("market data unavailable", " ".join(pack["notes"]))

    def test_empty_and_one_sided_books_are_reported_not_invented(self):
        no_markets = KalshiEvidenceResolver(FakeKalshi(markets=[])).resolve(
            _kalshi_assignment(), _kalshi_source(), now=NOW)
        self.assertEqual(no_markets["status"], "measured")
        self.assertEqual(no_markets["facts"]["markets"]["open_count"], 0)
        self.assertNotIn("representative_quote", no_markets["facts"])
        self.assertIn("no OPEN markets", " ".join(no_markets["notes"]))

        one_sided = KalshiEvidenceResolver(FakeKalshi(
            markets=[{"ticker": "T1", "volume": 5}],
            books={"T1": {"yes_bid": 0.4, "yes_ask": None,
                          "bid_qty": 10, "ask_qty": 0}},
        )).resolve(_kalshi_assignment(), _kalshi_source(), now=NOW)
        self.assertNotIn("representative_quote", one_sided["facts"])
        self.assertIn("no two-sided quote", " ".join(one_sided["notes"]))

    def test_sizeless_quote_is_not_reported_as_a_spread(self):
        client = FakeKalshi(
            markets=[{"ticker": "T1", "volume": 5}],
            books={"T1": {"yes_bid": 0.01, "yes_ask": 0.99,
                          "bid_qty": 0, "ask_qty": 0}})
        pack = KalshiEvidenceResolver(client).resolve(
            _kalshi_assignment(), _kalshi_source(), now=NOW)
        self.assertNotIn("representative_quote", pack["facts"])

    def test_only_applies_to_kalshi_records_carrying_a_series(self):
        resolver = KalshiEvidenceResolver(FakeKalshi())
        self.assertTrue(resolver.applies_to(
            _kalshi_assignment(), _kalshi_source()))
        self.assertFalse(resolver.applies_to(
            _kalshi_assignment(venue_ids=[]), _kalshi_source(venue_ids=[])))
        self.assertFalse(resolver.applies_to(
            _kalshi_assignment(), _kalshi_source(metadata={})))


def _page(html):
    class FakeResponse:
        text = html

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, timeout=None, headers=None):
            return FakeResponse()

    return FakeSession()


# Trimmed from the live page for filing 62095: two real documents wrapped in
# the site's standard .htm navigation chrome.
CFTC_PAGE = """
<a href="/Transparency/index.htm">Transparency</a>
<a href="/About/Commissioners/index.htm">Commissioners</a>
<a href="/sites/default/files/css/style.css?delta=0">css</a>
<a href="https://www.cftc.gov/filings/orgrules/rules07302612485.pdf">
  <span>2026-45 Modifications to Equity Index Perp Style Futures
  MMP_Redacted</span></a>
<a href="https://www.cftc.gov/filings/orgrules/rules07302612485.pdf">dup</a>
<a href="https://www.cftc.gov/filings/orgrules/rules07302612486.pdf">
  2026-45_FOIA Request</a>
<a href="/Contact/index.htm">Contact</a>
"""


class TestCFTCEvidence(TestCase):
    def test_navigation_chrome_is_not_mistaken_for_filing_documents(self):
        # Accepting .htm returned twelve cftc.gov nav links and zero filings.
        pack = CFTCFilingEvidenceResolver(_page(CFTC_PAGE)).resolve(
            {"source_type": "regulatory_filing"},
            {"url": "https://www.cftc.gov/filing/62095",
             "metadata": {"Documents": "2"}}, now=NOW)
        urls = [row["url"] for row in pack["facts"]["documents"]]
        self.assertEqual(urls, [
            "https://www.cftc.gov/filings/orgrules/rules07302612485.pdf",
            "https://www.cftc.gov/filings/orgrules/rules07302612486.pdf",
        ])
        self.assertNotIn(
            "https://www.cftc.gov/About/Commissioners/index.htm", urls)
        self.assertFalse([u for u in urls if u.endswith((".htm", ".css"))])

    def test_document_titles_separate_rule_text_from_attachments(self):
        pack = CFTCFilingEvidenceResolver(_page(CFTC_PAGE)).resolve(
            {"source_type": "regulatory_filing"},
            {"url": "https://www.cftc.gov/filing/62095",
             "metadata": {"Documents": "2"}}, now=NOW)
        titles = [row["title"] for row in pack["facts"]["documents"]]
        self.assertEqual(titles, [
            "2026-45 Modifications to Equity Index Perp Style Futures "
            "MMP_Redacted",
            "2026-45_FOIA Request",
        ])
        self.assertEqual(pack["status"], "measured")

    def test_count_disagreement_is_reported_not_hidden(self):
        pack = CFTCFilingEvidenceResolver(_page(CFTC_PAGE)).resolve(
            {"source_type": "regulatory_filing"},
            {"url": "https://www.cftc.gov/filing/62095",
             "metadata": {"Documents": "5"}}, now=NOW)
        self.assertIn("reports 5 documents but 2 were extracted",
                      " ".join(pack["notes"]))

    def test_a_page_with_no_documents_says_so(self):
        pack = CFTCFilingEvidenceResolver(_page("<a href='/x.htm'>x</a>")).resolve(
            {"source_type": "regulatory_filing"},
            {"url": "https://www.cftc.gov/filing/1", "metadata": {}}, now=NOW)
        self.assertEqual(pack["facts"]["documents"], [])
        self.assertIn("no document files", " ".join(pack["notes"]))

    def test_unreachable_filing_page_degrades_to_partial(self):
        class DeadSession:
            def get(self, *a, **k):
                raise OSError("no route to host")

        pack = CFTCFilingEvidenceResolver(DeadSession()).resolve(
            {"source_type": "regulatory_filing"},
            {"url": "https://www.cftc.gov/filing/62095",
             "metadata": {"Organization": "COIN"}}, now=NOW)
        self.assertEqual(pack["status"], "partial")
        self.assertEqual(pack["facts"]["organization"], "COIN")


class TestResolveEvidence(TestCase):
    def test_unknown_source_types_get_an_explicit_not_applicable_pack(self):
        pack = resolve_evidence(
            {"source_type": "social"}, {"source_type": "social"},
            build_resolvers(True), now=NOW)
        self.assertEqual(pack["status"], "not_applicable")
        self.assertEqual(supplied_evidence_keys(pack), [])

    def test_disabling_resolvers_yields_a_pack_not_an_exception(self):
        pack = resolve_evidence(
            _kalshi_assignment(), _kalshi_source(), build_resolvers(False),
            now=NOW)
        self.assertEqual(pack["status"], "not_applicable")

    def test_a_raising_resolver_never_breaks_the_pipeline(self):
        class Exploding:
            name = "boom"

            def applies_to(self, *a):
                return True

            def resolve(self, *a, **k):
                raise RuntimeError("kaboom")

        pack = resolve_evidence(
            _kalshi_assignment(), _kalshi_source(), [Exploding()], now=NOW)
        self.assertEqual(pack["status"], "unavailable")
        self.assertIn("kaboom", " ".join(pack["notes"]))

    def test_supplied_keys_list_only_what_is_present(self):
        pack = KalshiEvidenceResolver(FakeKalshi(markets=[])).resolve(
            _kalshi_assignment(), _kalshi_source(), now=NOW)
        self.assertEqual(supplied_evidence_keys(pack), ["fees", "markets",
                                                        "series_ticker"])
