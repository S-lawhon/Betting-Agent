# P-001 — the falling placement rate, explained

**Run:** 2026-07-28, Task 8 of `RUN_QUEUE_2026-07-28-day.md`.
**Diagnosis only — no threshold, matcher or config change.**

---

> ## It is not seasonality, not the Odds API, and not the matcher fix. **It is a CLV gate Sam turned on on 2026-07-18**, and it is now rejecting **89.7%** of everything that clears the edge threshold.
>
> **Opportunities did not fall — they nearly doubled.** Candidates clearing the
> edge threshold went **131 → 60 → 169 → 301** across the four weeks. Placements
> went **131 → 60 → 60 → 31** because a new stage started rejecting them.
>
> **Every one of the 1,432 rejections is the same reason: `price ≥ $0.50`.** The
> gate is a pure underdog filter. Its second clause — positive maker-net edge —
> **has never once bound.**
>
> **The gate still resolves this season**, at every rate considered: **2026-08-18
> / 08-23 / 09-05**, and **2026-09-11** at the rate I actually measure. The
> risk is not today's rate; it is that the rejection rate is *rising* — 64.5% →
> 89.7% — and a further step to ~95% pushes resolution past the MLB season.

---

## 0. A methodological correction I had to make mid-run, because it inverted the answer

My first pass globbed `data/trade_logs/trade_log*.jsonl*`. **That also matches
the four `.bak_*` snapshots the 2026-07-26/27 correction scripts left behind**
(`bak_scalar_correction` ×2, `bak_spurious_voids`, `bak_void_unsettleable`).
Every placement from 07-22 onward is therefore present **up to five times**,
while 07-28's rows — written after the last backup — are present once.

| | 07-01..07 | 07-08..14 | 07-15..21 | 07-22..28 |
|---|---:|---:|---:|---:|
| counted raw (contaminated) | 210 | 84 | 86 | **196** |
| deduplicated (correct) | **131** | **60** | **60** | **31** |

**The contaminated series says the last week ROSE. The correct one says it
halved.** Opposite conclusions from the same directory.

This is the same family as the log-rotation orphan bug: **a glob over a
directory that also holds copies of itself.** The probe now excludes `.bak_*`
and `contaminated` **by name** and deduplicates by fingerprint as a backstop.

**It deliberately does NOT exclude `.archive_*`** — those are genuine
rotations holding rows that exist nowhere else, and dropping them would swap a
double-count for a data loss. 16 files read, 4 skipped, 787,927 duplicate
fingerprints dropped.

## 1. The fact, with the windows stated

`research/p001_placement_probe.py`, run on the droplet. Blocks are 7 days
counted **back from the last day with rows** (2026-07-28), so **no block is a
trailing partial** — the only partial one is labelled and excluded from the
comparison.

| window | days | PLACED | SCANNED | conv % | per week |
|---|---:|---:|---:|---:|---:|
| 2026-06-24..06-30 | 3 | 116 | 9,885 | 1.173 | *270.7 — PARTIAL, ignore* |
| 2026-07-01..07-07 | 7 | **131** | 13,870 | 0.944 | 131.0 |
| 2026-07-08..07-14 | 7 | **60** | 7,677 | 0.782 | 60.0 |
| 2026-07-15..07-21 | 7 | **60** | 7,727 | 0.776 | 60.0 |
| 2026-07-22..07-28 | 7 | **31** | 7,468 | 0.415 | 31.0 |

**The decline is real and it is in two distinct steps with different causes:**

* **Week 1 → 2: the DENOMINATOR halved** — 13,870 scanned → 7,677. Conversion
  barely moved (0.944% → 0.782%).
* **Weeks 2 → 4: the denominator is FLAT** (7,677 / 7,727 / 7,468) **and
  conversion collapsed** — 0.782% → 0.776% → **0.415%**, a 47% fall on a
  stable scan volume.

Two different questions, and only the second is the one the queue is worried
about.

## 2. Which stage dropped — the funnel splits it cleanly

