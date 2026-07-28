# P-022 — four open rule questions, written before anything settles

**Written 2026-07-28 03:1x UTC. The first round close is 2026-07-30T15:30Z.**

This document **decides nothing**. Each item states the question, the
alternatives, the evidence, and the exact consequence of each choice, and ends
with an unanswered line for Sam. Nothing in `P022_DECISION_RULE.md`,
`manager/registry.yaml`, `scripts/p022_checkpoint.py` or any parameter has been
changed by this work.

**Why tonight.** The first tournament settles on 2026-07-30. After that, any
answer to any of these four is a rule chosen in the presence of a result. This
fund has never done that, and the whole value of the P-022 gate rests on it
never doing that. *Deciding later is not neutral.*

Two of the four turned up evidence that changes what the questions are *about*,
so read §5 before answering §4.

---

## Item 1 — `t_start_utc`

### The facts

`P022_DECISION_RULE.md` §12 records **T starts 2026-07-26 22:36:52 UTC**, the
moment `betting-round-leader-fade` restarted onto the reconciled code. From
then until **2026-07-27 17:53 UTC** the pod was structurally incapable of
quoting: it had no real close reference, every Kalshi time field on an open
round-leader market being the same ~20-day placeholder. That is **19.3 hours**
of recorded elapsed gate time in which the strategy did not exist.

**A fact not previously recorded, found while writing this:** `t_start_utc` is
**not implemented anywhere.** It appears in §12 of the rule, in the
checkpoint's docstring, and in one printed line — and nowhere in
`scripts/p022_checkpoint.py` is any row filtered by it. `load_settled()` counts
every settled P-022 row it finds, of any date. So today the value is
*documentation about* the gate, not a term *of* the gate.

That costs nothing right now, because zero rows exist. It stops being free the
moment a decision turns on it. It is the same shape as §7's cap-breach
exclusion, which was an unimplementable gate condition until 2026-07-28.

### Option A — reset to the first demonstrated quote

T's clock starts at the first `QUOTE` row P-022 ever writes.

*Consequence:* T measures the strategy and nothing else. The dead period is
excluded by construction rather than by a later reader being told about it.
Costs one line of honesty about the restart. **Nothing else moves** — see
below.

### Option B — leave it at 2026-07-26 22:36:52 UTC

*Consequence:* a continuous record with no restatement, at the price of 19.3 h
of counted time in which no observation could have been made. Because
`t_start_utc` gates nothing in code, and the pod wrote no quotes in that
period, **Option B and Option A produce the identical T today.** The
difference is entirely about what a future reader is told, and about which
value becomes load-bearing if `t_start` is ever implemented as a filter.

### §8.1 is not engaged, verbatim

> **8.1** *"**No mid-flight parameter changes.** Offset, band, window, series
> set, caps. Any change resets T to 0 under a new pod ID."*

`t_start_utc` is not in that list. It is not a parameter of the strategy; it
is the left edge of the observation window. §4a's list of what must stay
byte-identical is likewise *"`offset`, the H = 12–24h posting window, the
[0.03, 0.12] anchor band, the series set, and every cap in §7"* — again not
`t_start`. **A reset under Option A does not create P-022b, does not reset the
pod ID, and does not touch a parameter.**

> **DECISION (Sam):** ______________________________________________
> *(A — reset to the first demonstrated quote · B — leave as recorded ·
> and: should `t_start_utc` become an actual filter in
> `scripts/p022_checkpoint.py`, or remain documentation?)*

---

## Item 2 — weather-suspended tournaments: in T or excluded?

### The case for KEEP

The placement decision was made inside a valid window, against a price the
strategy is defined on, before anyone knew the round would be interrupted.
Excluding an event *because of what happened after the quote rested* is
post-hoc selection — the thing §8.2 forbids. The pod cannot know at quote time
that a round will be suspended, so a live implementation could never act on
this rule prospectively; it would only ever be applied to results.

### The case for EXCLUDE

