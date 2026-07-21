# Phase 3 — Batter-Hits Model: Build, Validation, and a Downgraded Edge

**Date:** 2026-07-20
**Purpose:** Build the batter-hits prop model Phase 2 recommended, and test whether it beats the market and beats the naive "buy every cheap YES" rule (+8.5¢ baseline).
**Data:** 133,150 batter-game lines and 54,223 pitcher lines from MLB StatsAPI (2024–2026), joined to 10,332 settled Kalshi `KXMLBHIT` markets with pregame trades across both trade windows.
**Code:** `pull_batter_logs.py`, `hits_model.py`, `validate_hits_model.py`, `diagnose_hits_edge.py`.

---

## Two findings, both negative for the original plan

1. **The model does not beat the market.** On identical rows, market Brier 0.2210 vs model 0.2236 — the market is better at every strike (1+, 2+, 3+). Worse, the model has **negative selection value**: bets it likes returned +7.73¢ while bets it flagged to *avoid* returned +9.04¢. Filtering harder made results worse monotonically (+7.7¢ → +7.6¢ → +5.2¢ → −0.2¢ as the edge threshold rose). A log5 model built on public box-score aggregates carries no information Kalshi's hits prices don't already have.

2. **The +8.5¢ cheap-YES edge from Phase 2 was inflated by stale prices.** It is real but roughly **half the size** once entries are restricted to prices you could actually have traded.

---

## The model (for the record)

Standard, defensible construction:
- **Per-PA hit probability** via Log5 odds-ratio: batter hit rate/PA × opposing-starter hits allowed/BF ÷ league rate, park-adjusted.
- **Rates** empirical-Bayes shrunk toward league (K=150 PA batters, K=300 BF pitchers), built from prior-season totals (decayed 0.5) plus current season to date.
- **PA distribution** by lineup slot and home/away, estimated from prior seasons only.
- **P(H≥N)** = Σ_pa P(PA=pa)·P(Binom(pa,p) ≥ N).
- **Strict no-lookahead**, verified: cumulative stats exclude the current game (spot-checked row by row).

The model is well calibrated *in isolation* — 2+ hits: 21.8% predicted vs 21.5% realized; 3+ hits: 4.2% vs 4.6%. It is simply not sharper than the market.

## Data integrity check

