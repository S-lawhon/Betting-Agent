# DRAFT — Amendment 2 to `P022_DECISION_RULE.md`

> **STATUS: AUTHORISED BY SAM 2026-08-02 AND NOW IN FORCE.**
> Written into `P022_DECISION_RULE.md` as **AMENDMENT 2**; that file, not this
> one, is the rule. This document is retained as the derivation and the record
> of the rejected alternatives.
>
> **ONE FIGURE IN THIS DRAFT WAS WRONG.** §4.3 below proposed an extension of
> `T = 55`, asserted rather than solved. Solving the same criterion gives
> **T = 98** (808 filled markets); at T = 55 the power against half-effect is
> 55.5%, reproducing the ~53% defect Amendment 1 flagged at T = 40. **The rule
> as enacted uses 98.** Corrected before the amendment took effect.
>
> Written 2026-08-02 by Claude at Sam's request, after the estimator defect in
> `quirks_common` was fixed (`e47d8e9`). Revised the same day to fold in the
> added-block investigation this draft originally deferred.

---

## 1. Why `T = 24`'s stated justification is void

Amendment 1 set `T = 24` as "the smallest sample with 90% power against the
effect actually measured", solving §5's

```
T = ((2.0 + z_power) * sigma / d)^2
```

with `d = +2.57¢/ct` and `sigma = 3.781¢/ct`. **Both inputs came from the
pooled estimator.**

- `d = +2.57¢` is the pooled contract-weighted mean. The rule's own statistic
  (§2/§3) on that same sample is **+1.45¢**.
- `sigma = 3.781¢` was back-derived from the Phase-2 bootstrap CI
  `[+1.7, +5.1]` — the **pooled statistic's interval**, and therefore too
  narrow. Measured directly, between-tournament SD is **5.04¢** (published)
  and **10.44¢** (widened).

Both errors push the same way, making the required sample look smaller than
it is. Recomputing with §5's own method — which reproduces `T = 24` exactly
from Amendment 1's inputs, confirming fidelity:

| basis | d | sigma | T @ 90% | power at T=24 |
|---|---:|---:|---:|---:|
| Amendment 1 (pooled) | +2.57¢ | 3.78¢ | **24** | 90.8% |
| corrected published 364 | +3.80¢ | 5.04¢ | **19** | 95.5% |
| corrected widened 404 | +1.45¢ | 10.44¢ | **556** | **9.3%** |

`T = 24`'s justification does not survive. What replaces it is the subject of
§3–§4.

## 2. Integrity constraint

**Amendment 1 was legitimate because it was made at `T = 0`** — verbatim,
*"the only moment this change can be made without touching evidence."*

**That no longer holds.** Production checkpoint, today:

```
tournaments (T): 2   contracts: 109   tournaments +ve: 1/2
edge: -2.18 c/ct (equal-weighted)   sd 6.97c   se 4.93c   z = -0.44
VERDICT: NO DECISION
```

Two observations decide nothing, but their existence constrains what may be
proposed. The forward evidence is currently **negative**.

The protection is direction: **a correction that RAISES the bar cannot be
goalpost-moving.** The proposal in §4 raises the tournament requirement from
**24 to 33**. It makes the strategy harder to pass, at a moment when it is not
passing. Any variant that lowered the bar — notably the `T = 19` implied by
the corrected *published* sample — is rejected on these grounds regardless of
its statistical merit.

## 3. The investigation: why 19 and 556 disagree

The 29× spread was the reason this draft originally declined to set a
threshold. It has a specific, identifiable cause.

**It is one tournament.** `KIN26` aggregates to `x_t = -38.49¢/ct`, against a
worst published tournament of `-11.86¢`. Drop it and the widened sample reads
`+3.36¢, sd 5.56¢ → T = 30` — back in line with the published picture.

**But it cannot be dropped, and no filter removes it.** `KIN26` holds 53.7
filled contracts, so it survives any contract-count screen. Screening makes
matters worse, not better:

| inclusion rule | T | mean x_t | sd | T @ 90% |
|---|---:|---:|---:|---:|
| all tournaments | 22 | +1.45¢ | 10.44¢ | 556 |
| drop worst (KIN26) — *post-hoc, not legitimate* | 21 | +3.36¢ | 5.56¢ | 30 |
| ≥10 filled contracts | 20 | +0.90¢ | 10.79¢ | 1,552 |
| ≥50 filled contracts | 17 | **−0.06¢** | 11.47¢ | — |

No defensible inclusion rule stabilises the estimate, and the strictest one
sends the mean to zero. **The instability is not a data-quality problem to be
screened away. It is the shape of the payoff.**

**The payoff is a rare, large loss.** Across 183 filled markets:

