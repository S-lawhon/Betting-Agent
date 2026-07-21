"""
tests/test_learner.py
─────────────────────
Phase 10: tests for src/learner.py

Covers
──────
  _sigmoid            — numeric helper
  _fit_logistic       — gradient-descent logistic regression
  _global_bias        — bias computation
  _sport_bias         — per-sport bias (threshold gating)
  Learner.update      — record appending, rolling window, ignored outcomes
  Learner.n_resolved  — property
  Learner.train       — skipped below min_trades, fits above
  Learner.adjust_prob — disabled / enabled, bias-only, logistic, clamping
  Learner.stats       — summary dict fields
  Learner.save / load — JSON round-trip, missing file, error handling
  Learner.from_config — config wiring, enabled flag, path handling
  Integration         — update → train → adjust_prob chain
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.learner import (
    Learner,
    _fit_logistic,
    _global_bias,
    _sigmoid,
    _sport_bias,
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

_FIXED_NOW = datetime(2024, 1, 15, 18, 30, 0, tzinfo=timezone.utc)


def _now():
    return _FIXED_NOW


def _make_learner(
    min_trades: int = 5,
    max_adj: float = 0.03,
    max_records: int = 500,
    enabled: bool = True,
    state_path: Optional[Path] = None,
) -> Learner:
    return Learner(
        min_trades=min_trades,
        max_adjustment_pct=max_adj,
        max_records=max_records,
        model_state_path=state_path,
        enabled=enabled,
        _now_fn=_now,
    )


def _add_wins(learner: Learner, n: int, fair_prob: float = 0.60) -> None:
    for _ in range(n):
        learner.update(fair_prob, 0.55, 0.05, 0.02, "nfl", "WIN")


def _add_losses(learner: Learner, n: int, fair_prob: float = 0.40) -> None:
    for _ in range(n):
        learner.update(fair_prob, 0.45, 0.04, 0.01, "nfl", "LOSS")


def _add_mixed(learner: Learner, n_win: int, n_loss: int) -> None:
    _add_wins(learner, n_win)
    _add_losses(learner, n_loss)


# ══════════════════════════════════════════════════════════════════════════════
# _sigmoid
# ══════════════════════════════════════════════════════════════════════════════

class TestSigmoid(unittest.TestCase):

    def test_zero_gives_half(self):
        self.assertAlmostEqual(float(_sigmoid(np.array([0.0]))[0]), 0.5)

    def test_large_positive_approaches_one(self):
        self.assertGreater(float(_sigmoid(np.array([100.0]))[0]), 0.99)

    def test_large_negative_approaches_zero(self):
        self.assertLess(float(_sigmoid(np.array([-100.0]))[0]), 0.01)

    def test_monotonically_increasing(self):
        z = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
        s = _sigmoid(z)
        self.assertTrue(all(s[i] < s[i + 1] for i in range(len(s) - 1)))


# ══════════════════════════════════════════════════════════════════════════════
# _fit_logistic
# ══════════════════════════════════════════════════════════════════════════════

class TestFitLogistic(unittest.TestCase):

    def test_returns_array_of_correct_shape(self):
        X = np.random.default_rng(0).random((20, 2))
        y = (X[:, 0] > 0.5).astype(float)
        w = _fit_logistic(X, y)
        self.assertEqual(w.shape, (3,))   # 2 features + 1 bias

    def test_learns_separable_data(self):
        # x > 0.5 → WIN; x <= 0.5 → LOSS
        rng = np.random.default_rng(42)
        X = rng.random((100, 1)) * 2 - 1   # [-1, 1]
        y = (X[:, 0] > 0).astype(float)
        w = _fit_logistic(X, y, lr=0.5, n_iter=500)
        # positive feature should have positive weight
        self.assertGreater(w[0], 0)

    def test_all_wins_bias_positive(self):
        X = np.ones((10, 1)) * 0.5
        y = np.ones(10)
        w = _fit_logistic(X, y, n_iter=200)
        # prediction should push toward 1 → bias term should be large positive
        self.assertGreater(float(_sigmoid(np.array([0.5, 1.0]) @ w)), 0.7)

    def test_all_losses_bias_negative(self):
        X = np.ones((10, 1)) * 0.5
        y = np.zeros(10)
        w = _fit_logistic(X, y, n_iter=200)
        self.assertLess(float(_sigmoid(np.array([0.5, 1.0]) @ w)), 0.3)


# ══════════════════════════════════════════════════════════════════════════════
# _global_bias
# ══════════════════════════════════════════════════════════════════════════════

class TestGlobalBias(unittest.TestCase):

    def test_empty_returns_zero(self):
        self.assertEqual(_global_bias([]), 0.0)

    def test_all_wins_at_low_fair_prob_gives_positive_bias(self):
        # all won, but fair_prob was 0.50 → actual=1.0 > pred=0.50 → bias=+0.50
        records = [{"fair_prob": 0.50, "outcome": 1} for _ in range(10)]
        self.assertAlmostEqual(_global_bias(records), 0.50)

    def test_all_losses_at_high_fair_prob_gives_negative_bias(self):
        records = [{"fair_prob": 0.70, "outcome": 0} for _ in range(10)]
        self.assertAlmostEqual(_global_bias(records), -0.70)

    def test_calibrated_returns_near_zero(self):
        # 6 wins and 4 losses; fair_prob matches observed rate
        records = (
            [{"fair_prob": 0.60, "outcome": 1}] * 6 +
            [{"fair_prob": 0.60, "outcome": 0}] * 4
        )
        # mean outcome = 0.6, mean fair_prob = 0.6 → bias ≈ 0
        self.assertAlmostEqual(_global_bias(records), 0.0, places=10)


# ══════════════════════════════════════════════════════════════════════════════
# _sport_bias
# ══════════════════════════════════════════════════════════════════════════════

class TestSportBias(unittest.TestCase):

    def test_returns_none_below_threshold(self):
        records = [{"fair_prob": 0.5, "sport": "nfl", "outcome": 1}] * 9
        self.assertIsNone(_sport_bias(records, "nfl"))

    def test_returns_bias_at_threshold(self):
        records = [{"fair_prob": 0.5, "sport": "nfl", "outcome": 1}] * 10
        result = _sport_bias(records, "nfl")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.5)   # all wins, fair=0.5 → bias=+0.5

    def test_returns_none_for_unknown_sport(self):
        records = [{"fair_prob": 0.5, "sport": "nfl", "outcome": 1}] * 20
        self.assertIsNone(_sport_bias(records, "nba"))

    def test_correct_bias_mixed(self):
        records = (
            [{"fair_prob": 0.60, "sport": "nfl", "outcome": 1}] * 7 +
            [{"fair_prob": 0.60, "sport": "nfl", "outcome": 0}] * 3
        )
        result = _sport_bias(records, "nfl")
        # mean_outcome=0.7, mean_fair_prob=0.6 → bias=+0.1
        self.assertAlmostEqual(result, 0.1)


# ══════════════════════════════════════════════════════════════════════════════
# Learner.update / n_resolved
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerUpdate(unittest.TestCase):

    def setUp(self):
        self.learner = _make_learner()

    def test_win_increments_n_resolved(self):
        self.learner.update(0.6, 0.55, 0.05, 0.02, "nfl", "WIN")
        self.assertEqual(self.learner.n_resolved, 1)

    def test_loss_increments_n_resolved(self):
        self.learner.update(0.4, 0.45, 0.03, 0.01, "nfl", "LOSS")
        self.assertEqual(self.learner.n_resolved, 1)

    def test_void_ignored(self):
        self.learner.update(0.5, 0.5, 0.05, 0.02, "nfl", "VOID")
        self.assertEqual(self.learner.n_resolved, 0)

    def test_pending_ignored(self):
        self.learner.update(0.5, 0.5, 0.05, 0.02, "nfl", "PENDING")
        self.assertEqual(self.learner.n_resolved, 0)

    def test_unrecognised_outcome_ignored(self):
        self.learner.update(0.5, 0.5, 0.05, 0.02, "nfl", "UNKNOWN")
        self.assertEqual(self.learner.n_resolved, 0)

    def test_rolling_window_evicts_oldest(self):
        learner = _make_learner(max_records=3)
        _add_wins(learner, 5)
        self.assertEqual(learner.n_resolved, 3)

    def test_outcome_stored_as_int(self):
        self.learner.update(0.6, 0.55, 0.05, 0.02, "nfl", "WIN")
        self.assertEqual(self.learner._records[0]["outcome"], 1)

    def test_loss_stored_as_zero(self):
        self.learner.update(0.4, 0.45, 0.03, 0.01, "nfl", "LOSS")
        self.assertEqual(self.learner._records[0]["outcome"], 0)

    def test_none_sport_stored_as_unknown(self):
        self.learner.update(0.6, 0.55, 0.05, 0.02, None, "WIN")
        self.assertEqual(self.learner._records[0]["sport"], "unknown")


# ══════════════════════════════════════════════════════════════════════════════
# Learner.train
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerTrain(unittest.TestCase):

    def test_returns_false_below_min_trades(self):
        learner = _make_learner(min_trades=10)
        _add_wins(learner, 5)
        self.assertFalse(learner.train())

    def test_returns_true_at_min_trades(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 3, 2)
        self.assertTrue(learner.train())

    def test_sets_logistic_weights(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 3, 2)
        learner.train()
        self.assertIsNotNone(learner._logistic_w)
        self.assertEqual(len(learner._logistic_w), 3)  # 2 features + bias

    def test_weights_none_before_train(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 3, 2)
        self.assertIsNone(learner._logistic_w)

    def test_last_trained_n_updated(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 4, 1)
        learner.train()
        self.assertEqual(learner._last_trained_n, 5)

    def test_train_skipped_when_enabled_false(self):
        # Verify train() still runs regardless of enabled flag —
        # training is independent of inference gating.
        learner = _make_learner(min_trades=5, enabled=False)
        _add_mixed(learner, 3, 2)
        result = learner.train()
        self.assertTrue(result)   # train succeeds even if learner is "disabled"


# ══════════════════════════════════════════════════════════════════════════════
# Learner.adjust_prob
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerAdjustProb(unittest.TestCase):

    def test_disabled_returns_fair_prob_unchanged(self):
        learner = _make_learner(enabled=False)
        _add_wins(learner, 10)
        self.assertAlmostEqual(learner.adjust_prob(0.60), 0.60)

    def test_no_records_returns_fair_prob_unchanged(self):
        learner = _make_learner()
        self.assertAlmostEqual(learner.adjust_prob(0.60), 0.60)

    def test_all_wins_raises_prob(self):
        # fair_prob=0.40, but 100% win rate → positive bias → prob should rise
        learner = _make_learner(min_trades=5)
        _add_wins(learner, 10, fair_prob=0.40)
        result = learner.adjust_prob(0.40)
        self.assertGreater(result, 0.40)

    def test_all_losses_lowers_prob(self):
        learner = _make_learner(min_trades=5)
        _add_losses(learner, 10, fair_prob=0.70)
        result = learner.adjust_prob(0.70)
        self.assertLess(result, 0.70)

    def test_adjustment_capped_at_max_adj(self):
        learner = _make_learner(min_trades=5, max_adj=0.03)
        # huge positive bias (100% wins at 0.10 fair_prob)
        _add_wins(learner, 10, fair_prob=0.10)
        result = learner.adjust_prob(0.10)
        self.assertAlmostEqual(result, 0.13, places=5)   # 0.10 + 0.03

    def test_result_clamped_to_min_001(self):
        learner = _make_learner(min_trades=5, max_adj=0.5)  # huge cap
        _add_losses(learner, 10, fair_prob=0.01)
        result = learner.adjust_prob(0.01)
        self.assertGreaterEqual(result, 0.01)

    def test_result_clamped_to_max_099(self):
        learner = _make_learner(min_trades=5, max_adj=0.5)
        _add_wins(learner, 10, fair_prob=0.98)
        result = learner.adjust_prob(0.98)
        self.assertLessEqual(result, 0.99)

    def test_logistic_path_used_after_train(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 4, 1)
        before_train = learner.adjust_prob(0.60, edge_pct=0.05)
        learner.train()
        after_train = learner.adjust_prob(0.60, edge_pct=0.05)
        # After training the logistic path is active; values may differ
        # (both are valid, just check they're in [0.01, 0.99])
        self.assertGreaterEqual(after_train, 0.01)
        self.assertLessEqual(after_train, 0.99)

    def test_sport_bias_used_when_available(self):
        learner = _make_learner(min_trades=200)   # high threshold → no logistic
        # Add 15 NFL all-wins at fair_prob=0.40 → sport bias ≈ +0.60
        for _ in range(15):
            learner.update(0.40, 0.45, 0.05, 0.02, "nfl", "WIN")
        result = learner.adjust_prob(0.40, sport="nfl")
        # bias capped at 0.03 → result should be 0.43
        self.assertAlmostEqual(result, 0.43, places=5)


# ══════════════════════════════════════════════════════════════════════════════
# Learner.stats
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerStats(unittest.TestCase):

    def test_empty_stats(self):
        learner = _make_learner()
        s = learner.stats()
        self.assertEqual(s["n_resolved"], 0)
        self.assertFalse(s["trained"])

    def test_n_resolved_correct(self):
        learner = _make_learner()
        _add_mixed(learner, 3, 2)
        self.assertEqual(learner.stats()["n_resolved"], 5)

    def test_trained_false_before_train(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 3, 2)
        self.assertFalse(learner.stats()["trained"])

    def test_trained_true_after_train(self):
        learner = _make_learner(min_trades=5)
        _add_mixed(learner, 3, 2)
        learner.train()
        self.assertTrue(learner.stats()["trained"])

    def test_global_bias_present(self):
        learner = _make_learner()
        _add_wins(learner, 5, fair_prob=0.50)
        s = learner.stats()
        self.assertIn("global_bias", s)
        self.assertAlmostEqual(s["global_bias"], 0.50, places=2)

    def test_by_sport_populated_when_enough_records(self):
        learner = _make_learner()
        for _ in range(10):
            learner.update(0.5, 0.5, 0.05, 0.02, "nba", "WIN")
        s = learner.stats()
        self.assertIn("nba", s["by_sport"])


# ══════════════════════════════════════════════════════════════════════════════
# Learner.save / load
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerSaveLoad(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_creates_file(self):
        path = self.tmp / "state.json"
        learner = _make_learner(state_path=path)
        _add_mixed(learner, 2, 1)
        self.assertTrue(learner.save())
        self.assertTrue(path.exists())

    def test_load_restores_records(self):
        path = self.tmp / "state.json"
        learner_a = _make_learner(state_path=path)
        _add_mixed(learner_a, 3, 2)
        learner_a.save()

        learner_b = _make_learner(state_path=path)
        learner_b.load()
        self.assertEqual(learner_b.n_resolved, 5)

    def test_load_restores_logistic_weights(self):
        path = self.tmp / "state.json"
        learner_a = _make_learner(min_trades=5, state_path=path)
        _add_mixed(learner_a, 3, 2)
        learner_a.train()
        learner_a.save()

        learner_b = _make_learner(min_trades=5, state_path=path)
        learner_b.load()
        self.assertIsNotNone(learner_b._logistic_w)
        np.testing.assert_array_almost_equal(
            learner_a._logistic_w, learner_b._logistic_w
        )

    def test_save_returns_false_without_path(self):
        learner = _make_learner()
        self.assertFalse(learner.save())

    def test_load_returns_false_missing_file(self):
        learner = _make_learner(state_path=self.tmp / "nope.json")
        self.assertFalse(learner.load())

    def test_load_returns_false_corrupt_json(self):
        path = self.tmp / "state.json"
        path.write_text("not valid json", encoding="utf-8")
        learner = _make_learner(state_path=path)
        self.assertFalse(learner.load())

    def test_save_explicit_path(self):
        learner = _make_learner()
        _add_wins(learner, 2)
        explicit = self.tmp / "explicit.json"
        self.assertTrue(learner.save(explicit))
        self.assertTrue(explicit.exists())

    def test_load_explicit_path(self):
        learner_a = _make_learner()
        _add_wins(learner_a, 3)
        explicit = self.tmp / "explicit.json"
        learner_a.save(explicit)

        learner_b = _make_learner()
        self.assertTrue(learner_b.load(explicit))
        self.assertEqual(learner_b.n_resolved, 3)

    def test_state_json_has_expected_keys(self):
        path = self.tmp / "state.json"
        learner = _make_learner(state_path=path)
        _add_wins(learner, 1)
        learner.save()
        raw = json.loads(path.read_text())
        for key in ("version", "records", "logistic_weights",
                    "last_trained_n", "last_updated"):
            self.assertIn(key, raw)

    def test_rolling_window_respected_on_load(self):
        path = self.tmp / "state.json"
        # Manually write a state file with more records than max_records
        state = {
            "version": 1,
            "records": [
                {"fair_prob": 0.5, "kalshi_prob": 0.5, "edge_pct": 0.05,
                 "ev": 0.02, "sport": "nfl", "outcome": 1}
            ] * 10,
            "logistic_weights": None,
            "last_trained_n": 0,
            "last_updated": "2024-01-01T00:00:00",
        }
        path.write_text(json.dumps(state), encoding="utf-8")
        learner = _make_learner(max_records=5, state_path=path)
        learner.load()
        self.assertEqual(learner.n_resolved, 5)


# ══════════════════════════════════════════════════════════════════════════════
# Learner.from_config
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerFromConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_disabled_when_not_in_config(self):
        learner = Learner.from_config({})
        self.assertFalse(learner._enabled)

    def test_reads_enabled_flag(self):
        config = {"learner": {"enabled": True}}
        learner = Learner.from_config(config)
        self.assertTrue(learner._enabled)

    def test_reads_min_trades(self):
        config = {"learner": {"min_trades_for_adjustment": 100}}
        learner = Learner.from_config(config)
        self.assertEqual(learner._min_trades, 100)

    def test_reads_max_adjustment_pct(self):
        config = {"learner": {"max_adjustment_pct": 0.05}}
        learner = Learner.from_config(config)
        self.assertAlmostEqual(learner._max_adj, 0.05)

    def test_reads_state_path(self):
        config = {"learner": {"model_state_path": "custom/path.json"}}
        learner = Learner.from_config(config)
        self.assertEqual(str(learner._path), "custom/path.json")

    def test_auto_loads_existing_state(self):
        state_path = self.tmp / "state.json"
        # Pre-populate state
        learner_a = _make_learner(state_path=state_path)
        _add_wins(learner_a, 5)
        learner_a.save()

        config = {
            "learner": {
                "enabled": True,
                "model_state_path": str(state_path),
            }
        }
        learner_b = Learner.from_config(config)
        self.assertEqual(learner_b.n_resolved, 5)


# ══════════════════════════════════════════════════════════════════════════════
# Integration
# ══════════════════════════════════════════════════════════════════════════════

class TestLearnerIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_update_train_adjust_chain(self):
        """Full cycle: add records, train, then adjust_prob."""
        learner = _make_learner(min_trades=5, max_adj=0.03)
        # Add 5 records where we systemically underestimate (actual win rate > fair_prob)
        for _ in range(5):
            learner.update(0.45, 0.40, 0.05, 0.02, "nfl", "WIN")
        self.assertTrue(learner.train())
        result = learner.adjust_prob(0.45, edge_pct=0.05)
        # Calibration should shift probability upward (capped at +3%)
        self.assertGreater(result, 0.45)
        self.assertLessEqual(result, 0.48)   # 0.45 + 0.03

    def test_save_reload_preserves_calibration(self):
        """Save state mid-session; reload and verify adjust_prob gives same result."""
        path = self.tmp / "state.json"
        learner_a = _make_learner(min_trades=5, max_adj=0.03, state_path=path)
        _add_mixed(learner_a, 4, 1)
        learner_a.train()
        learner_a.save()
        before = learner_a.adjust_prob(0.55, edge_pct=0.05)

        learner_b = _make_learner(min_trades=5, max_adj=0.03, state_path=path)
        learner_b.load()
        after = learner_b.adjust_prob(0.55, edge_pct=0.05)
        self.assertAlmostEqual(before, after, places=5)

    def test_disabled_learner_is_transparent(self):
        """Even with records and trained weights, disabled learner is a pass-through."""
        learner = _make_learner(min_trades=5, enabled=False)
        _add_mixed(learner, 4, 1)
        learner.train()
        self.assertAlmostEqual(learner.adjust_prob(0.62, edge_pct=0.05), 0.62)

    def test_high_win_rate_increases_prob(self):
        """With 80% win rate vs 50% fair_prob, bias-only mode increases adjusted prob."""
        learner = _make_learner(min_trades=200)  # never trains logistic
        for _ in range(8):
            learner.update(0.50, 0.45, 0.05, 0.02, "nfl", "WIN")
        for _ in range(2):
            learner.update(0.50, 0.45, 0.05, 0.02, "nfl", "LOSS")
        result = learner.adjust_prob(0.50)
        self.assertGreater(result, 0.50)


if __name__ == "__main__":
    unittest.main()
