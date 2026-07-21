# Fed Rate Arbitrage: Kalshi vs. Public Markets

**Research Document — March 26, 2026**
**Betting Pod Shop — Strategy Exploration**

---

## 1. The Core Thesis

Kalshi lists binary event contracts on FOMC rate decisions (e.g., "Will the Fed cut rates at the May meeting?"). The traditional fixed income market — specifically CME 30-Day Fed Funds futures and SOFR options — also prices the probability of rate changes, but does so implicitly through forward rates. If these two markets disagree on the probability of a given rate outcome, an arbitrage (or at least a statistical edge) may exist by taking opposing positions on each side.

The question is: do they disagree, by how much, and can you practically capture the spread after fees and execution costs?

---

## 2. The Two Sides of the Trade

### 2A. Kalshi — The Prediction Market Side

Kalshi offers several contract types related to the Fed:

- **Fed Decision contracts** (`KXFEDDECISION`): Binary yes/no on whether the Fed will cut, hold, or hike at a specific FOMC meeting. These are the cleanest contracts for this strategy — they settle to $1 or $0 based on the announced decision.
- **Fed Funds Rate contracts** (`KXFED`): What will the Fed Funds rate be after a given meeting? These are bracketed ranges (e.g., 4.00–4.25%, 4.25–4.50%) and settle based on the announced target range.
- **Rate Cut Count contracts** (`KXRATECUTCOUNT`): How many total cuts by year-end? These are cumulative and harder to hedge against the futures curve.

For a clean arb, the **Fed Decision** or **Fed Funds Rate** contracts per meeting are ideal — they map directly to a single FOMC meeting outcome.

**Kalshi Fee Structure:**
- **Taker fee:** 0.07 × P × (1 − P) per contract, where P is the contract price. Max fee is 1.75¢ per contract (at P = 0.50). For a contract trading at 0.85 ("85% probability"), the fee is approximately 0.07 × 0.85 × 0.15 = 0.89¢ per contract.
- **Maker fee:** Zero for most markets (limit orders that rest on the book).
- **Settlement:** No additional settlement fee.
- **Interest on positions:** Kalshi pays ~4% annualized interest on cash in your account, which partially offsets the cost of capital tied up in positions.
- **Contract size:** $1 max payout per contract. Positions are denominated in contracts (integer quantities).