Kalshi settlement agrees with independently pulled box-score hit counts in **99.77%** of 9,169 joined markets (21 disagreements out of 9,169). Ticker parsing, player-code construction (incl. Kalshi's non-ASCII dropping: Nuñez→NUEZ, García→GARCA), and strike semantics are all confirmed correct.

## Why the Phase 2 number was too high

Phase 2 priced entries at the **last pregame trade**, which in a thin prop book can be hours old. Edge by age of that entry price (cheap-YES 0.15–0.45, net of fee + 1.5¢):

| Entry price age | n | Net |
|---|---|---|
| 5–30 min before pitch | 2,025 | **+5.00¢** |
| 30–60 min | 533 | +10.04¢ |
| 1–2 h | 360 | +19.59¢ |
| 2–4 h | 165 | +13.20¢ |
| >8 h | 24 | +19.71¢ |

The apparent edge grows with staleness. Importantly, this is **not** price drift — for the 1,421 markets that traded both >2h out and ≤1h out, mean price moved +0.05¢ (essentially zero) and the edge was ~+1.2¢ at *both* prices. The staleness pattern instead reflects **which markets go stale**: the thinnest, least-contested books carry the largest apparent mispricing, and those are precisely the ones where the last trade is not a price you can get size at.

Edge by market activity tells the same story:

| Sample | Net edge |
|---|---|
| All cheap-YES, any entry age (Phase 2 basis) | +8.52¢ |
| Fresh entry (last trade ≤30m before pitch) | +5.00¢ |
| Actively traded (traded both >2h and ≤1h out) | **+1.22¢** |

## Honest tradeable estimate

Fresh entries only (last trade ≤30 min before first pitch), split by window:

| Window | n | Price | Hit rate | Net @1.5¢ cost | Net @3¢ cost |
|---|---|---|---|---|---|
| In-sample (0–45d) | 1,270 | 0.276 | 0.360 | **+5.55¢** | +4.05¢ |
| Out-of-sample (45–90d) | 781 | 0.255 | 0.320 | **+3.66¢** | +2.16¢ |

Positive in both windows and at both cost assumptions — the edge survives, but it is **+2 to +5.5¢**, not +8.5¢, and it is **declining between windows**, which may be noise or may be early decay as prop liquidity grows.

## Capacity — the binding constraint

Across 2,051 qualifying markets, the final 30 minutes see a **median of 86 contracts** traded (p25=21, p75=224). At a 5¢ edge, the median market offers roughly **$4.30 of theoretical edge**. Total flow across 90 days is ~447K contracts ≈ $22K of theoretical edge — but that assumes capturing *all* opposing flow, which is impossible; you would be a large fraction of the book and would move the price. Realistically capturing 5–15% implies **~$10–35/day**.

That is materially below the "low hundreds of $/day" projected in Phase 1, and it is the number that should govern any capital decision.

## Verdict

- **Do not deploy the hits model.** It adds nothing over market prices and actively degrades selection. Shelve it.
- **The residual edge is a price filter, not a model:** buy cheap YES (0.15–0.45) on *fresh* prices near first pitch. Real, replicated across two windows, cost-robust — but small and capacity-limited.
- **Recommended next step is a live paper execution test, not more modeling.** The open question is no longer "is there an edge in the data" but "can you actually get filled at these prices at game time." Everything so far is measured against *trade prints*, never against a live book with real depth. Run the existing collector's order-book data against the strategy to measure realistic fill availability at the ask.
- **Reset expectations for the satellite book:** this is a ~$10–35/day paper strategy at current liquidity, worth running to validate execution mechanics and to be positioned if Kalshi prop volume grows — not a fund-scale allocation.

---

# ADDENDUM — Phase 3b: The Execution Test (2026-07-20)

Two tests were run to price entries at a genuinely executable ask rather than at trade prints. **They disagree, and the disagreement is the finding.**

## Test A — live order book, 2 days (n=170)

From the collector's snapshots, every `KXMLBHIT` market's last observation within 30 min of first pitch, buying at the displayed ask:

- A tradeable ask was displayed **100%** of the time; median displayed size at the ask **784 contracts**; median spread 4.0¢.
- The ask sat only **+0.62¢ mean / +0.00¢ median** above the last trade — so the trade-print basis was *not* wildly optimistic on price level.
- **Net: −4.64¢/contract** (95% CI −10.78 to +1.50¢), realized hit rate 0.212 vs mean ask 0.245.

## Test B — historical, 90 days, using true ask prints (n≈2,000)

A trade with `taker_side='yes'` is by definition someone lifting the offer, so **its price is the ask**. This prices entries at real, filled ask prices across the full dataset (net of fee, no synthetic cost — the ask *is* what was paid):

| Window | Entry | n | Price | Hit | Net (95% CI) |
|---|---|---|---|---|---|
| In-sample | ≤30m buy-at-ask | 1,197 | 0.276 | 0.354 | **+6.40¢** ±2.71 |
| Out-of-sample | ≤30m buy-at-ask | 726 | 0.254 | 0.318 | **+5.16¢** ±3.37 |
| In-sample | ≤2h buy-at-ask | 1,621 | 0.273 | 0.376 | +8.89¢ ±2.36 |
| Out-of-sample | ≤2h buy-at-ask | 1,199 | 0.251 | 0.344 | +7.93¢ ±2.69 |

**Positive and statistically significant in both windows at genuinely executable prices.** The ≤30m figures (+6.4¢ / +5.2¢) are the clean ones; the ≤2h numbers remain partly stale-inflated per the staleness analysis above.

## Reconciling them — the actionable distinction

The two tests select **different populations**:

- Test A selects on a *displayed ask in range*, including markets nobody trades. In a wide, uncontested book the ask sits above fair, so you overpay.
- Test B selects markets where someone *actually transacted at the ask* — books tight enough to attract real flow.

So the rule matters enormously: **"buy at the ask in actively-trading markets near first pitch" is supported; "scan the book and lift any ask in 0.15–0.45" is not.** A liquidity/activity filter is not an optimization — it is load-bearing.

Test A is also **badly underpowered and should not be treated as a refutation**: 170 markets drawn from only 2 days (~30 games) are heavily clustered, so the true confidence interval is far wider than the naive ±6.1¢, and its low realized hit rate (0.212) is well within day-to-day game-script noise.

## Revised verdict

- The cheap-YES hits edge **survives pricing at real ask prints**: +5.2 to +6.4¢ in the final 30 minutes, significant in both windows. This is a modest *upgrade* over the main report's +3.7 to +5.5¢, which double-counted cost by subtracting a synthetic spread on top of trade prices that already embedded it.
- **Capacity may be better than estimated:** median *displayed* size at the ask was 784 contracts, versus the 86-contract traded-volume proxy used above. Displayed depth is the more relevant number for a taker.
- **Required before any capital:** 2–3 weeks of collector data for a properly powered, multi-day live execution sample, with the activity filter applied and paper fills logged. Two days cannot settle this.
- The model remains shelved; none of this depends on it.

---

# ADDENDUM 2 — Phase 3c: Re-examining the negative live result (2026-07-20)

Three things were tested. **Two corrections to the addendum above resulted.**

## Correction 1 — the live negative is ONE day, not two samples

The addendum described the live order-book test and the paper ledger as separate negative results. They are not independent: the entire settled live sample is **a single day (2026-07-19, n=170)**; July 20's markets had not settled at fetch time, and the 23-entry paper ledger is a *subset of that same day*. One day = one cluster = zero degrees of freedom. **It carries no statistical weight whatsoever** and should not have been presented as corroborating evidence.

## Correction 2 — the informed-flow hypothesis is REFUTED

The worry was that pricing entries off `taker_side='yes'` prints conditions on *informed traders buying YES*, so the measured edge would be theirs, not ours. Testing hit rates by type of late flow, matched on **pregame** price (an earlier version of this test used settled `last_price` and was invalid — winners trade toward $1.00 and get excluded by the price filter, leaving mostly losers, producing absurd ~1.5% hit rates):

| Late flow in market | n | Pregame price | Hit rate | Bias |
|---|---|---|---|---|
| Late BUY only | 1,730 | 0.266 | 0.343 | **+7.7pp** |
| No late trade | 1,121 | 0.252 | 0.422 | **+16.9pp** |
| Late SELL only | 122 | 0.264 | 0.393 | +13.0pp |
| Both | 173 | 0.288 | 0.341 | +5.3pp |

If informed YES-buying drove the edge, buy-conditioned markets would show the *largest* bias. They show the **smallest**. The mispricing is greatest in the most *neglected* books — an inattention story, not an informed-flow story. The +7.7pp gross on actively-traded markets maps directly onto the +6.4¢ net figure, confirming that number comes from the **conservative** subgroup. (The +16.9pp on never-traded books is partly the staleness artifact and is not executable.)

## The properly day-clustered estimate — the headline number

Clustering by game-day, which is the correct unit of independence (59 qualifying days, buy-at-ask, ≤30m window, price 0.15–0.45):

- **Mean +5.51¢/contract, clustered SE 1.19¢, 95% CI +3.18¢ to +7.84¢**
- Daily net SD **9.13¢**; **27% of days are negative**; deciles of daily net: −5.7 / −0.2 / +5.0 / +10.1 / +15.5¢

The edge survives correct clustering. This is the number to quote.

## Where July 19 sits

- A day at or below −4.64¢ occurs **12% of the time** historically.
- A day with hit rate ≤0.212 occurs **7% of the time**.

July 19 was an unremarkable bad day, comfortably inside normal variation. It is not evidence against the edge.

## Power requirement

At a daily net SD of 9.13¢, detecting a +5¢ edge at 80% power requires **~27 game-days**. The droplet collector (systemd timer, 10:00 ET daily) is now running toward that target; ~4 weeks of slates.

## Net effect on the verdict

The edge is **stronger and better-established** than the main report concluded: +5.51¢ [+3.18, +7.84] properly clustered, with the informed-flow objection tested and refuted. What remains genuinely unresolved is **live execution** — whether displayed asks can be lifted at these prices at scale — and that needs the full 27-day sample, not the single day currently in hand.

---

## Caveats

The model-vs-market comparison sample conditions on the batter having started (box-score rows only exist for players who played), which inflates realized rates for model *and* market alike; the apples-to-apples comparison on identical rows is unaffected and still favors the market. Edge estimates use last-trade prices with a synthetic 1.5–3¢ execution penalty, not observed asks. Two windows within one 2026 season; no cross-season validation. The IS→OOS decline (+5.5¢→+3.7¢) is unexplained and should be monitored. Capacity figures assume the observed final-30-minute flow is the addressable pool.
