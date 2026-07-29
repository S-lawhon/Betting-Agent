# Combo Market-Making on Kalshi — Research Report

**Date:** 2026-07-28 · **Author:** research session (Cowork) · **Status:** CANDIDATE, gated
**Proposed pod:** P-029 · **Prior record it must beat:** maker/fade **0 for 5**, multi-leg/structural **0 for 5**

---

## 0. Verdict

**CANDIDATE — the largest measured edge in the fund's history, with one undischarged risk that
the house's own standing rule says must be measured before a pod is written.**

Kalshi combos (multi-leg parlays, traded via RFQ) show a measured, composition-controlled,
contract-weighted edge to the **seller** of:

| basis | target zone (2–4 legs, 10–35¢) | whole combo universe |
|---|---|---|
| last-price | **+7.20 ¢/ct**, day-clustered 95% CI [+6.28, +8.08] | +2.79 ¢/ct [+2.20, +3.42] |
| first-print (conservative) | **+3.17 ¢/ct** [+2.78, +3.57] | +1.23 ¢/ct |

n = 958,185 markets / 556M contracts (target zone), 68 event-day clusters, 2026-05-21 → 2026-07-28.
**Maker fee is zero** on every combo series, so gross ≈ net on the quoting side.
Return on posted collateral is **~4.0% per deployment turn** (first-print basis), median hold **5.4 h**.

**The undischarged risk is fill rate.** Every number above is measured on trades that *happened*,
i.e. at the price at which some incumbent quoter won. It does not tell us what fraction of RFQ
flow *we* would win at a price that preserves the edge. CLAUDE.md's standing rule after P-017A —
*"never propose a maker variant without a fill estimate first"* — binds here. §7 pre-registers a
two-week live measurement that produces that estimate for ~$0 of risk capital.

**Do not build the pod before §7 returns.**

---

## 1. Why this is not simply the sixth maker kill

The house record says behavioural and pure-market-making bets fail on Kalshi, and that multi-leg
structural arbitrage is 0 for 5. Both priors were applied adversarially throughout. Five things
make combos structurally different from the five maker kills, and they are differences in
*mechanism*, not in optimism:

1. **The fee sign flips.** P-016, P-017M, P-017A, P-019 and P-023b all fought a fee. All 15 combo
   series are `fee_type: quadratic` — **the maker pays nothing**. Verified live on `GET /series`
   across all 12,255 series. The taker (the retail buyer) pays the full 0.07 parabola.
2. **The house's own edge law stops binding.** The corrected law says stay in the price tails
   because the fee parabola peaks at P=0.50. That law is a *fee* law. With a zero maker fee, the
   quoter is free to sit at 20–35¢ — which §5 shows is exactly where the edge is largest
   (+7.9 to +8.9 ¢/ct) — without paying for the privilege. **This is the first strategy the fund
   has examined where the tail rule does not apply.**
3. **The counterparty is structurally, not incidentally, disadvantaged.** Retail cannot post limit
   orders on combos; they are price takers by construction and they self-select into
   low-probability, high-payout tickets (average implied win rate 9% vs 43% on non-combos).
   This is not "the crowd is irrational" — it is a product whose UI only offers one side.
4. **The edge is measured on fills, not on quotes.** P-017A's +13.69¢ was on quotes and died on a
   2.2% fill fraction. Here the −1.14¢/−3.17¢ *is* the realised P&L of whoever actually won the
   flow. The winner's curse of the RFQ auction is already inside the number.
5. **External corroboration exists and is one day old.** Bloomberg, 2026-07-28: Kalshi retail has
   lost **$294M on combos YTD 2026**, with the piece attributing the other side to "professional
   market makers and algorithmic traders … through automated quoting." Our independently measured
   buyer loss over a 3-month window is $482M on a last-price basis (INDICATIVE, upper bound;
   §4.4). Two independent estimates of the same phenomenon, same order of magnitude.

What it shares with the graveyard: it is a maker strategy, and it is multi-leg. Those are exactly
the two priors §7's gates are designed to honour.

---

## 2. Mechanism ground truth

Sourced from `docs.kalshi.com` OpenAPI v3.26.0, the AsyncAPI spec, the CFTC-filed rulebook, and
live API queries. Where the documentation is silent this report says so rather than inferring.

