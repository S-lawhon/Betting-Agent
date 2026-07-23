# Session 9 — Unified Engine

## Summary

Built the three core engine components that tie the multi-pod system together: PodRunner (unified main loop), CapitalAllocator (portfolio-level bankroll management), and AggregateRiskGuard (cross-pod risk controls). This completes Phase 5 of the build roadmap — the system now has a full execution pipeline from config loading through pod instantiation, capital allocation, scan execution, and portfolio-wide risk enforcement. All 268 tests pass (215 carried forward + 53 new), zero failures. Zero existing files modified.

## New Files Created

### Source Modules (3 files)

| File | Lines | Purpose |
|------|-------|---------|
| `src/pod_runner.py` | 230 | Unified main loop. Loads config, dynamically imports pods from `POD_REGISTRY`, runs scan cycles via MultiExecutor, fires callbacks. Supports `run_once()` and `run_loop(interval, max_cycles)`. |
| `src/capital_allocator.py` | 260 | Portfolio-level capital allocation. Three strategies (equal, weighted, performance-based). Drawdown throttling halves allocation when a pod's max drawdown exceeds threshold. Tracks per-pod wins/losses/P&L. |
| `src/aggregate_risk.py` | 250 | Cross-pod aggregate risk controls. Enforces total/venue exposure caps, daily loss halt with cooldown, open position limits, and emergency kill switch requiring manual resume. |

### Test Files (3 files, 53 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_pod_runner.py` | 16 | run_once returns CycleReport, tallies placed/skipped/errors, executor integration, callback firing, capital_allocator hooks, run_loop multi-cycle, max_cycles, stop_on_error, history accumulation |
| `tests/test_capital_allocator.py` | 17 | Equal/weighted strategies, pre_cycle pod initialization, bankroll push, drawdown throttling, record_settlement wins/losses/drawdown/consecutive, PodPerformance metrics, from_config |
| `tests/test_aggregate_risk.py` | 20 | Pre-cycle clear/halted/is_halted, daily loss halt/reset, cooldown resume/still-halted, check_trade exposure/venue/position/halted, close_position, emergency halt + manual resume, snapshot, update_post_cycle, from_config |

## Key Design Decisions

1. **Three-tier risk architecture**: Risk is enforced at three layers — per-pod RiskManager (individual position limits), CapitalAllocator (bankroll sizing per pod), and AggregateRiskGuard (portfolio-wide halts). Each layer can independently veto trades or halt scanning.

2. **Dynamic pod registry**: `POD_REGISTRY` maps pod IDs to `(module_path, class_name)` tuples. `PodRunner.from_config()` reads the active pod list from config, dynamically imports each pod class via `importlib`, and calls `from_config()` with shared dependencies. Adding a new pod requires only a registry entry — no hard imports.

3. **Capital allocation strategies**: Three built-in strategies serve different portfolio phases. `equal` splits bankroll evenly (good for starting). `weighted` uses config-defined weights (good for conviction-based sizing). `performance` scores pods by `win_rate * avg_pnl * (1 - drawdown)` and allocates proportionally (good for mature portfolios with track records).

4. **Drawdown throttling**: When a pod's max drawdown exceeds `drawdown_throttle_pct` (default 10%), its allocation is halved automatically. This provides a soft degradation before the aggregate risk guard's hard halt. Consecutive loss tracking feeds into this — 5 straight losses on a $100 avg bet triggers throttling.

5. **Daily loss halt with cooldown**: AggregateRiskGuard halts all trading when daily P&L loss exceeds `max_daily_loss_pct` (default 5% of bankroll). After `cooldown_minutes` (default 60), trading auto-resumes. Emergency halt sets cooldown to infinity, requiring an explicit `resume()` call. Daily P&L resets at midnight UTC.

6. **CycleReport as the communication primitive**: Every `run_once()` produces a `CycleReport` with full tallies (placed, skipped, errors, duration). The callback system lets callers hook into the report stream for logging, dashboards, or alerts without coupling to the engine internals.

## Test Results

```
Ran 268 tests in 0.034s — OK
```

Breakdown by file: 21 + 22 + 14 + 18 + 23 + 12 + 26 + 14 + 17 + 16 + 13 + 19 + 16 + 17 + 20 = 268

## Cumulative Project State

| Metric | Session 6 | Session 7 | Session 8 | Session 9 |
|--------|-----------|-----------|-----------|-----------|
| Source modules | 7 | 10 | 14 | 17 |
| Test files | 5 | 8 | 12 | 15 |
| Total tests | 96 | 150 | 215 | 268 |
| Pods implemented | 1 (P-001) | 2 (+P-006) | 5 (+P-002, P-004, P-012) | 5 (unchanged) |
| Venue connectors | 2 | 3 | 3 | 3 |
| Engine components | 0 | 0 | 0 | 3 (PodRunner, CapitalAllocator, AggregateRiskGuard) |

## Architecture Overview

```
config_multi_pod.yaml
        │
        ▼
   PodRunner.from_config()
        │
        ├── POD_REGISTRY → importlib → Pod.from_config()
        │       ├── P-001 KalshiMoneylinePod
        │       ├── P-002 CrossVenueArbPod
        │       ├── P-004 ForecastExKalshiArbPod
        │       ├── P-006 PolymarketConsensusPod
        │       └── P-012 MacroNowcastPod
        │
        ├── CapitalAllocator
        │       ├── pre_cycle() → allocate bankroll per pod
        │       ├── post_cycle() → track placements
        │       └── record_settlement() → P&L + drawdown
        │
        ├── AggregateRiskGuard
        │       ├── check_pre_cycle() → halt / cooldown / daily loss
        │       ├── check_trade() → exposure / venue / position limits
        │       └── emergency_halt() / resume()
        │
        └── MultiExecutor.run_cycle()
                ├── pod.scan_once() → List[ScanResult]
                └── route orders → Kalshi / Polymarket / ForecastEx
```

## What's Next (Session 10+)

Per the build roadmap, remaining priorities are:

- **T1 promo pods (P-009, P-010)**: Sign-up bonus converter and daily odds boost scanner. Highest immediate revenue potential. No API dependency — manual + calculator workflow.
- **NowcastClient HTTP wiring**: Implement Cleveland Fed, Atlanta Fed, and FRED fetchers (currently stubs). Free public APIs, no auth required.
- **Integration wiring**: Connect PodRunner + AggregateRiskGuard + CapitalAllocator into a single `main.py` entry point with CLI args for mode (paper/live), config path, and pod selection.
- **Dashboard extension**: Update terminal dashboard to show multi-pod status, per-pod P&L, aggregate risk snapshot, allocation breakdown.
- **Backtesting**: Run historical simulations for P-004 and P-012 using archived economic data to validate edge models before going live.
