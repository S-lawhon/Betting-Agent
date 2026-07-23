# Session 4 — Editor Pass & Master Build Roadmap

*Completed February 22, 2026*

---

## Editor Pass: Score Adjustments

After reviewing all research findings against initial pod scores, four adjustments were made:

| Pod | Change | Rationale |
|-----|--------|-----------|
| P-003 Crypto Flash | Robustness 3→2 | Research showed 40+ open-source arb bots, "easy money captured by 2026," need sub-5ms to compete, Polymarket 10% taker on crypto |
| P-005 ForecastEx Carry | Capacity 2→1 | Capital locked 6+ months for 26-35 bps edge; ForecastEx liquidity too thin for meaningful deployment |
| P-012 Macro Nowcast | Status RESEARCH→PLANNED | All data sources free (Cleveland Fed, Atlanta Fed, FRED), model is straightforward weighted ensemble |
| P-014 Market Maker | Robustness 4→3 | Depends entirely on other pods for fair values; adverse selection is primary failure mode for retail market makers |

---

## Final Build Priority (all 15 pods)

### Tier 1 — Quick Wins (Weeks 1-3)

High ROI, fastest to build, minimal new infrastructure.

| Order | Pod | Score | Edge | Why First |
|-------|-----|-------|------|-----------|
| 1 | P-009 Sign-Up Bonus Blitz | 4.10 | 7000-8000 bps | Highest raw edge. 3 days to set up account tracker + conversion workflow. Generates immediate cash ($2K+ month 1) to fund other pods. No code build needed — just tooling. |
| 2 | P-006 Sportsbook-Polymarket Consensus | 4.30 | 200-400 bps | Your existing P-001 scanner with Polymarket as execution venue instead of Kalshi. 0% fees means lower edge threshold. Unlocks the **Polymarket CLOB Client** (shared infrastructure for 5+ future pods). ~1 week build. |
| 3 | P-010 Daily Odds Boost Grind | 3.55 | 200-500 bps | Repeatable $500-900/month per account. Boost scanner + EV calculator. Runs alongside P-009 during priming period. ~4 days build. |

**Tier 1 total effort**: ~3 weeks. **Revenue potential**: $3K-6K/month from promos + first automated Polymarket scanning.

### Tier 2 — Core Extensions (Weeks 4-8)

Strong edge, moderate build. Each component unlocks future pods.

| Order | Pod | Score | Edge | Why Next |
|-------|-----|-------|------|----------|
| 4 | P-002 Kalshi-Poly Cross-Venue Scanner | 4.30 | 150-300 bps | Proven $40M edge. Requires Event Matcher + Dual-Leg Executor (both shared infra). Uses Polymarket Client from T1. |
| 5 | P-004 ForecastEx-Kalshi Econ Arb | 3.45 | 50-150 bps | Unlocks **IB ForecastEx Connector** (shared by P-005, P-008). Diversifies into non-sports. 0% commission + 3.14% APY. |
| 6 | P-012 Macro Economic Nowcast | 3.50 | 300-800 bps | All data free (Fed nowcasts). Kalshi beat Bloomberg 11/13 months on CPI. First pod building its own fair-value model. |

**Tier 2 also builds**: BasePod Framework (refactor scanner.py into abstract base class). All future pods implement the same `scan_once()` interface.

**Tier 2 total effort**: ~5 weeks. **Revenue potential**: Cross-venue arb + economic event trades adding $1K-5K/month.

### Tier 3 — Advanced (Weeks 9-14)

Promising strategies requiring research validation or more complex infrastructure.

| Order | Pod | Score | Edge | Notes |
|-------|-----|-------|------|-------|
| 7 | P-003 Crypto 15-Min Flash Arb | 3.20 | 80-150 bps | High competition. Needs Binance WSS + sub-500ms latency. Consider after T2 infrastructure proves stable. |
| 8 | P-011 Political Fair-Value Model | 3.10 | 200-500 bps | Best during election cycles. Bayesian ensemble of polls + forecasters + base rates. Seasonal. |
| 9 | P-014 Prediction Market Maker | 2.80 | 100-250 bps | Meta-pod using other pods' fair values. Only viable once P-006/P-012 are generating reliable fair-value signals. |

### Tier 4 — Backlog (Months 4+)

Complex, speculative, or limited capacity. Build only after Tiers 1-3 are generating revenue.

