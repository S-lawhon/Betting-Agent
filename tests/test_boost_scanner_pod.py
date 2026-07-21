"""
tests/test_boost_scanner_pod.py
─────────────────────────────────
Tests for src/pods/boost_scanner_pod.py (P-010).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pods.boost_scanner_pod import BoostScannerPod
from src.promo_calculator import PromoRecord

# ── Helpers ──────────────────────────────────────────────────────────

def _now_present():
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _now_past():
    return datetime(2030, 1, 1, tzinfo=timezone.utc)


def _make_boost(
    promo_id="FD-BOOST-001",
    sportsbook="fanduel",
    boosted_odds=350,       # +350 American
    original_odds=150,      # +150 pre-boost
    fair_yes_prob=0.38,
    amount=25.0,
    max_stake=25.0,
    hedge_venue="kalshi",
    hedge_market_id="CHIEFS-2026-01",
    hedge_no_price=0.64,
    expiry="2026-01-20",
    status="pending",
):
    return PromoRecord(
        promo_id=promo_id,
        sportsbook=sportsbook,
        promo_type="odds_boost",
        amount=amount,
        boosted_odds=boosted_odds,
        original_odds=original_odds,
        fair_yes_prob=fair_yes_prob,
        max_stake=max_stake,
        hedge_venue=hedge_venue,
        hedge_market_id=hedge_market_id,
        hedge_no_price=hedge_no_price,
        expiry=expiry,
        status=status,
    )


def _make_pod(boosts, tmp_dir, boost_mode="ev", min_ev_pct=0.05,
              mode="paper", now_fn=None):
    return BoostScannerPod(
        boosts=boosts,
        boost_mode=boost_mode,
        min_ev_pct=min_ev_pct,
        trade_log_path=Path(tmp_dir) / "P-010.jsonl",
        mode=mode,
        _now_fn=now_fn or _now_present,
    )


# ── TestPodMetadata ───────────────────────────────────────────────────

class TestPodMetadata(unittest.TestCase):

    def test_pod_id(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([], td)
        self.assertEqual(pod.pod_id, "P-010")

    def test_pod_name(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([], td)
        self.assertEqual(pod.pod_name, "Daily Odds Boost Grind")

    def test_venue_name(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([], td)
        self.assertEqual(pod.venue_name, "multi")


# ── TestEvMode ────────────────────────────────────────────────────────

class TestEvMode(unittest.TestCase):

    def test_returns_placed_for_positive_ev(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td, boost_mode="ev")
            results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "PLACED")

    def test_scan_result_fields(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td)
            r = pod.scan_once()[0]
        self.assertEqual(r.pod_id, "P-010")
        self.assertEqual(r.market_id, "FD-BOOST-001")
        self.assertEqual(r.side, "YES")   # Boost is on YES side
        self.assertEqual(r.market_type, "promo_odds_boost")

    def test_ev_is_positive(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td)
            r = pod.scan_once()[0]
        self.assertGreater(r.ev, 0)

    def test_position_size_respects_max_stake(self):
        with tempfile.TemporaryDirectory() as td:
            # amount=50 but max_stake=25 → should bet $25
            boost = _make_boost(amount=50, max_stake=25)
            pod = _make_pod([boost], td)
            r = pod.scan_once()[0]
        self.assertAlmostEqual(r.position_size_usd, 25.0)

    def test_extra_has_boost_details(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td)
            r = pod.scan_once()[0]
        self.assertEqual(r.extra["sportsbook"], "fanduel")
        self.assertEqual(r.extra["boosted_odds"], 350)
        self.assertEqual(r.extra["original_odds"], 150)
        self.assertIn("ev_pct", r.extra)

    def test_log_written(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td)
            pod.scan_once()
            log_path = Path(td) / "P-010.jsonl"
            self.assertTrue(log_path.exists())
            with open(log_path) as fh:
                data = json.loads(fh.read().strip())
            self.assertEqual(data["action"], "PLACED")


# ── TestLockedMode ────────────────────────────────────────────────────

class TestLockedMode(unittest.TestCase):

    def test_locked_mode_placed(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td, boost_mode="locked")
            results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "PLACED")

    def test_locked_mode_hedge_size_in_extra(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td, boost_mode="locked")
            r = pod.scan_once()[0]
        self.assertGreater(r.extra["hedge_size"], 0)
        self.assertIn("locked_profit", r.extra)

    def test_locked_mode_falls_back_to_ev_when_no_price(self):
        """If hedge_no_price is None and no client, fall back to EV mode."""
        with tempfile.TemporaryDirectory() as td:
            boost = _make_boost(hedge_no_price=None, hedge_market_id=None)
            pod = _make_pod([boost], td, boost_mode="locked")
            results = pod.scan_once()
        # Should still produce a result (EV fallback)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "PLACED")


# ── TestScanOnceSkips ─────────────────────────────────────────────────

class TestScanOnceSkips(unittest.TestCase):

    def test_used_boost_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost(status="used")], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)

    def test_expired_boost_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td, now_fn=_now_past)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)

    def test_negative_ev_boost_skipped(self):
        """Boost with EV below min_ev_pct → SKIPPED_EDGE."""
        with tempfile.TemporaryDirectory() as td:
            # +100 (d=2.0), fair_prob=0.40: EV% = 2.0*0.40 - 1 = -0.20 < 0
            boost = _make_boost(boosted_odds=100, fair_yes_prob=0.40)
            pod = _make_pod([boost], td, min_ev_pct=0.05)
            results = pod.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "SKIPPED_EDGE")

    def test_missing_fair_prob_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            boost = PromoRecord(
                promo_id="x", sportsbook="fd", promo_type="odds_boost",
                amount=25, boosted_odds=350, fair_yes_prob=None, status="pending",
            )
            pod = _make_pod([boost], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)

    def test_non_boost_promo_type_ignored(self):
        """free_bet promos should be ignored by P-010."""
        with tempfile.TemporaryDirectory() as td:
            promo = PromoRecord(
                promo_id="x", sportsbook="fd", promo_type="free_bet",
                amount=100, status="pending", boosted_odds=200, fair_yes_prob=0.40,
            )
            pod = _make_pod([promo], td)
            results = pod.scan_once()
        self.assertEqual(len(results), 0)


# ── TestDedup ─────────────────────────────────────────────────────────

class TestDedup(unittest.TestCase):

    def test_dedup_across_scans(self):
        """Same boost shouldn't be processed twice in the same hour."""
        with tempfile.TemporaryDirectory() as td:
            pod = _make_pod([_make_boost()], td)
            r1 = pod.scan_once()
            r2 = pod.scan_once()
        self.assertEqual(len(r1), 1)
        self.assertEqual(r1[0].action, "PLACED")
        self.assertEqual(len(r2), 0)


