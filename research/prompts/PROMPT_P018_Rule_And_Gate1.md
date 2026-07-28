# PROMPT — P-018: decision rule written blind, and gate #1 redesigned

**The week's largest research risk. Its data gate opens ~2026-07-30.**

## Why this is urgent

P-018's committed backtest reports **+9.09¢/ct over 677 game-days and 5,676
faded fills across 240 markets, game-clustered CI [+5.33, +13.10]** — the
largest headline this fund has ever produced.

**And its own cheapest pre-registered kill cannot run.** Spec §5/§8 says gate #1
is *"does edge rise with surprise?"*, answered by comparing high-surprise
buckets against low ones. Both low buckets are **empty by construction**:

```
[0.00, 0.02)     0 fills
[0.02, 0.06)     0 fills
[0.06, 0.12)  3514 fills   +8.46¢  [+3.86, +13.22]
[0.12, 1.01)  2162 fills  +10.13¢  [+5.84, +14.66]
```

`p018_params.quoting.surprise_hi = 0.06` **is the threshold at which the
strategy quotes at all**, so no fade can ever be booked below it. The
discriminating test was specified against a population the strategy cannot
generate.

**A large headline whose falsifying test is structurally disabled is the exact
shape of every finding this fund has later had to retract.** Treat +9.09¢ as
unadjudicated until gate #1 can actually run.

## Part 1 — redesign gate #1 so it can discriminate

The mechanism claim is *the edge comes from fading surprise, and rises with it*.
The current design cannot test that because the sampled range is truncated.
Options to evaluate — pick one, justify it, and **pre-register it before
computing any effect**:

- **Widen the quoting threshold in the backtest only** (not the pod) to book
  fades below 0.06 and populate the low buckets. State whether the historical
  data supports it — if the strategy would not have quoted there, are there
  prices at all?
- **Test the monotonic dose-response across the range that DOES exist.** Split
  [0.06, 1.01) into finer buckets and test for a trend, not a two-group
  contrast. Weaker, but runnable today.
- **Find a placebo.** If the edge is really about surprise, a matched set of
  non-surprise fades in the same markets and hours should show no edge. A
  placebo that also shows +9¢ kills the mechanism claim outright.

**The bar: a redesigned gate #1 must be capable of returning KILL.** Write down,
before running it, what result would kill the pod. If no such result exists, the
gate is decoration and you must say so.

## Part 2 — the decision rule, written BLIND

### Blindness protocol

- **Do not run `--unblind`.** You may read the *already-committed* backtest
  aggregates above — they are in the repo and cannot be unseen — but do **not**
  compute new per-trade outcomes while drafting thresholds.
- **Derive every threshold from the locked documents or from an
  outcome-independent measurement**, exactly as P-014's rule did, and say which
  for each one. Inherit `z ≥ 2.0` PASS and `z ≤ −2.0` hard kill verbatim.
- Disclose any contamination in full, as the 07-29 run did. A disclosed,
  checkable contamination is recoverable; a hidden one is not.

### What the rule must specify

1. **The statistic** — game-clustered, fee-net at the actual traded price.
2. **The clustering unit** and why. 5,676 fills across 240 markets across 677
   game-days is **not** 5,676 observations; state the effective n, as P-014's
   rule did when it found 500 rows ≈ 123 game clusters.
3. **PASS / KILL / NO DECISION thresholds**, numerically, plus a hard kill.
4. **Admissibility** — `result="scalar"` is a partial payout, never a void; rows
   with a missing price are **excluded, never defaulted**.
5. **The sanctioned reader**, returning `None` when it cannot read, pointed at
   the file the pod actually writes — verified by a round-trip test, per
   `docs/GATE_INSTRUMENTATION_STANDARD.md`.
6. **The coverage caveat must appear in the rule, not a footnote:** the replay
   sample dropped **33.9%** of discovered markets, lowest-volume-first, so it is
   biased toward liquid markets. The limiter is the exchange (128 cumulative
   429s), not the config, so this cannot be fixed by collecting harder.
7. **Power** — what effect does the gate detect, and is it adequate against
   something far below +9.09¢?

## Part 3 — run the standard against it

Run `scripts/check_gate_instrumentation.py` against P-018 and report the result.
The pod is currently `blocked_on: backtest` and inert on four counts; confirm all
four still hold and that nothing in this task woke it up.

## Stop rule

**Do not enable P-018. Do not deploy it. Do not act on +9.09¢.** This task ends
with a rule, a redesigned gate #1, and a verdict on whether the headline is
adjudicable at all.

## Deliverables

- `research/P018_DECISION_RULE.md` — blind, with a contamination statement
- `research/P018_GATE1_REDESIGN.md` — pre-registered before any effect number
- `research/REPORT_P018_Gate1_2026-07-28.md` — the redesigned gate's result, the
  placebo if run, the effective-n calculation, and a plain statement of whether
  +9.09¢ survives a test that could have killed it
