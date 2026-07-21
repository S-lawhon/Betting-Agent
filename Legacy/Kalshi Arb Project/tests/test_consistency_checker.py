"""
tests/test_consistency_checker.py
─────────────────────────────────
Unit tests for cross-tier implied probability consistency arbitrage.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.consistency_checker import (
    ConsistencyChecker,
    ConsistencySignal,
    GameMarketInfo,
    TeamMarketSnapshot,
    _mid_price,
)


# ── Test helpers ─────────────────────────────────────────────────────────────

def _make_market(
    ticker: str,
    title: str = "",
    yes_bid: int = 50,
    yes_ask: int = 54,
    volume: int = 1000,
    series_ticker: str = "",
    close_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal Kalshi market dict for testing."""
    m: Dict[str, Any] = {
        "ticker": ticker,
        "title": title or f"Market for {ticker}",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "volume": volume,
        "series_ticker": series_ticker,
    }
    if close_time:
        m["close_time"] = close_time
    return m


def _make_checker(**kwargs) -> ConsistencyChecker:
    """Create a ConsistencyChecker with test defaults."""
    defaults = {
        "min_divergence": 0.05,
        "min_game_markets": 3,
        "min_volume": 100,
        "log_path": Path(tempfile.mktemp(suffix=".jsonl")),
        "max_conditional_finals_prob": 0.75,
        "_now_fn": lambda: datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return ConsistencyChecker(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMidPrice(unittest.TestCase):
    """Test mid-price extraction from Kalshi market dicts."""

    def test_normal_mid(self):
        m = {"yes_bid": 50, "yes_ask": 54}
        self.assertAlmostEqual(_mid_price(m), 0.52)

    def test_missing_bid(self):
        self.assertIsNone(_mid_price({"yes_ask": 54}))

    def test_missing_ask(self):
        self.assertIsNone(_mid_price({"yes_bid": 50}))

    def test_zero_mid(self):
        """Mid of 0 is out of range (0, 1)."""
        self.assertIsNone(_mid_price({"yes_bid": 0, "yes_ask": 0}))

    def test_hundred_mid(self):
        """Mid of 1.0 is out of range (0, 1)."""
        self.assertIsNone(_mid_price({"yes_bid": 100, "yes_ask": 100}))


class TestTeamGrouping(unittest.TestCase):
    """Markets from different tiers should be grouped by team."""

    def test_groups_across_tiers(self):
        """Markets from 4 tiers for BOS should merge into one snapshot."""
        checker = _make_checker()
        markets = [
            _make_market("KXNBAGAME-26FEB23BOSKNK-BOS", series_ticker="KXNBAGAME",
                        yes_bid=60, yes_ask=64, volume=500),
            _make_market("KXNBAGAME-26FEB25BOSMIA-BOS", series_ticker="KXNBAGAME",
                        yes_bid=55, yes_ask=59, volume=400),
            _make_market("KXNBAGAME-26FEB27BOSIND-BOS", series_ticker="KXNBAGAME",
                        yes_bid=65, yes_ask=69, volume=600),
            _make_market("KXNBAPLAYOFF-26-BOS", series_ticker="KXNBAPLAYOFF",
                        yes_bid=88, yes_ask=92, volume=1000),
            _make_market("KXNBAEAST-26-BOS", series_ticker="KXNBAEAST",
                        yes_bid=25, yes_ask=29, volume=800),
            _make_market("KXNBA-26-BOS", series_ticker="KXNBA",
                        yes_bid=10, yes_ask=14, volume=600),
        ]
        snapshots = checker._group_by_team(markets)
        self.assertIn("BOS", snapshots)
        snap = snapshots["BOS"]
        self.assertEqual(len(snap.game_markets), 3)
        self.assertAlmostEqual(snap.playoff_prob, 0.90)      # (88+92)/200
        self.assertAlmostEqual(snap.conference_prob, 0.27)    # (25+29)/200
        self.assertAlmostEqual(snap.championship_prob, 0.12)  # (10+14)/200

    def test_low_volume_markets_excluded(self):
        """Markets below min_volume should be excluded."""
        checker = _make_checker(min_volume=500)
        markets = [
            _make_market("KXNBAPLAYOFF-26-BOS", series_ticker="KXNBAPLAYOFF",
                        yes_bid=80, yes_ask=84, volume=100),  # below threshold
        ]
        snapshots = checker._group_by_team(markets)
        self.assertEqual(len(snapshots), 0)

    def test_multiple_teams(self):
        """Different teams should produce separate snapshots."""
        checker = _make_checker()
        markets = [
            _make_market("KXNBAPLAYOFF-26-BOS", series_ticker="KXNBAPLAYOFF",
                        yes_bid=88, yes_ask=92, volume=1000),
            _make_market("KXNBAPLAYOFF-26-LAL", series_ticker="KXNBAPLAYOFF",
                        yes_bid=40, yes_ask=44, volume=800),
        ]
        snapshots = checker._group_by_team(markets)
        self.assertIn("BOS", snapshots)
        self.assertIn("LAL", snapshots)
        self.assertAlmostEqual(snapshots["BOS"].playoff_prob, 0.90)
        self.assertAlmostEqual(snapshots["LAL"].playoff_prob, 0.42)


class TestTierMonotonicity(unittest.TestCase):
    """P(champ) ≤ P(conf) ≤ P(playoff) — violations should be flagged."""

    def test_champ_greater_than_conf(self):
        """P(champ) > P(conf) is a monotonicity violation."""
        checker = _make_checker(min_divergence=0.03)
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=0.90,
            conference_prob=0.20,
            championship_prob=0.30,  # VIOLATION: 0.30 > 0.20
            playoff_ticker="KXNBAPLAYOFF-26-BOS",
            conference_ticker="KXNBAEAST-26-BOS",
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_tier_monotonicity(snapshot)
        # Should flag champ > conf
        mono_signals = [s for s in signals if "champ" in s.description and "conf" in s.description]
        self.assertTrue(len(mono_signals) >= 1)
        self.assertEqual(mono_signals[0].signal_type, "TIER_MONOTONICITY")
        self.assertEqual(mono_signals[0].recommended_side, "NO")
        self.assertAlmostEqual(mono_signals[0].divergence, 0.10)

    def test_conf_greater_than_playoff(self):
        """P(conf) > P(playoff) is a monotonicity violation."""
        checker = _make_checker(min_divergence=0.03)
        snapshot = TeamMarketSnapshot(
            team="LAL",
            game_markets=(),
            playoff_prob=0.30,
            conference_prob=0.40,  # VIOLATION: 0.40 > 0.30
            championship_prob=0.10,
            playoff_ticker="KXNBAPLAYOFF-26-LAL",
            conference_ticker="KXNBAWEST-26-LAL",
            championship_ticker="KXNBA-26-LAL",
        )
        signals = checker._check_tier_monotonicity(snapshot)
        conf_vs_playoff = [
            s for s in signals
            if "conf" in s.description and "playoff" in s.description
        ]
        self.assertTrue(len(conf_vs_playoff) >= 1)

    def test_consistent_tiers_no_signal(self):
        """Properly ordered tiers should produce no signal."""
        checker = _make_checker()
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=0.90,
            conference_prob=0.30,
            championship_prob=0.15,
            playoff_ticker="KXNBAPLAYOFF-26-BOS",
            conference_ticker="KXNBAEAST-26-BOS",
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_tier_monotonicity(snapshot)
        self.assertEqual(len(signals), 0)

    def test_missing_tiers_skipped(self):
        """If a tier is missing, those checks should be skipped."""
        checker = _make_checker()
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=0.90,
            conference_prob=None,
            championship_prob=0.15,
            playoff_ticker="KXNBAPLAYOFF-26-BOS",
            conference_ticker=None,
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_tier_monotonicity(snapshot)
        # champ vs conf → skipped (conf is None)
        # conf vs playoff → skipped (conf is None)
        # champ vs playoff → 0.15 ≤ 0.90, ok
        self.assertEqual(len(signals), 0)

    def test_divergence_below_threshold(self):
        """Small violations within min_divergence should not trigger."""
        checker = _make_checker(min_divergence=0.10)
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=0.90,
            conference_prob=0.25,
            championship_prob=0.30,  # only 0.05 above conf, below 0.10 threshold
            playoff_ticker="KXNBAPLAYOFF-26-BOS",
            conference_ticker="KXNBAEAST-26-BOS",
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_tier_monotonicity(snapshot)
        self.assertEqual(len(signals), 0)


class TestGameVsPlayoff(unittest.TestCase):
    """Game win rate should be consistent with playoff probability."""

    def _make_snapshot(
        self,
        game_probs: List[float],
        playoff_prob: float,
        volume: int = 500,
    ) -> TeamMarketSnapshot:
        games = tuple(
            GameMarketInfo(
                ticker=f"KXNBAGAME-26FEB{20+i}-BOS",
                opponent=None,
                implied_win_prob=p,
                commence_time=None,
                volume=volume,
            )
            for i, p in enumerate(game_probs)
        )
        return TeamMarketSnapshot(
            team="BOS",
            game_markets=games,
            playoff_prob=playoff_prob,
            conference_prob=None,
            championship_prob=None,
            playoff_ticker="KXNBAPLAYOFF-26-BOS",
            conference_ticker=None,
            championship_ticker=None,
        )

    def test_high_win_rate_low_playoff(self):
        """70% game win rate but 40% playoff prob → signal."""
        checker = _make_checker(min_game_markets=3, min_divergence=0.05)
        snapshot = self._make_snapshot(
            game_probs=[0.70, 0.72, 0.68, 0.71],
            playoff_prob=0.40,
        )
        signals = checker._check_game_vs_playoff(snapshot)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type, "GAME_VS_PLAYOFF")
        self.assertEqual(signals[0].recommended_side, "YES")  # playoff underpriced

    def test_low_win_rate_high_playoff(self):
        """40% game win rate but 90% playoff prob → signal."""
        checker = _make_checker(min_game_markets=3, min_divergence=0.05)
        snapshot = self._make_snapshot(
            game_probs=[0.40, 0.38, 0.42],
            playoff_prob=0.90,
        )
        signals = checker._check_game_vs_playoff(snapshot)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].recommended_side, "NO")  # playoff overpriced

    def test_consistent_no_signal(self):
        """50% win rate and 50% playoff prob → roughly consistent."""
        checker = _make_checker(min_game_markets=3, min_divergence=0.10)
        snapshot = self._make_snapshot(
            game_probs=[0.50, 0.51, 0.49],
            playoff_prob=0.50,
        )
        signals = checker._check_game_vs_playoff(snapshot)
        self.assertEqual(len(signals), 0)

    def test_too_few_games_skipped(self):
        """Fewer than min_game_markets → skip check."""
        checker = _make_checker(min_game_markets=3)
        snapshot = self._make_snapshot(
            game_probs=[0.70, 0.72],  # only 2 games
            playoff_prob=0.40,
        )
        signals = checker._check_game_vs_playoff(snapshot)
        self.assertEqual(len(signals), 0)

    def test_no_playoff_market_skipped(self):
        """Missing playoff market → skip check."""
        checker = _make_checker()
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(
                GameMarketInfo("T1", None, 0.70, None, 500),
                GameMarketInfo("T2", None, 0.70, None, 500),
                GameMarketInfo("T3", None, 0.70, None, 500),
            ),
            playoff_prob=None,
            conference_prob=None,
            championship_prob=None,
            playoff_ticker=None,
            conference_ticker=None,
            championship_ticker=None,
        )
        signals = checker._check_game_vs_playoff(snapshot)
        self.assertEqual(len(signals), 0)

    def test_volume_weighted_average(self):
        """Game win rate should be volume-weighted."""
        checker = _make_checker(min_game_markets=3, min_divergence=0.05)
        # High-volume game at 0.70, low-volume games at 0.50
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(
                GameMarketInfo("T1", None, 0.70, None, 10000),  # dominates
                GameMarketInfo("T2", None, 0.50, None, 100),
                GameMarketInfo("T3", None, 0.50, None, 100),
            ),
            playoff_prob=0.40,  # much lower than implied by ~0.70 weighted avg
            conference_prob=None,
            championship_prob=None,
            playoff_ticker="KXNBAPLAYOFF-26-BOS",
            conference_ticker=None,
            championship_ticker=None,
        )
        signals = checker._check_game_vs_playoff(snapshot)
        self.assertEqual(len(signals), 1)
        # Weighted avg is dominated by 0.70, so playoff should be ~90%+
        self.assertEqual(signals[0].recommended_side, "YES")