**Key numbers:** On a $0.85 contract, you put up $0.85 to win $0.15 (if the event occurs) or lose $0.85 (if it doesn't). After taker fees, your effective cost is ~$0.859.

### 2B. CME Fed Funds Futures — The Traditional Market Side

CME's **30-Day Federal Funds futures** (ticker: ZQ) are the canonical instrument for hedging or speculating on the Fed Funds rate. Each contract settles to the monthly average of the effective federal funds rate (EFFR) as published by the New York Fed.

**Contract specs:**
- **Notional:** $5,000,000
- **Tick size:** Half a basis point (0.005) = $20.835 per tick
- **Settlement:** Cash-settled to the arithmetic average of the daily EFFR for the contract month
- **Listed months:** 60 consecutive months
- **Margin:** Roughly $500–$1,500 per contract at most brokers (low relative to notional, because these instruments have low volatility)

**How to extract probabilities:** The CME FedWatch methodology is straightforward. If the current EFFR target is 4.25–4.50% (effective ~4.33%) and the futures contract for the meeting month prices at 95.75 (implied rate = 4.25%), the market is implying a rate cut is partially priced in. The exact probability calculation assumes rate changes come in 25bp increments:

> P(cut) = (Current Effective Rate − Implied Futures Rate) / 0.25

This is a simplification — in months with a meeting, only some days of the month trade at the new rate, so the calculation needs a day-weighting adjustment.

**SOFR Options (more granular):** The Atlanta Fed's Market Probability Tracker uses SOFR options prices to derive full probability distributions over rate outcomes, not just point estimates. This is a richer signal. CME also offers a SOFRWatch tool based on SOFR futures.

**Retail access:** Fed Funds futures are tradeable at Interactive Brokers, tastytrade, and other futures brokers. Minimum account size at IB is $2,000 for futures. SOFR futures and options are also available.

---

## 3. Where the Edge Might Come From

There are several structural reasons why Kalshi and the futures curve could disagree:

### 3A. Risk Premium in Futures

This is the biggest conceptual issue. Fed Funds futures prices are **not** pure probability forecasts — they embed a risk premium (term premium). Academic research from the Federal Reserve Board has documented that the term premium in near-dated Fed Funds futures is small but non-zero, and critically, it varies over time. In periods of high uncertainty, futures may overstate the probability of rate cuts (because investors pay a premium for downside protection), making futures-implied probabilities systematically biased relative to true probabilities.

Kalshi contracts, being binary events, theoretically reflect "purer" probabilities — though they carry their own biases (favorite-longshot bias, retail sentiment).

**Implication for the arb:** If futures systematically overstate the probability of cuts by, say, 2–5 percentage points due to the term premium, and Kalshi prices are closer to the "true" probability, you could consistently sell the implied probability on the futures side while buying it on Kalshi (or vice versa). The research from Piazzesi & Swanson (Stanford) and the Fed's own FEDS Notes series suggests term premiums are typically 1–6 basis points for near-term meetings and can be larger for 6+ month horizons.

### 3B. Participant Composition

Kalshi's participant base is predominantly retail and small-to-mid institutional — political junkies, macro traders, and prediction market enthusiasts. CME Fed Funds futures are traded by banks, asset managers, central bank reserve managers, and hedge funds. These populations have different information sets, risk tolerances, and biases.

Kalshi participants may:
- Overreact to headlines and rhetoric (Fed governor speeches get outsized reactions)
- Exhibit the classic favorite-longshot bias (underpricing high-probability outcomes, overpricing longshots)
- Have stale prices during off-hours (thinner liquidity than CME)

CME participants may:
- Embed hedging demand that distorts prices away from "pure" probabilities
- React faster to economic data releases (the machinery of institutional trading)
- Be influenced by flows that have nothing to do with rate expectations (hedging mortgage portfolios, for example)

### 3C. Timing and Information Asymmetry

Kalshi markets are open roughly 24/7 but with thin liquidity outside US trading hours. Fed Funds futures trade on CME Globex nearly 24 hours on weekdays with deep institutional liquidity. When a data release hits (CPI, NFP, ISM), the futures market reprices within seconds. Kalshi may take minutes or hours to fully adjust, creating windows of divergence.

### 3D. Contract Structure Mismatch

The trickiest issue. Fed Funds futures settle to the **monthly average** of the EFFR, not the rate on a specific day. An FOMC meeting in the middle of the month means the futures price reflects a blend of the pre-meeting rate and the post-meeting rate, weighted by the number of days each rate was in effect.

Kalshi contracts settle cleanly on the announced decision. This structural difference means the "probabilities" aren't exactly comparable without adjustment. For example, a month with a meeting on the 18th and 30 total days would have 17 days at the old rate and 13 at the new rate. The futures price is a day-weighted average, not a clean bet on the outcome.

**This is a solvable problem** — the day-weighting adjustment is well-documented (CME FedWatch does it automatically) — but it introduces basis risk into any cross-market position.

---

## 4. Constructing the Trade

### Scenario: Kalshi says 80% chance of a hold, CME implies 75% chance of a hold

**Kalshi leg:** Buy "Hold" contracts at $0.80. If the Fed holds, you receive $1.00 for a $0.20 profit per contract. If not, you lose $0.80. After taker fees (~0.07 × 0.80 × 0.20 = 1.12¢), net cost is ~$0.8112 per contract.

**CME leg:** The CME is underpricing the hold (or overpricing the cut). You want to express a "hold" view on the futures. If the Fed holds, the EFFR stays the same, so the futures price should stay flat. If the market is pricing some probability of a cut, the futures contract is trading slightly above where it would be under a certainty-of-hold scenario. You would **sell** the Fed Funds futures contract for the meeting month (betting that the rate will be higher than implied, i.e., no cut will happen).

**Notional sizing:** This is where it gets interesting. One CME Fed Funds futures contract has a notional of $5M. Each basis point move = ~$41.67. If the probability divergence is 5 percentage points (80% vs. 75% for a 25bp cut), the expected mispricing on the futures side is 5% × 25bp = 1.25bp = ~$52 per contract.

On Kalshi, you'd need roughly $52 / $0.20 = 260 contracts at the $0.80 price level to match the notional exposure, costing $208 in capital.

**The hedge isn't perfect.** The futures contract is a continuous price instrument settling to a monthly average; the Kalshi contract is a binary outcome. You're fundamentally combining a binary position with a linear position, which creates convexity mismatch. In the scenario where the Fed does something unexpected (a 50bp cut instead of 25bp), the payoffs diverge materially.

### Payout Matrix (simplified, per-unit)

| Outcome | Kalshi "Hold" (bought at 80¢) | CME Short (notional-matched) | Combined |
|---|---|---|---|
| Fed holds | +$0.20 | ≈ +$0 (rate unchanged) | +$0.20 |
| Fed cuts 25bp | −$0.80 | +$52 (futures overpriced) | Depends on sizing |
| Fed cuts 50bp | −$0.80 | +$104 | Net positive |

The hedge ratio is the critical variable and must be solved for your specific probability divergence and desired risk profile.

---

## 5. Practical Exploitability Assessment

### 5A. Is the Edge Real?

**Probably yes, but small and episodic.** Here's why:

- The Kalshi Fed rate markets have grown rapidly ($450M+ in open interest as of early 2026) but are still less efficient than CME markets. Retail-dominated order flow creates periodic mispricings.
- The academic literature confirms that Fed Funds futures embed a time-varying risk premium, which means they are **not** clean probability estimates. Any comparison must account for this, and the adjustment isn't trivial.
- The Bonini (2026) paper published in the Journal of Futures Markets ("Watching the FedWatch") examines this exact question and provides evidence that FedWatch probabilities can diverge from other market-implied measures.

**Best-case for finding edge:** Immediately after major data releases (when one market adjusts faster than the other) or in periods of high policy uncertainty (when the risk premium in futures widens).

### 5B. Fee Drag

On the Kalshi side, taker fees are roughly 0.5–1.75¢ per contract depending on the probability level. For a $0.80 contract with a $0.20 target profit, the fee is ~1.12¢ or about 5.6% of the gross profit. This is meaningful but manageable. Using maker orders (limit orders) eliminates this cost.

On the CME side, commissions at Interactive Brokers are typically $0.85 per side per contract for Fed Funds futures, plus exchange fees (~$0.47). Round-trip cost is under $3 per contract, negligible relative to the $5M notional.

### 5C. Liquidity

- **Kalshi:** The KXFED and KXFEDDECISION markets have grown significantly but the order book is still thin relative to CME. Bid-ask spreads on the most liquid meeting dates are typically 2–4 cents. Moving size (1,000+ contracts = $1,000+ at risk) may cause slippage.
- **CME:** Fed Funds futures are among the most liquid contracts in the world. You will not face liquidity issues here.

### 5D. Capital Efficiency

Kalshi requires you to post the full price of the contract as margin (no leverage). A $0.80 contract ties up $0.80. CME futures require margin of $500–$1,500 per contract on ~$5M notional — massively more capital efficient.

Kalshi's 4% interest on account balances partially compensates for the capital drag, but the asymmetry is significant. You need far more capital on the Kalshi side per unit of exposure.

### 5E. Settlement and Basis Risk

The biggest practical headache: Kalshi settles on the announced decision (a clean binary), while Fed Funds futures settle on the monthly average EFFR. If you get the direction right but the timing or magnitude is off, the two legs can move against you differently.

Additionally, the EFFR can deviate from the target range due to repo market dynamics, month-end effects, and reserve scarcity — none of which affect the Kalshi contract.

---

## 6. Data Sources and Monitoring Tools

To pursue this strategy, you'd want continuous monitoring of the probability divergence. Here are the data sources:

### Free

| Source | What You Get | Access Method |
|---|---|---|
| **Kalshi API** | Real-time order books, trades, contract prices for KXFED/KXFEDDECISION | REST + WebSocket, no auth needed for market data |
| **CME FedWatch Tool** | Meeting-by-meeting rate probabilities implied by futures | Web scraping (cmegroup.com) |
| **Atlanta Fed Market Probability Tracker** | SOFR-options-implied probability distributions | Web (updated daily) |
| **FRED API** | Effective federal funds rate, target range history | REST API (free key) |
| **Minneapolis Fed Market-Based Probabilities** | Alternative probability estimates | Web |

### Paid

| Source | What You Get | Cost |
|---|---|---|
| **CME DataMine** | Historical Fed Funds futures tick data, SOFR options | Varies (~$100–500/mo) |
| **Interactive Brokers API** | Live Fed Funds futures prices, execution | Commission-based |
| **Bloomberg Terminal** | WIRP function (rate probabilities), full SOFR surface | $24k/yr |
| **Refinitiv Eikon** | Similar to Bloomberg, lower cost | ~$12–22k/yr |
| **FinFeedAPI** | Aggregated prediction market data including Kalshi | Tiered pricing |

### Recommended Starter Stack

For a monitoring tool before committing to a trading pod:

1. **Kalshi API** (free) → poll KXFED contract prices every 5 minutes
2. **CME FedWatch** (scrape or use a proxy like growbeansprout.com) → extract implied probabilities
3. **FRED API** (free) → current EFFR for day-weighting calculations
4. Compare and log divergences. If you consistently see 3%+ divergences that persist for hours, the strategy has legs.

---

## 7. Recommendation and Next Steps

### Is This Worth Pursuing?

**Yes, as a monitoring/research pod first.** The theoretical foundations are sound — two markets pricing the same event with different participant bases, fee structures, and contract designs should produce periodic mispricings. The academic literature confirms that Fed Funds futures carry a risk premium that prediction markets may not fully embed, creating a structural wedge.

However, the edge is likely **small (2–5 percentage points of probability), episodic (concentrated around data releases and meeting dates), and difficult to hedge cleanly** due to the binary-vs-continuous payoff mismatch.

### Suggested Path

1. **Build a monitoring tool** (potential new pod) that continuously compares Kalshi KXFED probabilities to CME FedWatch probabilities, logging divergences with timestamps. This is a straightforward build using your existing pod framework + Kalshi API.

2. **Backtest the divergence** using historical Kalshi prices (available via API) and historical CME FedWatch data (harder to get — CME DataMine or scraping archives). Key question: how often does a 3%+ divergence appear, how long does it persist, and which side was "right" at settlement?

3. **Paper trade the arb** if the monitoring shows consistent opportunities. Start with the Kalshi-only side (buy/sell the mispriced Kalshi contract), since hedging on CME introduces complexity that may not be worth it at small scale.

4. **Consider the Kalshi-only angle.** If the monitoring reveals that Kalshi consistently misprices relative to FedWatch (e.g., Kalshi is systematically 3% too high on "cut" probabilities), you don't need the CME hedge at all — just fade the Kalshi bias directionally. This would be analogous to how P-001 uses Odds API consensus to find Kalshi mispricing in sports.

5. **P-004 revival.** Your pod architecture already has P-004 (ForecastEx-Kalshi Econ Arb) stubbed out. This strategy could fit naturally as a P-004 reactivation or a new pod in the same family.

---

## 8. Key Risks

- **Model risk:** The day-weighting adjustment and risk premium estimation are non-trivial. Small errors compound.
- **Liquidity risk:** Kalshi order books can thin out, especially for further-dated meetings.
- **Regulatory risk:** Kalshi's CFTC status is stable but the regulatory landscape for prediction markets continues to evolve (cf. state gaming law challenges).
- **Correlation risk:** In a crisis scenario, both markets could move in unexpected directions simultaneously.
- **Fee drag:** At small edge sizes (2–3%), Kalshi taker fees consume a significant portion of the profit.

---

## References

- Piazzesi & Swanson, "Futures Prices as Risk-Adjusted Forecasts of Monetary Policy" (Stanford)
- Fed Board FEDS Notes, "A Simple Macro-Finance Measure of Risk Premia in Fed Funds Futures" (2019)
- Fed Board FEDS Notes, "Front-End Term Premiums in Federal Funds Futures Rates" (2016)
- Bonini, "Watching the FedWatch" — Journal of Futures Markets (2026)
- CME FedWatch Tool User Guide
- Atlanta Fed Market Probability Tracker
- Kalshi API Documentation (docs.kalshi.com)
