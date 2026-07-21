# New Strategies — How They Work and Where the Edge Is

**Betting Pod Shop · Strategy walkthrough**
**Prepared July 21, 2026**

---

## Purpose and scope

This report walks through the strategies we've built and validated over the last few weeks and explains, for each one, the mechanism (why the mispricing exists), the measured edge (with confidence intervals, net of fees), and the discipline gating it toward real capital. It covers five things:

- **P-015 — Tennis Qualifier Favorites** (Kalshi, taker)
- **P-016 — Live MLB Maker** (Kalshi, in-play market-maker)
- **MLB Batter-Hits Cheap-YES** (Kalshi, taker satellite — the tradeable residue of the props research)
- **P-017 — Golf Top-N Taker** (Kalshi, pre-tournament)
- **P-017M — Golf Fade Maker** (Kalshi, in-tournament maker)

Everything here is running or staged in **paper mode**. No real orders have been placed. The numbers below are backtested or forward-collected edges measured against real Kalshi prices, and each strategy carries a pre-registered kill/promote rule so we don't repeat the P-013 mistake (a pod that lost ~$2,094 while its success criteria were still being argued after the fact).

### How to read the edge numbers

Edge is quoted as **cents per contract, net of fees**, because a Kalshi contract pays $1.00 and every strategy is a series of yes/no contract bets. A "+5¢/contract" edge means that, on average, each $1 contract we buy or sell returns five cents of profit after Kalshi's fee is taken out. Confidence intervals are bootstrapped and, where outcomes within one event are correlated (golf names in a tournament, MLB bets in a slate), **clustered by event or day** — so the interval reflects the real number of independent bets, not the inflated raw count. When an interval excludes zero, the edge is statistically distinguishable from noise at that sample size.

One theme runs through all five: **the edge lives where attention doesn't, and the maker side beats the taker side.** Every strategy is either a bet on a market that nobody is watching closely, or a bet that collects the spread instead of paying it.

---

## P-015 — Tennis Qualifier Favorites

**Venue:** Kalshi (ATP/WTA match-winner series) · **Style:** taker · **Status:** paper, pre-registered validation

### How it works

We buy heavy favorites — ask price between **0.85 and 0.985** — but only in **qualifying-round** matches, not the main draw. The pod (`qualifier_favorite_pod.py`) reads the Kalshi public API, filters to qual matches, sizes each position conservatively (`fair = ask + 0.025`, i.e. we only credit ourselves the bottom half of the measured edge), caps size at half the displayed ask depth, and holds at most six positions at once. Settlement comes straight from the Kalshi `result` field, with a 14-day auto-void to match Kalshi's postponement rule.

### Where the edge comes from

This started as a broad "tennis favorite-longshot bias" idea and got narrowed hard by the data. The favorite-longshot bias — favorites winning slightly more than their price implies — is real in a two-month window but **evaporates across the full 13-month Kalshi history**: taker EV on "buy any favorite" is negative at every price floor once you use the long sample. The market has tightened as volume grew.

What survived is a single pocket: **qualifying matches**. These are the least-watched contracts on the board — played before the tournament proper, no TV, no sharp closing line — and there the favorites are mispriced.

| Cohort | Trades | Hit rate | Net edge/contract | 95% CI |
|---|---|---|---|---|
| **Tour quals, ask ≥ 0.85** | 238 | 95.8% | **+4.1¢** | [+1.4, +6.3] |
| Tour quals, ask ≥ 0.90 | 130 | 98.5% | +3.6¢ | [+1.1, +5.3] |
| Tour main draw | 936 | 90.1% | −2.1¢ | [−4.0, −0.1] |

ATP carries the effect (the ATP-only slice is ~+5¢); WTA is positive but not significant on its own, so WTA runs at half size. Expected volume is only **~20 trades/month**, with the first real spike at US Open qualifying (Aug 17–21, 2026).

### Honesty flags