```
adverse events (faded name won) : 7  = 3.83% of filled markets
mean filled quote               : 7.66c  -> a winner costs 12.1x the credit
P&L from those 7 events         : -$159.50
P&L from all 183 fills          : +$110.12
x_t skewness                    : -2.60      (median +5.00c vs mean +1.45c)
```

Most tournaments are profitable; the mean is dragged by rare disasters. `KIN26`
is not an anomaly — it is one draw from the tail, landing in a tournament with
few filled markets so nothing dilutes it.

**This is why §5's arithmetic is the wrong instrument.** `z = d√T/sigma` and
`power = Φ(z − 2.0)` are normal-theory formulas. With skewness −2.60 and an
estimate resting on **7 events**, the sampling distribution of `mean(x_t)` at
T = 20–40 is still materially skewed, so a normal-theory `T` is untrustworthy
in *either* direction. The 556 is not a real requirement; it is the formula
failing on a distribution it does not fit.

## 4. The re-derivation

**The unit that carries the information is the filled market, not the
tournament.** The strategy sells a ~7.7¢ tail and pays ~12× when it lands. Its
edge is the gap between the price and the true rate:

```
edge = q - p     q = mean filled quote = 0.0766
                 p = P(faded name wins) = 7/183 = 0.0383

edge = +3.83c    SE = 1.42c    z = 2.70    95% CI [+1.05, +6.61]c
```

Significant, and consistent with the corrected *published* equal-weight figure
(+3.80¢) — the two independent routes agree.

**Sample required for 90% power at critical z = 2.0: 270 filled markets.**
183 are in hand. At the observed 8.3 filled markets per tournament that is
**≈ 33 tournaments**.

This is not a new methodology invented to rescue a number.
`analyze_p022_added_block.py` already identifies the market-level binomial as
**"THE RIGHT TEST"** and the tournament-clustered permutation on a thin block
as **"THE WRONG TEST … an artifact."** This applies the instrument the
research already named, and it happens to raise the bar.

**Why tournament clustering can be relaxed *for this quantity* — and the
condition under which it cannot.** Clustering exists to guard against
correlated adverse events: one hot wave producing several winners at once. In
183 filled markets that is **not observed** — the 7 adverse events fall in **7
distinct tournaments, exactly one each**. With only 7 events the power to
*detect* clustering is low, so this is a working assumption, not a proven
one. It must be monitored, not assumed. Hence the guard below.

### Proposed amendment

1. **`T = 24`'s justification is struck.** It was derived from a pooled effect
   size and a pooled-CI dispersion, and is not "90% power against the measured
   effect."
2. **The gate threshold becomes `T = 33` tournaments** (equivalently ≈270
   filled markets, whichever is reached later), derived from the market-level
   adverse-rate calculation above. This *raises* the bar from 24.
3. **The single extension moves `T = 40 → T = 98`** (808 filled markets),
   preserving §5's structure of "80% power against half the measured effect"
   on the same market-level basis:
   `n = (2.0 + 0.8416)² · p(1−p) / (edge/2)² = 808` → `808 / 8.32 = T = 98`.
   *(This draft originally said 55, asserted without solving. See the banner.)*
4. **New guard — the clustering diagnostic.** At each checkpoint, report
   adverse events per affected tournament. **If that ratio exceeds 1.5, the
   market-level basis is void** and the threshold reverts to the conservative
   tournament-level requirement computed at that time. This is the falsifier
   for §4's central assumption, and it is checked continuously rather than
   assumed once.
5. **Unchanged:** band, offset, window, series set, every §7 cap, `z >= 2.0` to
   pass, HARD KILL at `z <= -2.0`, and the single-extension structure.

## 5. What this does not touch

**Phase 2's GREEN-LIGHT stands.** On the published sample the pooled and
corrected estimators agree in conclusion (+3.41¢ vs +3.80¢, both `z > 3`), and
the market-level route agrees again (+3.83¢, `z = 2.70`). Nothing here reopens
that decision.

**No parameter of the strategy changes.** This amendment concerns only how
much evidence is required before a decision, and by what arithmetic.

## 6. Residual risk, stated plainly

- **The market-level basis rests on 7 events.** `p = 3.83%` has a wide relative
  uncertainty, and the required-sample figure inherits it. §4.4's diagnostic is
  the check, and it should be read at every checkpoint rather than at the end.
- **Independence within a tournament is supported but not proven.** 7 events in
  7 tournaments is consistent with independence and also consistent with mild
  clustering that 7 events cannot resolve.
- **A tail strategy can look profitable for a long time and then not be.** The
  edge is +3.83¢ against a 12× downside at a 3.83% rate; the whole result is
  the difference between roughly +$270 of credit and −$160 of tail. Neither the
  old gate nor this one changes that, and no sample size makes it go away.

— *Requires Sam's written authorisation. Until then `T = 24` stands as written
and this file is a proposal, not a rule.*