### 2.1 What a combo is

A combo is **one binary market** (`market_type: "binary"`, `notional_value_dollars: "1.0000"`)
that resolves YES only if every leg resolves YES. It is instantiated **on demand**:
`POST /multivariate_event_collections/{collection_ticker}` with a `selected_markets` array returns
a new `market_ticker`. Combo markets do not pre-exist — the market exists because someone built a
ticket. This is why 81.8% of all combo markets have traded, and it is a structural fact, not a
liquidity finding.

Scalar legs multiply and are **rounded down** (`functional_description`, 1,349 of 1,387
collections) — a small, permanent asymmetry in the seller's favour.

### 2.2 RFQ, and who may quote

Combos trade by Request For Quote. The requester names a market and size; any member may respond
with a two-sided quote (`yes_bid`, `no_bid`; either may be `"0"`, both may not; rejected if
`yes_bid + no_bid > $1`). The CFTC-filed rulebook (Rule 5.3.D.b) is explicit that **any Member**
may quote, subject only to sufficient collateral and position limits. **No market-maker agreement,
KYC tier, minimum capital, or application is required.** Kalshi's separate designated-MM programme
exists but nothing indicates it covers RFQ/combos.

`rfq_created` is broadcast on the `communications` WebSocket channel **to all subscribers**, and
carries `mve_selected_legs` — the full leg list — in the push message itself. A quoter is pushed,
not polled, and does not need a follow-up REST call to learn what it is pricing. The channel
supports sharding (`shard_factor` 1–100).

`active_quoters` — the field that would identify designated quoters per event — is **empty on all
12,916 associated-event entries across all 1,387 collections**. Competition is not enumerable from
public data.

### 2.3 The finding that changes the build cost

Combos are High Volatility Markets: the docs say **3 s to confirm / 1 s execution timer**; the
CFTC rulebook says **2 s / 3 s**. *(Documented contradiction — assume the tighter of each.)*
Either way, a naive RFQ-responder needs sub-second infrastructure.

**But RFQ execution goes through the public book, and resting orders beat it.** Rulebook
5.3.D.d–e: at the end of the execution timer the platform *"sequentially enter[s] orders into the
order book"*, those orders *"execute at a lower time priority than all existing resting orders"*,
and — verbatim — *"Quoter and Requester may in fact never transact a contract between them, if
existing book liquidity exists at the quoted price."*

So there is a second, cheaper path to the same flow:

> **Resting interception.** Subscribe to `rfq_created` (broadcast, with legs). Price the combo.
> Rest a limit offer in that combo market. If the eventual winning quote is at or above your
> resting price, the requester's buy sweeps **your** order first, at **your** price — and you
> never entered the auction.

This collapses the latency requirement from sub-second to *seconds*, needs no MM approval, and
lets you set your own price rather than win a race to the bottom. Kalshi's own June 2026 Combo
Incentive Program ($1.2M) paid pro-rata on maker volume for exactly this behaviour, with an
imbalance clause favouring *resting YES* orders — which implies incumbent quoters are
overwhelmingly the sellers and Kalshi is subsidising the other side.

**This is the single most important build-cost finding in the report.** It is also the least
verified: it rests on two independent readings of the rule text and has not been tested live.
§7 Gate 2 tests it for the price of a handful of contracts.

### 2.4 Collateral — the gap the documentation will not close

Selling a combo YES at 6¢ is mechanically buying NO at 94¢; on a fully-collateralised exchange
that is 94¢ locked per contract. **This is structurally implied and stated nowhere.**

There is **no documented cross-margining between a combo and its underlying legs**. Combo events
carry `collateral_return_type: "MECNET"` (mutually-exclusive netting *within* an event), and the
combo lives in its own dynamically-created MVE event while the legs live in separate events —
different netting groups. **Hedging legs adds collateral rather than releasing it.**

Model a short combo as full `(1 − price)` collateral, unhedged and unnettable. Confirming this
directly with Kalshi is the highest-value single question in the report.

### 2.5 Other mechanism facts worth banking

- Combos quote on a **`deci_cent` grid — a 0.1¢ tick**, ten times finer than standard Kalshi
  markets. Materially changes quoting economics on longshots.
