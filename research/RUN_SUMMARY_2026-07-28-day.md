# Run Summary — Tuesday 2026-07-28, DAYTIME

> ## Is P-022 inside its §7 cap, and does it quote off two-sided books?
>
> **Inside the cap: YES, now — it was not this morning, and the breach was
> nearly twice what the pre-flight measured.** At AIGWO26 R1's real window-open
> instant the pod would have placed **24 quotes carrying $111.65** against a
> $50 (5%) per-tournament limit — 2.23×, not the reported 13 quotes / $60.45 —
> with **no breach recorded**, because sizing subtracted filled collateral only
> and nothing had filled. Fixed, deployed, and verified from the running
> process: **11 quotes, $49.47 ≤ $50.00.**
>
> **Two-sided books: NO, and this is the stop-the-line finding of the day.**
> **143 of the 146 markets in AIGWO26 R1 carry no resting YES bid at any
> price.** 23 of the 24 in-band candidates are priced off a bare ask through
> `_mid()`'s one-sided branch. And the validated backtest **never once priced
> off a one-sided ask** — `quirks_common.candle_price` rejects one outright and
> says why in its own docstring: *"illiquid golf props park phantom asks … and
> using them fabricates edge."*
>
> **Sam's call, before `2026-07-29T18:32Z`.** It is narrower than it sounds
> (§Task 2) but it is a real change of claim.

> ### ⏰ The queue's deadline is wrong, in our favour
> **ESPN published tee times during this run.** Five of seven events upgraded
> off `tour_day_offset` and every window moved **~3–4 h later** — exactly the
> 3–5 h the pre-flight predicted. **AIGWO26 R1 opens `18:32Z`, not `15:30Z`;
> ROC26 R1 opens `22:22Z`.** Live `close_source` across all 958 books:
> `tee_times` 586 · `r1_tee_anchor` 293 · `tour_day_offset` **79** — and **the
> 79 are exactly POI26**, the one event whose lag constant is uncalibrated. The
> upgrade fired for the two events that did not need it.

---

## Per-task verdicts

| # | Task | Verdict | Headline number |
|---|---|---|---|
| **1** | P-022 §7 quoted collateral | **FIXED + DEPLOYED** | **24 quotes / $111.65 → 11 / $49.47** against a $50 cap |
| **2** | One-sided books | **STOP-THE-LINE** | backtest priced off a one-sided ask **0 of 364 times**; live **23 of 24** |
| **3** | POI26 close reference | **EXCLUDED, pre-registered** | only event still on `tour_day_offset`; **no tee sheet exists anywhere** |
| **4** | P-018 rule + gate #1 | **KILL** | placebo **+19.19 ¢** vs fade **+9.09 ¢** — surprise adds nothing |
| **5** | P-017 decision rule | **WRITTEN** | **P(a tournament ≤ −9.89 ¢) = 0.0082, ≈1 in 122**; T = 8 kept |
| **6** | P-016 reader or retire | **RETIRED properly** | gate had already **resolved**; standard 15 → **12** failing checks |
| **7** | Ops backlog | **3 of 4 DONE, 1 needs Sam** | archiver **1.66 GB → ~600 MB peak**, 3× better, not solved |
| **8** | P-001 placement rate | **EXPLAINED** | candidates **rose** 60→169→301; CLV gate rejects **89.7%** |

**Suite: 1,751 passed, 2 skipped** (from 1,723 at session start; **+28**).
**9 commits, all on `origin/main`**, verified from an independent clone.

