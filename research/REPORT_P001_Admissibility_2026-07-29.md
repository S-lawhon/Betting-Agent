# P-001 post-fix admissibility — 2026-07-29

> ## The one-line classification
>
> **LIVE GATE, ~4–9 weeks — provisionally, and decisive by 2026-08-01.**
>
> Post-fix admissibility is **3 of 3 = 100%**, worst error **7 minutes**, and
> the tie-break fingerprint that produced the 14.3% all-time rate has **zero
> post-fix instances** — its last occurrence is **five minutes before the
> fixed process started**. n = 3 is too small to state a rate, but it is not
> too small to state that the defect is gone.
>
> **The remaining uncertainty is the PLACEMENT rate, not the admissibility
> rate**, and that rate is falling: 66.2/week over 28 days → 53.5 over 14 →
> **36.0 over 7**.

---

## 1. The cutoff, and why

Established from the **running process**, not a commit timestamp.
`journalctl -u betting-pod-shop` for 2026-07-26:

```
21:21:15 Stopped        21:21:25 Started
21:31:22 Stopped        21:31:32 Started     <-- the deploy that carried the fix
21:35:19 Stopped        21:37:52 Started
22:36:20 Stopped        22:36:30 Started     <-- last restart of the sequence
```

Three candidate cutoffs were computed rather than argued about:

| cutoff | instant | n | admissible |
|---|---|---:|---:|
| the reader's current `MATCHER_FIX_EPOCH` | `21:30:00Z` | 3 | 3 |
| **earliest defensible** — the fixed process's own start | **`21:31:32Z`** | **3** | **3** |
| conservative — last restart of the deploy sequence | `22:36:30Z` | 3 | 3 |

**All three give the same answer**, which is the most useful thing about the
exercise: the result does not depend on where the line is drawn. The reader's
`21:30:00Z` is 92 seconds *early* and would in principle admit the last
pre-fix cycle; it does not matter here because no placement lands in that gap.

**`21:31:32Z` is used below.** It is the instant a process running the fixed
matcher began, it is observable in the journal rather than inferred, and it is
the least generous of the three to my own conclusion.

---

## 2. Post-fix admissibility

Computed with **`scripts/p001_checkpoint.py`'s own `ticker_start()` and
`load_placements()`, imported, not reimplemented.** The previous attempt
hand-rolled a ticker parser that read Kalshi MLB tickers as UTC when they are
**ET**, and produced a spurious 0.3%. My figures reproduce the sanctioned
reader's `placements.since_matcher_fix` block exactly — `n: 3, admissible: 3,
rate: 1.0, max_delta_h: 0.12` — which is the check that matters: **two
readers, one number.**

| window | n | admissible | rate |
|---|---:|---:|---|
| all time | 742 | 109 | **14.7%** |
| last 28d | 265 | 20 | 7.5% |
| last 7d | 36 | 5 | 13.9% |
| **since the fix** | **3** | **3** | **100%** |

**Confidence interval, honestly.** Game-day-clustered bootstrap over the post-
fix rows gives `[100%, 100%]` on **2 clusters**, which is a degenerate
interval and should not be quoted as evidence. The defensible statement is the
exact one-sided binomial bound on 3 successes in 3 trials:

> **the post-fix admissibility rate is ≥ 36.8% with 95% confidence.**

That is a weak bound, and it is still enough to place the all-time 14.3% rate
**outside** it. Weak evidence pointing one way, stated as weak.

---

## 3. Every inadmissible post-fix row, diagnosed

**There are none.** All three post-fix placements, individually:

| market | placed (UTC) | ticker start (ET) | priced game_time (UTC) | Δ |
|---|---|---|---|---|
| `KXMLBGAME-26JUL282140COLSD-COL` | 07-27 18:02:45 | 07-28 21:40 −04:00 | 07-29 01:41 | **0.017 h (1 min)** |
| `KXMLBGAME-26JUL282210SEALAD-LAD` | 07-28 01:39:12 | 07-28 22:10 −04:00 | 07-29 02:11 | **0.017 h (1 min)** |
| `KXMLBGAME-26JUL272138HOULAA-LAA` | 07-28 01:44:12 | 07-27 21:38 −04:00 | 07-28 01:45 | **0.117 h (7 min)** |

The pod is not merely picking the right *day*; it is picking the right game to
within the odds feed's own timestamp granularity.

### The exactly-24.00h signature — the night's decisive number

The original tie-break fingerprint is a Δ of exactly 24.00 h: the matcher
choosing the same fixture on the adjacent day.

```
exactly-24.00h rows, all time : 152
latest such row, placed       : 2026-07-26 21:26:45.74 UTC
of which POST-fix             : 0
```

**The last one was placed 4 minutes 47 seconds before the fixed process
started**, and there has not been one since. 152 instances stopping dead at
the deploy instant is a far stronger signal than three admissible rows: it is
the failure mode disappearing exactly when its cause was removed.

Per the prompt, if this signature appeared post-cutoff it would be the night's
most important finding. **It does not appear.** The fix is complete on the
evidence available.

---

## 4. What is actually holding the gate at 0

Not admissibility. The sanctioned reader's tally:

```
clv_rows 654 | joined 654 | pre_epoch 654 | post_epoch 0
admissible_post_epoch 0 | inadmissible_post_epoch 0 | unjoinable 0 | not_mlb 0
verdict: NO DECISION — clean forward sample is empty; the epoch is
         2026-07-26 21:31Z and no admissible row has settled yet
