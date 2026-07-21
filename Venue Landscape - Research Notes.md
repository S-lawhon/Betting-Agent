# Venue Landscape Research — Session 1 Notes

*Research conducted February 22, 2026*

---

## Executive Summary

You have access to 6 venues. Here's the quick picture of what's programmable and what's not:

| Venue | API Trading | Automated OK | Your Status |
|-------|-------------|-------------|-------------|
| Kalshi | Full (REST + WSS + FIX) | Yes | Active, scanner running |
| Polymarket | Full (CLOB REST + WSS) | Yes, encouraged | Active |
| Interactive Brokers | Full (TWS + Client Portal) | Yes | Active |
| Robinhood | Crypto API only | No (event contracts) | Active, limited use |
| FanDuel | None | Prohibited | Not yet opened |
| DraftKings | None | Prohibited | Not yet opened |

**Bottom line:** Three venues (Kalshi, Polymarket, IB) have full API access and welcome automation. FanDuel/DraftKings are manual-only and actively hostile to sharp bettors — their value is exclusively in promos and as odds-data sources (via third-party APIs like The Odds API). Robinhood's event contracts are growing fast but have no programmatic access yet.

---

## Venue Deep Dives

### Kalshi (Primary Execution Venue — Already Integrated)

**What you already use:** REST API for market listing + order placement, The Odds API for fair value.

**What you're not yet using:**
- **WebSocket feeds** — real-time orderbook deltas and trade feeds. Much lower latency than REST polling. Would enable stale-line detection pods.
- **Non-sports markets** — Kalshi offers political (elections), economics (CPI, GDP, jobs, Fed), crypto (BTC/ETH price ranges), climate, entertainment. Your existing edge_calculator and risk_manager work for any binary contract — the only new work is finding fair-value sources for non-sports markets.
- **Batched orders** — up to 15 orders per API call, now generally available. Could improve execution efficiency for multi-market scanning.
- **Historical data API** — separate endpoint with up to 1-year lookback. Valuable for backtesting non-sports pods. Note: migrating away from live API by March 2026.

**Fee insight:** The $0.07*P*(1-P) formula means fees peak at ~$0.0175 for 50¢ contracts and shrink toward extremes. Pods targeting high-probability (>80¢ or <20¢) contracts face minimal fees. Maker fees were introduced April 2025 but follow similar scaled structure.

**Rate limits:** Tiered system (Basic → Prime). Your current tier likely limits high-frequency scanning. For pods that need faster polling, consider WSS subscriptions instead of REST polling.

---

### Polymarket (Highest-Priority New Venue)

**Why this is Pod #2 territory:** Full API, zero fees on most markets, explicitly bot-friendly, and you already have an account. The CLOB architecture is similar to Kalshi's — the existing scanner pattern translates directly.

**API architecture:**
- CLOB REST at `https://clob.polymarket.com` for order management
- WebSocket feeds for real-time Level 2 data (bid/ask with sizes), trade feeds, sports-specific channel
- Authentication via EIP-712 signing (L1) or HMAC-SHA256 API credentials (L2)
- Official Python client: `py-clob-client`
- Batch orders up to 15 per call

**Fee advantage:** Most markets are zero-fee for both makers and takers. Only 15-minute crypto markets have a 10% taker fee. Polymarket US (CFTC-regulated, invite-only) charges 0.01% taker. Sports taker fees just starting to pilot (Feb 2026, NCAA and Serie A only).

**Market coverage:** ~3,242 active sports markets plus politics, crypto, economics, entertainment, weather. $21.5B total volume in 2025 (nearly 50% of global prediction market volume).

**Key opportunity:** Documented $40M in arb bot profits between Polymarket and other venues (April 2024 – April 2025). Cross-venue discrepancy scanning between Kalshi and Polymarket is a proven strategy.

**Blockchain considerations:** Built on Polygon, USDC only. Deposits via direct Polygon withdrawal from exchanges (cheapest) or card on-ramps. Gas fees negligible (<$0.01). Withdrawals instant and free.

---

### Interactive Brokers (Event Contracts + Options Plays)

**Two distinct opportunities:**

1. **ForecastEx / Forecast Contracts:** Binary event contracts (politics, economics, climate) at zero commission plus a 3.14% APY incentive coupon. CFTC-regulated. Tradeable via TWS API — contracts modeled as options instruments. Can't short directly; buy opposing contract to exit. Nearly 24/6 trading hours.

2. **Event-driven options strategies:** IB's Volatility Lab, Option Strategy Lab, and Price Skew Tracker enable automated vol strategies around events (earnings, Fed announcements, elections). These can serve as hedges or standalone plays correlated with prediction market contracts. For example: buy straddles when implied vol is low relative to prediction market implied probability of a large move.

