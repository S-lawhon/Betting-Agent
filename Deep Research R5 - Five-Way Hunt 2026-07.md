# Deep Research R5 — Five-Way Opportunity Hunt

**Betting Pod Shop — Kalshi Fund Project**
**Run:** overnight 2026-07-26 → 27 · five parallel hunts, live public Kalshi API
**Verdict:** **5 hunts, 5 KILLs, 0 candidates advanced** — and the most useful result this project has produced in weeks.

> All work read-only against the public API. No orders, no authenticated endpoints, nothing deployed.

---

## 0. Read this part if you read nothing else

Five independent hunts all died. That is not the finding. **The finding is that we now know why everything dies, and the previous explanation was wrong.**

The house law said: *the mechanic was real but smaller than the tick and the spread.* Hunt E showed the binding constraint is neither the tick nor the spread — **it is the fee**, and the fee has a shape we have never exploited deliberately:

```
Kalshi fee = ceil(0.07 · contracts · P · (1−P))
```

That is a **parabola maximised at P = 0.50** (1.75¢/contract — 1.75× the tick) and collapsing at the extremes (**0.33¢ at P = 0.95**). Coin-flip markets are the most expensive region on the exchange, and the cheapest region is the tails.

Now look at our three survivors:

| Survivor | Where it trades | Fee at that price |
|---|---|---|
| **P-015** tennis qualifier | buys asks 0.85–0.975 | ~0.33¢ |
| **P-017** golf top-N | buys cheap props | ~0.4–0.7¢ |
| **P-022** round-leader fade | sells 3–12¢ names | ~0.4–0.7¢ |

**All three live in the tails. Every one of them.** Nobody chose that — it was never written down as a selection criterion. It is the single most actionable thing in this report, and it reframes the whole hunt.

---

## 1. What each hunt did and found

| Hunt | Question | Verdict | Decisive number |
|---|---|---|---|
| **A** | Do newly-listed series carry a P-015-style inattention pocket? | **KILL** | New series are *worse*: 6¢ vs 4¢ median spread, $115 vs $214 depth; **0 monotonicity violations in 13,507 strike pairs** |
| **B** | Do two series ever resolve the same fact under different rulebooks? | **KILL** | 197 divergent groups found, 4 verbatim rule conflicts — but **0 are simultaneously live on the same underlying** |
| **C** | Anything in the 7 categories four rounds never touched? | **KILL** | Mentions books converge to **exactly 1.00¢** from truth by T−15min; Health and Transportation are **100% dormant** |
| **D** | The three seasonal leads queued and never run | **3 KILLs** | F1 "outside the top" markets: **0 of 550 ever listed**; NCAAF target band median spread **27¢** |
| **E** | Invert it — do *liquid* markets carry structural defects? | **KILL** | Defect density *rises* with liquidity, but **0 of 2,069 mutex families have positive net** |

### The five best individual findings

**A — the inattention hypothesis is inverted, not merely unsupported.** Brand-new series are measurably worse on every friction axis, and the 0–7-day cohort is **53.7% one-sided** — no counterparty at all. Of 18,886 new-series markets the book calls near-certain (bid ≥ 0.95), **0.0%** offer ≥5¢ of upside. The P-015 shape simply does not occur.

**B — a new screen that kills an entire category in one query.** Divergent rulebooks are abundant in the documents but **generational**: a legacy series and its `KX`-prefixed successor, where the old one has expired. Where both are live they are deliberately disjoint (quarterly vs annual, one speech vs one month). *A settlement-rule divergence is only tradeable if both rulebooks are simultaneously live on the same underlying with overlapping windows. Measured incidence across 12,187 series: **zero**.* Run that before reading a single PDF.

**C — Mentions is the anti-survivor.** 462 settled mention markets, notional-weighted error from truth: 16.5¢ at T−120min → **1.00¢ at T−15min**. The book is pinned exactly one tick from truth before close, and the 99 is a *bid* with no ask behind it. One well-policed rulebook, converging perfectly.

