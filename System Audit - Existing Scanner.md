# System Audit: Existing Betting Agent Codebase

## Overview

The existing codebase is significantly more developed than the README's "Phase 0 — repo skeleton" label suggests. The test suite (750+ tests across 15 test files) reveals a comprehensive system with most core modules implemented or at least test-specified.

**Important note:** Only compiled `.pyc` files and the README were uploaded — not the `.py` source code. This audit is based on reverse-engineering the test structure, class/method names, and embedded string constants from the bytecode. To do deeper work (refactoring, extending, debugging), we'll need the actual source files.

---

## Architecture Summary

### Core Modules (13 identified from test files)

| Module | Purpose | Test Coverage | Maturity |
|--------|---------|---------------|----------|
| `scanner.py` | Main scan loop — finds +EV opportunities | 88 tests | High |
| `edge_calculator.py` | Odds conversions, vig removal, consensus prob, edge/EV/Kelly | 73 tests | High |
| `matcher.py` | Fuzzy-matches Kalshi markets to Odds API events | 62 tests | High |
| `kalshi_client.py` | Kalshi API wrapper (auth, rate limiting, retry) | 70 tests | High |
| `odds_client.py` | The Odds API client with TTL caching | 54 tests | High |
| `executor.py` | Order execution (paper + live modes) | 49 tests | High |
| `risk_manager.py` | Position limits, exposure caps, daily loss guard, ledger | 79 tests | High |
| `settler.py` | Settlement engine, P&L tracking | 40 tests | High |
| `learner.py` | Bayesian/rolling learner (logistic regression, sport bias) | 69 tests | High |
| `backtester.py` | Backtesting with equity curves, Sharpe, drawdown | 83 tests | High |
| `dashboard.py` | Terminal dashboard + CSV/Excel exports | 84 tests | High |
| `team_resolver.py` | NBA team name resolution (30 teams, abbreviations, nicknames) | 38 tests | Moderate |
| `fatigue_analyzer.py` | Back-to-back detection, rest differential, fatigue scoring | 34 tests | Moderate |
| `consistency_checker.py` | Tier monotonicity (championship > conference > playoff pricing) | 37 tests | Moderate |

### Supporting Infrastructure

| Component | Status | Details |
|-----------|--------|---------|
| Structured logging | Built | JSON logging via structlog |
| Configuration | Built | `config.yaml` for all tunables, `.env` for secrets |
| JSON Schema validation | Built | Schemas for trade_log, unmatched_markets, edge_history |
| Idempotency | Built | Fingerprint-based dedup (SHA-256, per-hour) |
| Safety gates | Built | Live trading requires dual gate (config + env var) |

---

## Data Flow (Reconstructed)

```
The Odds API ──→ odds_client (cached) ──→ consensus probability
                                              │
Kalshi API ──→ kalshi_client ──→ markets      │
                    │                         │
                    ▼                         ▼
                matcher (fuzzy match Kalshi ↔ Odds API)
                    │
                    ▼
              edge_calculator (edge, EV, Kelly)
                    │
                    ├──→ fatigue_analyzer (adjust for back-to-back, rest)
                    ├──→ consistency_checker (tier monotonicity signals)
                    ├──→ learner (Bayesian calibration adjustment)
                    │
                    ▼
              risk_manager (position limits, exposure, daily loss)
                    │
                    ▼
                scanner (orchestrates full scan cycle)
                    │
                    ▼
                executor (paper or live order placement)
                    │
                    ▼
                settler (tracks outcomes, computes P&L)
                    │
                    ▼
              dashboard + backtester (reporting + analysis)
```

---

## Key Design Decisions (from test evidence)

### Edge Calculation
- **Vig removal:** Both additive and power methods implemented
- **Consensus probability:** Weighted average across bookmakers (Pinnacle weighted heavily), plus median
- **Edge threshold:** 3% minimum (configurable)
- **Kelly sizing:** Quarter-Kelly (0.25 fraction, configurable)
- **Fee adjustment:** EV and Kelly both account for Kalshi fees

### Matching
- **Fuzzy matching:** Token sort + partial match scoring for Kalshi ↔ Odds API event names
- **Time window:** Events must match within a time threshold
- **Market type filtering:** Only moneyline (rejects spread, total, prop, futures, handicap)
- **Unmatched logging:** Failed matches logged with best candidate for debugging