**API access:** TWS API (native socket, most capable) and Client Portal REST API. Both support event contracts. `ib_insync` Python library simplifies development. 10 req/sec on Web API.

**Pod opportunities:**
- ForecastEx-vs-Kalshi arb (same underlying event, different pricing)
- ForecastEx-vs-Polymarket arb
- Options vol vs. prediction market implied probability arb
- Carry strategy: hold ForecastEx positions for 3.14% APY coupon while hedged elsewhere

---

### FanDuel & DraftKings (Promo Value Only)

**No programmatic trading possible.** Both explicitly ban bots/scripts with immediate account termination. Sharp bettors are actively limited (stake caps reduced, markets restricted, accounts "gubbed").

**Value proposition is exclusively promos:**
- FanDuel: Bet $5 get $100 if it wins. Daily odds boosts, profit boosts, parlay insurance.
- DraftKings: Bet $5 get $200 if it wins (expires 3/8/26). Daily boosts. DraftKings+ ($20/mo) for extra boost tokens.
- Typical promo conversion rate: 70-80% guaranteed profit by hedging bonus bets across other venues.

**Odds data value:** Even without placing bets, their odds are accessible via The Odds API, Unabated, SportsDataIO, etc. These lines contribute to your existing multi-bookmaker consensus — your scanner already ingests them indirectly through The Odds API.

**Recommendation:** Open both accounts for promo conversion. Manually place hedged bets to convert sign-up bonuses and ongoing promos to cash. Don't invest engineering effort in scraping or automating — the ToS risk and account-gubbing make it not worth the infrastructure cost.

---

### Robinhood (Watch and Wait)

**Growing fast but no API:** 12B+ event contracts traded in 2025, fastest-growing Robinhood product. Building infrastructure through Rothera (JV with Susquehanna) and MIAXdx acquisition (CFTC DCM, expected operational 2026).

**Current state:** Event contracts available via app only. Crypto API exists but doesn't cover event contracts or stocks. No developer access for event contracts documented.

**Recommendation:** Monitor for API release (likely late 2026). If Robinhood opens an event contract API, it becomes another venue for cross-platform arb given its massive retail flow (retail flow = more mispricing to exploit).

---

## Opportunity Map — Where the Pods Live

Based on this research, here's where the highest-value pods concentrate:

**Tier 1 — Build now (full API access, proven edge mechanisms):**
- Kalshi-vs-Polymarket cross-venue discrepancy scanning (both have APIs, both you have accounts)
- Kalshi non-sports expansion (political, economics — same execution venue, new fair-value sources)
- Polymarket-vs-sportsbook consensus (your existing fair-value engine, new execution venue)

**Tier 2 — Build soon (API access, new edge types):**
- IB ForecastEx-vs-Kalshi arb (both have APIs, same events, different pricing)
- ForecastEx carry strategy (3.14% APY while hedged)
- Options vol-vs-prediction market implied probability (IB Volatility Lab + Kalshi/Polymarket)

**Tier 3 — Manual/opportunistic:**
- FanDuel/DraftKings promo conversion (manual, not automated; extract sign-up bonuses and ongoing boosts)
- Stale-line capture (requires WSS on multiple venues; higher latency sensitivity)

**Tier 4 — Future:**
- Robinhood event contract arb (pending API release)
- Multi-venue portfolio arb aggregator (requires 3+ venue connectors operational)

---

## Key Data Sources Summary

| Source | What It Provides | Current Use | Cost |
|--------|-----------------|-------------|------|
| The Odds API | Multi-bookmaker odds (Pinnacle, FanDuel, DraftKings, etc.) | Active (fair value for scanner) | Paid (usage-based) |
| Kalshi API | Markets, orderbook, trades, historical | Active (market data + execution) | Free (fees on trades) |
| Polymarket CLOB | Markets, orderbook, trades, historical | Not yet integrated | Free |
| IB TWS API | ForecastEx contracts, options chains, vol data | Not yet integrated | Subscription for market data |
| FinFeedAPI | Polymarket OHLCV, orderbook snapshots | Not yet used | Free tier (1K calls/hr), paid from $99/mo |
| Polling aggregators | Political polls (for non-sports fair value) | Not yet used | Various |
| FRED / BLS | Economic data (for CPI/GDP/jobs fair value) | Not yet used | Free |

---

## Next Session Preview

With the venue landscape mapped, Session 2 will focus on researching and populating specific cross-venue discrepancy pods in the spreadsheet — the highest-priority pod family based on your existing infrastructure and account access.
