# Live Betting Agent — Research & Opportunity Analysis

**Date:** March 28, 2026
**Author:** Sam / Claude
**Pod Designation:** P-014 (Live Game Agent)
**Target Sports:** NBA, MLB
**Execution Venue:** Kalshi

---

## 1. Thesis

Retail-heavy live/in-game betting markets exhibit systematic mispricings driven by emotional overreaction, momentum chasing, and recency bias. These behavioral patterns create windows of positive expected value that a disciplined, model-driven agent can exploit — particularly on Kalshi, where 0% transaction fees remove the cost barrier that typically prevents profitable exploitation of these edges.

---

## 2. Evidence for the Thesis

### 2.1 Academic Support

The academic literature broadly supports that live betting markets contain exploitable inefficiencies, though with important nuance:

**Moskowitz (Journal of Finance):** Analysis of 100,000+ contracts across three decades (NBA, NFL, MLB, NHL) found strong momentum effects — prices move from open to close and are "completely reversed by the game outcome." The key finding: mispricings persist because transaction costs (~4.55% vig at traditional sportsbooks) prevent arbitrage from eliminating them. On Kalshi at 0% fees, this barrier is removed.

**Ötting (2025, Economic Inquiry):** Directly tested momentum betting in contests. Found that a contrarian strategy — betting against momentum — yields statistically significant returns. The momentum premium is initially pronounced but "sequentially rectified" as information arrives, meaning there's a window to capture the overreaction.

**Croxson & Reade (2014):** Studied live betting specifically. Found prices update "swiftly" upon major events but this speed of movement ≠ accuracy. Fast doesn't mean correct.

**Key insight:** The literature consistently finds that mispricings are real and measurable, but *economically small at traditional sportsbooks*. The typical 4.55% vig makes them unprofitable. Kalshi's 0% fee structure fundamentally changes this equation.

### 2.2 Retail Bettor Behavior Patterns

Research documents several systematic biases in live betting:

**Recency bias / FOMO:** When a favorite gives up an early lead, the public overreacts — hammering the underdog based on the last 5 minutes rather than the full game probability. This creates temporary mispricing on the favorite.

**Momentum chasing:** Bettors show strong preference for teams on scoring runs. Studies find this preference "does not earn superior returns" — the perceived momentum doesn't predict outcomes, but the betting volume moves the line.

**Overreaction to shocks:** When a longshot team scores late or takes an unexpected lead, markets underestimate the favorite's comeback probability relative to what outcome data shows.

### 2.3 Sport-Specific Patterns

**NBA:**
- Scoring runs create the most consistent overreaction. When a good team falls behind due to unsustainable shooting variance, live spreads overshoot.
- Star player foul trouble triggers line movement that often exceeds the true impact probability.
- 1,230 games/season provides large sample for edge detection.
- Momentum has no actual impact on outcomes, but fans and recreational bettors are heavily influenced by its perception.

**MLB:**
- Starting pitcher dominance: A single player influences outcomes more than in any other sport. Pitcher scratches create 50+ cent moneyline swings.
- Early run effects: Baseball moneylines swing more dramatically than other sports. An ace on the mound can make a mediocre team -180; their fifth starter makes them +110.
- Slower pace creates longer windows for line adjustment, potentially favorable for medium-frequency strategies.
- Odds refresh every 10-30 seconds during play, with gaps between stadium events and display updates.

---

## 3. Infrastructure Assessment

### 3.1 Kalshi Live Market Capabilities

**What's available:**
- Live/in-game moneyline contracts for MLB and NBA
- Player props (expanded significantly in 2025-2026)
- Peer-to-peer order book exchange — prices set by trader supply/demand, not a market maker
- 0% transaction fees (as of March 2026)
- Full cash-out capability (sell position at any time before settlement)
- CFTC-regulated, nationally available

**API capabilities:**
- REST API for market data and order placement
- WebSocket API for real-time price updates, orderbook changes, and quote data
- Rate limits enforce medium-frequency trading (not HFT)
- REST latency: 50-200ms typical
- Free API access for authenticated users
- Full order book depth visibility

**Position limits:** Up to $25,000 per contract (retail), higher for membership tiers.

**Key advantage vs. sportsbooks:** No account closure risk for winning. Kalshi can't limit your bets because you're trading on an exchange against other participants, not against the house.

### 3.2 Odds API Live Data

