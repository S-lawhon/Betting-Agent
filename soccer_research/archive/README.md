# `soccer_research/archive/` — committed data caches

These are the first-half BTTS feasibility snapshots, gzipped. They live here rather
than in `data/` because `.gitignore` excludes `data/`, `*.jsonl` and `*.log`
repo-wide — the same trap that lost the `golf_quirks_research` harness on
2026-07-25, when the working caches were invisible to `git status` and nobody
noticed they were untracked.

| Archive | Expands to | Re-pullable? |
|---|---|---|
| `btts_1h_snapshot_1_20260816.json.gz` | `data/btts_1h_snapshot_1.json` — 33 first-half + 245 full-match BTTS markets with live top-of-book, plus live `fee_type` for all 52 touched series | **No.** These are live order books at a moment in time. Kalshi publishes no historical orderbook endpoint, and the AFT review's own soccer liquidity numbers could not be reconstructed for the same reason. |

Restore with:

```bash
mkdir -p soccer_research/data
gunzip -c soccer_research/archive/btts_1h_snapshot_1_20260816.json.gz \
  > soccer_research/data/btts_1h_snapshot_1.json
```

**These snapshots are the evidence behind the PROCEED verdict** recorded in
`manager/registry.yaml` under `R-SOCCER-BTTS-1H`. The verdict is reproducible from
them via `python3 soccer_research/btts_1h_census.py --report` without touching the
network.
