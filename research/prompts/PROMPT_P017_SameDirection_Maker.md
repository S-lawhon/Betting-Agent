# PROMPT — P-017A as a same-direction maker

**Origin:** `fee_parabola_research/REPORT_Fee_Parabola_2026-07-28.md`. Offline,
no new data pull, no live pod change. **Slot into the 07-30 queue.**

## The question, stated narrowly

P-017A lifts the ask at the anchor and pays a **taker fee of 1.04¢/ct** (its own
backtest: gross +8.18¢ − net +7.14¢) — independently estimated at **1.20¢/ct**
from the trade tape. `KXPGATOP10` and `KXPGATOP20` are `quadratic`, so **a maker
pays zero.** That 1.04¢ is **15% of P-017's gross edge** and it is the only
recoverable fee anywhere in the live book.

> **Does resting a YES bid at the anchor beat lifting the ask, once non-fill is
> paid for?**

This is **not** Leg B. Leg B rested a YES *offer* — the opposite side, a fade —
and returned +3.34¢ with a CI straddling zero against Leg A's +7.14¢. The
same-**direction** maker has never been run.

## Hard pre-registration — write this BEFORE computing any effect

Commit `golf_research/P017_MAKER_PREDECLARATION.md` first, containing: the
statistic, the comparison, the thresholds, the admissibility rule, the fill
model, and the quote size. **Then** run. A number seen before the declaration is
committed invalidates the study — amend only in the open, with the reason, as the
P-022 widening did.

## The two design traps — both have precedent here

### 1. Per-contract is the WRONG statistic. Pre-register per *posted market*.

A maker that fills 30% of the time and earns +9¢ on its fills is **worse** than a
taker that fills every time at +7.14¢ — you forgo the taker's edge on the 70% you
never filled. Report all four numbers and make the **primary** statistic
`total net cents / posted market`:

| | must report |
|---|---|
| posted markets | every market where a quote would have rested |
| filled markets | where the resting bid actually traded |
| fill rate | filled / posted |
| net ¢/ct **on fills** | the seductive number — secondary |
| **net ¢ per POSTED market** | **the primary statistic** |

The taker baseline fills ~100% by construction, so its per-posted and per-filled
numbers coincide. That asymmetry is the entire point of the comparison.

### 2. Compare PAIRED on the same markets, never two independent means.

Leg A's CI is **[+4.30, +9.90]** — a spread of 5.6¢ around an effect you are
trying to resolve to **1.04¢**. Two unpaired means can never settle this.

For every market in the Leg A universe, compute the taker outcome and the maker
outcome **on that same market**, take the per-market difference, and bootstrap the
**difference**, clustered by tournament. Report `Δ = maker − taker` with its own
CI. **The sign and CI of Δ is the finding**; the two levels are context.

## Method

- **Universe and anchor: identical to Leg A.** Series `KXPGATOP10`, `KXPGATOP20`;
  band **0.08–0.45** (the shipped band); the same 4–10 day pre-tournament anchor
  window; 12 events. Change nothing else — this is a one-variable experiment.
- **Reuse the existing fill model. Do NOT write a third harness.**
  `golf_quirks_research/backtest_topn_fade_fills.py` already rests quotes on these
  exact series with `quote_size: 25.0` and `maker_fee: 0.0`; it does the sell
  side. **Invert it to the buy side** and say in the report exactly which lines
  changed. If inverting proves unsound, stop and report why rather than building
  new machinery.
- **Quote placement:** rest at the anchor bid, and separately at `anchor − offset`
  for the same offset grid the fade study used. Report the grid; do not pick the
  best cell and present it as the result.
- **Fees from `src/kalshi_fees.py` with `series_ticker` passed.** Do not hard-code
  a maker fee, and do not hard-code zero — the module and the fixture are the
  authority now, and the fixture is the thing that killed five drifts.
- **Anchor contemporaneity** must be checked and reported, per the house rule. The
  make-cut study's "48h anchor" was a median 68h-old price and produced a pure
  +9.5¢ artifact.
- **Statistics** clustered by tournament, two-sided quotes only, screened on
  top-of-book size rather than spread.

## Adverse selection — measure it, do not assume it

The tape says **13.3% of in-band volume would fill a resting YES bid**, and that
bid-filling flow shows **no** penalty (YES-buyer raw **+0.23¢** vs **−1.41¢** on
ask-lifting flow) — but **both CIs straddle zero, so the tape cannot settle it.**
That is why this study exists.

Inside the backtest, split filled from unfilled markets and report the realised
settlement rate of each. **If fills are systematically the losers, that is Leg B
repeating and it should be named as such**, not buried in a per-contract mean.

## Pre-registered decision rule

| Δ (maker − taker), per posted market, tournament-clustered | verdict |
|---|---|
| **≥ +1.0¢ with CI excluding zero** | **ADVANCE** — present to Sam as evidence for an execution change |
| CI includes zero | **NO DECISION** — record and stop |
| **≤ −1.0¢ with CI excluding zero** | **KILL** — the fee is worth paying; close the question permanently |
| fill rate **< 25%** | **KILL regardless of Δ** — the strategy cannot deploy capital |

The theoretical ceiling on Δ is **+1.04¢** (the fee). **A result above +1.5¢ is a
red flag, not a triumph** — it means the maker model is capturing something other
than the fee saving, most likely a look-ahead in the fill logic. Investigate
before reporting it.

## What an ADVANCE does NOT authorise

**It does not flip the live pod.** P-017's gate counts **settled tournaments**
(currently 1 of 8), and changing execution mid-gate is a spec change. **Check the
locked document for whether that resets the count, and state the answer in the
report** — do not assume it either way. Buying 1.04¢/ct at the price of resetting
the fund's second-oldest forward gate is a trade Sam decides, not the study.

## Stop rule

**STOP at the report.** No pod change, no config change, no deploy. One variable,
one comparison, one verdict.

## Deliverables

- `golf_research/P017_MAKER_PREDECLARATION.md` — committed **before** any effect number
- `golf_research/REPORT_P017_Maker_2026-07-30.md` — Δ with clustered CI, the four
  fill statistics, the offset grid in full, the adverse-selection split, the
  anchor-contemporaneity check, and the gate-reset answer
- Params JSON alongside, and `bash scripts/check_research_committed.sh` clean

## Companion task, not in scope here

Once the deploy key is available, pull the **live fee bill by pod** from the
droplet trade log — P-001 and P-015 trade near the fee peak on maker-**charging**
series (tennis match markets: VWAP 0.519, taker 1.156¢/ct, maker 0.289¢/ct). The
local trade log is from 2026-03-13, so this audit could not measure it.
