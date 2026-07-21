"""
tests/test_settler_construction.py
──────────────────────────────────
Pins the coupling between `pods.active` and settler construction.

WHY THIS EXISTS — READ BEFORE REFACTORING POD LOADING.

`pods.active` is NOT only a list of pods to instantiate. build_shared_deps()
inspects it directly to decide whether to construct two settlers:

    engine.py  if "P-015" in config["pods"]["active"]  -> KalshiTennisSettler
    engine.py  if "P-017" in config["pods"]["active"]  -> KalshiGolfSettler

Any refactor that changes how `pods.active` is computed — including the
registry-driven tier work specced in research/SPEC_Pod_Tiers_2026-07-21.md —
can silently stop constructing these while every other test still passes,
because nothing else asserts on them.

THE FAILURE IS SILENT AND HAS ALREADY HAPPENED ONCE. Per CLAUDE.md, P-017
originally shipped with no settler, which made `on_settlement` dead code. The
symptom: "bets placed, nothing resolved, _open_count pegged at the cap, pod
mute after one tournament." Nothing errors. Nothing logs a fault. The pod just
quietly stops trading after its first event, and the gate never advances.

These tests are the tripwire. If one fails, do not adjust it to pass — the
settler wiring is broken and a live pod is about to go silently mute.
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.engine import build_shared_deps


def _config(active):
    """Minimal config carrying only what the settler branches read."""
    return {
        "pods": {"active": list(active)},
        "paths": {"trade_log": "data/trade_logs/trade_log.jsonl"},
    }


class TestSettlerConstruction(TestCase):
    """P-015 -> tennis settler, P-017 -> golf settler, keyed off pods.active."""

    def test_tennis_settler_built_when_p015_active(self):
        deps = build_shared_deps(_config(["P-015"]), clients={})
        self.assertIn(
            "tennis_settler", deps,
            "P-015 is active but no tennis settler was built — P-015 "
            "positions will never resolve and the failure is SILENT",
        )

    def test_golf_settler_built_when_p017_active(self):
        deps = build_shared_deps(_config(["P-017"]), clients={})
        self.assertIn(
            "golf_settler", deps,
            "P-017 is active but no golf settler was built — this is the "
            "exact regression CLAUDE.md documents: bets placed, nothing "
            "resolved, pod mute after one tournament",
        )

    def test_both_built_when_both_active(self):
        deps = build_shared_deps(_config(["P-015", "P-017"]), clients={})
        self.assertIn("tennis_settler", deps)
        self.assertIn("golf_settler", deps)

    def test_settlers_absent_when_pods_inactive(self):
        """The converse: don't poll for pods that aren't trading."""
        deps = build_shared_deps(_config(["P-001"]), clients={})
        self.assertNotIn("tennis_settler", deps)
        self.assertNotIn("golf_settler", deps)

    def test_settlers_absent_when_active_list_empty(self):
        deps = build_shared_deps(_config([]), clients={})
        self.assertNotIn("tennis_settler", deps)
        self.assertNotIn("golf_settler", deps)

    def test_missing_pods_block_does_not_crash(self):
        """A malformed config must not take the engine down at startup."""
        deps = build_shared_deps({}, clients={})
        self.assertNotIn("tennis_settler", deps)
        self.assertNotIn("golf_settler", deps)

    def test_null_active_list_does_not_crash(self):
        """pods.active: (empty in YAML) parses to None, not []."""
        deps = build_shared_deps({"pods": {"active": None}}, clients={})
        self.assertNotIn("tennis_settler", deps)

    def test_settler_build_failure_is_contained(self):
        """A broken settler must not prevent the rest of the engine loading.

        Guards the opposite risk from the tests above: the branches are
        wrapped in try/except, and that must stay a containment boundary
        rather than becoming a silent swallow of a real wiring break.
        """
        with patch(
            "src.kalshi_golf_settler.KalshiGolfSettler.from_config",
            side_effect=RuntimeError("boom"),
        ):
            deps = build_shared_deps(_config(["P-015", "P-017"]), clients={})
        self.assertNotIn("golf_settler", deps)
        self.assertIn(
            "tennis_settler", deps,
            "one settler failing must not prevent the other being built",
        )


class TestShippedConfigWiring(TestCase):
    """The deployed config must actually construct settlers for its pods."""

    def test_active_pods_get_the_settlers_they_need(self):
        import yaml
        with open("config_multi_pod.yaml") as fh:
            cfg = yaml.safe_load(fh)
        active = (cfg.get("pods") or {}).get("active") or []

        deps = build_shared_deps(cfg, clients={})

        if "P-015" in active:
            self.assertIn(
                "tennis_settler", deps,
                "shipped config has P-015 active but builds no tennis settler",
            )
        if "P-017" in active:
            self.assertIn(
                "golf_settler", deps,
                "shipped config has P-017 active but builds no golf settler",
            )
