"""Gate progress must be DERIVED, never read from a hand-typed registry number.

The bug these cover is a real gate-integrity failure found 2026-07-26. P-017's
``manager/registry.yaml`` carried ``progress: 1``, typed by hand on the day the
pod entered its first tournament and never updated. Nothing computed it;
``checks.py`` read it straight out of the YAML. As written, the gate counted
tournaments *entered* rather than *settled* — satisfiable without a single
observation of the thing under test. At the moment it was found, the 3M Open was
counted as 1 of 8 while 16 of its 38 positions were still open and unsettleable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MANAGER = Path(__file__).resolve().parent.parent / "manager"
sys.path.insert(0, str(MANAGER))

checks = pytest.importorskip("checks")


DERIVED_GATE = {"threshold": 8, "source": "p017_checkpoint", "progress": 99}
PLAIN_GATE = {"threshold": 500, "progress": 512}


def test_derived_source_wins_over_a_stale_yaml_number():
    """A gate naming a source must ignore any hand-typed progress beside it."""
    snap = {"p017": {"available": True, "checkpoint": {"progress": 0}}}
    assert checks._gate_progress(snap, "P-017", DERIVED_GATE) == 0


def test_unavailable_reader_returns_none_rather_than_the_stale_number():
    """Failing open to the hand-typed value is what this exists to prevent."""
    snap = {"p017": {"available": False, "error": "boom"}}
    assert checks._gate_progress(snap, "P-017", DERIVED_GATE) is None


def test_missing_snapshot_block_returns_none():
    assert checks._gate_progress({}, "P-017", DERIVED_GATE) is None


def test_gate_without_a_source_still_uses_the_registry_value():
    """Backward compatibility for gates that have no reader yet."""
    assert checks._gate_progress({}, "P-016", PLAIN_GATE) == 512


def test_unknown_source_is_not_silently_trusted():
    assert checks._gate_progress({}, "P-999", {"threshold": 1,
                                               "source": "nope_checkpoint"}) is None


def test_non_numeric_progress_is_rejected():
    snap = {"p001": {"available": True, "checkpoint": {"progress": "lots"}}}
    assert checks._gate_progress(snap, "P-001",
                                 {"threshold": 200, "source": "p001_checkpoint"}) is None


def test_promotion_prompt_does_not_fire_on_a_stale_number():
    """End-to-end: the exact P-017 shape that was live before the fix.

    registry says 1 of 8 by hand; the reader says 0 settled tournaments. Neither
    clears the gate, and critically the check must consult the reader.
    """
    registry = {"workstreams": [{
        "id": "P-017", "tier": "validating",
        "gate": {"threshold": 8, "source": "p017_checkpoint", "progress": 8,
                 "question": "does it hold?"},
    }]}
    snap = {"p017": {"available": True, "checkpoint": {"progress": 0}},
            "invariants": {"config_fingerprint": {"exists": True, "active": []}},
            "services": []}
    keys = [f.key for f in checks.check_registry_reconciliation(snap, registry)]
    assert "registry.promotion_due.P-017" not in keys


def test_promotion_prompt_fires_when_the_reader_says_so():
    registry = {"workstreams": [{
        "id": "P-017", "tier": "validating",
        "gate": {"threshold": 8, "source": "p017_checkpoint", "progress": 0,
                 "question": "does it hold?"},
    }]}
    snap = {"p017": {"available": True, "checkpoint": {"progress": 8}},
            "invariants": {"config_fingerprint": {"exists": True, "active": []}},
            "services": []}
    keys = [f.key for f in checks.check_registry_reconciliation(snap, registry)]
    assert "registry.promotion_due.P-017" in keys
