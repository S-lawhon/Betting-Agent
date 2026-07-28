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
    CHUNK = 20000
    parts, rows, seen = [], [], 0

    def _flush():
        if not rows:
            return
        part = build_frame(rows)
        # drop zero-volume MVE parlay husks to keep the archive lean
        parts.append(part[~part.is_mve | (part.volume > 0)])
        rows.clear()

    for m in kc.paginate("/markets", {"status": "settled", "limit": 1000,
                                      "min_close_ts": min_close},
                         list_key="markets"):
        rows.append(m)
        seen += 1
        if len(rows) >= CHUNK:
            _flush()
            print(f"[archive] {seen} scanned, "
                  f"{sum(len(p) for p in parts)} kept...", flush=True)
    _flush()
    df = (pd.concat(parts, ignore_index=True) if parts
          else build_frame([]).iloc[0:0])
    if OUT.exists():
        old = pd.read_parquet(OUT)
        df = pd.concat([old, df]).drop_duplicates(subset="ticker", keep="last")
    df.to_parquet(OUT, index=False)
    json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
    print(f"[done] archive now {len(df)} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
