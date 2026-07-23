# Session 7 — Pod Implementation & Infrastructure Extension

## Summary

Built the first complete pod (P-006 Sportsbook-Polymarket Consensus), the cross-venue matcher for Kalshi-Polymarket arbitrage, and the IB ForecastEx connector. Extended the config system for multi-pod operation. All 150 tests pass (96 Session 6 + 54 Session 7), zero failures. Zero existing files modified.

## New Files Created

### Source Modules (3 files)

| File | Lines | Purpose |
|------|-------|---------|
| `src/pods/polymarket_consensus.py` | 280 | P-006: Full scan cycle — fetch Polymarket markets, match to Odds API consensus, evaluate edge (0% fees), risk check, dedup, place order |
| `src/cross_venue_matcher.py` | 145 | Two-pass Kalshi-Polymarket matching: fuzzy title match + settlement source verification |
| `src/forecastex_client.py` | 210 | IB ForecastEx connector: BUY-only, paper mode default, injectable IB instance for testing |

### Test Files (3 files, 54 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_polymarket_consensus_pod.py` | 26 | Happy path PLACED, SKIPPED_EDGE, empty markets/odds, no consensus/midpoint, per-cycle dedup, fingerprint dedup, multi-sport scanning, `_infer_yes_team`, from_config |
| `tests/test_cross_venue_matcher.py` | 14 | Same-event matching, threshold filtering, price extraction, settlement alignment check, from_config |
| `tests/test_forecastex_client.py` | 14 | Paper mode (no IB needed), get_price midpoint/NaN/no-IB, connect/disconnect flags, live mode filled/rejected/no-IB, BUY-only constraint, from_config |

### Config Extension (1 file)

| File | Purpose |
|------|---------|
| `config_multi_pod.yaml` | Extended config: `polymarket`, `interactive_brokers`, `cross_venue_matcher` sections, pod definitions (P-001, P-002, P-004, P-006, P-012), log paths |

## Key Design Decisions

1. **P-006 mirrors P-001 logic with Polymarket specifics** — Same scan cycle pattern (fetch → match → edge → risk → dedup → place) but with 0% fee assumption, question-style matching via `PolymarketMatcher`, and `_infer_yes_team` to map "Will [team] win?" questions to home/away probabilities.

2. **`_infer_yes_team` heuristic** — Parses Polymarket question text to determine which team YES corresponds to. Checks "Will [team] win/beat" regex patterns, then falls back to team name presence. Defaults to home if ambiguous.

3. **`_fetch_sport_markets` keyword filtering** — Polymarket CLOB API has no sport tags, so we paginate and filter by sport-specific keywords (team names, league abbreviations). Safety-limited to 5 pages per sport.

4. **CrossVenueMatcher two-pass design** — First pass: fuzzy title matching picks the best Polymarket match for each Kalshi market above threshold. Second pass: settlement alignment check (token overlap heuristic, >40% of smaller token set). Mismatches are flagged but not auto-rejected — the consuming pod decides.

5. **ForecastExClient BUY-only constraint** — ForecastEx only supports BUY orders (buy opposing contract to exit). Uses `secType="OPT"`, `exchange="FORECASTEX"`, `right="CALL"` for YES and `"PUT"` for NO. Paper mode simulates without IB connection.

6. **Import fallback pattern in ForecastExClient** — `_make_contract` and `_make_limit_order` try to import from `ib_insync` first, falling back to lightweight dataclass substitutes. This lets the full test suite run without `ib_insync` installed.

## Test Results

```
Ran 150 tests in 0.025s — OK
```

Breakdown: 23 (base_pod) + 12 (kalshi_moneyline) + 21 (polymarket_client) + 22 (polymarket_matcher) + 18 (multi_executor) + 26 (polymarket_consensus) + 14 (cross_venue_matcher) + 14 (forecastex_client) = 150

## Cumulative Project State

| Metric | Session 6 | Session 7 |
|--------|-----------|-----------|
| Source modules | 7 | 10 |
| Test files | 5 | 8 |
| Total tests | 96 | 150 |
| Pods implemented | 1 (P-001 wrapper) | 2 (P-001, P-006) |
| Venue connectors | 2 (Kalshi, Polymarket) | 3 (Kalshi, Polymarket, ForecastEx) |
| Config files | 1 (config.yaml) | 2 (+ config_multi_pod.yaml) |

## What's Next (Session 8+)

Per the build roadmap, the next priorities are:

- **P-004 ForecastExKalshiArbPod** — Uses the new `ForecastExClient` + existing `KalshiClient` to scan for economic event arbitrage (Fed rate, CPI, GDP). Both venues are live-capable now.
- **P-002 CrossVenueArbPod** — Uses `CrossVenueMatcher` to find Kalshi-Polymarket spread opportunities. Infrastructure is ready; needs pod scan logic.
- **T1 promo pods (P-009, P-010)** — Sign-up bonus converter and daily odds boost scanner. Immediate revenue, no API dependency.
- **Integration wiring** — Connect `MultiExecutor` + all pods into main run loop with unified scheduling.