This is a **plausible-but-unconfirmed** edge, not a proven one. It was one of ~10 slices examined on 2026-only data with no holdout, so regression toward zero is the expected outcome. An out-of-sample test on Challenger/ITF qualifiers — the nearest comparable cohort — **came back negative (−2.0¢)**, which means the "quals are underpriced because nobody watches" mechanism doesn't cleanly replicate. Either tour quals are genuinely special for a reason we haven't isolated, or the original result is a surviving false positive.

Because of that, P-015 is under a **locked decision rule**: hard-kill if it goes significantly negative; no decision before 120 settled trades (~6 months); kill at 120 if edge ≤ 0; promote to live only at 240 trades with a 2σ positive result. Parameters cannot be tuned mid-flight (that resets the trade counter). The honest prior is that this kills at the 120-trade checkpoint rather than promoting.

---

## P-016 — Live MLB Maker

**Venue:** Kalshi (MLB moneyline + totals, in-play) · **Style:** market-maker · **Status:** paper pilot design

### How it works

Instead of chasing price after a scoring play, P-016 **posts resting two-sided quotes** around our own continuously-updated live win-probability model (Elo/moneyline base plus a game-state layer — inning, score, base/out) and collects the spread from in-play takers. Quotes are shaded by the favorite-longshot bias (lean toward being a net buyer of favorites, seller of longshots), pulled or re-priced around event risk (the at-bat resolving), and we prefer to let positions **settle** rather than trade out, which avoids the second fee leg entirely.

### Where the edge comes from

This is the conclusion of a large in-play research effort (207-agent deep-research pass plus reconciliation with three internal projects), and the finding was sharp: **the taker version doesn't work, the maker version does.**

In-play prediction markets genuinely misprice around game events — the June 2026 Kalshi NBA study (1,438 games, 409k contract-minutes) confirmed the market **underreacts**, moving only ~0.64-for-1 with the true win-probability change. But trading that drift as a taker is **unprofitable at every gap threshold** once you cross the bid-ask spread and pay the ~1.75¢/contract taker fee — the spread absorbs the entire edge. Four independent lines of evidence (two academic, two internal) converge on the same point: on Kalshi, the spread + fee kills takers, and the documented positive returns sit with **resting orders**. A separate Kalshi study measured **maker after-fee returns of +2.6% per contract** on ≥50¢ contracts, uncompeted because the books are thin.

The maker edge has three sources: the spread itself, the behavioral biases of in-play takers (who chase events), and Kalshi's fee structure — **makers pay zero-to-tiny fees** (designated series pay ¼ of the taker rate, ~0.44¢) versus ~1.75¢ for takers, and there's no settlement fee.

### What the pilot must prove

The edge is structural, but three things are genuinely unknown until we collect live fills, and they are the pilot's pre-registered gates:

1. **Fill rate** — is there enough in-play taker flow at our quoted prices?
2. **Adverse selection (markouts)** — where does the mid sit 1/5/15 min after each fill? The central risk is that our fills cluster at exactly the moments someone faster knew more. Paper fills are modeled **pessimistically** (we only count a fill when the market trades *through* our quote, never at the touch).
3. **Net spread capture** after maker fee vs. markout losses.

Success gate: positive markout-adjusted P&L over ≥500 fills, robust to dropping the best day, before any real-money discussion. It runs paper-only, MLB-only, no taker logic, alongside the existing pods.

---

## MLB Batter-Hits Cheap-YES (satellite)

**Venue:** Kalshi (`KXMLBHIT` props) · **Style:** taker · **Status:** paper, needs live execution sample

### How it works

Buy **cheap YES** on batter-hits props — priced **0.15–0.45** — but only on **fresh prices in the final ~30 minutes before first pitch**, and only in markets that are **actually trading** (a liquidity/activity filter, not an optimization). No forecasting model is involved; it's a price-and-timing filter.

### Where the edge comes from — and what we killed