A 51.8 h resolver residual means the quote rested through 2+ days of held
inventory in conditions the backtest never sampled. The fade is a
settlement-mechanic bet whose risk is *a faded name leading*; a suspended
round changes the field's scoring distribution, the leaderboard's tie
structure, and the time the quote is exposed to informed flow. That is a
different holding period from anything in the 19-tournament sample.

### The pre-registered detection rule — and it works

A rule that needs a human to notice is not a rule, so here is one computable
from data the system already produces.

**Definition.** For each event-round P-022 quoted, at settlement:

```
lag_h = actual_close_utc − close_ref
        actual_close_utc = the settled market's close_time, which Kalshi
                           REWRITES to the true value at close
        close_ref        = the resolver's prediction, already carried on
                           every QUOTE row
A TOURNAMENT is WEATHER-AFFECTED if max(lag_h) over its rounds > 12.0 h.
```

**Validation on the 72-event settled set** (`schedule_resolver_validation.json`):

| criterion | flags |
|---|---|
| `lag_h > 12.0 h` | **8** of 72 event-rounds |
| physical check: round span (close − first tee) `> 24 h` | **8** of 72 |
| **agreement** | **8 of 8 — the same 8, exactly** |

The two criteria are independent — one uses only the resolver's own prediction,
the other only ESPN tee times — and they select the identical set. Both have a
clean empty gap around the threshold: spans run `…, 15.5, 19.9 ‖ 24.4, 26.3, …`
and lags run `…, 10.2 ‖ 12.2, 14.8, …`. **Any threshold in (19.9, 24.4) h of
span or (10.2, 12.2) h of lag gives the same answer**, so the number is not
fitted. The flagged rounds belong to **6 distinct competitions of 25 = 24%.**

**What it is not, yet.** Neither quantity is currently recorded. The pod's
`SETTLE` row does not carry `actual_close`, although `_maybe_settle` already
calls `/markets/{ticker}`, which returns it. **If Sam chooses EXCLUDE, the
rule is unimplementable until the pod records `actual_close` on settlement** —
a one-field, observation-only addition, and it must go in *before* the first
settlement or the first tournament will have to be classified from memory.
Recording the field is worth doing under either choice, since it is also the
only way to measure the resolver's live error.

### The number that makes this a real decision

Applying the rule retrospectively to the backtest, using §2's own statistic
(equal weight per tournament — see Item 4):

| sample | n | edge | z |
|---|---:|---:|---:|
| ORIGINAL, all | 19 | **+3.80 ¢/ct** | 3.29 |
| ORIGINAL, ex-weather | 14 | **+2.86 ¢/ct** | **1.96** |
| the 5 dropped | 5 | **+6.42 ¢/ct** | — |
| POOLED (widened), all | 22 | +1.45 ¢/ct | 0.65 |
| POOLED, ex-weather | 17 | **−0.01 ¢/ct** | **0.00** |

**Excluding weather-affected tournaments removes the backtest's five *best*
observations** — GESO26 +3.14, KLO26 +9.00, THMTPBW26 +7.32, U.SWOPBA26 +5.00,
USO26 +7.62 — and takes the original z from 3.29 to 1.96, i.e. below the
rule's own PASS bar. On the widened sample it takes the estimate to exactly
zero.

State this plainly: **EXCLUDE is the conservative choice procedurally and the
*unfavourable* choice numerically.** That is the right order to learn it in —
before any forward result — and it is precisely why this cannot be decided in
August. It also raises a question this document does not answer: whether the
measured edge is partly *an artefact of suspended rounds*, where a longer,
more chaotic round produces more ties and therefore more $1/n splits, which is
the mechanism the pod is betting on. That would be a *reason the edge is real*,
not a reason to exclude — but it is a hypothesis, and it is untested.

> **DECISION (Sam):** ______________________________________________
> *(KEEP all tournaments · EXCLUDE weather-affected · and if EXCLUDE, authorise
> recording `actual_close` on the SETTLE row before 2026-07-30T15:30Z)*

---

## Item 3 — posting above H = 24 h

### What the backtest actually sampled

The Phase-2 grid is **seven points**, transcribed in
`golf_quirks_research/backtest_fade_fills.py`:

| H | offset 0.00 | offset 0.02 | offset 0.04 |
|---:|---|---|---|
| 6 h | +0.5 ¢ [−1.9, +3.0] | +1.7 ¢ [−1.0, +4.6] | — |
| **12 h** | +2.1 ¢ [+0.8, +3.5] | **+3.4 ¢ [+1.7, +5.1] ← headline** | +4.7 ¢ [+2.7, +6.7] |
| **24 h** | +2.4 ¢ [0.0, +4.3] | **+3.5 ¢ [+0.5, +5.9]** | — |

**The maximum H sampled anywhere in the study is 24.0 h.** There is no
observation at H = 25, 30 or 40. **The region the pod actually posts in is
therefore entirely out-of-sample** — not "conservatively early within a
validated band", which is a materially weaker claim than the one previous
write-ups make.

Two further things the table shows that are worth reading before answering:

* the H = 24 h edge is the **weakest sampled point that is still positive** —
  its CI `[+0.5, +5.9]` nearly touches zero, against `[+1.7, +5.1]` at H = 12;
* the H = 24 h universe is **274 posted names against 364 at H = 12**, i.e. a
  quarter of the names have no 24 h anchor at all. Whatever the pod posts at
  the window edge is drawn from a thinner and differently-selected population.

### How far above 24 h the live pod actually posts

The resolver's error is one-sided early on **72 of 72** settled events, so
true H = predicted H + error. For a quote placed the instant the window opens
(predicted H = 24) — which is what the pre-flight dry run shows happens for
all 13 AIGWO26 names:

| resolver path | n | true H median | mean | min | p90 | max | fraction > 24 h |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tee_times` | 69 | **25.58 h** | 28.17 | 24.16 | 36.75 | 75.80 | **69/69** |
| `tour_day_offset` | 72 | **29.60 h** | 31.29 | 24.49 | 41.39 | 76.55 | **72/72** |

Taking the whole posting window rather than just its edge, the share of the
nominal `[12 h, 24 h]` window that lies above 24 h *true* is **26.3%** on
average under the live source mix and **47.9%** on the coarse day-offset path
that is in force for all seven currently listed events. On **8 of 72** events
the *entire* window sits above 24 h true.

**Every currently listed event resolves through `tour_day_offset`**, so the
47.9% column is the live regime this week, not the 26.3% one.

### Options

* **(a) Accept and record as a known deviation.** T counts these tournaments;
  the report says in writing that the forward test is running above the
  sampled region. Cheapest, and honest so long as it is written down *now*.
* **(b) Exclude fills whose true H exceeded 24 h.** Principled, and
  **catastrophic to throughput**: on the day-offset path roughly half the
  window is disqualified and on 8 of 72 events all of it is. It also cannot be
  applied at quote time — true H is only knowable at settlement — so it is a
  retrospective filter, which is the shape §8.2 warns about.
* **(c) Narrow the pod's band or window to compensate.** **This is the
  forbidden fix.** It is a §8.1 parameter change, it resets T to 0 under pod
  ID P-022b, and it is listed here only so that it is on the record as having
  been considered and rejected rather than quietly available later.

> **DECISION (Sam):** ______________________________________________
> *(a — accept and record · b — exclude >24 h true fills · c — §8.1 change,
> resets T to 0)*

---

## Item 4 — T = 14 was sized against +3.4 ¢/ct

### 4.1 The re-derivation, with every input shown

§5's method, unchanged:

```
SE(T)    = (ci_hi − ci_lo) / 2 / 1.95996
sigma    = SE(T) · sqrt(T)                    # between-tournament SD
T(power) = ((2.0 + z_power) · sigma / d)^2    # one-sided, critical z = 2.0
power(T) = Phi( d·sqrt(T)/sigma − 2.0 )
```

| input | d (¢/ct) | T | SE | sigma | T for 90% | power at T=14 |
|---|---:|---:|---:|---:|---:|---:|
| Phase-2 ORIGINAL `[+1.65, +5.13]` | +3.41 | 19 | 0.888 | 3.870 | **13.9** | **90.3%** |
| widened POOLED `[+0.08, +4.51]` | +2.57 | 22 | 1.130 | **5.301** | **45.8** | **42.6%** |

**T = 14 reproduces exactly** (§5 says `ceil(13.3)` using the rounded
`[+1.7, +5.1]`; the stored endpoints give 13.9 — same number).

**The "~24" figure in the queue is only half the move.** It comes from
lowering `d` to +2.57 while holding `sigma` at the *original* 3.870:
`((2.0+1.2816)·3.870/2.57)² = 24.4`. But the widening **also raised sigma**,
from 3.870 to 5.301 — the run summary recorded that the CI got *looser*, and
sigma is what a looser CI means. Carrying both moves gives **T ≈ 46**, not 24.
Which number is right depends on whether the added block's dispersion is
signal or a small adverse draw; the widening report's own verdict was
INCONCLUSIVE. **Both are shown rather than one being chosen.**

### 4.2 The finding that matters more than the power arithmetic

**The backtest and the sanctioned reader compute different statistics.**

* `quirks_common.bootstrap_weighted` — the source of every headline above —
  is documented as *"contract-weighted mean and 95% CI, resampling
  TOURNAMENTS with replacement"*. Clustering is right; weighting is by
  contracts.
* `P022_DECISION_RULE.md` §2 requires the opposite across tournaments:
  *"each tournament enters the aggregate with **equal weight**. A
  900-contract tournament and a 40-contract tournament count the same."*
  `scripts/p022_checkpoint.py` implements exactly that —
  `edge = mean(xs)`, `se = stdev(xs)/sqrt(T)`.

So the prior and the forward test are not on the same scale. Recomputing the
backtest under **the reader's own statistic**, from the per-tournament values
stored in `widen_results.json`:

| sample | n | edge | sigma | z | T for 90% | power @14 | @24 | @40 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ORIGINAL | 19 | **+3.80** | 5.04 | 3.29 | **19.0** | 79.4% | 95.5% | 99.7% |
| POOLED (widened) | 22 | **+1.45** | 10.44 | 0.65 | **555** | 7.0% | 9.4% | 13.1% |
| ORIGINAL ex-weather | 14 | +2.86 | 5.46 | 1.96 | 39.3 | 48.3% | 71.4% | 90.5% |
| POOLED ex-weather | 17 | −0.01 | 11.48 | 0.00 | — | — | — | — |

Under the statistic the checkpoint will actually apply, **T = 14 has 79%
power against the original effect, not 91%**, and the widened estimate is not
detectable at any T this pod will ever reach.

This is not a reason to change the gate. It is a reason to know, before the
first result, that **T = 14 was calibrated on a number the reader does not
compute.** I am not proposing which statistic is correct — §2 is locked and
says equal weight, so the checkpoint is right and the *backtest* is the one
using a different lens.

### 4.3 The cadence figure — checked, and neither prior number is right

The prompt flagged a conflict: `P022_DECISION_RULE.md` §5 says *"~15–19
qualifying tournaments per month"* while `REPORT_P028_Template_Sweep_2026-07.md`
says *"1.3/week for the round-leader series P-022"*. **Measured directly from
Kalshi tonight**, across all 13 series, counting distinct golf event codes
(§2's unit — rounds of one event are one observation):

```
28 distinct event codes · 78 event×round tickers · 2026-05-22 → 2026-07-23
62 days = 8.86 weeks
  -> 3.16 event codes/week  = 13.7/month     <- the gate's unit
  -> 8.81 event×round tickers/week
  by ISO week: 2,3,4,2,2,3,3,3,2,4  (stable)
