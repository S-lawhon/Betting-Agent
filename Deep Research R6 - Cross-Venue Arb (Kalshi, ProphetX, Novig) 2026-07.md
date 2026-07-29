# Deep Research R6 — Cross-Venue Arbitrage: Kalshi × ProphetX × Novig

*2026-07-29. Question: can we arb between the three venues Sam now has accounts on?*

---

## Verdict in one paragraph

**It is a two-venue question, not three — Novig cannot be traded programmatically today.** The live pair is **Kalshi ↔ ProphetX**, and the arb is *structurally* real: both are now CFTC-designated contract markets listing economically identical binary contracts on the same games, both have order-placement APIs, ProphetX's ToS **affirmatively permits algorithmic trading for all users**, and — the part that matters — **no arbitrage screen on the market currently covers ProphetX.** But the corridor is narrower than the fee math alone suggests, and it is narrowed by two things nobody has priced: a **2026 tax change that behaves like a second Kalshi-sized fee on both legs**, and a **ProphetX trade-bust rule whose safe band collapses exactly where the fee corridor is tightest**. Net: there is a genuine window at mid-prices, it is worth measuring, and **it should not be built until the tax characterization question is answered by a CPA** — because that single answer moves the breakeven gap from 2.25¢ to 3.85¢, which is the difference between the published cross-venue basis being an edge and being a loss.

**Recommended next step is read-only and costs nothing: 14 days of two-venue book snapshots, reusing `scripts/collect_inplay_basis.py`.** Pre-registered kill criteria in §8.

---

## 1. Both venues changed underneath the question

The premise "Kalshi (prediction market) vs Novig/ProphetX (sportsbooks)" is out of date by six weeks. All three are now, or are about to be, the same *kind* of thing:

| | Kalshi | ProphetX | Novig |
|---|---|---|---|
| **Status** | CFTC DCM, live | **CFTC DCM + DCO** (self-clearing), approved Jun 11 2026, **live nationwide Jun 18 2026** | CFTC DCM designated **Jun 16 2026** (as Ludlow Exchange LLC) — **real-money not launched**, Q3 2026 target |
| **What you can trade today** | Everything | Everything, 49 states + DC (not NV), 18+ | **Sweepstakes only** (Novig Coins / Novig Cash) |
| **Structure** | CLOB | CLOB, price-time priority, fully collateralized, DCO novates | P2P book **+ Novig's own internal market maker** takes the other side on thin markets |
| **Fees** | Taker `ceil(0.07·C·P(1−P))`; maker `0.0175·C·P(1−P)` on **130 of 12,299 series** (107 of them sports) | **2% of net gains per market**, 0% on parlays. Losing positions cost nothing | Zero retail commission today. **Post-DCM schedule unpublished** (Rule 3.6 reserves the right) |
| **API** | REST + WSS + FIX, token-bucket rate limits | **Full public docs, sandbox with demo funds, ToS §3.7 explicitly carves out "programmatic trading via the ProphetX API, which is available to all Users"** | Real API exists (`docs.novig.com`, OAuth2, batch orders, kill switch) but **institutional-gated** — manual provisioning via `developers@novig.com` |
| **Bots on a retail account** | Permitted | **Permitted** | **Prohibited.** ToS §3.7 bans "software-assisted methods… bots designed to play automatically" |

### Novig: not a venue for us yet — but one free action

Three independent reasons Novig is out of scope today, any one of which is sufficient:

1. **Bots are a flat ToS violation on a retail Novig account.** The compliant path is LP/EMM onboarding, which is "not self-service."
2. **It's a sweepstakes product.** Novig Cash carries a 1× playthrough, redemption caps of **$5,000/transaction and $10,000/24h** (\$5k/day in NY and FL), and 2–3 business day settlement. That is incompatible with an arb book's capital velocity — and DarkHorse Odds specifically reports Novig is *"more likely than other sportsbooks to limit bettors after a withdraw."*
3. **ToS §4.13** reserves the right to suspend accounts for play *"in tandem with other player(s) as part of a club, syndicate, group"* — squarely aimed at fund-shaped activity.

Post-DCM this all changes and Novig becomes a third real leg. **The one free action now: email `developers@novig.com` asking for LP/EMM onboarding criteria and the post-DCM fee schedule.** It resolves the two biggest unknowns at zero cost, and the answer determines whether Novig is a 2026 or a 2027 question.