class TestConfVsChampionship(unittest.TestCase):
    """P(champ)/P(conf) conditional probability should be realistic."""

    def test_unrealistic_conditional(self):
        """P(champ)/P(conf) = 0.30/0.35 ≈ 0.857 → too high."""
        checker = _make_checker(max_conditional_finals_prob=0.75)
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=None,
            conference_prob=0.35,
            championship_prob=0.30,
            playoff_ticker=None,
            conference_ticker="KXNBAEAST-26-BOS",
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_conference_vs_championship(snapshot)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type, "CONF_VS_CHAMP")
        self.assertEqual(signals[0].recommended_side, "NO")

    def test_realistic_conditional(self):
        """P(champ)/P(conf) = 0.15/0.30 = 0.50 → fine."""
        checker = _make_checker(max_conditional_finals_prob=0.75)
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=None,
            conference_prob=0.30,
            championship_prob=0.15,
            playoff_ticker=None,
            conference_ticker="KXNBAEAST-26-BOS",
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_conference_vs_championship(snapshot)
        self.assertEqual(len(signals), 0)

    def test_missing_conference_skipped(self):
        """No conference market → skip check."""
        checker = _make_checker()
        snapshot = TeamMarketSnapshot(
            team="BOS",
            game_markets=(),
            playoff_prob=None,
            conference_prob=None,
            championship_prob=0.15,
            playoff_ticker=None,
            conference_ticker=None,
            championship_ticker="KXNBA-26-BOS",
        )
        signals = checker._check_conference_vs_championship(snapshot)
        self.assertEqual(len(signals), 0)

    def test_near_zero_conference_skipped(self):
        """Very low P(conf) should be skipped to avoid div-by-zero."""
        checker = _make_checker()
        snapshot = TeamMarketSnapshot(
            team="WAS",
            game_markets=(),
            playoff_prob=None,
            conference_prob=0.005,
            championship_prob=0.003,
            playoff_ticker=None,
            conference_ticker="KXNBAEAST-26-WAS",
            championship_ticker="KXNBA-26-WAS",
        )
        signals = checker._check_conference_vs_championship(snapshot)
        self.assertEqual(len(signals), 0)


