"""
tests/test_signup_bonus_pod.py
─────────────────────────────────
Tests for src/pods/signup_bonus_pod.py (P-009).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pods.signup_bonus_pod import SignupBonusPod
from src.promo_calculator import PromoRecord

# ── Helpers ──────────────────────────────────────────────────────────

def _now_past():
    """A now_fn that is in the future relative to all test expiries."""
    return datetime(2030, 1, 1, tzinfo=timezone.utc)


def _now_present():
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_promo(
    promo_id="FD-FB-001",
    sportsbook="fanduel",
    promo_type="free_bet",
    amount=100.0,
    boosted_odds=200,       # +200 American
    fair_yes_prob=0.40,
    hedge_venue="kalshi",
    hedge_market_id="CHIEFS-2026-01",
    hedge_no_price=0.62,
    expiry="2026-01-20",
    status="pending",
):
    return PromoRecord(
        promo_id=promo_id,
        sportsbook=sportsbook,
        promo_type=promo_type,
        amount=amount,
        boosted_odds=boosted_odds,
        fair_yes_prob=fair_yes_prob,
        hedge_venue=hedge_venue,
        hedge_market_id=hedge_market_id,
        hedge_no_price=hedge_no_price,
        expiry=expiry,
        status=status,
    )


def _make_pod(promos, tmp_dir, min_conv=0.60, mode="paper", now_fn=None):
    return SignupBonusPod(
        promos=promos,
        min_conversion_rate=min_conv,
        trade_log_path=Path(tmp_dir) / "P-009.jsonl",
        mode=mode,
        _now_fn=now_fn or _now_present,
    )


# ── TestPodMetadata ───────────────────────────────────────────────────

class TestPodMetadata(unittest.TestCase):

    def test_pod_id(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([], td)
        self.assertEqual(pod.pod_id, "P-009")

    def test_pod_name(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([], td)
        self.assertEqual(pod.pod_name, "Sign-Up Bonus Blitz")

    def test_venue_name(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([], td)
        self.assertEqual(pod.venue_name, "multi")


# ── TestScanOnceHappyPath ─────────────────────────────────────────────

class TestScanOnceHappyPath(unittest.TestCase):

    def test_returns_placed_result(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo()], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "PLACED")

    def test_scan_result_fields(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo()], td)
            r = pod.scan_once()[0]
        self.assertEqual(r.pod_id, "P-009")
        self.assertEqual(r.market_id, "FD-FB-001")
        self.assertEqual(r.side, "NO")   # Hedge is always NO
        self.assertEqual(r.market_type, "promo_free_bet")

    def test_ev_is_locked_profit(self):
        """ev field should equal locked profit from hedge."""
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo(boosted_odds=200, hedge_no_price=0.62)], td)
            r = pod.scan_once()[0]
        # locked = free_bet_guaranteed_profit(100, american_to_decimal(200), 0.62)
        self.assertGreater(r.ev, 0)

    def test_edge_pct_is_conversion_rate(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo()], td)
            r = pod.scan_once()[0]
        self.assertGreater(r.edge_pct, 0)
        self.assertLessEqual(r.edge_pct, 1.0)

    def test_extra_has_hedge_details(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo()], td)
            r = pod.scan_once()[0]
        self.assertEqual(r.extra["promo_id"], "FD-FB-001")
        self.assertEqual(r.extra["sportsbook"], "fanduel")
        self.assertIn("hedge_size", r.extra)
        self.assertIn("locked_profit", r.extra)
        self.assertIn("conversion_rate", r.extra)

    def test_log_written(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo()], td)
            pod.scan_once()
            log_path = Path(td) / "P-009.jsonl"
            self.assertTrue(log_path.exists())
            with open(log_path) as fh:
                data = json.loads(fh.read().strip())
            self.assertEqual(data["action"], "PLACED")


# ── TestScanOnceSkips ─────────────────────────────────────────────────

class TestScanOnceSkips(unittest.TestCase):

    def test_used_promo_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo(status="used")], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)

    def test_expired_promo_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            # now_fn returns 2030, expiry is 2026
            pod = _make_pod([_make_promo()], td, now_fn=_now_past)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)

    def test_low_conversion_skipped_edge(self):
        """Conversion rate below min should produce SKIPPED_EDGE."""
        with tempfile.TemporaryDirectory() as td:
            # +100 odds (d=2.0), p_no=0.50: conv_rate = (2-1)*(1-0.50) = 0.50
            # min_conversion_rate=0.70 → should skip
            promo = _make_promo(boosted_odds=100, hedge_no_price=0.50)
            pod = _make_pod([promo], td, min_conv=0.70)
            results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "SKIPPED_EDGE")

    def test_missing_boosted_odds_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            promo = _make_promo()
            promo = PromoRecord(
                promo_id="x", sportsbook="fd", promo_type="free_bet",
                amount=100, boosted_odds=None, hedge_no_price=0.60,
            )
            pod = _make_pod([promo], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)

    def test_odds_boost_type_returns_none(self):
        """P-009 ignores odds_boost promos (handled by P-010)."""
        with tempfile.TemporaryDirectory() as td:
            promo = _make_promo(promo_type="odds_boost")
            pod = _make_pod([promo], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)


# ── TestDedup ─────────────────────────────────────────────────────────

class TestDedup(unittest.TestCase):

    def test_dedup_across_scans(self):
        """Same promo should not be hedged twice in the same hour."""
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_promo()], td)
            r1 = pod.scan_once()
            r2 = pod.scan_once()
        self.assertEqual(len(r1), 1)
        self.assertEqual(r1[0].action, "PLACED")
        self.assertEqual(len(r2), 0)  # Deduped


# ── TestFromConfig ────────────────────────────────────────────────────

class TestFromConfig(unittest.TestCase):

    def test_from_config_defaults(self):
        pod = SignupBonusPod.from_config({})
        self.assertEqual(pod.pod_id, "P-009")
        self.assertAlmostEqual(pod.min_conversion_rate, 0.60)

    def test_from_config_reads_promos(self):
        config = {
            "pods": {
                "P-009": {
                    "promos": [{
                        "promo_id": "FD-FB-001",
                        "sportsbook": "fanduel",
                        "promo_type": "free_bet",
                        "amount": 100,
                        "status": "pending",
                    }]
                }
            }
        }
        pod = SignupBonusPod.from_config(config)
        self.assertEqual(len(pod._promos), 1)
        self.assertEqual(pod._promos[0].promo_id, "FD-FB-001")

    def test_from_config_reads_min_conversion(self):
        config = {"pods": {"P-009": {"min_conversion_rate": 0.70}}}
        pod = SignupBonusPod.from_config(config)
        self.assertAlmostEqual(pod.min_conversion_rate, 0.70)


if __name__ == "__main__":
    unittest.main()
