# Status Reassessment — After the 2026-07-26 Run

**Betting Pod Shop — Kalshi Fund Project**
**Prepared:** Sunday, July 26, 2026 (late) · supersedes `RESEARCH_TESTING_BRIEF_2026-07-26.md`
**Basis:** 22 commits (`f90cfb2..1b2d5e7`), `research/RUN_SUMMARY_2026-07-26.md`, nine new REPORTs, and the current `manager/registry.yaml`.

> Still paper/demo throughout. No real money has ever been deployed. No pod is `tier: production`.

---

## 1. What actually happened

The queue predicted "three clean KILLs, one integrity fix, and one ADVANCE is a good run." The result was **six KILLs, three integrity fixes, and zero ADVANCEs** — then a second, unplanned wave of work that was more consequential than the queue itself.

**The research half is quickly told: everything died.** P-020, P-023b, P-023c, P-026, P-027 and all three satellite studies ran to verdicts and none survived. The **Tier-1 never-tested queue is now empty.**

**The integrity half is where the value was.** Three findings, each of which was quietly invalidating something we believed:

| Finding | What we thought | What was true |
|---|---|---|
| **P-001 CLV** | Capture ~29%, gate probably unreachable | Capture is **96.9%** — and the gate **had already passed** at 650 rows reading +1.39¢/ct, almost exactly its +1.4pp target. But **86% of those bets were priced off a different day's game.** Correctly-matched rows: +7.65¢/ct. Mismatched: +0.19¢/ct |
| **P-017 settler** | Might not be wired | Wired, then **silently dead since Jul 25 15:05 UTC**. True gate progress **0 of 8**, not 1. Blast radius **177 orphaned positions, $1,458.10** across four pods, invisible to `TradeStore`, every settler, and `AggregateRiskGuard` |
| **`scalar` = void** | A bug affecting P-022's future numbers | **532 scalar markets booked at $0.00; 0 of 532 actually settle at zero.** Booking scalar as void deletes *exactly the loss tail* — a fade sold at 5¢ and hit by a two-way tie owes 50¢ and was recorded as costing nothing |

That last one deserves restating plainly, because it is the single most important thing the day produced: **the P-022 gate, as it stood, would have passed a money-losing strategy.** Forward P&L would have been premium collected with the entire loss tail removed — strictly positive whether or not the edge was real.

### Then the second wave

Acting on the findings surfaced more:

- **P-017 recovery worked, then immediately broke.** Nine seconds after the restart, the *generic* Kalshi settler voided all 16 recovered positions at $0.00 — its pod filter existed in `rebuild_ledger_from_log()` but not in `_open_placed_entries()`, which is what `settle_cycle()` actually walks. It runs first in the engine cycle, so it won the race. Fixed, deployed, corrected; 16 now OPEN and settleable. The two bugs had been *masking each other* — orphaning the rows out of the log meant the unscoped scan had nothing to damage.
- **Both gates were being read off hand-typed numbers.** P-017's `progress: 1` was typed on entry day and never moved; P-001's counted raw rows. Both now **derive** progress from a sanctioned reader, and a gate whose reader fails returns `None` rather than falling back to stale YAML.
- **P-022 was never unbuilt.** The pod was committed 2026-07-23 and `betting-round-leader-fade` had been running live on the droplet for three days — before the decision rule that says those files "do not exist" was locked. It wrote **zero quotes and zero fills** the entire time: `_close_epoch()` preferred `occurrence_datetime`, which on this family is a far-future placeholder 13–18 days past `close_time` (10 of 10 measured), so the 12–24h window opened after each round had already settled. Reconciled against the locked rule, redeployed, and **T started 2026-07-26 22:36:52 UTC.**

---

## 2. Where we actually stand

