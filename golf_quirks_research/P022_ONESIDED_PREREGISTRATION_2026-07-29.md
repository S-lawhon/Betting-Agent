# P-022 — PRE-REGISTRATION: the one-sided-book stratification

**Written 2026-07-29T01:35Z. The first quote cannot be placed before
`2026-07-29T18:32Z` — 16.9 hours from writing.** Filed alongside
`P022_WIDENING_PREDECLARATION.md` and `P022_POI26_PREREGISTRATION_2026-07-28.md`,
for the same reason: a split decided after the numbers are known is not a
split, it is a selection.

Sam's decision, 2026-07-29: **quote, and pre-register the stratification now.**

---

## 1. The problem this exists to solve

P-022's validated edge (+3.41 ¢/ct, H=12, offset +0.02, 364 markets, 19
tournaments) was **never once measured off a one-sided ask**.
`quirks_common.candle_price` accepts an executed trade price or the mid of a
tight two-sided quote, and **rejects a one-sided book outright** — its
docstring says using one "fabricates edge". Count of backtest anchors taken
from a bare one-sided ask: **0 of 364**.

Live, measured 2026-07-28 on `KXLPGAR1LEAD-AIGWO26`: **143 of 146 markets
carry no resting YES bid at any price**, and **23 of the 24 in-band candidates**
will be priced through `_mid()`'s one-sided branch.

The pod is therefore about to trade a population its own research excluded.
Not necessarily a worse one — but a different one, and if all 24 gate
tournaments come from it, the gate will produce a confident verdict about a
strategy nobody validated, with no honest way to notice afterwards.

**This document makes that noticeable in advance.**

## 2. What is NOT changed

* **The primary verdict is untouched.** `P022_DECISION_RULE.md` §3/§4 stand:
  tournament-clustered net ¢/ct, contract-weighted within a tournament and
  equal-weight across, **T = 24**, `z ≥ 2.0` PASS, `z ≤ −2.0` hard kill.
* **The all-in number is computed and reported FIRST**, per §8.2, before any
  split. `p022_checkpoint.py` remains the only sanctioned verdict.
* **No pod parameter changes.** Band `(0.03, 0.12)`, offset `+0.02`, window
  `[12h, 24h]`, caps `0.5 / 5 / 15 %`, 13 series. No depth screen, no
  `_mid()` change. Nothing here resets T under §8.1.

**The stratification below is a DIAGNOSTIC, not a second verdict.** It cannot
promote P-022 and it cannot rescue a failing gate. It can only tell you
whether the number the gate produces describes the strategy that was
validated.

## 3. The attribution rule — fixed now, so it cannot be chosen later

Every `QUOTE` row has carried `book_side` since the 2026-07-28 deploy
(`two_sided` / `one_sided_ask` / `unpriceable` / `no_book`), alongside raw
`yes_bid`, `yes_ask`, `bid_qty`, `ask_qty`.

> **A FILL inherits the `book_side` of the last `QUOTE` row for the same
> ticker with `ts ≤ fill.ts`.**

That is exact, not approximate: a fill can only be against the quote that was
resting, and a QUOTE row is written whenever the quote is placed or
re-priced. The recorded sidedness is the reference the quote was **priced
off**, which is the quantity in question — not the book at the instant of the
fill, which is a different and irrelevant thing.

A fill with no preceding QUOTE row for its ticker is **`unattributed`** and is
counted in the all-in number but excluded from both strata. It is never
silently assigned to either.

**Reader:** `scripts/p022_book_side_split.py`. A rule with no reader is
documentation, not a gate.

## 4. The two hypotheses, with the numbers they predict

The live population is ~96% one-sided, so there will almost certainly be **no
meaningful two-sided stratum to compare against internally**. Stated up front
rather than discovered at T=24. The comparison is therefore against the
backtest's own cells:

| hypothesis | predicts live net ¢/ct | source |
|---|---:|---|
| **H_validated** — the headline transfers | **+3.41** | Phase-2 H=12 off +0.02, all 364 |
| **H_onesided** — live resembles the excluded cell | **−1.48** | bare-one-sided-at-T cell, 117 markets, CI [−11.93, +8.15] |

At T = 24, with the tournament-clustered 95% CI on the all-in number:

