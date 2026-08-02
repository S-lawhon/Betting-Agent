"""
tests/test_main.py
──────────────────
Tests for src/main.py — the CLI entry point.

All external dependencies (PodRunner, venue clients, existing system
modules) are mocked so these tests run without credentials or infra.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import (
    load_config,
    filter_pods,
    setup_logging,
    build_venue_clients,
    build_shared_deps,
    make_cycle_callback,
    _run_guarded_loop,
    main,
)
from src.pod_runner import CycleReport
from src.aggregate_risk import AggregateRiskGuard


# ── Helpers ──────────────────────────────────────────────────────────

def _write_yaml(path: str, content: str) -> None:
    with open(path, "w") as fh:
        fh.write(content)


def _make_report(cycle=1, placed=1, skipped=0, errors=0):
    return CycleReport(
        cycle_number=cycle,
        timestamp_utc="2026-01-01T00:00:00+00:00",
        duration_seconds=0.05,
        pods_scanned=1,
        total_results=placed + skipped + errors,
        placed_count=placed,
        skipped_count=skipped,
        error_count=errors,
    )


# ── TestLoadConfig ────────────────────────────────────────────────────

class TestLoadConfig(unittest.TestCase):

    def test_loads_multi_pod_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.yaml")
            _write_yaml(path, "risk:\n  initial_bankroll: 5000\n")
            cfg = load_config(path)
        self.assertEqual(cfg["risk"]["initial_bankroll"], 5000)

    def test_merges_base_and_multi(self):
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "base.yaml")
            multi = os.path.join(td, "multi.yaml")
            _write_yaml(base, "kalshi:\n  environment: demo\nedge:\n  fee_pct: 0.07\n")
            _write_yaml(multi, "risk:\n  initial_bankroll: 9000\n")
            cfg = load_config(multi, base)
        self.assertEqual(cfg["kalshi"]["environment"], "demo")
        self.assertEqual(cfg["risk"]["initial_bankroll"], 9000)
        self.assertEqual(cfg["edge"]["fee_pct"], 0.07)

    def test_multi_wins_on_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "base.yaml")
            multi = os.path.join(td, "multi.yaml")
            _write_yaml(base, "risk:\n  initial_bankroll: 1000\n")
            _write_yaml(multi, "risk:\n  initial_bankroll: 9999\n")
            cfg = load_config(multi, base)
        self.assertEqual(cfg["risk"]["initial_bankroll"], 9999)

    def test_missing_config_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/does/not/exist.yaml")

    def test_base_missing_is_silent(self):
        """Missing base config doesn't raise — multi config is sufficient."""
        with tempfile.TemporaryDirectory() as td:
            multi = os.path.join(td, "multi.yaml")
            _write_yaml(multi, "risk:\n  initial_bankroll: 1234\n")
            cfg = load_config(multi, base_path="/nonexistent.yaml")
        self.assertEqual(cfg["risk"]["initial_bankroll"], 1234)


# ── TestFilterPods ────────────────────────────────────────────────────

class TestFilterPods(unittest.TestCase):

    def _base_config(self):
        return {"pods": {"active": ["P-001", "P-006", "P-012"]}}

    def test_no_filter_returns_unchanged(self):
        cfg = self._base_config()
        result = filter_pods(cfg, None)
        self.assertEqual(result["pods"]["active"], ["P-001", "P-006", "P-012"])

    def test_filters_to_requested_subset(self):
        cfg = self._base_config()
        result = filter_pods(cfg, "P-001,P-006")
        self.assertEqual(result["pods"]["active"], ["P-001", "P-006"])

    def test_unknown_pods_ignored(self):
        cfg = self._base_config()
        result = filter_pods(cfg, "P-001,P-999")
        self.assertEqual(result["pods"]["active"], ["P-001"])

    def test_all_unknown_results_in_empty_list(self):
        cfg = self._base_config()
        result = filter_pods(cfg, "P-999,P-888")
        self.assertEqual(result["pods"]["active"], [])

    def test_does_not_mutate_original(self):
        cfg = self._base_config()
        _ = filter_pods(cfg, "P-001")
        self.assertEqual(cfg["pods"]["active"], ["P-001", "P-006", "P-012"])