*Everything below is Kalshi ↔ ProphetX.*

---

## 2. The fee corridor — the honest floor

A two-leg lock buys the YES side on one venue at `a` and the complementary side on the other at `b`. Gross lock = `100 − a − b` cents. The two fee models are structurally different and have to be composed, not compared:

- **Kalshi** charges upfront, per contract, win or lose: `0.07·P(1−P)`.
- **ProphetX** charges 2% of net gain, **only on the leg that wins**. A leg bought at `q` wins with probability `q` and gains `1−q`, so its *expected* cost is `0.02·q(1−q)` — the same parabola, coefficient 2 instead of 7.

**Combined expected fee = `0.09·P(1−P)` — i.e. ProphetX adds the equivalent of 2 points to Kalshi's 7.**

| Kalshi P | Kalshi taker + PX | Kalshi maker (fee-free series) + PX | Kalshi maker (charging series) + PX |
|---|---|---|---|
| 0.50 | **2.25¢** | 0.50¢ | 0.94¢ |
| 0.70 | 1.89¢ | 0.42¢ | 0.79¢ |
| 0.80 | 1.44¢ | 0.32¢ | 0.60¢ |
| 0.90 | 0.81¢ | 0.18¢ | 0.34¢ |
| 0.95 | 0.43¢ | 0.10¢ | 0.18¢ |

**A trap in that middle column.** Kalshi's maker-free series (12,169 of 12,299) include golf top-N, all H2H, and all 100 `LEAD` tickers — the pod shop's home turf. But **the big liquid sports moneylines charge maker**: `KXMLBGAME`, `KXNBAGAME`, `KXNFLGAME`, `KXNHLGAME`, `KXATPMATCH`, `KXWTAMATCH` are all in the 107. So the universe where the fee is cheapest is *golf* — and §4 shows golf is exactly where ProphetX's settlement rules are broken. The cheap fee and the clean contract do not overlap.

**Reference point from the literature:** Gebele & Matthes (arXiv 2601.01706, ten platforms incl. Kalshi, 100k+ events) measure **persistent execution-aware deviations of 2–4%** across semantically equivalent markets, *and explain why they persist*: "arbitrage positions cannot be netted across venues and must be held until resolution." That is a capital constraint, not a speed constraint — and it applies to us identically. **2–4¢ against a 2.25¢ floor is a real but thin edge.** Then §3 happens to it.

---

## 3. The finding that reframes the whole thing: §165(d) is an arb-specific tax

Effective 2026, the wagering-loss deduction is limited to **90% of losses** (still capped at wagering gains). If prediction-market P&L is characterized as **gambling** rather than capital, then 10% of every losing leg is non-deductible — and **an arb book is the single worst possible structure for that rule**, because by construction it books a full-size loss against a full-size gain on every trade.

Closed form. For a lock at gross gap `g` with Kalshi price `P`:

```
E[loss per contract-pair] = 200·P(1−P) − g/2   cents
extra tax                 = 0.10 · E[loss] · τ  ≈  20τ · P(1−P)
after-tax breakeven gap   = [9 + 20τ] · P(1−P)
```

**At a 32% marginal rate, `20τ = 6.4` — the IRS becomes a second, nearly Kalshi-sized taker fee on both legs, and it scales as the same parabola.**

| Kalshi P | fee only | fee + tax @32% | fee + tax @37% |
|---|---|---|---|
| 0.50 | 2.25¢ | **3.85¢** | 4.10¢ |
| 0.70 | 1.89¢ | 3.23¢ | 3.44¢ |
| 0.80 | 1.44¢ | 2.46¢ | 2.62¢ |
| 0.90 | 0.81¢ | 1.39¢ | 1.48¢ |

**This is the crux: the published cross-venue deviation is 2–4¢. The pre-tax floor is 2.25¢. The after-tax floor is 3.85¢.** Under capital treatment there is an edge; under gambling treatment most of the distribution is underwater. One CPA answer is worth more than any amount of code here.

### Why this hits arb ~25× harder than the fund's existing pods

The phantom tax scales with **gross turnover**; profit scales with **the gap**. Directional strategies have a far better ratio:

| Strategy | E[loss]/ct | net edge | phantom tax | as % of edge |
|---|---|---|---|---|
| Cross-venue lock @ P=0.50, 3.0¢ gap | 50.0¢ | 0.75¢ | 1.60¢ | **213%** |
| Cross-venue lock @ P=0.50, 5.0¢ gap | 50.0¢ | 2.75¢ | 1.60¢ | 58% |
| P-022 maker (sell YES 7¢, wins 96.5%) | 10.0¢ | 3.50¢ | 0.32¢ | **9%** |
| P-018 headline (+9.09¢/ct) | 25.0¢ | 9.09¢ | 0.80¢ | **9%** |

At a 3¢ gap the tax exceeds the entire edge twice over. **This is not a reason to worry about the existing book — it is a reason to be specifically suspicious of arb.**

*Status of the law: unsettled and undocumented. Green Trader Tax (Apr 2026) calls §1256 treatment "uncertain and may be considered aggressive"; Thomson Reuters (Jun 2026) notes the IRS "has not addressed prediction market contracts." I found **zero** practitioner discussion of §1092 straddle rules applied to event contracts — a documented gap, not a failed search. Capital treatment is "commonly used among practitioners" but has no guidance behind it.*

---

## 4. Two structural hazards the fee math doesn't see

### (a) ProphetX Rule 4.6 puts a *ceiling* on how much edge you're allowed to keep

Verbatim, Rulebook v2.0 Rule 4.6(d): *"A range equal to **fifteen percent (15%) above and below Fair Market Value** (the 'No-Cancellation Range') will generally be applied… Trades outside the No-Cancellation Range may be cancelled or adjusted at ProphetX DCM's sole discretion."*

It is **15% of the price, multiplicative** — so the safe band is ±7.5¢ at a 50¢ contract but only **±1.5¢ at a 10¢ contract**. Worse: Rule 4.6(b) lets *"a Market Participant or third party"* request the bust — your counterparty can void your good fill — and 4.6(f) accepts *"automated trading malfunction"* as grounds, meaning **someone else's bot bug can cancel your leg**. Determinations are final, no appeal.

Composing the floor (§3) with this ceiling gives the actual viable window, worst case where the whole gap sits on the ProphetX leg:

| Kalshi P | PX price | floor (fee+tax32) | ceiling (Rule 4.6) | **window** |
|---|---|---|---|---|
| 0.50 | 50¢ | 3.85¢ | 7.50¢ | **3.65¢** |
| 0.60 | 40¢ | 3.70¢ | 6.00¢ | **2.30¢** |
| 0.70 | 30¢ | 3.23¢ | 4.50¢ | 1.27¢ |
| 0.80 | 20¢ | 2.46¢ | 3.00¢ | 0.54¢ |
| 0.90 | 10¢ | 1.39¢ | 1.50¢ | **0.11¢ — closed** |
| 0.95 | 5¢ | 0.73¢ | 0.75¢ | **0.02¢ — closed** |

**This inverts the naive read.** Fees alone say *trade the tails* (the parabola is smallest there). Rule 4.6 says the tails are exactly where your fills are bustable. **The viable band is mid-price, P ≈ 0.40–0.65 — the opposite of where the fund's existing golf pods live, and the opposite of where the fee curve points.** That is a non-obvious result and it should drive the market selection.

### (b) The venues use opposite settlement conventions — Kalshi *marks*, ProphetX *refunds*

Confirmed at two independent levels on the ProphetX side (API `winning_status` enum, and the CFTC-certified contract specs):

> **`no_result`**: "There was no official result for the event, so **the original quantity is returned** to the participant's cash balance." — also `draw`, `push`
>
> **MLB spec, §C Postponed Events:** "If an event does not commence on the officially scheduled calendar day… **all contracts referencing that event shall be void and collateral shall be returned.**"

Kalshi never refunds. Every sports contract template says the market *"will resolve based on the **last fair market price** as determined by the Exchange in its sole discretion."*

