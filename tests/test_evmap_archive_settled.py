"""Crash-safety and resume tests for the EV-Map settled archive."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "kalshi-ev-map" / "src"
sys.path.insert(0, str(SRC_DIR))
SPEC = importlib.util.spec_from_file_location(
    "evmap_archive_settled", SRC_DIR / "archive_settled.py")
archive = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(archive)


def market(ticker, *, mve=False, volume="1.0000"):
    return {
        "ticker": ticker,
        "event_ticker": "KXTEST-26",
        "title": ticker,
        "result": "yes",
        "settled_time": "2026-08-16T00:00:00Z",
        "volume_fp": volume,
        "mve_collection_ticker": "MVE" if mve else None,
    }


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(archive, "BASE", data / "settled_archive.parquet")
    monkeypatch.setattr(archive, "PARTS", data / "settled_archive_parts")
    monkeypatch.setattr(archive, "INDEX", data / "settled_archive_index.sqlite")
    monkeypatch.setattr(archive, "PROGRESS", data / "archive_progress.json")
    monkeypatch.setattr(archive, "STATE", data / "archive_state.json")
    monkeypatch.setattr(
        archive, "LEGACY_RAW", data / "settled_archive.parquet.raw")
    return data


def write_base(path, tickers, row_group_size=2):
    frame = archive.build_frame([market(t) for t in tickers])
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path,
                   row_group_size=row_group_size)
    return path


def test_atomic_part_is_readable_and_leaves_no_temporary(sandbox):
    frame = archive.build_frame([market("A"), market("B")])
    part = archive.PARTS / "run.part-000000.parquet"
    archive._atomic_parquet(frame, part)
    assert pq.ParquetFile(part).metadata.num_rows == 2
    assert not part.with_name(part.name + ".tmp").exists()


def test_temporary_and_legacy_raw_files_are_not_archive_sources(sandbox):
    write_base(archive.BASE, ["A"])
    archive.PARTS.mkdir()
    (archive.PARTS / "unfinished.parquet.tmp").write_bytes(b"partial")
    archive.LEGACY_RAW.write_bytes(b"partial")
    assert archive.archive_sources() == [archive.BASE]


def test_corrupt_committed_part_fails_closed(sandbox):
    archive.PARTS.mkdir()
    bad = archive.PARTS / "bad.parquet"
    bad.write_bytes(b"not parquet")
    with archive.ArchiveIndex(archive.INDEX) as index:
        with pytest.raises(Exception, match="Parquet|magic bytes"):
            archive.backfill_index(index)


def test_legacy_index_backfill_resumes_by_row_group(sandbox):
    write_base(archive.BASE, ["A", "B", "C"], row_group_size=1)
    with archive.ArchiveIndex(archive.INDEX) as index:
        complete, rows = index.index_next_row_group(archive.BASE)
        assert complete is False and rows == 1
        assert index.source(archive.BASE)["row_groups_done"] == 1
    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.backfill_index(index) is True
        assert index.count() == 3
        assert index.source(archive.BASE)["complete"] == 1


def test_changed_committed_source_is_rejected(sandbox):
    write_base(archive.BASE, ["A"])
    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.backfill_index(index)
        write_base(archive.BASE, ["A", "B"])
        with pytest.raises(RuntimeError, match="committed archive source changed"):
            archive.backfill_index(index)


def test_cursor_resume_and_exact_deduplication(sandbox):
    calls = []
    pages = {
        None: {"markets": [market("A"), market("B")], "cursor": "c1"},
        "c1": {"markets": [market("B"), market("C")], "cursor": "c2"},
        "c2": {"markets": [market("D")], "cursor": None},
    }

    def get_page(_path, params):
        calls.append(params.get("cursor"))
        return pages[params.get("cursor")]

    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.ingest(index, pages_per_part=1, get_page=get_page,
                              max_parts=1) is False
        assert index.get_meta("ingest")["cursor"] == "c1"
        assert index.count() == 2

    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.ingest(index, pages_per_part=1, get_page=get_page) is True
        assert index.count() == 4
        assert archive.finalize(index) is True

    assert calls == [None, "c1", "c2"]
    state = json.loads(archive.STATE.read_text())
    assert state["archive_layout"] == "immutable_base_plus_parts"
    assert len(archive.archive_sources()) == 3


def test_orphan_part_is_indexed_before_refetched_page(sandbox):
    """Simulate a kill after atomic rename but before cursor/index commit."""
    orphan = archive.PARTS / "orphan.part-000000.parquet"
    archive._atomic_parquet(archive.build_frame([market("A")]), orphan)

    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.backfill_index(index)
        assert index.count() == 1

        def get_page(_path, _params):
            return {"markets": [market("A"), market("B")], "cursor": None}

        assert archive.ingest(index, pages_per_part=1, get_page=get_page)
        assert index.count() == 2
        assert archive.finalize(index)

    rows = sum(pq.ParquetFile(p).metadata.num_rows
               for p in archive.archive_sources())
    assert rows == 2


def test_mve_husks_are_skipped_before_part_write(sandbox):
    def get_page(_path, _params):
        return {"markets": [market("DROP", mve=True, volume="0"),
                            market("KEEP")], "cursor": None}

    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.ingest(index, pages_per_part=1, get_page=get_page)
        run = index.get_meta("ingest")
        assert run["seen"] == 2
        assert run["skipped_husks"] == 1
        assert run["written"] == 1


def test_existing_base_bytes_are_never_rewritten(sandbox):
    write_base(archive.BASE, ["OLD"])
    before = archive.BASE.read_bytes()

    def get_page(_path, _params):
        return {"markets": [market("NEW")], "cursor": None}

    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.backfill_index(index)
        assert archive.ingest(index, get_page=get_page)
        assert archive.finalize(index)
    assert archive.BASE.read_bytes() == before
    with archive.ArchiveIndex(archive.INDEX) as index:
        assert index.count() == 2


def test_atomic_progress_leaves_no_temporary(sandbox):
    archive._atomic_json(archive.PROGRESS, {"status": "indexing"})
    archive._atomic_json(archive.PROGRESS, {"status": "complete"})
    assert json.loads(archive.PROGRESS.read_text()) == {"status": "complete"}
    assert not archive.PROGRESS.with_name(archive.PROGRESS.name + ".tmp").exists()


def test_duplicate_within_one_bundle_keeps_one_row(sandbox):
    def get_page(_path, _params):
        return {"markets": [market("A"), market("A"), market("B")],
                "cursor": None}

    with archive.ArchiveIndex(archive.INDEX) as index:
        assert archive.ingest(index, get_page=get_page)
        assert index.count() == 2
        assert index.get_meta("ingest")["written"] == 2
