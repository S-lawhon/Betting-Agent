"""Resumable settled-market archiver.

The original archiver rewrote one lifetime Parquet file every Sunday. By
2026-08-16 the valid base had reached 8.68 million rows and pass A alone ran
past the 45-minute systemd limit, leaving an unreadable 401 MB ``.raw`` file.

This version treats the existing file as an immutable base and appends atomic
Parquet parts under ``data/settled_archive_parts``. A disk-backed SQLite index
provides exact ticker de-duplication without loading the lifetime archive into
RAM. Both indexing the legacy base and API pagination are checkpointed, so a
timeout or process kill repeats at most one row group/page bundle and can never
damage committed archive data.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
from pull_settled import build_frame

kc.MIN_INTERVAL = 0.25

STATE = kc.DATA_DIR / "archive_state.json"
BASE = kc.DATA_DIR / "settled_archive.parquet"
PARTS = kc.DATA_DIR / "settled_archive_parts"
INDEX = kc.DATA_DIR / "settled_archive_index.sqlite"
PROGRESS = kc.DATA_DIR / "archive_progress.json"
LEGACY_RAW = BASE.with_suffix(".parquet.raw")

SCHEMA_VERSION = 2
DEFAULT_BUDGET_SECONDS = 35 * 60
DEFAULT_PAGES_PER_PART = 20
SQL_VARS = 800


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    """Durably replace a JSON checkpoint; a killed write leaves the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _atomic_parquet(frame, path: Path) -> None:
    """Publish a readable Parquet part only after its footer is durable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        frame.to_parquet(tmp, index=False)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _chunks(values: Sequence[str], size: int = SQL_VARS) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


class ArchiveIndex:
    """Disk-backed exact ticker index plus transactional resume metadata."""

    def __init__(self, path: Path = INDEX):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=60)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tickers (
                ticker TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS sources (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                row_groups_done INTEGER NOT NULL DEFAULT 0,
                rows_indexed INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM tickers").fetchone()[0])

    def get_meta(self, key: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key: str, value: Dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, sort_keys=True)),
            )

    def delete_meta(self, key: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM metadata WHERE key=?", (key,))

    def unseen(self, tickers: Sequence[str]) -> List[bool]:
        """Return a position-preserving mask without materialising the archive."""
        present = set()
        unique = list(dict.fromkeys(str(t) for t in tickers if t))
        for group in _chunks(unique):
            marks = ",".join("?" for _ in group)
            present.update(row[0] for row in self.db.execute(
                f"SELECT ticker FROM tickers WHERE ticker IN ({marks})", tuple(group)))
        batch_seen = set()
        mask = []
        for ticker in tickers:
            keep = bool(ticker) and ticker not in present and ticker not in batch_seen
            mask.append(keep)
            if ticker:
                batch_seen.add(ticker)
        return mask

    def source(self, path: Path) -> Optional[Dict[str, int]]:
        row = self.db.execute(
            "SELECT size_bytes,mtime_ns,row_groups_done,rows_indexed,complete "
            "FROM sources WHERE path=?", (str(path),)).fetchone()
        if not row:
            return None
        return dict(zip(("size_bytes", "mtime_ns", "row_groups_done",
                         "rows_indexed", "complete"), map(int, row)))

    def _ensure_source(self, path: Path) -> Dict[str, int]:
        stat = path.stat()
        rec = self.source(path)
        identity = (stat.st_size, stat.st_mtime_ns)
        if rec and (rec["size_bytes"], rec["mtime_ns"]) != identity:
            raise RuntimeError(f"committed archive source changed: {path}")
        if not rec:
            with self.db:
                self.db.execute(
                    "INSERT INTO sources(path,size_bytes,mtime_ns) VALUES(?,?,?)",
                    (str(path), *identity),
                )
            rec = self.source(path)
        assert rec is not None
        return rec

    def index_next_row_group(self, path: Path) -> Tuple[bool, int]:
        """Index one Parquet row group transactionally; return (complete, rows)."""
        import pyarrow.parquet as pq

        rec = self._ensure_source(path)
        pf = pq.ParquetFile(path, pre_buffer=False, memory_map=False)
        group = rec["row_groups_done"]
        if group >= pf.metadata.num_row_groups:
            if not rec["complete"]:
                with self.db:
                    self.db.execute("UPDATE sources SET complete=1 WHERE path=?",
                                    (str(path),))
            return True, 0

        table = pf.read_row_group(group, columns=["ticker"], use_threads=False)
        tickers = [str(t) for t in table.column("ticker").to_pylist() if t]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO tickers(ticker) VALUES(?)",
                                ((t,) for t in tickers))
            self.db.execute(
                "UPDATE sources SET row_groups_done=row_groups_done+1, "
                "rows_indexed=rows_indexed+? WHERE path=?",
                (len(tickers), str(path)),
            )
        complete = group + 1 >= pf.metadata.num_row_groups
        if complete:
            with self.db:
                self.db.execute("UPDATE sources SET complete=1 WHERE path=?",
                                (str(path),))
        return complete, len(tickers)

    def commit_part(self, path: Path, tickers: Sequence[str],
                    ingest: Dict[str, Any]) -> None:
        """Atomically index a published part and advance its API cursor."""
        stat = path.stat()
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path, pre_buffer=False, memory_map=False)
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO tickers(ticker) VALUES(?)",
                                ((str(t),) for t in tickers if t))
            self.db.execute(
                "INSERT OR REPLACE INTO sources"
                "(path,size_bytes,mtime_ns,row_groups_done,rows_indexed,complete) "
                "VALUES(?,?,?,?,?,1)",
                (str(path), stat.st_size, stat.st_mtime_ns,
                 pf.metadata.num_row_groups, pf.metadata.num_rows),
            )
            self.db.execute(
                "INSERT INTO metadata(key,value) VALUES('ingest',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(ingest, sort_keys=True),),
            )


def archive_sources(base: Optional[Path] = None,
                    parts: Optional[Path] = None) -> List[Path]:
    """Return committed archive sources; temporary files are never data."""
    base = BASE if base is None else base
    parts = PARTS if parts is None else parts
    out = [base] if base.exists() else []
    if parts.exists():
        out.extend(sorted(parts.glob("*.parquet")))
    return out


def _write_progress(index: ArchiveIndex, status: str,
                    **extra: Any) -> Dict[str, Any]:
    sources = archive_sources()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "status": status,
        "base_archive_exists": BASE.exists(),
        "committed_parts": max(0, len(sources) - int(BASE.exists())),
        "indexed_tickers": index.count(),
        "legacy_raw_present": LEGACY_RAW.exists(),
        **extra,
    }
    _atomic_json(PROGRESS, payload)
    return payload


def backfill_index(index: ArchiveIndex, deadline: Optional[float] = None) -> bool:
    """Reconcile committed sources and checkpoint new indexing by row group.

    Completed sources take a metadata-only fast path: their stored size/mtime
    identity is verified, but the Parquet footer is not reopened and the
    fsync-backed progress file is not rewritten. New or incomplete sources
    still use the row-group path, which is what recovers an orphan published
    just before a process kill.
    """
    sources = archive_sources()
    already_complete = 0
    indexed_sources = 0
    indexed_rows = 0
    for source in sources:
        rec = index._ensure_source(source)
        if rec["complete"]:
            already_complete += 1
            continue

        indexed_sources += 1
        while True:
            complete, rows = index.index_next_row_group(source)
            indexed_rows += rows
            rec = index.source(source) or {}
            _write_progress(
                index, "indexing" if not complete else "indexed_source",
                source=str(source), rows_indexed=rec.get("rows_indexed"),
                row_groups_done=rec.get("row_groups_done"),
            )
            if complete:
                break
            if deadline is not None and time.monotonic() >= deadline:
                print(f"[archive] checkpointed index at {source.name} "
                      f"row_group={rec.get('row_groups_done')}", flush=True)
                return False
    print(f"[archive] reconciled sources={len(sources)} "
          f"already_complete={already_complete} "
          f"indexed_sources={indexed_sources} indexed_rows={indexed_rows}",
          flush=True)
    return True


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unreadable archive state: {STATE}: {exc}") from exc


def _new_ingest() -> Dict[str, Any]:
    now = int(time.time())
    state = _load_state()
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                  + "-" + uuid.uuid4().hex[:8],
        "started_at": _utc_now(),
        "min_close_ts": int(state.get("last_run_ts", now - 8 * 86400)) - 2 * 86400,
        "through_ts": now,
        "cursor": None,
        "next_part": 0,
        "pages": 0,
        "seen": 0,
        "skipped_husks": 0,
        "written": 0,
        "complete": False,
    }


def _collapse_last(frame):
    """Keep the last row for a ticker inside one fetched page bundle."""
    if frame.empty:
        return frame
    return frame.drop_duplicates(subset="ticker", keep="last").reset_index(drop=True)


def _empty_frame():
    """Return build_frame's stable schema without special-casing downstream."""
    return build_frame([{}]).iloc[0:0]


