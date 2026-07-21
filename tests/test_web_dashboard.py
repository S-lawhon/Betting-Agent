"""
tests/test_web_dashboard.py
───────────────────────────
Tests for src/web_dashboard.py — DashboardState, WebDashboardServer,
HTTP endpoints, and main.py integration.

HTTP tests start a real server on an OS-assigned port (port 0) so
there are no fixed-port conflicts.
"""

import json
import os
import sys
import time
import urllib.request
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.web_dashboard import DashboardState, WebDashboardServer


# ── Mock factories ────────────────────────────────────────────────────

def _snap(
    exposure_usd=1_250.0, exposure_pct=0.125,
    daily_pnl=147.25, daily_pnl_pct=0.0147,
    positions=8, halted=False, halt_reason=None,
    venue_exposure=None,
):
    return SimpleNamespace(
        total_exposure_usd=exposure_usd,
        total_exposure_pct=exposure_pct,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        open_positions=positions,
        halted=halted,
        halt_reason=halt_reason,
        venue_exposure=venue_exposure or {"kalshi": 800.0, "polymarket": 450.0},
        timestamp_utc="2026-01-15T14:32:07+00:00",
    )


def _report(cycle=42, duration=0.43, pods=4, placed=3, skipped=12, errors=0, total=15):
    obj = SimpleNamespace(
        cycle_number=cycle, duration_seconds=duration, pods_scanned=pods,
        placed_count=placed, skipped_count=skipped, error_count=errors,
        total_results=total, timestamp_utc="2026-01-15T14:32:07+00:00",
        results=[],
    )
    obj.success_rate = (total - errors) / total if total else 1.0
    return obj


def _perf(pod_id, placed=10, wins=6, losses=4, pnl=150.0):
    return SimpleNamespace(
        pod_id=pod_id, total_placed=placed, total_wins=wins,
        total_losses=losses, total_pnl=pnl,
        win_rate=wins / (wins + losses) if (wins + losses) else 0.0,
    )


def _alloc(pod_id, pct=0.20, max_pos=200.0):
    return SimpleNamespace(
        pod_id=pod_id, allocation_pct=pct,
        allocated_usd=pct * 10_000, max_position_usd=max_pos, active=True,
    )


def _settle(pnl=437.25, total=38, wins=24, losses=12, voids=2, wr=0.632):
    return dict(total_pnl=pnl, total_settled=total, wins=wins,
                losses=losses, voids=voids, win_rate=wr)


# ── TestDashboardState ────────────────────────────────────────────────

