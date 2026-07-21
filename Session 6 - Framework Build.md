# Session 6 — Framework Build

## Summary

Implemented the core multi-pod infrastructure designed in Session 5. All 6 new modules are built, tested, and passing — 96 tests, 0 failures. Zero existing files were modified.

## New Files Created

### Source Modules (6 files)

| File | Lines | Purpose |
|------|-------|---------|
| `src/base_pod.py` | 120 | `ScanResult` frozen dataclass + `BasePod` ABC with fingerprinting, dedup, JSONL logging |
| `src/pods/__init__.py` | 6 | Package marker |
| `src/pods/kalshi_moneyline.py` | 100 | P-001 wrapper: delegates to existing `Scanner.scan_once()`, converts dicts → `ScanResult` |
| `src/polymarket_client.py` | 200 | Polymarket CLOB wrapper: paper mode default, injectable adapter, `get_price()` preferred |
| `src/polymarket_matcher.py` | 200 | Fuzzy matching for Polymarket question-style naming (80 threshold vs Kalshi's 85) |
| `src/multi_executor.py` | 185 | Venue-agnostic router: directs `ScanResult` to correct client (Kalshi/Polymarket/ForecastEx) |
| `src/fuzzy_utils.py` | 30 | Lightweight token-sort ratio using `difflib.SequenceMatcher` (no external deps) |

### Test Files (5 files, 96 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_base_pod.py` | 23 | ScanResult frozen fields, to_dict, fingerprint dedup, write_log, log persistence |
| `tests/test_kalshi_moneyline_pod.py` | 12 | Delegation, field mapping, from_config, SKIPPED entries |
| `tests/test_polymarket_client.py` | 21 | Paper/live modes, get_price/book/markets, rate limiter, from_config, no-client fallback |
| `tests/test_polymarket_matcher.py` | 22 | Fuzzy scoring, question extraction, match/reject paths, time window, unmatched log |
| `tests/test_multi_executor.py` | 18 | Paper routing, live Kalshi/Polymarket routing, run_cycle, pod errors, from_config |

## Key Design Decisions

1. **Zero external dependencies added** — fuzzy matching uses stdlib `difflib.SequenceMatcher` instead of `fuzzywuzzy`/`thefuzz`. Logging uses stdlib `logging` instead of `structlog`. When deploying to real repo, swap to `thefuzz` + `structlog` imports (both files have clear import points).

2. **KalshiMoneylinePod wraps Scanner via delegation** — `scan_once()` calls `Scanner.scan_once()` and maps each dict entry to `ScanResult`. All 88 existing Scanner tests remain untouched.

3. **PolymarketClient defaults to paper mode** — aligns with US waitlist constraint. Paper orders log but don't execute. Live mode requires `py-clob-client` + private key.

4. **MultiExecutor routes by venue** — reads `result.venue` field and dispatches to the correct `_execute_*` method. Unknown venue → error result (no crash).

5. **ScanResult generalises Scanner dict** — key field renames: `sport` → `market_type`, `market_ticker` → `market_id`, `kalshi_prob` → `venue_prob`. New fields: `pod_id`, `pod_name`, `venue`, `extra`.

## What's Unchanged

- `src/scanner.py` — untouched
- `src/executor.py` — untouched
- `src/matcher.py` — untouched
- `src/edge_calculator.py` — untouched
- `src/kalshi_client.py` — untouched
- `src/risk_manager.py` — untouched
- All 750+ existing tests — untouched

## Next Session (7+)

Per the build roadmap, the next implementation targets are:

- **T1 Pods**: P-009 (FanDuel/DK Promo Converter), P-006 (Sportsbook-Polymarket Consensus, paper), P-010 (DK Odds Boost Grinder)
- **Config extension**: Add `polymarket`, `forecastex`, and `pods` YAML sections
- **Integration wiring**: Connect MultiExecutor + pods into the main run loop
- **Dashboard updates**: Add pod-level metrics to existing dashboard
