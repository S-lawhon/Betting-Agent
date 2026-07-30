"""
tests/test_engine_snapshot.py
─────────────────────────────
Tests for the engine's per-cycle snapshot writer — the producer the standalone
dashboard reads.

Two properties matter:

* **The snapshot is ``_serialize()``'s own output.** Not a second serializer.
  Two serializers drift, and only one of them is covered by the 56 tests that pin
  the v1 contract.
* **A dashboard write can never break a scan cycle.** The engine's job is to
  trade; persisting a snapshot for a read-only viewer is strictly subordinate to
  that, so every failure path here is swallowed and logged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.aggregate_risk import AggregateRiskGuard  # noqa: E402
from src.engine import make_cycle_callback  # noqa: E402
from src.pod_runner import CycleReport  # noqa: E402
from src.web_dashboard import DashboardState  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────


def report(n: int = 7) -> CycleReport:
    return CycleReport(cycle_number=n, timestamp_utc="2026-07-30T02:00:00Z",
                       duration_seconds=0.42, pods_scanned=3, total_results=5,
                       placed_count=1, skipped_count=4, error_count=0,
                       results=[])


class Store:
    """Minimal TradeStore stand-in that records the cap it was asked for."""

    def __init__(self, rows: int = 900):
        self.rows = rows
        self.asked_for = None

    def get_recent_placed_with_status(self, max_trades=2000):
        self.asked_for = max_trades
        n = min(self.rows, max_trades) if max_trades else self.rows
        return [{"pod_id": "P-001", "status": "OPEN", "market_id": "KX%d" % i,
                 "position_size_usd": 100.0, "event": "e", "side": "YES",
                 "fingerprint": "f%d" % i} for i in range(n)]

    def compute_settlement_summary(self, trades=None):
        return {"total_pnl": 1.5, "total_settled": 2, "wins": 1, "losses": 1,
                "voids": 0, "win_rate": 0.5}


@pytest.fixture()
def guard():
    return AggregateRiskGuard.from_config({})


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "dashboard"


# ── the snapshot is written without an embedded server ────────────────


def test_snapshot_written_with_no_web_server(guard, state_dir):
    """The standalone dashboard must not depend on --web being enabled."""
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())
    assert (state_dir / "engine_state.json").exists()
    assert (state_dir / "open_positions.json").exists()


def test_snapshot_is_v1_minus_trades_plus_meta(guard, state_dir):
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report(n=8812))
    snap = json.loads((state_dir / "engine_state.json").read_text())

    assert "trades" not in snap, "trades belong in open_positions.json"
    assert snap["engine_status"] in ("starting", "running", "halted")
    assert snap["cycle"]["cycle_number"] == 8812
    assert snap["cycle"]["success_rate"] == 100.0        # percent, not fraction
    assert snap["settlement"]["win_rate"] == 50.0        # percent, not fraction

    meta = snap["_meta"]
    assert meta["interval_seconds"] == 300.0
    assert meta["cycle_number"] == 8812
    assert meta["written_at_utc"].endswith("+00:00")
    assert isinstance(meta["pid"], int)
    assert meta["max_trades"] == DashboardState.DEFAULT_MAX_TRADES


def test_interval_is_recorded_so_readers_can_scale_staleness(guard, state_dir):
    """mtime alone cannot distinguish "the engine is slow" from "it is dead"."""
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=1800)
    cb(report())
    snap = json.loads((state_dir / "engine_state.json").read_text())
    assert snap["_meta"]["interval_seconds"] == 1800.0


# ── the trades cap ────────────────────────────────────────────────────


def test_store_is_asked_for_one_more_than_the_cap(guard, state_dir):
    """The cap is applied on the way IN, and one extra row probes for more.

    Asking for exactly the cap makes "there are more" indistinguishable from
    "there are exactly this many", which left `truncated` permanently False.
    """
    store = Store(rows=900)
    cb = make_cycle_callback(guard, trade_store=store,
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())
    assert store.asked_for == DashboardState.DEFAULT_MAX_TRADES + 1


def test_open_positions_reports_truncation(guard, state_dir):
    store = Store(rows=900)
    cb = make_cycle_callback(guard, trade_store=store,
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())
    op = json.loads((state_dir / "open_positions.json").read_text())
    assert len(op["rows"]) == DashboardState.DEFAULT_MAX_TRADES
    assert op["capped_at"] == DashboardState.DEFAULT_MAX_TRADES
    assert op["truncated"] is True
    assert op["written_at_utc"]


def test_no_truncation_flag_when_under_the_cap(guard, state_dir):
    cb = make_cycle_callback(guard, trade_store=Store(rows=3),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())
    op = json.loads((state_dir / "open_positions.json").read_text())
    assert op["truncated"] is False
    assert len(op["rows"]) == 3


def test_embedded_v1_is_capped_too(guard, state_dir):
    """The unbounded list lived in the engine's address space; cap it there."""
    from src.web_dashboard import WebDashboardServer

    ws = WebDashboardServer(port=0, auto_open=False)
    cb = make_cycle_callback(guard, web_server=ws, trade_store=Store(rows=900),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())
    v1 = json.loads(ws.state.to_json())
    assert len(v1["trades"]) == DashboardState.DEFAULT_MAX_TRADES


