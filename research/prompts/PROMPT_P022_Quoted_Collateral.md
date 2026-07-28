# PROMPT — §7: reserve QUOTED collateral, not just filled

**PRE-WINDOW. Must be deployed and verified before 2026-07-29T15:30Z.**
**Sam has authorised the fix** (2026-07-28). Run it first.

## The defect

P-022 would place **13 quotes carrying $60.45 of worst-case collateral against
§7's $50 per-tournament cap (5%)**, because sizing subtracts **filled**
collateral only. A resting quote is an unconditional obligation — it consumes
the cap the moment it rests, not when it fills.

**The consequence is a gate condition, not a P&L number.** If ≥11 of the 13
fill, §7 is breached and **AIGWO26 is excluded from T** — the first tournament
of a 24-tournament gate, spent on an accounting inconsistency.

This is the **seventh** instance of the house pattern: *a filter asserted in a
docstring and applied in one call path is not a filter.* §7 asserts a cap; the
code enforces it against the wrong quantity.

## The fix

Make the reservation count **resting quotes plus fills**, so the cap binds at
the moment of quoting:

1. **Find every consumer first.** Before changing anything, enumerate every call
   path that reads or writes the reservation — `_reserve()`, the sizing call,
   `_sweep_reservations()`, release-on-settlement, and anything the standalone
   loop touches. Write the list into the report. **This is the enumeration step
   the pattern keeps punishing.**
2. Reserve at quote placement, release on cancel, on settlement, and on
   restart-recovery. **A quote that is cancelled must give its collateral back**
   — otherwise the guard ratchets to permanent mute, which is the failure mode
   the 2026-07-27 wiring already had to avoid once.
3. Worst-case collateral for a `sell_yes` maker quote is `size × (1 − price)`.
   Derive it in code from the actual quote, do not hard-code a per-quote figure.
4. **Books must survive restart.** Verify that reservations rebuild from the
   persisted book rather than re-arming from zero — the 07-27 report recorded
   this as closed; re-verify it rather than trusting it.

## What must NOT change

- **Band `(0.03, 0.12)`, offset `+0.02`, window `[12h, 24h]`, the three caps,
  the 13 series.** Verify byte-identical before and after — `git diff` empty
  against the pod's parameter block and both configs.
- **§8.1 analysis is a required section of the report.** §8.1 resets T for
  changes to *offset, band, window*. This is §7 enforcement. **State the
  reasoning and quote the clause; do not assume the answer.** If there is any
  reading under which this resets T, say so and stop for Sam.

## Verify it against tomorrow, not in the abstract

Re-run the pre-flight at an injected `2026-07-29T15:31:00Z` and report the
funnel again, before and after:

| | before | after |
|---|---|---|
| in-band candidates | 13 | ? |
| quotes placed | 13 | ? |
| worst-case collateral | $60.45 | **must be ≤ $50.00** |
| per-quote size | 5 | ? |

**Report which quotes get dropped or shrunk and by what rule** — first-come,
best-edge, or pro-rata. That rule is itself a spec decision: write it down, and
prefer the one that is deterministic and reproducible over the one that
maximises expected edge. A tie-break that depends on dict ordering is not a
rule.

Also report the same funnel for **ROC26** (window opens 18:30Z) and **POI26**
(07-30T16:00Z) — the cap is per tournament, and the fix must not silently
starve the later ones.

## Deploy — and prove the running process took it

```bash
bash scripts/deploy.sh 129.212.176.202 restart
systemctl restart betting-round-leader-fade      # NOT covered by deploy.sh
```

**A file on disk is not a deployed fix.** Verify from the running process:
`schedule`/`risk_guard` live, band/offset/window/caps/series byte-identical,
markets discovered, detector state, and the reservation total reading from the
live book. Paste the evidence into the report.

## Stop rule

Cap enforcement only. **Do not add a size or depth screen** — the pre-flight
found the pod has none, and that is Task 2's finding to report, not this task's
to fix. One change, one verification.

## Deliverable

`research/REPORT_P022_Quoted_Collateral_2026-07-28.md`: the consumer
enumeration, the drop/shrink rule, the before/after funnel for all three
tournaments, the §8.1 analysis, and the running-process verification.
