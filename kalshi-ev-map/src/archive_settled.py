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
    rows = []
    for m in kc.paginate("/markets", {"status": "settled", "limit": 1000,
                                      "min_close_ts": min_close},
                         list_key="markets"):
        rows.append(m)
        if len(rows) % 100000 == 0:
            print(f"[archive] {len(rows)}...", flush=True)
    df = build_frame(rows)
    # drop zero-volume MVE parlay husks to keep the archive lean
    df = df[~df.is_mve | (df.volume > 0)]
    if OUT.exists():
        old = pd.read_parquet(OUT)
        df = pd.concat([old, df]).drop_duplicates(subset="ticker", keep="last")
    df.to_parquet(OUT, index=False)
    json.dump({"last_run_ts": int(time.time())}, open(STATE, "w"))
    print(f"[done] archive now {len(df)} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