**D — the transferable object from P-015 was misidentified.** On NCAAF, attention and liquidity are *perfectly correlated*: the target band has a 27¢ median spread and the genuine backwater families quote 87–93¢. P-015 did not work because tennis qualifying was low-attention. It worked because it was **low-attention *despite* having a maker-supported book** — a far rarer thing, and the reason the analogue doesn't transfer.

**E — liquid rulebooks are the richest on the exchange, and are read.** The "liquid markets have simpler rules" hypothesis is false; defect density *rises* with liquidity. The proof they get read: `CONTROLS` mid sits at **0.445** against the ladder-implied P(D≥51) of **0.445** — exact to the tenth of a cent, and nowhere near the naive P(D≥50) = 0.61. Someone priced the VP tiebreak from the PDF.

---

## 2. Correction to Hunt E — one load-bearing claim is wrong

Hunt E concluded the mutex/partition trade is **"structurally impossible"** via:

> fee = 0.07(1 − ΣPᵢ²) **≥** 0.07(1 − 1/n) → floor of 3.50¢ (n=2) rising to 6.30¢ (n=10)

**The inequality is backwards, and I verified it numerically.** ΣPᵢ² is *minimised* at 1/n (uniform) and *maximised* near 1 (degenerate), so 0.07(1 − 1/n) is a **CEILING on the fee, not a floor**:

| Partition | Total fee |
|---|---|
| n=2 uniform [.5, .5] | 3.50¢ |
| n=10 uniform | 6.30¢ |
| **n=2 skewed [.97, .03]** | **0.41¢** |
| **n=10 skewed [.97, .003×9]** | **0.41¢** |

The fee on a **skewed** partition is an order of magnitude below the uniform case — and skewed partitions are exactly what the flawed proof would have dismissed.

**What survives and what doesn't.** The *empirical* result stands and is strong: 2,069 mutex families with all legs two-sided, 33 with positive gross, **zero with positive net**, best −0.065¢ (reproducing the earlier −0.06¢ independently). But "structurally impossible" is **not established**, and the skewed sub-case is specifically under-tested. My own targeted re-scan was too small a sample to settle it (700 usable non-MVE markets after dust removal) and I am reporting that rather than dressing it up.

**This is the P-016-v2 error pattern** — a founding number justifying a conclusion before it was reproduced. It got caught this time. Bank the empirics; strike the proof.

---

## 3. The corrected law — and what it tells us to hunt

The three survivors share a profile that has never been written down. All six clauses hold for all three:

1. **Single-leg** — no multi-leg structure multiplying a 7%-of-premium tax
2. **Hold-to-settlement** — never pays an exit spread
3. **Priced in the tails** — where the fee parabola collapses (0.33¢, not 1.75¢)
4. **LARGE mechanic** — top-N tie inflation is **+13–30% of contract count**; the dead-heat haircut is **37%**. These are tens of cents of probability mass, not 1¢ quirks
5. **Recurring events** — so a forward gate can actually accumulate observations
6. **Real top-of-book size** on the specific leg traded

Now check the graveyard against it. **Every killed mechanic violates at least one clause:**

| Killed | Clause violated |
|---|---|
| Stat-leader tie fade | #4 — mechanic provably <1¢ for every N |
| Ladder / mutex arb | #1 — multi-leg, fee multiplies |
| Mentions | #3 + #4 — converges to the tick before close |
| Soccer BTTS | #3 — sits at P = 0.483, the exact maximum of the fee parabola |
| Award ties | #4 + #6 — +0.93¢ on 99¢ collateral, and 68 of 70 are one-sided |
| Make-cut maker | #2 — pays the spread on a maker fill, then adverse selection |

The filter retrodicts the graveyard and passes the survivors. That is the test Task 6 of the standing queue was supposed to build, and this run has effectively built it — with the important correction that **the discriminating variable is the fee-and-magnitude profile, not the spread.**