| Order | Pod | Score | Edge | Notes |
|-------|-----|-------|------|-------|
| 10 | P-005 ForecastEx Yield Carry | 2.80 | 26-35 bps | Very thin edge, capital locked months. Only worth it at scale ($500K+) after IB connector proven. |
| 11 | P-007 Econ Data Release Stale-Line | 2.70 | 50-150 bps | Limited to ~10 events/month. Needs sub-second execution competing against HFT. |
| 12 | P-008 Options IV-to-Probability | 2.65 | 200-600 bps | Richest theoretical edge but hardest build (SABR calibration, delta hedging). 8-12 weeks. |
| 13 | P-013 Crypto Derivatives Fair-Value | 2.75 | 300-600 bps | Shares SABR infrastructure with P-008. Needs offshore Deribit account. |
| 14 | P-015 LLM Event Forecaster | 2.80 | 200-500 bps | Cross-category but unproven at scale. $1-5K/mo API cost. Revisit Q4 2026 when LLM forecasting matures. |

---

## Shared Infrastructure Map

The 18 infrastructure components group into a dependency tree. Building them in order means each tier's work unlocks the next.

```
EXISTING (P-001 Foundation)
├── edge_calculator ──────────→ ALL pods
├── risk_manager ─────────────→ ALL pods
├── settler ──────────────────→ ALL pods
├── matcher ──────────────────→ P-001, P-006 (adapt), P-002 (extend)
├── kalshi_client ────────────→ P-001, P-002, P-004, P-007
├── odds_client ──────────────→ P-001, P-006
├── scanner (→ BasePod) ──────→ ALL new pods (refactored)
└── backtester, dashboard, learner → ALL pods

TIER 1 NEW (3 weeks)
├── Account Tracker ──────────→ P-009
├── Polymarket CLOB Client ───→ P-006, P-002, P-003, P-014, P-015
├── Polymarket Matcher ───────→ P-006, P-002
└── Boost Scanner ────────────→ P-010

TIER 2 NEW (5 weeks)
├── Event Matcher (Cross-Venue) → P-002, P-004
├── Dual-Leg Executor ────────→ P-002, P-003
├── IB ForecastEx Connector ──→ P-004, P-005, P-008
├── Nowcast Pipeline ─────────→ P-012
└── BasePod Framework ────────→ ALL new pods

TIER 3 NEW (6 weeks)
├── Binance WSS Monitor ──────→ P-003
├── Poll Aggregator Pipeline ─→ P-011
└── Quote Engine + Inventory ─→ P-014

TIER 4 NEW (6+ weeks)
├── SABR Calibrator ──────────→ P-008, P-013
├── LLM Forecasting Pipeline ─→ P-015
├── Carry Accrual Tracker ────→ P-005
└── Econ Release Parser ──────→ P-007
```

**Key insight**: The **Polymarket CLOB Client** is the single most leveraged infrastructure build — it unlocks 5 pods. Building it in Tier 1 alongside P-006 creates maximum future optionality.

---

## Critical Path: First 8 Weeks

```
Week 1:   P-009 setup (account tracker, first bonus conversions)
          Begin Polymarket CLOB Client build

Week 2:   Complete Polymarket Client + Polymarket Matcher
          P-006 live (paper mode) — your scanner against Polymarket

Week 3:   P-010 boost scanner integration
          P-006 validation + tuning

Week 4:   Begin BasePod Framework refactor (scanner.py → abstract base)
          Begin Event Matcher (cross-venue)

Week 5:   Begin Dual-Leg Executor
          P-002 initial build (Kalshi-Polymarket scanner)

Week 6:   P-002 testing + paper mode
          Begin IB ForecastEx Connector

Week 7:   P-004 build (ForecastEx-Kalshi econ arb)
          Begin Nowcast Pipeline (FRED, Cleveland Fed, Atlanta Fed)

Week 8:   P-012 build (Macro Economic Nowcast)
          Review: assess live results from P-006, P-009, P-010
          Decision gate: commit to Tier 3 priorities or iterate on T1/T2
```

---

## Deliverables Updated

- **Strategy Library spreadsheet**: 3 new columns (Build Tier, Build Order, Shared Infra Deps)
- **Build Roadmap sheet**: 18 infrastructure components with tier, effort, dependencies
- **Score adjustments**: P-003 (3.40→3.20), P-005 (2.95→2.80), P-014 (3.00→2.80), P-012 (RESEARCH→PLANNED)

---

## What's Next

Sessions 1-4 (Research Phase) are now complete. The project plan calls for:

- **Session 5**: Architecture & feasibility — design the BasePod abstract class, define the pod interface, plan the Polymarket client architecture
- **Session 6**: Detailed technical design — data models, API integration specs, testing strategy
- **Session 7**: Framework build — implement BasePod, Polymarket Client, refactor P-001 into KalshiMoneylinePod
- **Session 8+**: Pod implementation — build Tier 1 pods (P-009, P-006, P-010), then Tier 2
