# `crossvenue_research/archive/` — committed data cache

`crossvenue_cache_20260726.tar.gz` expands to `crossvenue_research/data/`
(657 files, 271 MB → 11 MB).

Contents: the Polymarket Gamma universe (3,752 closed politics/world/econ markets), the
Kalshi settled set (86,825 markets across 1,942 series), per-market Kalshi candlesticks,
and Polymarket CLOB `/prices-history` series for the matched pairs.

**The Polymarket half cannot be re-pulled.** The CLOB `/prices-history` endpoint retains
roughly the last **30 days** — markets ending before ~2026-06-20 return an empty history
array (verified across six month-buckets on 2026-07-26). Re-running `harvest.py` next
month will NOT reproduce the 2026-06-15 → 07-27 window this study rests on; it is gone
from the API. That retention limit is also the reason 152 HIGH-confidence pairs collapse
to 34 with usable price series, and why the study lands on 5 event clusters.

Archived here rather than left in `data/` because `.gitignore` excludes `data/`
repo-wide — the blind spot that lost the 2026-07-25 harness. The in-tree
`crossvenue_research/.gitignore` correctly warns the directory is irreplaceable; this
archive is what makes that warning enforceable.

Restore:

```bash
tar xzf crossvenue_research/archive/crossvenue_cache_20260726.tar.gz -C crossvenue_research
```

Then `python3 crossvenue_research/backtest_crossvenue.py` reproduces every table in the
REPORT with **no network calls**.