class TestDashboardState(unittest.TestCase):

    def test_initial_engine_status_starting(self):
        state = DashboardState()
        data = json.loads(state.to_json())
        self.assertEqual(data["engine_status"], "starting")

    def test_update_running_snapshot_sets_running(self):
        state = DashboardState()
        state.update(snapshot=_snap(halted=False))
        data = json.loads(state.to_json())
        self.assertEqual(data["engine_status"], "running")

    def test_update_halted_snapshot_sets_halted(self):
        state = DashboardState()
        state.update(snapshot=_snap(halted=True, halt_reason="daily_loss"))
        data = json.loads(state.to_json())
        self.assertEqual(data["engine_status"], "halted")

    def test_no_risk_key_before_snapshot(self):
        state = DashboardState()
        data = json.loads(state.to_json())
        self.assertNotIn("risk", data)

    def test_risk_key_present_after_snapshot(self):
        state = DashboardState()
        state.update(snapshot=_snap())
        data = json.loads(state.to_json())
        self.assertIn("risk", data)

    def test_risk_exposure_usd(self):
        state = DashboardState()
        state.update(snapshot=_snap(exposure_usd=5_000.0))
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["risk"]["total_exposure_usd"], 5_000.0)

    def test_risk_exposure_pct_is_percentage(self):
        # 0.125 fraction → 12.5%
        state = DashboardState()
        state.update(snapshot=_snap(exposure_pct=0.125))
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["risk"]["total_exposure_pct"], 12.5)

    def test_risk_daily_pnl(self):
        state = DashboardState()
        state.update(snapshot=_snap(daily_pnl=99.99))
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["risk"]["daily_pnl"], 99.99)

    def test_risk_halted_flag(self):
        state = DashboardState()
        state.update(snapshot=_snap(halted=True, halt_reason="test"))
        data = json.loads(state.to_json())
        self.assertTrue(data["risk"]["halted"])
        self.assertEqual(data["risk"]["halt_reason"], "test")

    def test_risk_venue_exposure_included(self):
        state = DashboardState()
        state.update(snapshot=_snap(venue_exposure={"kalshi": 800.0}))
        data = json.loads(state.to_json())
        self.assertIn("kalshi", data["risk"]["venue_exposure"])

    def test_no_cycle_key_before_report(self):
        state = DashboardState()
        data = json.loads(state.to_json())
        self.assertNotIn("cycle", data)

    def test_cycle_key_present_after_report(self):
        state = DashboardState()
        state.update(report=_report())
        data = json.loads(state.to_json())
        self.assertIn("cycle", data)

    def test_cycle_number(self):
        state = DashboardState()
        state.update(report=_report(cycle=42))
        data = json.loads(state.to_json())
        self.assertEqual(data["cycle"]["cycle_number"], 42)

    def test_cycle_success_rate_is_percentage(self):
        state = DashboardState()
        state.update(report=_report(total=10, errors=0))
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["cycle"]["success_rate"], 100.0)

    def test_cycle_partial_success_rate(self):
        state = DashboardState()
        state.update(report=_report(total=10, errors=2))
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["cycle"]["success_rate"], 80.0)

    def test_pods_key_present_after_performances(self):
        state = DashboardState()
        state.update(performances={"P-001": _perf("P-001")})
        data = json.loads(state.to_json())
        self.assertIn("pods", data)

    def test_pods_win_pct_calculation(self):
        # 6 wins / (6+4) = 60%
        state = DashboardState()
        state.update(performances={"P-001": _perf("P-001", wins=6, losses=4)})
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["pods"]["P-001"]["win_pct"], 60.0)

    def test_pods_win_pct_zero_when_no_resolved(self):
        state = DashboardState()
        state.update(performances={"P-001": _perf("P-001", wins=0, losses=0)})
        data = json.loads(state.to_json())
        self.assertEqual(data["pods"]["P-001"]["win_pct"], 0.0)

    def test_pods_alloc_pct_from_allocations(self):
        state = DashboardState()
        state.update(
            performances={"P-001": _perf("P-001")},
            allocations={"P-001": _alloc("P-001", pct=0.15)},
        )
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["pods"]["P-001"]["alloc_pct"], 15.0)

    def test_settlement_key_present(self):
        state = DashboardState()
        state.update(settlement_summary=_settle())
        data = json.loads(state.to_json())
        self.assertIn("settlement", data)

    def test_settlement_win_rate_is_percentage(self):
        state = DashboardState()
        state.update(settlement_summary=_settle(wr=0.632))
        data = json.loads(state.to_json())
        self.assertAlmostEqual(data["settlement"]["win_rate"], 63.2)

    def test_partial_update_preserves_snapshot(self):
        state = DashboardState()
        state.update(snapshot=_snap(daily_pnl=200.0))
        state.update(report=_report())          # should not clear snapshot
        data = json.loads(state.to_json())
        self.assertIn("risk", data)
        self.assertAlmostEqual(data["risk"]["daily_pnl"], 200.0)

    def test_to_json_is_valid_json(self):
        state = DashboardState()
        state.update(snapshot=_snap(), report=_report())
        parsed = json.loads(state.to_json())
        self.assertIsInstance(parsed, dict)


# ── TestWebDashboardServer ────────────────────────────────────────────

def _start_server(poll_secs: int = 5) -> WebDashboardServer:
    """Start a server on an OS-assigned port with auto_open=False."""
    from http.server import HTTPServer
    from src.web_dashboard import _DashboardHandler

    state = DashboardState()
    poll_ms = poll_secs * 1_000

    class _H(_DashboardHandler):
        pass
    _H.state   = state
    _H.poll_ms = poll_ms

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]

    import threading
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, state, port


