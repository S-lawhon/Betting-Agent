from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest import TestCase

from src.research_eligibility import EligibilityRegistry
from src.research_intake import (
    CFTCCollector,
    FeedCollector,
    PolymarketUSCollector,
    SourceItem,
    XRecentSearchCollector,
    build_intake,
    market_census_items,
    normalize_regulatory_title,
    quality_rejection_reason,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

# The live CFTC row behind src_95f6533902b2cf2e6343, verbatim.  It was ingested
# before the collector preferred the description column, so the persisted title
# is the filer code while the mechanism text sits in the metadata.
COIN_FILING_METADATA = {
    "Date": "07/30/2026",
    "Documents": "2",
    "Filing Description": (
        "Modifications to Equity Index Perp Style Futures "
        "Market Maker Program"),
    "Organization": "COIN",
    "Receipt Date": "07/30/2026",
    "Remarks": "",
    "Status": "10 Day Review",
}


def _legacy_cftc_item(**changes):
    values = {
        "source_type": "regulatory_filing", "source_name": "CFTC",
        "external_id": ("https://www.cftc.gov/IndustryOversight/IndustryFilings"
                        "/TradingOrganizationRules/62095"),
        "title": "COIN", "summary": "COIN: COIN",
        "url": ("https://www.cftc.gov/IndustryOversight/IndustryFilings"
                "/TradingOrganizationRules/62095"),
        "published_at": "07/30/2026",
        "retrieved_at": "2026-08-02T13:46:48.274032Z",
        "topics": ["rules"], "venue_ids": [], "reliability": "primary",
        "metadata": dict(COIN_FILING_METADATA),
    }
    values.update(changes)
    return SourceItem.create(**values)


def _registry(research_allowed=True):
    return EligibilityRegistry({"venues": [{
        "id": "venue", "status": "pending_review",
        "research_allowed": research_allowed, "execution_allowed": False,
        "verified_at": "2026-08-01", "expires_at": "2026-09-01",
        "products": [],
    }]})


def _item(**changes):
    values = {
        "source_type": "venue_market", "source_name": "Venue",
        "external_id": "market-1", "title": "Will event happen?",
        "url": "https://example.test/market-1", "retrieved_at": "2026-08-01T12:00:00Z",
        "venue_ids": ["venue"], "product_family": "politics",
    }
    values.update(changes)
    return SourceItem.create(**values)


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.responses.pop(0))


