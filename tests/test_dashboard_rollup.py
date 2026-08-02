"""
tests/test_dashboard_rollup.py
──────────────────────────────
Tests for ``scripts/build_dashboard_rollup.py``.

The load-bearing test in this file is ``test_rotation_does_not_double_count``.
``rotate_active_log.rotate()`` gzips the active log verbatim into an archive and
then rewrites the active log holding the still-open PLACED rows — SAME
fingerprints. The first version of the rollup kept ``(dev, inode, offset)`` per
source and double-counted: rows already consumed from the active log were
counted a second time when the archive containing those bytes showed up as a
"new" immutable source. Measured on this fixture: placed 4 → 6, realized P&L
5.25 → 10.50 after one rotation.

**That test must never be skipped.** It is the only thing standing between the
rebuild and a P&L figure that inflates a little every night at 06:00.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_dashboard_rollup import SCHEMA, build  # noqa: E402
from scripts.rotate_active_log import _read_lines, open_placed_rows  # noqa: E402


# ── fixtures / helpers ────────────────────────────────────────────────


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "trade_logs").mkdir(parents=True)
    (tmp_path / "data" / "dashboard").mkdir(parents=True)
    return tmp_path


def log_path(root: Path) -> Path:
    return root / "data" / "trade_logs" / "trade_log.jsonl"


def out_path(root: Path) -> Path:
    return root / "data" / "dashboard" / "rollup.json"


def append(root: Path, rows, mode: str = "a") -> None:
    with log_path(root).open(mode) as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def run(root: Path, full: bool = False, write: bool = True):
    payload, summary = build(root, out_path(root), full=full)
    if write:
        out_path(root).write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return payload, summary


def skipped(pod="P-001", reason="edge_below_threshold", ts="2026-07-01T12:00:00Z",
            action="SKIPPED_EDGE"):
    return {"action": action, "skip_reason": reason, "pod_id": pod,
            "timestamp_utc": ts}


def placed(fp, pod="P-001", usd=100.0, ts="2026-07-01T13:00:00Z"):
    return {"action": "PLACED", "pod_id": pod, "fingerprint": fp,
            "position_size_usd": usd, "contracts": usd * 2, "timestamp_utc": ts}


def settled(fp, outcome="WIN", pnl=12.5, pod="P-001",
            ts="2026-07-02T20:00:00Z"):
    return {"action": outcome, "outcome": outcome, "pod_id": pod,
            "fingerprint": fp, "pnl_usd": pnl, "settled_at_utc": ts,
            "timestamp_utc": "2026-07-01T13:00:00Z"}


def seed(root: Path) -> None:
    rows = [skipped(ts="2026-07-01T12:0%d:00Z" % i) for i in range(3)]
    rows += [placed("fp0"), placed("fp1", ts="2026-07-01T13:01:00Z")]
    rows += [settled("fp0"),
             placed("fp2", pod="P-017", usd=50.0, ts="2026-07-02T14:00:00Z"),
             skipped(pod="P-017", reason="exposure_cap",
                     ts="2026-07-02T14:05:00Z", action="SKIPPED_RISK")]
    append(root, rows, mode="w")


def rotate(root: Path, stamp: str) -> list:
    """Reproduce scripts/rotate_active_log.rotate() exactly.

    Archive = a verbatim gzip of the whole active log. New active log = only the
    still-open PLACED rows, same fingerprints. Uses the real
    ``open_placed_rows`` so this test cannot drift from the rotator it models.
    """
    lp = log_path(root)
    carried = open_placed_rows([_read_lines(lp)])
    arch = lp.with_name("trade_log.archive_%s.jsonl.gz" % stamp)
    with gzip.open(arch, "wt", encoding="utf-8") as fh:
        fh.write(lp.read_text(encoding="utf-8"))
    tmp = lp.with_suffix(".tmp")
    tmp.write_text("".join(v + "\n" for v in carried.values()), encoding="utf-8")
    os.replace(tmp, lp)          # new inode, exactly like _atomic_write
    return sorted(carried)


LIFE_KEYS = ("rows", "placed", "settled", "wins", "losses", "voids",
             "realized_pnl", "skipped", "gross_wagered_usd")


def life(payload):
    return {k: payload["lifetime"][k] for k in LIFE_KEYS}


# ── the guard ─────────────────────────────────────────────────────────


def test_rotation_does_not_double_count(root: Path):
    """One rotation must not change a single lifetime counter.

    Rotation MOVES rows into an archive and COPIES the still-open PLACED rows
    forward. Nothing new happened, so nothing may change.
    """
    seed(root)
    before, _ = run(root)
    carried = rotate(root, "20260704_060000")
    assert carried, "fixture must have open positions for this test to mean anything"

    after, summary = run(root)

    assert life(after) == life(before), (
        "rotation inflated the counters — the PLACED fingerprint dedupe is broken.\n"
        "before: %r\nafter:  %r" % (life(before), life(after)))
    assert after["coverage"]["carried_placed_rows_skipped"] == len(carried)
    assert after["by_pod"] == before["by_pod"]
    assert after["daily"] == before["daily"]
    assert summary["archives_complete"] == 1


def test_two_rotations_with_activity_between(root: Path):
    """The guard must hold across repeated rotations, not just the first."""
    seed(root)
    run(root)
    rotate(root, "20260704_060000")
    run(root)

    append(root, [placed("fp5", pod="P-014", usd=25.0,
                         ts="2026-07-05T09:00:00Z"),
                  settled("fp1", outcome="LOSS", pnl=-7.25,
                          ts="2026-07-05T21:00:00Z"),
                  skipped(reason="no_sharp_price", ts="2026-07-05T11:00:00Z")])
    mid, _ = run(root)
    assert mid["lifetime"]["placed"] == 4
    assert mid["lifetime"]["settled"] == 2
    assert mid["lifetime"]["realized_pnl"] == pytest.approx(5.25)

    rotate(root, "20260706_060000")
    after, _ = run(root)
    assert life(after) == life(mid)


def test_carried_row_does_not_inflate_rows_or_by_action(root: Path):
    """A carried duplicate must inflate NOTHING — not even the raw row count.

    An earlier version incremented ``rows`` and ``by_action`` before the dedupe
    check, so ``by_action.PLACED`` crept up at every rotation while placed/pnl
    stayed correct. A row rotation copied forward is not an observation in any
    sense.
    """
    seed(root)
    before, _ = run(root)
    rotate(root, "20260704_060000")
    after, _ = run(root)
    assert after["lifetime"]["rows"] == before["lifetime"]["rows"]
    assert after["lifetime"]["by_action"] == before["lifetime"]["by_action"]


# ── incrementality ────────────────────────────────────────────────────


def test_rerun_is_idempotent(root: Path):
    seed(root)
    a, _ = run(root)
    b, _ = run(root)
    assert life(b) == life(a)
    assert b["daily"] == a["daily"]
    assert b["skip_reasons"] == a["skip_reasons"]


def test_incremental_equals_full(root: Path):
    """Feeding the log in chunks must equal one full pass, byte for byte."""
    seed(root)
    run(root)
    append(root, [placed("fp9", usd=10.0, ts="2026-07-03T10:00:00Z")])
    run(root)
    append(root, [settled("fp1", outcome="LOSS", pnl=-7.25,
                          ts="2026-07-03T21:00:00Z"),
                  skipped(reason="no_sharp_price", ts="2026-07-03T11:00:00Z"),
                  skipped(pod="P-014", ts="2026-07-03T11:05:00Z")])
    inc, _ = run(root)
    full, _ = run(root, full=True)

    for key in ("lifetime", "by_pod", "daily", "daily_by_pod", "weekly_by_pod",
                "skip_reasons", "coverage", "open_placed_fingerprints"):
        assert inc[key] == full[key], "incremental != full for %r" % key


def test_archive_consumed_once(root: Path):
    seed(root)
    run(root)
    rotate(root, "20260704_060000")
    first, s1 = run(root)
    assert s1["archive_rows_added_this_run"] > 0
    _, s2 = run(root)
    assert s2["archive_rows_added_this_run"] == 0, "archive re-read"


# ── pruning / the irreplaceable-history contract ──────────────────────


def test_pruned_archive_keeps_its_rows_forever(root: Path):
    """Deleting a counted archive must not change any counter.

    This is the property that makes rollup.json the system of record for
    pre-horizon history — and the reason it must be backed up.
    """
    seed(root)
    run(root)
    rotate(root, "20260704_060000")
    before, _ = run(root)

    arch = log_path(root).with_name("trade_log.archive_20260704_060000.jsonl.gz")
    arch.unlink()

    after, summary = run(root)
    assert life(after) == life(before)
    assert summary["pruned_sources_counted"] == 1
    entry = after["_checkpoint"]["archived_sources"][
        "data/trade_logs/trade_log.archive_20260704_060000.jsonl.gz"]
    assert entry["state"] == "counted_pruned"

    again, _ = run(root)
    assert life(again) == life(before), "pruned rows were re-added"


# ── robustness ────────────────────────────────────────────────────────


def test_bankroll_reset_clears_open_set(root: Path):
    seed(root)
    before, _ = run(root)
    assert before["open_placed_fingerprints"]
    append(root, [{"action": "BANKROLL_RESET",
                   "timestamp_utc": "2026-07-07T00:00:00Z"}])
    after, _ = run(root)
    assert after["open_placed_fingerprints"] == []


def test_malformed_lines_are_counted_not_fatal(root: Path):
    seed(root)
    with log_path(root).open("a") as fh:
        fh.write("{not json\n")
        fh.write("[]\n")            # valid JSON, wrong type
        fh.write("\n")              # blank
        fh.write('"a string"\n')
    payload, _ = run(root)
    assert payload["coverage"]["rows_unparseable"] == 3


def test_truncated_active_log_is_rescanned(root: Path):
    seed(root)
    before, _ = run(root)
    log_path(root).write_text("", encoding="utf-8")
    after, _ = run(root)
    # The log is now empty, so the tail contributes nothing; nothing was
    # archived, so lifetime drops to zero rather than keeping phantom rows.
    assert after["lifetime"]["placed"] == 0
    assert before["lifetime"]["placed"] > 0


def test_empty_tree_produces_a_valid_rollup(root: Path):
    payload, summary = run(root, write=False)
    assert payload["schema"] == SCHEMA
    assert payload["lifetime"]["rows"] == 0
    assert payload["daily"] == []
    assert payload["by_pod"] == {}
    assert summary["ok"] is True


def test_schema_bump_forces_rebuild(root: Path):
    seed(root)
    run(root)
    stale = json.loads(out_path(root).read_text())
    stale["schema"] = SCHEMA - 1
    out_path(root).write_text(json.dumps(stale))
    payload, _ = run(root)
    assert payload["schema"] == SCHEMA
    assert payload["lifetime"]["placed"] == 3


def test_unreadable_prior_rollup_rebuilds(root: Path):
    seed(root)
    out_path(root).write_text("{{{ not json")
    payload, _ = run(root)
    assert payload["lifetime"]["placed"] == 3


# ── correctness of the aggregates ─────────────────────────────────────


def test_counts_and_attribution(root: Path):
    seed(root)
    p, _ = run(root)
    lt = p["lifetime"]
    assert lt["placed"] == 3
    assert lt["settled"] == 1
    assert lt["wins"] == 1 and lt["losses"] == 0 and lt["voids"] == 0
    assert lt["skipped"] == 4
    assert lt["realized_pnl"] == pytest.approx(12.5)
    assert lt["by_action"] == {"PLACED": 3, "SKIPPED_EDGE": 3, "WIN": 1,
                              "SKIPPED_RISK": 1}
    assert p["by_pod"]["P-017"]["placed"] == 1
    assert p["by_pod"]["P-001"]["skipped"] == 3


def test_skip_reason_histogram_matches_hand_count(root: Path):
    append(root, [skipped(reason="edge_below_threshold") for _ in range(5)]
           + [skipped(reason="no_sharp_price") for _ in range(2)]
           + [skipped(pod="P-017", reason="outside_band")], mode="w")
    p, _ = run(root)
    by_pod = p["skip_reasons"]["lifetime_by_pod"]
    assert by_pod["P-001"] == {"edge_below_threshold": 5, "no_sharp_price": 2}
    assert by_pod["P-017"] == {"outside_band": 1}
    assert p["coverage"]["rows_with_skip_reason"] == 8


def test_daily_series_and_equity_inputs(root: Path):
    seed(root)
    p, _ = run(root)
    days = {row[0]: row for row in p["daily"]}
    assert days["2026-07-01"][3] == 2          # placed
    assert days["2026-07-01"][4] == 3          # skipped
    assert days["2026-07-02"][1] == pytest.approx(12.5)   # realized_pnl
    assert days["2026-07-02"][2] == 1          # settled


def test_rows_missing_pod_id_attributed_and_counted(root: Path):
    append(root, [{"action": "PLACED", "fingerprint": "x1",
                   "position_size_usd": 10.0,
                   "timestamp_utc": "2026-07-01T10:00:00Z"}], mode="w")
    p, _ = run(root)
    assert p["by_pod"]["P-001"]["placed"] == 1
    assert p["coverage"]["rows_missing_pod_id"] == 1


def test_clv_is_read_with_the_logs_own_field_names(root: Path):
    """clv_log.jsonl uses pinn_fair_close / clv_net_maker, NOT src/clv.py's names."""
    clv = root / "data" / "trade_logs" / "clv_log.jsonl"
    clv.write_text("\n".join(json.dumps(r) for r in [
        {"fingerprint": "a", "pod_id": "P-001", "clv_gross": 2.0,
         "clv_net_maker": 1.0, "outcome": "WIN", "settled_at": "2026-07-02T00:00:00Z"},
        {"fingerprint": "b", "pod_id": "P-001", "clv_gross": 4.0,
         "clv_net_maker": 3.0, "outcome": "LOSS", "settled_at": "2026-07-03T00:00:00Z"},
        {"fingerprint": "c", "pod_id": "P-001", "sharp_fair_close": 9.0},
    ]) + "\n", encoding="utf-8")
    seed(root)
    p, _ = run(root)
    b = p["clv"]["by_pod"]["P-001"]
    assert b["n"] == 2, "a row with only src/clv.py-style names must not count"
    assert b["clv_gross_sum"] == pytest.approx(6.0)
    assert b["clv_net_maker_sum"] == pytest.approx(4.0)
    assert b["n_settled"] == 2


