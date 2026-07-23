# Betting Pod Shop — Full Code Review

**Date:** March 31, 2026
**Scope:** All 50+ source files, 10 pods, 10 scripts, config, deploy
**Test Suite:** 1,027 tests — all passing

---

## Critical Issues (Fix Immediately)

### 1. Polymarket contract sizing is mathematically wrong
**File:** `src/multi_executor.py` ~line 321

```python
size_contracts = result.position_size_usd / result.venue_prob
```

This is incorrect. If buying YES at $0.40, you pay $0.40 per contract to win $1.00. To deploy `position_size_usd` dollars, you need `position_size_usd / venue_prob` contracts — but this gives you contracts, not USD exposure. The formula confuses "how many contracts can I buy" with "how much USD do I risk." For Polymarket, cost = `contracts × venue_prob`, so contracts = `position_size_usd / venue_prob` is actually the cost-based sizing. Verify this matches your Kelly output (which returns USD to risk, not contracts).

### 2. aggregate_risk.py: Positions never decrease
**File:** `src/aggregate_risk.py` ~line 174

`close_position()` updates `_open_positions` but does NOT reduce `_venue_exposure` or `_pod_exposure`. Comment says "simplified — real impl would track venue per position." This means venue/pod exposure limits ratchet up forever until restart, artificially blocking trades over time.

### 3. cli.py missing trade_store in _run_guarded_loop
**File:** `src/cli.py` ~line 297

The CLI entry point doesn't pass `trade_store` to `_run_guarded_loop()`, while `main.py` does. This means running via CLI skips trade store sync for settlements, causing dashboard/P&L to be stale.

### 4. health_check.py: Undefined variable on parse failure
**File:** `src/health_check.py` ~line 280-282

If `datetime.fromisoformat()` fails, `age_minutes` is never set, but the code uses it on the next line. Will crash with `NameError`.

### 5. nowcast_http.py: Python 3 dict_keys not indexable
**File:** `src/nowcast_http.py` ~line 137-142

`latest.keys()[1:]` doesn't work in Python 3 — `dict_keys` doesn't support slicing. Needs `list(latest.keys())[1:]`.

### 6. crypto_options_pricer.py: Array out-of-bounds
**File:** `src/crypto_options_pricer.py` ~line 468

Linear interpolation accesses `strikes_arr[idx + 1]` without checking that `idx < n - 1`. Will crash with IndexError when interpolating at the last strike.

---

## High Priority (Should Fix Soon)

### 7. aggregate_risk.py: emergency_halt() infinity trap
Sets `cooldown_minutes = float("inf")`, which means `check_pre_cycle()` will block forever (until process restart). No programmatic way to resume. Add a `resume()` method or use a boolean halt flag.

### 8. P-009 and P-010: Missing from_config() classmethod
Both `SignupBonusPod` and `BoostScannerPod` lack the `from_config()` factory method that all other pods implement. They can't be instantiated from `config_multi_pod.yaml` through the standard pod registry path.

### 9. settlement_bridge.py: Trade cache never invalidates
Once `_load_trades()` is called, it caches in memory forever. New PLACED entries added during execution are invisible to the settlement bridge until restart.

### 10. polymarket_settler.py: 1e-9 fallback produces nonsense P&L
**File:** `src/polymarket_settler.py` ~line 82

```python
pnl = size_usd * (1.0 - venue_prob) / max(venue_prob, 1e-9)
```

If `venue_prob` is 0 (or near-zero), the `1e-9` floor produces astronomical fake P&L values. Should reject trades with venue_prob < 0.01 entirely.

### 11. game_state.py: Unguarded int() on scores
**File:** `src/game_state.py` ~line 209-211

`int(score_val)` will crash with `ValueError` if the API returns non-numeric scores (e.g., "TBD", "", null). Needs try/except.

### 12. pnl_calculator.py: Independent rounding causes drift
**File:** `src/pnl_calculator.py` ~line 166-170

`gross_pnl`, `fees`, and `net_pnl` are rounded independently to 4 decimal places. This means `gross_pnl - fees != net_pnl` in some cases. Compute net_pnl from the rounded components instead.

### 13. promo_calculator.py: ZeroDivisionError if decimal_odds == 1.0
**File:** `src/promo_calculator.py` ~line 62

`-100.0 / (decimal_odds - 1.0)` crashes if decimal_odds is exactly 1.0. Guard needed.

---

## Medium Priority (Code Quality)

### 14. main.py / cli.py: Heavy duplication
Both files contain near-identical config loading, pod filtering, and run loop logic (~150 lines duplicated). Should extract into a shared `_bootstrap()` or `run_from_config()` function.