| Pod | Status | Gate | True progress | Lands |
|---|---|---|---|---|
| **P-001** Moneyline Value | paper | 200 admissible CLV rows (scenario D, post-matcher-fix only) | **0 of 200** | late Aug – early Sept |
| **P-014** Live Game Agent | paper | 500 settled trades | accumulating | unknown |
| **P-015** Tennis Qualifier | paper | 120 trades (locked) | **0 of 120** | first volume Aug 17–21 |
| **P-017** Golf Top-N | paper | 8 **settled** tournaments | **0 of 8** | ~Q4 2026 |
| **P-022** Round-Leader Fade | **paper, LIVE** | **14 settled tournaments** (90% power vs +3.4¢) | **0 of 14** | ~3–4 weeks → mid/late Aug |

**Every gate now reads zero, and every one of those zeros is more honest than the number it replaced.** P-017 went 1→0, P-001 went 650→0, P-022 started at 0 having appeared to be running for three days. Nothing regressed; the instruments were recalibrated.

### The strategic fact

**P-022 is the entire forward pipeline.** It is the only validated edge, it is now live, and it has **never emitted a single quote.** Behind it: nothing. The research funnel that produced 27 hypotheses in five weeks has no untested candidate left in it.

---

## 3. The edge law, revised again

The old scoreboard said settlement mechanics were 3-for-3. After this run:

- **Settlement / structural mechanics: 3 for 6.** P-015, P-017, P-022 stand. P-026, P-027 and the three satellites all died — and they died *the same way*: **the mechanic was real but smaller than the tick and the spread.**
- **"We have better information": 0 for 7.** P-020 joins P-016, P-019, P-021, P-024, P-025, EV-Map Build 1.
- **Maker / fade: 0 for 4** once adverse selection is measured honestly (P-016, make-cut, LIV top-N, P-023c).

> **The new law: a verified mechanic is necessary but not sufficient. Before any study, compute the tick, the spread, the fee, and the required edge — and kill anything whose mechanic cannot clear that friction.**

P-026's co-leader bid-sum maxed at **99.0¢ against a hard 100¢ ceiling**. P-023c's +3.2¢ gross decomposed to **+0.2¢ executable**. The satellites' best award-tie trade was **+0.93¢ on 99¢ of collateral**. Every one of those was knowable *before* the study was written. That screen is the highest-leverage thing we can build next, and Task 6 below builds it.

### Two methodology rules earned the hard way

- **Check anchor contemporaneity in every maker replay.** Make-cut's "48h anchor" was a median **68h-old** price (stale in 100% of markets); posting it yielded +9.5¢/ct with a CI excluding zero — a pure artifact. P-023c then found the bias runs the *opposite* way on its cohort, so this is not a "stale looks optimistic" correction. Stale is simply wrong.
- **`settlement_ts − close_time` is a free first-pass screen** for any settlement-quirk hypothesis: one field, no external data, answers "did this market settle on the release it closed for?"

### And one process rule, from three separate incidents in one day

> **A filter asserted in a docstring and applied in one call path is not a filter.** The generic settler's pod scoping, `_close_epoch`'s field preference, and `from_config`'s bankroll key were all *documented correctly* and *wrong in the path that mattered*. Before trusting a component, enumerate every consumer.

---

## 4. What to do next — six tasks

Full prompts are in `research/prompts/`; the queue is `RUN_QUEUE_2026-07-27.md`.

### Task 1 · P-022 first-quote watch — **CRITICAL**
`PROMPT_P022_First_Quote_Watch.md`

The pod sat dead for three days and **nothing noticed**. It is now fixed but **verified only by tests and settled payloads — never by a live quote**, because no round-leader markets were open at the restart. If it fails to quote when golf relists mid-week, T never accumulates and the only edge in the fund produces nothing while appearing healthy. This task instruments the pod so that *silence during a quotable window* is loud, rather than indistinguishable from the legitimate silence between tournaments.

### Task 2 · Widen the P-022 backtest — the cheapest open lead in the folder
`PROMPT_P022_Widen_Backtest.md`

Kalshi trade history reaches back to **at least 2026-05-20**, not the ~1 month we assumed. P-022's 19 tournaments can be widened backwards **for minutes of API budget instead of months of calendar** — tightening the CI on the only edge we have. Pre-declare the extension before running it: this is a backtest widening, not a gate change, and the distinction must be written down *before* the numbers are seen.