**Reports:** [P-022 quoted collateral](REPORT_P022_Quoted_Collateral_2026-07-28.md) ·
[P-022 one-sided](REPORT_P022_OneSided_2026-07-28.md) ·
[POI26 pre-registration](../golf_quirks_research/P022_POI26_PREREGISTRATION_2026-07-28.md) ·
[P-018 gate #1 redesign](P018_GATE1_REDESIGN.md) ·
[P-018 rule](P018_DECISION_RULE.md) · [P-018 result](REPORT_P018_Gate1_2026-07-28.md) ·
[P-017 rule](P017_DECISION_RULE.md) · [P-016 status](REPORT_P016_Status_2026-07-28.md) ·
[P-001 placement rate](REPORT_P001_Placement_Rate_2026-07-28.md)

---

## The three findings that change what someone would do tomorrow

### 1. P-022's live population is not the population the edge was measured on

The nuance matters in both directions. One-sided books are **half the
backtest** (181 of 364 posted markets) — the harness used those markets, it
just took their price from a **trade print**, never from the ask. That class
returns **+3.90 ¢ [+0.33, +7.19]**, indistinguishable from two-sided
**+4.47 ¢ [+1.69, +7.27]**.

The genuinely untested class is narrower **and is the live one**: no trade in
the bar, no bid, priced off the ask. 117 of 364 markets showed that at T, and
they return **−1.48 ¢ [−11.93, +8.15] on a 15% fill rate** against 67% for
two-sided — the weakest cell in the table, worth 441 of 4,061 contracts.
Under-powered, **not** refuted. But the fill rate is the shape P-017A died on.

**Second, independent gap:** on that class the harness walks *back* to an
earlier clean candle — median 0.98 h, **p90 40 h, max 249 h** — so it quotes a
stale trade price where the pod quotes today's ask. Neither is the other.

**Depth, recorded not fixed:** the pod screens on neither size nor depth. Size
resting **ahead of** the quote is bimodal — median 13, max 1,122 — so 9 of 24
names need an ~805-contract sweep to fill a 5-lot.

### 2. P-018's +9.09 ¢ is dead, and the original gate could never have killed it

Pre-registered in `c1daf00` **before the harness existed**. Two findings needed
no outcome at all:

* **Gate #1 as specified could never return KILL, and populating the empty
  low-surprise buckets would not have fixed it.** `_fade_spec` rests at
  `px = mid + 0.5·(fair − mid)`, so the model's own expected edge is exactly
  `0.5·|fair − mid|`, and at event time `fair − mid ≈ underreaction`, which
  grows with surprise. **The bucket table is obliged to slope upward whenever
  the model is roughly right — arithmetic, not evidence.**
* The committed backtest **clusters on the wrong unit**: 240 tickers are
  **124 games**, and 116 games contribute two anti-correlated tickers.

Then the tests ran:

| test | result |
|---|---|
| **1R-A placebo** | fade **+9.09 ¢** vs placebo **+19.19 ¢** — **211%**. Paired difference **−10.09 ¢ [−15.83, +15.54]**. → **KILL** |
| **1R-C dose-response, quote distance fixed** | flat or negative in all three strata; with low buckets populated it runs **backwards** (+39.01 ¢ at surprise 0–0.02 vs +28.14 ¢ at 0.12+) → **KILL** |
| **1R-B decomposition** (cannot kill) | realized +9.09 ¢ vs model-predicted **+12.34 ¢**, residual **−3.25 ¢ [−6.50, +0.04]** |

**Two thirds of all fills earn at or below zero.** The entire headline is the
tercile where model and market disagree by **≥ 23 cents** — which is more
likely a 60-second capture cadence and a first-tick pregame anchor than alpha.
**Maker/fade is now 0 for 6.**

### 3. P-001's decline is Sam's own CLV gate, and the good-news explanation is wrong

**Opportunities did not fall — they nearly doubled.** Candidates clearing the
edge threshold: **131 → 60 → 169 → 301**. Placements: **131 → 60 → 60 → 31**.
`SKIPPED_CLV_GATE`, added 2026-07-18, rejects **64.5% → 89.7%**.

**All 1,432 rejections are the same reason: `price ≥ $0.50`.** The gate is a
pure underdog price filter; `min_net_edge` has **never once bound**.

**The matcher fix contributes zero** — the exactly-24.00h fingerprint appears
**4 times in 3,369 all-time PLACED rows and the last was 2026-04-27**. The 152
in the 07-29 report were not placements. The prompt called this the most likely
and only good-news explanation; it is not the explanation.

**The gate still resolves this season at every observed rate**:
**2026-08-18 / 08-23 / 09-05**, and **2026-09-11** at the 31/wk measured here.
**The risk is the trend, not the level** — one more step to ~95% rejection
lands 2026-10-28, outside the MLB season.

---

## Deploy — both units, and the proof the running processes took it

```bash
bash scripts/deploy.sh 129.212.176.202                    # sync only
ssh root@129.212.176.202 systemctl restart betting-round-leader-fade
```

| unit | state | started | restarted by this run? |
|---|---|---|---|
| **`betting-round-leader-fade`** (P-022) | **active** | `2026-07-28 17:38:42Z` | **YES — twice**, once per code change. `deploy.sh` does NOT cover it. |
| **`betting-pod-shop`** (5-min engine) | **active** | `2026-07-28 15:57:03Z` | **NO** — see the anomaly below |

**`betting-pod-shop` was deliberately not restarted.** P-022 is not in
`pods.active`, runs as its own unit, and an unnecessary restart carries the
known log-rotation / orphaned-position hazard. The P-001 and P-016 work is
read-only or registry-only. `scripts/p022_window_check.py` is cron-invoked and
picks up on its next `8,23,38,53` run.

**A file on disk is not a deployed fix.** From the running P-022 process:

| check | evidence |
|---|---|
| file identity | local `sha256 6cbdd67e3972…` = droplet `6cbdd67e3972…` |
| ordering | file mtime **<** `ExecMainStartTimestamp` on both restarts |
| fix present by source inspection | `_cycle_book` contains `tournament_exposure` ✓ and `own_quote_coll` ✓; `cycle` contains `key=lambda b: b.ticker` ✓; `check_caps` contains `b.exposure` ✓; `_mid` records `book_side` ✓; the QUOTE row carries it ✓ |
| new API live | `MarketBook.exposure`, `.quoted_collateral`, `Engine.tournament_exposure`, `.total_exposure` |
| params live, read from the merged config | band `(0.03, 0.12)` · offset `0.02` · window `[12.0, 24.0]` · caps `$5 / $50 / $150` · pct `0.005 / 0.05 / 0.15` · maxct `25` · **13 series** — byte-identical |
| wiring live | `schedule = GolfScheduleResolver`, `risk_guard = AggregateRiskGuard` |
| markets live | **958 discovered, 958 books** |
| restart recovery | `rebuild_from_log()` → 0 unsettled fills (correct — nothing has ever filled); `tournament_exposure("AIGWO26") = 0.0` |
| service log | `17:38 starting (paper)` → `discovered 958 new round-leader markets` |

**Not deployed:** P-018 (inert by design). **Nothing enabled, no trading
behaviour changed anywhere.**

---

## Ops backlog — 3 of 4 done

**1. Push — DONE.** `521b6b8..7cf9aee main -> main`, then `c0accbe`.
**Verified from a fresh independent clone**, not the local tracking ref:
tip SHA identical, **147 commits**, and all ten of today's artifacts present.
Topology reported before touching anything: **9 ahead, 0 behind — a clean
fast-forward**, `git merge-base --is-ancestor` confirmed. One merge commit
(`2380aff`, *"Merge branch 'main' into claude/admiring-cohen-eeca92"*) sits in
that history; it was not rewritten. **`p024-mlb-f5-research` no longer exists
locally**; the branch audit's other nine unique-commit branches are untouched.
**No branch deleted, no history rewritten, no force push.**

`.claude/settings.local.json` **is tracked** and carries three new permission
entries. It is a per-machine permission allowlist and arguably should be
gitignored — but it has been tracked since before this session and untracking
it is a repo-hygiene decision, not mine to make mid-run. **Left as is,
uncommitted**, flagged for Sam.

**2. Mac crontab — NEEDS SAM AT A TERMINAL.** Attempted once,
non-interactively, with the replacement file prepared and both backups taken.
**`crontab <file>` hung again**, exactly as the prompt predicted — the same
macOS permission gate that produced the original 139 failures. **Not retried.**
The five `kalshi-ev-map` lines are **still active**.

> **Sam: run this in Terminal.app.** No race exists today (TCC keeps all five
> inert) but it must land before any Full Disk Access grant.
> ```bash
> crontab ~/mac_crontab.disabled.2026-07-28
> ```
> Backups: `~/mac_crontab.live.2026-07-28.bak` (verbatim current) and
> `~/mac_crontab.bak.2026-07-29`.

**3. EV-Map archiver — FIXED, and the capacity finding is the bigger one.**

The 07-29 chunking was a real change that **could not have worked**: `parts`
accumulated every chunk, so peak was still the whole filtered frame plus a copy
for the final `concat`. **The chunk loop moved where the memory was spent, not
how much.** Now each chunk is streamed to a `ParquetWriter` as a row group and
released, and the existing archive is merged back via `iter_batches` — both
bounded, and exactly equivalent to `drop_duplicates(keep="last")`.

**Seeding `archive_state.json` was evaluated and rejected**: the timer is
**weekly**, so a window shorter than 7 days drops settled markets permanently —
it would have traded an OOM for silent data loss — and there is no archive to
seed from in the first place.

Measured on the droplet under a **600 MB hard cap with swap disabled**, so it
could not threaten P-022:

| | before | after |
|---|---|---|
| peak RSS | **1.66 GB** → OOM-killed at 433 s | **487 → 599 MB**, see below |
| progress | died at 433 s | **1.82 M markets scanned, 300 k kept** and still going at 15 min |

**Correction to my own claim, made before publishing it: the memory is NOT
flat.** I first recorded "487 MB and flat"; watched longer it climbed
487 → 519 → 554 → 599 MB against the 600 MB cap. The growth tracks kept rows,
so the residual leak is the `new_tickers` de-duplication set (300 k+ ticker
strings) plus per-chunk `isin` temporaries — **bounded by the archive's own
size, not by the window**, but not zero.

**This is a 3× improvement, not a solved problem.** It completes far more work
in far less memory, but a first run over ~2 M settled markets may still reach
the cap. The next iteration is to replace the in-memory ticker set with a
dedup pass over the written parquet. **Recorded as unfinished rather than
claimed as fixed.**

**The droplet's real headroom, which the prompt correctly said is worth more:**

```
total 1968 MB   used ~1000-1150   available ~820-1010   swap 2 GB (256 MB used)
```

Six workloads. The two largest are **P-022's runner (313 MB, 15.5%)** and the
**5-minute engine (309 MB, 15.3%)**. **Exactly one OOM kill in 14 days** — the
archiver, 2026-07-28 03:14. Disk is fine (26% of 67 GB).

> **The risk was never the archiver — it is that the OOM killer scores by RSS,
> so the most likely victim of the next memory event is P-022's runner**, the
> single largest process on the box, the night before its first window. That is
> why this run was capped at 600 MB with swap disabled rather than run free:
> under a cgroup cap the archiver can only kill itself. **Nothing with a
> deadline is at risk today**, with ~1 GB available and 1.75 GB of swap free —
> **and the archiver must keep running under a cap until its memory is
> genuinely bounded.**

**The timer is still DISABLED.** It is weekly (Sunday), so enabling it costs
nothing before the P-022 window — but I am not enabling a job that has never
completed a single run. Enable it once this run finishes clean.

**4. Cadence — corrected in all three places.**
`manager/throughput.py`'s `STALL_DAYS["P-022"]`: `14.0 → 21.0` days, because
14 was justified by "4–5 golf tournaments/week" against a measured **3.16 event
codes/week (13.7/month)** — 42% too fast, and a stall alarm keyed to a cadence
that fast fires on healthy silence. `14 × (4.5/3.16) = 19.9 → 21`, matching
P-017's tolerance for the same reason.
`P022_DECISION_RULE.md` §5 and §12 corrected **visibly, with strikethrough**,
not silently rewritten. **Re-derived: T = 14 lands ~4.4 weeks from the FIRST
QUOTE** — which is tomorrow, not from T-start, because the pod could not quote
until now. **T = 24 lands ~7.6 weeks ≈ 2026-09-20**, one tournament less after
POI26's exclusion.

---

## Decisions needed from Sam

**New today — the two that are time-critical**

1. **🛑 QUOTE OR DON'T, before `2026-07-29T18:32Z`.** P-022's edge was measured
   on traded and two-sided references; tomorrow's quotes are priced off bare
   asks, a class worth 10.9% of measured contracts at **−1.48 ¢
   [−11.93, +8.15]** and a **15% fill rate**. *My recommendation: quote anyway,
   and treat the first tournaments as measuring that class rather than
   confirming the headline* — the gate has zero observations, paper costs only
   time, and the cell is under-powered rather than refuted. **But it is a real
   change of claim and the call is yours.**
2. **The "two-sided quotes only" rule needs a scoped, written exception for
   this family — or P-022 must stop.** It cannot silently not apply. Note it
   was never binding on the *backtest*; the harness enforced something
   stricter. What is needed is an exception for the **pod**.

**New today — not time-critical**

3. **Accept or amend `P017_DECISION_RULE.md`.** NOT LOCKED. Every threshold
   carries a provenance line; none is derived from the −9.89 ¢. **T = 8 is kept
   and was not lowered** — the power re-solve says T = 3 suffices for the
   backtest effect, so 8 is already ~3× conservative.
4. **P-017 has caps but NO cap-breach exclusion clause and no breach
   recording**, where P-022 §7 has both. **Deliberately not written** — adding
   one after the first tournament settled negative would be indistinguishable
   from an escape hatch.
5. **Accept `P018_DECISION_RULE.md`** (NOT LOCKED, with a disclosed exposure)
   and **record P-018's gate #1 as KILL.** Do not re-parameterise.
6. **`.claude/settings.local.json`** — tracked in the repo; decide whether it
   should be.
7. **Delete the four `.bak_*` trade-log copies** in the droplet's
   `data/trade_logs/`, or move them out. They match the log glob and inverted
   the P-001 analysis once already. **Not deleted by me.**
8. **A depth screen for P-022** would be an §8.1 change. The bimodal 12-vs-800
   split says the quoted population contains two very different fill regimes.
9. **`STALL_DAYS` for other pods may carry the same error** — only P-022's was
   in scope today.

**Carried forward from `RUN_SUMMARY_2026-07-29.md`**

| was # | item | status today |
|---|---|---|
| **2** | Accept or re-derive `P014_DECISION_RULE.md` given the disclosed contamination | **STILL OPEN** — untouched today |
| **3** | POI26's close reference has no verified margin | **ADDRESSED** — exclusion from T pre-registered before the window; confirm or override |
| **5** | Disable the Mac crontab | **STILL OPEN — needs you at a terminal**, attempted and hung |
| **6** | How to unblock the EV-Map archiver | **CLOSED** — streaming parquet writes; 1.66 GB → 554 MB |
| **7** | P-017 has no decision rule | **CLOSED** — written; acceptance is item 3 above |
| **10** | `t_start_utc` for P-022, and it is not implemented as a filter anywhere | **STILL OPEN** |
| **11** | Weather-suspended tournaments in T | **STILL OPEN** — EXCLUDE costs the backtest's five best tournaments |
| **12** | Posting above H = 24 h is out-of-sample, not merely conservative | **STILL OPEN** |
| **13** | Widening approval (`STATUS_REASSESSMENT` §5.1) never recorded | **STILL OPEN** |

Also closed today, from that list's item 8: **P-016 has no reader and an
unresolvable `source: maker_fills`** — resolved as a *closure*, not a reader.

---

## Anomalies

* **The P-022 breach was 2.23×, not the 1.2× reported**, and it grows as books
  fill in toward the round. A day's delay would have made it larger again.
* **`betting-pod-shop` restarted at `2026-07-28 15:56:53Z`** — a clean systemd
  stop/start (`Deactivated successfully`, no failure, 546 MB peak, 78 MB swap),
  **about an hour before this session's first deploy**. Not done by this run
  and not explained by it. Recorded with the evidence rather than guessed at.
* **A glob that swept up copies of its own directory inverted an answer.**
  `trade_log*.jsonl*` matches four `.bak_*` snapshots; counted raw, P-001's
  last week **rose** (196), deduplicated it **halved** (31). Caught by the
  numbers disagreeing between two of my own runs, not by review. Same family as
  the log-rotation orphan bug.
* **My own §2 prediction in `P018_DECISION_RULE.md` was wrong and is corrected
  in place, visibly.** I predicted correct game clustering would *widen* the CI
  by 1.39×; it *narrowed* it by 7%. The two tickers of a game are
  **anti-correlated**, so pooling them reduces between-cluster variance. The
  unit correction stands; the variance reasoning did not. It cuts in favour of
  the headline, which is killed on other grounds anyway.
* **A pod with no gate block was passing the instrumentation standard by
  absence.** P-018 never appeared in the checker's output in any form. Fixed —
  gate count in the checker rose 6 → **11**.
* **`src/kalshi_fees.py`'s dependency note was about to authorise a change it
  meant to forbid.** It named P-016 as the reason the general maker fallback
  must not be perturbed; P-016's sample is no longer running, so read literally
  it now permits the change. **The dependency moved rather than ended** —
  `clv_settlement.py` calls it bare to compute `clv_net_maker`, which carries
  **P-001's live CLV gate**. Corrected.
* **`manager.checks` reports `P-001 / P-014 / P-015 / P-017 is in pods.active
  but has no registry entry`** — the check looks for a `pods:` key while the
  entries live under `workstreams:`. **Pre-existing, not caused today,
  reported not fixed.**
* **P-022's `min_net_edge`-equivalent second clause on P-001's CLV gate has
  never bound** in 1,432 rejections. A two-condition filter behaving as a
  one-condition filter.

## Permanent data loss

**None today, and nothing was deleted.**

| earlier loss | status |
|---|---|
| Weather paper quotes 2026-07-21 → 07-27 (7 days, ~1,200/day) | unchanged — not recoverable |
| Weekly settled-market archive | **now being created for the first time** — 254 k rows and counting |
| P-016's 3,700 maker rows | **retained deliberately**; the gate was closed, the history was not touched |

---

### One line

**The day's work was mostly the fund catching up with decisions it had already
made and not noticed** — P-001 is slow because Sam narrowed it on 07-18, P-016
was never broken because its gate had already answered, and P-022 was over its
cap because a resting quote was free in one ledger and at-risk in another —
while the one genuinely new result, **P-018's +9.09 ¢, died to a placebo that
earned twice as much doing nothing.**
