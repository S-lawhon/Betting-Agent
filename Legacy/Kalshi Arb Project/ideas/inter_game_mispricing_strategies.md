# Inter-Game Mispricing Strategies for Kalshi Basketball Markets

## Overview

The existing betting agent infrastructure identifies edges on individual game moneylines by comparing Kalshi prices to sportsbook consensus. These strategies extend that approach to exploit **inter-game** and **inter-match** mispricing — inefficiencies that only become visible when you look across multiple related markets simultaneously.

---

## Strategy 1: Schedule-Adjusted Fatigue Arbitrage

**Core Insight:** NBA scheduling creates predictable fatigue patterns (back-to-backs, 3-in-4 nights, road trip legs, altitude changes) that sportsbooks price in reasonably well but Kalshi's thinner markets often don't.

**Mechanism:** When you see Game A on a Tuesday night, you simultaneously look at the same team's Wednesday game. If the books have already adjusted the Wednesday line for the back-to-back but Kalshi hasn't, you fade the tired team on Kalshi.

**What to Build:** A schedule-awareness layer that pulls the full NBA schedule, tags each game with fatigue indicators (rest days, miles traveled, timezone shifts), and cross-references the Kalshi price against the fatigue-adjusted fair value. The existing `odds_client.py` consensus already captures how books adjust for this — the key is being faster at identifying when Kalshi lags behind on the *second* game of a pair.

**Infrastructure Fit:** Slots into the existing Scanner loop. Add a "related games" lookup step after matching, so when evaluating Game X, also pull Game X+1 for that team and check if the spread between Kalshi and consensus is wider than usual.

**Edge Profile:** Medium frequency, medium edge per trade, moderate speed requirement.

---

## Strategy 2: Implied Probability Consistency Arbitrage

**Core Insight:** Kalshi lists multiple market types for the same sport — individual game winners, season win totals, playoff advancement, conference winners, MVP markets, etc. These all embed implied probabilities that should be internally consistent but often aren't.

**Mechanism:** If Kalshi prices the Celtics at 65% to beat the Knicks tonight, and 60% to beat the Pacers tomorrow, but their "Celtics to win 55+ games" season market implies a pace that requires winning ~70% of remaining games, there's a structural inconsistency. Trade the mispriced leg.

**What to Build:** A "probability graph" module that ingests all Kalshi basketball markets simultaneously, extracts the implied probability from each, and checks them for internal consistency using basic probability math. When the individual game markets imply a different season trajectory than the season-level markets, trade the outlier.

**Infrastructure Fit:** The existing `kalshi_client.list_markets()` already paginates through all markets. Add a second pass that groups markets by team and computes whether the individual game probabilities are consistent with the aggregate markets.

**Edge Profile:** Low-medium frequency, potentially large edge per trade, no speed requirement (structural, not time-sensitive).

---

## Strategy 3: Correlated Outcome Mispricing (Injury/News Propagation)

**Core Insight:** Basketball outcomes across games are correlated in ways Kalshi doesn't price. Key injuries, lineup changes, and coaching adjustments propagate across multiple games simultaneously, but Kalshi updates each market independently (and slowly, since liquidity is thin).

**Mechanism:** When a star player gets ruled out for "2-3 weeks," sportsbooks quickly adjust the next 8-10 games. Kalshi market-makers often update the next game but are slow on games 3-10 in that window. Race to trade those stale markets.

**What to Build:** A news/injury monitoring layer (NBA injury reports are published on a set schedule) that, upon detecting a significant status change, immediately scans all of that team's upcoming Kalshi markets and compares them to the already-adjusted sportsbook lines. The edge decays fast, so speed matters.

**Infrastructure Fit:** A separate "event-triggered" scanner that runs outside the normal 60-second polling loop. When it detects a trigger (injury report, trade, etc.), it fires immediately and evaluates all related markets in one burst.

**Edge Profile:** Low frequency, highest edge per trade, high speed requirement.

