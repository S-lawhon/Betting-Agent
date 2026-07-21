# Kalshi vs. Crypto Options Arbitrage: Research & Bot Viability

**Date:** March 26, 2026
**Author:** Betting Pod Shop Research

---

## Executive Summary

Kalshi's crypto prediction markets (binary event contracts on BTC price thresholds) are structurally equivalent to binary/digital options. The same payoff can be replicated using vertical spreads on Deribit (the dominant crypto options venue, ~80% global share). When implied probabilities diverge between these two venues, a cross-venue arbitrage exists. This research assesses the mechanics, edge potential, fee drag, and viability of building a pod (P-XXX) to exploit this.

---

## 1. The Two Venues

### Kalshi — Binary Event Contracts

Kalshi offers BTC price contracts across multiple timeframes: **15-minute, hourly, daily, weekly, monthly, and yearly** expirations. Each contract is a binary bet paying $1.00 if BTC is above/below a threshold at expiry, $0.00 otherwise.

**Settlement:** CF Benchmarks Real-Time Index (CFB RTI) — averages 60 seconds of per-second prices at expiration. This is the same benchmark used by CME Bitcoin futures, providing institutional-grade settlement.

**Key Contract Types for Arbitrage:**
- "Will BTC be above $X by date Y?" — pure terminal binary
- "Will BTC cross $X before date Y?" — path-dependent (one-touch style)
- Hourly range contracts — short-dated binaries

**Fee Structure (Taker):**
```
Fee = roundup(0.07 × C × P × (1 - P))
```
Where C = contracts, P = price in dollars. This is a **sliding scale** — fees are highest near P=0.50 (maximum uncertainty) and lowest at extremes. Example: a contract at 14¢ (like the "BTC $100k before July 2026" shown in your screenshot) has a taker fee of ~0.07 × 1 × 0.14 × 0.86 = ~0.84¢ per contract, or roughly **6% of the contract price**.

**Maker fees** are 1/4 of taker fees (0.0175 multiplier). API-driven limit orders qualify as maker.

**API:** REST + WebSocket. Free for all verified users. Latency 50-200ms REST, lower on WS. Rate limits preclude true HFT but are fine for medium-frequency strategies.

### Deribit — Vanilla Options (Binary Replication)

Deribit is the world's largest crypto options exchange. It offers European-style BTC options with strikes across a wide range and expirations from daily to multi-year.

**Key Facts:**
- Settled in BTC (inverse contracts), but USD-denominated strikes
- European exercise only (no early exercise)
- Settlement uses Deribit BTC Index (multi-exchange aggregate)
- Current open interest: $14+ billion in BTC options as of March 2026

**Fee Structure:**
- Options: 0.03% of underlying per side (capped at 12.5% of option price)
- For a $80,000 BTC option trade: ~$24 per contract per side
- Maker/taker both 0.04% of underlying for options

**API:** Well-documented REST + WebSocket. High throughput. ccxt library support. Historical data available via Tardis.

---

## 2. The Arbitrage Mechanics

### 2A. Terminal Binary Replication (Main Strategy)

A Kalshi contract "BTC above $100k by Jan 2027" is economically identical to a **digital call option** struck at $100k expiring Jan 2027.

**Replicating with a vertical spread on Deribit:**

A tight bull call spread approximates a digital call:
```
Buy Call at Strike K₁ (slightly below target)
Sell Call at Strike K₂ (slightly above target)

Implied Probability ≈ Spread Price / (K₂ - K₁)
```

For example, to replicate "BTC above $100k":
- Buy $99k call, sell $101k call (Dec 2026 expiry)
- If the spread costs $400 on a $2,000 width → implied probability = 20%
- If Kalshi YES trades at 27¢ → Kalshi implies 27% probability
- **Edge = 7 percentage points** (buy Deribit spread at 20%, sell Kalshi YES equivalent)

**More precisely**, the Moontower Meta methodology uses:
```
z-score = ln(Strike / Spot) / (IV × √T)
```
Where IV comes from the Deribit options chain. This z-score maps to a probability via the normal CDF, automatically incorporating the volatility surface.

### 2B. Path-Dependent Contracts (One-Touch)

Some Kalshi contracts are "will BTC hit $X at any point before Y" — these are **one-touch digital options**, not terminal binaries.

**Replication heuristic:** The probability of BTC *touching* a strike is approximately **2× the delta** of the vanilla option at that strike. This works because the reflection principle doubles the terminal probability for the touch event.

This is harder to arb because:
- Replication requires dynamic hedging, not a static spread
- Skew sensitivity is amplified
- Deribit only offers European options (no path-dependent payoff)

**Recommendation:** Focus the bot on terminal contracts only. One-touch contracts are a Phase 2 opportunity requiring more sophisticated modeling.

### 2C. Short-Dated Contracts (Hourly/Daily)