# ── TestSetupLogging ──────────────────────────────────────────────────

class TestSetupLogging(unittest.TestCase):

    def test_does_not_crash(self):
        """setup_logging should run without errors."""
        setup_logging("WARNING")

    def test_accepts_all_levels(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            setup_logging(level)  # should not raise


# ── TestBuildVenueClients ─────────────────────────────────────────────

class TestBuildVenueClients(unittest.TestCase):

    def test_handles_missing_kalshi_import(self):
        """ImportError from existing kalshi_client is swallowed gracefully."""
        config = {"polymarket": {"environment": "paper", "host": "http://x", "chain_id": 137}}
        clients = build_venue_clients(config)
        # Should not raise; kalshi may or may not be present
        self.assertIsInstance(clients, dict)

    def test_polymarket_client_present(self):
        config = {"polymarket": {"environment": "paper", "host": "http://x", "chain_id": 137}}
        clients = build_venue_clients(config)
        self.assertIn("polymarket", clients)

    def test_mode_override_applied(self):
        """mode_override should be applied to venue config dicts."""
        config = {
            "polymarket": {"environment": "paper", "host": "http://x", "chain_id": 137},
        }
        # Just ensure it doesn't crash with a mode override
        clients = build_venue_clients(config, mode_override="paper")
        self.assertIsInstance(clients, dict)

    def test_kalshi_raw_key_written_to_temp_file(self):
        """KALSHI_PRIVATE_KEY env var is passed directly to from_config()
        as the private_key kwarg."""
        captured = {}

        def fake_from_config(cfg, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        fake_kalshi = MagicMock()
        fake_kalshi.KalshiClient.from_config.side_effect = fake_from_config

        raw_key = "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----"
        config = {"polymarket": {"environment": "paper", "host": "http://x", "chain_id": 137}}

        env_patch = {"KALSHI_PRIVATE_KEY": raw_key, "KALSHI_API_KEY_ID": "test-id"}
        with patch.dict(os.environ, env_patch, clear=False):
            with patch.dict("sys.modules", {"kalshi_client": fake_kalshi}):
                build_venue_clients(config)

        kwargs = captured.get("kwargs", {})
        self.assertEqual(kwargs.get("private_key"), raw_key)
        self.assertEqual(kwargs.get("api_key_id"), "test-id")

    def test_kalshi_raw_key_priority_over_path(self):
        """KALSHI_PRIVATE_KEY is passed as kwarg even when PATH is also set."""
        captured = {}

        def fake_from_config(cfg, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        fake_kalshi = MagicMock()
        fake_kalshi.KalshiClient.from_config.side_effect = fake_from_config

        raw_key = "-----BEGIN PRIVATE KEY-----\nABCD\n-----END PRIVATE KEY-----"
        config = {"polymarket": {"environment": "paper", "host": "http://x", "chain_id": 137}}

        env_patch = {
            "KALSHI_PRIVATE_KEY": raw_key,
            "KALSHI_PRIVATE_KEY_PATH": "/should/be/ignored.pem",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            with patch.dict("sys.modules", {"kalshi_client": fake_kalshi}):
                build_venue_clients(config)

        kwargs = captured.get("kwargs", {})
        self.assertEqual(kwargs.get("private_key"), raw_key)

    def test_kalshi_key_path_env_var_used_when_no_raw_key(self):
        """When KALSHI_PRIVATE_KEY is empty, private_key kwarg is empty string."""
        captured = {}

        def fake_from_config(cfg, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        fake_kalshi = MagicMock()
        fake_kalshi.KalshiClient.from_config.side_effect = fake_from_config

        config = {"polymarket": {"environment": "paper", "host": "http://x", "chain_id": 137}}
        # Make sure KALSHI_PRIVATE_KEY is absent
        clean_env = {k: v for k, v in os.environ.items() if k != "KALSHI_PRIVATE_KEY"}

        with patch.dict(os.environ, clean_env, clear=True):
            with patch.dict("sys.modules", {"kalshi_client": fake_kalshi}):
                build_venue_clients(config)

        kwargs = captured.get("kwargs", {})
        # With no KALSHI_PRIVATE_KEY env var, the key should be empty string
        self.assertEqual(kwargs.get("private_key", ""), "")


# ── TestBuildSharedDeps ───────────────────────────────────────────────

class TestBuildSharedDeps(unittest.TestCase):

    def test_missing_existing_modules_graceful(self):
        """All existing-system imports are try/except; should never raise."""
        config = {}
        deps = build_shared_deps(config)
        self.assertIsInstance(deps, dict)

    def test_nowcast_client_always_present(self):
        """NowcastClient should always be created (stub if HTTP adapter fails)."""
        config = {}
        deps = build_shared_deps(config)
        self.assertIn("nowcast_client", deps)


# ── TestMakeCycleCallback ─────────────────────────────────────────────

class TestMakeCycleCallback(unittest.TestCase):

    def test_calls_guard_update(self):
        guard = MagicMock()
        guard.snapshot.return_value = MagicMock(
            total_exposure_pct=0.1, daily_pnl=0.0, halted=False
        )
        callback = make_cycle_callback(guard)
        report = _make_report()
        callback(report)
        guard.update_post_cycle.assert_called_once_with(report)

    def test_snapshot_called(self):
        guard = MagicMock()
        guard.snapshot.return_value = MagicMock(
            total_exposure_pct=0.05, daily_pnl=-10.0, halted=False
        )
        callback = make_cycle_callback(guard)
        callback(_make_report())
        guard.snapshot.assert_called_once()

    def test_halted_state_logged(self):
        """Callback should not crash when guard is halted."""
        guard = MagicMock()
        guard.snapshot.return_value = MagicMock(
            total_exposure_pct=0.5, daily_pnl=-500.0, halted=True
        )
        callback = make_cycle_callback(guard)
        callback(_make_report(errors=2))  # should not raise


# ── TestRunGuardedLoop ────────────────────────────────────────────────

class TestRunGuardedLoop(unittest.TestCase):

    def test_respects_max_cycles(self):
        runner = MagicMock()
        runner.run_once.return_value = _make_report()
        guard = MagicMock()
        guard.check_pre_cycle.return_value = True
        guard.snapshot.return_value = MagicMock(halt_reason=None)

        reports = _run_guarded_loop(runner, guard, interval=0.0, max_cycles=3)
        self.assertEqual(len(reports), 3)
        self.assertEqual(runner.run_once.call_count, 3)

    def test_skips_cycle_when_guard_halted(self):
        runner = MagicMock()
        guard = MagicMock()
        guard.check_pre_cycle.return_value = False
        guard.snapshot.return_value = MagicMock(halt_reason="test_halt")

        reports = _run_guarded_loop(runner, guard, interval=0.0, max_cycles=2)
        self.assertEqual(len(reports), 0)
        runner.run_once.assert_not_called()

    def test_crash_in_cycle_continues_loop(self):
        """Exception in run_once should be caught and loop should continue."""
        runner = MagicMock()
        runner.run_once.side_effect = RuntimeError("pod crash")
        guard = MagicMock()
        guard.check_pre_cycle.return_value = True
        guard.snapshot.return_value = MagicMock(halt_reason=None)

        # Should not raise; just skips adding to reports
        reports = _run_guarded_loop(runner, guard, interval=0.0, max_cycles=2)
        self.assertEqual(len(reports), 0)

    def test_golf_settler_is_polled_and_releases_pod_slot(self):
        """Regression: P-017 shipped with a settler that was never called.

        on_settlement() existed on the pod but nothing invoked it — the
        generic Settler filters to P-001 and only the tennis branch
        reached pod.on_settlement.  The failure was silent: bets placed,
        nothing resolved, the open-position counter pegged at the cap,
        and the pod went mute after one tournament.  Pin the wiring.
        """
        runner = MagicMock()
        runner.run_once.return_value = _make_report()
        guard = MagicMock()
        guard.check_pre_cycle.return_value = True
        guard.snapshot.return_value = MagicMock(halt_reason=None)

        pod = MagicMock()
        pod.pod_id = "P-017"
        runner.pods = [pod]

        golf_settler = MagicMock()
        golf_settler.settle_cycle.return_value = [{
            "pod_id": "P-017",
            "market_id": "KXPGATOP10-3MO26-AAA",
            "pnl_usd": 12.5,
            "fingerprint": "fp1",
        }]
        allocator = MagicMock()

        _run_guarded_loop(
            runner, guard, interval=0.0, max_cycles=1,
            golf_settler=golf_settler, allocator=allocator,
        )

        golf_settler.settle_cycle.assert_called()
        # the pod's open-position slot must actually be released
        pod.on_settlement.assert_called_once_with("KXPGATOP10-3MO26-AAA")
        allocator.record_settlement.assert_called_once_with(
            "P-017", 12.5, fingerprint="fp1",
        )
        guard.close_position.assert_called_once_with(
            "KXPGATOP10-3MO26-AAA", 12.5,
        )

    def test_golf_settler_failure_does_not_break_cycle(self):
        runner = MagicMock()
        runner.run_once.return_value = _make_report()
        runner.pods = []
        guard = MagicMock()
        guard.check_pre_cycle.return_value = True
        guard.snapshot.return_value = MagicMock(halt_reason=None)

        golf_settler = MagicMock()
        golf_settler.settle_cycle.side_effect = RuntimeError("kalshi down")

        reports = _run_guarded_loop(
            runner, guard, interval=0.0, max_cycles=1,
            golf_settler=golf_settler,
        )
        self.assertEqual(len(reports), 1)


# ── TestMain ──────────────────────────────────────────────────────────

class TestMain(unittest.TestCase):

    def _make_config_file(self, td: str, content: str) -> str:
        path = os.path.join(td, "config_multi_pod.yaml")
        _write_yaml(path, content)
        return path

    # ── shadow mode reaches the DEPLOYED entry point ─────────────────
    # The unit runs `python -m src.main --loop ... ` with no --mode, so
    # main() (not cli.main) is what production executes. main() used to
    # call from_config(config) with no mode, and from_config fails closed
    # — so the droplet came up ENFORCING while the config said shadow,
    # and the only sign was one ERROR line in the journal.

    @patch("src.main.PodRunner")
    @patch("src.main.CapitalAllocator")
    @patch("src.main.AggregateRiskGuard")
    @patch("src.main.build_venue_clients", return_value={})
    @patch("src.main.build_shared_deps", return_value={})
    def test_absent_mode_flag_resolves_to_paper(
        self, mock_deps, mock_clients, mock_guard_cls, mock_alloc_cls,
        mock_runner_cls,
    ):
        mock_guard = MagicMock()
        mock_guard.check_pre_cycle.return_value = True
        mock_guard.snapshot.return_value = MagicMock(
            total_exposure_pct=0.0, daily_pnl=0.0, halted=False,
            halt_reason=None, open_positions=0,
        )
        mock_guard_cls.from_config.return_value = mock_guard
        mock_alloc_cls.from_config.return_value = MagicMock()
        mock_runner = MagicMock()
        mock_runner.pods = [MagicMock(pod_id="P-001")]
        mock_runner.run_once.return_value = _make_report()
        mock_runner_cls.from_config.return_value = mock_runner

        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._make_config_file(
                td, "risk:\n  initial_bankroll: 10000\n")
            main(["--config", cfg_path, "--once"])

        _, kwargs = mock_guard_cls.from_config.call_args
        self.assertEqual(kwargs.get("mode"), "paper")

    @patch("src.main.PodRunner")
    @patch("src.main.CapitalAllocator")
    @patch("src.main.AggregateRiskGuard")
    @patch("src.main.build_venue_clients", return_value={})
    @patch("src.main.build_shared_deps", return_value={})
    def test_explicit_live_mode_is_passed_through(
        self, mock_deps, mock_clients, mock_guard_cls, mock_alloc_cls,
        mock_runner_cls,
    ):
        mock_guard = MagicMock()
        mock_guard.check_pre_cycle.return_value = True
        mock_guard.snapshot.return_value = MagicMock(
            total_exposure_pct=0.0, daily_pnl=0.0, halted=False,
            halt_reason=None, open_positions=0,
        )
        mock_guard_cls.from_config.return_value = mock_guard
        mock_alloc_cls.from_config.return_value = MagicMock()
        mock_runner = MagicMock()
        mock_runner.pods = [MagicMock(pod_id="P-001")]
        mock_runner.run_once.return_value = _make_report()
        mock_runner_cls.from_config.return_value = mock_runner

        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._make_config_file(
                td, "risk:\n  initial_bankroll: 10000\n")
            main(["--config", cfg_path, "--once", "--mode", "live"])

        _, kwargs = mock_guard_cls.from_config.call_args
        self.assertEqual(kwargs.get("mode"), "live")

    def test_main_and_cli_construct_the_guard_identically(self):
        """Two entry points build the guard; they must not drift.

        cli.py was fixed first and main.py was missed, which is exactly
        the failure this asserts against.
        """
        import inspect
        from src import cli as _cli
        for mod, fn in ((_cli, _cli.main), (sys.modules["src.main"], main)):
            src_text = inspect.getsource(fn)
            self.assertIn("args.mode or \"paper\"", src_text,
                          "%s does not resolve the paper default" % mod.__name__)
            self.assertIn("AggregateRiskGuard.from_config(config, mode=", src_text,
                          "%s does not pass mode to the guard" % mod.__name__)

    @patch("src.main.PodRunner")
    @patch("src.main.CapitalAllocator")
    @patch("src.main.AggregateRiskGuard")
    @patch("src.main.build_venue_clients", return_value={})
    @patch("src.main.build_shared_deps", return_value={})
    def test_once_returns_zero(self, mock_deps, mock_clients, mock_guard_cls,
                                mock_alloc_cls, mock_runner_cls):
        mock_guard = MagicMock()
        mock_guard.check_pre_cycle.return_value = True
        mock_guard.snapshot.return_value = MagicMock(
            total_exposure_pct=0.0, daily_pnl=0.0, halted=False,
            halt_reason=None, open_positions=0,
        )
        mock_guard_cls.from_config.return_value = mock_guard
        mock_alloc_cls.from_config.return_value = MagicMock()

        mock_runner = MagicMock()
        mock_runner.pods = [MagicMock(pod_id="P-001")]
        mock_runner.run_once.return_value = _make_report()
        mock_runner_cls.from_config.return_value = mock_runner

        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._make_config_file(td, "risk:\n  initial_bankroll: 10000\n")
            exit_code = main(["--config", cfg_path, "--once"])

        self.assertEqual(exit_code, 0)
        mock_runner.run_once.assert_called_once()

    @patch("src.main.PodRunner")
    @patch("src.main.CapitalAllocator")
    @patch("src.main.AggregateRiskGuard")
    @patch("src.main.build_venue_clients", return_value={})
    @patch("src.main.build_shared_deps", return_value={})
    def test_no_pods_returns_one(self, mock_deps, mock_clients, mock_guard_cls,
                                  mock_alloc_cls, mock_runner_cls):
        mock_guard_cls.from_config.return_value = MagicMock()
        mock_alloc_cls.from_config.return_value = MagicMock()

        mock_runner = MagicMock()
        mock_runner.pods = []  # No pods loaded
        mock_runner_cls.from_config.return_value = mock_runner

        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._make_config_file(td, "risk:\n  initial_bankroll: 10000\n")
            exit_code = main(["--config", cfg_path, "--once"])

        self.assertEqual(exit_code, 1)

    def test_missing_config_returns_one(self):
        exit_code = main(["--config", "/nonexistent_config.yaml", "--once"])
        self.assertEqual(exit_code, 1)

    @patch("src.main.PodRunner")
    @patch("src.main.CapitalAllocator")
    @patch("src.main.AggregateRiskGuard")
    @patch("src.main.build_venue_clients", return_value={})
    @patch("src.main.build_shared_deps", return_value={})
    def test_guard_halted_returns_one(self, mock_deps, mock_clients, mock_guard_cls,
                                      mock_alloc_cls, mock_runner_cls):
        mock_guard = MagicMock()
        mock_guard.check_pre_cycle.return_value = False
        mock_guard.snapshot.return_value = MagicMock(halt_reason="daily_loss")
        mock_guard_cls.from_config.return_value = mock_guard
        mock_alloc_cls.from_config.return_value = MagicMock()

        mock_runner = MagicMock()
        mock_runner.pods = [MagicMock(pod_id="P-001")]
        mock_runner_cls.from_config.return_value = mock_runner

        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._make_config_file(td, "risk:\n  initial_bankroll: 10000\n")
            exit_code = main(["--config", cfg_path, "--once"])

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