**Capabilities:**
- Live/in-game odds via `GET /v4/sports/{sport}/odds`
- Markets: moneyline, spreads, totals, player props
- ~40 sportsbooks covered (DraftKings, FanDuel, BetMGM, Pinnacle, etc.)
- Live scores update ~every 30 seconds via `/v4/sports/{sport}/scores`
- HTTP polling only (no WebSocket)

**Rate limits:** 30 requests/second hard limit.

**Cost consideration:** Credit-based pricing. Polling 3 markets × 1 region every 10 seconds = ~25,920 credits/day. Free tier (500 credits) exhausted in ~18 minutes. Will need a paid plan ($30-99/month) for live polling.

**Latency:** Several seconds per request. Adequate for medium-frequency strategies (10-30 second cycles) but not for sub-second execution.

### 3.3 Data Architecture for Live Agent

The live agent requires fundamentally different data handling than pre-game pods:

| Dimension | Pre-Game Pods (P-001/P-006) | Live Agent (P-014) |
|---|---|---|
| Scan frequency | Every 5-10 min | Every 10-30 sec |
| Data freshness | Hours old is fine | Seconds matter |
| Game state | Not needed | Critical (score, inning/quarter, time remaining) |
| Fair value model | Static consensus | Dynamic, game-state-dependent |
| Decision latency | Minutes acceptable | <30 seconds target |
| Volume per game | 1 trade max | Multiple trades per game |

---

## 4. The Edge: Where Exactly Does It Come From?

### 4.1 Primary Edge: Kalshi vs. Sharp Sportsbook Consensus

The same core logic as P-001/P-006, applied to live markets:

1. Poll Odds API for live odds across ~40 sportsbooks (including Pinnacle as the sharpest line)
2. Build a fair value estimate from the multi-book consensus, Pinnacle-weighted
3. Compare to Kalshi's live market price
4. When Kalshi deviates beyond threshold → trade

**Why this should work in-game:** Kalshi's live market is populated by retail traders reacting emotionally to game events. The sportsbook consensus (especially Pinnacle) is priced by algorithms with real-time data feeds. The gap between "what retail thinks" and "what the sharp market says" should be wider during live games than pre-game.

### 4.2 Secondary Edge: Momentum Reversion

When a team goes on a scoring run (NBA) or scores early (MLB), Kalshi's retail-heavy market likely overshoots. A contrarian strategy that:
1. Detects when Kalshi's implied probability has moved more than the sharp consensus after a game event
2. Bets against the momentum (backing the team that's falling out of favor)
3. Sizes based on the magnitude of the overreaction

### 4.3 Fee Advantage

This is the structural edge that makes everything work. Traditional sportsbooks charge ~4.55% vig. Kalshi charges 0%. The academic literature consistently finds live mispricings in the 1-5% range — unprofitable at sportsbooks, potentially profitable on Kalshi.

---

## 5. Challenges & Risk Factors

### 5.1 Latency

- Odds API polling adds seconds of latency
- Kalshi REST API adds 50-200ms per order
- Total loop: detect opportunity → place order could take 5-15 seconds
- This is acceptable for momentum-reversion (which persists for 10-60+ seconds) but not for true latency arbitrage

### 5.2 Liquidity

- Kalshi live markets may have thin order books
- Wide bid-ask spreads could consume the edge
- Need to monitor fill rates and slippage

### 5.3 Odds API Costs

- Frequent polling will require paid tier ($30-99/month)
- Need to optimize polling (only during active games, batch requests)

### 5.4 Model Risk

- Fair value estimates are only as good as the consensus
- Consensus can be wrong — all books moved by the same data
- Need graceful degradation when consensus is unavailable

### 5.5 Market Suspension

- Kalshi may suspend trading during high-volatility game moments
- Need to handle "market closed" states gracefully

---

## 6. Conclusion

The opportunity is real, supported by academic evidence and structurally enabled by Kalshi's 0% fee model. The primary risk is execution — latency, liquidity, and model accuracy in fast-moving markets. An aggressive build-test-iterate approach is warranted given that:

1. The core infrastructure (BasePod, TradeStore, Kalshi client, Odds API) already exists
2. Paper mode provides safe testing environment
3. NBA and MLB are actively in-season right now
4. The edge is time-sensitive — as Kalshi's markets mature and attract more sophisticated participants, the retail inefficiency window may narrow

**Recommendation:** Build P-014 as a new pod extending BasePod, start with live moneyline consensus (mirroring P-001's logic but at 10-30 second intervals), add momentum-reversion as a second signal, and paper trade immediately.