We built the obvious thing first: a full batter-hits projection model (Log5 per-PA hit probability, empirical-Bayes shrinkage, lineup-slot PA distributions, strict no-lookahead). **We shelved it.** On identical rows the market's Brier score (0.2210) beat the model's (0.2236) at every strike, and the model had *negative* selection value — the bets it liked returned less than the bets it flagged to avoid. A public-box-score model carries no information Kalshi's prices don't already have.

What's left is a pure market-structure edge: **cheap YES on neglected prop books is systematically underpriced.** The mispricing is largest in the *least-contested* books (an inattention story, not an informed-flow story — we tested and refuted the worry that we'd be trading against sharps). The Phase 2 "+8.5¢" headline was inflated by stale prices; once you restrict to prices you could actually trade, the properly day-clustered edge is:

| Measure | Value |
|---|---|
| Net edge/contract (day-clustered, buy-at-ask, ≤30m, price 0.15–0.45) | **+5.5¢** |
| 95% CI | [+3.2, +7.8] |
| Daily net SD | 9.1¢ (27% of days negative) |
| Displayed depth at ask (median) | 784 contracts |

The edge is real and replicated across two windows and at real ask prints, but it is **small and capacity-limited** — realistic capture is on the order of **$10–35/day** at current prop liquidity. This is a satellite book worth running to validate execution mechanics and to be positioned if Kalshi prop volume grows, not a fund-scale allocation. The open question is no longer "is there an edge" but "can we get filled at these prices live" — which needs ~27 game-days of collector data (a droplet timer is now accumulating toward that).

---

## P-017 — Golf Top-N Taker

**Venue:** Kalshi (golf top-10 / top-20 finish props) · **Style:** taker · **Status:** built, backtested, wired, paper

### How it works

Before a tournament starts, buy **YES on top-10 and top-20 finish** contracts for mid-tier players — ask price in the **8–45¢ band**, entered **4–10 days before the market closes** (the "Wednesday" anchor), spread ≤6¢. The pod (`golf_topn_pod.py`) uses a conservative structural edge bump (`edge_bump = 0.04`, the lower-CI convention) and is depth-capped. It runs without any model input; an optional DataGolf feed refines *which names* to pick but does not create the edge.

### Where the edge comes from

Two mechanical/behavioral drivers, both visible in Kalshi's own settlement data:

1. **Tie/dead-heat inflation.** Top-20 markets actually settle **~22.7 YES per event vs the nominal 20** (+13% extra winning mass) because ties at the cut line all pay out. The contracts are structurally worth more than "20 of N."
2. **Attention concentration.** Retail money piles onto the stars, leaving the **mid-tier field structurally cheap** pre-tournament.

Backtest result across 10 tournaments (926 bets), replayed through a realistic execution model with correct series fees:

| Slice | Net edge/contract | 95% CI (event-clustered) |
|---|---|---|
| **Leg A (top-10/20, 8–45¢, 4–10d out)** | **+6.8¢** | [+3.1, +10.2] |
| top-20 only | +8.3¢ | [+3.6, +13.2] |
| top-10 only | +4.9¢ | [+2.3, +7.3] |
| top-5 (rejected) | +0.5¢ | [−0.7, +1.8] |

It's robust: **9 of 10 tournaments positive**, and band-insensitive (every band from 5–25¢ to 15–50¢ returns +6.6¢ to +7.4¢, so it's not a knife-edge fit). The one negative event was Travelers — a signature, heavily-watched field — which fits the attention story perfectly, so the pod initially **skips signature-event weeks**. top-5 straddles zero and is dropped.

### Honesty flags

Ten tournaments in H1-2026, outcomes correlated within each event, so the real sample size is the event count, not the bet count. The structural tie-inflation edge should persist; the behavioral inattention edge can decay as Kalshi's maker ecosystem professionalizes. Kill criterion: net edge below half the +6.8¢ baseline. Go/no-go after ~8 paper tournaments (each tournament = one independent observation).

---

## P-017M — Golf Fade Maker

