# First-half soccer BTTS — Phase 1 mechanism rule (containment)

**Written 2026-08-16 BEFORE `btts_containment.py` existed and before any pair
was matched or any inversion counted.** Authorised by the Phase 0 PROCEED in
`BTTS_1H_FEASIBILITY_RULE.md` §3, which authorises writing a decision rule and
nothing else.

**This document authorises no pod, no quote, and no order.**

---

## 1. The mechanism, stated as a logical claim rather than a forecast

> If both teams score **in the first half**, then both teams have scored **in
> the match**. Therefore, on the same fixture,
>
> **P(BTTS_1H) ≤ P(BTTS_full)** — and likewise **P(BTTS_2H) ≤ P(BTTS_full)**.
>
> No model, no scoring rate, no forecast. The constraint is entailment.

The corresponding position, if the inequality is violated in prices, is:

```
BUY  YES on BTTS_full   at ask
SELL YES on BTTS_1H     at bid   (equivalently, buy NO on BTTS_1H)
payoff = 1{full} − 1{1H} ≥ 0  in every state of the world
```

A non-negative payoff acquired for a **net credit** is an arbitrage. The test
is whether `bid(1H) − ask(full)` is ever positive net of fees.

**This is deliberately the same archetype as the satellites WINS dutch-book**,
because it is the only archetype that has ever paid in this fund: *"only a
rulebook mechanic pointing the same way as the trade (P-017, P-022) has ever
paid for it."* It is the P-018 lesson applied — no win-probability model is
involved, so there is nothing for adverse selection to pick off.

## 2. Gate 0 — settlement containment must be VERIFIED, not assumed

**The entailment in §1 is a claim about Kalshi's settlement rules, not about
football.** It must be read out of the contract-terms documents for both
templates before any price is examined.

Specifically, containment **fails** — and the mechanism is void — if any
enumerated scenario lets `BTTS_1H` settle YES while `BTTS_full` settles NO or
voids. Candidate scenarios that MUST be checked explicitly:

* **abandonment after half-time** (1H determined, match unfinished),
* **void / cancellation rules that differ between the two templates**,
* any definition of "the match" in the full-match template that **excludes**
  the first half (e.g. a regulation-time carve-out that is not symmetric),
* `result="scalar"` partial settlement on either leg.

> **RULE:** read the `contract_terms_url` for each template and quote the
> governing clause **verbatim** in the report. A ticker name is not evidence —
> `GOLDENGLOBESNOM.pdf` is a RANKLIST rulebook, and the 2026-07 task brief that
> quoted golf H2H ties as "$0" when the PDF says $0.50/$0.50 is the standing
> reminder that **a quoted rule is not a read rule.**

**If containment is not airtight, this rule terminates at Gate 0 and no price
test is run.** A logical arbitrage with one leaking scenario is not a weaker
arbitrage; it is a short option with an unbounded-looking payoff table.

## 3. The attrition funnel — inherited verbatim from the satellites scanner

The stages, thresholds and their ORDER are taken from
`satellites_research/wins_scanner.py`, which was written before this question
existed. Nothing here is tuned to soccer.

| stage | definition |
|---|---|
| **F0** | fixtures where both a 1H (or 2H) and a full-match BTTS market are listed |
| **F0′** | **mid** inversions — `mid(1H) > mid(full)`, the naive "violation" |
| **F1** | both legs genuinely two-sided: bid > 0, ask < 1, size on both sides |
| **F2** | **executable** — survives crossing both spreads: `bid(1H) − ask(full) > 0` |
| **F3** | depth — ≥ 20 contracts available on every leg |
| **F4** | **net of fees** — clears the taker fee on both legs |
| **F5** | **persistence** — present in two snapshots ≥ 1 h apart |

**F4 is where this most likely dies, and the arithmetic is stated now rather
than discovered later.** BTTS is maker-free, but this is a **taker** trade —
it crosses two spreads — so the fee is `0.07·P·(1−P)` on each leg. At the
Phase 0 medians (`P_full = 0.525`, `P_1H = 0.1875`):

```
0.07 × (0.525×0.475 + 0.1875×0.8125) = 0.07 × 0.4017 = 2.81¢ per pair
```

So a violation must exceed **≈2.8¢** before it is worth anything, on top of
already having crossed both spreads. Fees come from
`src.kalshi_fees.fee_per_contract(price, maker=False, series_ticker=...)` at
the actual traded price — **never the 2.81¢ figure above**, which is an
illustration computed at the median, not a constant to hard-code.

## 4. PASS / KILL / NO DECISION

| verdict | condition |
|---|---|
| **KILL** | Gate 0 fails (containment not airtight in the PDFs) — terminal, at any F-stage |
| **KILL** | **F2 = 0** across all matched fixtures in both snapshots |
| **NO DECISION** | F2 > 0 but **F4 = 0** (violations exist but never clear fees), or F5 = 0 (nothing persists) |
| **ESCALATE** | F4 > 0 **and** F5 > 0 — real, executable, fee-clearing, persistent violations |
| **NO DECISION — SEASONAL** | fewer than 20 matched fixtures |

**ESCALATE is not "build".** It authorises exactly one thing: a written
capacity and execution study, because the satellites precedent is that a
surviving violation was still worth `$0` of capacity.

## 5. The prior, recorded before the number

**The identical funnel returned F2 = 0 on the WINS ladder** — 1,514 pairs, 118
mid inversions, 61 two-sided, **zero executable, zero persisting** — and it
returned F2 = 0 again on the 2026-08-16 preseason re-run at thicker liquidity
(1,658 pairs, 152 inversions, 93 two-sided). The base rate for this test
returning anything is, so far, zero for two.

**The one honest reason this case could differ**, stated so it can be checked
rather than invoked afterwards: WINS pairs are **adjacent strikes inside one
series ladder**, which a single market maker quotes jointly and therefore keeps
internally consistent by construction. 1H and full-match BTTS are **different
series** on the same fixture, and nothing forces one maker to quote both. That
is a mechanism for cross-series drift that does not exist within a ladder.

**It is a reason, not evidence.** If F2 = 0, this paragraph does not get
re-litigated into "we should look at 2H as well" — 2H is already in the funnel.

## 6. Anti-rationalisation

1. **No re-parameterisation.** F3's 20-contract depth floor and F4's fee
   treatment are inherited; they do not move after seeing F2.
2. **No sub-slicing.** Not "it works if you drop MLS", not "only in the top-5
   leagues". The all-in number is the number.
3. **A single snapshot is not a result.** F5 is a gate, not a robustness check.
4. **The mid-inversion count (F0′) is not a finding.** The satellites study
   found 118 of them and **zero were tradeable**; 112 recurred across snapshots
   as stable one- and five-lot artifacts on empty books. Reporting F0′ as if it
   were an opportunity is the specific error this row exists to prevent.
5. **Phase 2 is named but NOT authorised here** (§7). It does not become
   authorised because Phase 1 was disappointing.

## 7. What this rule explicitly does not cover

The other structural candidate — **the deterministic late-first-half time
bound**, i.e. at minute 44 with 0–0 the probability that both teams score
before half-time is near zero regardless of any model — is **not tested by this
rule and is not authorised by it.**

It is genuinely structural rather than a forecast, but it requires **in-play
soccer book capture that does not exist**: `src/book_capture.py:223` is
hardcoded to `["KXMLBGAME"]` at a **60-second cadence**, and the 2026-07-30 AFT
review already flagged that cadence as fatal for a minute-resolution soccer
signal. Testing it is a data-collection decision first and a research decision
second. Recorded here so that Phase 1's outcome is not read as a verdict on it.
