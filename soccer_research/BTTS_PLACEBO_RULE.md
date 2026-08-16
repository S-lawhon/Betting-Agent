# BTTS decay — placebo and scalar correction, PRE-REGISTERED

**Written 2026-08-16 AFTER the Phase 2 PASS and BEFORE either test was run.**

Adding a test that can only KILL is always admissible; loosening a threshold
after a result never is. This is the same move `P018_GATE1_REDESIGN.md` made
after P-018's +9.09¢ headline — and there, the placebo killed it.

---

## 1. Why this exists

Phase 2 returned **+4.52¢/ct, z = 3.71**. Two things make that insufficient:

1. **Amendment 1 removed the close-anchored window**, so the strategy is no
   longer "late-half decay." It is *"be systematically short in-play first-half
   BTTS, every open minute."* That is a **generic short-longshot premium**, and
   nothing in the Phase 2 test distinguishes "first-half BTTS is mispriced"
   from "selling any in-play soccer longshot on Kalshi pays."
2. **P-018 measured +9.09¢ at z = 4.91 with a CI excluding zero and was killed
   by exactly this test.** The identical construction, applied where the
   mechanism should not work, earned *more*.

## 2. Defect found and corrected: scalar settlements were dropped

`btts_decay.py` filtered `result in ("yes","no")`, silently discarding
`result = "scalar"` — **5 of 433 markets (1.2%)** in a five-series probe.

Per CLAUDE.md, **`scalar` is a PARTIAL PAYOUT at `settlement_value_dollars`,
never a void and never $0.** Booking it as absent is the same error that made
`KalshiGolfSettler` record $0 on every scalar and erase the events P-022 exists
to harvest. Here scalars are the abandonment / "last fair price" branch
documented in the Phase 1 report — **the branch I documented and then wrote a
harness that ignored.**

**The direction flatters the result**: a dropped scalar is a dropped *loss*
category for a short position. The correction can only move the edge down or
leave it unchanged.

> **RULE:** a short YES filled at `q` against a scalar settling at `v` books
> `q − v`. A scalar with **no** `settlement_value_dollars` is **EXCLUDED with a
> logged reason, never defaulted to 0 or 1.**

## 3. The placebo — Gate P

**Construction.** The **identical** replay — same one-tick offer inside the
ask, same strictly-through fill rule, same maker fee call, same match
clustering, same gate estimator — run on **full-match BTTS** (`KX*BTTS`) on the
same fixtures over the same period.

Full-match BTTS is the correct control because it is the market whose one-line
kill this whole workstream reopened, it prices at a **median mid 0.525** rather
than 0.1875 — so it is *not* a longshot — and it shares venue, fee regime, tick
and settlement rulebook (Phase 1 proved the rulebook is literally the same
document).

**Pre-registered decision, applied mechanically:**

| result | verdict |
|---|---|
| placebo net ≥ **50%** of the first-half arm **and** the match-clustered CIs **overlap** | **KILL.** There is no first-half effect; the edge is a generic short-longshot premium and the workstream's premise is refuted. |
| paired difference (1H − full), match-clustered, CI **excludes 0** positive | **PASS Gate P.** The first-half effect is separable. |
| anything else | **NO DECISION** — Gate P unresolved, and Phase 2's PASS does not advance. |

**A second placebo, reported but not a gate:** the same construction on the
**buy** side (offer to BUY YES one tick inside the bid) on first-half markets.
If *both* directions earn, the harness is measuring spread capture or a fill
artifact, not a directional edge. Reported whatever it says.

## 4. Committed in advance

1. **Both arms are produced by ONE code path in ONE run.** A placebo computed
   by a second implementation measures the implementation — `P018_GATE1_REDESIGN.md` §5.
2. **No sub-slicing to rescue Phase 2** if Gate P kills. Not "it still works in
   Brasileirão."
3. **A Gate P KILL outranks Phase 2's `z`**, exactly as a gate-#1 KILL outranked
   P-018's z = 4.91. A real number measuring the wrong thing is the failure
   this fund has made repeatedly.
4. **The scalar correction is applied to BOTH arms**, and the corrected
   first-half number replaces +4.52¢ as the headline wherever it is quoted.