### 15. capital_allocator.py: Dead code block
**File:** `src/capital_allocator.py` ~line 185-190

Empty `if result.success and not result.error: pass` block does nothing. Remove or implement.

### 16. capital_allocator.py: Duplicate bootstrap methods
`bootstrap_from_trade_log()` and `bootstrap_from_store()` are near-identical. Consolidate.

### 17. trade_store.py: Double-locking performance issue
**File:** `src/trade_store.py` ~line 466-475

Acquires `_lock` once to copy placed entries, then acquires it again inside a loop for each entry. Should wrap the entire operation in one lock.

### 18. P-001: Unsafe /tmp trade log path
**File:** `src/pods/kalshi_moneyline.py` ~line 44

Defaults to `/tmp/p001_trade_log.jsonl` which won't survive reboots. Should use `data/pods/P-001.jsonl` like other pods.

### 19. Trade log path inconsistency across pods
No standard convention:
- P-001: `/tmp/p001_trade_log.jsonl`
- P-002: `data/trade_logs/trade_log.jsonl`
- P-004: `data/pods/P-004.jsonl`
- P-010: `data/pods/P-010.jsonl`
- P-014: `data/trade_logs/trade_log.jsonl`

Should standardize to `data/pods/{pod_id}.jsonl`.

### 20. cross_venue_matcher.py: 40% token overlap is too loose
**File:** `src/cross_venue_matcher.py` ~line 437-443

Settlement match requires only 40% token overlap. "Warriors vs Suns" and "Warriors vs Raptors" would have ~33% overlap — close to threshold. Could cause false positive settlements.

### 21. compat.py: check_deprecated_imports() never called
Defined but has no call site or test.

### 22. No requirements.txt or pyproject.toml
Project depends on httpx, pyyaml, and many others but has no dependency file. Deployment reproducibility at risk.

---

## Low Priority (Cleanup)

### 23. Stale one-shot scripts
These scripts are historical data repairs that no longer serve a purpose:
- `scripts/void_bad_p006_trades.py`
- `scripts/void_mismatched_p006.py`
- `scripts/fix_void_trades.py`
- `scripts/dedup_trades.py`

Move to `scripts/archive/` to reduce clutter.

### 24. void_mismatched_p006.py: Broken output
Line 111 prints event name but not the count. Report is useless.

### 25. seed_elo.py: Accesses private _dirty attribute
Line 317 sets `model._dirty = True` directly. Should use a public method.

### 26. P-014: Many hardcoded thresholds
`min_absolute_edge = 0.05`, `max_divergence = 0.20`, and several others are hardcoded instead of pulled from config. Makes tuning require code changes.

### 27. P-002: Hardcoded Kelly multiplier
`kelly_frac = min(arb.net_spread * 2.0, 0.10)` — the `2.0` multiplier is a magic number.

### 28. Inconsistent error logging
Some modules log full exception tracebacks (`exc_info=True`), others just log the message string. No consistent policy.

### 29. No retry/backoff on API clients
`polymarket_client.py`, `deribit_client.py`, `forecastex_client.py`, and `live_odds_poller.py` all fail immediately on transient HTTP errors. Should implement exponential backoff, at least for 429/503.

### 30. watchdog.py: Socket never closed
`_get_socket()` creates a socket stored in module-level global but never closes it.

### 31. config_multi_pod.yaml: P-014 environment confusion
Config says `environment: demo` but comment says "# paper mode". Clarify.

### 32. config_multi_pod.yaml: P-001 has `pass: true`
This is valid YAML but semantically meaningless. Clean up.

### 33. check_polymarket_settlements.py: market_id vs condition_id confusion
Main loop passes `market_id` where `condition_id` is expected. May cause failed lookups.

### 34. rotate_trade_logs.py: Not scheduled
Script exists with a suggested crontab line in the docstring, but no evidence it's actually scheduled on the VPS. Trade logs will grow unbounded.

---

## Architecture Observations

**What's working well:**
- Clean BasePod abstraction with consistent scan_once/execute pattern
- TradeStore singleton with proper locking (mostly)
- Pod auto-discovery via registry
- Comprehensive test coverage (1,027 tests, 100% pass rate)
- Good separation of concerns (matcher, settler, executor layers)
- Config-driven pod enable/disable

**What could be improved:**
- Consolidate main.py and cli.py into single entry point
- Standardize trade log paths across all pods
- Add retry/backoff layer for all external API clients
- Create requirements.txt for dependency management
- Implement proper cache invalidation in settlement_bridge and polymarket_settler
- Add input validation to financial calculations (pnl_calculator, promo_calculator)
- Archive one-shot repair scripts
