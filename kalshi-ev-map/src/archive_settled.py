"""Incremental settled-market archiver (cron target).

Appends all newly settled markets since the last run to
data/settled_archive.parquet. The public API only keeps ~90 days reachable;
run at least weekly so calibration history is never lost.
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
    # MEMORY, not methodology (2026-07-29).  This accumulated every settled
    # market of the window in one list and then built one DataFrame.  On the
    # 16GB Mac that was merely wasteful; on the 2GB droplet it reached 1.66GB
    # RSS and was OOM-killed at 433s (exit -9) on its first run.  Kalshi
    # settles a very large number of MVE parlay husks, and the filter that
    # drops them ran only AFTER everything was in memory.
    #
    # Chunking is exactly equivalent, not an approximation: `build_frame` is
    # row-wise and the MVE filter is row-wise, so filtering per chunk and
    # concatenating gives the identical frame.  No field, threshold or
    # semantic changes.
    #
    # 2026-07-28: chunking alone was NOT enough and the 07-29 note above says
    # why without drawing the conclusion — `parts` accumulated every chunk, so
    # peak memory was still the whole filtered frame plus a copy for the final
    # `pd.concat`.  Measured: still climbing past 1.4GB at 35 minutes on a box
    # with 993MB available.  The chunk loop moved WHERE the memory was spent,
    # not HOW MUCH.
    #
    # Now each chunk is STREAMED to a parquet writer as a row group and
    # released.  Peak memory is one chunk plus the ticker set, both bounded and
    # neither growing with the length of the window.  Still exactly equivalent:
    # `pd.concat([old, new]).drop_duplicates(subset="ticker", keep="last")`
    # means new rows win over old ones, which is reproduced by writing every
    # new row first and then streaming the old file back in while skipping any
    # ticker the new pass already wrote.
    #
    # The rejected alternative was seeding `archive_state.json` to shrink the
    # 10-day window.  It does not work: the timer is WEEKLY, so a window
    # shorter than 7 days drops settled markets permanently — and the archive
    # has never existed, so there is nothing to seed from in the first place.
    # It would have traded an OOM for silent data loss.
    import pyarrow as pa
    import pyarrow.parquet as pq

    CHUNK = 20000
    rows, seen, kept = [], 0, 0
    tmp = OUT.with_suffix(".parquet.tmp")
    writer = None
    new_tickers: set = set()

    def _write(part):
        """Append one filtered frame as a row group; never hold it after."""
        nonlocal writer, kept
        if part.empty:
            return
        table = pa.Table.from_pandas(part, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(tmp, table.schema)
        writer.write_table(table)
        kept += len(part)

    def _flush():
        if not rows:
            return
        part = build_frame(rows)
        # drop zero-volume MVE parlay husks to keep the archive lean
        part = part[~part.is_mve | (part.volume > 0)]
        # de-duplicate within this run (pagination can repeat a market across
        # page boundaries); the old-file merge below keys off the same set.
        part = part[~part.ticker.isin(new_tickers)]
        new_tickers.update(part.ticker.tolist())
        _write(part)
        rows.clear()

    try:
        for m in kc.paginate("/markets", {"status": "settled", "limit": 1000,
                                          "min_close_ts": min_close},
                             list_key="markets"):
            rows.append(m)
            seen += 1
            if len(rows) >= CHUNK:
                _flush()
                print(f"[archive] {seen} scanned, {kept} kept...", flush=True)
        _flush()

        # Carry the existing archive forward, one row group at a time, dropping
        # any ticker this run just rewrote. `iter_batches` never materialises
        # the whole old file — which is the other half of the memory problem,
        # and the half that would have bitten later as the archive grew.
        if OUT.exists():
            old_pf = pq.ParquetFile(OUT)
            carried = 0
            for batch in old_pf.iter_batches(batch_size=CHUNK):
                part = batch.to_pandas()
                part = part[~part.ticker.isin(new_tickers)]
                if part.empty:
                    continue
                _write(part)
                carried += len(part)
            print(f"[archive] carried {carried} existing rows forward",
                  flush=True)

        if writer is None:
            # Nothing new and nothing carried. Write the empty frame with the
            # real schema rather than skipping: a run that legitimately found
            # nothing must still leave a readable archive, or the next run
            # cannot tell "empty" from "never ran".
            build_frame([]).iloc[0:0].to_parquet(tmp, index=False)
        else:
            writer.close()
            writer = None
        tmp.replace(OUT)
    finally:
        if writer is not None:                 # a crash must not leave a
            writer.close()                     # half-written temp claiming to
        if tmp.exists() and OUT.exists() and tmp != OUT:
            tmp.unlink(missing_ok=True)        # be the archive

    json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
    print(f"[done] archive now {kept} new + carried rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
