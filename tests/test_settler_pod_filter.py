"""
tests/test_settler_pod_filter.py
────────────────────────────────
Tests for the pod_ids filtering in Settler.rebuild_ledger_from_log().

Verifies that P-001's Ledger only counts its own positions and is not
contaminated by positions from other pods (P-002, P-014, etc.).
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Legacy" / "Kalshi Arb Project" / "src"))

from risk_manager import Ledger, Position
from settler import Settler


FIXED_NOW = datetime(2026, 4, 3, 12, 0, 0, tzinfo=timezone.utc)


def _placed_entry(ticker, pod_id=None, venue="kalshi", size=10.0,
                  fingerprint=None, sport="basketball_nba"):
    """Build a minimal PLACED trade-log entry."""
    entry = {
        "action": "PLACED",
        "market_ticker": ticker,
        "side": "YES",
        "position_size_usd": size,
        "kalshi_prob": 0.50,
        "timestamp_utc": "2026-04-02T20:00:00+00:00",
        "sport": sport,
        "event": "Team A vs Team B",
        "fingerprint": fingerprint or ticker,
    }
    if pod_id is not None:
        entry["pod_id"] = pod_id
    if venue:
        entry["venue"] = venue
    return entry


def _settled_entry(ticker, action="WIN", fingerprint=None):
    """Build a WIN/LOSS/VOID settlement entry."""
    return {
        "action": action,
        "outcome": action,       # Settler resolves on 'outcome' field
        "market_ticker": ticker,
        "fingerprint": fingerprint or ticker,
        "timestamp_utc": "2026-04-03T01:00:00+00:00",
    }


def _write_log(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_settler(log_path, ledger=None):
    if ledger is None:
        ledger = Ledger(initial_bankroll=1000.0, _now_fn=lambda: FIXED_NOW)
    kalshi = MagicMock()
    return Settler(
        kalshi=kalshi,
        ledger=ledger,
        log_path=log_path,
        odds_api_key="test-key",
        _now_fn=lambda: FIXED_NOW,
    )


class TestRebuildLedgerPodFilter(unittest.TestCase):
    """Test that rebuild_ledger_from_log(pod_ids=...) filters correctly."""

    def test_no_filter_loads_all_pods(self):
        """Without pod_ids, all Kalshi positions are loaded (backward compat)."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-NBA-001", pod_id="P-001"),
            _placed_entry("KX-NBA-002", pod_id="P-002"),
            _placed_entry("KX-NBA-003", pod_id="P-014"),
            _placed_entry("KX-NBA-004"),  # legacy, no pod_id
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        count = settler.rebuild_ledger_from_log()  # no pod_ids filter

        self.assertEqual(count, 4)
        self.assertEqual(settler._ledger.open_count, 4)

    def test_pod_filter_only_loads_matching(self):
        """With pod_ids=('P-001', ''), only P-001 and legacy entries load."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-NBA-001", pod_id="P-001"),
            _placed_entry("KX-NBA-002", pod_id="P-001"),
            _placed_entry("KX-ARB-001", pod_id="P-002"),
            _placed_entry("KX-ARB-002", pod_id="P-002"),
            _placed_entry("KX-ARB-003", pod_id="P-002"),
            _placed_entry("KX-LIVE-001", pod_id="P-014"),
            _placed_entry("KX-LEGACY-001"),  # no pod_id
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        count = settler.rebuild_ledger_from_log(pod_ids=("P-001", ""))

        # Should only load P-001 (2) + legacy (1) = 3
        self.assertEqual(count, 3)
        self.assertEqual(settler._ledger.open_count, 3)

    def test_pod_filter_excludes_p002_arb_legs(self):
        """P-002 Kalshi arb legs must not inflate P-001's position count."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-NBA-001", pod_id="P-001"),
            # P-002 arb legs on Kalshi
            _placed_entry("KX-ARB-001", pod_id="P-002"),
            _placed_entry("KX-ARB-002", pod_id="P-002"),
            _placed_entry("KX-ARB-003", pod_id="P-002"),
            _placed_entry("KX-ARB-004", pod_id="P-002"),
            _placed_entry("KX-ARB-005", pod_id="P-002"),
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        count = settler.rebuild_ledger_from_log(pod_ids=("P-001", ""))

        self.assertEqual(count, 1)
        self.assertEqual(settler._ledger.open_count, 1)

    def test_pod_filter_with_settled_trades(self):
        """Settled P-001 trades should not be loaded even with filter."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-NBA-001", pod_id="P-001", fingerprint="fp-001"),
            _placed_entry("KX-NBA-002", pod_id="P-001", fingerprint="fp-002"),
            _settled_entry("KX-NBA-001", action="WIN", fingerprint="fp-001"),
            _placed_entry("KX-ARB-001", pod_id="P-002", fingerprint="fp-arb"),
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        count = settler.rebuild_ledger_from_log(pod_ids=("P-001", ""))

        # Only 1 unsettled P-001 trade (fp-002); fp-001 was settled
        self.assertEqual(count, 1)
        self.assertEqual(settler._ledger.open_count, 1)

    def test_pod_filter_still_skips_polymarket(self):
        """Non-Kalshi venues are still excluded regardless of pod_ids."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-NBA-001", pod_id="P-001"),
            _placed_entry("PM-NBA-001", pod_id="P-001", venue="polymarket"),
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        count = settler.rebuild_ledger_from_log(pod_ids=("P-001", ""))

        # Polymarket entry skipped (venue filter), only Kalshi loaded
        self.assertEqual(count, 1)

    def test_settle_cycle_still_sees_all_pods(self):
        """settle_cycle must still check ALL pods for settlement."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-NBA-001", pod_id="P-001", fingerprint="fp-001"),
            _placed_entry("KX-ARB-001", pod_id="P-002", fingerprint="fp-002"),
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        # Rebuild with filter (only P-001 in ledger)
        settler.rebuild_ledger_from_log(pod_ids=("P-001", ""))

        # _open_placed_entries (used by settle_cycle) should still
        # return ALL unsettled entries
        open_entries = settler._open_placed_entries()
        self.assertEqual(len(open_entries), 2)

    def test_empty_string_pod_id_matches_legacy(self):
        """Entries with no pod_id field should match pod_ids=('',)."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)

        entries = [
            _placed_entry("KX-LEGACY-001"),  # no pod_id key at all
            _placed_entry("KX-P001-001", pod_id="P-001"),
        ]
        _write_log(log_path, entries)

        settler = _make_settler(log_path)
        count = settler.rebuild_ledger_from_log(pod_ids=("",))

        # Only legacy entry matches
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()