### The targeting rule for Round 6

> Do not hunt "settlement mechanics." Hunt for a **large** mechanic (≥5¢ of value), **single-leg**, **hold-to-settlement**, **priced in the tails**, in a **recurring** event, with **verified top-of-book size on the traded leg**.

And one operational change from Hunt E: **screen on top-of-book size, not spread.** Inside a single event family, `CONTROLH-2026-R` shows **152,862 contracts** at the bid while `KXRHOUSESEATS-27-230` shows **1** — and both pass "spread ≤ 2¢". Spread is nearly uninformative about tradeability. Only **43%** of even the top-60 liquid series have ≥$100 at top-of-book on both sides; **15%** have ≥$1,000.

---

## 4. Traps and facts banked (worth more than the kills)

**Data-shape traps that would corrupt any future study:**

- **84.8% of all open markets are auto-generated parlay dust** (`KXMVESPORTSMULTIGAMEEXTENDED`, `KXMVECROSSCATEGORY`): 362,671 of 425,682, of which **0.1% are two-sided**. Any market-count screen that doesn't exclude these measures nothing.
- Only **~2,863 of 12,187 series have any open market.** The rest are dormant shells; "12,187 series" overstates the tradeable universe ~4×.
- **`last_updated_ts` cannot date a listing** — 3,599 series share one backfill timestamp. Use "zero settled markets" as the newness test.
- `status=settled` is the correct *filter* and returns rows reading `"finalized"`. Combined filters (`finalized,closed`) return 400 silently.
- List-endpoint `yes_bid_dollars`/`yes_ask_dollars` were validated accurate (56/60 vs orderbook) — safe to screen on, confirm finalists via `/markets/{t}/orderbook` (`orderbook_fp.{yes_dollars,no_dollars}`).

**Rule facts:**

- **Kalshi's void conventions are edge-neutral by construction.** Cancellation pays either "the last traded price prior to cancellation" or "$1/[eligible strikes listed], rounded down." A payout anchored to the market's own price cannot transfer value in expectation. **Void/cancellation arbitrage does not exist on this venue — that closes an entire category permanently.** Corollary pre-screen: *before measuring friction, ask whether the mechanic's payout is anchored to the market's own price. If so it is variance, not edge.*
- **The award tie rule is conditional** — it binds only *"if 'Tie/Co-Winners' is listed as a participant."* **5 of 75 live award events don't list one**, including `KXHEISMAN-27` (OI 3.16M). Co-winners there are undefined and fall to Exchange discretion.
- **`BALLONDOR.pdf` pays ties `$(1 − 1/N)`** — for N ≥ 3 the total payout **exceeds $1**. Kalshi paying out more than the contract is worth is unusual enough to note, though no live market expresses it.
- `$1/N` splits are universally **"rounded down"** — a permanent small transfer from YES to NO, but provably **<1¢ for every N** (max 0.882¢ at N=17; exactly 0 at N=2,4,5,10,20). A fact about the exchange, not a trade.
- **`KXF1RETIRE` is career retirement, not race DNF** (`RETIRESPORT.pdf`). Anyone screening ticker text for "F1 DNF" grabs the wrong series.
- **F1's DNF rule is confirmed in our favour** — "outside the top" explicitly includes non-classified drivers — but Kalshi has **never listed an "outside the top" market** (0 of 550 settled). Re-test trigger: any `KXF1*` market matching `/outside the top/i`.
- `GPUMON.pdf` contains an explicit partition failure: *"If no data is available… all strikes, excluding 'No data' or 'None', will resolve to 'No.'"*

**One claim I could not verify:** Hunt E reported non-1¢ tick regimes (`tapered_deci_cent`, `deci_cent`, ~890 markets quoting sub-1¢ spreads, with CONTROL/LLM1/PRESPERSON rulebooks stating a $0.001 minimum tick). The `/markets` payload returned `tick_size: None` for all 200 sampled. **Treat as unverified** — but if true it is materially interesting, because a deci-cent tick in a deep book changes the friction arithmetic for every candidate. Worth ten minutes to settle.

