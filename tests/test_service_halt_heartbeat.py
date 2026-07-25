"""
tests/test_service_halt_heartbeat.py
────────────────────────────────────
Coverage for the daily-loss-halt exception in checks.check_services.

The heartbeat check treats a stale trade_log.jsonl as a wedged loop and pages
CRITICAL. But when the aggregate-risk guard trips the daily-loss limit it skips
the whole scan cycle — no telemetry rows are written, so the file goes stale
BY DESIGN while the loop is alive and logging a "guard halted" line every cycle
(observed 2026-07-25: a P-017 golf tournament settled -19.7% and halted trading
for the rest of the UTC day, paging a false wedged-loop CRITICAL).

The contract under test:
  - stale file + FRESH halt line   -> demote CRITICAL to an info note.
  - stale file + STALE halt line    -> genuine wedge, CRITICAL still fires.
  - stale file + no halt info        -> unchanged CRITICAL (back-compat).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manager"))

import checks  # noqa: E402


def _svc(halt=None):
    """A stale-heartbeat service snapshot, optionally with a halt block."""
    hb = {
        "file": "data/trade_logs/trade_log.jsonl",
        "age_minutes": 640.0,          # ~11h stale, far past the 20m limit
        "max_stale_minutes": 20,
    }
    if halt is not None:
        hb["halt"] = halt
    return {
        "id": "betting-pod-shop",
        "active": "active",
        "description": "engine",
        "heartbeat": hb,
    }


def _snap(svc):
    return {"services": [svc]}


def _keys(findings):
    return {f.key for f in findings}


def _by_key(findings, key):
    return next((f for f in findings if f.key == key), None)


class TestHaltSuppressesWedgedLoopCritical(TestCase):
    def test_fresh_halt_demotes_critical_to_info(self):
        f = checks.check_services(_snap(_svc(halt={
            "halted": True,
            "halt_age_minutes": 3.0,
            "max_silent_journal_minutes": 12,
            "reason": "guard halted (daily_loss_limit: 19.7%)",
        })))
        self.assertNotIn("service.betting-pod-shop.stale", _keys(f))
        note = _by_key(f, "service.betting-pod-shop.halted")
        self.assertIsNotNone(note, "expected an info note in place of the CRITICAL")
        self.assertEqual(note.severity, "info")

    def test_stale_halt_line_still_pages_critical(self):
        # Loop wedged WHILE halted: the halt line ages out past the limit, so
        # the safety property must hold — this is a real wedge.
        f = checks.check_services(_snap(_svc(halt={
            "halted": False,
            "halt_age_minutes": 45.0,
            "max_silent_journal_minutes": 12,
            "reason": "guard halted (daily_loss_limit: 19.7%)",
        })))
        stale = _by_key(f, "service.betting-pod-shop.stale")
        self.assertIsNotNone(stale)
        self.assertEqual(stale.severity, "critical")

    def test_no_halt_block_is_unchanged_critical(self):
        # Back-compat: a service without halt_signal wiring behaves as before.
        f = checks.check_services(_snap(_svc(halt=None)))
        stale = _by_key(f, "service.betting-pod-shop.stale")
        self.assertIsNotNone(stale)
        self.assertEqual(stale.severity, "critical")

    def test_halted_none_unknown_does_not_suppress(self):
        # journal unavailable (e.g. the Mac): halted=None must NOT mask a wedge.
        f = checks.check_services(_snap(_svc(halt={
            "halted": None,
            "halt_age_minutes": None,
            "max_silent_journal_minutes": 12,
        })))
        stale = _by_key(f, "service.betting-pod-shop.stale")
        self.assertIsNotNone(stale)
        self.assertEqual(stale.severity, "critical")