- Quote creation costs **2 tokens** (vs the default 10) — throughput is not a constraint.
- Max legs is undocumented; **43 observed** in one sample, **173** in another.
- Kalshi's help centre says *"once a combo is placed and filled, it cannot be canceled or
  reversed."* Against the rulebook this is **misleading**: the position sits in a normal binary
  market with a normal orderbook and can be traded out of. Exit is structurally permitted but
  practically unavailable, because no resting liquidity exists.

---

## 3. The universe, measured

Full pull of `GET /markets` across all 12 KXMVE series, 22,726 pages, 2026-07-28.

- **22,727,958 combo markets** — 911,172 open, 21,816,563 settled.
- Exchange-wide, **1,027,718 of 1,097,003 open markets (93.7%) are combos.** *(Prior house note
  said 362,671 / 85%; the exchange has roughly 2.6×'d. The shape is unchanged.)*
- **Two-sided combos: 203 of 1,027,718 = 0.020%.** Non-combo two-sided: 64.16%. 70.5% of open
  combos are **ask-only** — a one-way offer book with no bid to sell back into.
- Price distribution: mean 12.77¢, median 5.70¢, 46.9% of prints under 5¢, p95 50.1¢.
- Leg counts 2–173, mode 4.
- Only **two collections are live at any time** — `KXMVESPORTSMULTIGAMEEXTENDED-R` and
  `KXMVECROSSCATEGORY-R`, rolling containers re-opened daily, each pointing at 536 associated
  events across NFL/WNBA/MLB.

**Data-shape traps found (add to the house list):**
- `status=open` is the *filter*; rows read **`active`**. The string `"open"` never appears as a
  status value in 22.7M rows. Code filtering on `status == "open"` silently returns zero.
- `/markets?ticker=X` (singular) is **silently ignored** and returns the unfiltered list with
  HTTP 200. Use `?tickers=` (plural, CSV).
- `?tickers=` 414s at ~9,600 URL chars. Combo tickers are ~47 chars vs ~30 — batch by **character
  budget**, not count, or a batch size tuned on leg tickers dies on combos.
- The house note *"settled-market LIST endpoints null out volume/price/last"* **does not apply to
  KXMVE** — verified on 40/40 sampled settled combos against per-ticker `GET`.
- **Kalshi purges settled combo markets after ~3 months.** Only 2026-05 → 2026-07 exists. The
  2.06 GB cache from this session is the only copy of ~17.6M settled combo outcomes outside
  Kalshi. It must be archived (see §9).

---

## 4. The edge, and four attempts to kill it

### 4.1 The measurement

For every settled combo that traded, the seller's P&L per contract is `price − payout`
(payout = 1 for YES, 0 for NO, `settlement_value_dollars` for `scalar`). Aggregated per event day
— the day the combo resolves — and bootstrapped by **event-day cluster**, never by contract:
one slate's combos share the same games and the same shocks.

`scalar` is **not void**. 56,128 settled combos resolve `scalar` with a populated
`settlement_value_dollars` spread across the whole range. Booking them at $0 would repeat the
golf-settler error exactly.

**Whole universe: +2.79 ¢/ct, day-clustered 95% CI [+2.20, +3.42], 68 days, 3.7M markets.**
Losing days 17.6% unweighted, **2.9% contract-weighted**. Median hold (open→close) **5.4 h**.

### 4.2 Kill attempt 1 — is `last_price` contaminated by settlement? **SURVIVES**

If the exchange rewrites the final print at settlement, the whole measurement is circular. This is
the exact failure mode that burned the tennis VWAP figure (fee-parabola audit) and the P-022
close-time reconciliation.

| result | n | mean | median | frac > 90¢ | frac < 2¢ |
|---|---|---|---|---|---|
| `no` | 400,000 | 12.36¢ | 7.30¢ | 0.03% | 22.8% |
| `yes` | 400,000 | 40.24¢ | 34.50¢ | **8.04%** | 0.5% |
| `scalar` | 13,354 | 25.77¢ | 21.30¢ | 0.29% | 3.7% |

If prices were rewritten at settlement, YES-settled markets would pile up near 100¢ and NO-settled
near 0¢. They do not — only 8% of YES-settled markets ever printed above 90¢. The separation that
does exist (40.24¢ vs 12.36¢) is the *correct* direction for a genuine forecast. **The price field
is real.**

*(Partial convergence does exist on the ~40% of markets with more than one print. That is why
first-print and last-price differ, and it is why every headline in this report is also stated on
the conservative first-print basis.)*

### 4.3 Kill attempt 2 — does it survive contract-weighting? **SURVIVES**

A maker earns per *contract*, not per market. An earlier n=7,922 sample suggested the
contract-weighted first-print edge might flip sign. On the full population it does not:

| | edge | day-clustered 95% CI | losing days |
|---|---|---|---|
| unweighted (per market) | +2.792 ¢/ct | [+2.203, +3.417] | 17.6% |
| **contract-weighted** | **+2.739 ¢/ct** | **[+2.305, +3.188]** | **2.9%** |

The earlier flip was a handful of large tickets dominating a thin sample. **Rebutted.**

### 4.4 Kill attempt 3 — correlated-book risk. **SURVIVES, with a real cost**

Retail parlays the same popular teams, so a seller's whole book can pay out on the same Sunday.
Measured rather than assumed, by resampling **real same-day joint outcomes**:

- median **2,286 distinct legs** available per day; the single most-used leg appears in **15.1%**
  of that day's combos; top-10 legs take 18.6% of all leg-slots.
- The common factor is real and large: at 5,000 contracts the realised sd is **2.8× what
  independence would predict**. Diversification saturates — mean/sd goes 0.60 (300 contracts) →
  1.02 (5,000) → 1.09 (60,000) and stops improving.
- But it is survivable: at $25,000 of collateral (~29,800 contracts) a single turn is
  **mean +$370, sd $344, P(loss) 15.5%, 1-in-1000 worst −$343.**

**The book is correlated, the correlation is measurable, and it does not threaten solvency at
$100K.** It does mean the honest Sharpe is per-*turn*, not per-contract.

### 4.5 Kill attempt 4 — is the edge decaying? **THE REAL FINDING**

Aggregate edge falls hard across the three months of available history:

| month | markets | unweighted | contract-weighted |
|---|---|---|---|
| 2026-05 | 387,311 | +4.161 ¢/ct | +3.126 ¢/ct |
| 2026-06 | 1,649,263 | +2.740 | +2.815 |
| 2026-07 | 1,680,379 | +1.853 | +2.095 |

May is NBA playoffs, July is MLB/WNBA — so the first suspicion is composition. **It is not.**
Recomputing inside 41 matched (price-bucket × leg-bucket) cells with the mix held fixed reproduces
the decay almost exactly: **+3.128 → +2.815 → +2.104 ¢/ct.** The aggregate edge is being competed
away at roughly **−0.5 ¢/ct per month**, which puts it at zero around Q4 2026.

**But the decay is not uniform, and it is not in the part of the market worth quoting.**

Contract-weighted edge by price bucket:

| month | 0–2¢ | 2–5¢ | 5–10¢ | 10–20¢ | 20–35¢ | 35–60¢ | 60–100¢ |
|---|---|---|---|---|---|---|---|
| 2026-05 | +0.43 | +1.77 | +3.64 | +6.36 | **+8.90** | +6.50 | −0.72 |
| 2026-06 | +0.60 | +2.12 | +3.62 | +5.60 | **+8.19** | +8.54 | +0.06 |
| 2026-07 | +0.53 | +1.90 | +3.21 | +5.63 | **+7.90** | +4.07 | **−2.46** |

and by leg count:

| month | 2 | 3 | 4 | 5–6 | 7–9 | 10+ |
|---|---|---|---|---|---|---|
| 2026-05 | +4.72 | +3.46 | +3.13 | +3.14 | +1.87 | +1.10 |
| 2026-06 | +4.16 | +3.34 | +2.81 | +2.65 | +2.03 | +0.97 |
| 2026-07 | +3.47 | +3.10 | +2.31 | +1.53 | +0.86 | **+0.40** |

Two conclusions, both actionable:

1. **The decay lives in the many-leg, sub-5¢ lottery-ticket end.** That is where new quoters have
   arrived and where the edge is heading to zero.
2. **Above 60¢ the seller LOSES** (−2.46 ¢/ct in July). Near-certain parlays are where the sharps
   are. Hard exclusion.

### 4.6 The target zone — no decay

Restricting to **2–4 legs priced 10–35¢**, the zone the two tables above jointly identify:

| | value |
|---|---|
| n | 958,185 markets · **556.5M contracts** · 68 event days |
| contract-weighted edge, last-price | **+7.202 ¢/ct**, day-clustered 95% CI [+6.276, +8.084] |
| contract-weighted edge, first-print | **+3.170 ¢/ct** [+2.775, +3.565] |
| mean price / collateral | 20.32¢ / 79.68¢ |
| **return on collateral per turn** | **9.04%** last-price · **3.98%** first-print |
| losing days | **4.4%** |
| weekly trend, 11 weeks | **+0.069 ¢/ct per week, 95% CI [−0.318, +0.413]** |

**The trend CI straddles zero. Inside the target zone there is no measurable decay across
11 weeks** (level oscillates +5.4 to +9.9 ¢/ct with no direction). The aggregate decay of §4.5 is
entirely a story about the part of the book this strategy would not quote.

Capacity is not a constraint: the target zone alone carries **8.2M contracts per day**, against a
$25,000 deployment of ~30,000 contracts — **0.07% of measured daily combo premium.**

---

## 5. What this would look like at $100,000

Per-turn distribution, resampled from real same-day joint outcomes, first-print basis, whole
universe (the target zone is better; these are the conservative numbers):

| collateral | ~contracts | mean | sd | mean/sd | P(loss) | 1-in-100 | 1-in-1000 |
|---|---|---|---|---|---|---|---|
| $1,000 | 1,190 | +$14.80 | $17.09 | 0.87 | 20.2% | −$22.72 | −$33.61 |
| $10,000 | 11,904 | +$147 | $140 | 1.05 | 15.7% | −$114 | −$165 |
| **$25,000** | **29,761** | **+$370** | **$344** | **1.08** | **15.5%** | **−$262** | **−$343** |
| $50,000 | 59,523 | +$750 | $687 | 1.09 | 15.4% | −$512 | −$640 |

At a 25% pod cap and full deployment, the compounded 180-day paths run 2×–14× depending on turns
per day, with P(ruin) = 0.00% and median drawdown under 1%.

**Do not believe those path numbers.** They assume 100% fill — that every combo in the tape could
have been sold at its traded price. That assumption is exactly what §7 exists to test, and it is
the assumption that has been wrong in five prior maker studies. The *per-turn* distribution is
sound; the *path* is a ceiling, not a forecast. Realistic output scales linearly with fill rate.

The honest framing at $100K: **capital is not the binding constraint, and neither is capacity.
Fill rate is the only thing between this measurement and a P&L.**

---

## 6. What is still most likely to kill this

Ranked by probability × cost, adversarially:

1. **Fill rate (highest).** P-017A died at a 2.2% fill fraction against a 25% floor. There is no
   public data on how many quoters answer a typical combo RFQ; the one public report (a trader
   with RFQ background, Nov 2025) complained he was *not* seeing multiple quotes. Unknown, and
   decisive.
2. **We win the ones we are wrong about.** The tape's prices are incumbent quotes. If our
   correlation model is worse than theirs, we win precisely the tickets where we are too generous
   — adverse selection through model error rather than through auction structure. Independence
   pricing understates a correlated 3-leg joint by ~30–35% relative, which dwarfs the entire
   ~15% hold. **A naive independence quoter is not shaving margin; it is trading at negative
   expectancy against anyone with a copula.** Same-game correlation could not be measured here
   (n=39; both live collections are cross-slate) and is blocked until NBA/NFL season.
3. **The first-print haircut is borrowed.** The 0.44 factor comes from an unweighted n=7,922
   sample across all zones, applied to a contract-weighted target-zone number. It may be wrong in
   either direction. Measuring it properly inside the target zone is cheap and should be done.
4. **Three months, one regime.** The tape is MLB/WNBA/World Cup summer. NFL and NBA — the volume
   that actually matters — are not in it. Kalshi purges older data, so this cannot be fixed by
   pulling harder; it can only be fixed by waiting and archiving.
5. **Collateral is unconfirmed** (§2.4) and there is no leg netting, so a hedged book costs
   *more* capital than an unhedged one.
6. **Regulatory.** Sports event contracts survived the Third Circuit (2026-04-04) and the CFTC has
   actively overridden a Michigan court order (2026-07-14), so near-term national injunction is
   unlikely. The live risks are geographic shrinkage (Minnesota's ban takes effect 2026-08-01;
   Michigan faces a 2026-08-12 geofence deadline at $500k/day) and an SDNY class action whose
   theory is specifically that Kalshi's **privileged market-maker structure** is deceptive. That
   last one targets the seat this strategy would occupy.
7. **Competition arriving.** Documented: Susquehanna, Kalshi's own trading arm, and at least one
   solo operator described as among the largest Kalshi combo RFQ makers — who built it as a
   college junior. The barrier is modelling and engineering, not capital or colocation. That cuts
   both ways: it is reachable, and it is reachable by others.

---

## 7. PRE-REGISTERED GATES — run before any pod is written

Declared in advance, with kill thresholds fixed now.

### Gate 1 — FILL RATE (the binding gate). Cost: ~2 weeks, <$500 at risk.
Run a **shadow quoter**: subscribe to `communications`, price every combo RFQ in the target zone
with the model of §8, and log what we *would* have quoted. Simultaneously place real, small
resting offers (10–25 contracts) in target-zone combo markets at our model price.
- **KILL if** the realised fill fraction on resting offers is **< 10%** of target-zone RFQ flow
  we priced, or if fewer than 200 fills accumulate in 14 days.
- **KILL if** realised P&L per filled contract is **< +1.0 ¢** with a day-clustered CI excluding
  the measured +3.17¢.
- This gate is *cheap* and it is the one the house rule after P-017A requires. It cannot be
  skipped by reasoning about the tape.

### Gate 2 — RESTING INTERCEPTION works as the rulebook describes. Cost: ~1 day, <$50.
Place a resting offer in a combo market, have a second account RFQ that same market, and confirm
the resting order fills ahead of the accepted quote.
- **KILL the low-latency thesis if** resting orders do *not* take priority — which forces the
  sub-second RFQ-responder build and materially raises cost. (The edge would survive; the build
  path would not.)

### Gate 3 — CORRELATION MODEL beats independence. Cost: ~3 days on existing data.
Fit a Gaussian copula on the leg outcomes already in the archive and compare its combo-price
prediction against the independence product, scored on realised settlement.
- **KILL if** the copula does not beat independence on out-of-sample log-loss by a margin larger
  than the measured edge — because then risk 2 in §6 is unmanaged.

### Gate 4 — HAIRCUT measured in-zone. Cost: ~1 day.
Recompute first-print vs last-price **inside** the target zone, contract-weighted, day-clustered.
- **KILL if** the in-zone first-print edge is **< +1.5 ¢/ct** or its CI includes zero.

### Gate 5 — COLLATERAL confirmed. Cost: one email + one live contract.
Confirm the collateral charged on a short combo, and confirm no leg netting exists.
- **No kill threshold** — this sizes the strategy, it does not falsify it. But it must be a
  measured number before capital is committed.

**Standing amendment rule:** these thresholds may be tightened at any time and loosened only with
a written justification and a forward-only sample, per the P-022 precedent.

---

## 8. If it clears — the shape of P-029

Sketch only; the spec is `combo_research/SPEC_P-029_Combo_Maker.md`.

- **Universe:** target zone only — 2–4 legs, model fair value 10–35¢. **Hard exclusion above 60¢**
  (measured −2.46 ¢/ct) and below 5¢ (decaying, and rounding-fee hostile for the taker).
- **Pricing:** correlation-aware joint (Gaussian copula over leg outcomes) — never the
  independence product. Legs priced off the underlying Kalshi books, which are genuinely
  two-sided (396/400 sampled, median leg spread 1.00¢).
- **Execution:** resting interception first (Gate 2), RFQ response second.
- **Risk:** unhedged short book, full `(1 − price)` collateral, no leg netting assumed. Per-turn
  sizing off the §5 distribution, not per-contract Kelly. Day-level exposure cap because the book
  carries a large common factor (§4.4).
- **Counterparty profiling:** RFQ creator IDs are persistent and pseudonymous. Quote tighter to
  IDs whose flow settles as expected, wider or not at all to IDs that beat us. This is reportedly
  table stakes among existing responders and is the main defence against the sharp minority.
- **Settler:** required from day one. P-017 shipped without one and `on_settlement` was dead code.
  `scalar` must book at `settlement_value_dollars`, never as VOID.

---

## 9. Artifacts, and an archiving obligation

Scripts and caches from this session live in `/tmp/combo_research/` (universe pull) and
`/tmp/combo_research/kill/` (the kill gates: `k1_daily_book.py`, `k2*`, `k3*`, `k4_integrity.py`,
`k5_decay.py`, `k6_target.py`, with `.log` files carrying every number quoted above).

**Kalshi purges settled combo markets after ~3 months.** The 2.06 GB
`mve_markets_KXMVESPORTSMULTIGAMEEXTENDED.jsonl.gz` cache is the only copy of ~17.6M settled combo
outcomes outside Kalshi, and it cannot be re-pulled. Per CLAUDE.md's rule after the
`golf_quirks_research/` loss, it must be archived gzipped under `combo_research/archive/` and
committed, and `bash scripts/check_research_committed.sh` must pass before this line of work is
handed off. **A daily settled-combo archiver should start now regardless of the verdict** — every
day it does not run is a day of NFL/NBA season data that will not exist later.

---

## 10. Open questions this report could not answer

1. How many quoters answer a typical combo RFQ. Unknown, undocumented, and the single most
   important unknown for the strategy.
2. Whether incumbent quoters price correlation. Evidence conflicts; their aggregate profitability
   argues they do.
3. Same-game correlation structure — n=39, blocked until NBA/NFL season.
4. The exact collateral formula and whether any leg netting exists (§2.4).
5. Push-feed latency (`rfq_created` → local receipt). No published figures; must be measured.
6. Whether the June Combo Incentive Program was renewed after 2026-06-30.
7. Documentation contradictions banked for later: HVM timers (docs 3s/1s vs rulebook 2s/3s);
   "all trades are final" (help centre) vs a tradeable book (rulebook); partial quote acceptance
   (rulebook) vs full-size-only (API docs); fee schedule PDF date (Feb 5 2026 in body vs
   "July 2026" in metadata).

---

## Sources

Kalshi: [RFQ guide](https://docs.kalshi.com/getting_started/rfqs) ·
[OpenAPI spec](https://docs.kalshi.com/openapi.yaml) ·
[Communications WS](https://docs.kalshi.com/websockets/communications) ·
[MVE collections](https://docs.kalshi.com/api-reference/multivariate/get-multivariate-event-collections) ·
[Rate limits](https://docs.kalshi.com/getting_started/rate_limits) ·
[Fee rounding](https://docs.kalshi.com/getting_started/fee_rounding) ·
[Fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) ·
[Combos help](https://help.kalshi.com/en/articles/13823820-combos) ·
[Combo Incentive Program](https://help.kalshi.com/en/articles/15410257-combo-incentive-program) ·
[Market maker programme](https://help.kalshi.com/en/articles/13823819-how-to-become-a-market-maker-on-kalshi) ·
[CFTC RFQ rule filing](https://www.cftc.gov/sites/default/files/filings/orgrules/25/11/rules11252533483.pdf)

Reporting: [Bloomberg 2026-07-28](https://www.bloomberg.com/news/articles/2026-07-28/parlay-bets-saddle-gamblers-with-294-million-losses-on-kalshi) ·
[Sportico — retail parlay losses](https://www.sportico.com/business/sports-betting/2026/kalshi-parlays-retail-bettor-losses-rfq-1234894471/) ·
[Sportico — RFQ parlay explainer](https://www.sportico.com/business/sports-betting/2025/kalshi-parlay-combo-rfq-explainer-1234877038/) ·
[Sportico — Novig voided parlays](https://www.sportico.com/business/sports-betting/2025/novig-parlay-void-how-it-works-1234878306/) ·
[Courtney — counterparty profiling](https://whirligigbear.substack.com/p/are-traders-on-kalshi-being-profiled) ·
[Wizard of Odds — SGP correlation](https://wizardofodds.com/article/same-game-parlays-the-mathematics-of-correlation/) ·
[Skadden — Third Circuit](https://www.skadden.com/insights/publications/2026/04/third-circuit-affirms-kalshis-preliminary-injunction) ·
[CBS — state-by-state status](https://www.cbssports.com/prediction/news/prediction-market-legal-states/)
