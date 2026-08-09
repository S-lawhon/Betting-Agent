from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from src.research_evidence import (
    ArxivEvidenceResolver,
    CFTCFilingEvidenceResolver,
    KalshiEvidenceResolver,
    SocialEvidenceResolver,
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
        def get(self, url, timeout=None, headers=None, params=None):
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
    def test_uncovered_source_types_get_an_explicit_not_applicable_pack(self):
        # practitioner and official_data have no resolver yet; the pack says
        # so rather than silently looking like a measurement that found
        # nothing.
        for source_type in ("practitioner", "official_data"):
            pack = resolve_evidence(
                {"source_type": source_type}, {"source_type": source_type},
                build_resolvers(True), now=NOW)
            self.assertEqual(pack["status"], "not_applicable", source_type)
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


# Shaped after the live response for 2608.00647: the author comment is where
# arXiv puts "no live venue data" and any code link.
ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2608.00647v3</id>
    <updated>2026-08-06T00:00:00Z</updated>
    <published>2026-08-04T00:00:00Z</published>
    <title>Axient: On-Chain Credit for Leveraged Event Markets</title>
    <summary>  A physically backed leveraged event position requires
      real credit. Code: https://github.com/example/axient  </summary>
    <arxiv:comment>90 pages. Fixed-seed synthetic validation; no live venue
      data</arxiv:comment>
    <arxiv:journal_ref>J. Fin. Mkts 12 (2026) 1</arxiv:journal_ref>
    <arxiv:doi>10.1000/example</arxiv:doi>
    <category term="q-fin.TR"/>
    <category term="q-fin.RM"/>
  </entry>
</feed>"""


class TestArxivEvidence(TestCase):
    def _resolver(self, body=ARXIV_ATOM):
        return ArxivEvidenceResolver(_page(body), sleep=lambda _s: None)

    def _paper(self, **changes):
        payload = {
            "source_type": "paper", "source_name": "arXiv q-fin.TR",
            "external_id": "oai:arXiv.org:2608.00647v1",
            "url": "https://arxiv.org/abs/2608.00647",
        }
        payload.update(changes)
        return payload

    def test_identifier_is_read_from_either_field(self):
        self.assertEqual(
            ArxivEvidenceResolver.arxiv_id(
                {"external_id": "oai:arXiv.org:2512.22476v2"}),
            ("2512.22476", 2))
        self.assertEqual(
            ArxivEvidenceResolver.arxiv_id(
                {"url": "https://arxiv.org/abs/2608.00647"}),
            ("2608.00647", 0))
        self.assertEqual(ArxivEvidenceResolver.arxiv_id({"url": "x"}), ("", 0))

    def test_measures_version_artifact_and_full_text(self):
        pack = self._resolver().resolve(
            {"source_type": "paper"}, self._paper(), now=NOW)
        facts = pack["facts"]
        self.assertEqual(pack["status"], "measured")
        self.assertEqual(facts["arxiv_id"], "2608.00647")
        self.assertEqual(facts["pdf_url"], "https://arxiv.org/pdf/2608.00647")
        self.assertEqual(facts["categories"], ["q-fin.RM", "q-fin.TR"])
        self.assertEqual(facts["journal_ref"], "J. Fin. Mkts 12 (2026) 1")
        self.assertEqual(facts["doi"], "10.1000/example")
        self.assertIn("no live venue data", facts["author_comment"])
        self.assertEqual(facts["code_links"],
                         ["https://github.com/example/axient"])
        self.assertIn("replication artifact is linked", " ".join(pack["notes"]))

    def test_a_superseded_version_is_called_out(self):
        pack = self._resolver().resolve(
            {"source_type": "paper"}, self._paper(), now=NOW)
        # Ingested at v1, arXiv is now at v3.
        self.assertEqual(pack["facts"]["ingested_version"], 1)
        self.assertEqual(pack["facts"]["latest_version"], 3)
        self.assertIn("now at v3", " ".join(pack["notes"]))

    def test_a_paper_without_code_says_the_test_must_be_rebuilt(self):
        body = ARXIV_ATOM.replace(
            "Code: https://github.com/example/axient", "No code.")
        pack = self._resolver(body).resolve(
            {"source_type": "paper"}, self._paper(), now=NOW)
        self.assertEqual(pack["facts"]["code_links"], [])
        self.assertIn("must be rebuilt", " ".join(pack["notes"]))

    def test_an_unreachable_api_still_yields_the_full_text_location(self):
        class Dead:
            def get(self, *a, **k):
                raise OSError("read timed out")

        pack = ArxivEvidenceResolver(Dead(), sleep=lambda _s: None).resolve(
            {"source_type": "paper"}, self._paper(), now=NOW)
        self.assertEqual(pack["status"], "partial")
        self.assertEqual(pack["facts"]["pdf_url"],
                         "https://arxiv.org/pdf/2608.00647")
        self.assertNotIn("abstract", pack["facts"])

    def test_applies_only_to_papers_carrying_an_arxiv_identifier(self):
        resolver = self._resolver()
        self.assertTrue(resolver.applies_to({"source_type": "paper"},
                                            self._paper()))
        self.assertFalse(resolver.applies_to(
            {"source_type": "paper"},
            self._paper(external_id="ssrn-1", url="https://ssrn.test/1")))
        self.assertFalse(resolver.applies_to({"source_type": "social"},
                                             self._paper()))

    def test_requests_are_spaced_as_arxiv_asks(self):
        slept = []
        resolver = ArxivEvidenceResolver(_page(ARXIV_ATOM),
                                         sleep=slept.append)
        resolver.resolve({"source_type": "paper"}, self._paper(), now=NOW)
        self.assertEqual(slept, [])          # nothing to wait for on the first
        resolver.resolve({"source_type": "paper"}, self._paper(), now=NOW)
        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 0)


class _Registry:
    def __init__(self, rows=None, raises=False):
        self.rows = rows or {}
        self.raises = raises

    def decision(self, venue_id, product_family, as_of=None):
        if self.raises:
            raise RuntimeError("registry unreadable")
        from types import SimpleNamespace

        row = self.rows.get(venue_id) or {}
        return SimpleNamespace(
            status=row.get("status", "pending_review"),
            research_allowed=row.get("research_allowed", True),
            execution_allowed=row.get("execution_allowed", False),
            stale=row.get("stale", False))


def _post(text, **changes):
    payload = {
        "source_type": "social", "source_name": "X", "title": text[:180],
        "summary": text, "url": "https://x.com/u/status/1",
        "metadata": {"username": "u", "metrics": {"impression_count": 2}},
    }
    payload.update(changes)
    return payload


class TestSocialEvidence(TestCase):
    def test_named_registered_venue_carries_its_eligibility_decision(self):
        resolver = SocialEvidenceResolver(_Registry({
            "kalshi": {"status": "reference_only", "research_allowed": True,
                       "execution_allowed": False}}))
        pack = resolver.resolve({"source_type": "social"}, _post(
            "Kalshi settlement fees look mispriced on this series"), now=NOW)
        facts = pack["facts"]
        self.assertEqual(facts["registered_venues_named"], ["kalshi"])
        self.assertEqual(facts["venue_decisions"], [{
            "venue_id": "kalshi", "status": "reference_only",
            "research_allowed": True, "execution_allowed": False,
            "stale": False}])

    def test_a_post_naming_no_registered_venue_says_so(self):
        pack = SocialEvidenceResolver(_Registry()).resolve(
            {"source_type": "social"},
            _post("YeNo is a Solana prediction market. Check it @YeNoMarkets "
                  "https://t.co/abc"), now=NOW)
        facts = pack["facts"]
        self.assertEqual(facts["registered_venues_named"], [])
        self.assertIn("names no venue in the research registry",
                      " ".join(pack["notes"]))
        # Promotional markers are listed, never scored into a verdict.
        self.assertEqual(facts["promotional_markers"], [
            "contains_outbound_link", "mentions_other_account",
            "call_to_action"])
        self.assertEqual(facts["mentioned_accounts"], ["YeNoMarkets"])
        self.assertIn("t.co is a shortener", " ".join(pack["notes"]))

    def test_unqualified_polymarket_is_flagged_never_guessed(self):
        # polymarket_us is research-allowed; polymarket_international is
        # prohibited. Resolving the bare word to either would be a guess.
        pack = SocialEvidenceResolver(_Registry()).resolve(
            {"source_type": "social"},
            _post("Polymarket odds moved before the news broke"), now=NOW)
        self.assertEqual(pack["facts"]["registered_venues_named"], [])
        self.assertEqual(pack["facts"]["ambiguous_venue_terms"], ["polymarket"])
        self.assertIn("US/international qualifier", " ".join(pack["notes"]))

    def test_a_qualified_polymarket_mention_resolves(self):
        pack = SocialEvidenceResolver(_Registry()).resolve(
            {"source_type": "social"},
            _post("Polymarket US listed a new weather series"), now=NOW)
        self.assertEqual(pack["facts"]["registered_venues_named"],
                         ["polymarket_us"])
        self.assertEqual(pack["facts"]["ambiguous_venue_terms"], [])

    def test_metrics_ship_with_an_explicit_not_edge_evidence_warning(self):
        pack = SocialEvidenceResolver(_Registry()).resolve(
            {"source_type": "social"}, _post("Kalshi spread"), now=NOW)
        self.assertEqual(pack["facts"]["public_metrics"],
                         {"impression_count": 2})
        self.assertIn("never whether", pack["facts"]["metrics_note"])
        self.assertIs(pack["semantics"]["is_edge_evidence"], False)

    def test_the_author_is_not_reported_as_a_mentioned_account(self):
        pack = SocialEvidenceResolver(_Registry()).resolve(
            {"source_type": "social"},
            _post("@u thinks @other is wrong about Kalshi",
                  metadata={"username": "u", "metrics": {}}), now=NOW)
        self.assertEqual(pack["facts"]["mentioned_accounts"], ["other"])

    def test_an_unreadable_registry_does_not_break_the_pack(self):
        pack = SocialEvidenceResolver(_Registry(raises=True)).resolve(
            {"source_type": "social"}, _post("Kalshi fees"), now=NOW)
        self.assertEqual(pack["status"], "measured")
        self.assertEqual(pack["facts"]["registered_venues_named"], ["kalshi"])
        self.assertEqual(pack["facts"]["venue_decisions"], [])

    def test_social_makes_no_network_call(self):
        class Boom:
            def get(self, *a, **k):
                raise AssertionError("social evidence must not fetch")

        resolver = SocialEvidenceResolver(_Registry())
        resolver._session = Boom()
        pack = resolver.resolve({"source_type": "social"},
                                _post("Kalshi fees"), now=NOW)
        self.assertEqual(pack["status"], "measured")
