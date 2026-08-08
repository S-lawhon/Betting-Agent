"""
tests/test_throughput.py
────────────────────────
Tests for `manager/throughput.py` — the standing instrument for observation
throughput per gate.

The properties that matter are the ones that stop it becoming another
comfortable number:

  * **Unmeasurable must never render as fine, or as dead.** A gate whose
    sanctioned reader failed gets `None`, not a projection off a stale figure.
    That is the house rule `_gate_progress` already enforces, and it is the fix
    for P-017's hand-typed `progress: 1`.
  * **Silence must be distinguishable from patience.** A stall is only raised
    once the gap exceeds what that gate's own event cadence explains.
  * **Archived logs count.** Rotation truncates the live file; a counter that
    reads only `trade_log.jsonl` under-reports by whatever has rotated.
"""
import json
import gzip
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.throughput import (STALL_DAYS, gate_throughput,
                                observations_by_day, summarize)

UTC = timezone.utc
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _write(root: Path, name: str, rows, sub="data/trade_logs"):
    d = root / sub
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _settle(pod, day, market, outcome="WIN"):
    return {"pod_id": pod, "outcome": outcome, "market_id": market,
            "settled_at_utc": f"{day}T12:00:00+00:00"}


def _registry(pod="P-017", metric="settled_tournaments", threshold=8, **gate):
    g = {"metric": metric, "threshold": threshold}
    g.update(gate)
    return {"workstreams": [{"id": pod, "tier": "validating", "gate": g}]}


# ── counting ─────────────────────────────────────────────────────────

def test_counts_settled_observations_by_day(tmp_path):
    _write(tmp_path, "trade_log.jsonl", [
        _settle("P-017", "2026-07-25", "A"),
        _settle("P-017", "2026-07-25", "B"),
        _settle("P-017", "2026-07-26", "C"),
        {"pod_id": "P-017", "outcome": "PLACED", "market_id": "D"},
    ])
    by = observations_by_day(tmp_path)
    assert by["P-017"] == {"2026-07-25": 2, "2026-07-26": 1}


def test_archived_logs_are_counted(tmp_path):
    """Rotation truncates the live file. A counter that reads only the live log
    under-reports by however much has rotated — the same shape as the
    log-rotation incident that hid 177 open positions."""
    _write(tmp_path, "trade_log.jsonl", [_settle("P-017", "2026-07-26", "C")])
    _write(tmp_path, "trade_log.archive_old.jsonl",
           [_settle("P-017", "2026-07-01", "A")],
           sub="data/trade_logs/archive")
    by = observations_by_day(tmp_path)
    assert by["P-017"] == {"2026-07-01": 1, "2026-07-26": 1}


def test_duplicate_rows_are_not_double_counted(tmp_path):
    """A rotated log can carry the same settlement twice."""
    row = _settle("P-017", "2026-07-25", "A")
    _write(tmp_path, "trade_log.jsonl", [row, dict(row)])
    _write(tmp_path, "trade_log.archive.jsonl", [dict(row)])
    assert observations_by_day(tmp_path)["P-017"] == {"2026-07-25": 1}


def test_backup_and_skip_only_archives_are_not_authoritative(tmp_path):
    _write(tmp_path, "trade_log.jsonl", [
        _settle("P-017", "2026-07-25", "live")])
    _write(tmp_path, "trade_log_backup.jsonl", [
        _settle("P-017", "2026-07-24", "backup")])
    _write(tmp_path, "skipped_archive_20260420.jsonl", [
        _settle("P-017", "2026-07-23", "skip")],
        sub="data/trade_logs/archive")

    assert observations_by_day(tmp_path)["P-017"] == {"2026-07-25": 1}


def test_monthly_canonical_archive_is_authoritative(tmp_path):
    archive = tmp_path / "data" / "trade_logs" / "archive"
    archive.mkdir(parents=True)
    with gzip.open(archive / "2026-03.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(_settle("P-001", "2026-03-20", "old")) + "\n")

    assert observations_by_day(tmp_path) == {"P-001": {"2026-03-20": 1}}


def test_voids_count_as_observations_of_throughput(tmp_path):
    """A VOID is excluded from most gate STATISTICS (no risk taken) but it is
    still evidence the machinery is turning, which is what this measures."""
    _write(tmp_path, "trade_log.jsonl", [
        _settle("P-017", "2026-07-25", "A", outcome="VOID")])
    assert observations_by_day(tmp_path)["P-017"] == {"2026-07-25": 1}


# ── projection ───────────────────────────────────────────────────────

def test_projects_resolution_for_a_position_counting_gate(tmp_path):
    rows = [_settle("P-014", "2026-07-%02d" % d, "M%d" % (d * 10 + i))
            for d in range(1, 29) for i in range(2)]        # 2/day for 28 days
    # An older row so the pod's lifetime spans the whole 28d window and the
    # denominator clamps at 28 exactly.
    rows.append(_settle("P-014", "2026-06-01", "OLD"))
    _write(tmp_path, "trade_log.jsonl", rows)
    snap = {"p014": {}}
    recs = gate_throughput(tmp_path, snap,
                           _registry("P-014", "settled_trades", 500,
                                     progress=444),
                           now=NOW)
    r = recs[0]
    assert r["rate_window_days"] == 28.0
    assert r["rate_per_week"] == 56 / 4.0          # 28d window / 4
    assert r["weeks_to_threshold"] == 4.0          # (500-444)/14
    assert r["resolvable_within_12m"] is True


def test_a_gate_that_cannot_resolve_in_12_months_is_flagged(tmp_path):
    _write(tmp_path, "trade_log.jsonl",
           [_settle("P-015", "2026-06-26", "A"),
            _settle("P-015", "2026-07-26", "B")])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-015", "settled_trades", 120,
                                     progress=2),
                           now=NOW)
    r = recs[0]
    # Only one settle falls inside the 28d window; lifetime spans it fully.
    assert r["rate_per_week"] == 0.25
    assert r["weeks_to_threshold"] == 472.0
    assert r["resolvable_within_12m"] is False


