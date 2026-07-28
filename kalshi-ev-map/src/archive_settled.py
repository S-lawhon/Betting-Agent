"""Incremental settled-market archiver (cron target).

Appends all newly settled markets since the last run to
data/settled_archive.parquet. The public API only keeps ~90 days reachable;
run at least weekly so calibration history is never lost.

STATUS 2026-07-28: memory rebuilt from the ground up. See the version
history in main(). The short version, because three fixes in a row treated a
symptom:

  **89.5% of every settled market Kalshi returns is a zero-volume MVE parlay
  husk**, and the filter that dropped them ran AFTER they had been paginated,
  parsed and built into a DataFrame. v1 said so in a comment and then v2
  (chunking) and v3 (streaming writer) both optimised the memory that the
  waste consumed instead of removing the waste. Peak RSS across the four
  attempts: 1.66 GB -> (v2, unmeasured, still >1.4 GB) -> 599 MB -> 182 MB.

They cannot be excluded server-side — `exclude_mve`, `mve=false`,
`is_mve=false`, `multivariate=false`, `min_volume=1`, `min_open_interest=1`
are all silently ignored by `/markets`. So they are skipped in the ingest loop
before `build_frame` ever sees them.

RUN IT UNDER A CAP until the above is done — on a 2 GB box the largest RSS
consumer is P-022's runner, and the global OOM killer scores by RSS:

    systemd-run --unit=evmap-archive-probe --property=MemoryMax=600M \\
        --property=MemorySwapMax=0 --uid=bettingbot \\
        --working-directory=/opt/betting-pod-shop/kalshi-ev-map \\
        /opt/betting-pod-shop/venv/bin/python src/archive_settled.py
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
from pull_settled import build_frame

import numpy as np
import pandas as pd

kc.MIN_INTERVAL = 0.25
STATE = kc.DATA_DIR / "archive_state.json"
OUT = kc.DATA_DIR / "settled_archive.parquet"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-raw", action="store_true",
                    help="skip pass A and merge an existing "
                         "settled_archive.parquet.raw. Pass A is ~7 minutes of "
                         "rate-limited pulling over 7M markets; a pass-B "
                         "failure should not cost that twice.")
    args = ap.parse_args()
    state = json.load(open(STATE)) if STATE.exists() else {}
    # overlap 2 days to be safe against settlement lag
    min_close = int(state.get("last_run_ts", time.time() - 8 * 86400)) - 2 * 86400

    # ── THE MEMORY HISTORY, so the next person does not repeat it ──────────
    #
    # v1  accumulated every settled market in one list, then built one
    #     DataFrame. 1.66 GB, killed by the GLOBAL OOM killer at 433 s.
    # v2  (2026-07-29) chunked the accumulation — but kept every chunk in
    #     `parts` and concatenated at the end, so peak was still the whole
    #     frame plus a copy. It moved WHERE the memory went, not HOW MUCH.
    # v3  (2026-07-28) streamed row groups to a ParquetWriter. A real ~3x
    #     win (1.66 GB -> 599 MB, 4x the work done) and STILL killed, at a
    #     600 MB cap, because two things still grew with KEPT ROWS: the
    #     `new_tickers` de-duplication set, and the array pandas materialises
    #     for `part.ticker.isin(set)` on every single chunk.
    #
    # v4, below, is bounded BY CONSTRUCTION rather than by a component being
    # small enough. The ingest loop holds NO cross-chunk Python state at all —
    # no set, no list of frames, nothing whose size depends on how much has
    # been read. De-duplication is deferred to a second pass that works on ONE
    # COLUMN of the file just written.
    #
    # Still exactly equivalent to the original
    # `pd.concat([old, new]).drop_duplicates(subset="ticker", keep="last")`:
    #   * within the new data, keep="last" is reproduced by
    #     `Series.duplicated(keep="last")` over the ticker column in file order;
    #   * new beats old is reproduced by writing the deduped new rows first and
    #     then streaming the old archive back in, dropping any ticker the new
    #     pass wrote.
    #
    # The rejected alternative was seeding `archive_state.json` to shrink the
    # 10-day window. It does not work: the timer is WEEKLY, so a window shorter
    # than 7 days drops settled markets permanently — and the archive has never
    # existed, so there is nothing to seed from. It trades an OOM for silent
    # data loss.
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    CHUNK = 20000
    raw = OUT.with_suffix(".parquet.raw")     # pass A output (with duplicates)
    tmp = OUT.with_suffix(".parquet.tmp")     # pass B output (final)

    # A killed run cannot clean up after itself — SIGKILL does not run
    # `finally` — so stale temporaries are cleared at STARTUP. v3 left a 34 MB
    # orphan behind exactly this way.
    keep_raw = args.from_raw and raw.exists()
    for stale in ((tmp,) if keep_raw else (raw, tmp)):
        if stale.exists():
            print(f"[archive] removing stale {stale.name} from a killed run",
                  flush=True)
            stale.unlink()

    # ── PASS A: ingest → raw parquet. No dedup, no set, no growing state. ──
    #
    # THE HUSK FILTER RUNS BEFORE `build_frame`, NOT AFTER — measured
    # 2026-07-28 and this is the single biggest cost in the whole job.
    # **89.5% of every settled market Kalshi returns is a zero-volume MVE
    # parlay husk**: over 250k markets, `KXMVESPORTSMULTIGAMEEXTENDED` is
    # 75.4% and `KXMVECROSSCATEGORY` 14.1%. v1's own comment said the filter
    # "ran only AFTER everything was in memory" and every fix since — the
    # chunking, the streaming writer — treated the MEMORY CONSEQUENCE instead
    # of the CAUSE, which is that nine rows in ten are built into a DataFrame
    # only to be thrown away.
    #
    # They cannot be excluded server-side: `exclude_mve`, `mve=false`,
    # `is_mve=false`, `multivariate=false`, `min_volume=1` and
    # `min_open_interest=1` are all silently IGNORED by `/markets` (89.5%
    # unchanged on every one). So they must be fetched — but they need not be
    # parsed, framed, or written.
    #
    # Exactly equivalent to the old post-filter: `build_frame` sets
    # `is_mve = bool(mve_collection_ticker)` and `volume = fnum(volume_fp)`,
    # and `~is_mve | (volume > 0)` drops a null-volume husk because
    # `NaN > 0` is False — which `(fnum(...) or 0) > 0` reproduces.
    #
    # CHUNK now counts KEPT rows, so a chunk is a real unit of work rather
    # than mostly husks.
    rows, seen, skipped, written = [], 0, 0, 0
    writer = None
    if keep_raw:
        written = pq.ParquetFile(raw).metadata.num_rows
        print(f"[archive] --from-raw: reusing {raw.name} with {written} rows, "
              f"skipping pass A", flush=True)
    try:
        if keep_raw:
            raise StopIteration
        def _flush():
            nonlocal writer, written
            if not rows:
                return
            part = build_frame(rows)
            rows.clear()
            if part.empty:
                return
            table = pa.Table.from_pandas(part, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(raw, table.schema)
            writer.write_table(table)
            written += table.num_rows

        for m in kc.paginate("/markets", {"status": "settled", "limit": 1000,
                                          "min_close_ts": min_close},
                             list_key="markets"):
            seen += 1
            if m.get("mve_collection_ticker") and not (
                    (kc.fnum(m.get("volume_fp")) or 0.0) > 0):
                skipped += 1                      # zero-volume parlay husk
                continue
            rows.append(m)
            if len(rows) >= CHUNK:
                _flush()
                print(f"[archive] pass A: {seen} scanned, {skipped} husks "
                      f"skipped, {written} kept...", flush=True)
        _flush()
    except StopIteration:
        pass
    finally:
        if writer is not None:
            writer.close()
            writer = None
    if keep_raw:
        print(f"[archive] pass A skipped ({written} rows reused)", flush=True)
    else:
        print(f"[archive] pass A done: {seen} scanned, {skipped} husks "
              f"skipped ({100*skipped/max(seen,1):.1f}%), {written} kept",
              flush=True)

    if written == 0 and not OUT.exists():
        # A run that legitimately found nothing must still leave a readable
        # archive, or the next run cannot tell "empty" from "never ran".
        build_frame([]).iloc[0:0].to_parquet(tmp, index=False)
        tmp.replace(OUT)
        json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
        print(f"[done] no settled markets in window -> empty {OUT}", flush=True)
        return

    # ── PASS B: merge. Bounded — no column is ever materialised in pandas. ──
    #
    # v5 finished pass A fine (7,055,386 markets scanned, 3,680,825 kept, 402 MB)
    # and was then OOM-KILLED HERE, because this pass called `.to_pandas()` on
    # the whole ticker column: 3.68 M Python strings is ~400 MB in one spike.
    #
    # And it was computing a NO-OP. Measured on that very file with a bounded
    # hash pass: 3,680,825 rows, **3,680,825 distinct tickers, zero duplicates**.
    # Kalshi's cursor pagination does not repeat a market, so the within-run
    # `drop_duplicates` never removed anything. It is gone — but the assumption
    # is now CHECKED rather than assumed, at ~30 MB (uint64 hashes) instead of
    # ~400 MB (strings), so if Kalshi ever starts repeating we find out.
    def _ticker_hashes(path):
        """uint64 hash per row, in file order. ~8 B/row instead of ~110 B."""
        pf_ = pq.ParquetFile(path)
        out = np.empty(pf_.metadata.num_rows, dtype=np.uint64)
        i = 0
        for b in pf_.iter_batches(batch_size=200000, columns=["ticker"]):
            for t in b.column("ticker").to_pylist():
                out[i] = np.frombuffer(
                    hashlib.blake2b((t or "").encode(), digest_size=8).digest(),
                    dtype=np.uint64)[0]
                i += 1
        return out[:i]

    if written:
        h = _ticker_hashes(raw)
        n_distinct = len(np.unique(h))
        if n_distinct != len(h):
            print(f"[archive] WARNING: {len(h) - n_distinct} DUPLICATE tickers "
                  f"in this pull — pagination has started repeating. The "
                  f"within-run de-duplication removed in v6 is now needed "
                  f"again; the archive keeps every copy until it is restored.",
                  flush=True)
        else:
            print(f"[archive] pass B: {len(h)} rows, all tickers distinct",
                  flush=True)

    if not OUT.exists():
        # First run, or the archive was lost. Nothing to merge — the raw file
        # IS the archive. A rename costs no memory and no time, where the old
        # code streamed 3.68 M rows through a second writer to achieve exactly
        # the same bytes.
        raw.replace(OUT)
        print("[archive] no existing archive to merge — raw promoted in place",
              flush=True)
    else:
        # Merge: every new row wins, then carry forward the old rows whose
        # ticker this pull did not rewrite. Compared by uint64 hash, so the
        # exclusion set is ~30 MB rather than ~125 MB of arrow strings or
        # ~400 MB of Python strings. Collision risk over 3.7 M keys in a 64-bit
        # space is ~1e-6 and its only consequence is dropping one superseded
        # OLD row that a NEW row already replaces — it cannot lose new data.
        new_h = np.sort(h) if written else np.empty(0, dtype=np.uint64)
        out_writer = None
        carried = 0
        try:
            for src in (raw, OUT):
                if src is raw and not written:
                    continue
                for batch in pq.ParquetFile(src).iter_batches(batch_size=CHUNK):
                    tbl = pa.Table.from_batches([batch])
                    if src is OUT and len(new_h):
                        bh = np.array(
                            [np.frombuffer(hashlib.blake2b(
                                (t or "").encode(), digest_size=8).digest(),
                                dtype=np.uint64)[0]
                             for t in tbl.column("ticker").to_pylist()],
                            dtype=np.uint64)
                        idx = np.searchsorted(new_h, bh)
                        idx[idx >= len(new_h)] = 0
                        keep = new_h[idx] != bh
                        tbl = tbl.filter(pa.array(keep))
                        carried += tbl.num_rows
                    if tbl.num_rows == 0:
                        continue
                    if out_writer is None:
                        out_writer = pq.ParquetWriter(tmp, tbl.schema)
                    out_writer.write_table(tbl)
            if out_writer is None:
                build_frame([]).iloc[0:0].to_parquet(tmp, index=False)
            else:
                out_writer.close()
                out_writer = None
            tmp.replace(OUT)
            print(f"[archive] carried {carried} existing rows forward",
                  flush=True)
        finally:
            if out_writer is not None:
                out_writer.close()

    for leftover in (raw, tmp):
        if leftover.exists():
            leftover.unlink()

    json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
    total = pq.ParquetFile(OUT).metadata.num_rows
    print(f"[done] archive now {total} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
