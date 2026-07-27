# Run Summary — 2026-07-28

> ## Did P-022 quote?
>
> **No — but for the first time that is a calendar fact rather than a defect.**
>
> The blocker is closed **and DEPLOYED — 2026-07-27 17:53 UTC**, with ~46h of
> margin against the window. P-022 now resolves a real round close for all three
> listed tournaments (ROC26 `07-30T18:30Z`, AIGWO26 `07-30T15:30Z`, POI26
> `07-31T16:00Z`) instead of one shared `2026-08-16` placeholder, and all 351
> markets are in its book with **0 unresolved**.
>
> **The first placement window opens 2026-07-29T15:30Z.** It has not opened, so
> no live quote has been observed. That remains the only thing that counts:
> two previous fixes were "verified" and failed live.
>
> The instrument caught its own state change — `status.jsonl` goes
> `17:00 CLOSE_REF_PLACEHOLDER alarm=True` → `17:30 CLOSE_REF_PLACEHOLDER
> alarm=True` → **`17:54 WAITING alarm=False, resolved=3, unresolved=0`**.

---

## Per-task verdicts

| # | Task | Verdict | Headline number |
|---|---|---|---|
| **1** | P-022 close-time resolution | **FIXED + DEPLOYED** | resolver error **one-sided early on 72 of 72** settled events; min +0.16h, median +1.58h |
| **2** | Gate throughput audit | **KILL** (hypothesis refuted) | 432 P-001 settlements in 28 days → **0** gate-countable rows; 4 of 5 gates are not measuring |
| **3** | P-028 golf template sweep | **KILL** (both leads) | H2H ties pay **0.50/0.50**, not $0 — pair sums to $1.00 exactly; category-leader has **2 tournament-clusters** |
| **4** | R5 follow-ups | **NOT REACHED** | — |
| **5** | P-022 widen the backtest | **PARTIAL** | pre-declaration committed before any pull; **13/13 series** have history back to **2026-05-22** — "~1 month" refuted |
| **6** | Corrections + droplet hygiene | **NOT REACHED** | — |
| **7** | Fee-table fixture + CI | **FIXED** | **fifth drift found**; blast radius **zero verdicts changed** |
| **8** | Data-readiness audit | **MIXED** | P-018 **ahead**; MLB props **on time to the day**; weather **dead, 139 of 139 runs failing** |

**Reports:** [P-022 close time](REPORT_P022_Close_Time_2026-07.md) ·
[P-028 golf sweep](../golf_quirks_research/REPORT_P028_Template_Sweep_2026-07.md) ·
[gate throughput](REPORT_Gate_Throughput_2026-07.md) ·
[fee audit](REPORT_Fee_Audit_2026-07-27.md) ·
[data readiness](REPORT_Data_Readiness_2026-07-27.md) ·
[P-022 first quote (updated)](REPORT_P022_First_Quote_2026-07.md) ·
[widening pre-declaration](../golf_quirks_research/P022_WIDENING_PREDECLARATION.md)

---

## The finding that reframes the queue

The queue's premise was *"the bottleneck is observation throughput, not
hypothesis supply."* **Measured, that is refuted — and what replaces it is
worse.**

The fund settles plenty: **432 P-001, 54 P-014, 38 P-017 and 5 P-015 settled
positions in the last 28 days.** Observation supply is not scarce. What is
missing is the path from a settlement to a *gate-countable* observation, and it
is broken four separate ways:

| pod | 28d settled | gate reads | why |
|---|---:|---|---|
| P-001 | **432** | 0 / 200 | **CORRECTED** — the matcher is fine (see report §2.1); CLV rows lag settlement |
| P-014 | 54 | ~~unreadable~~ **331 / 500** | no sanctioned reader existed — **FIXED + deployed**; resolves ~2026-10-23 |
| P-015 | 5 | ~~0, but 5 exist~~ **now 5** | reader read `data/pods/P-015.jsonl`, which does not exist — **FIXED + deployed** |
| P-017 | 38 | 1 / 8 | working correctly — the control |
| P-022 | 0 | 0 / 14 | could not compute its window (fixed and deployed today) |

> **The scarce resource is not observations and not hypotheses. It is correctly
> instrumented observations.** A 33rd candidate is worth less than an hour spent
> on any line of that table.

That conclusion is why Tasks 3, 4 and 6 were the ones left unreached rather than
Tasks 2, 7 and 8 — Task 3 is a new-hypothesis hunt, and this run's own
measurements argue it is the lowest-value item on the list. **Flagging that as a
judgement call, not a fact: the queue ranked 3 above 5–8, and I did not.**

