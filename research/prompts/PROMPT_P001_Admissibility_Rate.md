# PROMPT — P-001 post-fix admissibility rate

**One number decides whether P-001 is a live gate or a parked one.**

## The situation, including my own correction

P-001's gate (scenario D) reads **0 of 200**. All-time admissibility is **106/739 = 14.3%**. At that rate and ~45 placements/week, 200 rows needs **~31 weeks (≈ March 2027)** — past the end of the MLB season, which makes the pod **inert**, not conservative. The registry's "late Aug – early Sept 2026" is unachievable.

But the matcher was reported broken and **that report was wrong**. It was based on five placements made *within five minutes of the restart that loaded the fix* — provably not the fixed matcher's output. Reproduced against the deployed matcher, it picks the right day in every ordering and rejects a wrong-day-only set at 1440 min against a 720-min window. **The one unambiguously post-fix MLB placement is admissible to within one minute.**

So the open question is exactly this: **what is the post-fix admissibility rate?** It is currently measured at **n = 1**. At 14.3% the pod is inert; at something near 100% it resolves in ~4–5 weeks and becomes the fund's fastest gate.

## Do this

1. **Establish the fix's deploy instant precisely** from the deploy record and the running process's start time — not from a commit timestamp. Everything before it is contaminated; everything within the restart window is contaminated. State the cutoff and defend it.
2. **Partition all placements since the cutoff** and compute the admissibility rate with a confidence interval, clustered by game-day. Report n honestly — if it is still tiny, the deliverable is the CI and the date at which n becomes decisive, not a point estimate dressed as a finding.
3. **Diagnose every inadmissible post-fix row individually.** There will not be many. For each, state the cause. Pay attention to the **exactly-24.00h** signature — four of the five contaminated rows carried it, and it is the original tie-break fingerprint. If it appears post-cutoff, the fix is incomplete and that is the night's most important finding.
4. **Use the checkpoint's own `ticker_start()`.** Do not hand-roll a ticker parser: the previous attempt read Kalshi MLB tickers as UTC when they are **ET** and produced a spurious 0.3%. This is the second time a reimplemented reader has produced a wrong second opinion; treat the sanctioned reader as the only reader.
5. **Re-project the gate** at the measured rate, with the interval, and compare against the MLB season end. Then state plainly which of these P-001 is: *live gate, ~N weeks* · *inert, cannot resolve this season* · *undetermined until DATE*.
6. **Fix the label.** `blocked_on: time` is dishonest for P-001 under either outcome — it is blocked on a measurement, or on a defect. Update the registry to whichever the evidence supports and record the change.

## Stop rule

**Do not change the matcher.** Do not change the gate or the 200-row threshold. If the rate is bad, the deliverable is the number and the re-projection — the remedy is Sam's call.

## Deliverable

`research/REPORT_P001_Admissibility_2026-07-29.md`: cutoff and its justification, post-fix n, rate with clustered CI, per-row diagnosis of every inadmissible case, re-projected resolution date, and the one-line classification above.
