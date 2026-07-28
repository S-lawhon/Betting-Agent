# `golf_quirks_research/archive/` — committed data caches

These are the P-022 / P-023 Kalshi pulls, compressed. They live here rather than in
`data/` because `.gitignore` excludes `data/`, `*.jsonl` and `*.log` repo-wide, which is
exactly how the 2026-07-25 loss happened: the working caches were invisible to
`git status` and nobody noticed they were untracked.

208 MB of loose files compresses to 10 MB, so there is no reason not to keep them.

| Archive | Expands to | Re-pullable? |
|---|---|---|
| `leader_trades_widened_20260728.tar.gz` | `data/leader_trades/` — **404** round-leader markets. **This is what `restore.sh` expands by default**: the current sample, a strict superset of the published 364. | See the 2026-07-28 note below — parts of it are no longer re-pullable at all. |
| `candles_widened_20260728.tar.gz` | `data/candles/` — **13,473** settled golf markets, 1m candlesticks. Default. | Yes, but ~110 min of the shared 2 req/s Kalshi budget. |
| `leader_trades.tar.gz` | `data/leader_trades/` — the frozen **364** markets the published P-022 Phase-2 cells rest on. `restore.sh --published-only`. | Re-pullable as of 2026-07-26, but do not count on it — see the note below. |
| `makecut_trades.tar.gz` | `data/makecut_trades/` — 615 make-cut markets (566 PGA + 43 DPW with candles), 14 k prints | Yes as of 2026-07-26, ~8 min of the shared 2 req/s budget. P-023 Phase-2 sample. |
| `livtopn_trades.tar.gz` | `data/livtopn_trades/` — 57 LIV top-N markets, 236 prints | Yes, ~1 min. P-023 MARGINAL cohort. |
| `candles.tar.gz` | `data/candles/` — 11,349 settled golf markets, 1m candlesticks. `restore.sh --published-only`. | Yes, but ~95 min of the shared 2 req/s Kalshi budget. |
| `settled_meta.jsonl.gz` | `data/settled_meta.jsonl` — settled-market metadata incl. `settlement_value_dollars` | Yes, cheaply. |
| `schedule_probe_caches.tar.gz` | `data/espn_schedule.json`, `espn_teetimes.json`, `leader_event_meta.json`, `leader_event_close_repair.json` — the P-022 close-time resolver's validation inputs (2026-07-28) | ESPN legs yes, cheaply. The Kalshi `/events` product_metadata leg is 72 calls and is the join key between a Kalshi event ticker and a tournament NAME, which nothing else carries. |
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

## Correction (2026-07-28) — part of the sample IS now gone

The 2026-07-26 correction above is itself now out of date for the leader cache.
Re-probing on 2026-07-28 (`REPORT_P022_Widened_2026-07.md` §1), the oldest cached
tournaments return **no prints at all** — `KXPGAR1LEAD-THCCBN26`,
`KXPGAR3LEAD-THCCBN26`, `KXDPWORLDTOURR1LEAD-AUAOPBKT26`, `KXLIVR3LEAD-LIGK26`, each
0 of 6 markets. **These archives are the only surviving copy of part of the published
P-022 Phase-2 sample.** Do not delete them, and do not assume a fresh pull can rebuild
`leader_trades`.

## Restore

```bash
bash golf_quirks_research/archive/restore.sh                    # widened: 404 leader markets (default)
bash golf_quirks_research/archive/restore.sh --published-only   # frozen 364 the published cells rest on
```

Refuses to clobber an existing non-empty `data/` unless you pass `--force`; with
`--force` it clears `leader_trades/` and `candles/` before expanding, so switching
between the two universes actually switches (tar merges rather than replaces, and a
`--published-only` restore over a widened tree would otherwise hand back 404 markets
while claiming 364).

Until 2026-07-28 `restore.sh` expanded only `leader_trades`, `candles` and `pull_logs`
— the four P-023 cohort archives were listed in the table above but never restored,
and `backtest_makecut_fills.py` ran to completion against the absent cache and wrote a
results file with every cohort empty rather than failing. All ten archives are
restored now; a full restore regenerates `makecut_fill_results.json` byte-identical to
the committed copy.

`backtest_fade_fills.py --validate` pins itself to `published_universe_364.json` and
so reproduces the published cells under **either** restore — verified both ways on
2026-07-28. That is the point of the pin: the reproduction test is independent of
which cache is on disk.

## Provenance

Pulled 2026-07-23 by the P-022 Phase-2 harness. The harness `.py` files were lost on
2026-07-25 (never committed) and are being rebuilt — see
`research/prompts/PROMPT_P023_MakeCut_Phase2.md`. These caches are what makes that
rebuild possible at all.
