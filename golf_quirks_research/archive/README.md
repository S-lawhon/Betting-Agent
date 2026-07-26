# `golf_quirks_research/archive/` — committed data caches

These are the P-022 / P-023 Kalshi pulls, compressed. They live here rather than in
`data/` because `.gitignore` excludes `data/`, `*.jsonl` and `*.log` repo-wide, which is
exactly how the 2026-07-25 loss happened: the working caches were invisible to
`git status` and nobody noticed they were untracked.

208 MB of loose files compresses to 10 MB, so there is no reason not to keep them.

| Archive | Expands to | Re-pullable? |
|---|---|---|
| `leader_trades.tar.gz` | `data/leader_trades/` — 364 round-leader markets, executed trade prints | **NO.** Kalshi's `/markets/{t}/trades` history reaches back only ~1 month and rolls off. This window (late-June → late-July 2026) is gone from the API. It is the entire Phase-2 tick-replay sample for P-022. |
| `candles.tar.gz` | `data/candles/` — 11,349 settled golf markets, 1m candlesticks | Yes, but ~95 min of the shared 2 req/s Kalshi budget. |
| `settled_meta.jsonl.gz` | `data/settled_meta.jsonl` — settled-market metadata incl. `settlement_value_dollars` | Yes, cheaply. |
| `pull_logs.tar.gz` | `data/trades_pull.log`, `data/candle_pull.log` | n/a — provenance record of how the pulls were made. |

## Restore

```bash
bash golf_quirks_research/archive/restore.sh
```

Idempotent; refuses to clobber an existing non-empty `data/` unless you pass `--force`.

## Provenance

Pulled 2026-07-23 by the P-022 Phase-2 harness. The harness `.py` files were lost on
2026-07-25 (never committed) and are being rebuilt — see
`research/prompts/PROMPT_P023_MakeCut_Phase2.md`. These caches are what makes that
rebuild possible at all.
