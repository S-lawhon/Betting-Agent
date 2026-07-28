# PROMPT — Gate instrumentation standard

**The generalisation of last night's finding. Cheapest permanent win on the list.**

## The finding this exists to prevent

Last night measured that the fund's constraint is neither hypothesis supply nor observation supply:

> **The scarce resource is correctly instrumented observations.**

Four of five gates were not measuring anything, each for a different reason:

| pod | 28d settled | why the gate read nothing |
|---|---:|---|
| P-001 | 432 | CLV rows lag settlement; admissibility unmeasured |
| P-014 | 54 | **no sanctioned reader existed at all** |
| P-015 | 5 | reader pointed at `data/pods/P-015.jsonl`, **a file that does not exist** |
| P-017 | 38 | working — the control |
| P-022 | 0 | could not compute its own window |

Three were fixed by hand yesterday. **Nothing prevents the fourth recurrence**, and the fund has now produced the same class of defect five times ("a filter asserted in a docstring and applied in one call path is not a filter").

## Build

### A. The checklist — `docs/GATE_INSTRUMENTATION_STANDARD.md`

Conditions a pod must satisfy before it may accumulate toward a gate. Derive them from the actual failures above, not from first principles. At minimum:

1. A **sanctioned reader** exists, is named in the locked document, and is the *only* path to the gate number.
2. The reader points at **the file the pod actually writes** — asserted by a test that writes a row through the pod's real path and reads it back through the reader.
3. The reader **returns `None` when it cannot read.** Never a stale YAML value, never a fabricated default. (Precedents: the `or 0.9` breakeven, the stale-registry read.)
4. A **pre-registered decision rule** exists, written blind, before n is decisive.
5. The reader emits **progress and threshold**, so the gate is readable and not merely computable.
6. The gate key matches the field the pod actually sets — the JDAY re-book set `outcome` while `p017_checkpoint` keys off **`action`**, and the gate silently did not move.
7. **Every consumer of the filter is enumerated**, not just the documented one. This is the five-time failure.
8. A **realised-rate projection** exists, or the gate is explicitly labelled unprojectable — never given a made-up date.

### B. The enforcer — `scripts/check_gate_instrumentation.py`

Runs the checklist against every registered pod and fails loudly. Wire it into CI alongside `check_fee_fixture.py`. **A network or read failure is a failure, never a skip.**

### C. Validate it backwards — this is the part that matters

A checklist that passes everything is decoration. Prove it:

- Run it against the **07-26 state** of P-014, P-015 and P-022. **It must fail all three**, each for the right reason. If it passes any of them, the checklist is wrong — fix the checklist.
- Run it against **P-017**, the known-good control. It must pass.
- Run it against **today's** P-014 and P-015. They should now pass, which also independently confirms yesterday's fixes.

Report the confusion matrix. **A screen that cannot re-detect the bugs that motivated it is not a screen** — the same standard the R5 friction screener was held to.

## Also fix, since it is the same class

`blocked_on: time` is currently honest only for P-017 and P-022. P-014 and P-015 are genuinely waiting on volume; **P-001 is blocked on a measurement, not a calendar** (Task 6 will settle which). Add a `blocked_on` vocabulary to the standard — `time` / `defect` / `measurement` / `backtest` / `decision-rule` — and make the enforcer reject a pod whose label it can contradict from data.

## Stop rule

Do not change any gate threshold or decision rule. This task builds a check, not a verdict.

## Deliverable

`docs/GATE_INSTRUMENTATION_STANDARD.md`, `scripts/check_gate_instrumentation.py`, tests, CI wiring, and the backwards-validation confusion matrix in `research/REPORT_Gate_Standard_2026-07-29.md`.
