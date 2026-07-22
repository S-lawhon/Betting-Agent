# P-016 v2 — Offline Suppress/Widen Grid + Lag Measurement

**Date:** 2026-07-21 · **Author:** offline analysis (no deployment, no pod change)
**Input:** `data/trade_logs/maker_fills.jsonl` (pulled read-only from the droplet)
**Harness:** `src/maker_challenger.py` (Tier-2, 33 tests, unmodified) driven by
`research/p016_v2_offline_grid.py`
**Spec:** [SPEC_P016_v2_2026-07-21.md](SPEC_P016_v2_2026-07-21.md) §8 (first concrete step)

## TL;DR — **NO-GO on a live pilot as specified.**

No arm turns the +5m markout non-negative, or even close. The best arm
(suppress-15s) moves the champion's per-fill markout from **−1.73¢ to −1.49¢** —
a +0.24¢ improvement, an order of magnitude below the **3.85¢** this sample can
detect at 80% power. More decisively, **v2's founding premise does not hold on
this tape**: the loss is *not* localised in the post-event window. ~60% of fills
sit >60s from any observed state change and markout **−2.24¢** — v2's mechanism
cannot touch them. And the true-event-vs-observed lag the spec calls the
load-bearing risk (§3) is **not measurable at all** — no play-by-play event
timestamp is logged anywhere.

---

## Data & method

- Analysis set: **672 real fills across 20 games**, shadow excluded, each paired
  with its +5m (`horizon_s=300`) MARKOUT by the harness's `build_tape`.
- `exclude_before: 2026-07-20T01:26:00Z` (registry P-016 gate block) applied
  defensively — a **no-op** here: 0 real fills predate it (the 484 contaminated
  first-night fills already live in `contaminated_2026-07-20/`, not in this file).
