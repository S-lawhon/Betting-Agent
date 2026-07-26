# satellites_research cache archive

`satellites_research/data/` is gitignored repo-wide (`.gitignore:21 data/`), so the
cached Kalshi pulls that back the satellite census live here as a tarball instead.

`satellites_cache_20260726.tar.gz` (1.7 MB) contains, relative to `data/`:

| Path | What |
|---|---|
| `templates.json` | 9,839 Kalshi series grouped into 2,662 contract-terms templates |
| `award_markets/` | every market for 407 award series (Study A) |
| `rt_markets/` | every market for the RT / RTTV / RTCOMPARISON / METACRITIC families (Study B) |
| `candles/` | hourly pre-close `yes_bid`/`yes_ask` candlesticks for 607 settled markets |
| `wins_snapshots/` | live WINTOTAL order-book snapshots (Study C scanner) |
| `wayback_rt.json` | archived Tomatometer readings scraped from the Wayback Machine |

Not archived (regenerable, and large): `all_series.json` and the per-category
`series_*.json` raw pulls — 20 MB, rebuilt in ~15 s by
`python3 satellites_research/pull_series_census.py`.

To restore:

```bash
tar xzf satellites_research/archive/satellites_cache_20260726.tar.gz \
    -C satellites_research/data
```

Every pull script is cache-first, so restoring the tarball means the analyses
re-run with **zero** Kalshi API calls.
