# PROMPT — the one-sided-book question (and POI26's close reference)

**PRE-WINDOW. The finding must be in Sam's hands before 2026-07-29T15:30Z.**
**Investigate and report. Do NOT change the pod's behaviour in this task.**

## The finding to explain

The pre-flight found that **every in-band reference tomorrow is a one-sided ask**
— `yes_bid: null`, `yes_ask: 0.05`, `ask_qty: 10`. `_mid()`'s one-sided branch
drives **every** placement in the first tournament. The pod is not quoting off a
two-sided mid at all.

The fund's own standing rule, applied to every study since R3, is **"two-sided
quotes only."** The live pod is about to violate it on the first tournament of a
24-tournament gate.

## The question that decides everything

> **Was P-022's validated edge itself measured on one-sided books, or is this new?**

If the +3.41¢ backtest was built on one-sided references, then one-sided is the
*norm* for this family and the research rule was never binding here — the pod is
consistent with its own evidence, and the rule needs a scoped exception written
down. If the backtest used two-sided books and live is one-sided, then
**tomorrow's quotes are out-of-sample** and that is a materially different claim.

**Answer it from the settled data, not from reasoning.** The published
364-ticker universe and the widened cache both exist, and `--validate` is
already pinned to the published set.

## Method

### A. Classify the backtest's own references

For every quote the validated backtest posted, reconstruct the book state at the
anchor and classify: **two-sided · one-sided ask · one-sided bid · no book.**
Report the share of *posted markets*, of *filled markets*, and of *contracts*.

**Do not reconstruct the anchor by hand.** Use the harness's own anchor logic —
the P-001 lesson is that a reimplemented reader produces a second, subtly
different number for a quantity meant to be unambiguous.

### B. Split the measured edge by book type

Recompute the headline net ¢/ct **within each class**, tournament-clustered:

| book at anchor | posted | filled | net ¢/ct | 95% CI |
|---|---|---|---|---|
| two-sided | | | | |
| one-sided ask | | | | |

**If the edge lives in one class and not the other, that is the finding of the
week** — it would mean the pod's live population is not the population the edge
was measured on. Report it plainly either way, including the case where the
classes agree, which is the reassuring answer.

### C. What `_mid()` actually does on a one-sided book

Read the branch and state, concretely, what reference price it produces when
`yes_bid` is null — and therefore what the `+0.02` offset is being added to.
Tomorrow's quotes are **sell YES at 7¢ against a 5¢ reference**; show the
arithmetic that produced 7¢ and confirm it is the intended semantics rather than
an accident of a null-handling default. A `None`-to-zero coercion anywhere in
that path is a defect, not a convention.

Also record, without fixing: **the pod has no size or depth screen.** The
pre-flight's funnel included one; the code does not. Given the survivor
profile's *deep at the traded leg* clause — the exact clause P-017A died on —
quantify what an `ask_qty: 10` book means for a 5-contract quote, and state
whether the 131 excluded "above band" names were junk books (`bid 0.01 /
ask 0.97`) that a depth screen would also have caught.

### D. POI26's close reference — verify or pre-register an exclusion

`LAG_DAY_H["KXCHAMPTOUR"] = 12.0` is calibrated on **n = 1**, a **US** event.
POI26 is the first PGA Tour Champions event ever staged in Europe — a different
clock frame. Its window opens **2026-07-30T16:00Z** and the pre-flight could
find no tee sheet anywhere.

1. Search once more for a published R1 tee time or round schedule.
2. **If it cannot be verified: pre-register, before the window opens, that POI26
   is excluded from T** unless `close_source` reads something better than
   `tour_day_offset` at quote time. A tournament quoted off an uncalibrated
   constant cannot be evidence either way, and deciding that afterwards is
   fitting the sample to the result.
3. Either way, add `close_source` to what the detector reports at quote time so
   the question is answerable from `status.jsonl` rather than reconstructed.

## Stop rule

**STOP at the report.** No change to `_mid()`, no depth screen, no band change.
The one thing you may ship is **observability**: logging `close_source` and the
book-sidedness of each reference at quote time, since a quote whose provenance
was never recorded cannot be adjudicated later.

If the answer is "the edge was measured on two-sided books and live is
one-sided", **say so at the top of the report and page Sam** — that is a
stop-the-line finding and the decision to quote or not is his, before 15:30Z.

## Deliverable

`research/REPORT_P022_OneSided_2026-07-28.md`: the classification table, the
edge split by book type with clustered CIs, the `_mid()` arithmetic, the depth
observation, and the POI26 verdict with its pre-registration if it cannot be
verified.
