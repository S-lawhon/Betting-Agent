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
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
from pull_settled import build_frame

import pandas as pd

kc.MIN_INTERVAL = 0.25
STATE = kc.DATA_DIR / "archive_state.json"
OUT = kc.DATA_DIR / "settled_archive.parquet"


def main():
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
    for stale in (raw, tmp):
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
    try:
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
    finally:
        if writer is not None:
            writer.close()
            writer = None
    print(f"[archive] pass A done: {seen} scanned, {skipped} husks skipped "
          f"({100*skipped/max(seen,1):.1f}%), {written} kept", flush=True)

    if written == 0 and not OUT.exists():
        # A run that legitimately found nothing must still leave a readable
        # archive, or the next run cannot tell "empty" from "never ran".
        build_frame([]).iloc[0:0].to_parquet(tmp, index=False)
        tmp.replace(OUT)
        json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
        print(f"[done] no settled markets in window -> empty {OUT}", flush=True)
        return

    # ── PASS B: dedup + merge, one batch at a time. ────────────────────────
    out_writer = None
    try:
        new_tickers = None
        keep_mask = None
        if written:
            # ONE column of the file just written — not the whole file. For
            # ~300k rows this is a few MB of contiguous string data, against
            # the tens of MB a Python set of the same strings cost, and it is
            # read once instead of touched on every chunk.
            col = pq.ParquetFile(raw).read(columns=["ticker"]).column("ticker")
            ser = col.to_pandas()                      # materialised ONCE
            keep_mask = ~ser.duplicated(keep="last")
            new_tickers = pa.array(ser[keep_mask.values].values,
                                   type=pa.string())
            del ser, col
            print(f"[archive] pass B: {written} rows -> "
                  f"{int(keep_mask.sum())} unique tickers", flush=True)

        pf = pq.ParquetFile(raw) if written else None
        off = 0
        if pf is not None:
            for batch in pf.iter_batches(batch_size=CHUNK):
                n = batch.num_rows
                mask = pa.array(keep_mask.values[off:off + n])
                off += n
                filtered = pa.Table.from_batches([batch]).filter(mask)
                if filtered.num_rows == 0:
                    continue
                if out_writer is None:
                    out_writer = pq.ParquetWriter(tmp, filtered.schema)
                out_writer.write_table(filtered)

        carried = 0
        if OUT.exists():
            for batch in pq.ParquetFile(OUT).iter_batches(batch_size=CHUNK):
                tbl = pa.Table.from_batches([batch])
                if new_tickers is not None:
                    tbl = tbl.filter(
                        pc.invert(pc.is_in(tbl.column("ticker"),
                                           value_set=new_tickers)))
                if tbl.num_rows == 0:
                    continue
                if out_writer is None:
                    out_writer = pq.ParquetWriter(tmp, tbl.schema)
                out_writer.write_table(tbl)
                carried += tbl.num_rows
            print(f"[archive] carried {carried} existing rows forward",
                  flush=True)

        if out_writer is None:
            build_frame([]).iloc[0:0].to_parquet(tmp, index=False)
        else:
            out_writer.close()
            out_writer = None
        tmp.replace(OUT)
    finally:
        if out_writer is not None:
            out_writer.close()
        if raw.exists():
            raw.unlink()
        if tmp.exists():
            tmp.unlink()

    json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
    total = pq.ParquetFile(OUT).metadata.num_rows
    print(f"[done] archive now {total} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