`SKIPPED_CLV_GATE` is a rejection stage that **did not exist before
2026-07-19**. Candidates = `PLACED + SKIPPED_CLV_GATE`, i.e. everything that
cleared the edge threshold.

| window | candidates | placed | CLV-gate rejected | rejection % |
|---|---:|---:|---:|---:|
| 2026-07-01..07-07 | 131 | 131 | 0 | 0.0% |
| 2026-07-08..07-14 | 60 | 60 | 0 | 0.0% |
| 2026-07-15..07-21 | **169** | 60 | 109 | **64.5%** |
| 2026-07-22..07-28 | **301** | 31 | 270 | **89.7%** |

> **Opportunities did not fall. They rose 60 → 169 → 301.** Placements fell
> because a filter was added and is rejecting nine in ten.

`SKIPPED_RISK` and `SKIPPED_DUPLICATE` are **zero on every day in the window** —
neither the position cap nor deduplication is implicated.

## 3. What the gate is, and why the rejections are 100% one reason

`config_multi_pod.yaml`, with its own dated rationale:

```yaml
# CLV gate (2026-07-18): validated MLB edge is in UNDERDOGS vs the de-vigged
# Pinnacle close, net-positive only as a maker. Reversible: enabled: false.
clv_gate:
  enabled: true
  max_entry_price: 0.50      # underdogs only (Kalshi price < 50c)
  maker: true
  min_net_edge: 0.0
```

`src/pods/kalshi_moneyline.py:140` demotes a `PLACED` to `SKIPPED_CLV_GATE`
when `_passes_clv_gate` fails. Classifying all **1,432** rejections by which
clause fired:

| reason | count | share |
|---|---:|---:|
| **`price ≥ $0.50`** (favourite) | **1,432** | **100.0%** |
| `net_edge_maker ≤ 0` | 0 | 0.0% |
| missing inputs (fails closed) | 0 | 0.0% |

**The gate is a pure price filter.** Its `min_net_edge` clause has never once
been the binding constraint, and the fail-closed branch has never fired — so
missing `fair_prob` / price is not silently eating candidates.

**This is a deliberate strategy narrowing with a date and a written rationale,
not a defect.** The queue calls the decline "unexplained"; the explanation has
been sitting in the config for ten days. What was missing is that **nobody
connected the 07-18 change to the rate it was always going to cause** — the
same shape as every other finding in this fund: the fact was recorded, the
consequence was not.

## 4. The candidates, worked in the order the prompt asks

### The matcher fix — checked FIRST, and it is **not** the cause

The prompt calls this "the most likely explanation and the only one that is
good news." **It is not the explanation.**

The exactly-24.00h tie-break fingerprint appears **4 times in 3,369 all-time
P-001 `PLACED` rows, and the last was 2026-04-27** — three months before this
window. The 152 instances in the 07-29 report were **not placements**; they
were in a different population (CLV rows). Post-fix days (07-27, 07-28) carry
zero. **Contribution to the decline: zero.**

### Seasonality — real, and it explains the FIRST step only

Scan volume 13,870 → 7,677 between weeks 1 and 2, then flat. The sport mix
shows why: **NFL futures dominate the scanned book** (07-22: NFL 721 vs MLB
725; 07-15: NFL 489 vs MLB **1**), which is pre-season listing noise, while
placements are almost entirely MLB. Tennis appears from 07-27 (Washington
Open, 233 + 135 scanned). **Explains week 1 → 2. Does not touch weeks 2 → 4,
where the denominator is flat.**

### Odds API supply — no evidence, and the funnel argues against it

If book coverage or Pinnacle presence had degraded, **candidates clearing the
edge threshold would fall**. They rose, 60 → 169 → 301. A degraded sharp
consensus cannot produce more edge-clearing candidates. **Not implicated.**
(Not positively excluded either — I did not audit the book mix per request;
the funnel simply makes it an unnecessary hypothesis.)

### Threshold / config drift — found, and it IS the answer

The 2026-07-18 `clv_gate` block. No other P-001 threshold moved in the window.

