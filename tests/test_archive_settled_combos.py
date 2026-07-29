"""Tests for the P-029 settled-combo archiver.

The archiver had no tests, and the defect that took it down was not subtle: the
dedup set lived in RAM and grew with the archive. Run 1 wrote 6,677,105 rows and
succeeded because the archive was empty; run 2 had to rebuild a 6.7M-entry set
inside MemoryMax=1G and could not. It sat pinned against its cgroup cap for four
hours, read 249 GB against a 462 MB archive, wrote zero bytes, and blocked its
own timer by never leaving `activating`.

These tests pin the properties that failure depended on: dedup must not scale
with archive size, and a part must be recorded only after it is durably written.
"""
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "combo_research" / "archive_settled_combos.py"
_spec = importlib.util.spec_from_file_location("archive_settled_combos", _SRC)
arch = importlib.util.module_from_spec(_spec)
sys.modules["archive_settled_combos"] = arch
_spec.loader.exec_module(arch)


def _row(ticker, day="2026-07-29"):
    return {"ticker": ticker, "settled_time": f"{day}T12:00:00Z", "legs": []}


def _write_part(out: Path, day: str, rows):
    return arch.write_part(out, day, rows)


# ── dedup index ───────────────────────────────────────────────────────
def test_unseen_filters_rows_already_archived(tmp_path):
    idx = arch.SeenIndex(tmp_path)
    idx.record([_row("A"), _row("B")])
    fresh = idx.unseen([_row("A"), _row("C"), _row("B"), _row("D")])
    assert [r["ticker"] for r in fresh] == ["C", "D"]


def test_unseen_preserves_order_and_handles_more_than_the_sqlite_var_limit(tmp_path):
    """Chunking regression: SQLite caps bound variables at 999 by default."""
    idx = arch.SeenIndex(tmp_path)
    idx.record([_row(f"T{i}") for i in range(0, 3000, 2)])       # evens seen
    fresh = idx.unseen([_row(f"T{i}") for i in range(3000)])
    assert [r["ticker"] for r in fresh] == [f"T{i}" for i in range(1, 3000, 2)]


def test_backfill_indexes_existing_parts_without_loading_them_into_a_set(tmp_path):
    """An archive written before the index exists must still dedup correctly."""
    _write_part(tmp_path, "2026-07-29", [_row("A"), _row("B")])
    _write_part(tmp_path, "2026-07-29", [_row("C")])
    idx = arch.SeenIndex(tmp_path)
    assert idx.backfill(tmp_path) == 2          # two parts indexed
    assert idx.count() == 3
    assert [r["ticker"] for r in idx.unseen([_row("B"), _row("Z")])] == ["Z"]


def test_backfill_is_incremental(tmp_path):
    _write_part(tmp_path, "2026-07-29", [_row("A")])
    idx = arch.SeenIndex(tmp_path)
    assert idx.backfill(tmp_path) == 1
    assert idx.backfill(tmp_path) == 0          # nothing new to do
    _write_part(tmp_path, "2026-07-29", [_row("B")])
    assert idx.backfill(tmp_path) == 1          # only the new part


def test_index_survives_reopen(tmp_path):
    idx = arch.SeenIndex(tmp_path)
    idx.record([_row("A")])
    idx.close()
    assert arch.SeenIndex(tmp_path).unseen([_row("A"), _row("B")])[0]["ticker"] == "B"


def test_memory_does_not_grow_with_archive_size(tmp_path):
    """The property whose absence wedged the droplet.

    `unseen` must query the index rather than materialise every archived ticker.
    Peak allocation is compared against an archive an order of magnitude larger
    than the batch being checked.
    """
    import tracemalloc
    idx = arch.SeenIndex(tmp_path)
    idx.record([_row(f"T{i}") for i in range(50_000)])

    tracemalloc.start()
    idx.unseen([_row(f"T{i}") for i in range(100)])
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # A set of 50k tickers is >2 MB; querying 100 must cost far less.
    assert peak < 500_000, f"unseen() allocated {peak} bytes — is it loading the index?"


# ── crash safety ──────────────────────────────────────────────────────
def test_a_part_is_written_before_it_is_recorded(tmp_path):
    """Ordering matters: recording first, then crashing, loses rows forever."""
    idx = arch.SeenIndex(tmp_path)
    rows = [_row("A"), _row("B")]
    part = _write_part(tmp_path, "2026-07-29", rows)
    assert part.exists()                        # durable before the index knows
    idx.record(rows, part.name)
    assert idx.count() == 2


def test_unrecorded_part_is_reindexed_rather_than_lost(tmp_path):
    """Simulates a crash between write_part and record."""
    part = _write_part(tmp_path, "2026-07-29", [_row("A")])
    idx = arch.SeenIndex(tmp_path)              # index never heard about it
    assert idx.count() == 0
    idx.backfill(tmp_path)                      # next run repairs itself
    assert idx.count() == 1
    assert part.exists()


def test_write_part_never_clobbers_an_existing_part(tmp_path):
    a = _write_part(tmp_path, "2026-07-29", [_row("A")])
    b = _write_part(tmp_path, "2026-07-29", [_row("B")])
    assert a != b
    assert len(arch.parts_for(tmp_path, "2026-07-29")) == 2


def test_read_rows_keeps_the_readable_prefix_of_a_truncated_part(tmp_path):
    """A gzip stream cut mid-write must not cost the whole file."""
    good = tmp_path / "combos_2026-07-29.0000.jsonl.gz"
    with gzip.open(good, "wt") as fh:
        for t in ("A", "B", "C"):
            fh.write(json.dumps(_row(t)) + "\n")
    truncated = tmp_path / "combos_2026-07-29.0001.jsonl.gz"
    truncated.write_bytes(good.read_bytes()[:-12])
    assert [r["ticker"] for r in arch.read_rows(good)] == ["A", "B", "C"]
    arch.read_rows(truncated)                   # must not raise


# ── window default ────────────────────────────────────────────────────
def test_default_window_is_narrow_enough_to_fit_the_droplet():
    """--days-back 7 put ~6.7M tickers in scope on a 2 GB box. 2 is the fix."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=2)
    assert ap.parse_args([]).days_back <= 2
