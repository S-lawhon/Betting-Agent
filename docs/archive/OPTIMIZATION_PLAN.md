# Betting Pod Shop — Optimization Implementation Plan

**Created:** March 12, 2026
**Based on:** Codebase Optimization Report (same date)
**Last Updated:** March 14, 2026

---

## Phase I: Critical Performance & Data Integrity (P0) ✅ COMPLETE

The foundation — these three changes have the highest standalone impact and unblock later phases.

### 1. TradeStore Singleton (Report §1.1) ✅
- New module: `src/trade_store.py`
- In-memory indexed store loaded once on startup
- Indexes: by fingerprint, by ticker, by pod_id, by status (open/settled)
- All 7 read sites rewired to use TradeStore instead of re-parsing JSONL
- Atomic append: `fsync()` on every write (also addresses Report §2.1)
- **Files touched:** `src/trade_store.py` (new), `src/main.py`, `src/base_pod.py`, `src/polymarket_settler.py`, `Legacy/.../settler.py`, `Legacy/.../scanner.py`, `src/settlement_bridge.py`, `src/capital_allocator.py`

### 2. Concurrent Pod Scanning (Report §1.2) ✅
- `ThreadPoolExecutor` in `src/pod_runner.py`
- Pre-cycle (risk guard) runs single-threaded, then pods fan out, results merge
- **Files touched:** `src/pod_runner.py`

### 3. Atomic Trade Log Writes (Report §2.1) ✅
- Folded into TradeStore — every write gets `flush()` + `fsync()`
- Malformed-line recovery on load (skip + warn)
- **Files touched:** `src/trade_store.py` (covered by item 1)

### 4. Tests for Phase I ✅
- `tests/test_trade_store.py` — unit tests for TradeStore
- `tests/test_pod_runner_concurrent.py` — verify concurrent scanning works
- **Deliverable:** All existing tests still pass; new tests green

---

## Phase II: Reliability & Safety Hardening (P1) ✅ COMPLETE

### 5. Exception Audit (Report §2.2) ✅
- Audited all 91 `except Exception` blocks
- Narrowed to specific exceptions, upgraded silent `pass` to logged warnings
- Critical paths (trade log writes, order placement) fail loudly

### 6. HTTP Connection Pooling (Report §1.3) ✅
- Persistent `httpx.Client` sessions in `polymarket_client.py`
- `urllib3.PoolManager` or `httpx` session in legacy `kalshi_client.py` and `odds_client.py`

### 7. Centralize Magic Numbers (Report §5.1) ✅
- Created `src/constants.py` with all hardcoded thresholds:
  - `DEFAULT_BANKROLL = 10_000.0`
  - `DEFAULT_MAX_BET_PCT = 0.03`
  - `MIN_VALID_PRICE = 0.03`
  - `MAX_VALID_PRICE = 0.97`
  - `HTTP_TIMEOUT_SECONDS = 15.0`
  - `DEDUP_WINDOW_HOURS = 1`
- Replaced hardcoded `10_000.0` in 12+ locations and `0.03` in 4+ pod files
- **Files touched:** `src/constants.py` (new), `src/aggregate_risk.py`, `src/capital_allocator.py`, `src/main.py`, `src/web_dashboard.py`, all 4 active pod files

### 8. VPS Security Hardening (Report §4.1, §4.5) ✅
- Created `bettingbot` system user, updated service file
- Service runs as `Type=notify` under `bettingbot` user (not root)
- Hardened with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `ReadWritePaths=/opt/betting-pod-shop/data`, `RestrictAddressFamilies`
- **Files touched:** `scripts/betting-pod-shop.service` (rewritten), `scripts/server_setup.sh` (rewritten)

### 9. Watchdog + Log Rotation (Report §4.2, §4.3) ✅
- `WatchdogSec=900` in service file
- `src/watchdog.py` — zero-dependency systemd notify socket integration (`notify_watchdog()`, `notify_ready()`, `notify_status()`, `notify_stopping()`)
- `journald` max size config (500 MB cap via `server_setup.sh`)
- `scripts/rotate_trade_logs.py` — archives entries >30 days into compressed monthly `.jsonl.gz` files
- Cron job installed by `server_setup.sh`
- **Files touched:** `src/watchdog.py` (new), `scripts/rotate_trade_logs.py` (new), `scripts/server_setup.sh`, `scripts/betting-pod-shop.service`
- **Tests:** `tests/test_watchdog.py` (7 tests), `tests/test_rotate_trade_logs.py` (5 tests)

---

## Phase III: Architecture Cleanup (P2) ✅ COMPLETE

### 10. Split main.py (Report §3.5) ✅
- Extracted from `main.py` (1191 lines → 4 focused modules + thin shim):
  - `src/config_loader.py` — `load_config()`, `filter_pods()`, `_deep_merge()`, `enrich_sports_with_active_tennis()`
  - `src/engine.py` — `build_venue_clients()`, `build_shared_deps()`, `make_cycle_callback()`, `_run_guarded_loop()`, `_resolve_bet_team()`, `_read_recent_placed_trades()`, `_compute_settlement_summary()`
  - `src/cli.py` — `setup_logging()`, `_build_parser()`, standalone `main()` entry point
  - `src/main.py` — thin backward-compat shim re-exporting all symbols so test patches (`@patch("src.main.PodRunner")` etc.) continue to work