| Event | Kalshi | ProphetX | Hedge outcome |
|---|---|---|---|
| Postponed to next day | market stays open, **48-hour** window, resolves on the rescheduled game | **void + refund** (not same calendar day) | ⚠️ **You go naked long on Kalshi.** Must detect the void and flatten or re-hedge |
| Spread/total lands exactly on the line | Kalshi uses its own strike grid | **void + refund** | Only matters where strikes align at all — see below |
| Game-winner tie | **$1/n split** (two-way tie → 50¢/50¢) | `draw` → refund | Mismatch |
| Player doesn't start (props) | **last fair price** | **void + refund** | Mismatch |
| Player withdraws *after* starting | resolves **No** | resolves **No** | ✅ aligned |
| Overtime / extra innings | included | included | ✅ aligned |
| **Golf top-N dead heat** | boundary tie resolves **Yes**, full $1 | ⚠️ **UNDEFINED** | ⛔ see below |

**The golf spec is defective.** ProphetX's certified golf filing says dead heats follow *"Rule F above"* — but Rule F is *"Player Must Start."* **The dead-heat rules are never defined anywhere in the filing.** This is a broken cross-reference in a CFTC-certified document. Given that golf tie-handling is the mechanical basis of P-017 and P-022, and that this project has already been burned once by assuming golf tie payouts (`golf_template_sweep_2026-07-27` — GOLFH2H ties pay $0.50/$0.50, not $0), **golf is disqualified from the arb universe until ProphetX publishes a dead-heat formula in writing.**

**Net effect on the tradeable universe.** What survives is *two-outcome, no-push, no-draw, full-game* markets where both venues list an identical binary: **MLB moneyline, NBA moneyline, NHL moneyline, tennis match winner.** Spreads and totals require Kalshi's strike grid and ProphetX's sportsbook line grid to coincide — an open measurement question, not an assumption. Postponement remains a live operational requirement even on the clean universe (MLB postponement rate is ~2% of games): **the system must detect a ProphetX void and immediately flatten the orphaned Kalshi leg.**

### (c) Two smaller operational traps, noted for the spec

- **`wiped` order status:** *"all pre-match orders being wiped once an event transitions from pre-match to live."* Your entire resting pre-match book is auto-cancelled at first pitch. Any maker-side design must plan for this.
- **Capital-split drift.** In a two-venue lock, whichever venue's leg wins receives *all* the money. The balance split is a random walk. Simulated over 200 locks, the 5th percentile leaves **6pp of the book stranded on one venue** (12pp over 50 locks) — so a live book needs buffer capital on both sides plus a rebalancing loop, and Kalshi withdrawals are reportedly 3–30 days. **This is the practical form of the "cannot net across venues" constraint the literature identifies as the reason the basis doesn't close.**

---

## 5. Price granularity is *not* a problem (checked)

ProphetX prices on an **American-odds integer ladder**, 291 ticks from −10000 to +10000. Increment varies by level (1 near ±100, widening to 2500 beyond ±5000), but in implied-probability terms:

- **Finest tick 0.168 pp, coarsest 0.758 pp** — finer than Kalshi's 1¢ grid *everywhere*
- Near even money: `+100` = 50.000%, `+101` = 49.751%, `−110` = 52.381% → ~0.22 pp spacing
- No gap at even money; ladder runs `… −102, −101, +100, +101 …` continuously

**Kalshi's 1¢ grid is the binding constraint, not ProphetX's ladder.** Snap error is at most ~0.38 pp. This was a plausible killer and it is cleanly ruled out.

---

## 6. What it's worth — and why the answer is "measure first"

Net-of-fee P&L at $100K deployed, 200 trading days, by gap and by how much you can actually get filled per day:

| gap | net/ct | $5k/day | $20k/day | $50k/day | $150k/day |
|---|---|---|---|---|---|
| 3.0¢ | 0.75¢ | $7,500 | $30,000 | $75,000 | $225,000 |
| 4.0¢ | 1.75¢ | $17,500 | $70,000 | $175,000 | $525,000 |
| 5.0¢ | 2.75¢ | $27,500 | $110,000 | $275,000 | $825,000 |
| 7.0¢ | 4.75¢ | $47,500 | $190,000 | $475,000 | $1,425,000 |

Same table **after the §165(d) phantom tax** under gambling characterization (−1.60¢/ct at P=0.50):

