# `soccer_research/archive/` — committed data caches

These are the first-half BTTS caches, gzipped. They live here rather than in
`data/` because `.gitignore` excludes `data/`, `*.jsonl` and `*.log` repo-wide —
the same trap that lost the `golf_quirks_research` harness on 2026-07-25, when
the working caches were invisible to `git status` and nobody noticed they were
untracked.

| Archive | Expands to | Re-pullable? |
|---|---|---|
| `btts_1h_snapshot_1_20260816.json.gz` | `data/btts_1h_snapshot_1.json` — 33 first-half + 245 full-match BTTS markets with live top-of-book, plus live `fee_type` for all 52 touched series | **No.** These are live order books at a moment in time. Kalshi publishes no historical orderbook endpoint. |
| `btts_decay_cache_20260816.tar.gz` | `data/decay_cache/` — 908 settled first-half BTTS markets, 1-minute candlesticks (bid/ask/OHLC/volume/OI) for 4h before each close | **No.** Kalshi settled history rolls off at ~30 days, so this sample can be extended forward but never backwards. |
| `btts_decay_results_20260816.json.gz` | `data/decay_results.json` — per-market replay output behind the Phase 2 PASS | Regenerable from the cache above via `btts_decay.py --replay`, offline. |

Restore with:

```bash
mkdir -p soccer_research/data
gunzip -c soccer_research/archive/btts_1h_snapshot_1_20260816.json.gz \
  > soccer_research/data/btts_1h_snapshot_1.json
tar -xzf soccer_research/archive/btts_decay_cache_20260816.tar.gz \
  -C soccer_research/data
```

**These caches are the evidence behind both recorded verdicts** — the Phase 0
PROCEED and Phase 1 KILL (`REPORT_BTTS_Containment_2026-08-16.md`) and the
Phase 2 PASS (`REPORT_BTTS_Decay_2026-08-16.md`), both registered in
`manager/registry.yaml` under `R-SOCCER-BTTS-1H`. Every verdict is reproducible
from them **without touching the network**:

```bash
python3 soccer_research/btts_1h_census.py --report
python3 soccer_research/btts_containment.py
python3 soccer_research/btts_decay.py --replay
```