class TestSourceItems(TestCase):
    def test_stable_identity_and_content_hash(self):
        a = _item()
        b = _item(retrieved_at="2026-08-02T12:00:00Z")
        self.assertEqual(a.id, b.id)
        self.assertEqual(a.content_hash, b.content_hash)

    def test_intake_deduplicates_across_runs(self):
        item = _item()
        ledger, manifest, assignments = build_intake(
            [item, item], eligibility=_registry(), now=NOW)
        self.assertEqual(manifest["new_unique"], 1)
        self.assertEqual(manifest["duplicates"], 1)
        self.assertEqual(len(assignments), 1)
        _, second, repeated = build_intake(
            [item], eligibility=_registry(), previous_ledger=ledger, now=NOW)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(repeated, [])

    def test_assignment_carries_fail_closed_legal_decision(self):
        _, _, assignments = build_intake([_item()], eligibility=_registry(), now=NOW)
        decision = assignments[0].legal_decisions[0]
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(assignments[0].may_enter_strategy_registry)
        self.assertEqual(assignments[0].source_name, "Venue")
        self.assertEqual(assignments[0].attribution_key, "Venue")

    def test_research_prohibited_venue_does_not_generate_assignment(self):
        _, manifest, assignments = build_intake(
            [_item()], eligibility=_registry(False), now=NOW)
        self.assertEqual(manifest["research_prohibited"], 1)
        self.assertEqual(assignments, [])

    def test_lane_diversity_cap(self):
        items = [_item(
            external_id=f"venue-{i}", title=f"Venue {i}",
            product_family=f"family-{i}",
        ) for i in range(20)]
        items += [_item(
            source_type="paper", source_name="arXiv", external_id=f"paper-{i}",
            title=f"Paper {i}", url=f"https://arxiv.org/abs/{i}", venue_ids=[],
        ) for i in range(5)]
        items += [_item(
            source_type="social", source_name="X", external_id=f"post-{i}",
            title=f"Prediction market fee mispricing model {i}",
            url=f"https://x.com/u/status/{i}", venue_ids=[],
        ) for i in range(5)]
        _, manifest, _ = build_intake(
            items, eligibility=_registry(), now=NOW, max_assignments=10)
        self.assertLessEqual(max(manifest["lane_counts"].values()), 4)
        self.assertGreaterEqual(len(manifest["lane_counts"]), 3)

    def test_deferred_items_remain_eligible_for_next_run(self):
        items = [_item(
            external_id=f"m-{i}", title=f"Market {i}",
            product_family=f"family-{i}",
        ) for i in range(3)]
        ledger, manifest, assignments = build_intake(
            items, eligibility=_registry(), now=NOW, max_assignments=1)
        self.assertEqual(manifest["backlog_deferred"], 2)
        _, next_manifest, next_assignments = build_intake(
            items, eligibility=_registry(), previous_ledger=ledger,
            now=NOW, max_assignments=3)
        self.assertEqual(next_manifest["duplicates"], 1)
        self.assertEqual(len(next_assignments), 2)

    def test_market_family_dedupes_contract_instances(self):
        items = [_item(
            external_id=f"m-{i}", title=f"Will event {i} happen?",
            product_family="same-family",
        ) for i in range(3)]
        _, manifest, assignments = build_intake(
            items, eligibility=_registry(), now=NOW)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(
            manifest["quality_rejection_reasons"]["duplicate_market_family"], 2)

    def test_live_failure_modes_are_hard_rejected(self):
        expired = _item(metadata={
            "status": "MARKET_STATUS_RESOLVED",
            "end_at": "2026-01-14T00:00:00Z",
        })
        generic_reply = _item(
            source_type="social", source_name="X", external_id="post-1",
            title="@user probabilities are interesting", venue_ids=[],
        )
        opaque_filing = _item(
            source_type="regulatory_filing", source_name="CFTC",
            external_id="filing-1", title="MLBEXTRAS1", venue_ids=[],
        )
        withdrawn_filing = _item(
            source_type="regulatory_filing", source_name="CFTC",
            external_id="filing-2", title="EOX Exchange LLC", venue_ids=[],
            topics=["organizations"], metadata={"Status": "Withdrawn"},
        )
        self.assertEqual(
            quality_rejection_reason(expired, now=NOW), "terminal_market")
        self.assertEqual(
            quality_rejection_reason(generic_reply, now=NOW),
            "low_signal_social_reply")
        self.assertEqual(
            quality_rejection_reason(opaque_filing, now=NOW),
            "opaque_product_code")
        self.assertEqual(
            quality_rejection_reason(withdrawn_filing, now=NOW),
            "terminal_regulatory_filing")

    def test_filing_description_takes_precedence_over_opaque_codes(self):
        self.assertEqual(
            normalize_regulatory_title("COIN", COIN_FILING_METADATA),
            "Modifications to Equity Index Perp Style Futures "
            "Market Maker Program")
        self.assertEqual(
            normalize_regulatory_title(
                "KEX", {"Organization": "KEX",
                        "Product Name": "Weather Contract"}),
            "Weather Contract")
        self.assertEqual(
            normalize_regulatory_title(
                "MLBEXTRAS1", {"Description": "Extra innings rulebook change"}),
            "Extra innings rulebook change")
        # A description-bearing column wins, but a human-readable title is
        # never overwritten and a code with no description is left alone.
        self.assertEqual(
            normalize_regulatory_title(
                "Market Maker Incentive Program",
                {"Organization": "CFE", "Filing Description": "CFE"}),
            "Market Maker Incentive Program")
        self.assertEqual(
            normalize_regulatory_title("MLBEXTRAS1", {}), "MLBEXTRAS1")

    def test_active_ten_day_review_filing_is_not_an_opaque_product_code(self):
        filing = _legacy_cftc_item()
        self.assertIsNone(quality_rejection_reason(filing, now=NOW))
        # The same active filing with no description column stays opaque, and
        # a terminal status still outranks the title rule.
        self.assertEqual(
            quality_rejection_reason(
                _legacy_cftc_item(metadata={
                    "Organization": "COIN", "Status": "10 Day Review"}),
                now=NOW),
            "opaque_product_code")
        self.assertEqual(
            quality_rejection_reason(
                _legacy_cftc_item(metadata=dict(
                    COIN_FILING_METADATA, Status="Withdrawn")), now=NOW),
            "terminal_regulatory_filing")

    def test_legacy_filing_title_survives_assignment_generation(self):
        filing = _legacy_cftc_item()
        # Identity is derived from source type, name and URL, so correcting the
        # title must not move the source item or its dedup hash.
        self.assertEqual(filing.id, "src_95f6533902b2cf2e6343")
        self.assertEqual(
            filing.content_hash,
            "c205b7e9cea96af97b6628184d9dc00dd8955565e94f6547c6cdd21ac73ce99b")
        _, manifest, assignments = build_intake(
            [filing], eligibility=_registry(),
            now=datetime(2026, 8, 2, 13, 47, 26, 10201, tzinfo=timezone.utc))
        self.assertEqual(manifest["quality_rejection_reasons"], {})
        self.assertEqual(len(assignments), 1)
        self.assertEqual(
            assignments[0].title,
            "Modifications to Equity Index Perp Style Futures "
            "Market Maker Program")
        self.assertEqual(assignments[0].id, "assignment_44c86b342e8ac95d8f67")
        self.assertEqual(assignments[0].source_item_id, filing.id)
        self.assertEqual(assignments[0].attribution_key, "CFTC:COIN")

    def test_active_organization_listing_is_low_priority_not_edge_evidence(self):
        organization = _item(
            source_type="regulatory_filing", source_name="CFTC",
            external_id="org-1", title="Active Exchange LLC", venue_ids=[],
            topics=["organizations"], reliability="primary",
            metadata={"Status": "Designated"},
        )
        _, _, assignments = build_intake(
            [organization], eligibility=_registry(), now=NOW)
        self.assertEqual(assignments[0].score, 68)
        self.assertEqual(assignments[0].assignment["quality_flags"], [
            "official_listing_is_not_edge_evidence",
        ])

    def test_research_memory_blocks_killed_and_existing_hypotheses(self):
        rules = [
            {"id": "P-024", "status": "killed", "match_any": ["first inning"]},
            {"id": "MLB-PROPS", "status": "existing", "match_any": ["home run"]},
        ]
        first_inning = _item(title="Will either team score in the first inning?")
        home_run = _item(external_id="hr", title="Will this player hit a home run?")
        self.assertEqual(
            quality_rejection_reason(first_inning, now=NOW, memory_rules=rules),
            "prior_research_killed")
        self.assertEqual(
            quality_rejection_reason(home_run, now=NOW, memory_rules=rules),
            "prior_research_existing")

    def test_market_census_bridge_preserves_provenance(self):
        items = market_census_items({
            "run_id": "census_1",
            "seeds": [{
                "id": "seed_1", "market_family": "KXTEST", "title": "Test",
                "lane": "contract_mechanics", "rank": 1, "score": 80,
                "generated_at": "2026-08-01T00:00:00Z",
                "evidence": {"category": "Politics"},
            }],
        }, retrieved_at="2026-08-01T12:00:00Z")
        self.assertEqual(items[0].venue_ids, ["kalshi"])
        self.assertEqual(items[0].metadata["census_run_id"], "census_1")