### Task 3 · Clear the corrections backlog + droplet hygiene
`PROMPT_OPS_Corrections_And_Hygiene.md`

Six owed items, each small, several load-bearing: the JDAY scalar re-book (+$0.43); two documentation errors in `REPORT_Golf_Quirks_2026-07.md` §4.2 (round-based cells are anchored at H=12h not 48h; the "48h before R1" anchor is **post-R1 for 58–71%** of the control cohort); the **KXLEADER split clause is conditional** and every prior write-up states it unconditionally; `GOLDENGLOBESNOM.pdf` serves RANKLIST, not awards — two opposite dead-heat regimes one PDF apart; H2 re-scoped to "fee-bounded *in the liquid sports head*"; and **20 MB of stale detached-HEAD source now sits on the droplet** where a future debugging session can grep it as live code (rsync runs without `--delete`, so the exclusions stop the bleeding but don't clean it).

### Task 4 · Fix the fee table properly
`PROMPT_OPS_Fee_Table_Fixture.md`

`_SERIES_MAKER_FEE` has drifted **four times, three slices in this run alone**, each time silently billing a maker fee on maker-free series — which flows straight into every backtest verdict. It wants a fixture generated from `/series` plus a CI check that fails when Kalshi's schedule moves, not a fifth hand patch.

### Task 5 · Data-readiness audit on everything "blocked on time"
`PROMPT_OPS_Data_Readiness_Audit.md`

Three workstreams are waiting on calendar, and **nobody has verified any of them is actually accumulating.** P-018's in-play tick sample was due ~Aug 4; MLB props needs 27 game-days by ~Aug 17; and EV-Map Build 2's weather jobs run on **Mac cron and silently skip whenever the machine sleeps** — against a 90-day API horizon where every missed week is calibration data lost forever. Given that this project has now found a dead settler, a dead pod, and a dead cron in a single day, "it's collecting" is a claim to test, not assume.

### Task 6 · Build the friction screener, then hunt with it
`PROMPT_R5_Friction_Screener_And_Hunt.md`

The pipeline is bare and the last six kills shared one cause. **Build the screen first, validate it against the graveyard** (it must retrodict the six kills *and* pass P-015/P-017/P-022 — a screen that kills the survivors is wrong), **then** apply it to new candidates. Screen before study, not after. Note the honest prior: after 27 hypotheses, the base rate of a new idea surviving is low, and the screen's main value may be killing candidates in an hour rather than a night.

---

## 5. Decisions that are yours

1. **Approve the P-022 backtest widening** before Task 2 runs — specifically, that extending the sample backwards does not constitute re-fitting a locked rule. My read: it doesn't, because the rule governs the *forward* test and the parameters are frozen. But it should be written down before the numbers exist, not after.
2. **Deploy remnant C** — the `manager/` daily-brief work (`e4768c8`, `f90cfb2`) plus `mlb_f5_research/` are committed and still not on the droplet.
3. **Whether to keep P-002/P-006 shelved.** The orphan incident surfaced ~$1,311 of P-002/P-006 exposure with **no settler that will ever drain it**. That's not a trading decision, it's a bookkeeping one — but those positions should be explicitly voided or the pods explicitly retired, rather than sitting as permanent phantom exposure.
4. **How hard to push Task 6.** The funnel is empty. Options are: hunt broadly again, go deeper on the one thing that works (golf settlement mechanics, where 3 of 3 verified quirks have paid), or accept a thinner pipeline and let the four live gates resolve before spending more research effort. Task 6 assumes "hunt, but screened" — say if you'd rather concentrate.

---

### One-line summary

Nothing survived the research queue, but the day found three defects that were each silently invalidating a live gate — including one that would have passed a money-losing strategy — and **P-022 is now genuinely live, genuinely instrumented, and has still never quoted.** Verifying that first quote is worth more than any new hypothesis this week.
