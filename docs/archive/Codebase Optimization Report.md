# Betting Pod Shop — Codebase & Architecture Optimization Report

**Date:** March 12, 2026
**Scope:** Full codebase audit across `src/` (27 files, ~10,400 LOC), `Legacy/` (16 files, ~8,500 LOC), `scripts/` (9 files), and configuration
**Trade Log:** 62,226 lines in `trade_log.jsonl`

---

## Executive Summary

The Betting Pod Shop is a well-structured multi-pod sports betting engine with clean separation of concerns, comprehensive test coverage (750+ legacy tests, 24 new test files), and solid safety-first design (dual trading gates, fingerprint dedup, daily loss guards). The codebase reflects rapid iterative development — the architecture is sound, but several patterns that worked at prototype scale are now creating real performance, maintainability, and reliability costs as the system matures.

This report identifies **28 specific optimizations** across five categories, ranked by impact. The top three would each independently produce measurable improvements to cycle time, reliability, or developer velocity.

---

## Category 1: Performance Bottlenecks (Critical)

### 1.1 Trade Log Full-Scan Anti-Pattern

**The single biggest performance issue in the codebase.**

The 62,226-line `trade_log.jsonl` is re-read from disk in at least 7 places every scan cycle:

- `scanner.py` → `_open_game_keys()` (lines 414–452): reads entire log to find open positions
- `settler.py` → `_open_placed_entries()` (lines 317–354): reads entire log for unresolved PLACED
- `polymarket_settler.py` → same pattern for P-006
- `base_pod.py` → `_load_seen_from_log()` on every pod init
- `main.py` → `_read_recent_placed_trades()` (lines ~680+): reads log for dashboard
- `settlement_bridge.py` → `_load_trades()`: reads all pod logs via glob
- `capital_allocator.py` → `bootstrap_from_trade_log()`: reads log on startup

Each full scan parses 62K+ JSON lines. At 5-minute intervals, this is ~12 full parses per cycle (across components), meaning the system deserializes ~750K JSON objects per cycle just for bookkeeping.

**Recommendation:** Introduce an in-memory `TradeStore` singleton that loads once on startup and maintains indexed views (by fingerprint, by ticker, by pod_id, by status). All components read from this store instead of re-parsing the file. The store appends new entries to both memory and disk. This alone would cut per-cycle I/O by ~95% and reduce cycle time by 1–2 seconds.

```
TradeStore (singleton)
  ├── _all: List[dict]                    # chronological
  ├── _by_fingerprint: Dict[str, dict]    # O(1) lookup
  ├── _by_ticker: Dict[str, List[dict]]   # settlement matching
  ├── _by_pod: Dict[str, List[dict]]      # pod performance
  ├── _open: Dict[str, dict]              # PLACED without settlement
  ├── append(entry) → writes to memory + disk
  └── settled(fp, outcome) → updates indexes
```

### 1.2 Sequential Pod Scanning

All pods run sequentially in `pod_runner.py`'s `run_once()`. With 2 active pods (P-001, P-006), each making multiple HTTP calls to Odds API, Gamma API, and Kalshi API, scan cycles take 5–9 seconds. Adding more pods (P-002, P-004, P-012) would push this beyond the 5-minute interval.

**Recommendation:** Run pod scans concurrently using `concurrent.futures.ThreadPoolExecutor`. Each pod's `scan_once()` is I/O-bound (HTTP calls), making threading highly effective without requiring async refactoring. The aggregate risk guard's `pre_cycle_check()` runs first (single-threaded), then pods fan out, then results merge back for post-cycle processing.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def run_once(self):
    self.aggregate_risk.pre_cycle_check()
    with ThreadPoolExecutor(max_workers=len(self.pods)) as pool:
        futures = {pool.submit(pod.scan_once): pod for pod in self.pods}
        for future in as_completed(futures):
            results = future.result()  # with error handling
            self._process_results(futures[future], results)