**Venue:** Kalshi (golf top-10 / top-20 props, in-tournament) · **Style:** maker · **Status:** paper-only, hypothesis to confirm

### How it works

The mirror image of P-017, run in-tournament. We **rest offers (sell YES)** on top-10/20 names at **mid + 3¢**, during a specific window — **36 to 6 hours before close** (roughly Saturday through Sunday morning) — filling only on public prints that go strictly *through* our quote (the same pessimistic-maker convention as P-016). Golf prop series charge **zero maker fee**, so the whole spread is ours. It runs as a standalone engine (`run_golf_maker.py`), not in the main pod loop, with a kill-switch file.

### Where the edge comes from

Once a tournament is underway, the same top-N YES contracts that were cheap pre-tournament get **bid up** by in-play retail as favorites separate from the field — and by late in the event, that YES demand overshoots. Resting an offer above mid captures the overshoot plus the spread, fee-free.

| Window | Net edge/contract | 95% CI |
|---|---|---|
| **36→6h before close** | **+9.1¢** | [+4.5, +13.5] |
| 48→6h before close | +1.4¢ | — |
| 48→24h before close | −3.1¢ | — |

### Honesty flags

**Timing is everything, and this is the pod's whole story.** Fade too early (48→24h, i.e. Friday) and you *lose* −3.1¢ because YES is still being bid up. The edge is entirely in the Saturday-through-Sunday-morning window. The aggregate research had suggested a wider "12–48h" window worth ~+8¢; the realistic replay showed the naive version is break-even and would have bled — a good catch before it cost anything. The bigger caveat: **only 4 tournaments have the tick-level data** behind this, so even the significant CI rests on four events. Treat the +9.1¢ as a hypothesis to confirm with ~8–10 events of live paper fills, exactly as with P-016 — not a validated number yet.

---

## The through-line

Read together, these five strategies are variations on two findings that keep reappearing across every market we study:

**1. The edge lives where attention doesn't.** Tennis qualifiers, mid-tier golfers, neglected MLB prop books — every surviving taker edge (P-015, P-017, MLB hits) is a bet on a contract that nobody is watching closely. The moment attention shows up (main-draw tennis, signature golf events, actively-contested props), the edge disappears. This is why the honesty flags matter so much: an inattention edge is exactly the kind that decays as a venue's market-making professionalizes.

**2. The maker side beats the taker side.** On the same underlying inefficiency, paying the spread loses and collecting it wins. P-016 and P-017M are both built on this — Kalshi's fee structure (zero-to-tiny for makers, ~1.75¢ for takers, no settlement fee) tilts the economics decisively toward resting orders. The taker strategies survive only where the raw mispricing is large enough (6–7¢) to clear the fee, and even then only in unwatched books.

### Status and next steps

| Strategy | Style | Measured edge | Status | Gate to live |
|---|---|---|---|---|
| P-015 Tennis Quals | Taker | +4.1¢ [+1.4, +6.3] | Paper, pre-registered | 240 trades + 2σ (~Jul 2027); honest prior = kill at 120 |
| P-016 Live MLB Maker | Maker | +2.6%/ct (external), unproven for us | Paper pilot | ≥500 fills, positive markout-adjusted P&L |
| MLB Hits Cheap-YES | Taker | +5.5¢ [+3.2, +7.8] | Paper satellite | ~27 game-days live execution sample |
| P-017 Golf Top-N | Taker | +6.8¢ [+3.1, +10.2] | Wired, paper | ~8 paper tournaments, edge > half baseline |
| P-017M Golf Fade Maker | Maker | +9.1¢ [+4.5, +13.5] | Paper-only | ~8–10 events of live fills |

Every one of these is deliberately gated behind a paper-validation period with a pre-committed kill rule. The discipline is the point: we're collecting the forward evidence to tell a real edge from a backtest artifact *before* any capital is at risk, and the honest expectation is that some of these (P-015 most likely) will be killed rather than promoted. That's the system working as designed.