---

## Strategy 4: Line Movement Lag Exploitation

**Core Insight:** Sportsbooks are a leading indicator — their lines move first when sharp money comes in. Kalshi is a lagging indicator because it has less liquidity and fewer sophisticated participants.

**Mechanism:** Monitor real-time line movement on sportsbooks. When you see a significant move on a game, immediately check if the correlated Kalshi markets (not just that game, but other games involving the same teams, or games in the same time slot that share a "primetime attention" effect) have moved yet.

**What to Build:** A line-movement tracker that stores snapshots of both sportsbook odds and Kalshi prices over time. When the delta between a Kalshi price and the sportsbook consensus widens beyond a threshold (suggesting the books moved but Kalshi didn't), trade. The inter-game angle comes from checking whether movement on Game A causes stale pricing on Game B (same team, same night, conference rival, etc.).

**Infrastructure Fit:** The existing `odds_client.py` already caches odds with a 60-second TTL. Extend this to store a rolling history (even just the last 3-4 snapshots) and compute velocity of line movement. The scanner flags markets where Kalshi is lagging behind the direction of book movement.

**Edge Profile:** Medium-high frequency, medium edge per trade, high speed requirement.

---

## Strategy 5: Game-Cluster Portfolio Construction

**Core Insight:** Rather than trading individual games, construct portfolios of correlated Kalshi positions that hedge each other, expressing views on systematic factors rather than individual outcomes.

**Mechanism:** If the Western Conference is underpriced relative to the Eastern Conference on a given night, go YES on multiple Western Conference teams and NO on Eastern Conference teams. Individual game variance is high, but portfolio variance is lower because you're expressing a directional view on a systematic factor.

**What to Build:** A portfolio optimizer that sits on top of the existing `risk_manager.py`. Instead of evaluating each game independently, group tonight's games by factors (conference, division, home/away, rest advantage) and look for systematic tilts where Kalshi is mispricing the factor rather than the individual game. Kelly sizing applied to the portfolio, not each leg.

**Infrastructure Fit:** A new module between the Scanner and RiskManager. The Scanner identifies individual edges, then the "PortfolioConstructor" groups them, checks for correlation, and sizes the combined position.

**Edge Profile:** High frequency, small edge per trade (but compounding), no speed requirement.

---

## Strategy 6: Settlement Timing Arbitrage

**Core Insight:** Kalshi settles markets at specific times, and the settlement source/criteria may differ from what sportsbooks use. If a game's outcome is known before Kalshi's market settles, stale prices may persist on related markets.

**Mechanism:** If Game A finishes early and its result makes Game B's outcome more/less likely (e.g., playoff seeding implications, momentum narrative), trade Game B's Kalshi market before it adjusts.

**What to Build:** A real-time game score feed (NBA has free APIs for this) integrated with the settler. When a game ends, immediately scan for related markets that should move in response and check if Kalshi prices are still stale.

**Infrastructure Fit:** Extends the settler module with a "post-settlement scanner" that, after resolving Game A, evaluates all related markets for staleness.

**Edge Profile:** Low frequency, variable edge, moderate speed requirement.

---

## Prioritization Recommendation

| Strategy | Ease of Build | Edge Durability | Speed Required | Recommended Order |
|----------|--------------|-----------------|----------------|-------------------|
| 1. Fatigue Arbitrage | Easy (extends existing) | Medium | Moderate | **Start here** |
| 4. Line Movement Lag | Medium | Medium | High | Second |
| 2. Consistency Arb | Medium-Hard | High (structural) | None | Third |
| 3. Injury Propagation | Medium | High (per-trade) | Very High | Fourth |
| 5. Portfolio Construction | Hard | Medium | None | Fifth |
| 6. Settlement Timing | Medium | Low-Medium | Moderate | Sixth |

---

*Document created: February 2026*
*Project: Kalshi Basketball Inter-Game Mispricing Bot*