class TestGameRateToPlayoffProb(unittest.TestCase):
    """Verify the logistic mapping from game win rate to playoff probability."""

    def test_fifty_pct_near_fifty(self):
        prob = ConsistencyChecker._game_rate_to_playoff_prob(0.50)
        self.assertAlmostEqual(prob, 0.50, places=2)

    def test_sixty_pct_high(self):
        prob = ConsistencyChecker._game_rate_to_playoff_prob(0.60)
        self.assertGreater(prob, 0.85)

    def test_forty_pct_low(self):
        prob = ConsistencyChecker._game_rate_to_playoff_prob(0.40)
        self.assertLess(prob, 0.15)

    def test_extreme_high(self):
        prob = ConsistencyChecker._game_rate_to_playoff_prob(0.80)
        self.assertGreater(prob, 0.95)

    def test_extreme_low(self):
        prob = ConsistencyChecker._game_rate_to_playoff_prob(0.20)
        self.assertLess(prob, 0.05)


class TestScanCycleIntegration(unittest.TestCase):
    """Full scan_cycle with realistic market data."""

    def test_full_cycle_detects_monotonicity_violation(self):
        """A full scan should detect tier monotonicity violations."""
        checker = _make_checker(min_divergence=0.03, min_volume=100)
        markets = [
            # BOS: championship > conference → violation
            _make_market("KXNBAPLAYOFF-26-BOS", series_ticker="KXNBAPLAYOFF",
                        yes_bid=88, yes_ask=92, volume=1000),
            _make_market("KXNBAEAST-26-BOS", series_ticker="KXNBAEAST",
                        yes_bid=20, yes_ask=24, volume=800),
            _make_market("KXNBA-26-BOS", series_ticker="KXNBA",
                        yes_bid=30, yes_ask=34, volume=600),  # 0.32 > 0.22
        ]
        signals = checker.scan_cycle(markets)
        mono_signals = [s for s in signals if s.signal_type == "TIER_MONOTONICITY"]
        self.assertTrue(len(mono_signals) >= 1)
        self.assertEqual(mono_signals[0].team, "BOS")

    def test_full_cycle_clean(self):
        """Consistent markets should produce no signals."""
        checker = _make_checker(min_volume=100)
        markets = [
            _make_market("KXNBAPLAYOFF-26-BOS", series_ticker="KXNBAPLAYOFF",
                        yes_bid=88, yes_ask=92, volume=1000),
            _make_market("KXNBAEAST-26-BOS", series_ticker="KXNBAEAST",
                        yes_bid=28, yes_ask=32, volume=800),
            _make_market("KXNBA-26-BOS", series_ticker="KXNBA",
                        yes_bid=12, yes_ask=16, volume=600),
        ]
        signals = checker.scan_cycle(markets)
        self.assertEqual(len(signals), 0)

    def test_empty_markets(self):
        """No markets → no signals, no crash."""
        checker = _make_checker()
        signals = checker.scan_cycle([])
        self.assertEqual(len(signals), 0)