```

**`post_epoch: 0`** — every one of the 654 CLV rows predates the epoch. The
gate reads 0 of 200 because the scenario-D forward sample **starts empty by
construction** and CLV settlement lags placement by roughly a day. The three
post-fix placements are for games on 07-27 and 07-28; their CLV rows have not
been written yet.

This is worth stating plainly because "0 of 200" invites the reading that
nothing is admissible. Nothing has *settled*.

---

## 5. Re-projection

Placement cadence for joinable MLB P-001 placements, and it is **declining**:

| window | placements | per day | per week |
|---|---:|---:|---:|
| last 28d | 265 | 9.46 | **66.2** |
| last 14d | 107 | 7.64 | **53.5** |
| last 7d | 36 | 5.14 | **36.0** |
| since the fix (30.3 h) | 3 | 2.38 | 16.7 |

200 admissible settled rows, at 100% admissibility:

| placement rate | weeks | date |
|---|---:|---|
| 66.2/week (28d) | 3.0 | 2026-08-19 |
| 53.5/week (14d) | 3.7 | **2026-08-23** |
| 36.0/week (7d) | 5.6 | **2026-09-04** |
| 16.7/week (post-fix, n=3) | 12.0 | 2026-10-20 |

For contrast, at the **all-time 14.3%** rate and 36/week: 200 rows needs
**38.9 weeks ≈ 2027-04-25**, which is what "inert" meant.

**Against the season.** The MLB regular season ends late September 2026;
postseason runs to late October. At 36–53/week the gate resolves **inside the
regular season** with weeks to spare. Only the post-fix-observed 16.7/week
figure pushes it to the edge of the season, and that figure rests on 30 hours
and 3 placements — it is a sample, not a rate.

**The registry's "late Aug – early Sept 2026" is now achievable**, at the 14-
and 7-day cadences respectively. It was not achievable at 14.3%.

---

## 6. When n becomes decisive

n = 3 cannot distinguish 90% from 100%, but it barely needs to — the question
is whether the rate is near 14.3% or near 100%, and those are far apart.

At **n = 10 with 10 of 10 admissible**, the exact one-sided 95% binomial bound
is `0.05^(1/10) = 74.1%`, which excludes anything near the old rate outright.
At the post-fix 2.38/day, n = 10 arrives **2026-08-01**; at the 7-day
5.14/day, **2026-07-30**.

> **Decisive by 2026-08-01.** If the rate is still 100% then, P-001 is a live
> gate resolving in late August / early September. If any exactly-24.00h row
> appears before then, the fix is incomplete and everything above is void.

`recheck_after: "2026-08-01"` is recorded in the registry so this is a dated
obligation rather than a note.

---

## 7. The label — changed

`blocked_on: time` → **`blocked_on: measurement`**, a new value defined in the
registry header:

> The blocker is not calendar and not a defect: it is that the **accrual rate
> is not yet measured**, so no resolution date can be stated. Carries
> `recheck_after`. Distinct from `time`, which asserts a sample is accruing at
> a known rate — claiming that when the rate is unknown is how a gate that
> cannot resolve gets reported as one that is patiently waiting.

`time` was dishonest in both directions, exactly as the brief said: at 14.3%
the gate could not resolve at all, and the reason it was 14.3% was a defect,
not the calendar. It is not honest to call it `time` *yet* either, on n = 3.
**It flips to `time` at n ≥ 10 confirmed** (§6), and that transition is now a
dated item rather than a judgement call.

---

## 8. Stop rule observed

* **The matcher was not changed.** Read only.
* **The gate was not changed** — 200 rows, scenario D, `ADMISSIBLE_HOURS = 3.0`,
  `EPOCH` all untouched.
* No threshold moved. The remedy, if the rate turns out bad, is Sam's.

## 9. For Sam

1. **P-001 is not inert.** The single number this task was written to produce
   is **3 of 3, worst error 7 minutes, and 152 → 0 on the tie-break
   fingerprint.** Provisionally it is the fund's fastest gate.
2. **Watch the placement rate, not the admissibility rate.** 66 → 53 → 36 per
   week is the real risk to the projected date, and nothing in this task
   explains the decline. Worth a look before it is read as an admissibility
   problem.
3. **Consider correcting `MATCHER_FIX_EPOCH` from `21:30:00Z` to `21:31:32Z`**
   — one line, no effect on any current number, but it currently admits 92
   seconds of pre-fix engine time. Not changed here, because the stop rule
   says not to touch the gate.
