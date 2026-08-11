"""
tests/test_manager_oneshot_service.py
─────────────────────────────────────
Coverage for `oneshot: true` service entries in checks.check_services.

A timer-driven oneshot unit (research-agent-screened-daily) rests at
ActiveState=inactive between runs, so the long-running down-check would page
it CRITICAL every quiet hour. Its one bad state is "failed", which systemd
latches until reset-failed. Observed 2026-08-10: the unit sat failed for 13+
hours while the manager reported all-nominal, because the only registered
check watched an output file the hourly dry-run worker also touches.

The contract under test:
  - oneshot + inactive  -> silent (resting state, not a fault)
  - oneshot + activating/active -> silent (mid-run)
  - oneshot + failed    -> pages at the entry's configured severity
  - oneshot + not-found -> the existing not-installed warn, not .failed
  - non-oneshot units keep the original down-check behavior
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manager"))

import checks  # noqa: E402


def _svc(active, *, oneshot=True, load="loaded", severity="warn"):
    return {
        "id": "research-agent-screened-daily",
        "active": active,
        "load": load,
        "oneshot": oneshot,
        "severity": severity,
        "description": "daily screened research pass",
    }


def _findings(svc):
    return checks.check_services({"services": [svc]})


def _by_key(findings, key):
    return next((f for f in findings if f.key == key), None)


class TestOneshotServiceCheck(TestCase):
    def test_inactive_between_runs_is_silent(self):
        self.assertEqual(_findings(_svc("inactive")), [])

    def test_mid_run_states_are_silent(self):
        for state in ("activating", "active"):
            self.assertEqual(_findings(_svc(state)), [], state)

    def test_failed_pages_at_configured_severity(self):
        f = _by_key(_findings(_svc("failed")),
                    "service.research-agent-screened-daily.failed")
        self.assertIsNotNone(f, "latched failed state must produce a finding")
        self.assertEqual(f.severity, "warn")

    def test_not_installed_wins_over_failed_check(self):
        findings = _findings(_svc("inactive", load="not-found"))
        self.assertIsNotNone(_by_key(
            findings, "service.research-agent-screened-daily.not_installed"))
        self.assertIsNone(_by_key(
            findings, "service.research-agent-screened-daily.failed"))

    def test_non_oneshot_inactive_still_pages_down(self):
        f = _by_key(_findings(_svc("inactive", oneshot=False)),
                    "service.research-agent-screened-daily.down")
        self.assertIsNotNone(f, "long-running units must keep the down-check")
        self.assertEqual(f.severity, "critical")
