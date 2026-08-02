"""Tests for deterministic market census and scout handoff artifacts."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from scripts.run_market_census import _fetch_active_series, main
from src.market_census import build_census, normalize_series, seeds_payload


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _series(ticker: str, **changes):
    row = {
        "ticker": ticker,
        "title": f"Title {ticker}",
        "category": "Politics",
        "frequency": "daily",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "contract_terms_url": f"https://example.test/{ticker}.pdf",
        "settlement_sources": [{"name": "Official", "url": "https://example.test"}],
    }
    row.update(changes)
    return row


class TestMarketCensus(TestCase):
    def test_normalizes_supported_shapes_and_is_stable(self):
        rows = normalize_series({"series": [_series("kxb"), _series("KXA")]})
        self.assertEqual([row["ticker"] for row in rows], ["KXA", "KXB"])
        self.assertEqual(rows, normalize_series({row["ticker"]: row for row in rows}))

    def test_delta_run_only_seeds_changed_series(self):
        first = build_census([_series("KXA"), _series("KXB")], now=NOW)
        second = build_census(
            [_series("KXA"), _series("KXB", fee_type="quadratic_with_maker_fees")],
            previous_snapshot=first.snapshot,
            previous_ledger=first.ledger,
            now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual([seed.market_family for seed in second.seeds], ["KXB"])
        self.assertIn("fee_regime_changed", second.seeds[0].reason_codes)
        self.assertEqual(second.manifest["changes"]["unchanged"], 1)

    def test_full_run_includes_unchanged_and_flags_duplicates(self):
        first = build_census([_series("KXA")], now=NOW)
        full = build_census(
            [_series("KXA")],
            previous_snapshot=first.snapshot,
            opportunities=[{"id": "op_old", "market_family": "KXA"}],
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
            full=True,
        )
        self.assertEqual(full.seeds[0].reason_codes, ["full_universe_review"])
        self.assertEqual(full.seeds[0].possible_duplicates, ["op_old"])
        self.assertFalse(full.seeds[0].assignment["may_enter_strategy_registry"])
        self.assertFalse(full.manifest["safety"]["creates_opportunity_cards"])

    def test_missing_series_remains_in_coverage_ledger(self):
        first = build_census([_series("KXA"), _series("KXB")], now=NOW)
        second = build_census(
            [_series("KXA")], previous_snapshot=first.snapshot,
            previous_ledger=first.ledger,
            now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(second.manifest["changes"]["missing"], 1)
        self.assertIn("missing_since", second.ledger["series"]["KXB"])

    def test_due_recheck_is_seeded_once_even_without_market_change(self):
        first = build_census([_series("KXA")], now=NOW)
        opportunity = {
            "id": "op_watch", "market_family": "KXA",
            "recheck_after": "2026-08-03T00:00:00Z",
        }
        due = build_census(
            [_series("KXA")], previous_snapshot=first.snapshot,
            previous_ledger=first.ledger, opportunities=[opportunity],
            now=datetime(2026, 8, 3, 1, tzinfo=timezone.utc),
        )
        self.assertIn("scheduled_recheck", due.seeds[0].reason_codes)
        repeated = build_census(
            [_series("KXA")], previous_snapshot=due.snapshot,
            previous_ledger=due.ledger, opportunities=[opportunity],
            now=datetime(2026, 8, 4, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(repeated.seeds, [])

    def test_seed_payload_is_not_a_strategy_runtime_request(self):
        result = build_census([_series("KXA")], now=NOW)
        payload = seeds_payload(result)
        self.assertEqual(payload["kind"], "strategy_scout_inbox")
        self.assertNotIn("type", payload)
        self.assertNotIn("payload", payload)

    def test_shortlist_caps_lane_concentration_when_other_lanes_exist(self):
        rows = [_series(f"KXPOL{i:02d}") for i in range(20)]
        rows += [_series(f"KXSPORT{i:02d}", category="Sports") for i in range(5)]
        rows += [_series(f"KXCRYPTO{i:02d}", category="Crypto") for i in range(5)]
        result = build_census(rows, now=NOW, max_seeds=10)
        lane_counts = result.manifest["lane_counts"]
        self.assertLessEqual(max(lane_counts.values()), 4)
        self.assertGreaterEqual(len(lane_counts), 3)

    def test_inactive_series_is_suppressed_until_first_open_event(self):
        first = build_census(
            [_series("KXDORMANT")], now=NOW, active_series=[])
        self.assertEqual(first.seeds, [])
        self.assertEqual(first.manifest["changes"]["inactive_suppressed"], 1)
        self.assertFalse(first.snapshot["series"][0]["has_open_events"])

        activated = build_census(
            [_series("KXDORMANT")], previous_snapshot=first.snapshot,
            previous_ledger=first.ledger,
            now=datetime(2026, 8, 3, tzinfo=timezone.utc),
            active_series=["KXDORMANT"],
        )
        self.assertEqual(len(activated.seeds), 1)
        self.assertIn("market_activated", activated.seeds[0].reason_codes)
        self.assertTrue(activated.seeds[0].evidence["has_open_events"])

    def test_fetch_active_series_paginates_and_deduplicates(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get(self, path, params):
                self.calls.append((path, dict(params)))
                if len(self.calls) == 1:
                    return {
                        "events": [
                            {"series_ticker": "kxa"},
                            {"series_ticker": "KXB"},
                        ],
                        "cursor": "next",
                    }
                return {
                    "events": [{"series_ticker": "KXA"}], "cursor": "",
                }

        client = Client()
        self.assertEqual(_fetch_active_series(client), {"KXA", "KXB"})
        self.assertEqual(client.calls[1][1]["cursor"], "next")

    def test_cli_persists_snapshot_manifest_ledger_and_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "series.json"
            source.write_text(json.dumps([_series("KXA")]))
            output = root / "output"
            rc = main([
                "--series-input", str(source),
                "--output-dir", str(output),
                "--opportunities-dir", str(root / "opportunities"),
                "--now", "2026-08-02T12:00:00Z",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue((output / "snapshot.json").exists())
            self.assertTrue((output / "coverage_ledger.json").exists())
            self.assertTrue((output / "latest_manifest.json").exists())
            inbox = json.loads((output / "scout_inbox" / "20260802T120000Z.json").read_text())
            self.assertEqual(inbox["seeds"][0]["market_family"], "KXA")
