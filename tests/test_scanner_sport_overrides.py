"""Per-sport edge-threshold overrides in the P-001 legacy Scanner.

Added 2026-07-30 with `edge.sport_overrides` (config_multi_pod.yaml): the
scenario-D gate needs MLB rows, but at the global 3%/1% thresholds zero MLB
candidates cleared the bar for four straight days (0 of 315 unique
ticker/side/day skips, 07-26 → 07-30). The override loosens MLB only, for
paper collection; every other sport must keep the shared calculator.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent /
                       "Legacy" / "Kalshi Arb Project" / "src"))

from edge_calculator import EdgeCalculator  # noqa: E402
from scanner import Scanner  # noqa: E402


def _make_scanner(tmp_log, overrides=None):
    edge = EdgeCalculator(min_edge_pct=0.03, min_ev=0.01)
    return Scanner(
        kalshi_client=None,
        odds_client=None,
        matcher=None,
        edge_calculator=edge,
        risk_manager=None,
        sports=["baseball_mlb", "tennis_atp_washington_open"],
        trade_log_path=tmp_log,
        sport_edge_overrides=overrides,
    )


class TestSportEdgeOverrides(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "trade_log.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_overrides_returns_shared_calculator(self):
        s = _make_scanner(self.log)
        self.assertIs(s._edge_for_sport("baseball_mlb"), s._edge)

    def test_override_applies_to_named_sport_only(self):
        s = _make_scanner(self.log, {"baseball_mlb": {"min_edge_pct": 0.01,
                                                      "min_ev": 0.0033}})
        mlb = s._edge_for_sport("baseball_mlb")
        self.assertEqual(mlb.min_edge_pct, 0.01)
        self.assertEqual(mlb.min_ev, 0.0033)
        # every other sport keeps the shared calculator, thresholds intact
        self.assertIs(s._edge_for_sport("tennis_atp_washington_open"), s._edge)
        self.assertEqual(s._edge.min_edge_pct, 0.03)

    def test_override_keeps_shared_sizing_params(self):
        s = _make_scanner(self.log, {"baseball_mlb": {"min_edge_pct": 0.01}})
        mlb = s._edge_for_sport("baseball_mlb")
        self.assertEqual(mlb.fee_pct, s._edge.fee_pct)
        self.assertEqual(mlb.kelly_fraction, s._edge.kelly_fraction)
        self.assertEqual(mlb.max_bet_pct, s._edge.max_bet_pct)
        # min_ev not named in the override falls back to the shared value
        self.assertEqual(mlb.min_ev, s._edge.min_ev)

    def test_override_calculator_is_cached(self):
        s = _make_scanner(self.log, {"baseball_mlb": {"min_edge_pct": 0.01}})
        self.assertIs(s._edge_for_sport("baseball_mlb"),
                      s._edge_for_sport("baseball_mlb"))

    def test_override_changes_best_side_outcome(self):
        # 2% raw edge: rejected by the 3% shared bar, accepted at the 1% bar.
        s = _make_scanner(self.log, {"baseball_mlb": {"min_edge_pct": 0.01,
                                                      "min_ev": 0.0}})
        fair, price = 0.42, 0.40
        self.assertIsNone(s._edge.best_side(fair, price))
        self.assertIsNotNone(s._edge_for_sport("baseball_mlb").best_side(fair, price))

    def test_fatigue_divides_the_sport_specific_thresholds(self):
        s = _make_scanner(self.log, {"baseball_mlb": {"min_edge_pct": 0.01,
                                                      "min_ev": 0.0033}})
        adj = s._adjusted_edge_calc(2.0, base=s._edge_for_sport("baseball_mlb"))
        self.assertAlmostEqual(adj.min_edge_pct, 0.005)
        # default base stays the shared calculator (pre-change behaviour)
        self.assertAlmostEqual(s._adjusted_edge_calc(2.0).min_edge_pct, 0.015)

    def test_from_config_reads_edge_sport_overrides(self):
        config = {
            "odds_api": {"sports": ["baseball_mlb"]},
            "paths": {"trade_log": str(self.log)},
            "edge": {"min_edge_pct": 0.03, "min_ev": 0.01,
                     "sport_overrides": {"baseball_mlb": {"min_edge_pct": 0.01,
                                                          "min_ev": 0.0033}}},
        }
        edge = EdgeCalculator.from_config(config)
        s = Scanner.from_config(config, None, None, None, edge, None)
        self.assertEqual(s._edge_for_sport("baseball_mlb").min_edge_pct, 0.01)


if __name__ == "__main__":
    unittest.main()