Kalshi's 15-minute and hourly BTC contracts are interesting but harder to arb against Deribit because:
- Deribit's shortest expiries are daily
- The timeframe mismatch makes clean replication impossible
- These are better suited for **cross-prediction-market arb** (Kalshi vs Polymarket), which P-002 already handles

---

## 3. Fee Drag Analysis

For a typical terminal binary arb on a monthly/quarterly contract:

| Component | Cost |
|-----------|------|
| Kalshi taker fee (at P=0.27) | ~5.4% of contract price |
| Kalshi maker fee (limit order) | ~1.4% of contract price |
| Deribit spread (2 legs × 0.03%) | ~$48 per BTC-equivalent notional |
| Deribit bid-ask spread | 1-3% on OTM options |

**Effective round-trip cost estimate (maker on Kalshi, taker on Deribit):**
- ~2-5% of notional edge is consumed by fees
- **Minimum viable edge: ~5-7% divergence** between Kalshi implied probability and Deribit-derived probability to be profitable after fees

For comparison, P-001 (Kalshi vs Sharp's) has been finding edges of 3-15% on sports, and P-002 (Kalshi-Polymarket) finds 1-5% edges. Crypto options arb edges are likely in a similar range but with different dynamics.

---

## 4. Practical Considerations

### What Makes This Harder Than Sports Arb

1. **Continuous underlying:** BTC price is continuous and options-priced, meaning the "sharp" side (Deribit) is extremely efficient. Kalshi is the "soft" side.

2. **Hedge complexity:** Unlike sports where both legs settle to $0 or $1, here one leg is a binary (Kalshi) and the other is a spread (Deribit) with different payoff profiles at the boundaries.

3. **Settlement basis risk:** Kalshi uses CF Benchmarks; Deribit uses its own index. Small divergences at expiry are possible.

4. **Collateral lock-up:** Kalshi contracts lock up full notional ($1 per contract). Deribit spread margin is more capital-efficient but denominated in BTC (adds FX risk).

5. **Currency mismatch:** Kalshi settles in USD, Deribit in BTC. The arb has embedded BTC/USD exposure unless hedged.

### What Makes This Easier / Attractive

1. **Kalshi crypto markets are less efficient** than their sports markets. Retail-heavy flow, wider spreads, and less sophisticated participants on the crypto prediction side.

2. **Deribit provides a high-quality pricing oracle.** Unlike sports where "fair value" requires consensus estimation, options IV gives a mathematically rigorous probability estimate.

3. **Continuous markets.** Both venues trade 24/7 (Kalshi crypto is 24/7, Deribit is 24/7). No event-timing constraints like sports.

4. **Existing infrastructure.** Your pod shop already has Kalshi API integration, order management, paper trading, and the BasePod framework.

5. **Multiple contract tenors.** You can scan across daily, weekly, monthly, quarterly, and yearly contracts simultaneously for the widest edges.

---

## 5. Existing Open-Source Reference

Several GitHub repos implement related strategies:

- **CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot** — Kalshi vs Polymarket hourly BTC arb. Python + FastAPI. Good reference for Kalshi API integration patterns.
- **Sectionnaenumerate/Polymarket-Kalshi-btc-arbitrage-bot** — 15-min BTC arb in Rust. Shows real-time orderbook monitoring.
- **Multiple Kalshi AI trading bots** — Pattern for market scanning and automated order placement via Kalshi API.

None of these implement the **Kalshi vs Deribit options chain** strategy specifically. This would be novel.

---

## 6. Proposed Pod Architecture: P-013 (Kalshi-Deribit Crypto Options Arb)

### Data Pipeline
```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│ Deribit WS   │────▶│ IV Surface   │────▶│ Implied Prob   │
│ Options Chain │     │ Builder      │     │ Calculator     │
└─────────────┘     └──────────────┘     └───────┬────────┘
                                                  │
                                          ┌───────▼────────┐
                                          │ Edge Detector  │
                                          │ (compare probs)│
                                          └───────┬────────┘
                                                  │
┌─────────────┐     ┌──────────────┐     ┌───────▼────────┐
│ Kalshi WS    │────▶│ Contract     │────▶│ Signal Gen     │
│ Crypto Mkts  │     │ Parser       │     │ & Sizing       │
└─────────────┘     └──────────────┘     └───────┬────────┘
                                                  │
                                          ┌───────▼────────┐
                                          │ Execution      │
                                          │ (Kalshi only*) │
                                          └────────────────┘
```
*Phase 1 executes only on Kalshi (the mispriced side). Phase 2 adds Deribit hedging.

### Core Components

1. **Deribit IV Surface Builder** — Pulls full BTC options chain, builds implied volatility surface by strike and expiry. Updates every 5-30 seconds.

2. **Binary Probability Calculator** — For each Kalshi contract (strike K, expiry T):
   - Method 1: Vertical spread replication (tight spreads around K)
   - Method 2: Black-Scholes N(d2) with interpolated IV
   - Method 3: Delta-based approximation (2× delta for one-touch)
   - Ensemble of methods with confidence weighting

3. **Kalshi Contract Scanner** — Monitors all active BTC contracts on Kalshi. Maps each to corresponding Deribit strike/expiry.

4. **Edge Detector** — Compares Kalshi market price to Deribit-implied fair value. Flags when |edge| > threshold (configurable, start at 7%).

5. **Kelly Sizer** — Uses edge magnitude and confidence to size positions. Fractional Kelly (0.25-0.5) given model uncertainty.

6. **Execution Engine** — Places maker limit orders on Kalshi at fair value. Phase 1 is Kalshi-only (directional bet that Kalshi is mispriced). Phase 2 adds Deribit spread hedging for true arb.

### Phase 1 vs Phase 2

| | Phase 1 (Directional) | Phase 2 (Hedged Arb) |
|--|--|--|
| Kalshi execution | Yes (maker orders) | Yes |
| Deribit execution | No | Yes (vertical spreads) |
| Risk profile | Directional — need Deribit pricing to be "right" | Market-neutral — profit regardless of BTC direction |
| Capital required | Low ($500-2k Kalshi balance) | Higher (Kalshi + Deribit margin) |
| Complexity | Moderate | High (cross-venue, cross-currency) |
| Edge required | 5-7% minimum | 3-5% minimum (lower because hedged) |

**Recommendation:** Start with Phase 1. Deribit's options market is extremely efficient and well-arbitraged by institutional market makers. If Deribit says the probability is 20% and Kalshi says 27%, Deribit is almost certainly closer to correct. Betting against Kalshi's mispricing directionally is a valid strategy (similar to how P-001 uses sharp books as the oracle).

---

## 7. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Model risk (IV surface wrong) | Medium | Ensemble methods, require agreement across approaches |
| Kalshi fee drag eats edge | High | Maker orders only, target edges > 7% |
| Settlement basis risk | Low | Both use multi-exchange BTC indices |
| Liquidity (Kalshi thin books) | Medium | Size limits, check depth before ordering |
| BTC volatility regime change | Medium | Dynamic IV recalculation, not stale params |
| Regulatory (Kalshi crypto contracts) | Low | CFTC-regulated, CF Benchmarks settlement |
| Capital lock-up | Medium | Position limits, diversify across tenors |

---

## 8. Implementation Roadmap

### Week 1-2: Data & Research
- [ ] Build Deribit options data pipeline (WebSocket, ccxt or direct API)
- [ ] Build IV surface interpolation module
- [ ] Backtest: pull historical Kalshi crypto prices + Deribit options data, compute historical edge distribution

### Week 3: Pod Skeleton
- [ ] Create P-013 pod extending BasePod
- [ ] Implement Kalshi crypto contract scanner (reuse existing Kalshi client)
- [ ] Implement binary probability calculator (vertical spread + BSM methods)
- [ ] Edge detection and alert system

### Week 4: Paper Trading
- [ ] Deploy to VPS in paper mode
- [ ] Monitor edge frequency, size, and duration
- [ ] Tune thresholds based on observed data

### Week 5+: Live (Phase 1)
- [ ] Enable Kalshi maker orders on highest-confidence signals
- [ ] Position limits and risk gates
- [ ] Dashboard integration (add to :8080 UI)

---

## 9. Key Technical Dependencies

```
# New packages needed
ccxt          # Deribit API (already supports options chains)
scipy         # Normal CDF for BSM probability calculations
numpy         # IV surface interpolation
websockets    # Deribit real-time feed (or use ccxt WS)
```

All compatible with the existing Python 3.12 venv on the VPS.

---

## 10. Bottom Line

**Is this viable?** Yes, with caveats.

The strategy is sound in theory — Kalshi crypto markets are less efficient than Deribit options, and the mathematical relationship between binary event contracts and vertical spreads is well-established. The Moontower Meta methodology provides a rigorous framework.

**The main challenge is edge size vs. fee drag.** Kalshi's sliding fee structure (up to ~6% on mid-probability contracts) means you need substantial divergence to profit. The sweet spot is likely **far OTM contracts** (like the 14% "BTC $100k before July" in your screenshot) where Kalshi fees are lower in absolute terms and retail mispricing is more common.

**Phase 1 (directional, Kalshi-only) is the pragmatic starting point.** It fits your existing pod architecture, reuses your Kalshi client, and has low capital requirements. The key question is empirical: how often and how large are the divergences? A 2-week data collection phase will answer this before any capital is at risk.

This would be the first pod in your shop that uses **options-derived fair value** rather than consensus odds, adding a fundamentally different pricing methodology to your arsenal.
