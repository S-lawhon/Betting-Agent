# `econstat_research/archive/` — committed data cache

`econstat_cache_20260726.tar.gz` expands to `econstat_research/data/`.

Contents: the 2026-07-26 pull behind the P-027 KILL — `series_all.json` (4,106 series),
`econstat_series.json` (the 197 ECONSTAT-template series), `markets/*.json` (197 files,
3,156 markets with their certified `rules_primary`), `candles/*.json` (264 settled markets,
1-minute books), `settle_lag.json`, `price_test.json`, the `ECONSTAT.pdf`/`ECONSTATTE.pdf`
contract terms with extracted text, and `umich_ics_final.csv`.

Archived rather than left loose in `data/` because `.gitignore` excludes `data/` repo-wide —
the blind spot that lost the July 25 harness. Re-pullable, but it is ~3,600 calls against the
shared 2 req/s Kalshi budget, and the cache is what makes the study reproducible without
re-hitting the API.

Restore:

```bash
tar xzf econstat_research/archive/econstat_cache_20260726.tar.gz -C econstat_research
```