---

## 5. Recommendations

**1. Do not queue another broad hunt.** Five hunts × five angles produced zero candidates, and the categories are now genuinely exhausted: sports, politics, elections, economics, awards, entertainment, mentions, companies, health, commodities, transportation, sci-tech, social, exotics, crypto, financials. That is the whole exchange. The honest read after 32 tested hypotheses is that **the mechanics well is dry at our size**, and further breadth-first hunting has a low expected value.

**2. Run the three cheap things this round actually produced:**
   - **Settle the deci-cent tick question** (~10 min). If real, it changes every friction calculation we make.
   - **Re-scan the skewed-partition sub-case properly** — the corrected fee maths says it is ~0.4¢ rather than 3.5–6.3¢, and the flawed proof would have closed it off. Bounded effort, and it is the only live lead the night produced.
   - **The `KXNCAAFGAME` bulk-listing snapshot**, protocol pre-registered below. Nearly free, and the snapshot cannot be reconstructed after the fact.

**3. Redirect the effort to what is already live.** P-022 is at T = 0 of 14 and has never emitted a quote; P-001, P-015 and P-017 are all at zero against re-derived gates. **We have four unresolved forward tests and no shortage of hypotheses — we have a shortage of resolved ones.** Making P-022 actually quote is worth more than any candidate in this report.

**4. If you want one more research swing, aim it with the six-clause filter** rather than at a new category. The filter says: large mechanic, single-leg, hold-to-settlement, tail-priced, recurring, deep at the traded leg. Golf satisfied it three times out of three. That is where to look, not somewhere new.

### Pre-registered: the NCAAF listing-window test

The one scheduled item. Week 1 is 2026-08-29; `KXNCAAFGAME` currently lists only 30 markets (15 marquee games, opened 2026-05-20). The bulk drop should land **~2026-08-10 to 08-25**.

1. Poll `/markets?series_ticker=KXNCAAFGAME&status=open` **daily from 2026-08-01**. Trigger = count jumping from 30 to several hundred.
2. **Within 6 hours of the bulk `open_time`**, snapshot bid/ask for every new market. Anchor contemporaneity is the entire point — a 68h-stale anchor already manufactured a +9.5¢ artifact once.
3. Restrict to two-sided books with ask ∈ [0.85, 0.975] on **non-marquee** games — the true inattention analogue.
4. Confirm each finalist on `/markets/{t}/orderbook` for ≥$100 depth within 3¢ **both sides**.
5. **Pre-registered kill threshold: required edge = 1¢ tick + spread + 0.4¢ fee + 1¢ margin.** Re-snapshot at T+24h and T+72h; the drift *is* the alpha. **If drift is under ~3.5¢ the hypothesis is dead regardless of statistical significance.**

Stated in advance: **I expect this to fail.** The 15 already-listed marquee games are priced tightly and sensibly, and nothing in tonight's data supports a listing-day mispricing. It is worth running only because it is nearly free and non-reconstructable.

---

## 6. Scoreboard

- **Settlement / structural mechanics: 3 for 12** (was 3 for 6). P-015, P-017, P-022 stand.
- **"We have better information": 0 for 7.** Untouched tonight — no hunt proposed one, correctly.
- **Maker / fade: 0 for 4.**
- **Multi-leg / structural arbitrage: 0 for 5** (ladders, mutex overround, cross-horizon nesting, word-subset nesting, partition families) — and the corrected fee maths explains why: the tax multiplies per leg.

The DECISIVE LAW held on all five kills tonight, in its corrected form. Nothing in this report justifies capital.

**Working artifacts:** `/tmp/huntA` `/tmp/huntB` `/tmp/huntC2` `/tmp/huntD` `/tmp/huntE` (scripts, caches, per-hunt FINDINGS files), `/tmp/verify/skew.py` (fee-inequality verification).