---

## What changed, by task

### Task 1 — P-022 close-time resolution · **FIXED and DEPLOYED**

`src/golf_schedule.py` resolves `(competition, round) → round-end UTC` from
ESPN's free public golf API for all five tours, keyed on Kalshi's
`product_metadata.competition`. Three sources in precedence: this round's tee
times (+4.0h), this event's R1 tee times + (n−1)d (+1.0h), per-tour day offset
(5.5–14.5h). **ESPN publishes no tee times ~3 days out**, so the coarse path is
what runs at listing time, and `discover()` re-resolves every pass rather than
freezing the coarse answer.

Validated against **72 settled events** where Kalshi *has* rewritten
`close_time`: error is **one-sided early on 72 of 72**. That direction is the
entire design — the measured round span (close − first tee) is **12.5h median**,
so the locked 12h window edge sits at the first tee and there is **no negative
tolerance at all**. Under a *perfect* resolver, H=12h is already inside a live
round on **46 of 69 events**; the conservative bias cuts that to 6.

The Kalshi-only fallback is dead on the numbers: open→close spans run **15h to
797h** (sd 168h). The resolver fails **closed**.

Also closed behind it: restart-safe books (caps no longer re-arm from zero),
cap-breach recording + §7 exclusion in the checkpoint (enforceable for the first
time), and a correctness bug where `_cycle_book` stopped processing fills at the
conservative close — which would have thrown away the rest of a resting quote's
round, i.e. exactly where Phase 2's edge comes from. Detector crontabbed on the
droplet. **No parameter changed** — band, offset, window, caps and series verified
byte-identical.

### Task 7 — fee fixture · **FIXED, and a fifth drift found**

`_SERIES_MAKER_FEE` is deleted. Fee classification is now a generated fixture
from one `GET /series` call (12,199 series, 130 charging), matched **exactly**,
which kills the prefix trap outright. Drift detection via
`scripts/check_fee_fixture.py`; a network failure is a **failure, never a skip**.

**Fifth drift:** `KXCHAMPTOUR`, `KXLIVTOUR`, `KXLPGATOUR` were hand-marked as
charging and none of them does — running the *opposite* way from the previous
four. A third copy of the same wrong data in
`golf_research/backtest/golf_fees.py` was deleted.

**Backwards audit: no committed verdict's sign or gate outcome changes.** But
the reason is uncomfortable: every maker study had already bypassed the code by
hard-coding a fee it verified live. **P-023c's +0.2¢ executable KILL sat well
inside the 0.44¢ phantom-fee band and was safe only because that report
distrusted the code.**

### Task 2 — gate throughput · **hypothesis refuted**

Instrument committed (`manager/throughput.py`, `check_throughput`, 13 tests):
per gate, observations 7d/28d, realised rate, projected resolution, and a stall
alarm keyed to each gate's own event cadence so silence is distinguishable from
patience. Unmeasurable renders as `None`, never as a projection.

Live output reads **`stalled=0, unprojectable=5`** — nothing has stalled and
almost nothing can be projected. That *is* the finding.

**P-001 is inert.** All-time admissibility 106/739 = 14.3%; since the matcher
fix deployed, **0 of 5** — and four of those five are **exactly 24.00h off**,
the original tie-break signature unchanged. Even at 14.3% and ~45
placements/week, 200 rows needs **~31 weeks (≈ Mar 2027)**, past the end of the
MLB season. The registry's "late Aug – early Sept 2026" is unachievable.

### Task 8 — data readiness · **MIXED**

* **P-018: ALIVE and AHEAD.** 27,307 genuine in-play ticks across 80 games in 7
  days; the harness's ≥10-game-day gate opens **~2026-07-30**, five days early.
  Coverage gap measured from the 581 DISCOVERY records: **33.9% dropped**,
  lowest-volume-first, so the replay sample is biased toward liquid markets and
  the backtest must say so. **The rate cannot be raised** — 128 cumulative 429s,
  48 in the last 24h, daemon self-throttling to 0.6–1.2 req/s. The limiter is
  the exchange, not the config.
* **MLB props: ALIVE, on time to the day, zero slack.** 5 complete clean ET
  game-days + 1 running; every clean day opens at exactly 10:00 ET and runs past
  23:40, so the West-Coast truncation is genuinely fixed. 27 clean days lands
  **2026-08-17 exactly** — one missed day slips it.