class TestWebDashboardServer(unittest.TestCase):

    def test_url_property_default(self):
        server = WebDashboardServer(auto_open=False)
        self.assertEqual(server.url, "http://0.0.0.0:8080")

    def test_url_property_custom_port(self):
        server = WebDashboardServer(port=9090, auto_open=False)
        self.assertEqual(server.url, "http://0.0.0.0:9090")

    def test_state_accessible(self):
        server = WebDashboardServer(auto_open=False)
        self.assertIsInstance(server.state, DashboardState)

    def test_update_delegates_to_state(self):
        server = WebDashboardServer(auto_open=False)
        server.update(snapshot=_snap(daily_pnl=55.0))
        data = json.loads(server.state.to_json())
        self.assertAlmostEqual(data["risk"]["daily_pnl"], 55.0)

    def test_stop_without_start_is_safe(self):
        server = WebDashboardServer(auto_open=False)
        server.stop()   # should not raise

    # ── Real HTTP tests (OS-assigned port) ────────────────────────────

    def _get(self, port: int, path: str, auth: bool = True) -> tuple:
        import base64
        url = f"http://127.0.0.1:{port}{path}"
        req = urllib.request.Request(url)
        if auth:
            tok = base64.b64encode(f"{self._user}:{self._pw}".encode()).decode()
            req.add_header("Authorization", "Basic " + tok)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.getcode(), resp.read(), dict(resp.getheaders())
        except urllib.error.HTTPError as e:
            return e.code, b"", {}

    def setUp(self):
        # Dashboard now requires HTTP basic auth; configure a throwaway cred.
        self._user, self._pw = "tester", "s3cret-test"
        os.environ["DASHBOARD_USER"] = self._user
        os.environ["DASHBOARD_PASSWORD"] = self._pw
        self._http_srv, self._state, self._port = _start_server()

    def tearDown(self):
        self._http_srv.shutdown()
        os.environ.pop("DASHBOARD_USER", None)
        os.environ.pop("DASHBOARD_PASSWORD", None)

    def test_get_root_returns_200(self):
        code, _, _ = self._get(self._port, "/")
        self.assertEqual(code, 200)

    def test_get_root_content_type_html(self):
        _, _, headers = self._get(self._port, "/")
        ctype = headers.get("Content-Type", "")
        self.assertIn("text/html", ctype)

    def test_get_root_body_contains_title(self):
        _, body, _ = self._get(self._port, "/")
        self.assertIn(b"BETTING POD SHOP", body)

    def test_get_root_body_contains_poll_ms(self):
        _, body, _ = self._get(self._port, "/")
        # __POLL_MS__ should have been replaced with a number
        self.assertNotIn(b"__POLL_MS__", body)
        self.assertIn(b"5000", body)     # default 5 s → 5000 ms

    def test_get_api_status_returns_200(self):
        code, _, _ = self._get(self._port, "/api/status")
        self.assertEqual(code, 200)

    def test_get_api_status_content_type_json(self):
        _, _, headers = self._get(self._port, "/api/status")
        ctype = headers.get("Content-Type", "")
        self.assertIn("application/json", ctype)

    def test_get_api_status_parseable(self):
        _, body, _ = self._get(self._port, "/api/status")
        data = json.loads(body)
        self.assertIsInstance(data, dict)

    def test_api_status_initial_starting(self):
        _, body, _ = self._get(self._port, "/api/status")
        data = json.loads(body)
        self.assertEqual(data["engine_status"], "starting")

    def test_api_status_reflects_update(self):
        self._state.update(snapshot=_snap(daily_pnl=777.77))
        _, body, _ = self._get(self._port, "/api/status")
        data = json.loads(body)
        self.assertAlmostEqual(data["risk"]["daily_pnl"], 777.77)

    def test_health_endpoint_returns_200(self):
        code, body, _ = self._get(self._port, "/health")
        self.assertEqual(code, 200)
        self.assertIn(b"ok", body)

    def test_unknown_path_returns_404(self):
        code, _, _ = self._get(self._port, "/not-a-page")
        self.assertEqual(code, 404)

    # ── Auth contract ─────────────────────────────────────────────────
    def test_health_open_without_auth(self):
        code, body, _ = self._get(self._port, "/health", auth=False)
        self.assertEqual(code, 200)
        self.assertIn(b"ok", body)

    def test_root_requires_auth(self):
        code, _, _ = self._get(self._port, "/", auth=False)
        self.assertEqual(code, 401)

    def test_api_status_requires_auth(self):
        code, _, _ = self._get(self._port, "/api/status", auth=False)
        self.assertEqual(code, 401)

    def test_unconfigured_credentials_returns_503(self):
        os.environ.pop("DASHBOARD_USER", None)
        os.environ.pop("DASHBOARD_PASSWORD", None)
        os.environ["DASHBOARD_AUTH_FILE"] = "/nonexistent/dashboard_auth_test"
        try:
            code, _, _ = self._get(self._port, "/", auth=False)
            self.assertEqual(code, 503)
        finally:
            os.environ.pop("DASHBOARD_AUTH_FILE", None)
            os.environ["DASHBOARD_USER"] = self._user
            os.environ["DASHBOARD_PASSWORD"] = self._pw

    def test_multiple_users_each_authenticate(self):
        # two distinct users configured via the file both work; wrong pw fails
        import base64, tempfile, os as _os
        _os.environ.pop("DASHBOARD_USER", None)
        _os.environ.pop("DASHBOARD_PASSWORD", None)
        fd, path = tempfile.mkstemp()
        with _os.fdopen(fd, "w") as fh:
            fh.write("alice:apw\n# a comment\nbob:bpw\n")
        _os.environ["DASHBOARD_AUTH_FILE"] = path
        try:
            def hit(u, p):
                import urllib.request, urllib.error
                req = urllib.request.Request(f"http://127.0.0.1:{self._port}/api/status")
                req.add_header("Authorization", "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode())
                try:
                    return urllib.request.urlopen(req, timeout=3).getcode()
                except urllib.error.HTTPError as e:
                    return e.code
            self.assertEqual(hit("alice", "apw"), 200)
            self.assertEqual(hit("bob", "bpw"), 200)
            self.assertEqual(hit("bob", "wrong"), 401)
            self.assertEqual(hit("carol", "x"), 401)
        finally:
            _os.environ.pop("DASHBOARD_AUTH_FILE", None)
            _os.unlink(path)
            _os.environ["DASHBOARD_USER"] = self._user
            _os.environ["DASHBOARD_PASSWORD"] = self._pw

    def test_wrong_password_rejected(self):
        import base64
        bad = base64.b64encode(b"tester:wrong").decode()
        url = f"http://127.0.0.1:{self._port}/api/status"
        req = urllib.request.Request(url); req.add_header("Authorization", "Basic " + bad)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                code = resp.getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 401)


