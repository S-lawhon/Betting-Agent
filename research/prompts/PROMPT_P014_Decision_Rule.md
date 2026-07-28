# PROMPT — P-014 decision rule, written BLIND

**This is the integrity task of the night. Read the blindness protocol before anything else.**

## Why now

P-014 reads **331 of 500** and projects **2026-10-23** — the first gate in this fund's history with a real projected resolution date. It has **no pre-registered decision rule.** A rule written at n = 331 is far more defensible than one written at n = 480, and one written after unblinding is worthless. Every day of delay costs credibility that cannot be bought back.

## Blindness protocol — non-negotiable

- **Do not run `--unblind`.** Do not read P-014's win rate, P&L, edge, or any per-trade outcome.
- **Do not open the trade log, the settled rows, or any dashboard view that would display them.** If a tool you need would incidentally print outcomes, use a different tool or read only the fields you need.
- You may read: **n**, the settlement rate, timestamps, market/venue/sport composition, price distribution at entry, and anything else that is a property of *placement* rather than *result*.
- **If you see an outcome by accident, stop and record it in the report.** A contaminated rule that says so is recoverable; one that hides it is not.

State at the top of the deliverable, explicitly, what you read and what you did not.

## What the rule must specify

Write `research/P014_DECISION_RULE.md` covering:

1. **The statistic.** What single number decides. Follow the house pattern: clustered by event, never per-contract; net of fees at the actual traded price, never an average.
2. **The clustering unit** and why — P-014 is in-play NBA/MLB, so game-level clustering is the obvious candidate and correlated within-game fills are the obvious hazard. Justify it.
3. **PASS / KILL / NO DECISION thresholds**, numerically, with the reasoning for each. Include a **hard kill** that fires regardless of n, on the P-015 pattern (z below a stated floor).
4. **Admissibility.** Which rows count. Be specific about the traps that have already bitten this fund: `result="scalar"` is a **dead-heat split, not a void**; voids and their treatment; anchor contemporaneity; any row whose price is missing must be **excluded, never defaulted** (the `or 0.9` fallback that fabricated a breakeven is the precedent).
5. **The reader.** Which script is sanctioned, and the assertion that it **returns `None` rather than a stale fallback when it cannot read.** Point it at the file the pod actually writes — verify that path exists and has rows, since this exact bug hit both P-015 and P-014 already.
6. **Power.** At the chosen thresholds, what effect size does n = 500 actually detect? If 500 is underpowered for a plausible edge, **say so now**, while saying it costs nothing.
7. **What happens at n = 500 if the verdict is NO DECISION** — pre-register the continuation rule, or the gate will be renegotiated in the moment.

## Sanity check that does not break blindness

Confirm the reader runs end-to-end and emits progress/threshold — the P-015 lesson is that a gate must be **readable**, not merely computable. Assert the output shape without reading the verdict fields.

## Stop rule

**Write the rule. Do not evaluate it.** Do not unblind. Do not adjust any threshold after seeing anything.

## Deliverable

`research/P014_DECISION_RULE.md`, committed, plus a blindness attestation in the run summary naming exactly what was read.
