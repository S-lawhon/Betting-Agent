# Phase 2 — Strategy Backtest & Out-of-Sample Validation

**Date:** 2026-07-20
**Purpose:** Turn the Phase 1 "maker edge in batter props / team totals" finding into a deployable strategy, and stress-test it against the report's own caveats (single-window, overfitting, average-maker-vs-real-quoter).
**Data:** two non-overlapping trade windows — in-sample days 0–45 (18.8M trades) and out-of-sample days 45–90 (8.5M trades) — plus a rerun maker-economics replication. Code: `backtest_maker.py`, results in `data/maker_backtest_{insample,oos}.csv`.

---

## Headline: the market-making strategy was overfit; the real edge is directional and concentrated in HITS props

The disciplined out-of-sample test changed the conclusion. Two findings:

1. **The mechanical symmetric maker quoter does NOT survive out-of-sample.** A rule-based quoter (EWMA-of-trades fair, symmetric ±k quotes, trade-through fills, jump-pull) was positive in-sample but **only 4 of 18 configurations stayed positive in both windows** — and the configs with the *highest* in-sample P&L flipped most negative out-of-sample (the classic overfitting signature). Do not deploy it.

2. **The underlying inefficiency is real, persistent, and directional — a systematic underpricing of cheap YES strikes in batter HITS props.** Buying YES on hits-prop strikes priced 0.15–0.45 and holding to settlement returns **+8.4¢/contract in-sample and +8.0¢/contract out-of-sample** — nearly identical across windows, and it survives even a punitive 3¢ execution-cost assumption (+6.9¢ / +6.5¢). This is the deployable edge.

---

## Why the maker strategy failed but the edge is real

The average-maker economics **replicated** out-of-sample (pregame ¢/contract): total bases +2.6 (was +3.8), HRR +2.1 (was +2.9), team totals +1.4 (was +4.0), hits +0.4 (was +2.9), strikeouts **−1.5** (was −1.4). The average resting order still beats the flow that hits it.

But a *mechanical* quoter anchored on trailing trade prices cannot capture that average. When the fair is an EWMA of past prints, a trending or news-driven market runs through the stale side of the quote before the anchor catches up — so the high-fill, thin-spread configs get adversely selected exactly where they trade most. **The winning maker is not quoting off trailing trades; they have a real fair-value model.** This is a direct, empirical argument for the fund's model-first thesis: the batter-prop pool is soft, but harvesting it as a maker requires a genuine prop-pricing model, not a trailing anchor.

The directional strategy sidesteps this: it doesn't try to earn the spread symmetrically, it takes a side the market is systematically wrong about.

## The HITS cheap-YES edge, by series and execution assumption

Net ¢/contract after taker fee + assumed cost above last trade, buying YES on strikes in [0.15, 0.45], held to settlement:

| Series | 1¢ cost IS | 1¢ cost OOS | 3¢ cost IS | 3¢ cost OOS | Verdict |
|---|---|---|---|---|---|
| **Hits** | **+8.4** | **+8.0** | **+6.9** | **+6.5** | Robust — deploy |
| Total bases | +0.6 | +4.2 | −0.9 | +2.7 | Marginal, cost-sensitive |
| HRR | +1.2 | +0.3 | −0.3 | −1.2 | Not robust |
| Team totals | −3.5 | +0.3 | −5.0 | −1.2 | Fails |
| Pooled | +0.9 | +3.1 | −0.6 | +1.6 | Driven by hits |

The edge is a **reverse favorite-longshot bias** concentrated in hits: retail systematically sells "player won't get his hits" (buys NO / unders), depressing cheap YES strikes below fair. Unlike classic FLB, this makes the *longshot* side underpriced — an anomaly, and a behaviorally plausible, persistent one. Roughly 40 qualifying markets per day.

## Controls & robustness

- **Strikeout control confirmed:** KS directional/maker P&L stays weak-to-negative in both windows (−1.5¢ maker pregame OOS) — the sharp-flow pool, as expected.
- **Two non-overlapping windows, pre-registered configs:** no per-series cherry-picking after seeing OOS; the comparison table is the full config grid.
- **Cost-robust:** the hits edge survives a 3¢ execution penalty (double the observed near-game half-spread), so it is not an artifact of optimistic fills.

## Recommended Phase 3

1. **Build a batter-hits prop model** (per-batter hit-probability given matchup, park, weather, lineup slot) and trade the cheap-YES tail where the market's implied is systematically low. This is the concrete, validated model to build — narrower and better-supported than the original "quote all batter props" plan. It shares data infrastructure with the moneyline model (lineups, park factors, pitcher quality).
2. **Paper-deploy directionally, not as a symmetric maker.** Enter YES on qualifying hits strikes; the maker-vs-taker execution question (rest inside the spread vs cross) is secondary to being on the right side.
3. **Shelve the mechanical MM quoter.** Revisit only with a real fair-value model as the anchor — at which point it becomes model-first quoting, not trailing-anchor quoting.
4. **Correlation overlay stays a Phase 4 idea**, pending websocket-grade book data — the measured lags are real but underpowered at 5-min sampling.

## Caveats

Still two windows within one 2026 season regime — persistence across seasons is untested. The hits edge is measured at last-pregame-trade prices with a synthetic spread penalty, not against a live book with real depth limits; thin batter-prop depth (few hundred contracts) caps per-market size. "Hold to settlement" ignores the option to exit early. The behavioral driver (retail under-betting) is inferred, not proven, and could erode if prop liquidity/sophistication grows. A second-season out-of-sample check and a live paper run against real order-book depth are the gates before real capital.
