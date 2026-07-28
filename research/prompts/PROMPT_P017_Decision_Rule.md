# PROMPT — P-017 decision rule, written blind-ish and honestly

**P-017 is the only pod with live-shaped evidence and no locked line.**

## The situation

P-017's gate counts **settled tournaments (1 of 8)**. It has **no decision
rule**. And its one settled tournament came in at **−9.89¢/ct on 2,276
contracts** against a **+6.8¢/ct** backtest baseline.

That combination is the worst possible order of events: a pod with a result and
no rule. **Every day it stays unruled is a day the rule can be fitted to the
outcome** — and the one outcome that exists is negative, which biases a
freshly-written rule toward leniency just as surely as a positive one would bias
it toward strictness.

## Blindness is already partly lost — say so, do not pretend otherwise

The −9.89¢ figure is in committed documents and cannot be unseen. **Do not
attempt a blind rule and claim it.** Instead:

1. **State at the top exactly what is known**: n=1 tournament, −9.89¢/ct, 2,276
   contracts, 38 positions, versus a +6.8¢ backtest on 12 events.
2. **Derive every threshold from a source that predates or is independent of
   that observation.** For each threshold, name the source: inherited verbatim
   from the locked P-015/P-022 documents (`z ≥ 2.0` PASS, `z ≤ −2.0` hard kill),
   or solved from §5's power criterion using the **backtest** effect size and
   variance — never from the settled tournament.
3. **Run the arithmetic the P-022 amendment ran.** T = 8 was set when? Against
   what effect? Re-solve the same power criterion with the backtest's own
   +7.14¢ and its variance and report what T *should* be. If it comes out above
   8, **say so and hand Sam the choice** — raising a threshold before evidence
   accumulates is conservative and is the same move Amendment 1 made at T=0.
   Lowering it would not be.

**A rule that any reader can re-derive without seeing the −9.89¢ is defensible.
One that cannot be is not, however carefully it was written.**

## What the rule must specify

1. **The statistic** — tournament-clustered net ¢/ct, fee-net at the actual
   traded price. P-017A is a **taker** paying ~1.04–1.20¢/ct; the fee is real
   here, unlike everywhere else in golf.
2. **The clustering unit** — tournament, and state the effective n. 2,276
   contracts in one tournament is **one** observation.
3. **PASS / KILL / NO DECISION** thresholds, plus a hard kill that can fire
   before T is reached.
4. **Admissibility** — which tournaments count. Address explicitly:
   - the **JDAY re-book** precedent (the gate keys off `action`, not `outcome` —
     a fix that sets the wrong field moves nothing);
   - `result="scalar"` as a partial payout, never a void;
   - whether a tournament with a §7 cap breach is excluded, as P-022's §7
     requires. If P-017 has no equivalent clause, **say that it does not** rather
     than inventing one.
5. **The sanctioned reader** — `p017_checkpoint`, verified by round-trip against
   the file the pod actually writes, returning `None` when it cannot read. Run
   `scripts/check_gate_instrumentation.py` against P-017 and report the result.
6. **What happens at T if the verdict is NO DECISION** — pre-register the
   continuation, or it gets renegotiated in the moment.

## The question the rule must not dodge

**Is one settled tournament at −9.89¢ against a +6.8¢ backtest inside the
expected distribution, or not?** Compute it: given the backtest's
tournament-level variance, what is P(a tournament ≤ −9.89¢)? Report the number.

That is not a verdict and must not be written as one — n=1 decides nothing. But
it is the difference between "an unremarkable draw" and "the backtest may not
describe live", and the reader deserves the number rather than a reassurance.
The P-022 added-block decomposition is the model here: **two fills explained a
−12.33¢ block, and saying so was worth more than either alarm or comfort.**

## Stop rule

**Write the rule. Change no threshold that already exists, and reach no verdict.**
Do not re-derive T downward under any circumstances.

## Deliverable

`research/P017_DECISION_RULE.md` — with a provenance line per threshold, the
power re-solve, the P(≤ −9.89¢) calculation, and an explicit
what-is-known-at-writing-time statement at the top.