| gap | net/ct | $5k/day | $20k/day | $50k/day | $150k/day |
|---|---|---|---|---|---|
| 3.0¢ | **−0.85¢** | −$8,500 | −$34,000 | −$85,000 | −$255,000 |
| 4.0¢ | 0.15¢ | $1,499 | $5,999 | $14,999 | $44,999 |
| 5.0¢ | 1.15¢ | $11,500 | $46,000 | $115,000 | $345,000 |
| 7.0¢ | 3.15¢ | $31,500 | $126,000 | $315,000 | $945,000 |

**Two free variables, both unmeasured: the gap distribution and the fill volume.** ProphetX has published *no* volume, open interest, or depth figures — even though its own Rule 2.13(e) requires daily publication of settlement price, volume and OI. Nobody appears to be reading that data. That is simultaneously the biggest unknown and the clearest signal that this corner is unworked.

**The reasons to think ProphetX gaps might exceed the mature Kalshi↔Polymarket basis:**

- Nationwide for **six weeks**. No arb screen covers it — I checked OddsJam, OpticOdds, odds-api.io, Oddpool, DarkHorse, ArbBets, RebelBetting, Betstamp. OpticOdds (the market-maker-grade feed, 11 exchanges with full depth) does **not** carry ProphetX. Only two data vendors carry it at all, and neither offers an arb screen.
- Peer-to-peer only — ProphetX publicly markets that *"customers trade only against one another rather than company-backed market makers,"* unlike Kalshi where Susquehanna quotes 2–3¢ spreads with 100k+ contract depth.
- It just raised **$35M (Jul 28, 2026)** explicitly to *buy* liquidity — an admission that it doesn't have enough.

**The reasons to think they won't:** three prop shops (Chicago Trading Company Ventures, Belvedere Trading, principals of Consolidated Trading) hold equity in ProphetX and are unlikely to have invested in a venue they don't intend to trade. CoinDesk reported "near-identical World Cup winner odds across Kalshi, Polymarket and the major sportsbooks." And P-020 already killed the analogous Polymarket-as-oracle thesis on the grounds that **Kalshi was both the deeper and the sharper venue** (Brier 0.1373 vs 0.1445; optimal blend weight on the other venue exactly 0.0).

**P-020's own §8 pre-registered the reopening condition, and it applies here verbatim: measure depth dollar-for-dollar *before* building the connector.**

---

## 7. Three things in this research worth more than the arb

The arb is the narrowest opportunity uncovered. Three by-products are larger and cheaper:

### (a) ProphetX is 3.5× cheaper than Kalshi for *taker* strategies — at every price

| P | Kalshi taker | **ProphetX taker (expected)** | Kalshi maker (free series) | Kalshi maker (charging) |
|---|---|---|---|---|
| 0.25 | 1.31¢ | **0.38¢** | 0.00¢ | 0.33¢ |
| 0.50 | 1.75¢ | **0.50¢** | 0.00¢ | 0.44¢ |
| 0.90 | 0.63¢ | **0.18¢** | 0.00¢ | 0.16¢ |

Because the coefficient is 2 vs 7, the ratio is exactly 3.5× at every price. This yields a clean **routing rule**: *take on ProphetX, make on Kalshi's fee-free series.* If P-001 — the fund's one durably validated external-information edge — executes as a taker on maker-charging series, porting it to ProphetX cuts its fee load by 72% with no change to the edge thesis. **That is a larger, more certain gain than the arb, against a strategy already proven.** Worth its own evaluation.

### (b) A maker-rebate program with an August deadline

ProphetX self-certified a **Make Volume Fee Rebate Program** effective Jun 29 2026: marginal rebate on fees paid, rising to **80% above $7.5M monthly make volume**, *"open to all Market Participants on objective thresholds"* — no Market Maker Agreement required. At the top tier the effective maker fee is 2% × 20% = **0.40% of net gains**.

⚠️ *"The initial term… is two (2) months from the Effective Date"* → nominally expires **~Aug 29, 2026**, extension discretionary and requiring a new CFTC filing. **Do not underwrite anything on this.** But it is worth knowing it exists, and separately, Chapter 8 (designated market makers, with quoting obligations and negotiated rebates) was added to the rulebook on **Jul 27, 2026 — two days ago**. A six-week-old venue that just raised $35M for liquidity and has published zero designated MMs is an unusually open door for a fund with an existing MM stack (P-029).

