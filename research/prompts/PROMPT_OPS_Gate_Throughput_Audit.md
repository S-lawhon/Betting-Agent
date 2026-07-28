# Claude Code Task — OPS: Gate Throughput Audit (the fund has never passed a gate — find out why)

> This is a measurement task about the fund's own machinery, not about markets. It may be the highest-leverage thing on the list.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

**The fact that motivates this.** In this project's entire history, **no forward gate has ever PASSED.** Every pod that reached a verdict got there by being KILLED — P-013, P-016 v1 and v2, P-019, P-021, P-024, P-025, P-015b, EV-Map Build 1, plus six R5 kills. As of 2026-07-27 **all five live gates simultaneously read zero**:

| Pod | Gate | Progress | Nominal resolution |
|---|---|---|---|
| P-001 | 200 admissible CLV rows (scenario D) | 0 | late Aug – early Sept 2026 |
| P-014 | 500 settled trades | ? | unknown |
| P-015 | 120 trades (locked) | 0 | Jan 2027 (240 → Jul 2027) |
| P-017 | 8 **settled** tournaments | 0 | ~Q4 2026 |
| P-022 | 14 settled tournaments | 0 — **and has never quoted** | mid/late Aug 2026 |

32 hypotheses tested, 3 survivors, **0 resolved forward tests**. The research machine produces kills efficiently; the validation machine has never produced a pass.

**The hypothesis to test: the binding constraint is observation throughput, not hypothesis supply.** If true, generating a 33rd candidate is worthless and the work belongs on making existing gates resolvable.

## Task — measure it, don't assert it

### 1. Compute observations-per-week for every live gate, from data
For each of P-001, P-014, P-015, P-017, P-022: the **realised** rate of gate-countable observations per week, measured from the trade logs and the sanctioned checkpoint readers — **not** from the pace assumptions written in `registry.yaml` (P-015's "~20 trades/month" is an estimate that has never been checked against a single realised trade, since it stands at 0).

Distinguish carefully:
- observations the pod **could** produce if healthy, from event cadence
- observations it is **actually** producing
- the gap, and its cause

### 2. Project a real resolution date per gate, with an honest confidence interval
Given the measured rate, when does each gate actually resolve? Flag any gate that **cannot resolve within 12 months** at its realised rate. Be explicit that a gate that cannot resolve is not conservative — **it is inert**, and it consumes attention while producing nothing.

### 3. Find the structural throughput limiters
For each gate, ask what is actually capping the rate, and separate the causes:
- **Event cadence** (P-015 depends on tennis qualifying weeks — genuinely irreducible)
- **Pod health** (P-022 produced zero for four days; P-017's settler was dead for a day and a half)
- **Counting rules** (P-017 counts a tournament only when it has ≥1 resolved and ZERO open positions — correct, but how much does it cost in practice?)
- **Capture rate** (P-001's CLV job)
- **Statistical power** — is the threshold larger than it needs to be? **Do not propose changing any threshold.** Report the power arithmetic and let Sam decide. Locked rules stay locked.

### 4. Build the standing instrument
Add **observation throughput** to `manager/` so this is visible daily rather than rediscovered:
- per-gate: observations last 7d / last 28d, realised rate, projected resolution date
- an alert when a gate's realised rate falls to **zero** for longer than its event cadence would explain — that is the generalisation of the P-022 silence problem, and it would have caught all four dead days
- follow the existing house pattern: derived from a sanctioned reader, never hand-typed, and **a reader that FAILS returns None rather than falling back to a stale number**

### 5. Recommend a portfolio posture — evidence first, opinion clearly labelled
Given the measured rates, which gates are worth active attention and which should be explicitly **parked** (still accumulating, but not consuming review)? A pod that cannot resolve inside a year is not the same as a pod under test, and the registry should say which is which. `blocked_on: time` already exists for this — check whether it is being applied honestly.

## Guardrails
- **Change no gate, threshold, or locked rule.** This task produces evidence for a decision that is Sam's. The P-013 lesson — criteria decided after the fact — is exactly what the locked rules exist to prevent, and an audit is not a licence to renegotiate.
- Read-only against live `data/`. No deploy.
- Every number must come from a file on disk; say which. Do not estimate what you can compute.

## Definition of done
`research/REPORT_Gate_Throughput_2026-07.md` with: the per-gate table (gate · threshold · measured rate · projected date · limiting cause · resolvable-within-12-months yes/no); the throughput instrument committed and wired into the brief; and a clearly-labelled recommendation on which gates to park. State plainly whether the throughput hypothesis is **supported or refuted** by the measurements — if the gates are in fact fine and the problem is elsewhere, that is the more valuable finding and should be said just as directly.