class TestSettleCyclePodFilter(unittest.TestCase):
    """The settle path must respect pod_ids, not just the ledger rebuild.

    Regression for a live incident on 2026-07-26. ``rebuild_ledger_from_log``
    had always taken ``pod_ids``, but ``_open_placed_entries`` — which is what
    ``settle_cycle`` walks — did not. The generic settler therefore processed
    every open row in the log, including pods with a purpose-built settler.

    It runs first in the engine cycle, so it won the race against
    ``KalshiGolfSettler`` and voided 16 recovered P-017 golf positions at
    $0.00 seconds after a restart: Kalshi leaves top-N markets
    ``status="active"`` with an empty ``result`` for ~a day post-tournament,
    and this settler reads "no result yet" as "void". That is precisely the
    rule ``KalshiGolfSettler`` exists to refuse.

    The gap stayed invisible for as long as those rows were orphaned out of
    the active log by the truncating rotation; recovering them exposed it.
    """

    def _log_with_mixed_pods(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            log_path = Path(f.name)
        _write_log(log_path, [
            _placed_entry("KXMLBGAME-26JUL26-NYY", pod_id="P-001"),
            _placed_entry("KXNBA-LEGACY-001"),  # legacy, no pod_id
            _placed_entry("KXPGATOP10-3MO26-ASMO", pod_id="P-017"),
            _placed_entry("KXPGATOP20-3MO26-LGRI", pod_id="P-017"),
            _placed_entry("KXATPMATCH-26JUL26-SVA", pod_id="P-015"),
        ])
        return log_path

    def test_open_entries_scoped_to_pod_ids(self):
        log_path = self._log_with_mixed_pods()
        settler = Settler(
            kalshi=MagicMock(), ledger=Ledger(initial_bankroll=1000.0,
                                              _now_fn=lambda: FIXED_NOW),
            log_path=log_path, odds_api_key="k",
            _now_fn=lambda: FIXED_NOW, pod_ids=("P-001", ""),
        )
        pods = {r.get("pod_id", "") for r in settler._open_placed_entries().values()}
        self.assertEqual(pods, {"P-001", ""})

    def test_golf_positions_are_not_reachable_by_the_generic_settler(self):
        """The specific rows that were voided in production."""
        log_path = self._log_with_mixed_pods()
        settler = Settler(
            kalshi=MagicMock(), ledger=Ledger(initial_bankroll=1000.0,
                                              _now_fn=lambda: FIXED_NOW),
            log_path=log_path, odds_api_key="k",
            _now_fn=lambda: FIXED_NOW, pod_ids=("P-001", ""),
        )
        tickers = {r.get("market_ticker") for r in settler._open_placed_entries().values()}
        self.assertNotIn("KXPGATOP10-3MO26-ASMO", tickers)
        self.assertNotIn("KXPGATOP20-3MO26-LGRI", tickers)
        self.assertNotIn("KXATPMATCH-26JUL26-SVA", tickers)

    def test_settle_cycle_never_touches_a_filtered_pod(self):
        """End-to-end: an unresolved golf market must not produce a VOID.

        The market is `active` with an empty result — the production shape.
        """
        log_path = self._log_with_mixed_pods()
        kalshi = MagicMock()
        kalshi.get_market.return_value = {"status": "active", "result": ""}
        settler = Settler(
            kalshi=kalshi, ledger=Ledger(initial_bankroll=1000.0,
                                         _now_fn=lambda: FIXED_NOW),
            log_path=log_path, odds_api_key="k",
            _now_fn=lambda: FIXED_NOW, pod_ids=("P-001", ""),
        )
        settled = settler.settle_cycle()
        touched = {r.get("market_ticker") for r in settled}
        self.assertNotIn("KXPGATOP10-3MO26-ASMO", touched)
        self.assertNotIn("KXPGATOP20-3MO26-LGRI", touched)

    def test_unfiltered_settler_still_sees_everything(self):
        """Backward compatibility: pod_ids=None keeps the old behaviour."""
        log_path = self._log_with_mixed_pods()
        settler = Settler(
            kalshi=MagicMock(), ledger=Ledger(initial_bankroll=1000.0,
                                              _now_fn=lambda: FIXED_NOW),
            log_path=log_path, odds_api_key="k", _now_fn=lambda: FIXED_NOW,
        )
        self.assertEqual(len(settler._open_placed_entries()), 5)