```

### 1.3 No HTTP Connection Pooling

`kalshi_client.py` uses stdlib `urllib` with no connection reuse — every API call opens a new TCP connection + TLS handshake. `polymarket_client.py` uses `httpx` but creates fresh instances. Over a 5-minute cycle with ~20 API calls, connection overhead adds up to ~2 seconds of pure TLS negotiation.

**Recommendation:** Use persistent `httpx.Client` (or `urllib3.PoolManager`) sessions with keep-alive. Create one session per venue at startup, reuse across cycles. This is a 5-line change per client that saves ~100ms per API call.

### 1.4 O(n^2) Fuzzy Matching in matcher.py

`matcher.py` uses `difflib.SequenceMatcher` (O(n^2) per comparison) across every Kalshi market × every Odds API event. The sport-aware prefix filtering (Session 1 fix) reduced the market set from 2000 to ~50, but matcher still runs SequenceMatcher on every candidate pair.

**Recommendation:** Two-phase matching — first filter by shared tokens (O(n) set intersection), then fuzzy-score only candidates with >= 1 shared team token. This eliminates ~80% of fuzzy comparisons. For further gains, swap `difflib` for `rapidfuzz` (C-accelerated, 10–50x faster) which is a drop-in replacement.

---

## Category 2: Reliability & Data Integrity

### 2.1 No Atomic Trade Log Writes

Trade log entries are appended via `open("a")` + `write()` without `fsync()` or atomic rename. A crash mid-write can produce a truncated JSON line that corrupts all subsequent reads (every component that parses the log will fail on the malformed line).

**Recommendation:** Write to a temp file, then `os.replace()` (atomic on POSIX). Or at minimum, add `f.flush(); os.fsync(f.fileno())` after each write, and add a recovery mode that skips malformed lines (some scripts already handle this, but core components don't).

### 2.2 Silent Exception Swallowing (91 instances)

There are 91 `except Exception` blocks across the codebase. Many do `pass` or log at DEBUG level, silently hiding real failures. Critical examples:

- `base_pod.py` line 157: trade log write failure silently ignored — trades can be placed but never recorded
- `polymarket_client.py` line 134: auth failure falls back to read-only mode without warning
- `boost_scanner_pod.py` line 304: hedge placement failure swallowed — primary bet placed without hedge

**Recommendation:** Audit all 91 broad catches. For each: (a) narrow to specific exceptions, (b) ensure failures are logged at WARNING or ERROR, (c) for critical paths (trade log writes, order placement), fail loudly rather than silently.

### 2.3 Fragmented Log Schemas

Multiple field names for the same concept exist across the codebase:

- `market_id` vs `market_ticker` (5 files check both)
- `action` vs `outcome` (4 files)
- `kalshi_prob` vs `venue_prob` (3 files)
- `pod_id` sometimes missing on legacy entries

This means log readers need fallback chains, and new code must know about old field names. The problem compounds over time as more entries accumulate.

**Recommendation:** Create a `TradeLogSchema` class with `normalize(entry: dict) -> dict` that maps all legacy field names to canonical names. Run this at read time in the proposed `TradeStore`. Optionally, run a one-time migration script on the existing 62K entries.

### 2.4 P-006 Duplicate PLACED Entries

Per `PROJECT_STATUS.md` item #7, the Polymarket scanner sometimes places multiple PLACED entries for the same market across scan cycles. These resolve to VOID via ticker fallback, which inflates void counts and complicates reconciliation.

**Recommendation:** Add an `_open_positions` set to `PolymarketConsensusPod` (similar to scanner.py's `_open_game_keys()` but using the proposed TradeStore for O(1) lookups). Check before placing: if a PLACED entry exists for this condition_id without a settlement, skip.

---

## Category 3: Architecture & Maintainability

### 3.1 Hardcoded Pod Registry

`pod_runner.py` lines 55–63 hardcode a `POD_REGISTRY` dict mapping pod IDs to module paths. Adding a new pod requires editing this file. This is the kind of coupling that slows down development as the pod count grows.

**Recommendation:** Auto-discover pods via a decorator or entry point pattern:

```python
# In each pod file:
@register_pod("P-006", "Sportsbook-Polymarket Consensus")
class PolymarketConsensusPod(BasePod):
    ...