def test_clv_is_not_double_counted_across_runs(root: Path):
    clv = root / "data" / "trade_logs" / "clv_log.jsonl"
    clv.write_text(json.dumps(
        {"pod_id": "P-001", "clv_gross": 2.0, "clv_net_maker": 1.0}) + "\n",
        encoding="utf-8")
    seed(root)
    run(root)
    p, _ = run(root)
    assert p["clv"]["by_pod"]["P-001"]["n"] == 1


# ── resources ─────────────────────────────────────────────────────────


def test_memory_stays_bounded_on_a_large_log(root: Path):
    """200k rows must not be held in memory. Counters are O(pods x days)."""
    rows = []
    for i in range(200_000):
        day = 1 + (i % 28)
        rows.append({"action": "SKIPPED_EDGE",
                     "skip_reason": "r%d" % (i % 7),
                     "pod_id": "P-%03d" % (i % 5),
                     "timestamp_utc": "2026-07-%02dT10:00:00Z" % day})
    append(root, rows, mode="w")

    tracemalloc.start()
    try:
        payload, _ = run(root, write=False)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert payload["lifetime"]["skipped"] == 200_000
    assert peak < 40 * 1024 * 1024, "peak %.1f MB — the log is being buffered" % (
        peak / 1e6)


def test_atomicity_failed_write_leaves_prior_file_intact(root: Path, monkeypatch):
    """A crash during the write must not advance the checkpoint or corrupt the file."""
    import scripts.build_dashboard_rollup as mod

    seed(root)
    run(root)
    good = out_path(root).read_bytes()

    append(root, [placed("fpZ", ts="2026-07-09T10:00:00Z")])

    def boom(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "_atomic_write", boom)
    with pytest.raises(OSError):
        mod.main(["--root", str(root)])

    assert out_path(root).read_bytes() == good, "prior rollup was modified"
    restored = json.loads(out_path(root).read_text())
    assert restored["lifetime"]["placed"] == 3, "checkpoint advanced despite failure"


