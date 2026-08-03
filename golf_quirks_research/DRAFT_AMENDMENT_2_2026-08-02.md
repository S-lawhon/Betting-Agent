# DRAFT — Amendment 2 to `P022_DECISION_RULE.md`

> **STATUS: UNAUTHORISED DRAFT. NOT IN FORCE.**
> `P022_DECISION_RULE.md` is unchanged and `T = 24` remains the live gate.
> Nothing here takes effect until Sam authorises it in writing, in the rule
> file itself, the way Amendment 1 was.
>
> Written 2026-08-02 by Claude, at Sam's request, after the estimator defect
> in `quirks_common` was fixed (commit `e47d8e9`).

---

## 1. Why this draft exists

Amendment 1 (2026-07-28) set `T = 24` as "the smallest sample with 90% power
against the effect actually measured", solving §5's

```
T = ((2.0 + z_power) * sigma / d)^2
```

with `d = +2.57¢/ct` and `sigma = 3.781¢/ct`.

**Both inputs came from the pooled estimator, and both were wrong for this
purpose.**

- `d = +2.57¢` is the **pooled** contract-weighted mean. The rule's own
  statistic (§2/§3, equal weight per tournament) on that same widened sample
  is **+1.45¢**.
- `sigma = 3.781¢` was back-derived from the Phase-2 bootstrap CI
  `[+1.7, +5.1]` — which was the **pooled statistic's interval**, and is
  therefore too narrow. The between-tournament SD is now measured directly:
  **5.04¢** on the published sample, **10.44¢** on the widened one.

So the power calculation behind `T = 24` used an effect that is too large and
a dispersion that is too small — the two errors compound in the same
direction, both making the required sample look smaller than it is.

This is a defect in the *arithmetic inputs*, discovered independently of any
forward result. It is not a reaction to how the forward test is going.

## 2. THE INTEGRITY PROBLEM — read this before the numbers

**Amendment 1's legitimacy rested on being made at `T = 0`.** It says so
verbatim: *"Made at T = 0, with zero forward observations in existence — the
only moment this change can be made without touching evidence."*

**That is no longer true. `T = 2`.**

```
P-022 checkpoint, 2026-08-02 (production):
  tournaments (T): 2    contracts: 109
  tournaments +ve: 1/2
  edge           : -2.18 c/contract (equal-weighted across tournaments)
  between-tourn sd: 6.97 c   se: 4.93 c   z = -0.44
  VERDICT: NO DECISION
```

Two observations decide nothing — `z = -0.44` at `T = 2` is noise, and the
rule correctly refuses to read it. But their *existence* changes what this
amendment may claim. Any change to `T` from here is made with forward
evidence in existence, and that evidence is currently **negative**.

The structural protection is direction: **a correction that RAISES the bar
cannot be goalpost-moving**, because it makes the strategy harder to pass at
a moment when it is not passing. That protection covers Options B, C and D
below. **It does not cover Option A, which lowers the bar to `T = 19`.**
Adopting Option A now would be indistinguishable from easing a gate that a
strategy is currently failing, whatever its statistical motivation. I would
not sign it, and I do not recommend it.

## 3. The corrected arithmetic

Method is §5's, unchanged. `z_power = 1.2816` (90%), `0.8416` (80%),
critical `z = 2.0`. Reproduces Amendment 1's `T = 24` exactly from its own
inputs, which is the check that this recomputation is faithful.

| basis | d (¢/ct) | sigma (¢/ct) | T @ 90% | T @ 80% | power at T=24 |
|---|---:|---:|---:|---:|---:|
| Amendment 1 (pooled) — **what T=24 rests on** | +2.57 | 3.78 | **24** | 18 | 90.8% |
| CORRECTED published 364 (T=19) | +3.80 | 5.04 | **19** | 15 | 95.5% |
| CORRECTED widened 404 (T=22) | +1.45 | 10.44 | **559** | 419 | **9.3%** |

Half-effect basis (the `T = 40` analogue, 80% power): pooled → 70;
corrected published → 57; corrected widened → **1,675**.