class TestCollectors(TestCase):
    def test_rss_and_atom_metadata_parsing(self):
        rss = """<rss><channel><item><guid>p1</guid><title>Paper One</title>
        <link>https://example.test/p1</link><pubDate>2026-08-01</pubDate>
        <description>An abstract.</description></item></channel></rss>"""
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>p2</id><title>Paper Two</title><link href="https://example.test/p2"/>
        <published>2026-08-01T00:00:00Z</published><summary>Summary.</summary>
        </entry></feed>"""
        collector = FeedCollector("Papers")
        self.assertEqual(collector.parse(rss, retrieved_at="now")[0].external_id, "p1")
        self.assertEqual(collector.parse(atom, retrieved_at="now")[0].external_id, "p2")

    def test_cftc_table_parser_preserves_primary_link(self):
        html = """<table><tr><th>Organization</th><th>Product Name</th><th>Date</th></tr>
        <tr><td>KEX</td><td><a href="/filings/product.pdf">Weather Contract</a></td>
        <td>08/01/2026</td></tr></table>"""
        items = CFTCCollector().parse(
            html, kind="products", retrieved_at="2026-08-01T12:00:00Z")
        self.assertEqual(items[0].title, "Weather Contract")
        self.assertEqual(items[0].url, "https://www.cftc.gov/filings/product.pdf")
        self.assertEqual(items[0].reliability, "primary")
        self.assertEqual(items[0].venue_ids, ["kalshi"])

    def test_cftc_rule_uses_filing_description_instead_of_venue_code(self):
        html = """<table><tr><th>Organization</th><th>Filing Description</th>
        <th>Status</th></tr><tr><td>CFE</td>
        <td><a href="/filings/rule.pdf">Market Maker Incentive Program</a></td>
        <td>Certified</td></tr></table>"""
        items = CFTCCollector().parse(
            html, kind="rules", retrieved_at="2026-08-01T12:00:00Z")
        self.assertEqual(items[0].title, "Market Maker Incentive Program")
        self.assertIn("CFE", items[0].summary)
        self.assertIsNone(quality_rejection_reason(items[0], now=NOW))

    def test_cftc_rows_keep_every_document_link(self):
        html = """<table><tr><th>Organization</th><th>Filing Description</th>
        <th>Documents</th></tr><tr><td>COIN</td>
        <td><a href="/filings/rule.pdf">Market Maker Program</a></td>
        <td><a href="/filings/rule.pdf">1</a><a href="/filings/ex.pdf">2</a></td>
        </tr></table>"""
        items = CFTCCollector().parse(
            html, kind="rules", retrieved_at="2026-08-01T12:00:00Z")
        self.assertEqual(items[0].metadata["documents"], [
            "https://www.cftc.gov/filings/rule.pdf",
            "https://www.cftc.gov/filings/ex.pdf",
        ])
        # The primary link still drives identity, so keeping the rest does not
        # move the source item id.
        self.assertEqual(items[0].url, "https://www.cftc.gov/filings/rule.pdf")

    def test_newer_equal_score_sources_rank_first(self):
        old = _item(
            external_id="old", title="Old", published_at="2026-07-01",
            product_family="old-family")
        new = _item(
            external_id="new", title="New", published_at="2026-08-01",
            product_family="new-family")
        _, _, assignments = build_intake(
            [old, new], eligibility=_registry(), now=NOW, max_assignments=2)
        self.assertEqual([item.title for item in assignments], ["New", "Old"])

    def test_polymarket_us_paginates_and_normalizes(self):
        session = _Session([
            {"markets": [{
                "slug": "market-one", "question": "Will one happen?",
                "category": "Sports", "createdAt": "2026-08-01T00:00:00Z",
            }]},
            {"markets": []},
        ])
        items = PolymarketUSCollector(session=session).collect(
            limit=1, max_pages=3, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].venue_ids, ["polymarket_us"])
        self.assertEqual(items[0].product_family, "sports")
        self.assertEqual(len(session.calls), 2)
        self.assertFalse(PolymarketUSCollector(session=_Session([{"markets": []}])).truncated)

    def test_polymarket_us_reports_inventory_truncation(self):
        session = _Session([{"markets": [{
            "slug": "market-one", "question": "Will one happen?",
            "category": "Sports",
        }]}])
        collector = PolymarketUSCollector(session=session)
        collector.collect(limit=1, max_pages=1, now=NOW)
        self.assertTrue(collector.truncated)

    def test_x_collector_is_explicit_and_stores_short_excerpt(self):
        with self.assertRaises(ValueError):
            XRecentSearchCollector("")
        session = _Session([{
            "data": [{
                "id": "123", "author_id": "7", "created_at": "2026-08-01T00:00:00Z",
                "text": "A possible exchange-mechanics idea", "public_metrics": {"like_count": 2},
            }],
            "includes": {"users": [{"id": "7", "username": "researcher"}]},
        }])
        items = XRecentSearchCollector("token", session=session).collect(
            "prediction market", now=NOW)
        self.assertEqual(items[0].url, "https://x.com/researcher/status/123")
        self.assertEqual(items[0].rights, "post_id_link_and_short_excerpt")