| result | pre-committed reading |
|---|---|
| CI **excludes −1.48** and contains/exceeds +3.41 | **The concern is discharged.** One-sided references behave like the validated population for this family. Write the scoped exception to the "two-sided quotes only" rule (§6). |
| CI **excludes +3.41** and contains −1.48 | **The gate is measuring a different strategy.** The all-in verdict does NOT validate the Phase-2 thesis, whatever its sign. P-022 must be re-registered as a new pod ID restricted to books the research covered, T back to 0. |
| CI contains **both** | **Under-powered — NO DECISION on this question**, regardless of what the primary gate says. The gate detects roughly ±5 ¢ at T=24 and these hypotheses are 4.9 ¢ apart, so this is the *expected* outcome and must not be read as reassurance. |
| CI excludes **both** | Report it and stop. Neither model describes live; nothing here licenses a next step. |

**The third row is the likely one and saying so now is the point.** A
pre-registration that only anticipates decisive outcomes is a way of
guaranteeing the ambiguous one gets argued about later.

## 5. The EARLY diagnostic — readable long before T = 24

Edge needs 24 tournaments. **Fill rate does not**, and it is the number that
separates the two cells most sharply:

| backtest cell | fill rate (filled ÷ posted markets) |
|---|---:|
| traded, two-sided | **67%** |
| traded, one-sided ask | 47% |
| **bare one-sided ask** | **15%** |

> **Pre-registered: after the first 5 tournaments carrying ≥ 1 quote, report
> the live fill rate on one-sided-referenced quotes.**
>
> * **≥ 40%** — live resembles the *traded* cells. Weak positive evidence for
>   H_validated. Continue.
> * **≤ 25%** — live resembles the excluded cell. **This is a stop-and-report
>   trigger**: bring it to Sam before T = 24, because continuing to spend gate
>   tournaments on a population that fills like the −1.48 ¢ cell is a choice,
>   not a default.
> * between — continue, report at T = 24.

This is deliberately NOT a kill. Fill rate is not edge, and P-017A's lesson
cuts the other way here: *a real edge you cannot fill is not an edge*, so a
low fill rate is informative about capacity even when the sign is fine.

**Denominator note, fixed now:** a "posted market" is a ticker that received
≥ 1 QUOTE row within a tournament; "filled" is ≥ 1 FILL row for that ticker.
Same definition as `quirks_common.replay`'s `posted_markets` / `filled_markets`,
so the live number and the backtest number are the same quantity.

## 6. What a discharge licenses, and what it does not

If §4 row 1 obtains, the "two-sided quotes only" rule gets a **scoped written
exception**, in these terms and no wider:

> Kalshi golf round-leader props (`KX*R{1,2,3}LEAD`) are exempt from the
> two-sided-quote requirement, because the family is structurally one-sided
> (143 of 146 markets on a measured event carry no resting YES bid at any
> price) and the exemption was validated forward on T = 24 tournaments.

It licenses nothing about any other family, and nothing about real money.

## 7. Anti-fitting clauses

1. **This document may not be revised after the first quote rests.** Its git
   commit timestamp precedes `2026-07-29T18:32Z`; check it.
2. **The strata are defined by `book_side`, a field recorded by the pod at
   quote time** — not by any property computed after settlement, and not by
   outcome. Neither stratum can be defined to contain a result.
3. **No third stratum may be added later.** `unpriceable` / `no_book` quotes
   cannot occur (a quote requires a priceable mid); if one appears it is an
   anomaly to report, not a bucket to analyse.
4. **The all-in number is reported first and is the verdict** (§8.2). If the
   two disagree, both are reported; the split does not overturn the gate, it
   qualifies what the gate measured.
5. **A failure to run is not a pass.** If the reader cannot attribute fills,
   it returns `None` and the question stays open — it does not default to
   "discharged".

## 8. Known limitation, recorded now

Even a clean discharge leaves one thing untested. On the bare-one-sided class
the backtest anchors to a **stale** clean candle — median 1.0 h old, p90
**40 h**, max **249 h** — whereas the pod quotes today's ask. So the two are
never quite the same estimator, and no live result can fully close that gap.
The stratification tests whether the *edge* transfers, not whether the two
*methods* agree.

**Also unaddressed by design:** the pod has no size or depth screen, and the
size resting ahead of its quote is bimodal (median 13 contracts, max 1,122).
Adding one is an §8.1 change and is not part of this registration.