def test_no_tmp_files_left_behind(root: Path):
    seed(root)
    run(root)
    leftovers = [p.name for p in (root / "data" / "dashboard").iterdir()
                 if p.name.startswith(".") or p.name.endswith(".tmp")]
    assert leftovers == []


# ── CLI ───────────────────────────────────────────────────────────────


def test_cli_dry_run_writes_nothing(root: Path, capsys):
    import scripts.build_dashboard_rollup as mod
    seed(root)
    assert mod.main(["--root", str(root), "--dry-run", "--json"]) == 0
    assert not out_path(root).exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is False
    assert payload["placed"] == 3


def test_cli_writes_and_backs_up(root: Path):
    import scripts.build_dashboard_rollup as mod
    seed(root)
    assert mod.main(["--root", str(root), "--backup"]) == 0
    assert out_path(root).exists()
    bak = out_path(root).with_name("rollup.json.bak.gz")
    assert bak.exists(), "the timer relies on --backup; rollup.json is not rebuildable"
    with gzip.open(bak, "rt", encoding="utf-8") as fh:
        assert json.load(fh)["lifetime"]["placed"] == 3


# ── aggregate-risk shadow log ─────────────────────────────────────────


def _shadow(root: Path, rows) -> None:
    path = root / "data" / "trade_logs" / "aggregate_risk_shadow.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row(day="2026-08-02", pod="P-017", kind="venue_exposure", usd=25.0):
    return {
        "timestamp_utc": day + "T12:00:00+00:00",
        "pod_id": pod, "venue": "kalshi", "market_id": "MKT",
        "position_size_usd": usd, "breach_kind": kind,
        "breach_detail": "…", "measured_pct": 0.32, "limit_pct": 0.30,
    }