### Silent failure — no evidence

`SKIPPED_RISK` and `SKIPPED_DUPLICATE` are zero throughout; scan volume is
stable at ~7,500/week across the last three weeks; the two low-scan days
(07-25: 674, 07-26: 308) coincide with known restarts and recover immediately.

## 5. The one thing that is genuinely worrying

**The rejection rate is rising: 64.5% → 89.7%.** That is not what a fixed
price filter does against a stable candidate mix — it means the candidate pool
is shifting toward favourites. Daily rejections: 2, 86, 40, 100, 380, 545, 60,
16, 152, 51 — with 07-23 and 07-24 alone accounting for 925 of 1,432.

I have **not** established why, and it is a different investigation:
whether the edge threshold is now surfacing mostly favourite-side candidates,
or whether the de-vigged sharp fair has drifted relative to Kalshi. **Flagged,
not diagnosed** — the stop rule is diagnose-don't-tune, and this needs its own
pass.

## 6. The gate, re-projected — the cost as a date

P-001's gate is **200 admissible CLV rows**, currently **0 of 200** because
`post_epoch = 0`: all 654 existing CLV rows predate the fix epoch and
settlement lags placement by about a day. Nothing is inadmissible; nothing has
settled yet.

At the post-fix admissibility of **3 of 3 (100%)**, from 2026-07-28:

| placement rate | weeks to 200 | projected resolution | inside the MLB season? |
|---|---:|---|---|
| 66.2/wk | 3.0 | **2026-08-18** | yes, comfortably |
| 53.5/wk | 3.7 | **2026-08-23** | yes, comfortably |
| 36.0/wk | 5.6 | **2026-09-05** | yes |
| **31.0/wk** (measured here) | **6.5** | **2026-09-11** | **yes** |
| 15/wk (if rejection reaches ~95%) | 13.3 | **2026-10-28** | **NO** |
| at the all-time 14.3% admissibility instead | ~45 | 2027-06 | no |

> **The honest answer is the low-urgency one: the gate still resolves this
> season at every rate actually observed**, including the current 31/wk, with
> about two weeks of slack before the season ends. **That materially lowers the
> urgency** and should be said plainly.
>
> **The urgency is in the trend, not the level.** One more step in the
> rejection rate — 89.7% → ~95% — takes it outside the season, and the
> rejection rate has moved that far once already in one week.

Caveats on the projection: it assumes 100% admissibility from n = 3 (the exact
one-sided 95% bound on 3/3 is ≥36.8%, which is weak evidence, and at 36.8% the
31/wk case lands in 2027); it assumes settlement and the daily CLV job keep up;
and it counts placements, not settled positions.

## 7. What I did not do

**No threshold changed, no matcher changed, no config changed.** Per the stop
rule. The `clv_gate` is `reversible: enabled: false` by its own comment, and
turning it off would roughly triple the placement rate — **but it would also
put P-001 back to betting favourites, which is precisely the population the
2026-07-18 validation said carries no edge.** That trade is Sam's.

## 8. For Sam

1. **The decline is your own 2026-07-18 CLV gate**, working as designed. Not a
   defect. Nothing to fix.
2. **The gate is a pure underdog price filter in practice** — `min_net_edge`
   has never bound in 1,432 rejections. If the intent was two conditions, only
   one is doing work.
3. **The real decision:** placement volume is the gate's clock, and the CLV
   gate is trading gate *speed* for gate *quality*. It still resolves this
   season at the current rate. **Leave it on** is my recommendation — a faster
   gate measuring the wrong population is what P-001 already spent months
   doing.
4. **Watch the rejection rate, not the placement rate.** 64.5% → 89.7% in one
   week. If it reaches ~95%, resolution moves outside the MLB season and the
   decision changes.
5. **Delete the four `.bak_*` trade-log copies** on the droplet, or move them
   out of `data/trade_logs/`. They are 4 of the 20 files matching the log glob
   and they inverted this analysis once already. **Not deleted by me** —
   nothing gets deleted here without you.
