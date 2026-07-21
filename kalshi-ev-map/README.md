# kalshi-ev-map

Opportunity map for systematic trading on Kalshi. Snapshot date: 2026-07-18.

## Deliverables
1. [01_universe_survey.md](01_universe_survey.md) — family-level liquidity/structure table, fee schedule, MVE analysis
2. [02_edge_hypotheses.md](02_edge_hypotheses.md) — hypotheses with mechanism/counterparty/falsification; live test results
3. [03_validation.md](03_validation.md) — calibration curves, fee-aware backtests, headline-vs-props verdict
4. [04_opportunity_ranking.md](04_opportunity_ranking.md) — scored ranking across 7 dimensions
5. [05_build_roadmap.md](05_build_roadmap.md) — **main deliverable**: what to build, in order, with kill-tests

## Code (`src/`)
- `kalshi_client.py` — throttled, retrying public-API client with disk cache
- `fees.py` — verified fee model (July 7, 2026 schedule) incl. per-series multipliers
- `families.py` — series→family taxonomy
- `pull_universe2.py` — per-series open-market universe pull (avoids the MVE flood)
- `pull_settled.py`, `pull_candles.py` — settled history + pre-close executable quotes
- `pull_orderbooks.py` — stratified top-3-level depth sampler
- `pull_polymarket.py`, `pull_trades_sample.py` — cross-venue + tape utilities
- `consistency.py` — bracket-sum & monotonicity scanner
- `calibration.py`, `headline_vs_props.py` — calibration, Wilson CIs, fee-aware backtests

## Data (`data/`)
`kalshi_universe.parquet` (68,561 open markets), `kalshi_settled.parquet` (1.0M settled),
`settled_candles.parquet` (18,152 with pre-close quotes), `orderbook_sample.parquet`,
`polymarket_active.parquet`, fee schedule PDF + extracted text, raw JSON caches in `data/raw/`.

All pulls cache to disk and resume; re-run any `pull_*.py` to refresh.
Python: use `/usr/bin/python3` (pandas/pyarrow installed there).
