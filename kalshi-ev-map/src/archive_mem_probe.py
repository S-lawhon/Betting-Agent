"""Where does archive_settled.py's memory actually go?

Written 2026-07-28 after the streaming-ParquetWriter fix reduced peak RSS
1.66 GB -> 599 MB and was STILL OOM-killed at a 600 MB cap. The previous two
attempts each targeted a component that was inferred rather than measured, and
one of them (the 07-29 chunking change) could not have worked. So: measure
first, then fix what the measurement shows.

Replicates the archiver's loop exactly and reports, every N chunks:
  * process RSS
  * the deep size of the `new_tickers` de-duplication set
  * bytes written to the parquet file so far
  * tracemalloc's top allocation sites

Bounded by --max-pages so it finishes in minutes. Read-only: writes only to a
scratch parquet it deletes.

    python3 src/archive_mem_probe.py --max-pages 400
"""
import argparse
import gc
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
from pull_settled import build_frame

import pyarrow as pa
import pyarrow.parquet as pq

kc.MIN_INTERVAL = 0.25


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return -1.0


def deep_set_size(s) -> float:
    """Bytes held by a set of strings, including the strings themselves."""
    return (sys.getsizeof(s) + sum(sys.getsizeof(x) for x in s)) / 1048576.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--chunk", type=int, default=20000)
    ap.add_argument("--report-every", type=int, default=5)
    a = ap.parse_args()

    tracemalloc.start(10)
    tmp = kc.DATA_DIR / "_memprobe.parquet"
    writer = None
    rows, seen, kept, nchunk = [], 0, 0, 0
    new_tickers = set()
    t0 = time.time()
    base = rss_mb()
    print(f"baseline RSS {base:.1f} MB", flush=True)
    print(f"{'chunk':>5} {'scanned':>9} {'kept':>8} {'RSS_MB':>8} "
          f"{'d_RSS':>7} {'set_MB':>7} {'file_MB':>8} {'rowgrp':>7}", flush=True)

    def flush():
        nonlocal writer, kept, nchunk
        if not rows:
            return
        part = build_frame(rows)
        part = part[~part.is_mve | (part.volume > 0)]
        part = part[~part.ticker.isin(new_tickers)]
        new_tickers.update(part.ticker.tolist())
        if not part.empty:
            table = pa.Table.from_pandas(part, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(tmp, table.schema)
            writer.write_table(table)
            kept += len(part)
        rows.clear()
        nchunk += 1

    prev = base
    try:
        for m in kc.paginate("/markets",
                             {"status": "settled", "limit": 1000,
                              "min_close_ts": int(time.time()) - 10 * 86400},
                             list_key="markets", max_pages=a.max_pages):
            rows.append(m)
            seen += 1
            if len(rows) >= a.chunk:
                flush()
                if nchunk % a.report_every == 0:
                    r = rss_mb()
                    fsz = (tmp.stat().st_size / 1048576.0) if tmp.exists() else 0
                    print(f"{nchunk:5d} {seen:9d} {kept:8d} {r:8.1f} "
                          f"{r-prev:+7.1f} {deep_set_size(new_tickers):7.1f} "
                          f"{fsz:8.1f} {nchunk:7d}", flush=True)
                    prev = r
        flush()
        if writer is not None:
            writer.close()
            writer = None

        r = rss_mb()
        print(f"\nFINAL  RSS {r:.1f} MB   (grew {r-base:+.1f} from baseline)")
        print(f"       set {deep_set_size(new_tickers):.1f} MB "
              f"({len(new_tickers)} tickers)")
        print(f"       parquet on disk "
              f"{tmp.stat().st_size/1048576.0:.1f} MB, {nchunk} row groups")
        print(f"       elapsed {time.time()-t0:.0f}s\n")

        snap = tracemalloc.take_snapshot()
        print("tracemalloc TOP 8 by size:")
        for st in snap.statistics("lineno")[:8]:
            print(f"  {st.size/1048576.0:8.2f} MB  {st.count:8d} blocks  "
                  f"{st.traceback[0]}")

        # What does dropping the set actually save?
        before = rss_mb()
        n = len(new_tickers)
        new_tickers.clear()
        gc.collect()
        after = rss_mb()
        print(f"\nclearing the {n}-ticker set + gc: "
              f"{before:.1f} -> {after:.1f} MB  ({after-before:+.1f})")
    finally:
        if writer is not None:
            writer.close()
        if tmp.exists():
            tmp.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
