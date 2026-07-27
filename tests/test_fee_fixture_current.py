"""
tests/test_fee_fixture_current.py
─────────────────────────────────
Two halves, deliberately separated:

  * **Offline (always runs).** Structural properties of the committed fixture
    and of the matching rule: no ambiguity, no collisions, no stale
    pre-registrations, and the exact cases that were wrong in production.
  * **Online (opt-in).** Regenerate against live `/series` and assert no diff.
    This is the check that catches *Kalshi* moving, which is the event this
    project keeps missing — the fee table drifted five times and every one was
    found by accident, downstream of a verdict already written.

Run the online half:

    KALSHI_FEE_CHECK=1 python3 -m pytest tests/test_fee_fixture_current.py -q

or, equivalently, `python3 -m scripts.check_fee_fixture` (exit 1 on drift).

**A network failure inside the online test is a FAILURE, not a skip.** A
silently-skipped check is exactly the failure mode already in place.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_fees import (_FIXTURE_PATH, _PENDING_SERIES, _fixture,
                             fee_per_contract, series_maker_charges_fee)

ONLINE = os.environ.get("KALSHI_FEE_CHECK") == "1"


# ── offline: the fixture itself ──────────────────────────────────────

def test_fixture_exists_and_is_populated():
    data = json.loads(_FIXTURE_PATH.read_text())
    assert data["n_series"] > 5_000
    assert 1 <= len(data["maker_fee_series"]) < data["n_series"] // 10
    assert data["unknown_fee_types"] == [], (
        "Kalshi has a fee_type this model does not know: "
        f"{data['unknown_fee_types']}")


def test_fixture_has_no_duplicate_tickers():
    data = json.loads(_FIXTURE_PATH.read_text())
    for key in ("all_series", "maker_fee_series", "zero_fee_multiplier_series"):
        assert len(data[key]) == len(set(data[key])), key


def test_every_charging_series_is_in_the_inventory():
    known, charging = _fixture()
    assert charging <= known


# ── offline: the matching rule cannot collide ────────────────────────

def test_matching_rule_is_unambiguous_across_the_full_inventory():
    """The whole point of moving off prefixes.

    Under longest-prefix matching, two entries could both match one ticker and
    the answer depended on which was longer — which is how `KXPGATOP` failed to
    cover `KXPGAR1TOP5` (shared prefix only `KXPGA`, which charges) and how
    `KXMLB` < `KXMLBHR` < `KXMLBHRDERBY` < `KXMLBHRDERBYR1LEAD` came to
    alternate charge/free/charge/free.

    Exact matching admits exactly one rule per ticker by construction. This
    test proves the property rather than asserting the intention: every series
    in the inventory resolves to precisely one classification, and resolving a
    full market ticker gives the same answer as its series ticker alone.
    """
    known, charging = _fixture()
    for ticker in sorted(known):
        expected = ticker in charging
        assert series_maker_charges_fee(ticker) is expected, ticker


def test_hyphenated_series_cannot_collide_with_their_leading_segment():
    """The one way exact matching could still go wrong.

    148 series tickers contain a hyphen (`KXMLBWINS-MIL`, `KXNFLWINS-SF`,
    `KXWO-GOLD`), and 86 of those have a leading segment that is ALSO a series.
    Resolution tries the full string first and the leading segment second, so
    those 86 are only safe while the pair agrees on fee type. Today all 86
    agree. If Kalshi ever creates a pair that does not, this fails — which is
    the whole point of writing it as a property rather than a comment.
    """
    known, charging = _fixture()
    disagree = sorted(
        (t, t.split("-")[0]) for t in known
        if "-" in t and t.split("-")[0] in known
        and (t in charging) != (t.split("-")[0] in charging))
    assert disagree == [], (
        "a hyphenated series and its leading segment now disagree on maker "
        f"fee, so falling back to the segment is unsafe: {disagree}")


def test_a_full_market_ticker_resolves_to_its_series():
    known, charging = _fixture()
    for ticker in sorted(t for t in known if "-" not in t)[:1500]:
        expected = ticker in charging
        assert series_maker_charges_fee(f"{ticker}-26JUL-XYZ") is expected, ticker


def test_no_series_is_a_prefix_ambiguity_any_more():
    """The four historical prefix traps, asserted directly."""
    cases = {
        "KXPGA": True, "KXPGATOP20": False, "KXPGAR1TOP5": False,
        "KXPGARYDER": True, "KXPGARYDERTOP3": False,
        "KXMLB": True, "KXMLBHR": False, "KXMLBHRDERBY": True,
        "KXMLBHRDERBYR1LEAD": False,
        "KXLIVTOUR": False, "KXLIVTOP5": False,
        "KXCHAMPTOUR": False, "KXCHAMPTOURR1LEAD": False,
        "KXDPWORLDTOURMAKECUT": False,
    }
    for ticker, charges in cases.items():
        assert series_maker_charges_fee(ticker) is charges, ticker


def test_unknown_series_is_charged_conservatively():
    """Over-charging only makes the net-edge gate stricter. Under-charging
    inflates every verdict computed against it."""
    assert series_maker_charges_fee("KXTOTALLYMADEUP") is True
    assert series_maker_charges_fee("") is True
    assert fee_per_contract(0.5, maker=True,
                            series_ticker="KXTOTALLYMADEUP") > 0


def test_pending_series_expire_once_kalshi_lists_them():
    """`_PENDING_SERIES` is the only hand-written fee data left, and it is
    only legitimate while the ticker does not exist. The moment Kalshi lists
    one, the fixture is authoritative and the entry must be deleted — or it
    rots into a second hand-maintained table, which is the thing being
    removed."""
    known, _ = _fixture()
    stale = sorted(set(_PENDING_SERIES) & known)
    assert stale == [], (
        f"these are listed now — delete them from _PENDING_SERIES: {stale}")


# ── online: does the committed fixture still match Kalshi? ───────────

@pytest.mark.skipif(
    not ONLINE,
    reason="opt-in network check — run with KALSHI_FEE_CHECK=1, or "
           "`python3 -m scripts.check_fee_fixture`")
def test_committed_fixture_matches_live_kalshi():
    from scripts.check_fee_fixture import check
    # No try/except: a network failure must FAIL this test. A check that
    # passes when it could not run is worse than no check.
    r = check()
    assert not r["drift"], (
        "Kalshi's maker-fee schedule has moved away from the committed "
        f"fixture — every net-edge number computed since "
        f"{r.get('fixture_generated_at')} is suspect.\n"
        f"  started charging : {r['started_charging']}\n"
        f"  stopped charging : {r['stopped_charging']}\n"
        f"  new + charging   : {r['new_series_that_charge']}\n"
        f"  unknown fee_types: {r['unknown_fee_types']}\n"
        f"  pending now live : {r['pending_now_listed']}\n"
        "Fix: python3 -m scripts.generate_fee_fixture   (then commit)")