class TestLogWriting(unittest.TestCase):
    """Verify signals are written to the log file."""

    def test_signal_written_to_jsonl(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        checker = _make_checker(log_path=log_path, min_divergence=0.03, min_volume=100)
        markets = [
            _make_market("KXNBAPLAYOFF-26-BOS", series_ticker="KXNBAPLAYOFF",
                        yes_bid=88, yes_ask=92, volume=1000),
            _make_market("KXNBAEAST-26-BOS", series_ticker="KXNBAEAST",
                        yes_bid=20, yes_ask=24, volume=800),
            _make_market("KXNBA-26-BOS", series_ticker="KXNBA",
                        yes_bid=30, yes_ask=34, volume=600),
        ]
        signals = checker.scan_cycle(markets)
        self.assertTrue(len(signals) >= 1)

        # Read the log
        lines = log_path.read_text().strip().split("\n")
        self.assertTrue(len(lines) >= 1)
        record = json.loads(lines[0])
        self.assertEqual(record["team"], "BOS")
        self.assertIn("signal_type", record)
        self.assertIn("timestamp_utc", record)


class TestFromConfig(unittest.TestCase):
    """Verify from_config() classmethod."""

    def test_default_config(self):
        config = {"consistency": {"enabled": True}}
        checker = ConsistencyChecker.from_config(config)
        self.assertAlmostEqual(checker._min_divergence, 0.05)
        self.assertEqual(checker._min_game_markets, 3)
        self.assertEqual(checker._min_volume, 200)

    def test_custom_config(self):
        config = {
            "consistency": {
                "enabled": True,
                "min_divergence": 0.08,
                "min_game_markets": 5,
                "min_volume": 300,
                "max_conditional_finals_prob": 0.65,
                "log_path": "data/custom_log.jsonl",
                "series_tickers": {
                    "game": "KXNBAGAME",
                    "playoff": "KXNBAPLAYOFF",
                    "championship": "KXNBA",
                },
            }
        }
        checker = ConsistencyChecker.from_config(config)
        self.assertAlmostEqual(checker._min_divergence, 0.08)
        self.assertEqual(checker._min_game_markets, 5)
        self.assertEqual(checker._min_volume, 300)
        self.assertAlmostEqual(checker._max_cond_finals, 0.65)


if __name__ == "__main__":
    unittest.main()
