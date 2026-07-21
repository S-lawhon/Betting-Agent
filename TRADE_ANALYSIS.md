# Betting Pod Shop — Trade Analysis Report

**Period:** March 4–7, 2026 (3 trading days)
**Generated:** March 7, 2026

---

## Executive Summary

The system has placed **41 settled trades** across two pods, generating **$1,707.05 in cumulative P&L** on $2,734 wagered (62.4% ROI). P-001 (Kalshi) is the clear outperformer with a 76.9% win rate and 148% ROI. P-006 (Polymarket) is marginally profitable but has a **critical matching problem** — roughly 22% of trades are matched to the wrong Polymarket market, and 143 trades remain open (many likely unmatchable for settlement).

**Headline numbers:** 25W / 16L (61% win rate), $1,707.05 P&L, $234 max drawdown, 8.91 profit factor on P-001.

---

## Combined Performance

| Metric | Value |
|--------|-------|
| Total Settled | 41 (25W / 16L) |
| Win Rate | 61.0% |
| Total P&L | $1,707.05 |
| Total Wagered | $2,734.21 |
| ROI | 62.4% |
| Max Drawdown | $234.14 |
| Peak P&L | $1,707.05 (at close) |
| Trading Days | 3 |
| Profitable Days | 3/3 (100%) |

---

## Pod Comparison

### P-001: Kalshi Moneyline Value

| Metric | Value |
|--------|-------|
| Record | 10W / 3L (76.9% WR) |
| P&L | $1,481.01 |
| Wagered | $998.19 |
| ROI | 148.4% |
| Avg Win | $160.15 |
| Avg Loss | -$40.28 |
| Largest Win | $817.74 (Clippers vs Pacers, NO side) |
| Largest Loss | -$58.90 (Winthrop vs Charleston Southern) |
| Voided | 0 |
| Open | 0 |

**P-001 is performing extremely well.** The edge calculator is finding real value — 10 of 13 trades have been winners. The NO side in particular has been very profitable (4W/1L, +$1,359), suggesting the system is successfully identifying overpriced underdogs on Kalshi.

### P-006: Sportsbook-Polymarket Consensus

| Metric | Value |
|--------|-------|
| Record | 15W / 13L (53.6% WR) |
| P&L | $226.04 |
| Wagered | $1,736.02 |
| ROI | 13.0% |
| Voided | 62 |
| Open | 143 |

**P-006 is marginally profitable but has serious issues.** While it generates more volume (233 trades placed vs 13 for P-001), only 28 have settled and 143 remain open. The matching system is producing false matches against wrong Polymarket markets.

---

## P-006 Performance by Sport

| Sport | Record | P&L | ROI |
|-------|--------|-----|-----|
| NBA | 2W / 1L | +$211.56 | 94.0% |
| NHL | 2W / 0L | +$178.75 | 119.2% |
| ATP Tennis | 7W / 6L | +$2.88 | 0.4% |
| WTA Tennis | 4W / 4L | -$75.11 | -15.5% |
| MLB | 0W / 2L | -$92.04 | -100.0% |

NBA and NHL are the clear P-006 winners. Tennis is essentially break-even. MLB has been a total loss.

---

## Critical Issue: P-006 Match Quality

**This is the most important finding in this analysis.**

The fuzzy matcher (threshold: 55) is producing incorrect matches between Odds API games and Polymarket markets. Examples from the trade log:

- **Event:** Winnipeg Jets vs Chicago Blackhawks → **Matched to:** "Canucks vs. Blackhawks" (wrong — only one team matches)
- **Event:** Calgary Flames vs Dallas Stars → **Matched to:** "Flames vs. Capitals" (wrong opponent)
- **Event:** Anaheim Ducks vs Colorado Avalanche → **Matched to:** "Avalanche vs. Blackhawks" (wrong opponent)
- **Event:** San Francisco Giants vs New York Yankees → **Matched to:** "Minnesota Twins vs. New York Yankees" (wrong opponent)

**Impact:**
- ~22% of sampled P-006 trades are matched to the wrong Polymarket market
- 47 out of 143 open trades have weak/no team overlap with the actual Polymarket question
- When a trade is placed against the wrong market, the edge calculation is meaningless — you're betting on a game whose odds have nothing to do with the detected edge
- 62 trades were already voided from a prior batch of bad matches (Biden COVID market)

**Root cause:** The fuzzy score threshold of 55 is too low. A single shared team name (like "Blackhawks") can produce a 55-60 score even when the other team is completely different. The matcher needs to verify that **both** teams match, not just one.

---

## P-001 Edge Analysis

All 13 P-001 trades fell into different edge brackets:

| Edge Range | Trades | Record | P&L |
|------------|--------|--------|-----|
| 3-10% | 6 | 4W/2L | +$41.52 |
| 10-20% | 4 | 3W/1L | +$495.47 |
| 20-25% | 1 | 1W/0L | +$265.99 |
| 68%+ | 1 | 1W/0L | +$817.74 |

The outlier Clippers trade (68% edge, $817 win) is worth investigating — an edge that large either indicates a truly mispriced market or a potential data issue.

---

## P-006 Edge Analysis

| Edge Range | Trades | Record | P&L |
|------------|--------|--------|-----|
| 2-5% | 6 | 3W/3L | +$5.17 |
| 5-10% | 11 | 6W/5L | +$24.69 |
| 10-20% | 7 | 4W/3L | +$247.18 |
| 20-50% | 4 | 2W/2L | -$51.00 |

The 10-20% edge bracket is the sweet spot for P-006. The 20-50% bracket actually lost money — very large edges on prediction markets often indicate either stale prices or a matching error.

---

## Equity Curve Summary

The equity curve shows three distinct phases:

1. **March 4 (Day 1):** P-001 settled 5 trades with a net +$1,089.56. Strong start driven by the Clippers win (+$817) and Knicks win (+$265).

2. **March 5 (Day 2):** P-006 trades began settling. The curve went choppy — the system hit a max drawdown of $234 as P-006 tennis trades split roughly 50/50. Ended day at +$1,315.

3. **March 6 (Day 3):** P-001 trades from the March 6 cycle settled, including the Cobolli tennis win (+$321). Recovered to new highs at +$1,707.

The $234 max drawdown (13.7% of peak) is acceptable for early paper trading but worth monitoring as volume scales.

---

## Recommendations

### 1. Fix P-006 Fuzzy Matching (CRITICAL)

The single most impactful improvement. Options:

**A. Raise fuzzy threshold to 75+** — This would eliminate the worst mismatches but may reduce volume.

**B. Require both teams to match (recommended)** — Instead of just a fuzzy score on the full string, verify that both teams from the Odds API event appear in the Polymarket question. A simple approach: extract team names from both sides and require a minimum overlap of 2 team identifiers.

**C. Add a validation step** — After fuzzy matching, do a secondary check: parse the Polymarket question for team names and compare against the Odds API event. Reject any match where a team doesn't appear in both.

### 2. Void or Review the 143 Open P-006 Trades

88 of the 143 open trades are NHL games, many of which were placed against wrong markets. These should either be bulk-voided or reviewed individually. A script could compare each open trade's `event` against its `polymarket_question` and auto-void any where the teams don't overlap.

### 3. Consider Disabling P-006 MLB

MLB is 0W/2L with -100% ROI. The spring training schedule may be creating matching confusion (teams playing in unusual configurations). Consider disabling MLB in P-006 until the regular season starts, or at minimum raising the edge threshold for MLB.

### 4. Add Odds API Scores-Based Settlement for P-006

Currently P-006 relies on Gamma API resolution which can lag hours behind game completion (as seen with the Venus Williams trade). Porting the P-001 settler's Odds API scores logic to the P-006 settler would resolve trades faster and provide a fallback when Gamma is slow.

### 5. P-001 Position Sizing

P-001's position sizes range from $18.79 to $150. The Kelly fraction appears to be working (larger positions on larger edges), but the max position cap of $150 may be leaving money on the table for high-confidence trades. Consider raising to $200-250 as the track record builds.

### 6. P-006 WTA Tennis Edge Threshold

WTA is 4W/4L with -15.5% ROI on P-006. The edge threshold may need to be higher for women's tennis where prediction market pricing tends to be less liquid and more volatile. Consider raising the minimum edge for WTA from 1% to 5%.

### 7. Monitor the P-001 68% Edge Outlier

The Clippers trade with a 68% edge is anomalous. While it was a win (+$817), an edge that large typically means either: (a) the Kalshi market was extremely stale, or (b) there was a data issue in the edge calculation. Track whether similarly large edges continue to produce winners or if they're noise.

---

## Open Items

| Item | Priority | Status |
|------|----------|--------|
| Fix P-006 fuzzy matching | CRITICAL | Not started |
| Void/review 143 open P-006 trades | HIGH | Not started |
| Deploy portfolio analytics dashboard | MEDIUM | Code written, not deployed |
| Add Odds API settlement to P-006 | MEDIUM | Suggested, not implemented |
| Disable P-006 MLB | LOW | Evaluate after fixing matcher |
| Raise P-006 WTA edge threshold | LOW | Evaluate after fixing matcher |