def ingest(index: ArchiveIndex, deadline: Optional[float] = None,
           pages_per_part: int = DEFAULT_PAGES_PER_PART,
           get_page: Callable[..., Dict[str, Any]] = kc.get,
           max_parts: Optional[int] = None) -> bool:
    """Resume API pagination and publish immutable parts; return completeness."""
    run = index.get_meta("ingest") or _new_ingest()
    if run.get("complete"):
        return True

    committed_this_call = 0
    while True:
        bundle: List[Dict[str, Any]] = []
        next_cursor = run.get("cursor")
        terminal = False
        pages = 0
        seen = skipped = 0
        while pages < pages_per_part:
            params = {
                "status": "settled",
                "limit": 1000,
                "min_close_ts": run["min_close_ts"],
                "max_close_ts": run["through_ts"],
            }
            if next_cursor:
                params["cursor"] = next_cursor
            data = get_page("/markets", params)
            items = data.get("markets") or []
            seen += len(items)
            for market in items:
                if market.get("mve_collection_ticker") and not (
                        (kc.fnum(market.get("volume_fp")) or 0.0) > 0):
                    skipped += 1
                else:
                    bundle.append(market)
            pages += 1
            next_cursor = data.get("cursor")
            if not next_cursor or not items:
                terminal = True
                break

        frame = _collapse_last(build_frame(bundle)) if bundle else _empty_frame()
        tickers = [str(t) for t in frame.get("ticker", [])]
        if tickers:
            mask = index.unseen(tickers)
            frame = frame.loc[mask].reset_index(drop=True)
            tickers = [str(t) for t in frame["ticker"].tolist()]

        next_run = dict(run)
        next_run.update({
            "cursor": next_cursor,
            "next_part": int(run["next_part"]) + int(not frame.empty),
            "pages": int(run["pages"]) + pages,
            "seen": int(run["seen"]) + seen,
            "skipped_husks": int(run["skipped_husks"]) + skipped,
            "written": int(run["written"]) + len(frame),
            "complete": terminal,
        })

        if not frame.empty:
            # UUID suffix prevents an orphan (published just before a kill,
            # before the cursor transaction) from occupying the sequence name
            # that the resumed run would otherwise try to reuse.
            name = (f"{run['run_id']}.part-{int(run['next_part']):06d}-"
                    f"{uuid.uuid4().hex[:8]}.parquet")
            part = PARTS / name
            if part.exists():
                raise RuntimeError(f"refusing to overwrite committed part: {part}")
            _atomic_parquet(frame, part)
            # Publish first, then transact index + cursor. A kill between them
            # leaves an orphan that backfill_index repairs before any refetch.
            index.commit_part(part, tickers, next_run)
            committed_this_call += 1
        else:
            index.set_meta("ingest", next_run)

        run = next_run
        _write_progress(index, "ingest_complete" if terminal else "ingesting",
                        ingest=run)
        print(f"[archive] pages={run['pages']} seen={run['seen']} "
              f"skipped={run['skipped_husks']} written={run['written']} "
              f"complete={terminal}", flush=True)

        if terminal:
            return True
        if max_parts is not None and committed_this_call >= max_parts:
            return False
        if deadline is not None and time.monotonic() >= deadline:
            print("[archive] runtime budget reached; cursor and parts committed",
                  flush=True)
            return False