def test_rate_denominator_is_the_pods_own_lifetime_not_a_flat_month(tmp_path):
    """The 2026-07-30 P-015 false alarm: 5 settles divided by a flat 4-week
    window read 1.25/week for a pod that had existed 10 days, projecting
    resolution in 2028 off ~18 days of nonexistence."""
    first = (NOW - timedelta(days=10)).strftime("%Y-%m-%d")
    _write(tmp_path, "trade_log.jsonl",
           [{"pod_id": "P-015", "action": "PLACED", "market_id": "P0",
             "timestamp_utc": f"{first}T12:00:00+00:00"}] +
           [_settle("P-015", (NOW - timedelta(days=3)).strftime("%Y-%m-%d"),
                    "M%d" % i) for i in range(5)])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-015", "settled_trades", 120,
                                     progress=5),
                           now=NOW)
    r = recs[0]
    assert r["rate_window_days"] == 10.0
    assert r["rate_per_week"] == 3.5               # 5 / (10/7), not 5/4
    assert r["weeks_to_threshold"] == round((120 - 5) / 3.5, 1)
    assert r["resolvable_within_12m"] is True


def test_rate_denominator_is_floored_at_a_week(tmp_path):
    """A pod that is days old must not project off an explosive rate."""
    day = (NOW - timedelta(days=2)).strftime("%Y-%m-%d")
    _write(tmp_path, "trade_log.jsonl",
           [_settle("P-015", day, "M%d" % i) for i in range(3)])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-015", "settled_trades", 120,
                                     progress=3),
                           now=NOW)
    assert recs[0]["rate_window_days"] == 7.0
    assert recs[0]["rate_per_week"] == 3.0         # 3 / (7/7)


def test_unmeasurable_progress_yields_no_projection(tmp_path):
    """The house rule: a reader that fails returns None rather than falling
    back to a stale number, and None must not become a projection.

    `progress` is absent here, which is what `_gate_progress` returns when a
    sanctioned reader could not run. Rendering that as either "on track" or
    "inert" would be inventing a measurement."""
    _write(tmp_path, "trade_log.jsonl",
           [_settle("P-014", "2026-07-26", "A")])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-014", "settled_trades", 500), now=NOW)
    r = recs[0]
    assert r["progress"] is None
    assert r["weeks_to_threshold"] is None
    assert r["resolvable_within_12m"] is None


def test_tournament_gates_get_no_position_based_projection(tmp_path):
    """P-017 and P-022 count TOURNAMENTS. Projecting them off settled
    positions would count the easy thing instead of the gate's thing — which
    is exactly how P-017 came to report `1` for a tournament it had merely
    entered."""
    _write(tmp_path, "trade_log.jsonl",
           [_settle("P-017", "2026-07-2%d" % d, "M%d" % d) for d in range(5)])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-017", "settled_tournaments", 8,
                                     progress=1), now=NOW)
    assert recs[0]["rate_per_week"] is None
    assert recs[0]["settled_positions_28d"] == 5


# ── stalls ───────────────────────────────────────────────────────────

def test_silence_within_the_event_cadence_is_not_a_stall(tmp_path):
    """Tennis qualifying is genuinely lumpy. Alarming on every legitimate gap
    trains the alarm to be ignored, which is the same failure as no alarm."""
    day = (NOW - timedelta(days=20)).strftime("%Y-%m-%d")
    _write(tmp_path, "trade_log.jsonl", [_settle("P-015", day, "A")])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-015", "settled_trades", 120), now=NOW)
    assert STALL_DAYS["P-015"] == 45.0
    assert recs[0]["stalled"] is False


