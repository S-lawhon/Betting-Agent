"""
tests/test_void_unsettleable.py
───────────────────────────────
Tests for `scripts/void_unsettleable_positions.py`.

This script writes terminal rows into live trade history, so the properties that
matter are the safety ones: it can only ever touch two pods, it refuses on any
count it was not told to expect, and it sets VOID in **both** fields — setting
one and not the other is exactly how the JDAY correction silently failed its
first pass (`p017_checkpoint` keys off `action`, trade_store off `outcome`).
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SPEC = importlib.util.spec_from_file_location(
    "void_unsettleable",
    Path(__file__).resolve().parent.parent / "scripts"
    / "void_unsettleable_positions.py")
vu = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vu)

SRC = {"pod_name": "Cross-Venue Arb", "mode": "paper", "venue": "polymarket",
       "side": "YES", "position_size_usd": 75.0, "fill_price": 0.5,
       "event": "Wild vs. Blackhawks"}


def test_allow_list_is_exactly_two_shelved_pods():
    """No other pod can be reached by this script."""
    assert vu.PODS == ("P-002", "P-006")


def test_void_row_sets_BOTH_action_and_outcome():
    """The regression from the JDAY correction: consumers disagree about which
    field they read, so a void that sets only one is invisible to half of them."""
    r = vu.void_row("P-002", "0xabc", SRC, "2026-07-27T20:58:07+00:00")
    assert r["action"] == "VOID"
    assert r["outcome"] == "VOID"
    assert r["result"] == "void"


def test_void_books_no_pnl():
    """Every gate statistic in the repo excludes VOIDs as 'no risk taken', so a
    void must not carry P&L into any of them."""
    r = vu.void_row("P-006", "0xabc", SRC, "2026-07-27T20:58:07+00:00")
    assert r["pnl_usd"] == 0.0


def test_void_row_is_self_describing():
    """A future reader must be able to tell this from a real settlement."""
    r = vu.void_row("P-002", "0xabc", SRC, "2026-07-27T20:58:07+00:00")
    assert r["resolution_source"] == "bookkeeping_void_unsettleable"
    assert "never resolve" in r["void_reason"]
    assert "REPORT_Corrections" in r["void_reason"]


def test_void_row_preserves_the_original_position_details():
    r = vu.void_row("P-002", "0xabc", SRC, "2026-07-27T20:58:07+00:00")
    assert r["position_size_usd"] == 75.0
    assert r["fill_price"] == 0.5
    assert r["venue"] == "polymarket"
    assert r["market_id"] == r["market_ticker"] == "0xabc"


def test_terminal_outcomes_include_void_so_reruns_are_no_ops():
    """A row this script wrote must itself count as terminal, or a second run
    would void the same position again."""
    assert "VOID" in vu.TERMINAL