**The single most important number in this table is 9.3%.** If the corrected
widened sample is the right description of the effect, the live gate has
essentially no power against it. A null result at `T = 24` would then be
uninformative — which is the exact failure §5 exists to prevent when it says
a sample below the powered threshold must be NO DECISION rather than a weak
KILL.

**The second most important fact is the spread: 19 versus 559.** Those are
the same rule, the same method, the same corrected statistic — applied to two
samples that differ only by 40 markets. A 29× disagreement in required sample
is not a detail to be split; it says the effect size is not pinned down well
enough to power a test against at all.

## 4. Options

**Option A — `T = 19`** (corrected published basis).
*Not recommended, on integrity grounds.* Lowers the bar while forward
evidence is negative (§2), and abandons the widened sample that Amendment 1
deliberately adopted as the more honest basis. Reverting to the friendlier of
two samples, after correction, at a moment of negative evidence, is the
pattern pre-registration exists to prevent.

**Option B — `T = 559`** (corrected widened basis, faithful to Amendment 1's
choice of basis plus the corrected statistic).
Internally consistent and maximally conservative. But at §5's measured
cadence — 13.7 qualifying tournaments/month, itself an *upper bound* that
assumes every listed tournament is quoted, filled and settled — this is
**~3.4 years minimum**. Adopting it is functionally a decision to retire the
strategy while calling it "still testing". If that is the decision, it is
more honest to retire it explicitly.

**Option C — keep `T = 24`, record it as underpowered.**
Changes no threshold; amends only the *claim*. `T = 24` stops being "90%
power against the measured effect" and becomes "a fixed budget with 9.3%
power against the widened corrected effect, 95.5% against the published one."
Cheapest and cannot be goalpost-moving, but it leaves a gate whose null
result the rule itself would call uninformative — and the pod keeps consuming
paper capacity and attention on a test that mostly cannot conclude.

**Option D — resolve `d` before setting `T`. (Recommended.)**
The 19-vs-559 spread is the finding. Do not set a threshold from a contested
effect size; establish which sample describes the strategy first. Concretely:

1. Record that `T = 24`'s stated justification is void, and that no powered
   threshold is in force pending re-derivation. Keep `T = 24` as an interim
   floor — no decision below it — so nothing is *eased* in the interim.
2. Determine why published and widened diverge so violently. `sigma` doubles
   (5.04 → 10.44) when 40 markets are added and `T` goes 19 → 22. That is not
   ordinary sampling noise; three added tournaments should not double
   between-tournament dispersion. Something in the added block is either a
   different regime or a data defect. `analyze_p022_added_block.py` exists for
   exactly this question and should be run and read before any threshold is
   set.
3. Only then re-derive `T`, in a further written amendment, from whichever
   basis survives that inspection.

This defers the threshold decision rather than making it under a number
nobody currently trusts, and it eases nothing in the meantime.

## 5. What this draft does NOT touch

Unchanged in every option: the band, the offset, the window, the series set,
every §7 cap, the HARD KILL at `z <= -2.0`, the single-extension structure,
and the requirement that a pass needs `z >= 2.0`. The estimator fix
(`e47d8e9`) changed how the statistic is *computed*, never what the gate
*is*.

Also unchanged: **Phase 2's GREEN-LIGHT stands.** On the published sample the
pooled and corrected estimators agree in conclusion — +3.41¢ vs +3.80¢, both
`z > 3`. Nothing here reopens that decision.

## 6. What I recommend

**Option D**, and I would not adopt Option A under any framing.

The honest summary is that this is not a "retune T" situation. The estimator
fix did not shift a threshold by a few tournaments; it revealed that the
effect size the threshold was derived from is not established. `T = 24` was
answering a question with a number that turns out to have a 29× uncertainty
band around it, and the forward evidence so far — 2 tournaments, −2.18¢ — is
at least not arguing that the larger estimate is the right one.

— *Requires Sam's written authorisation. Until then `T = 24` stands as
written and this file is a proposal, not a rule.*