### Execution
- **Paper mode:** Simulated fills at mid-price, no actual orders
- **Live mode:** Limit orders via Kalshi API, with dual safety gate
- **Order building:** Price in cents (1-99), contracts = floor(size/cost), min 1
- **Dedup:** Fingerprint prevents same-hour re-entry; open-position guard prevents re-entry on unsettled games

### Risk Management
- **Position limits:** Max open positions cap
- **Exposure limits:** Max exposure as % of bankroll
- **Daily loss guard:** 5% daily loss triggers 60-min cooldown
- **Per-trade checks:** Min confidence threshold, pre-trade risk approval
- **Ledger:** Full position tracking with open/close lifecycle

### Learning & Adaptation
- **Rolling window:** Evicts oldest observations
- **Logistic regression:** Fits calibration model on fair_prob vs. outcomes
- **Global bias:** Detects systematic over/under-estimation
- **Sport-specific bias:** Per-sport calibration adjustment
- **Probability adjustment:** Capped at max_adj, clamped to [0.01, 0.99]

### Settlement
- **Outcome types:** WIN, LOSS, VOID
- **P&L calculation:** Side-aware (YES/NO), rounded to 2 decimals
- **Ledger sync:** Settlement closes ledger positions and updates bankroll
- **Rebuild:** Can reconstruct ledger from trade log on restart

### Sports-Specific Features
- **NBA team resolver:** Full 30-team database with abbreviations, nicknames, East/West conference mapping, ticker parsing
- **Fatigue analyzer:** Back-to-back detection (28h threshold), games-in-window counting, rest differential, edge multiplier
- **Consistency checker:** Cross-tier pricing validation (championship > conference > playoff implied probabilities)

---

## What This System Already Is (Pod #1)

This is a **sportsbook-vs-Kalshi cross-venue discrepancy scanner** with:
- Fair value derived from multi-bookmaker consensus (via The Odds API)
- Execution on Kalshi
- Sports focus: NBA confirmed, likely other sports via The Odds API sports parameter
- Moneyline markets only (no spreads, totals, or props)
- Paper and live trading modes
- Full position lifecycle (open → monitor → settle)
- Backtesting capability
- Learning/calibration layer

### What's Working Well
- Comprehensive test suite (750+ tests) — this is production-quality testing
- Clean separation of concerns (each module has a clear responsibility)
- Safety-first design (dual gates, fingerprint dedup, position guards, daily loss limits)
- Configurable via YAML (easy to tune without code changes)

### Current Limitations / Extension Opportunities
1. **Moneyline only** — no spread, total, or prop markets
2. **Kalshi-only execution** — no connectors to other venues (Polymarket, sportsbooks)
3. **Sports-focused** — no political, climate, or financial event markets
4. **Single-strategy** — one scanning algorithm, no pod abstraction
5. **NBA-specific extras** — fatigue analyzer and team resolver are NBA-only
6. **No Polymarket integration** — your Polymarket Sports account isn't connected
7. **No cross-venue arb** — scans Kalshi pricing vs. fair value, doesn't scan for true arb across multiple execution venues

---

## What This Means for the Pod Shop Project

### Reusable Infrastructure (can serve all pods)
- Edge calculator (odds math, vig removal, Kelly) — **fully reusable**
- Risk manager + ledger — **fully reusable** with minor generalization
- Logging + schemas — **fully reusable**
- Backtester — **reusable** with adapter for different pod data formats
- Dashboard + exports — **reusable** with pod-aware filtering
- Config system — **reusable**, just add per-pod sections

### Needs Generalization
- Scanner — currently monolithic; needs refactoring into a "pod runner" that can load different strategies
- Matcher — currently Kalshi-specific; needs venue-agnostic matching interface
- Kalshi client — good pattern to follow for other venue connectors (Polymarket, sportsbooks)
- Odds client — good pattern; may need additional data source connectors
- Team resolver — NBA-only; needs sport-agnostic approach or per-sport resolvers
- Executor — Kalshi-only; needs venue-agnostic execution interface

### Can Be a Pod Directly
Your current scanner becomes **Pod: Kalshi Moneyline Value** — the first pod in the library, already (mostly) built.

---

## Recommended Next Steps

1. **Share the .py source files** — the .pyc files let me map the architecture, but actual source code is needed to refactor, extend, or debug
2. **Assess current state** — which of the 11 README phases are actually complete? The test suite suggests most are built, but the README says Phase 0
3. **Begin Phase 1 research** — now that we know what infrastructure exists, the strategy library research can be much more targeted (we know what's easy to add vs. what requires new plumbing)
