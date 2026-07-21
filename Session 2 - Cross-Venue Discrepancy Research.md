# Session 2 — Cross-Venue Discrepancy Pod Research

*Research conducted February 22, 2026*

---

## Summary

Session 2 researched and populated 7 new cross-venue discrepancy pods (P-002 through P-008) in the Strategy Library spreadsheet. All pods target price differences across your venue accounts (Kalshi, Polymarket, IB ForecastEx) using different edge mechanisms.

### Pods Added (ranked by Composite Score)

| Pod | Name | Score | Edge (bps) | Status |
|-----|------|-------|------------|--------|
| P-002 | Kalshi-Polymarket Cross-Venue Scanner | 4.30 | 150-300 | PLANNED |
| P-006 | Sportsbook-Polymarket Consensus | 4.30 | 200-400 | PLANNED |
| P-004 | ForecastEx-Kalshi Econ Arb | 3.45 | 50-150 | PLANNED |
| P-003 | Crypto 15-Min Flash Arb | 3.40 | 80-150 | PLANNED |
| P-005 | ForecastEx Yield Carry | 2.95 | 26-35 | PLANNED |
| P-007 | Econ Data Release Stale-Line | 2.70 | 50-150 | PLANNED |
| P-008 | Options IV-to-Probability Rel Value | 2.65 | 200-600 | RESEARCH |

---

## Key Research Findings

### 1. Kalshi-vs-Polymarket Arbitrage (P-002, P-006)

The two highest-scoring new pods both exploit Kalshi-Polymarket discrepancies. This is the most proven edge: $40M in documented arb bot profits between April 2024 and April 2025. The top single performer made $2.01M across 4,049 transactions.

**Why it works for you**: Your existing scanner infrastructure (edge_calculator, risk_manager, settler, matcher) translates almost directly. P-006 (Sportsbook-Polymarket Consensus) is essentially your existing P-001 scanner running against Polymarket instead of Kalshi — with the massive advantage that Polymarket charges 0% fees on most markets vs Kalshi's ~7% scaled fee. This means your existing 3% minimum edge threshold drops to ~1%, opening up far more opportunities.

**Minimum viable arb**: 3.5% aggregate spread needed to overcome Kalshi fees when both legs include Kalshi. When one leg is Polymarket (0% fees), minimum drops to ~1.5%.

**Competition**: 40+ open-source arb bots on GitHub. Professional bots operate at sub-5ms latency. But most target crypto 15-minute markets — sports and economics have less bot competition.

### 2. IB ForecastEx Opportunities (P-004, P-005)

ForecastEx is underexploited because it's newer and harder to discover contracts (no API-based symbol list). Two distinct strategies emerged:

**Direct arb (P-004)**: ForecastEx and Kalshi both list economic event contracts (Fed rate, CPI, GDP). ForecastEx charges $0 commission vs Kalshi's scaled fees. Fed research (Feb 2026) confirms Kalshi has a perfect day-before FOMC forecast record — beating CME futures. Price discrepancies between ForecastEx and Kalshi on the same FOMC outcomes create risk-free arb when > fee threshold.

**Carry strategy (P-005)**: ForecastEx pays 3.14% APY incentive coupon on positions (accrues daily on closing value, paid monthly). Even losing positions earn coupon. For long-dated events (6+ month expiry), this carry is material. Hedge on Kalshi/Polymarket and collect the coupon spread. Lower edge (26-35 bps) but very low risk.

**API access confirmed**: TWS API supports ForecastEx via secType="OPT", exchange="FORECASTX". ib_insync works. Limitation: can only BUY (buy opposing contract to exit, no short selling).

### 3. Stale-Line / Latency Edge (P-003, P-007)

Two speed-based pods, each targeting different information asymmetries:

**Crypto flash (P-003)**: Binance WebSocket delivers spot prices in 4-13ms. Prediction markets lag by 200-800ms. When BTC direction is confirmed, buy the confirmed outcome on the slower venue. Documented 78-98% win rates in 2025 by existing bots. However, Polymarket charges 10% taker fee on 15-minute crypto markets, significantly reducing edge.

**Econ data releases (P-007)**: When CPI/NFP/FOMC data drops, Kalshi reprices within 2-5 seconds but there's a window. Lower edge and fewer opportunities (~10 major releases/month) but less competition than crypto flash arb.

**Competitive reality**: Both require sub-500ms end-to-end latency. Institutional players with co-located infrastructure operate at sub-5ms. The "easy money" in latency arb has been largely captured by 2026. Success requires either proprietary data feeds, execution advantage, or focusing on less-competitive market types (economics over crypto).

### 4. Options IV vs Prediction Markets (P-008)

The most intellectually interesting but hardest to build. Options chains embed full probability distributions via Breeden-Litzenberger / SABR models. These can be compared to binary prediction market prices.

**Documented mispricings**: Earnings straddles overprice actual moves by 1-4 vol points. Prediction markets lag options on corporate events. During 2024 election, prediction markets repriced the winner hours before traditional options.

**Why it scored lowest**: Requires SABR vol surface calibration, delta/vega hedging, $1M+ for options margin, and the most complex new infrastructure. Estimated 8-12 weeks to build vs 2-3 weeks for P-002/P-006. Marked as RESEARCH status rather than PLANNED.

---

## Build Priority Recommendation

Based on composite scores and infrastructure reuse:

1. **P-006 (Sportsbook-Polymarket Consensus)** — Score 4.30, fastest to build. It's literally your existing P-001 scanner with a new execution venue and lower fee threshold. ~1 week build.

2. **P-002 (Kalshi-Polymarket Cross-Venue Scanner)** — Score 4.30, proven $40M edge. Requires dual-leg execution and event matching across venues. ~2-3 weeks build.

3. **P-004 (ForecastEx-Kalshi Econ Arb)** — Score 3.45, new venue connector but same economics. Diversifies into non-sports markets. ~3-4 weeks build (IB connector is the bottleneck).

---

## Infrastructure Dependencies Identified

All 7 pods share a common need for a **Polymarket CLOB client** — this should be the first infrastructure build. P-002, P-003, P-005, and P-006 all require it.

P-004, P-005, and P-008 all need an **IB ForecastEx connector** — second infrastructure priority.

P-003 and P-007 need **real-time data feeds** (Binance WebSocket, BLS/FRED parsers) — third priority, and these are pod-specific rather than shared.

---

## Next Session Preview

Session 3 will focus on Incentive/Promo pods (FanDuel/DraftKings promo conversion) and Fair-Odds Baseline pods (political/non-sports fair-value models, multi-source consensus). These fill out the remaining pod families before the Session 4 editor pass scores and prioritizes everything.
