# Session 10 — Integration & Live Wiring

## Summary

Built the three pieces that make the engine actually runnable end-to-end: a CLI entry point (`main.py`) that wires every component together into a single invocable command, real HTTP adapters for the macro data sources (`nowcast_http.py`), and a settlement bridge (`settlement_bridge.py`) that connects the pod trade logs to portfolio-level P&L accounting. All 352 tests pass (268 carried forward + 84 new), zero failures. Zero existing files modified.

## New Files Created

### Source Modules (3 files)

| File | Lines | Purpose |
|------|-------|---------|
| `src/main.py` | 290 | CLI entry point. Loads config, builds venue clients, wires PodRunner + CapitalAllocator + AggregateRiskGuard, runs `--once` or `--loop` with guard-gated cycles. Full argparse interface. |
| `src/nowcast_http.py` | 270 | Real HTTP adapters for all three nowcast data sources. `ClevelandFedAdapter` (CPI), `AtlantaFedAdapter` (GDPNow), `FredAdapter` (FRED API), `CompositeNowcastAdapter` (dispatch router). All use stdlib `urllib.request`. |
| `src/settlement_bridge.py` | 290 | Settlement accounting bridge. Reads PLACED trades from per-pod JSONL logs, tracks settled fingerprints, routes `settle()` calls to `CapitalAllocator.record_settlement()` and `AggregateRiskGuard.close_position()`. Persists to `settlements.jsonl`. |

### Test Files (3 files, 84 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_main.py` | 26 | load_config (merge/missing/conflict), filter_pods (subset/unknown/mutation), setup_logging, build_venue_clients (ImportError graceful), build_shared_deps, make_cycle_callback (guard update/snapshot/halted), _run_guarded_loop (max_cycles/halted/crash recovery), main() integration (--once success/no-pods/missing-config/guard-halted) |
| `tests/test_nowcast_http.py` | 34 | ClevelandFed (value/CI/unit/wrong-source/HTTP-error/404/empty-csv), Atlanta (value/unit/wrong-source/HTTP-error/503), FRED (value/unit/no-key-no-request/missing-value-fallback/wrong-source/HTTP-error), Composite (dispatches all three/unknown-source/from_config/register), helpers (_try_float, _parse_float_any, _pick_any) |
| `tests/test_settlement_bridge.py` | 24 | get_open_trades (PLACED only/skips SKIPPED/empty/missing-dir/excludes-settled/multi-file), settle (success/unknown-fp/duplicate/persists/outcome-WIN/outcome-LOSS-corrected/notifies-allocator/notifies-risk), settle_batch (all-success/partial-failure), summary (empty/wins-losses-voids/per-pod), from_config (defaults/custom-paths/wires-deps), robustness (corrupt-JSON/allocator-error-no-crash) |

## Key Design Decisions

1. **`main.py` is fully standalone**: It imports only from `src/` — no hard dependency on the existing system's modules. All existing-system imports (`KalshiClient`, `EdgeCalculator`, `RiskManager`, `Scanner`) are wrapped in try/except blocks so the engine starts gracefully even when running in isolation. Once the existing modules are on the Python path, they're picked up automatically.

2. **Guarded loop vs. `run_loop()`**: The built-in `PodRunner.run_loop()` handles interval timing, but doesn't know about `AggregateRiskGuard`. `_run_guarded_loop()` in main.py wraps each iteration with `guard.check_pre_cycle()` — if the guard is halted (daily loss, cooldown), it logs a warning and sleeps one interval before retrying. This avoids the loop dying permanently on a halt.

3. **Nowcast HTTP adapter pattern**: Each adapter is a thin HTTP wrapper with an injectable `_http_get` callable for testing. Production uses `urllib.request.urlopen` with a 10-second timeout and a `User-Agent` header. Column name discovery is flexible — the parsers try multiple known column name variants for robustness against minor format changes in the source CSVs.

4. **FRED optional**: The `FredAdapter` requires an API key (free to register at fred.stlouisfed.org). Without one, `get_reading()` returns `None` immediately and makes no network call. The other two adapters (Cleveland Fed, Atlanta Fed) require no credentials.

5. **Settlement bridge one-way write**: The bridge appends to `settlements.jsonl` — it never modifies the pod logs. The "open trades" list is derived by taking all PLACED fingerprints from pod logs and subtracting the settled fingerprints. This makes recovery after a crash trivial: just re-read both files.

6. **Outcome auto-correction**: If `settle(fp, pnl=-15, outcome="WIN")` is called (a common mistake), the bridge corrects the outcome to `"LOSS"` based on the actual pnl sign. This prevents accounting errors from manual settlement entry.

## How to Run

```bash
# Single paper scan (all active pods from config_multi_pod.yaml):
python -m src.main --once

# Continuous loop every 5 minutes, specific pods only:
python -m src.main --loop --interval 300 --pods P-001,P-006

# Live mode with custom config and verbose logging:
python -m src.main --loop --mode live --config /path/to/config.yaml -v

# Full options:
python -m src.main --help
```

## Test Results

```
Ran 352 tests in 0.137s — OK
```

Breakdown by file: 21 + 22 + 14 + 18 + 23 + 12 + 26 + 14 + 17 + 16 + 13 + 19 + 16 + 17 + 20 + 26 + 34 + 24 = 352

## Cumulative Project State

| Metric | Session 8 | Session 9 | Session 10 |
|--------|-----------|-----------|------------|
| Source modules | 14 | 17 | 20 |
| Test files | 12 | 15 | 18 |
| Total tests | 215 | 268 | 352 |
| Pods implemented | 5 | 5 | 5 (unchanged) |
| Engine components | 0 | 3 | 3 (unchanged) |
| Entry points | 0 | 0 | 1 (main.py) |
| Data adapters | 0 | 0 | 3 (ClevFed/Atlanta/FRED) |
| Settlement tracking | 0 | 0 | 1 (SettlementBridge) |

## What's Next (Session 11+)

- **T1 promo pods (P-009, P-010)**: Sign-up bonus converter and daily odds boost scanner. These were the highest-priority T1 items by revenue ($3K–6K/month), but require FanDuel/DraftKings account setup. No code complexity — mainly an account tracker + EV calculator workflow.
- **P-012 live activation**: Wire `PAYEMS` FRED API key in config, enable P-012 in `config_multi_pod.yaml`, run first live scan cycle with `python -m src.main --once --pods P-012`.
- **Dashboard extension**: Extend the existing terminal dashboard to show multi-pod status, per-pod P&L from `SettlementBridge.summary()`, and `AggregateRiskGuard.snapshot()` metrics.
- **Backtesting**: Use the existing `backtester` module against archived economic data to validate P-004 and P-012 edge models before going live.
- **Paper-to-live checklist**: Document the steps to flip each pod from `environment: paper` to `environment: live` — credential checks, position limits, first-cycle review gate.