- Two UTC dates, one contiguous run: **2026-07-21** (366 fills, −0.44¢, the "best
  day") and **2026-07-22** (306 fills, −3.26¢). Ex-best-day the tape is −3.26¢.
- Markout math, fee-adjustment (`_fee_adj_cents`), the subset invariant
  (`verify_subset`), and the game-clustered bootstrap all come from the harness
  as-is. Nothing was re-implemented.

### Baseline (harness champion) vs the postmortem headline

The harness's per-fill champion markout is **−1.73¢** (qty-weighted −2.34¢),
vs the postmortem's **−1.29¢** from `scripts.maker_report` on 814 fills. The gap
is sample/weighting (672 paired-markout fills here vs 814; per-fill vs
qty-weighted). Direction is identical — solidly negative — and **every arm below
is measured against the harness's own champion**, so the deltas are internally
consistent regardless of which headline baseline you prefer.

---

## TASK 1 — Suppress / Widen grid

Widen offset = **5¢** ("large" per Route B). CIs game-clustered, Bonferroni over
all 10 arms. `drop_mk` = mean markout of the fills the arm gives up (a positive
number here is impossible; less-negative = the arm is dropping *worse* fills).

| Arm | n | ret% | mean mkout | drop_mk | total $ | Δ vs champ | 95% CI | P(improve) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **champion** | 672 | 100% | **−1.73** | — | −130.43 | — | — | — |
| suppress-5s | 657 | 97.8% | −1.53 | −10.57 | −118.97 | +0.20 | [+0.00, +0.47] | 0.99 |
| suppress-10s | 634 | 94.3% | −1.63 | −3.33 | −129.07 | +0.10 | [−0.45, +0.51] | 0.72 |
| suppress-15s | 602 | 89.6% | −1.49 | −3.76 | −116.60 | +0.24 | [−0.45, +0.85] | 0.85 |
| suppress-20s | 580 | 86.3% | −1.78 | −1.42 | −133.30 | −0.05 | [−0.85, +0.68] | 0.45 |
| suppress-30s | 539 | 80.2% | −1.93 | −0.93 | −132.04 | −0.20 | [−1.32, +0.82] | 0.33 |
| widen-5s | 658 | 97.9% | −1.55 | −9.84 | −119.13 | +0.18 | [+0.00, +0.44] | 0.99 |
| widen-10s | 639 | 95.1% | −1.68 | −1.87 | −130.12 | +0.05 | [−0.58, +0.48] | 0.62 |
| widen-15s | 609 | 90.6% | −1.55 | −2.92 | −120.10 | +0.18 | [−0.41, +0.73] | 0.81 |
| widen-20s | 587 | 87.4% | −1.83 | −0.60 | −136.80 | −0.10 | [−0.87, +0.58] | 0.37 |
| widen-30s | 551 | 82.0% | −1.98 | −0.08 | −136.30 | −0.25 | [−1.40, +0.70] | 0.28 |

**Reading it:**

1. **No arm gets near zero.** The best (suppress-15s, −1.49¢) is still ~15× worse
   than break-even. v2's whole thesis — "stop losing in the window and the
   strategy plausibly turns non-negative" — is not visible anywhere in this grid.
2. **The improvements are noise-sized.** Every positive delta (+0.05 to +0.24¢)
   is far below the **3.85¢ minimum detectable effect** at 80% power
   (deff≈1.99, ICC≈0.031, 10-arm Bonferroni). The T=5 arms show CI lower bound
   +0.00 and P(improve)=0.99, but the *effect* is +0.2¢ — statistically "there,"
   economically irrelevant to a −1.7¢ hole.
3. **Widening the window past ~15s actively hurts** (Δ goes negative at T=20, 30;
   `drop_mk` climbs toward −0¢). Beyond 15s the arms suppress fills that were
   *not* the problem — forgoing their (roughly neutral) spread for nothing. This
   is the anti-cannibalisation check failing in the other direction.
4. **The forgone-spread trade is a wash.** Total markout dollars barely move:
   champion −$130, every arm between −$117 and −$137. Suppressing the 15 worst
   fills (0–5s, −10.6¢) recovers ~$11; extending the window gives it back.
   Suppress and widen are near-identical — the pick-off in the window is
   avoidable but not meaningfully *priceable* at 5¢ either.

---

## TASK 2 — The lag measurement (spec §3, the load-bearing risk)

### Is the true-event-vs-observed lag measurable? **No. It is not logged.**

Checked exhaustively:
- **FILL records** carry only `secs_since_state_change`, `fill_epoch`/`iso`, and
  `state_key_at_quote`/`state_key_now`. All are *observed-poll* times.
  `secs_since_state_change = fill_epoch − state_changed_at`, and
  `state_changed_at = self._now()` at the cycle that *observed* the change
  (`live_maker_pod.py:601`) — observed-to-observed, by construction.
- **MARKOUT records** carry no timestamp beyond `iso`.
- **`maker_anchors.json`** is pregame anchors only (`captured_utc`, `pregame_prob`).
- **The pod never reads a play-by-play event time.** `MlbStatsApi.linescore`
  polls `/game/{pk}/linescore`, which has no `about.endTime`/`startTime`; the only
  time it stamps is `fetched_at` (poll time). No statsapi event timestamp reaches
  any log.

**So the lag cannot be computed from disk. Stating that plainly, per the task —
no lag number is fabricated.** Measuring it requires new instrumentation (log the
statsapi play `about.endTime` next to `state_changed_at`), which is exactly the
"faster/better state polling" work the spec flags as the prerequisite.

### Best available proxy: markout by observed `secs_since_state_change`

| Bucket (s) | n | mean +5m markout | share |
|---|---:|---:|---:|
| 0–5 | 15 | **−10.57¢** | 2.2% |
| 5–10 | 23 | +1.40¢ | 3.4% |
| 10–15 | 32 | −4.28¢ | 4.8% |
| 15–30 | 63 | +2.22¢ | 9.4% |
| 30–60 | 138 | −1.00¢ | 20.5% |
| **60+** | **401** | **−2.24¢** | **59.7%** |

**Which pattern does the data show?** Mixed, and the honest read cuts against v2:

- The pick-off **does** spike at the smallest observed bucket (0–5s: −10.6¢) and
  the next bucket recovers (5–10s: +1.4¢). Taken alone that is the "suppression
  can help" signature — the observed window is not *hopelessly* lagged.
- **But it is a rounding error in the book.** The 0–5s bucket is **15 fills
  (2.2%)**. The 10–15s bucket dips again (−4.3¢, non-monotonic = noise at n=32).
  The loss is **not concentrated near state changes at all**: the **60+ bucket is
  60% of all fills and markouts −2.24¢**, and it *dominates* the total. v2's
  mechanism gates only the post-event window and structurally **cannot touch the
  bucket that holds the loss.** This is the decisive finding.

### The postmortem's −8.78¢/−0.96¢ split does not reproduce on this tape

v2's premise (spec §1.1) rests on "inside the window −8.78¢ vs outside −0.96¢
(near break-even)." Recomputed on the harness tape:

| | within 15s | outside 15s |
|---|---:|---:|
| per-fill | −3.76¢ (n=70) | **−1.49¢** (n=602) |
| qty-weighted | −2.86¢ | −2.30¢ |

Neither the −8.78 nor the −0.96 reproduces. Crucially the **outside-window number
is −1.49¢ (per-fill) / −2.30¢ (qty-wtd), not "near break-even."** So even a
*perfect, costless* window defense — drop every within-15s fill for free — lands
the strategy at **−1.49¢**, still deeply negative. The premise that "the rest is
near break-even, so defending the window turns it non-negative" is **false on this
data**. (The postmortem figure likely came from a different sample/window/weighting
via `scripts.maker_report`; it could not be reconciled here and should be, but the
same harness the spec mandates does not support the localised-loss claim.)

### Poll cadence — the window can't be finer than the poll, and isn't

- Median gap between consecutive fills within a game ≈ **9.8s** (p10 0.2s, p90
  193s), consistent with the pod's ~12s cycle.
- `secs_since_state_change`: min 0.4s, p10 14.8s, **median 76s**; only **6.4% of
  fills are <12s** since the observed change.
- Consequence: a "5s" suppression window is **finer than the poll interval**. The
  observed state change is itself up to a full cycle (~12s) stale relative to the
  true event, and the counterparty picking us off in the 0–5s bucket already had
  that head start. **A 5s window anchored to a ~12s-stale observation defends a
  window it cannot resolve** — precisely the §3 failure mode, now quantified. The
  only two arms with a (trivial) positive signal, T=5, are exactly the ones the
  poll cadence cannot operationally deliver.

---

## Recommendation: **NO-GO on the v2 live pilot.**

1. **The mechanism does not rescue the strategy, even in the optimistic offline
   replay.** Best case −1.49¢; MDE 3.85¢; the loss lives in the 60% of fills v2
   cannot gate. Suppress and widen are a wash on total dollars.
2. **v2's founding assumption fails on the tape.** Outside the 15s window is
   −1.49¢, not break-even. Removing the window leaves a losing strategy.
3. **The load-bearing risk is unmeasurable and the cadence makes it worse.** True
   lag isn't logged; the 12s poll is coarser than the 0–5s effect. If anything
   were to precede a pilot it is *faster/instrumented state polling* (log the
   statsapi event time), not a suppression window — and that is a different,
   larger piece of work the spec itself flags (§3).

This is a real, useful negative result, on the harness the spec chose, on one
underpowered best-day-heavy slate (n=672, 20 games — say so loudly). It says the
window-defense mechanism does not turn P-016 non-negative and there is no case for
spending a live pilot on it. Per spec §6, the honest conclusion is that
market-anchored MLB making does not work here; do not bolt on a fair-value model
to rescue it. If v2 is pursued at all, the prerequisite is measuring the true lag
(new logging), not deploying the suppression timer.

### Caveats (adversarial, as required)
- **One slate, best-day-heavy.** n=672, 20 games. Ex-best-day the tape is −3.26¢.
  Every number is underpowered; the 0–5s bucket is 15 fills.
- **Baseline differs from the postmortem** (−1.73¢ vs −1.29¢) and the −8.78/−0.96
  split did not reproduce. The discrepancy is unreconciled and worth a look, but
  it cuts *against* v2, not for it.
- **Offline replay only measures wider/suppress arms** (L5) — respected via
  `verify_subset`; no tighter arm was attempted. This is the ceiling of what the
  tape can answer, and it already says no.
