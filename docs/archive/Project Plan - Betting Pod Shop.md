# Betting Pod Shop — Cowork Project Plan (v2)

*Updated after deep source code audit of existing system*

---

## Your Starting Point — It's More Than a Scanner

After reading all 15 test files (~3,500 lines of tests covering 750+ test cases), your existing system is a **complete, production-quality betting engine**, not just a scanner. Here's what's already built and running:

**Core Pipeline (fully implemented):**
- `odds_client` — The Odds API connector with TTL caching and multi-sport support
- `matcher` — Fuzzy matching engine (token sort + partial match) that maps Kalshi markets to Odds API events, with time-window validation and moneyline filtering
- `edge_calculator` — Full odds math: vig removal (additive + power methods), consensus probability from multiple bookmakers (Pinnacle-weighted), edge/EV/Kelly computation with fee adjustment
- `scanner` — Orchestrator: scans Kalshi markets, matches to Odds API, evaluates edge, applies risk checks, writes trade log with fingerprint-based dedup and open-position guards
- `executor` — Paper and live execution with dual safety gates, limit order building, and ledger integration
- `risk_manager` — Position limits, exposure caps, daily loss guard (5% → 60-min cooldown), per-trade risk approval, full ledger with open/close lifecycle
- `settler` — Settlement engine tracking WIN/LOSS/VOID outcomes with P&L calculation and ledger sync

**Advanced Modules (also implemented):**
- `learner` — Bayesian/rolling calibration with logistic regression, global bias detection, and sport-specific adjustments
- `backtester` — Full backtesting with equity curves, Sharpe ratio, drawdown analysis
- `dashboard` — Terminal dashboard with CSV/Excel exports
- `team_resolver` — NBA 30-team database with abbreviations, nicknames, conference mapping
- `fatigue_analyzer` — Back-to-back detection (28h threshold), rest differential, fatigue edge multiplier
- `consistency_checker` — Cross-tier pricing monotonicity validation

**Infrastructure:**
- JSON structured logging, YAML config, JSON Schema validation, idempotent fingerprinting

---

## What's Reusable vs. What Needs Work

### Ready to reuse across all pods (no changes needed)
- `edge_calculator` + `utils` — odds math, vig removal, Kelly sizing
- `risk_manager` + `Ledger` — position/exposure management, daily loss guard
- `settler` — outcome tracking and P&L
- `backtester` — performance analysis
- `dashboard` — reporting and exports
- Config system, logging, schemas

### Needs generalization for multi-pod
- **`Scanner`** → Currently a monolithic orchestrator. Needs to become a "pod runner" that loads different strategy implementations. The `scan_once()` pattern is perfect — each pod just needs its own version.
- **`Matcher`** → Kalshi-specific fuzzy matching. For pods involving Polymarket or other venues, we need matching logic per venue pair.
- **`kalshi_client`** → Great pattern for a venue connector. Polymarket, sportsbook APIs, etc. each need their own client following the same interface pattern.
- **`odds_client`** → Currently The Odds API only. Other fair-value data sources (polls, models, other markets) need similar connectors.

### Current limitations to expand
1. Moneyline only — no spread, total, or prop markets
2. Kalshi-only execution — no Polymarket or sportsbook execution
3. Sports-focused — no political, climate, or financial event markets
4. Single-strategy — one scanning algorithm, no pod abstraction
5. NBA-specific extras — fatigue/team resolver are NBA-only

---

## The Revised Plan

Now that the infrastructure audit is done (Session 1 from the old plan is essentially complete), we can jump straight into research and building. The sessions below are renumbered accordingly.

### Phase 1: Strategy Library Research (Sessions 1–4)

**Session 1 — Venue Landscape Research & Spreadsheet Setup**

Now that I know your codebase, this session maps the *opportunity space* across your venue accounts.

- Create the master Strategy Library spreadsheet (17 columns from the original prompt framework)
- Enter your existing Kalshi Moneyline Value scanner as Pod #1 with full details
- Deep research: Kalshi API capabilities beyond moneyline (spreads, props, political, crypto, climate contracts)
- Deep research: Polymarket Sports API — endpoints, fee structure, market types, liquidity patterns
- Deep research: FanDuel/DraftKings API availability for programmatic access (or scraping limitations)
- Research Interactive Brokers event contract offerings and API
- Document each venue: API docs, fees, rate limits, market types, execution method

**Session 2 — Cross-Venue Discrepancy Pods**

- Research Kalshi-vs-Polymarket price discrepancies on identical or near-identical contracts
- Research sportsbook-vs-Polymarket discrepancies (your existing scanner logic, new execution venue)
- Research stale-line capture opportunities (which venue updates slower on breaking news?)
- Research cross-venue synthetic positions (e.g., buy YES on Kalshi, sell equivalent on Polymarket for locked profit)
- Investigate IB options/equity plays correlated with prediction markets (event-driven vol arb)
- Populate 6–8 discrepancy pods in the spreadsheet with real API endpoints, fee structures, capacity