# In pod_runner.py:
from src.pods import POD_REGISTRY  # populated by decorators at import time
```

Or use config-driven registration: the `pods.active` list in YAML already names the pods — just need a consistent `from_config()` contract.

### 3.2 Bankroll Fallback Duplication

The pattern `bankroll = 10_000.0` / `try: bankroll = getattr(self.risk_manager.ledger, "bankroll", 10_000.0)` / `except: pass` appears in **6 separate pod files** (cross_venue_arb, polymarket_consensus, macro_nowcast, forecastex_kalshi_arb, plus aggregate_risk and capital_allocator). Each has slightly different error handling.

**Recommendation:** Add a `get_bankroll(default=10_000.0)` method to `BasePod` that encapsulates the fallback chain once. All pods call `self.get_bankroll()`.

### 3.3 Legacy System Tight Coupling

`main.py` imports 6 modules from the legacy `Kalshi Arb Project/src/` via PYTHONPATH manipulation. This creates invisible dependencies — if any legacy file changes its API, `main.py` breaks with no test coverage catching it.

**Recommendation:** Create a `legacy_adapter.py` that imports from legacy and exposes a stable interface. All new code imports from the adapter, never from legacy directly. This also makes it possible to eventually replace legacy modules one at a time.

### 3.4 1,200-Line web_dashboard.py with Inline HTML/JS

The entire dashboard — HTML template, CSS, JavaScript, and Python HTTP server — lives in a single 1,200-line file. The JS is minified inline, making it nearly impossible to debug or extend.

**Recommendation:** Extract the HTML/JS/CSS into a `templates/` directory and serve as static files. The Python HTTP handler loads them at startup. This enables: (a) browser dev tools work normally, (b) CSS/JS changes don't require Python restarts during development, (c) eventual migration to a proper framework if needed.

### 3.5 main.py God Object (~1,140 lines)

`main.py` handles CLI parsing, config loading, component wiring, the main loop, trade log reading, settlement resolution, dashboard state computation, and signal handling. It's the most-changed file and the hardest to test.

**Recommendation:** Extract into focused modules:

- `cli.py` — argument parsing and entry point
- `engine.py` — main loop, cycle orchestration, signal handling
- `config_loader.py` — YAML loading, merging, validation
- `trade_reader.py` — trade log parsing, resolution logic (or fold into `TradeStore`)

---

## Category 4: Operational & Security

### 4.1 Service Running as Root

`betting-pod-shop.service` runs as `User=root`. If the service is compromised (e.g., via a malicious API response that exploits a deserialization bug), the attacker has full system access.

**Recommendation:** Create a dedicated `bettingbot` user with minimal permissions. Update the service file:

```ini
[Service]
User=bettingbot
Group=bettingbot
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/betting-pod-shop/data
```

### 4.2 No Health Check or Watchdog

The systemd service only detects crashes (process exits). If the main loop hangs (e.g., a Kalshi API call blocks indefinitely), the service appears healthy but does nothing. The last 4 debug logs (9.2MB total) suggest cycles that took significantly longer than expected.

**Recommendation:** Add a watchdog pattern: write a timestamp to a file after each cycle. A separate systemd timer checks if the timestamp is stale (>15 minutes) and restarts the service. Or use systemd's built-in `WatchdogSec=` with `sd_notify()`.

### 4.3 No Log Rotation

Debug logs are growing unbounded (the 4 saved debug logs total 9.2MB from just a few sessions). `trade_log.jsonl` at 62K lines will keep growing. On a VPS with limited disk, this will eventually fill the filesystem.

**Recommendation:** Configure `journald` with `SystemMaxUse=500M` and add logrotate for any file-based logs. For `trade_log.jsonl`, implement a rotation strategy: archive entries older than 30 days to `trade_log_archive_YYYYMM.jsonl` and keep only recent entries in the active file.

### 4.4 Deploy Script Lacks Validation

`deploy.sh` runs `rsync` then optionally restarts the service with no verification that the deploy succeeded. A syntax error in a Python file would crash the service after restart with no automatic rollback.

**Recommendation:** Add a post-deploy health check:

```bash
ssh root@$SERVER "systemctl restart betting-pod-shop && sleep 5 && systemctl is-active betting-pod-shop"
if [ $? -ne 0 ]; then
    echo "DEPLOY FAILED — rolling back"
    ssh root@$SERVER "systemctl stop betting-pod-shop"
    # restore from backup
