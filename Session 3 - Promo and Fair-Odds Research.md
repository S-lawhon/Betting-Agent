# Session 3 — Incentive/Promo & Fair-Odds Baseline Pod Research

*Research conducted February 22, 2026*

---

## Summary

Session 3 researched and populated 7 new pods (P-009 through P-015) across two new families: Incentive/Promo (2 pods) and Fair-Odds Baseline (5 pods). The Strategy Library now contains 15 pods across all three families.

### Full Pod Inventory (all 15, ranked by Composite Score)

| Pod | Name | Family | Score | Edge (bps) | Status |
|-----|------|--------|-------|------------|--------|
| P-002 | Kalshi-Polymarket Cross-Venue Scanner | Discrepancy | 4.30 | 150-300 | PLANNED |
| P-006 | Sportsbook-Polymarket Consensus | Discrepancy | 4.30 | 200-400 | PLANNED |
| P-001 | Kalshi Moneyline Value | Discrepancy | 4.20 | 300-500 | LIVE (paper) |
| P-009 | Sportsbook Sign-Up Bonus Blitz | Promo | 4.10 | 7000-8000 | PLANNED |
| P-010 | Daily Odds Boost Grind | Promo | 3.55 | 200-500 | PLANNED |
| P-012 | Macro Economic Nowcast | Baseline | 3.50 | 300-800 | RESEARCH |
| P-004 | ForecastEx-Kalshi Econ Arb | Discrepancy | 3.45 | 50-150 | PLANNED |
| P-003 | Crypto 15-Min Flash Arb | Discrepancy | 3.40 | 80-150 | PLANNED |
| P-011 | Political Fair-Value Model | Baseline | 3.10 | 200-500 | RESEARCH |
| P-014 | Prediction Market Maker | Baseline | 3.00 | 100-250 | RESEARCH |
| P-005 | ForecastEx Yield Carry | Discrepancy | 2.95 | 26-35 | PLANNED |
| P-015 | LLM Event Forecaster | Baseline | 2.80 | 200-500 | RESEARCH |
| P-013 | Crypto Derivatives Fair-Value | Baseline | 2.75 | 300-600 | RESEARCH |
| P-007 | Econ Data Release Stale-Line | Discrepancy | 2.70 | 50-150 | PLANNED |
| P-008 | Options IV-to-Probability Rel Value | Discrepancy | 2.65 | 200-600 | RESEARCH |

---

## Key Research Findings

### Incentive/Promo Pods (P-009, P-010)

**P-009 (Sign-Up Bonus Blitz)** scored 4.10 — the highest-edge pod in the entire library at 7000-8000 bps. This is because sportsbook bonuses are essentially free money with known conversion rates. FanDuel ($100-300 bonus bets) and DraftKings ($200 bonus bets) convert at 70-80% through matched betting (place bonus on underdog, hedge on Pinnacle or another book). 4 books x 3 states = $2,160 in month 1.

The catch: it's finite (one-time per book per state), requires manual bet placement (no API), and carries gubbing risk. But as a kickstart strategy, it's unbeatable ROI.

**P-010 (Daily Odds Boost Grind)** scored 3.55. Repeatable daily income ($500-900/month per account) from FanDuel/DraftKings boosts that exceed fair probability. DraftKings+ subscription ($20/mo) adds unlimited parlay boost tokens. However, gubbing timeline is 2-4 weeks of aggressive grinding. Sustainability requires account rotation across states and careful priming (mush bets, round amounts, varied patterns).

**Critical limitation for both**: All bet placement must be manual. FanDuel and DraftKings prohibit bots, use WebGL/Canvas fingerprinting for detection, and will terminate accounts. Your scanner infrastructure can automate the EV calculation and hedge sizing, but execution is human-in-the-loop.

### Fair-Odds Baseline Pods (P-011 through P-015)

These pods build their own fair-value estimates for markets where no bookmaker consensus exists (unlike sports, where you can devig Pinnacle).

**P-012 (Macro Economic Nowcast)** scored highest in this family at 3.50. Uses free Federal Reserve data: Cleveland Fed CPI nowcast + Atlanta Fed GDPNow + Survey of Professional Forecasters. Kalshi beat Bloomberg consensus in 11/13 months on CPI. The edge is real and documented: when your model estimate diverges from Kalshi bins, you trade the mispricing. Limitation: only ~36 events/year (12 CPI + 4 GDP + 12 NFP + 8 FOMC).

**P-011 (Political Fair-Value Model)** scored 3.10. Bayesian ensemble of polls (538, RCP) + superforecasters (Metaculus) + base rates. Superforecasters beat prediction markets by 30% on Brier score. However, this requires calibration data and works best during election cycles (seasonal).

**P-014 (Prediction Market Maker)** scored 3.00. This is a meta-pod: it uses fair-value estimates from other pods to quote both sides on Polymarket/Kalshi and earn the spread + maker rebates. Polymarket offers up to 100% taker fee rebate on eligible markets; Kalshi rebates up to $7K/week. High capacity ($1K-$25K/day) but requires sub-100ms quote updates and inventory management.

**P-015 (LLM Event Forecaster)** scored 2.80. LLMs now rank #2 on forecasting leaderboards (behind superforecasters, ahead of public crowd). Trained on 9,800 Polymarket outcomes with 7-10% accuracy improvement. Projected superforecaster parity by Q4 2026. Cross-category (politics, economics, crypto, sports, entertainment). However, API costs ($1-5K/month) and hallucination risk are real.

**P-013 (Crypto Derivatives Fair-Value)** scored 2.75. Extracts probability distributions from Deribit options via Breeden-Litzenberger and compares to Polymarket binary buckets. Theoretically the richest edge (300-600 bps) but requires SABR vol surface calibration, Deribit account (offshore, regulatory risk), and the most complex new infrastructure. 8-12 weeks to build.

---

## Session 3 Deliverables

- 7 new pods (P-009 through P-015) fully populated in Strategy Library spreadsheet
- All 20 composite score formulas verified (0 errors)
- Research notes covering promo conversion math, fair-value modeling frameworks, and market-making infrastructure

---

## Next Session Preview

Session 4 is the **Editor Pass**: score and rank all 15 pods, finalize build priority order, identify shared infrastructure components, and produce the master build roadmap. This is the last research/planning session before we move into architecture and code.