```

* **§5's 15–19/month is too high** by ~25% at the low end.
* **P-028's 1.3/week is `KXPGAR1LEAD` alone** — that series has 12 settled
  events in 8.86 weeks = **1.35/week**, reproducing the figure exactly. It is
  correct for one series and wrong by 2.3× for P-022's 13-series universe.
* **Use 3.16 event codes/week.** It is an upper bound on gate throughput: a
  tournament only counts if P-022 actually filled in it and breached no cap.

### 4.4 Three framings, with their calendar cost at 3.16/week

| framing | T | calendar | what it buys / costs |
|---|---:|---|---|
| **(i) Hold T = 14, accept lower power** | 14 | **4.4 weeks** (~2026-08-28) | Fastest, and the gate stays untouched — worth a lot on its own. But at 79% power (reader's statistic, original effect) a KILL at T=14 carries a real chance of being a false negative, and §8.4 makes NO DECISION sticky. |
| **(ii) Raise T, reason recorded before any result** | 24 · 40 · 46 | **7.6 · 12.7 · 14.6 weeks** | Restores 90%+ power against the original effect; nothing restores power against the widened estimate. Raising a threshold before any data exists is legitimate; raising it after is not, which is why this is tonight's decision. |
| **(iii) T = 14 as a SCREENING gate + a pre-registered confirmatory T** | 14 then 40 | 4.4 then 12.7 weeks | Closest to §4a, which already grants exactly one extension to T = 40 for a marginal result. The change is only that a *positive* T = 14 is also treated as provisional rather than as PASS. Costs the same calendar as (ii) in the good case and gives an early KILL in the bad one. |

**Nothing above changes the gate.** §4's schedule and §4a's single extension
stand as locked unless Sam changes them here.

> **DECISION (Sam):** ______________________________________________
> *(i — hold T = 14 · ii — raise T to ___ · iii — screening T = 14 with
> confirmatory T = 40 · and: which statistic should the backtest be restated
> in, §2's equal weight or the bootstrap's contract weight?)*

---

## 5. Corrections needed in locked documents — flagged, not applied

The stop rule for this task is *change nothing*, so these are listed for Sam
rather than edited:

1. **`P022_DECISION_RULE.md` §5 and §12**: *"~15–19 qualifying tournaments per
   month"* → measured **13.7/month (3.16 event codes/week)**. Affects the
   projected T = 14 date, not the threshold.
2. **`scripts/p022_checkpoint.py:396`** prints the same *"Golf events accrue
   ~15-19/month"* string. It is a printed line in the sanctioned reader and
   does not enter any statistic, but the sanctioned reader should not print a
   measured-wrong number.
3. **`REPORT_P028_Template_Sweep_2026-07.md`**: *"1.3/week for the round-leader
   series P-022"* is `KXPGAR1LEAD` alone, not P-022's universe. The
   *conclusion* it supports — that `KXPGAH2H`'s 59 events/week dwarfs
   round-leader cadence — survives at 3.16/week and does not need revisiting.
4. **The statistic mismatch of §4.2** should be recorded in the rule itself, in
   whichever direction Sam chooses.
5. **`t_start_utc` is not implemented** (Item 1). Whether that matters depends
   on Item 1's answer.

## 6. What was changed by this task

**Nothing.** No parameter, no gate, no threshold, no `t_start_utc`, no
document under `golf_quirks_research/`. The only artefact is this file.

Verified after: band `(0.03, 0.12)` · offset `+0.02` · window `[12h, 24h]` ·
caps `0.5 / 5 / 15 %` · 13 series · `git diff` empty against
`src/round_leader_fade_maker.py`, `src/golf_schedule.py`,
`scripts/p022_checkpoint.py`, `golf_quirks_research/P022_DECISION_RULE.md`,
`manager/registry.yaml`, `config.yaml`, `config_multi_pod.yaml`.

## Sources

* `golf_quirks_research/P022_DECISION_RULE.md` (locked 2026-07-26) §2, §3, §4,
  §4a, §5, §7, §8.1, §8.2, §8.4, §12
* `golf_quirks_research/widen_results.json` — per-tournament values for both
  the 19-tournament original and the 22-tournament pooled sample
* `golf_quirks_research/backtest_fade_fills.py` — the seven-point Phase-2 grid,
  transcribed verbatim from the Phase-2 report
* `golf_quirks_research/schedule_resolver_validation.json` — 72 settled
  event-rounds, resolver residuals and round spans
* `golf_quirks_research/quirks_common.py::bootstrap_weighted`,
  `scripts/p022_checkpoint.py::evaluate` — the two statistics of §4.2
* Kalshi `/markets?status=settled` across all 13 `*LEAD` series, pulled
  2026-07-28 — the cadence measurement of §4.3