* **EV-Map Build 2: DEAD**, and not for the expected reason. Not "the Mac
  slept": **139 of 139 cron runs failed** with `Operation not permitted` — macOS
  TCC denying cron read access under `~/Desktop`. It fails awake too.
  `cron_archive.log` does not exist at all.

### Task 3 — golf template sweep · **KILL, both leads**

**The brief's own lead was killed by following the brief's own instruction** to
read every rule verbatim from the PDF. `GOLFH2H.pdf` does not say a tie pays
YES `$0`; it says **$0.50 to YES and $0.50 to NO**, with a worked example. So
`fair(A) + fair(B) = $1.00 exactly` and the 9.4% tie mass (51 of 541 settled
events) is symmetric and fully paid — no mechanic at all. Settlement confirms:
**102 of 102 scalar legs at `0.5000`**. `KXPGAH2H` has the best cadence in golf
(59 events/week vs P-022's 1.3), wasted on a non-mechanic.

The category-leader `$1/N` mechanic *is* real and verified, but three of its
four series have **never settled a market** and the fourth has **8 events from 2
tournaments, both majors** — n = 2 clustered, ~4 clusters/year, T = 14 in ~3.5
years. Every other untested family is under the fee bar or has ≤ 68 settled
rows. Phase 2 was not run because nothing cleared the ≥5¢ gate.

### Task 5 — widening · **PARTIAL, discipline intact**

Pre-declaration committed in its own earlier commit, before any extended data
was pulled — parameters frozen, staleness threshold (>6h) and refutation
condition (WEAKENED if added block ≤ 0 or pooled < +2.1¢/ct) declared in
advance. Horizon measured: **13/13 series reachable, overall earliest print
2026-05-22**; "~1 month" refuted. Items 2–6 not done.

---

## Deploy — **DONE 2026-07-27 17:53 UTC**

All three blocks shipped in one pass: the P-022 resolver, the fee fixture, and
the throughput instrument.

> **`deploy.sh` restarts only `betting-pod-shop`. P-022 runs as a separate
> unit** (`betting-round-leader-fade`) and had to be restarted explicitly —
> without that the fix ships to disk while the running process keeps the old
> code, which is exactly the failure mode this workstream has been fighting.

```bash
bash scripts/deploy.sh 129.212.176.202 restart
systemctl restart betting-round-leader-fade      # NOT covered by deploy.sh
```

Verified on the droplet after restart:

| check | result |
|---|---|
| droplet test suite | **1,593 pass, 1 skipped** |
| main engine health check | PASSED |
| units active | `betting-pod-shop`, `betting-round-leader-fade`, `betting-book-capture`, `betting-inplay-basis` (`betting-live-maker` inactive — P-016 v1, retired 2026-07-21) |
| `schedule` / `risk_guard` live | `GolfScheduleResolver` / `AggregateRiskGuard` with `reserve_trade` |
| band · offset · window · caps · series | `(0.03, 0.12)` · `+0.02` · `[12, 24]` · `0.5/5/15%` ($5/$50/$150) · 13 — **byte-identical** |
| markets discovered | **351, 0 unresolved** |
| detector | `WAITING`, **exit 0** (was `CLOSE_REF_PLACEHOLDER`, exit 1) |
| gate reader | `T=0, NO DECISION` — correct |
| fee drift check | fixture matches live Kalshi (+4 new free series, correctly not alarming) |

**Also on the droplet:** the window detector is crontabbed `*/30` as
`bettingbot`; previous crontab backed up to `/root/crontab.bak.2026-07-28`.

---

## Decisions needed from Sam — explicitly

1. ~~**Deploy P-022 before 2026-07-29T15:30Z.**~~ **DONE 2026-07-27 17:53 UTC.**
   The remaining question is only whether it quotes when the window opens.
2. ~~**P-015's gate reader**~~ **FIXED and DEPLOYED 2026-07-27 18:43 UTC**, on
   your instruction. Reads **n = 5, 4 wins, edge −11.20pp, z = −0.63**; verdict
   unmoved at NO DECISION (n ≪ 120, z well above the −2.0 hard kill). Not a rule
   change — the locked document names the *script*, not a log path, and the
   thresholds, statistic and VOID exclusion are asserted unchanged by test. A
   second latent bug went with it: a `or 0.9` fallback that fabricated a
   breakeven for any row missing a price.
3. ~~**P-014's missing reader**~~ **FIXED and DEPLOYED 2026-07-27 18:55 UTC.**
   Reads **331 of 500**, projects **2026-10-23** — the first gate in the fund's
   history with a real projected resolution date. **But it has no
   pre-registered decision rule**, so the reader counts and refuses to judge.
   **Write `P014_DECISION_RULE.md` before n reaches 500 — blind, without
   running `--unblind`** — or it is a rule fitted to results. ~12.5 weeks.
4. ~~**P-001's matcher.**~~ **CORRECTION: it was already fixed.** I reported it
   still broken on the strength of five placements made *within five minutes of
   the restart that loaded the fix* — provably not its output. Reproduced
   against the deployed matcher: it picks the right day in every order and
   rejects a wrong-day-only set at 1440 min vs a 720-min window. The one
   unambiguously post-fix MLB placement is admissible to **within one minute**.
   Nothing to fix; 11 regression tests and a placement-level leading indicator
   added instead. **The open question is now the post-fix admissibility rate,
   measured at n=1** — it decides whether P-001 resolves in ~4–5 weeks or ~31.
5. **`t_start_utc` for P-022** — currently records a period in which the pod
   could not trade. My read is unchanged: reset it to the first demonstrated
   quote and record why.
6. **Weather-suspended tournaments in T.** The resolver's error runs to +52h on
   them; quotes are still placed pre-round, so I would keep them. This is a rule
   question and should be written down *before* any of them settle.
7. **Posting above H = 24h.** The conservative calibration puts the first quote
   at a true H of ~25.6h (tee times) or ~29.6h (day offset). I did **not** tune
   it away — narrowing the pod's band to compensate is a §8.1 change that resets
   T to 0.
8. **P-018's code is orphaned** on `p018-inplay-fade-core` (1,688 lines, 29
   tests, absent from HEAD, the droplet and the suite) while its data gate opens
   **2026-07-30**. **Cherry-pick `4ff5bea`, do not merge** — that branch's tip
   also removes the Legacy Kalshi Arb Project that P-001's live scanner imports.