def test_silence_beyond_the_event_cadence_is_a_stall(tmp_path):
    day = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
    snap = {"p022": {"checkpoint": {
        "tournament_observations": [
            {"event": "A", "settled_at_utc": day + "T12:00:00Z",
             "eligible": True}],
    }}}
    recs = gate_throughput(
        tmp_path, snap,
        _registry("P-022", "settled_tournaments", 14, progress=1), now=NOW)
    assert recs[0]["stalled"] is True
    assert recs[0]["days_since_last_settlement"] == 30


def test_a_gate_that_has_never_produced_is_unknown_not_healthy(tmp_path):
    """P-022 has never settled anything. That is not 'no stall' — it is the
    absence of evidence either way, and it must not read as health."""
    _write(tmp_path, "trade_log.jsonl", [])
    snap = {"p022": {"checkpoint": {"tournament_observations": []}}}
    recs = gate_throughput(tmp_path, snap,
                           _registry("P-022", "settled_tournaments", 14,
                                     progress=0), now=NOW)
    assert recs[0]["stalled"] is None
    assert recs[0]["last_settlement_utc"] is None


def test_only_validating_workstreams_are_measured(tmp_path):
    _write(tmp_path, "trade_log.jsonl", [_settle("P-013", "2026-07-26", "A")])
    reg = {"workstreams": [{"id": "P-013", "tier": "none",
                            "gate": {"metric": "settled_trades",
                                     "threshold": 10}}]}
    assert gate_throughput(tmp_path, {}, reg, now=NOW) == []


def test_summarize_counts_the_things_worth_alarming_on(tmp_path):
    day = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
    snap = {"p022": {"checkpoint": {"tournament_observations": [
        {"event": "A", "settled_at_utc": day + "T12:00:00Z",
         "eligible": True}]}}}
    recs = gate_throughput(
        tmp_path, snap,
        _registry("P-022", "settled_tournaments", 14, progress=1), now=NOW)
    s = summarize(recs)
    assert s["n_gates"] == 1 and s["n_stalled"] == 1 and s["n_zero_28d"] == 1


def test_p022_recent_activity_comes_from_checkpoint_tournaments(tmp_path):
    snap = {"p022": {"checkpoint": {
        "tournament_observations": [
            {"event": "A", "settled_at_utc": "2026-07-25T12:00:00Z",
             "eligible": True},
            {"event": "B", "settled_at_utc": "2026-07-26T12:00:00Z",
             "eligible": True},
            {"event": "X", "settled_at_utc": "2026-07-26T12:00:00Z",
             "eligible": False},
        ]}}}
    rec = gate_throughput(
        tmp_path, snap,
        _registry("P-022", "settled_tournaments", 24, progress=2), now=NOW)[0]
    assert rec["settled_positions_7d"] == 2
    assert rec["settled_positions_28d"] == 2
    assert rec["observation_unit"] == "tournaments"
    assert rec["last_settlement_utc"] == "2026-07-26"
    assert rec["stalled"] is False


def test_p022_missing_observation_timestamps_are_unknown_not_zero(tmp_path):
    snap = {"p022": {"checkpoint": {"tournament_observations": [
        {"event": "A", "settled_at_utc": None, "eligible": True}]}}}
    rec = gate_throughput(
        tmp_path, snap,
        _registry("P-022", "settled_tournaments", 24, progress=1), now=NOW)[0]
    assert rec["settled_positions_28d"] is None
    assert rec["activity_available"] is False
    assert rec["stalled"] is None


# ── the probe must work in the way it is actually INVOKED ────────────

def test_collector_throughput_probe_works_when_run_as_a_script(tmp_path):
    """The regression that shipped to production on 2026-07-27.

    `manager/collect.py` is invoked as a SCRIPT (`python manager/collect.py`),
    so `sys.path[0]` is `manager/` and `import manager.throughput` raises
    ModuleNotFoundError. Under pytest the repo root IS on sys.path, so importing
    `manager.throughput` directly — which every test above does — passes while
    the real invocation path fails.

    `@safe` then swallowed it into a fault, and `check_throughput` guards on
    `available`, so the instrument built to make silent failure visible was
    itself silently failing on every collector run.

    This test runs the collector the way cron runs it, in a subprocess, and
    asserts the probe actually produced records.
    """
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parent.parent
    _write(tmp_path, "trade_log.jsonl", [
        _settle("P-017", "2026-07-25", "A"), _settle("P-017", "2026-07-26", "B")])
    state = tmp_path / "state"
    res = subprocess.run(
        [_sys.executable, str(root / "manager" / "collect.py"),
         "--root", str(tmp_path), "--state-dir", str(state)],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]

    snap = json.loads((state / "status.json").read_text())
    faults = {f.get("probe") for f in (snap.get("faults") or [])}
    assert "throughput" not in faults, (
        "the throughput probe faulted in the real invocation path: "
        f"{snap.get('faults')}")
    assert (snap.get("throughput") or {}).get("available") is True