# ── atomicity and failure containment ─────────────────────────────────


def test_writes_are_atomic_no_tmp_left_behind(guard, state_dir):
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())
    cb(report(n=8))
    leftovers = [p.name for p in state_dir.iterdir()
                 if p.name.startswith(".") or p.name.endswith(".tmp")]
    assert leftovers == []


def test_write_failure_does_not_break_the_cycle(guard, tmp_path):
    """A dashboard write must never be able to stop the engine trading."""
    unwritable = Path("/proc/definitely-not-a-directory-xyz")
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=unwritable,
                             interval_seconds=300)
    cb(report())          # must not raise


def test_failed_write_leaves_the_previous_snapshot_intact(guard, state_dir,
                                                         monkeypatch):
    import src.web_dashboard as wd

    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report(n=1))
    good = (state_dir / "engine_state.json").read_bytes()

    def boom(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(wd, "_atomic_json", boom)
    cb(report(n=2))       # must not raise

    assert (state_dir / "engine_state.json").read_bytes() == good


def test_serializer_exception_does_not_break_the_cycle(guard, state_dir,
                                                      monkeypatch):
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)

    def boom(self):
        raise RuntimeError("serializer bug")

    monkeypatch.setattr(DashboardState, "_serialize", boom)
    cb(report())          # must not raise


def test_store_failure_does_not_break_the_cycle(guard, state_dir):
    class Broken:
        def get_recent_placed_with_status(self, max_trades=2000):
            raise RuntimeError("store exploded")

    cb = make_cycle_callback(guard, trade_store=Broken(),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report())          # must not raise


# ── opting out ────────────────────────────────────────────────────────


def test_no_state_dir_means_no_files(guard, tmp_path):
    cb = make_cycle_callback(guard, trade_store=Store())
    cb(report())
    assert not any(tmp_path.iterdir())


def test_state_dir_is_created_if_absent(guard, tmp_path):
    nested = tmp_path / "a" / "b" / "dashboard"
    cb = make_cycle_callback(guard, trade_store=Store(),
                             dashboard_state_dir=nested,
                             interval_seconds=300)
    cb(report())
    assert (nested / "engine_state.json").exists()


# ── the snapshot round-trips through the reader ────────────────────────


def test_snapshot_is_readable_by_dashboard_sources(guard, tmp_path):
    """End to end: what the engine writes is what the dashboard can read."""
    from src import dashboard_sources as ds

    state_dir = tmp_path / "data" / "dashboard"
    cb = make_cycle_callback(guard, trade_store=Store(rows=2),
                             dashboard_state_dir=state_dir,
                             interval_seconds=300)
    cb(report(n=99))

    payload, meta = ds.load_engine_state(tmp_path, state_dir)
    assert meta["available"] is True
    assert meta["stale"] is False, "a just-written snapshot must not read as stale"
    assert meta["expected_max_age_seconds"] == pytest.approx(900.0)
    assert payload["cycle"]["cycle_number"] == 99

    rows, rmeta = ds.load_open_positions(tmp_path, state_dir)
    assert rmeta["available"] is True
    assert rmeta["rows"] == 2