**Session 3 — Incentive/Promo & Fair-Odds Pods**

- Research FanDuel/DraftKings promo structures (sign-up bonuses, boosts, insured bets, profit boosts)
- Research promo-conversion strategies (hedge promo exposure across Kalshi/Polymarket)
- Research Kalshi and Polymarket maker/taker fee dynamics
- Research simple fair-odds models: multi-book consensus (you already have this), polling aggregation for political markets, implied probability calibration
- Research public data sources beyond The Odds API
- Populate 4–6 incentive pods and 4–6 fair-odds pods

**Session 4 — Editor Pass: Score, Spec, Prioritize**

- Apply the composite scoring rubric to all pods (Build Speed 25%, EV Clarity 20%, Robustness 20%, Capacity 15%, Ops Complexity 10%, Diversification 10%)
- Adjust scoring for your specific situation: pods that reuse your existing infra score higher on Build Speed, pods requiring accounts you don't have score lower
- Produce detailed Pod Spec Sheets for the top 6 pods
- Produce the Shared Infrastructure Requirements list (accounting for what already exists)
- Produce the Research Backlog: top 20 unknowns
- **You review and we refine**

---

### Phase 2: Architecture & Feasibility (Sessions 5–6)

**Session 5 — Pod Runner Architecture Design**

Your existing codebase is well-structured, so this is about *extending* not *rewriting*.

- Design the `BasePod` interface that each strategy implements (replacing the monolithic Scanner)
- Design venue connector interfaces (abstracting `kalshi_client` pattern for Polymarket, sportsbooks)
- Design the pod runner/orchestrator that loads and runs multiple pods
- Map each top-6 pod to: which existing modules it reuses, which new components it needs
- Produce the architecture document with component diagrams

**Session 6 — Per-Pod Feasibility & Build Sequencing**

- For each top 6 pod: assess data availability, API access, engineering effort, maintenance burden
- Identify hard blockers (API doesn't exist, venue blocks automation, data unavailable)
- Rank by feasibility-adjusted score + infrastructure overlap
- Produce the build roadmap: which 3 pods to build first and why
- Define MVP criteria for each pod
- Flag any accounts you need to open (FanDuel/DraftKings) and start early

---

### Phase 3: Infrastructure Extension (Session 7)

**Session 7 — Build the Pod Framework + First New Venue Connector**

This is the highest-leverage build session.

- Implement the `BasePod` abstract class and pod runner
- Wrap your existing Scanner as `KalshiMoneylinePod` (proving the pattern works without breaking anything)
- Build the first new venue connector (likely Polymarket, since you have an account)
- Build a shared event identity mapping layer (so pods across venues can reference the same underlying events)
- Verify everything still passes your existing 750+ tests

---

### Phase 4: Pod Implementation (Sessions 8+)

**Sessions 8+ — Build New Pods**

Each pod follows the same build pattern:
1. Data connector for the pod's signal sources
2. Matching logic for the pod's venue pair
3. Edge evaluation using existing `edge_calculator` (or pod-specific variant)
4. Risk check integration using existing `risk_manager`
5. Execution stub (paper mode first, using venue connector)
6. Settlement integration using existing `settler`
7. Backtesting validation
8. Paper trading period

Estimate 1–2 sessions per pod. Likely build order (to be confirmed in Session 6):
- **Pod 2:** Kalshi-vs-Polymarket discrepancy scanner (closest to existing system)
- **Pod 3:** Kalshi political/non-sports markets (expands market types, same execution venue)
- **Pod 4:** Sportsbook promo conversion (if FanDuel/DraftKings accounts acquired)
- **Pod 5:** Fair-odds model pod (independent pricing model, not just book consensus)
- **Pod 6:** Multi-venue arb aggregator

---

### Phase 5: Unified Engine (Later Sessions)

**Capital Allocator & Portfolio Risk**

- Build portfolio-level capital allocation across active pods
- Implement aggregate risk controls: total exposure limits, correlation monitoring, drawdown halts
- Build the "CIO" layer: dynamic rebalancing, pod performance tracking, capital rotation
- Extend dashboard for multi-pod monitoring

---

## Deliverables Summary

| Phase | Sessions | Key Deliverables | Format |
|-------|----------|-----------------|--------|
| 1 | 1–4 | Strategy Library (15–20 pods), Top 6 Spec Sheets, Research Backlog | .xlsx + .md |
| 2 | 5–6 | Architecture doc, feasibility assessment, build roadmap | .md |
| 3 | 7 | BasePod framework, KalshiMoneylinePod wrapper, Polymarket connector | .py |
| 4 | 8+ | Working code for each new pod (1–2 sessions each) | .py |
| 5 | Later | Capital allocator, portfolio risk, multi-pod dashboard | .py |

---

## Recommended Next Step

**Session 1: Venue Landscape Research & Spreadsheet Setup**

Since the system audit is now done, we should jump straight into mapping the opportunity space. I'll research each of your venues' API capabilities, create the master spreadsheet, and enter your existing scanner as Pod #1.

Want to kick off Session 1?