# ── TestMainWebFlags ──────────────────────────────────────────────────

class TestMainWebFlags(unittest.TestCase):

    def _parse(self, args):
        from src.main import _build_parser
        return _build_parser().parse_args(args)

    def test_web_flag_defaults_false(self):
        self.assertFalse(self._parse(["--once"]).web)

    def test_web_long_flag(self):
        self.assertTrue(self._parse(["--once", "--web"]).web)

    def test_web_short_flag(self):
        self.assertTrue(self._parse(["--once", "-w"]).web)

    def test_web_port_default_8080(self):
        self.assertEqual(self._parse(["--once"]).web_port, 8080)

    def test_web_port_custom(self):
        self.assertEqual(self._parse(["--once", "--web-port", "9090"]).web_port, 9090)

    def test_no_browser_defaults_false(self):
        self.assertFalse(self._parse(["--once"]).no_browser)

    def test_no_browser_flag(self):
        self.assertTrue(self._parse(["--once", "--no-browser"]).no_browser)

    def test_web_and_dashboard_can_coexist(self):
        args = self._parse(["--once", "--web", "--dashboard"])
        self.assertTrue(args.web)
        self.assertTrue(args.dashboard)


# ── TestMakeCycleCallbackWebServer ────────────────────────────────────

class TestMakeCycleCallbackWebServer(unittest.TestCase):

    def _report_obj(self):
        from src.pod_runner import CycleReport
        return CycleReport(
            cycle_number=1,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            duration_seconds=0.1,
            pods_scanned=1,
            total_results=1,
            placed_count=1,
            skipped_count=0,
            error_count=0,
        )

    def test_web_server_update_called(self):
        from unittest.mock import MagicMock
        from src.main import make_cycle_callback

        guard = MagicMock()
        guard.update_post_cycle.return_value = None
        guard.snapshot.return_value = _snap()

        web_server = MagicMock()
        cb = make_cycle_callback(guard, web_server=web_server)
        cb(self._report_obj())
        web_server.update.assert_called_once()

    def test_allocator_performances_passed_to_web_server(self):
        from unittest.mock import MagicMock
        from src.main import make_cycle_callback

        guard = MagicMock()
        guard.snapshot.return_value = _snap()

        allocator = MagicMock()
        allocator.performances = {"P-001": _perf("P-001")}
        allocator.allocations  = {"P-001": _alloc("P-001")}

        web_server = MagicMock()
        cb = make_cycle_callback(guard, allocator=allocator, web_server=web_server)
        cb(self._report_obj())

        call_kwargs = web_server.update.call_args[1]
        self.assertIn("P-001", call_kwargs["performances"])

    def test_no_web_server_no_crash(self):
        from unittest.mock import MagicMock
        from src.main import make_cycle_callback

        guard = MagicMock()
        guard.snapshot.return_value = _snap()
        cb = make_cycle_callback(guard, web_server=None)
        cb(self._report_obj())   # should not raise


if __name__ == "__main__":
    unittest.main()
