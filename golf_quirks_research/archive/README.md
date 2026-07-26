# `golf_quirks_research/archive/` — committed data caches

These are the P-022 / P-023 Kalshi pulls, compressed. They live here rather than in
`data/` because `.gitignore` excludes `data/`, `*.jsonl` and `*.log` repo-wide, which is
exactly how the 2026-07-25 loss happened: the working caches were invisible to
`git status` and nobody noticed they were untracked.

208 MB of loose files compresses to 10 MB, so there is no reason not to keep them.

| Archive | Expands to | Re-pullable? |
|---|---|---|
| `leader_trades.tar.gz` | `data/leader_trades/` — 364 round-leader markets, executed trade prints | Re-pullable as of 2026-07-26, but do not count on it — see the note below. It is the entire Phase-2 tick-replay sample for P-022. |
| `makecut_trades.tar.gz` | `data/makecut_trades/` — 615 make-cut markets (566 PGA + 43 DPW with candles), 14 k prints | Yes as of 2026-07-26, ~8 min of the shared 2 req/s budget. P-023 Phase-2 sample. |
| `livtopn_trades.tar.gz` | `data/livtopn_trades/` — 57 LIV top-N markets, 236 prints | Yes, ~1 min. P-023 MARGINAL cohort. |
| `candles.tar.gz` | `data/candles/` — 11,349 settled golf markets, 1m candlesticks | Yes, but ~95 min of the shared 2 req/s Kalshi budget. |
| `settled_meta.jsonl.gz` | `data/settled_meta.jsonl` — settled-market metadata incl. `settlement_value_dollars` | Yes, cheaply. |
| `pull_logs.tar.gz` | `data/trades_pull.log`, `data/candle_pull.log` | n/a — provenance record of how the pulls were made. |

## Correction (2026-07-26) — the "~1 month" roll-off

The original version of this file asserted that `/markets/{t}/trades` only reaches
back ~1 month and that the late-June → late-July window was already gone. **That is
wrong.** Probing one market per tournament on 2026-07-26 returned prints for all 15
golf tournaments in the cache, back to **2026-05-20** — over two months. The make-cut
tick pull re-fetched history from May without difficulty.

Keep archiving the caches anyway (they cost API budget to rebuild, and the roll-off
horizon is undocumented and could change), but do not treat "the data is gone from
the API" as a reason to trust a stale cache over a fresh pull.

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
