"""
tests/test_dashboard_sources.py
───────────────────────────────
Tests for ``src/dashboard_sources.py`` — the dashboard's only filesystem reader.

The contract under test is narrow and absolute: **no loader ever raises, and a
loader that cannot read something reports ``available: False`` with a spoken
reason that names the path.** Centralising I/O in one module is what makes
"every panel degrades to an explicit unknown" a property provable by tests
rather than a claim in a docstring.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import dashboard_sources as ds  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)


def clock():
    return NOW


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def backdate(p: Path) -> None:
    """Pin ``p``'s mtime to the frozen NOW.

    Tests whose file carries no parseable content timestamp fall back to the
    real mtime — which, against the frozen ``clock()``, reads as minutes-to-
    hours in the FUTURE once the wall clock passes 12:00 UTC, and the
    clock-skew reason overwrites the one under test.  (These tests passed
    every morning and failed every afternoon.)
    """
    ts = NOW.timestamp()
    os.utime(p, (ts, ts))


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "dashboard").mkdir(parents=True)
    (tmp_path / "data" / "trade_logs").mkdir(parents=True)
    (tmp_path / "data" / "p022_window_check").mkdir(parents=True)
    (tmp_path / "manager" / "state").mkdir(parents=True)
    return tmp_path


LOADERS = [
    ("load_engine_state", lambda r: r / "data/dashboard/engine_state.json"),
    ("load_open_positions", lambda r: r / "data/dashboard/open_positions.json"),
    ("load_manager_status", lambda r: r / "manager/state/status.json"),
    ("load_rollup", lambda r: r / "data/dashboard/rollup.json"),
]


# ── the universal contract ────────────────────────────────────────────


@pytest.mark.parametrize("name,pathfn", LOADERS)
def test_missing_file_is_unavailable_with_the_path_named(root, name, pathfn):
    payload, meta = getattr(ds, name)(root, clock=clock) \
        if name == "load_manager_status" else getattr(ds, name)(root, None, clock)
    assert payload is None
    assert meta["available"] is False
    assert meta["reason"], "a missing file must be explained, not silently empty"
    assert str(pathfn(root)) == meta["path"]
    assert meta["stale"] is True


@pytest.mark.parametrize("name,pathfn", LOADERS)
@pytest.mark.parametrize("body", ["", "{{{ not json", "[]", '"a string"'])
def test_unreadable_bodies_never_raise(root, name, pathfn, body):
    p = pathfn(root)
    p.write_text(body, encoding="utf-8")
    fn = getattr(ds, name)
    payload, meta = fn(root, clock=clock) if name == "load_manager_status" \
        else fn(root, None, clock)
    assert payload is None
    assert meta["available"] is False
    assert meta["reason"]


def test_oversize_file_is_refused_not_loaded(root):
    p = root / "manager" / "state" / "status.json"
    p.write_text("{}", encoding="utf-8")
    backdate(p)
    payload, meta = ds.load_json_file(p, max_bytes=1, clock=clock)
    assert payload is None
    assert meta["available"] is False
    assert "read limit" in meta["reason"]


def test_unreadable_permissions_do_not_raise(root):
    p = root / "manager" / "state" / "status.json"
    p.write_text(json.dumps({"collected_at": ago(minutes=1)}), encoding="utf-8")
    p.chmod(0o000)
    try:
        payload, meta = ds.load_manager_status(root, clock=clock)
    finally:
        p.chmod(0o644)
    # Root can read anything, so accept either outcome — the point is no raise.
    assert meta["available"] in (True, False)


# ── freshness ─────────────────────────────────────────────────────────


def test_content_timestamp_beats_mtime(root):
    """A file touched now but holding an old row must read as OLD.

    This is the clv_settlement lesson recorded in manager/collect.py: mtime was
    31h, the newest row was 50h, because the job rewrote the file without adding
    anything. mtime answers "was it touched"; the content stamp answers "is the
    information current".
    """
    p = root / "manager" / "state" / "status.json"
    p.write_text(json.dumps({"collected_at": ago(hours=9)}), encoding="utf-8")
    _, meta = ds.load_manager_status(root, clock=clock)
    assert meta["available"] is True
    assert meta["age_seconds"] == pytest.approx(9 * 3600, abs=2)
    assert meta["stale"] is True
    assert "expected every" in meta["reason"]


def test_fresh_source_is_not_stale(root):
    p = root / "manager" / "state" / "status.json"
    p.write_text(json.dumps({"collected_at": ago(minutes=3)}), encoding="utf-8")
    _, meta = ds.load_manager_status(root, clock=clock)
    assert meta["stale"] is False
    assert meta["reason"] is None


def test_future_timestamp_is_flagged_as_clock_skew(root):
    """A future stamp must not read as "very fresh" — that is the wrong direction."""
    p = root / "data" / "dashboard" / "engine_state.json"
    p.write_text(json.dumps({
        "engine_status": "running",
        "_meta": {"written_at_utc": (NOW + timedelta(hours=3)).isoformat(),
                  "interval_seconds": 300}}), encoding="utf-8")
    _, meta = ds.load_engine_state(root, None, clock)
    assert meta["clock_skew"] is True
    assert meta["stale"] is True
    assert "FUTURE" in meta["reason"]


def test_engine_staleness_scales_with_the_scan_interval(root):
    """A slow interval must not read as a dead engine."""
    p = root / "data" / "dashboard" / "engine_state.json"

    p.write_text(json.dumps({"engine_status": "running", "_meta": {
        "written_at_utc": ago(minutes=20), "interval_seconds": 300}}))
    _, fast = ds.load_engine_state(root, None, clock)
    assert fast["stale"] is True
    assert fast["expected_max_age_seconds"] == pytest.approx(900)

    p.write_text(json.dumps({"engine_status": "running", "_meta": {
        "written_at_utc": ago(minutes=20), "interval_seconds": 1800}}))
    _, slow = ds.load_engine_state(root, None, clock)
    assert slow["stale"] is False
    assert slow["expected_max_age_seconds"] == pytest.approx(5400)


# ── per-loader specifics ──────────────────────────────────────────────


def test_missing_status_json_reason_names_the_collector(root):
    _, meta = ds.load_manager_status(root, clock=clock)
    assert "collector cron" in meta["reason"]


def test_missing_rollup_reason_gives_the_command_to_run(root):
    _, meta = ds.load_rollup(root, None, clock)
    assert "build_dashboard_rollup" in meta["reason"]


def test_open_positions_wrong_shape_is_rejected(root):
    p = root / "data" / "dashboard" / "open_positions.json"
    p.write_text(json.dumps({"written_at_utc": ago(seconds=10),
                             "rows": "not a list"}))
    payload, meta = ds.load_open_positions(root, None, clock)
    assert payload is None
    assert meta["available"] is False


def test_open_positions_reports_truncation(root):
    p = root / "data" / "dashboard" / "open_positions.json"
    p.write_text(json.dumps({"written_at_utc": ago(seconds=10),
                             "truncated": True, "capped_at": 400,
                             "rows": [{"pod_id": "P-001"}]}))
    payload, meta = ds.load_open_positions(root, None, clock)
    assert payload is not None
    assert meta["truncated"] is True
    assert meta["rows"] == 1


def test_rollup_meta_surfaces_the_horizon_counters(root):
    p = root / "data" / "dashboard" / "rollup.json"
    p.write_text(json.dumps({"built_at_utc": ago(minutes=5),
                             "_checkpoint": {"rows_total": 12345,
                                             "pruned_sources_counted": 7}}))
    _, meta = ds.load_rollup(root, None, clock)
    assert meta["rows_total"] == 12345
    assert meta["pruned_sources_counted"] == 7


# ── the p022 window tail ──────────────────────────────────────────────


def test_p022_tail_returns_the_newest_parseable_row(root):
    p = root / "data" / "p022_window_check" / "status.jsonl"
    p.write_text("\n".join([
        json.dumps({"state": "OLD", "iso": ago(hours=5)}),
        "{ garbage",
        json.dumps({"state": "NEWEST", "iso": ago(minutes=7),
                    "funnel": {"markets_listed": 5}}),
    ]) + "\n", encoding="utf-8")
    row, meta = ds.tail_p022_window(root, clock)
    assert row["state"] == "NEWEST"
    assert meta["available"] is True
    assert meta["stale"] is False


def test_p022_missing_names_the_checker(root):
    row, meta = ds.tail_p022_window(root, clock)
    assert row is None
    assert "p022_window_check" in meta["reason"]


def test_p022_all_rows_unparseable(root):
    p = root / "data" / "p022_window_check" / "status.jsonl"
    p.write_text("nope\nalso nope\n", encoding="utf-8")
    backdate(p)
    row, meta = ds.tail_p022_window(root, clock)
    assert row is None
    assert meta["available"] is False
    assert "no parseable JSON row" in meta["reason"]


# ── kill switches ─────────────────────────────────────────────────────


def test_kill_switches_report_presence_and_mtime(root):
    (root / "data" / "KILL_MAKER").write_text("")
    out = ds.kill_switches(root)
    by = {k["name"]: k for k in out}
    assert by["KILL_MAKER"]["present"] is True
    assert by["KILL_MAKER"]["mtime_utc"]
    assert by["KILL_ROUND_LEADER_FADE"]["present"] is False
    assert set(by) == set(ds.KILL_SWITCH_NAMES)


# ── aggregate ─────────────────────────────────────────────────────────


def test_load_all_on_an_empty_tree_never_raises(root):
    out = ds.load_all(root, clock=clock)
    assert set(out["sources"]) == {"engine_state", "open_positions",
                                  "manager_status", "rollup", "p022_window",
                                  "clv"}
    for name, meta in out["sources"].items():
        assert meta["available"] is False, name
        assert meta["reason"], name
        assert meta["path"], name
    assert out["engine_state"] is None
    assert out["rollup"] is None


def test_load_all_missing_directory_entirely(tmp_path: Path):
    """Not even data/ exists — the fresh-checkout case."""
    out = ds.load_all(tmp_path, clock=clock)
    assert all(not m["available"] for m in out["sources"].values())
    assert out["kill_switches"]


def test_fingerprint_changes_when_a_source_changes(root):
    a = ds.source_fingerprint(root)
    p = root / "data" / "dashboard" / "rollup.json"
    p.write_text('{"schema":2}')
    b = ds.source_fingerprint(root)
    assert a != b
    assert b == ds.source_fingerprint(root), "fingerprint must be stable"


def test_fingerprint_is_stable_for_an_absent_tree(tmp_path: Path):
    assert ds.source_fingerprint(tmp_path) == ds.source_fingerprint(tmp_path)


def test_reuses_manager_collect_helpers():
    """The dashboard must share tail/parse helpers with the alerting path.

    If these diverge, the dashboard and the pager can disagree about what a
    timestamp means, and then neither number is trustworthy.
    """
    from manager import collect
    assert ds.tail_lines is collect.tail_lines
    assert ds.parse_ts is collect.parse_ts