# ── TestMultipleBoosts ────────────────────────────────────────────────

class TestMultipleBoosts(unittest.TestCase):

    def test_two_boosts_scanned(self):
        with tempfile.TemporaryDirectory() as td:
            boosts = [
                _make_boost(promo_id="BOOST-1"),
                _make_boost(promo_id="BOOST-2", boosted_odds=400),
            ]
            pod = _make_pod(boosts, td)
            results = pod.scan_once()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.action == "PLACED" for r in results))


# ── TestFromConfig ────────────────────────────────────────────────────

class TestFromConfig(unittest.TestCase):

    def test_defaults(self):
        pod = BoostScannerPod.from_config({})
        self.assertEqual(pod.pod_id, "P-010")
        self.assertEqual(pod.boost_mode, "ev")
        self.assertAlmostEqual(pod.min_ev_pct, 0.05)

    def test_reads_boosts(self):
        config = {
            "pods": {
                "P-010": {
                    "boosts": [{
                        "promo_id": "FD-B-001",
                        "sportsbook": "fanduel",
                        "promo_type": "odds_boost",
                        "amount": 25,
                        "boosted_odds": 350,
                        "fair_yes_prob": 0.38,
                        "status": "pending",
                    }]
                }
            }
        }
        pod = BoostScannerPod.from_config(config)
        self.assertEqual(len(pod._boosts), 1)
        self.assertEqual(pod._boosts[0].promo_id, "FD-B-001")

    def test_reads_boost_mode(self):
        config = {"pods": {"P-010": {"boost_mode": "locked", "min_ev_pct": 0.08}}}
        pod = BoostScannerPod.from_config(config)
        self.assertEqual(pod.boost_mode, "locked")
        self.assertAlmostEqual(pod.min_ev_pct, 0.08)


if __name__ == "__main__":
    unittest.main()