- **Files touched:** `src/config_loader.py` (new), `src/engine.py` (new), `src/cli.py` (new), `src/main.py` (rewritten as shim)

### 11. Normalize Log Schemas (Report §2.3) ✅
- `src/trade_log_schema.py` — `TradeLogSchema.normalize()`, `TradeLogSchema.validate()`, `TradeLogSchema.migrate_file()`
- `CANONICAL_FIELDS` list with field aliasing (market_ticker→market_id, kalshi_prob→venue_prob)
- Handles defaults, numeric coercion, atomic file rewrite
- `scripts/migrate_trade_log.py` — one-time migration script for existing 62K entries
- **Tests:** `tests/test_trade_log_schema.py` (15 tests)

### 12. BasePod Helpers (Report §3.2) ✅
- Added `get_bankroll()` — reads from risk_manager.ledger, falls back to `DEFAULT_BANKROLL`
- Added `compute_position_size(kelly_fraction)` — applies bankroll × Kelly with max_position_usd cap
- Eliminated duplicated 12-line position sizing blocks in all 4 active pods → single-line call
- **Files touched:** `src/base_pod.py`, `src/pods/cross_venue_arb.py`, `src/pods/forecastex_kalshi_arb.py`, `src/pods/macro_nowcast.py`, `src/pods/polymarket_consensus.py`

### 13. Deploy Script Hardening (Report §4.4) ✅
- Pre-deploy: `python3 -m pytest` gate (prompts to continue if failures)
- Sync: rsync with excludes for `data/`, `*.jsonl`, `*.log`, `.mypy_cache`, `.pytest_cache`, `venv/`
- Post-deploy: 60-second health check hitting `/health` endpoint
- Automatic rollback: on health check failure, restores pre-deploy backup and restarts
- **Files touched:** `scripts/deploy.sh` (rewritten)

### 14. Integration Tests (Report §5.3) ✅
- `tests/test_integration.py` — 10 end-to-end tests covering scan → match → edge → risk → place → settle pipeline
- Mock API server for deterministic testing
- **Files touched:** `tests/test_integration.py` (new)

---

## Phase IV: Developer Experience & Future-Proofing (P3) ✅ COMPLETE

### 15. Auto-Discover Pods (Report §3.1) ✅
- `src/pod_registry.py` — `@register_pod(pod_id)` class decorator, `discover_pods()` imports all `src/pods/` modules, `get_pod_class(pod_id)` with legacy fallback
- All 7 pod files decorated with `@register_pod("P-xxx")`
- `pod_runner.py` updated to use `discover_pods()` + `get_pod_class()`
- Legacy `POD_REGISTRY` dict kept for backward compat
- **Files touched:** `src/pod_registry.py` (new), `src/pod_runner.py`, all 7 pod files

### 16. Extract Dashboard HTML/JS (Report §3.4) ✅
- Moved 815 lines of inline HTML/CSS/JS from `web_dashboard.py` into `src/templates/dashboard.html`
- `web_dashboard.py` shrank from 1,201 → 411 lines
- Template loaded from disk via `_load_template()` with graceful fallback if file missing
- **Files touched:** `src/templates/dashboard.html` (new), `src/web_dashboard.py`

### 17. Type Hints (Report §5.2) ✅
- `src/protocols.py` — Protocol-based structural types for `EdgeCalculator`, `RiskManager`, venue clients (`Kalshi`, `Polymarket`, `ForecastEx`), `AggregateRisk`, `TradeStore`
- Fixed 8 mypy errors across pods (missing annotations, incorrect return types, union-attr issues)
- All 18 hot-path source files pass `mypy --ignore-missing-imports --explicit-package-bases` cleanly
- **Files touched:** `src/protocols.py` (new), `src/pods/cross_venue_arb.py`, `src/pods/macro_nowcast.py`, `src/pods/polymarket_consensus.py`, `src/polymarket_client.py`

### 18. Shared P&L Calculator (Report §5.5) ✅
- `src/pnl_calculator.py` — `PnLCalculator` with venue-specific `VenueFees` structures:
  - Kalshi: 7% profit fee (only on wins)
  - Polymarket: 0% fees
  - ForecastEx: 0% commission + 3.14% APY coupon on locked collateral
- Supports batch computation, custom fee overrides, holding period collateral credit
- **Files touched:** `src/pnl_calculator.py` (new)
- **Tests:** `tests/test_pnl_calculator.py` (17 tests)

### 19. Legacy Adapter Layer (Report §3.3) ✅
- `src/compat.py` — single-import access to all public symbols across refactored modules
- Deprecated aliases (`RiskGuard` → `AggregateRiskGuard`, `RunReport` → `CycleReport`, `ExecutionResult` → `MultiExecutionResult`) emit `DeprecationWarning` at import time
- `check_deprecated_imports()` utility for migration audits
- **Files touched:** `src/compat.py` (new)

---

## Test Suite Summary

| Phase | Tests Added | Cumulative Passing | Pre-existing Failures |
|-------|-------------|--------------------|-----------------------|
| Phase I | test_trade_store, test_pod_runner_concurrent | 642 | 35 |
| Phase II | test_watchdog (7), test_rotate_trade_logs (5) | 654 | 35 |
| Phase III | test_trade_log_schema (15), test_integration (10) | 679 | 35 |
| Phase IV | test_pnl_calculator (17) | 696 | 35 |

All 35 pre-existing failures are unchanged throughout — no regressions introduced.