def test_shadow_absent_when_guard_is_enforcing(root: Path):
    """No shadow log is the NORMAL state under enforcement — nothing
    writes it then. It must read as unavailable, not as zero blocks."""
    payload, summary = build(root, root / "data" / "dashboard" / "rollup.json")
    assert payload["shadow_risk"]["available"] is False
    assert summary["shadow_risk_rows"] == 0


def test_shadow_aggregates_by_kind_pod_and_day(root: Path):
    _shadow(root, [
        _row(kind="venue_exposure", pod="P-017"),
        _row(kind="venue_exposure", pod="P-017"),
        _row(kind="total_exposure", pod="P-015", usd=10.0),
        _row(day="2026-08-01", kind="halted", pod="P-015", usd=5.0),
    ])
    payload, summary = build(root, root / "data" / "dashboard" / "rollup.json")
    sh = payload["shadow_risk"]

    assert sh["available"] is True
    assert sh["lifetime"]["total"] == 4
    assert sh["lifetime"]["by_kind"] == {
        "venue_exposure": 2, "total_exposure": 1, "halted": 1}
    assert sh["lifetime"]["by_pod"]["P-017"] == {"venue_exposure": 2}
    assert sh["lifetime"]["usd_passed_through"] == 65.0
    assert sh["by_day"]["2026-08-02"]["venue_exposure"] == 2
    assert sh["by_day"]["2026-08-01"] == {"halted": 1}
    assert sh["last_utc"] == "2026-08-02T12:00:00+00:00"
    assert summary["shadow_risk_rows"] == 4


def test_shadow_is_recomputed_not_accumulated(root: Path):
    """It is NOT part of the archive/tail checkpoint machinery. A second
    run over the same file must not double the counts."""
    _shadow(root, [_row(), _row()])
    out = root / "data" / "dashboard" / "rollup.json"
    p1, _ = build(root, out)
    out.write_text(json.dumps(p1))
    p2, _ = build(root, out)
    assert p2["shadow_risk"]["lifetime"]["total"] == 2


def test_shadow_survives_corrupt_lines(root: Path):
    path = root / "data" / "trade_logs" / "aggregate_risk_shadow.jsonl"
    path.write_text(json.dumps(_row()) + "\n{ truncated\n" + json.dumps(_row()) + "\n")
    sh = build(root, root / "data" / "dashboard" / "rollup.json")[0]["shadow_risk"]
    assert sh["lifetime"]["total"] == 2
    assert sh["rows_unparseable"] == 1
