"""
tests/test_registry_reconciliation.py
─────────────────────────────────────
Coverage for check_registry_reconciliation — does what the registry SAYS is
trading match what IS trading?

Three real misses in two days motivated this, each a confident false statement:
the registry said P-017 was "not deployed" while it traded 38 positions; it
said "nothing is scheduled" for book capture hours after the daemon was
deployed; it said the MLB props collector had no timer while a timer ran.

Design note under test: runtime config is AUTHORITATIVE and the registry is
checked against it, never the reverse. Making the registry drive pod loading
would convert documentation lag into outages.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manager"))

import checks  # noqa: E402


def _snap(active, services=None):
    return {
        "invariants": {"config_fingerprint": {"exists": True, "active": list(active)}},
        "services": services or [],
    }


def _registry(*pods):
    return {"workstreams": list(pods)}


def _keys(findings):
    return {f.key for f in findings}


class TestUnregisteredPod(TestCase):
    def test_trading_but_unregistered_is_an_error(self):
        f = checks.check_registry_reconciliation(
            _snap(["P-001", "P-099"]),
            _registry({"id": "P-001", "tier": "validating"}))
        self.assertIn("registry.unregistered.P-099", _keys(f))
        self.assertEqual(
            [x.severity for x in f if x.key.endswith("P-099")], ["error"])

    def test_registered_pods_do_not_flag(self):
        f = checks.check_registry_reconciliation(
            _snap(["P-001"]), _registry({"id": "P-001", "tier": "validating"}))
        self.assertNotIn("registry.unregistered.P-001", _keys(f))


class TestRecordedTradingButNotRunning(TestCase):
    """The P-017 miss, inverted — this is the case that actually occurred."""

    def test_validating_pod_absent_from_active_is_an_error(self):
        f = checks.check_registry_reconciliation(
            _snap(["P-001"]),
            _registry({"id": "P-017", "tier": "validating"}))
        self.assertIn("registry.not_running.P-017", _keys(f))

    def test_service_backed_pod_is_satisfied_without_pods_active(self):
        """P-016 runs as its own unit and is deliberately not in pods.active."""
        f = checks.check_registry_reconciliation(
            _snap([], services=[{"id": "betting-live-maker", "active": "active"}]),
            _registry({"id": "P-016", "tier": "validating",
                       "service": "betting-live-maker"}))
        self.assertNotIn("registry.not_running.P-016", _keys(f))

    def test_shelved_pod_absent_from_active_is_fine(self):
        f = checks.check_registry_reconciliation(
            _snap([]), _registry({"id": "P-002", "tier": "none"}))
        self.assertEqual(_keys(f), set())

    def test_production_tier_is_held_to_the_same_rule(self):
        f = checks.check_registry_reconciliation(
            _snap([]), _registry({"id": "P-001", "tier": "production"}))
        self.assertIn("registry.not_running.P-001", _keys(f))


class TestServiceDown(TestCase):
    def test_declared_service_down_is_critical(self):
        f = checks.check_registry_reconciliation(
            _snap([], services=[{"id": "betting-live-maker", "active": "failed"}]),
            _registry({"id": "P-016", "tier": "validating",
                       "service": "betting-live-maker"}))
        found = [x for x in f if x.key == "registry.service_down.P-016"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "critical")

    def test_na_host_does_not_flag(self):
        """'n/a' means not a systemd host, not a failure."""
        f = checks.check_registry_reconciliation(
            _snap([], services=[{"id": "betting-live-maker", "active": "n/a"}]),
            _registry({"id": "P-016", "tier": "validating",
                       "service": "betting-live-maker"}))
        self.assertNotIn("registry.service_down.P-016", _keys(f))


class TestPromotionDue(TestCase):
    def test_gate_reached_while_validating_prompts_promotion(self):
        f = checks.check_registry_reconciliation(
            _snap(["P-017"]),
            _registry({"id": "P-017", "tier": "validating",
                       "gate": {"threshold": 8, "progress": 8,
                                "question": "does CLV hold?"}}))
        found = [x for x in f if x.key == "registry.promotion_due.P-017"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "info")

    def test_gate_not_reached_is_silent(self):
        f = checks.check_registry_reconciliation(
            _snap(["P-017"]),
            _registry({"id": "P-017", "tier": "validating",
                       "gate": {"threshold": 8, "progress": 1}}))
        self.assertNotIn("registry.promotion_due.P-017", _keys(f))

    def test_missing_progress_is_silent_not_a_crash(self):
        f = checks.check_registry_reconciliation(
            _snap(["P-015"]),
            _registry({"id": "P-015", "tier": "validating",
                       "gate": {"threshold": 120}}))
        self.assertNotIn("registry.promotion_due.P-015", _keys(f))


class TestDegenerateInputs(TestCase):
    def test_no_fingerprint_measured_is_silent(self):
        """Absent measurement must not be reported as a discrepancy."""
        f = checks.check_registry_reconciliation(
            {"invariants": {}}, _registry({"id": "P-001", "tier": "validating"}))
        self.assertEqual(f, [])

    def test_empty_registry_with_active_pods_flags_each(self):
        f = checks.check_registry_reconciliation(_snap(["P-001", "P-002"]), {})
        self.assertEqual(
            _keys(f),
            {"registry.unregistered.P-001", "registry.unregistered.P-002"})

    def test_null_active_list_does_not_crash(self):
        snap = {"invariants": {"config_fingerprint":
                               {"exists": True, "active": None}}}
        self.assertEqual(checks.check_registry_reconciliation(snap, {}), [])


class TestAgainstShippedState(TestCase):
    """The real registry and the real config must actually agree."""

    def test_shipped_registry_and_config_reconcile(self):
        import yaml
        with open("config_multi_pod.yaml") as fh:
            cfg = yaml.safe_load(fh)
        with open("manager/registry.yaml") as fh:
            reg = yaml.safe_load(fh)
        active = (cfg.get("pods") or {}).get("active") or []
        services = [{"id": s.get("id"), "active": "active"}
                    for s in reg.get("services", [])]

        findings = checks.check_registry_reconciliation(
            _snap(active, services=services), reg)
        blocking = [f for f in findings if f.severity in ("error", "critical")]
        self.assertEqual(
            [], blocking,
            "shipped registry disagrees with shipped config: {}".format(
                [(f.key, f.title) for f in blocking]))

    def test_every_pod_workstream_declares_a_tier(self):
        """A missing tier silently exempts a pod from every check above."""
        import yaml
        with open("manager/registry.yaml") as fh:
            reg = yaml.safe_load(fh)
        missing = [w["id"] for w in reg.get("workstreams", [])
                   if str(w.get("id", "")).startswith("P-") and not w.get("tier")]
        self.assertEqual([], missing, "pod workstreams with no tier: {}".format(missing))


class TestGamesWindowStaleness(TestCase):
    """The maker is correctly silent before first pitch — don't cry wolf.

    Observed 2026-07-21: the brief reported CRITICAL "betting-live-maker
    active but silent for 912 min" at 19:20 UTC, while the day's earliest game
    was 18:40 ET = 22:40 UTC. The window opens at 16:00 UTC to cover possible
    day games, so the maker was flagged for ~7h of correct idleness.
    """

    from datetime import datetime, timezone

    def _at(self, hour, minute=0):
        from datetime import datetime, timezone
        return datetime(2026, 7, 21, hour, minute, tzinfo=timezone.utc)

    def test_minutes_since_open_within_same_day(self):
        self.assertEqual(checks._minutes_since_window_open(self._at(19, 20)), 200)

    def test_minutes_since_open_carries_across_midnight(self):
        # 02:00 UTC -> window opened 16:00 UTC yesterday -> 10h
        self.assertEqual(checks._minutes_since_window_open(self._at(2, 0)), 600)

    def test_window_open_boundary_is_zero(self):
        self.assertEqual(checks._minutes_since_window_open(self._at(16, 0)), 0)

    def _maker_snap(self, age_minutes):
        return {"services": [{
            "id": "betting-live-maker", "active": "active",
            "heartbeat": {"age_minutes": age_minutes, "max_stale_minutes": 45,
                          "only_during": "mlb_games_window",
                          "file": "data/trade_logs/maker_quotes.jsonl"},
        }]}

    def test_not_yet_quoting_today_is_silent(self):
        """Heartbeat older than the window itself = hasn't started."""
        import unittest.mock as mock
        with mock.patch.object(checks, "_in_games_window", return_value=True), \
             mock.patch.object(checks, "_minutes_since_window_open", return_value=200):
            f = checks.check_services(self._maker_snap(912))
        self.assertEqual([], [x for x in f if x.key.endswith("stale")])

    def test_quoted_then_stalled_still_fires(self):
        """The real failure mode must survive the fix."""
        import unittest.mock as mock
        with mock.patch.object(checks, "_in_games_window", return_value=True), \
             mock.patch.object(checks, "_minutes_since_window_open", return_value=450):
            f = checks.check_services(self._maker_snap(90))
        stale = [x for x in f if x.key.endswith("stale")]
        self.assertEqual(len(stale), 1, "a wedged maker must still be caught")
        self.assertEqual(stale[0].severity, "critical")

    def test_outside_window_still_silent(self):
        import unittest.mock as mock
        with mock.patch.object(checks, "_in_games_window", return_value=False):
            f = checks.check_services(self._maker_snap(912))
        self.assertEqual([], [x for x in f if x.key.endswith("stale")])
