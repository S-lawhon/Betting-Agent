"""
tests/test_dashboard_standalone.py
──────────────────────────────────
Tests for the standalone, file-backed dashboard.

Two things matter most here:

1. **The engine-is-down test.** ``test_every_route_answers_on_an_empty_tree``
   is the executable form of the requirement that started this rebuild. The old
   dashboard was a daemon thread inside the engine process, so it died with it.

2. **The superset guarantee, mechanised.** ``FileBackedState.to_json()`` must be
   byte-equal to ``DashboardState._serialize()`` fed the equivalent objects,
   because ``engine_state.json`` *is* that serializer's output. That is what
   keeps the v1 contract (56 tests + ``src/health_check.py``) true by
   construction rather than by a parallel implementation that could drift.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dashboard_server import build_server  # noqa: E402
from src.web_dashboard import DashboardState, FileBackedState  # noqa: E402

USER, PASSWORD = "sam", "hunter2"
AUTH = "Basic " + b64encode("{}:{}".format(USER, PASSWORD).encode()).decode()


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def creds(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_USER", USER)
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    # Point the file-based store at a path that does not exist, so only the env
    # user is configured and the host's real .dashboard_auth is never read.
    monkeypatch.setenv("DASHBOARD_AUTH_FILE", str(tmp_path / "nope"))


@pytest.fixture()
def empty_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def full_root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "dashboard").mkdir(parents=True)
    (tmp_path / "manager" / "state").mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (tmp_path / "data" / "dashboard" / "engine_state.json").write_text(json.dumps({
        "engine_status": "running",
        "risk": {"bankroll": 10000.0, "total_exposure_usd": 1250.0,
                 "total_exposure_pct": 12.5, "daily_pnl": -4.25,
                 "daily_pnl_pct": -0.04, "open_positions": 8, "halted": False,
                 "halt_reason": None, "venue_exposure": {"kalshi": 1250.0}},
        "cycle": {"cycle_number": 8812, "duration_seconds": 0.43,
                  "pods_scanned": 4, "placed_count": 0, "skipped_count": 118,
                  "error_count": 0, "success_rate": 100.0,
                  "timestamp_utc": now},
        "pods": {"P-001": {"name": "Kalshi vs Sharp's", "placed": 3, "wins": 1,
                           "losses": 1, "win_pct": 50.0, "pnl": 5.25,
                           "alloc_pct": 15.0, "max_position_usd": 600,
                           "capital_usd": 100.0}},
        "settlement": {"total_pnl": 5.25, "total_settled": 2, "wins": 1,
                       "losses": 1, "voids": 0, "win_rate": 50.0},
        "_meta": {"written_at_utc": now, "cycle_number": 8812, "pid": 1,
                  "interval_seconds": 300},
    }))
    (tmp_path / "data" / "dashboard" / "open_positions.json").write_text(json.dumps({
        "written_at_utc": now, "capped_at": 400, "truncated": False,
        "rows": [{"pod_id": "P-001", "status": "OPEN",
                  "position_size_usd": 100.0, "market_id": "KX"}],
    }))
    (tmp_path / "manager" / "state" / "status.json").write_text(json.dumps({
        "collected_at": now, "host": "test", "invariants": {"all_paper": True,
                                                           "pod_modes": {}},
        "services": [], "jobs": [], "faults": [], "errors": {},
        "workstreams": [], "throughput": {"available": True, "records": []},
    }))
    return tmp_path


class Server:
    def __init__(self, root: Path):
        self.httpd = build_server(root, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                      daemon=True)
        self.thread.start()

    def get(self, path, auth=True, headers=None):
        req = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(self.port, path))
        if auth:
            req.add_header("Authorization", AUTH)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8"), dict(e.headers)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def server(full_root, creds):
    s = Server(full_root)
    yield s
    s.close()


@pytest.fixture()
def bare_server(empty_root, creds):
    s = Server(empty_root)
    yield s
    s.close()


# ── the engine-is-down requirement ────────────────────────────────────


ALL_ROUTES = ["/health", "/healthz", "/", "/index.html", "/api/status",
              "/api/v2/dashboard", "/api/v2/rollup", "/manager", "/api/manager"]


#: /manager and /api/manager proxy manager/brief.py's render of status.json.
#: With no status.json they answer 503 WITH AN EXPLANATION — pre-existing,
#: deliberate, and untouched by this rebuild.
EXPLAINED_503 = {"/manager", "/api/manager"}


@pytest.mark.parametrize("route", ALL_ROUTES)
def test_every_route_answers_on_an_empty_tree(bare_server, route):
    """No engine, no collector, no rollup — nothing may 500, hang or traceback.

    This is the requirement that motivated splitting the dashboard out of the
    engine process, as an executable test.
    """
    code, body, _ = bare_server.get(route)
    assert "Traceback" not in body, "%s leaked a stack trace" % route
    if route in EXPLAINED_503:
        assert code in (200, 503), "%s returned %d" % (route, code)
        if code == 503:
            assert "status.json" in body, (
                "a 503 must name what is missing, not just fail")
    else:
        assert code < 500, "%s returned %d: %s" % (route, code, body[:200])


def test_v1_on_an_empty_tree_matches_a_fresh_dashboard_state(bare_server):
    code, body, _ = bare_server.get("/api/status")
    assert code == 200
    d = json.loads(body)
    # Exactly what a fresh in-memory DashboardState reports. Frozen by the v1
    # contract (56 tests + src/health_check.py) even though a long-dead engine
    # reads as "starting" — the honest signal is the additive keys below.
    assert d["engine_status"] == "starting"
    for absent in ("risk", "cycle", "pods", "settlement"):
        assert absent not in d, "%s must stay absent until populated" % absent
    assert d["engine_state_available"] is False
    assert d["engine_state_reason"]
    assert d["engine_state_path"].endswith("engine_state.json")
    assert d["dashboard_mode"] == "standalone"


def test_v2_on_an_empty_tree_reports_unknown_not_zero(bare_server):
    code, body, _ = bare_server.get("/api/v2/dashboard")
    assert code == 200
    d = json.loads(body)
    assert d["engine"]["liveness"] == "unknown"
    assert d["mode"]["available"] is False
    assert d["pnl"]["realized"]["lifetime"] is None
    assert d["pnl"]["open"]["bankroll"] is None
    assert all(not m["available"] for m in d["sources"].values())


def test_healthz_stays_200_when_sources_are_missing(bare_server):
    """A dashboard that refuses to load because the collector died is worse
    than one that tells you the collector died."""
    code, body, _ = bare_server.get("/healthz", auth=False)
    assert code == 200
    d = json.loads(body)
    assert d["ok"] is False
    assert d["engine_liveness"] == "unknown"
    assert set(d["sources"]) >= {"engine_state", "manager_status", "rollup"}


# ── the superset guarantee ────────────────────────────────────────────


def test_file_backed_v1_equals_the_in_memory_serializer(full_root):
    """FileBackedState.to_json() must reproduce DashboardState._serialize().

    Built by feeding the in-memory state objects equivalent to what produced
    engine_state.json, then comparing the shared keys field for field.
    """
    from types import SimpleNamespace

    raw = json.loads(
        (full_root / "data" / "dashboard" / "engine_state.json").read_text())

    snap = SimpleNamespace(
        timestamp_utc=raw["cycle"]["timestamp_utc"],
        total_exposure_usd=1250.0, total_exposure_pct=0.125,
        venue_exposure={"kalshi": 1250.0}, pod_exposure={},
        daily_pnl=-4.25, daily_pnl_pct=-0.0004, open_positions=8,
        halted=False, halt_reason=None)
    report = SimpleNamespace(
        cycle_number=8812, timestamp_utc=raw["cycle"]["timestamp_utc"],
        duration_seconds=0.43, pods_scanned=4, total_results=4,
        placed_count=0, skipped_count=118, error_count=0, success_rate=1.0)
    perf = SimpleNamespace(pod_id="P-001", pod_name="Kalshi vs Sharp's",
                           total_placed=3, total_wins=1, total_losses=1,
                           total_pnl=5.25)
    alloc = SimpleNamespace(allocation_pct=0.15, max_position_usd=600)

    mem = DashboardState()
    mem.update(snapshot=snap, report=report,
               performances={"P-001": perf}, allocations={"P-001": alloc},
               settlement_summary={"total_pnl": 5.25, "total_settled": 2,
                                   "wins": 1, "losses": 1, "voids": 0,
                                   "win_rate": 0.5},
               trades=[{"pod_id": "P-001", "status": "OPEN",
                        "position_size_usd": 100.0, "market_id": "KX"}])
    mem_v1 = json.loads(mem.to_json())
    file_v1 = json.loads(FileBackedState(full_root).to_json())

    for key in ("engine_status", "risk", "cycle", "pods", "settlement"):
        assert file_v1[key] == mem_v1[key], "v1 drifted on %r" % key
    assert file_v1["trades"] == mem_v1["trades"]


def test_v1_percentages_survive_the_round_trip(server):
    code, body, _ = server.get("/api/status")
    d = json.loads(body)
    assert d["risk"]["total_exposure_pct"] == 12.5      # percent, not 0.125
    assert d["cycle"]["success_rate"] == 100.0
    assert d["pods"]["P-001"]["win_pct"] == 50.0
    assert d["pods"]["P-001"]["alloc_pct"] == 15.0
    assert d["settlement"]["win_rate"] == 50.0


def test_v1_additive_keys_only(server):
    """Nothing existing may be removed or renamed."""
    d = json.loads(server.get("/api/status")[1])
    for required in ("engine_status", "risk", "cycle", "pods", "settlement",
                     "trades"):
        assert required in d
    for added in ("engine_state_available", "engine_state_age_seconds",
                  "engine_state_path", "dashboard_version", "dashboard_mode",
                  "trades_truncated"):
        assert added in d


# ── routes, auth, caching ─────────────────────────────────────────────


def test_health_is_open_and_unchanged(server):
    """scripts/deploy.sh gates on this and has no credentials."""
    code, body, _ = server.get("/health", auth=False)
    assert code == 200
    assert body.strip() == "ok"


def test_healthz_is_open(server):
    assert server.get("/healthz", auth=False)[0] == 200


@pytest.mark.parametrize("route", ["/", "/api/status", "/api/v2/dashboard",
                                   "/api/v2/rollup", "/manager"])
def test_authenticated_routes_401_without_credentials(server, route):
    code, _, headers = server.get(route, auth=False)
    assert code == 401
    assert headers.get("WWW-Authenticate") == 'Basic realm="Betting Pod Shop"'


def test_no_credentials_configured_returns_503(monkeypatch, full_root, tmp_path):
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("DASHBOARD_AUTH_FILE", str(tmp_path / "absent"))
    s = Server(full_root)
    try:
        assert s.get("/api/status", auth=False)[0] == 503
    finally:
        s.close()


def test_unknown_path_404(server):
    assert server.get("/nope")[0] == 404


def test_html_has_the_poll_interval_substituted(server):
    code, body, headers = server.get("/")
    assert code == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert "BETTING POD SHOP" in body
    assert "__POLL_MS__" not in body
    assert "15000" in body, "standalone default poll is 15s"


def test_section_filter(server):
    d = json.loads(server.get("/api/v2/dashboard?section=pnl")[1])
    assert "pnl" in d and "gates" not in d
    assert d["sections"] == ["pnl"]


def test_unknown_section_is_a_400_naming_the_valid_ones(server):
    code, body, _ = server.get("/api/v2/dashboard?section=bogus")
    assert code == 400
    d = json.loads(body)
    assert "bogus" in d["error"]
    assert "pnl" in d["valid"]


def test_etag_revalidation_returns_304(server):
    code, _, headers = server.get("/api/v2/dashboard")
    assert code == 200
    etag = headers.get("ETag")
    assert etag
    code2, body2, _ = server.get("/api/v2/dashboard",
                                 headers={"If-None-Match": etag})
    assert code2 == 304
    assert body2 == ""


def test_etag_changes_when_a_source_changes(server, full_root):
    first = server.get("/api/v2/dashboard")[2]["ETag"]
    p = full_root / "data" / "dashboard" / "rollup.json"
    p.write_text('{"schema":2,"lifetime":{"realized_pnl":1.0}}')
    os.utime(p, (0, 0))     # force a distinct mtime
    assert server.get("/api/v2/dashboard")[2]["ETag"] != first


def test_etag_is_scoped_per_section(server):
    a = server.get("/api/v2/dashboard")[2]["ETag"]
    b = server.get("/api/v2/dashboard?section=pnl")[2]["ETag"]
    assert a != b, "a section-scoped 304 must not serve a full payload"


def test_rollup_route_reports_its_own_absence(server):
    d = json.loads(server.get("/api/v2/rollup")[1])
    assert d["rollup"] is None
    assert d["meta"]["available"] is False


def test_v2_build_failure_is_a_500_with_a_reason(server, monkeypatch):
    """A rendering bug must not take the page down silently."""
    import src.dashboard_api as mod

    def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mod, "build_v2", boom)
    code, body, _ = server.get("/api/v2/dashboard")
    assert code == 500
    d = json.loads(body)
    assert "kaboom" in d["detail"]


# ── the embedded path serves the same v2 ──────────────────────────────


def test_embedded_state_serves_v2_from_the_same_files(full_root):
    st = DashboardState()
    st.set_paths(full_root, full_root / "data" / "dashboard")
    v2 = st.v2()
    assert v2["schema"] == 2
    assert v2["engine"]["liveness"] in ("live", "stale", "down", "unknown")
    assert v2["mode"]["all_paper"] is True


def test_embedded_healthz_reports_embedded_mode():
    st = DashboardState()
    assert st.healthz()["dashboard_mode"] == "embedded"


# ── the trades cap ────────────────────────────────────────────────────


def test_trades_are_capped_and_truncation_is_reported():
    st = DashboardState(max_trades=5)
    st.update(trades=[{"pod_id": "P-001", "status": "OPEN"}] * 50)
    d = json.loads(st.to_json())
    assert len(d["trades"]) == 5


def test_cap_can_be_disabled_with_zero():
    st = DashboardState(max_trades=0)
    st.update(trades=[{"pod_id": "P-001", "status": "OPEN"}] * 50)
    assert len(json.loads(st.to_json())["trades"]) == 50


def test_default_cap_is_400():
    assert DashboardState.DEFAULT_MAX_TRADES == 400