9. **EV-Map hosting.** Move to the droplet (recommended) or grant cron Full Disk
   Access. The 30-day clock has not started; earliest completion ~2026-08-27.
10. **`blocked_on: time` is now honest only for P-017 and P-022.** P-014 and
    P-015 are fixed and genuinely waiting on volume; **P-001 is still blocked on
    a defect, not calendar**, and its label should say so.
11. **Widening approval** (`STATUS_REASSESSMENT` §5.1) was never recorded. The
    pre-declaration is committed; the decision is still open.

---

## Anomalies and permanent data loss

**Permanent loss**

| loss | quantity | recoverable? |
|---|---|---|
| Weather paper quotes 2026-07-22 → 07-27 | 6 days, ~1,200 quotes/day | **No** — 90-day horizon, point-in-time | 
| Weekly settled-market archives | ≥2 cycles (07-19, 07-26) | Partially, inside the horizon |
| MLB props days 07-19/20/21 | 3 contaminated days | No, but not needed |
| P-018 in-play ticks | **none** | n/a |

Loss accrues at **1 day per day** until the weather hosting is fixed.

**Anomalies**

* **P-017's first settled tournament is −10.08¢/ct on 2,276 contracts** against
  a +6.8¢/ct backtest baseline. n=1, no verdict, and the gate stays locked — but
  it is the first forward evidence the fund has ever had on that pod and it is
  negative.
* **Kalshi settled-market caches are treacherous in two new ways**, both found
  building the resolver's ground truth: `min(close_time)` across an event picks
  up **withdrawals** (days early), and `mode(close_time)` picks up **the cut** on
  R3 events (roughly half the field closes at the R2 stamp, frequently the
  *larger* cluster). The round end is the last cluster of any real size.
* **148 Kalshi series tickers contain a hyphen** (`KXMLBWINS-MIL`), so the
  obvious `split("-")[0]` truncates them to a different series. 86 have a
  leading segment that is also a series; zero currently disagree on fee type,
  and that is now asserted as a property.
* **A correction I made mid-run:** my first P-001 admissibility pass hand-rolled
  a ticker parser reading the encoded time as UTC (Kalshi MLB tickers are **ET**)
  and produced a spurious 0.3%. The 14.3% figure comes from the checkpoint's own
  `ticker_start()`. Reimplementing a sanctioned reader is how you get a second,
  subtly different number for a quantity meant to be unambiguous.

---

### One line

The blocker is closed and P-022 will quote on 2026-07-29T15:30Z **if it is
deployed first** — and the day's larger finding is that the fund's constraint
was never hypothesis supply or observation supply, but that four of its five
gates were not measuring anything.
