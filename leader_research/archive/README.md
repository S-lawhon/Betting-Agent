# `leader_research/archive/` — committed data cache

`leader_books_20260726.tar.gz` expands to `leader_research/data/` (583 files, 8.6 MB → 437 KB).

Contents: the 2026-07-26 19:15 UTC snapshot behind the P-026 KILL — 426 KXLEADER orderbooks,
30-day trade prints, the series index, MLB standings from `statsapi.mlb.com`, and
`split_pricing_analysis.json`.

**Top-of-book snapshots cannot be re-pulled.** They are the live quote state at one instant,
and the whole P-026 verdict rests on the bid/mid/ask decomposition of exactly this snapshot
(max `SUM(bid)` = 99.0¢ against the 100¢ partition ceiling). Re-running the puller tomorrow
produces a different, non-comparable dataset. It is archived here rather than left in `data/`
because `.gitignore` excludes `data/` repo-wide — the blind spot that lost the July 25
harness.

Restore:

```bash
tar xzf leader_research/archive/leader_books_20260726.tar.gz -C leader_research
```