fi
```

### 4.5 Python Version Mismatch

`server_setup.sh` installs Python 3.12, but `betting-pod-shop.service` references Python 3.11. This suggests the VPS may be running a different version than the service file expects.

**Recommendation:** Use the virtual environment Python explicitly in the service file: `ExecStart=/opt/betting-pod-shop/venv/bin/python -m src.main ...`. This eliminates version ambiguity.

---

## Category 5: Strategic Code Quality

### 5.1 Magic Numbers Throughout

Hardcoded thresholds scattered across files:

| Value | Location | Purpose |
|-------|----------|---------|
| 55 | polymarket_matcher.py | Fuzzy match threshold |
| 85 | matcher.py (Legacy) | Fuzzy match threshold |
| 0.6 | settler.py:454 | Team name match threshold |
| 2 hours | settler.py:476 | Game time match window |
| 72 hours | settler.py | Stale position auto-void |
| 0.95/0.05 | polymarket_settler.py | Resolution price threshold |
| 20% | scanner.py:656 | Edge sanity cap |
| 10_000.0 | 6 files | Default bankroll |
| 0.02–0.08 | scanner.py:179 | Synthetic price offset range |

**Recommendation:** Centralize all thresholds into `config.yaml` with sensible defaults. Each module reads from config at construction time. This enables tuning without code changes and makes the system's assumptions explicit.

### 5.2 No Type Hints on Core Functions

Most functions lack type annotations, making it hard to understand expected inputs/outputs without reading implementation. The dataclasses (`ScanResult`, `EdgeResult`, etc.) are well-typed, but the functions that produce and consume them aren't.

**Recommendation:** Add type hints to all public methods, starting with the hot path: `scan_once()`, `match()`, `evaluate()`, `settle_cycle()`. Use `mypy --strict` in CI to catch type errors. This is a gradual effort — start with `base_pod.py` and `pod_runner.py` as they define the interfaces all pods must satisfy.

### 5.3 No Integration Tests for the Critical Path

750+ unit tests exist for legacy modules, and 24 test files exist for new modules. But there's no integration test that exercises the full scan cycle: fetch markets → match → evaluate edge → risk check → place → settle. Each component is tested in isolation, but the wiring between them (which lives in `main.py`) is untested.

**Recommendation:** Create `tests/test_integration.py` with a mock server that simulates Kalshi + Odds API responses, runs one full cycle, and asserts: correct number of trades placed, correct P&L after settlement, correct dashboard state. This catches the wiring bugs that unit tests miss.

### 5.4 Stale Code: Disabled Pods Still Loaded

P-009 and P-010 are listed in `config_multi_pod.yaml` as active but are noted in `PROJECT_STATUS.md` as "not live." Their code is loaded and instantiated on startup even when not producing trades, consuming memory and adding to startup time.

**Recommendation:** The `pods.active` list should be the single source of truth. If a pod is disabled, it should not be imported or instantiated. `pod_runner.py` should skip pods not in the active list rather than importing everything and filtering later.

### 5.5 Duplicate Settlement Logic

Settlement P&L calculation exists in three places: `settler.py` (Legacy), `polymarket_settler.py`, and `settlement_bridge.py`. Each has slightly different formulas and edge-case handling. The Polymarket settler uses `size * (1-p) / p` while the legacy settler uses a different formula accounting for Kalshi fees.

**Recommendation:** Extract a shared `pnl_calculator.py` module with venue-specific fee parameters:

```python
def calculate_pnl(side, cost, payout, fee_pct) -> float:
    gross = payout - cost
    fee = gross * fee_pct if gross > 0 else 0
    return gross - fee
```

Each settler calls this with their venue's fee structure.

---

## Priority Matrix

| # | Optimization | Impact | Effort | Priority |
|---|-------------|--------|--------|----------|
| 1.1 | TradeStore singleton | High (cycle time -2s, eliminates 95% I/O) | Medium (new module + rewire reads) | **P0** |
| 1.2 | Concurrent pod scanning | High (cycle time halved) | Low (10 lines in pod_runner) | **P0** |
| 2.1 | Atomic log writes | High (prevents data corruption) | Low (5 lines per writer) | **P0** |
| 4.1 | Non-root service user | High (security) | Low (service file + user creation) | **P1** |
| 1.3 | HTTP connection pooling | Medium (saves ~2s/cycle) | Low (session reuse) | **P1** |
| 2.2 | Exception audit (91 catches) | Medium (prevents silent failures) | Medium (per-instance review) | **P1** |
| 5.1 | Centralize magic numbers | Medium (enables tuning) | Low (config additions) | **P1** |
| 3.5 | Split main.py | Medium (maintainability) | Medium (refactoring) | **P2** |
| 3.2 | Bankroll helper in BasePod | Low (DRY) | Low (one method) | **P2** |
| 4.2 | Health check watchdog | Medium (reliability) | Low (systemd config) | **P2** |
| 4.3 | Log rotation | Medium (prevents disk full) | Low (config) | **P2** |
| 2.3 | Normalize log schemas | Medium (reduces bugs) | Medium (migration script) | **P2** |
| 3.1 | Auto-discover pods | Low (dev velocity) | Low (decorator pattern) | **P3** |
| 3.4 | Extract dashboard HTML/JS | Low (dev experience) | Medium (file separation) | **P3** |
| 5.3 | Integration tests | Medium (catches wiring bugs) | Medium (mock server setup) | **P2** |

---

## Quick Wins (< 30 Minutes Each)

1. **Add `f.flush(); os.fsync(f.fileno())` after trade log writes** — prevents corruption on crash
2. **Add `get_bankroll()` to `BasePod`** — eliminates 6 duplicated try/except blocks
3. **Set `User=bettingbot` in service file** — security hardening
4. **Add `SystemMaxUse=500M` to journald.conf** — prevents log disk fill
5. **Use venv Python in service `ExecStart`** — eliminates version mismatch
6. **Add `WatchdogSec=900` to service file** — detects hung processes

---

## Conclusion

The Betting Pod Shop has a strong foundation — the pod abstraction is clean, the safety gates are comprehensive, and the trade log provides full auditability. The optimizations above focus on three themes: making the system faster (TradeStore, concurrency, connection pooling), making it safer (atomic writes, exception handling, non-root service), and making it easier to extend (pod registry, config-driven thresholds, type hints). The P0 items would take roughly 2–3 focused sessions to implement and would materially improve cycle performance and data integrity.
