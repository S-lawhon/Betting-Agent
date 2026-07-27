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
    _write(tmp_path, "old.jsonl", [_settle("P-017", "2026-07-01", "A")],
           sub="data/trade_logs/archive")
    by = observations_by_day(tmp_path)
    assert by["P-017"] == {"2026-07-01": 1, "2026-07-26": 1}


def test_duplicate_rows_are_not_double_counted(tmp_path):
    """A rotated log can carry the same settlement twice."""
    row = _settle("P-017", "2026-07-25", "A")
    _write(tmp_path, "trade_log.jsonl", [row, dict(row)])
    _write(tmp_path, "trade_log.archive.jsonl", [dict(row)])
    assert observations_by_day(tmp_path)["P-017"] == {"2026-07-25": 1}


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
    _write(tmp_path, "trade_log.jsonl", rows)
    snap = {"p014": {}}
    recs = gate_throughput(tmp_path, snap,
                           _registry("P-014", "settled_trades", 500,
                                     progress=444),
                           now=NOW)
    r = recs[0]
    assert r["rate_per_week"] == 56 / 4.0          # 28d window / 4
    assert r["weeks_to_threshold"] == 4.0          # (500-444)/14
    assert r["resolvable_within_12m"] is True


def test_a_gate_that_cannot_resolve_in_12_months_is_flagged(tmp_path):
    _write(tmp_path, "trade_log.jsonl",
           [_settle("P-015", "2026-07-26", "A"),
            _settle("P-015", "2026-07-26", "B")])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-015", "settled_trades", 120,
                                     progress=2),
                           now=NOW)
    r = recs[0]
    assert r["rate_per_week"] == 0.5
    assert r["weeks_to_threshold"] == 236.0
    assert r["resolvable_within_12m"] is False


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
    _write(tmp_path, "trade_log.jsonl", [_settle("P-022", day, "A")])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-022", "settled_tournaments", 14), now=NOW)
    assert recs[0]["stalled"] is True
    assert recs[0]["days_since_last_settlement"] == 30


def test_a_gate_that_has_never_produced_is_unknown_not_healthy(tmp_path):
    """P-022 has never settled anything. That is not 'no stall' — it is the
    absence of evidence either way, and it must not read as health."""
    _write(tmp_path, "trade_log.jsonl", [])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-022", "settled_tournaments", 14), now=NOW)
    assert recs[0]["stalled"] is None
    assert recs[0]["last_settlement_utc"] is None


def test_only_validating_workstreams_are_measured(tmp_path):
    _write(tmp_path, "trade_log.jsonl", [_settle("P-013", "2026-07-26", "A")])
    reg = {"workstreams": [{"id": "P-013", "tier": "none",
                            "gate": {"metric": "settled_trades",
                                     "threshold": 10}}]}
    assert gate_throughput(tmp_path, {}, reg, now=NOW) == []


def test_summarize_counts_the_things_worth_alarming_on(tmp_path):
    _write(tmp_path, "trade_log.jsonl", [
        _settle("P-022", (NOW - timedelta(days=30)).strftime("%Y-%m-%d"), "A")])
    recs = gate_throughput(tmp_path, {},
                           _registry("P-022", "settled_tournaments", 14), now=NOW)
    s = summarize(recs)
    assert s["n_gates"] == 1 and s["n_stalled"] == 1 and s["n_zero_28d"] == 1
