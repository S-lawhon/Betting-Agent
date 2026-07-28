# PROMPT — P-022 rule decisions, written before anything settles

**Run after Task 1. The first round close is 2026-07-30T15:30Z.**

## Why tonight is the last honest night

Four P-022 rule questions are open. Every one of them determines whether a specific tournament counts toward T = 14. **The moment the first tournament settles, any answer becomes a rule fitted to a result** — and this fund's entire credibility rests on the fact that it has never done that. There is no version of "decide later" that is neutral.

Your job is **not** to decide. It is to write each question as a pre-registered rule with its rationale, its alternatives, and the exact consequence of each choice, so Sam decides from evidence rather than from an outcome. Produce `research/P022_RULE_DECISIONS_2026-07-29.md`, committed, with a signature line for Sam's answer per item.

## The four questions

### 1. `t_start_utc` — currently records a period in which the pod could not trade

T started 2026-07-26 22:36:52 UTC. From then until 2026-07-27 17:53 UTC the pod was **structurally incapable of quoting** (no real close reference). Counting that period is counting calendar in which the strategy did not exist.

Write both options with consequences:
- **Reset to the first demonstrated quote.** Cleanest; T measures the strategy. Costs nothing but honesty about the restart.
- **Leave it.** Preserves a continuous record but includes dead time, and any later reader will have to be told why.

State clearly that a reset **does not** reset the pod ID or any parameter, so §8.1 is not engaged — verify that against the locked document and quote the clause.

### 2. Weather-suspended tournaments — in T or excluded?

The resolver's error runs to **+52h** on weather-suspended events. Quotes are still placed pre-round, so the fill is not obviously contaminated. But a 52h error means the quote sat through conditions the backtest never sampled.

- Write the case for **keep** (the placement decision was made inside a valid window; excluding is post-hoc).
- Write the case for **exclude** (the holding period is unlike anything in the 19-tournament backtest).
- **Pre-register a detection rule either way** — how does the checkpoint *identify* a weather-suspended tournament from data it already has, without a human labelling it after the fact? A rule that needs a human to notice is not a rule.

### 3. Posting above H = 24h

The conservative calibration puts the first quote at a true H of ~25.6h (tee-time path) or ~29.6h (day-offset path). The pod is therefore posting **outside** the validated window on the early side.

- Quantify from the 72-event validation set: what fraction of quotes land above 24h true, and by how much.
- The backtest's edge was measured at anchors inside `[12h, 24h]`. State whether the >24h region was sampled at all. **If it was not, say so plainly** — that makes the current live quotes out-of-sample, which is a materially different claim from "conservatively early".
- Options: accept and record as a known deviation · exclude >24h fills from T · narrow the pod's band to compensate (**flag this third one as a §8.1 change that resets T to 0** — it is the forbidden fix, listed only for completeness).

### 4. The power question — T = 14 was sized against +3.4¢/ct

The widening moved the pooled estimate to **+2.57¢/ct [+0.1, +4.5]** (from +3.41¢ [+1.7, +5.1]). At +2.57¢, T = 14 is underpowered — roughly **24 tournaments** for the same power.

- Re-derive the power calculation explicitly, showing the assumed variance and the clustering unit, so the "~24" is checkable rather than asserted.
- **Do not change the gate.** A wider backtest changes the prior, not the gate. Present three framings for Sam: hold T = 14 and accept lower power · raise T with the reason recorded before any result · hold T = 14 as a *screening* gate with a pre-registered confirmatory T.
- State the calendar cost of each at the realised golf cadence (~1.3 round-leader events/week per the P-028 census, **not** the 4–5/week figure earlier documents cite — check which is right and use the checked one).

## Stop rule

**Write the document. Change nothing.** No parameter, no gate, no `t_start_utc` value. Every item ends with an unanswered decision line for Sam.

## Deliverable

`research/P022_RULE_DECISIONS_2026-07-29.md`, committed, and four explicit items in the run summary's "Decisions needed from Sam".