### (c) Zero-cost API access, today

Both are free and reversible, and both are prerequisites to any of the above:

- **ProphetX:** request API access via the Zendesk form or `marketmaking@prophetexchange.com`. **Sandbox with demo funds is available**, plus a first-party Python scaffold (`github.com/betprophet1/mm-api-integration-guide`).
- **Novig:** email `developers@novig.com` re: LP/EMM criteria and the post-DCM fee schedule.

---

## 8. Pre-registered cheapest kill

Per house method — falsify on observation before building. **The entire test is read-only. No capital, no connector, no order path.**

**Instrument:** extend `scripts/collect_inplay_basis.py` (already does exactly this shape for Kalshi↔Polymarket, already has a systemd unit, already canonicalises to Kalshi team abbreviations). Add a ProphetX read-only leg via the Market Data API. Snapshot **top-of-book and depth on both venues** every 60s for **14 days**, on the clean universe only: **MLB / NBA / NHL moneyline + tennis match winner**. Append-only JSONL, same as the existing collector.

**Pre-registered gates — all four must pass, event-clustered CIs, no post-hoc corridor tuning:**

| # | Gate | KILL if |
|---|---|---|
| **1. Depth** | Median ProphetX top-3-level depth, in dollars, on matched markets | < $2,000 — the venue cannot absorb size and nothing else matters |
| **2. Gap** | P(gross gap > 3.85¢) at P ∈ [0.40, 0.65], measured **executable** (crossing the spread, both legs, snapped to each venue's grid) | < 2% of observations — the after-tax floor is never cleared |
| **3. Persistence** | Median survival of a qualifying gap | < 30s — this is a latency race we are not built to win, cf. Polymarket intra-venue arbs clearing in 3.6s median |
| **4. Capacity** | Implied $/day capturable = gap frequency × depth × matched-market count | < $5,000/day — see §6; below this the book cannot pay for its own operational risk |

**Gate 0, and it runs first because it is free and it can kill everything above it:** *put the §165(d) characterization question to a CPA.* If the answer is gambling treatment, gate 2's threshold is 3.85¢ and the strategy is probably dead on arrival; if capital treatment, it is 2.25¢ and the published 2–4¢ literature basis is live. **Do not run 14 days of collection before asking a question that can be asked this week.**

**Explicitly out of scope for v1** — each disqualified above, not deferred for convenience: golf (undefined dead heats, §4), spreads and totals (unverified strike-grid alignment, §4), player props (opposite DNP conventions, §4), tails P > 0.75 (Rule 4.6 window closed, §4a), Novig (§1).

**If all four gates pass**, the build is genuinely cheap — the venue abstraction already exists: `BasePod` carries a `venue` field, `@register_pod` is zero-friction, `build_venue_clients()` takes a new try/except block, `MultiExecutor` takes one new `elif`, and aggregate risk is already venue-aware. `src/pods/forecastex_kalshi_arb.py` + `src/forecastex_client.py` is the closest working template (~21KB combined, dual-venue, emits `venue="multi"`). **The expensive parts are fees and settlement, not plumbing:** `src/kalshi_fees.py` has no venue abstraction and a ProphetX module needs a *different shape* (2% of net gains is stochastic and paid on the winning leg only — it is not a per-contract cost and must not be modeled as one), and `src/settlement_bridge.py` plus `trade_log_schema.py` default to Kalshi throughout.

---

## 9. Decisions for Sam

1. **Gate 0 — ask a CPA about §165(d) and §1092 on CFTC event contracts.** This is the highest-value hour available and it gates everything. It also affects the *existing* book, just far less (9% of edge vs 213%).
2. **Request ProphetX API access + sandbox now.** Free, reversible, prerequisite to every path in §7.
3. **Email `developers@novig.com`.** Free; determines whether Novig is 2026 or 2027.
4. **Choose the priority:** the arb (narrow window, unmeasured capacity, needs a new connector) — or **§7a, porting an already-validated taker edge onto a 3.5×-cheaper venue**. My read is that §7a is the better risk-adjusted use of the same ProphetX integration work, and the read-only collector in §8 is a prerequisite for both, so it should run either way.

---

## Sources

**Venue primary:** [ProphetX Rulebook v2.0 (eff. Jul 27 2026)](https://framerusercontent.com/assets/qtPmOnTTXadHJNcu2t6etlF2Ho.pdf) · [ProphetX CFTC filings index — 39 contract specs](https://www.prophetx.co/lobby/t-c/filings) · [ProphetX trading fees](https://www.prophetx.co/lobby/t-c/trading-fees) · [ProphetX Terms §3.7](https://www.prophetx.co/lobby/t-c/terms) · [docs.prophetx.co](https://docs.prophetx.co/) · [enum semantics](https://docs.prophetx.co/docs/meaning-of-enums.md) · [price ladder constants](https://raw.githubusercontent.com/betprophet1/mm-api-integration-guide/main/src/constants.py) · [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) · [Kalshi rate limits](https://docs.kalshi.com/getting_started/rate_limits) · [KalshiEX Rulebook v1.24](https://mdf-law.com/wp-content/uploads/2026/03/Kalshi-Rulebook-v1.24.pdf) · Kalshi contract terms [MLBGAME](https://assets.kalshi.com/contract_terms/MLBGAME.pdf), [NFLGAME](https://assets.kalshi.com/contract_terms/NFLGAME.pdf), [GOLFFINISH](https://assets.kalshi.com/contract_terms/GOLFFINISH.pdf), [TENNISMATCH](https://assets.kalshi.com/contract_terms/TENNISMATCH.pdf) · [Novig Terms of Use §3.7/§4.13](https://support.novig.us/en/articles/8590101-terms-of-use) · [Novig Market Rules](https://support.novig.us/en/articles/9612523-market-rules) · [docs.novig.com](https://docs.novig.com/) · [CFTC — Ludlow Exchange DCM filings](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/59390)

**Literature:** [Gebele & Matthes — Law of One Price in Prediction Markets, arXiv 2601.01706](https://arxiv.org/html/2601.01706v1) · [Saguillo et al. — $40M Polymarket arbitrage, arXiv 2508.03474](https://arxiv.org/abs/2508.03474) · [Cheng, Yang & Zou — arbitrage persistence 3.6s median, arXiv 2605.00864](https://arxiv.org/html/2605.00864v1) · [Bürgi, Deng & Whelan — Makers and Takers on Kalshi](https://www.karlwhelan.com/Papers/Kalshi.pdf) · [Ng, Peng, Tao & Zhou — price discovery across venues](https://fss.um.edu.mo/fss-deco-seminar-price-discovery-and-trading-in-modern-prediction-markets/)

**Tax:** [Green Trader Tax — prediction market taxes](https://greentradertax.com/prediction-market-taxes-capital-gains-gambling-or-something-else/) · [Camuso CPA — §1256](https://camusocpa.com/section-1256-prediction-market-tax/) · [Camuso CPA — loss deductions](https://camusocpa.com/prediction-market-loss-deductions/) · [Thomson Reuters — IRS silence](https://tax.thomsonreuters.com/news/irs-silence-on-prediction-market-winnings-to-cause-confusion-as-world-cup-begins/)

**Market structure:** [ProphetX launches nationwide](https://www.prnewswire.com/news-releases/prophetx-launches-nationwide-302804384.html) · [ProphetX raises $35M, Jul 28 2026](https://www.covers.com/industry/prophetx-raises-35m-to-scale-its-sports-prediction-market-platform-july-28-2026) · [Novig secures CFTC designation](https://www.prnewswire.com/news-releases/novig-secures-cftc-designation-bringing-the-first-prediction-market-built-for-sports-fans-nationwide-302801964.html) · [Kalshi — liquidity / Susquehanna](https://news.kalshi.com/p/liquid-prediction-markets-are-finally-here) · [OpticOdds prediction-market coverage](https://developer.opticodds.com/docs/opticodds-for-prediction-market-makers) · [OddsJam Prediction Traders](https://oddsjam.com/prediction/traders) · [DarkHorse Odds — Novig](https://about.darkhorseodds.com/articles/sportsbooks/novig)

**Internal:** `crossvenue_research/REPORT_CrossVenue_2026-07.md` (P-020 KILL) · `fee_audit_research/` · `golf_template_sweep_2026-07-27` memory · `scripts/collect_inplay_basis.py`