def finalize(index: ArchiveIndex) -> bool:
    """Advance the high-water mark only after a complete, indexed ingest."""
    run = index.get_meta("ingest")
    if not run or not run.get("complete"):
        return False
    state = {
        "schema_version": SCHEMA_VERSION,
        "last_run_ts": int(run["through_ts"]),
        "last_run_id": run["run_id"],
        "updated_at": _utc_now(),
        "archive_layout": "immutable_base_plus_parts",
    }
    _atomic_json(STATE, state)
    index.delete_meta("ingest")
    _write_progress(index, "complete", last_run=state, ingest=run)
    print(f"[done] archive index has {index.count()} unique tickers; "
          f"committed {run['written']} rows in run {run['run_id']}", flush=True)
    return True


def run_cycle(*, budget_seconds: int = DEFAULT_BUDGET_SECONDS,
              pages_per_part: int = DEFAULT_PAGES_PER_PART,
              max_parts: Optional[int] = None,
              index_path: Path = INDEX) -> int:
    deadline = time.monotonic() + budget_seconds if budget_seconds > 0 else None
    with ArchiveIndex(index_path) as index:
        if LEGACY_RAW.exists():
            print(f"[archive] ignoring legacy partial file {LEGACY_RAW}; "
                  "only committed base/parts are archive data", flush=True)
        if not backfill_index(index, deadline):
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            return 0
        if not ingest(index, deadline, pages_per_part, max_parts=max_parts):
            return 0
        finalize(index)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget-seconds", type=int, default=DEFAULT_BUDGET_SECONDS,
                    help="checkpoint and exit cleanly before systemd's hard timeout")
    ap.add_argument("--pages-per-part", type=int, default=DEFAULT_PAGES_PER_PART)
    ap.add_argument("--max-parts", type=int,
                    help="test/recovery bound; normally omitted")
    args = ap.parse_args(argv)
    return run_cycle(budget_seconds=args.budget_seconds,
                     pages_per_part=args.pages_per_part,
                     max_parts=args.max_parts)


if __name__ == "__main__":
    raise SystemExit(main())
