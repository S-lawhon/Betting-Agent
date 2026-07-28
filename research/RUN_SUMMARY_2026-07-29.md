# Run Summary — 2026-07-29

> ## Is P-022 armed to quote at 15:30Z, and what will tell us within 30 minutes if it is not?
>
> **YES — armed.** Driven at an injected wall-clock of `2026-07-29T15:31:00Z`,
> the pod's own `discover() → cycle()` path **places 13 quotes** on
> `KXLPGAR1LEAD-AIGWO26` (sell YES at 7¢ against a 5¢ reference, 5 contracts
> each, $60.45 worst-case collateral). This is the first time this workstream
> has demonstrated the path from *window open* to *order would be placed*
> rather than inferring it from a state check.
>
> **If it is not, `WINDOW_OPEN_CANDIDATE_NO_QUOTE` pages within ~23 minutes of
> the window opening** — detector at `8,23,38,53`, collector and alerter at
> `*/15`, `critical` severity so `manager/alert.py` sends immediately with no
> confirmation window. Rehearsed against live market data at the real
> window-open instant: it stays quiet at +1 min (grace) and fires at +31 min,
> naming the same 13 tickers.
>
> **One correction to the queue's framing, and it matters for the watch:**
> 15:30Z is the window-open instant *only while the coarse `tour_day_offset`
> path is in force*. All seven listed events use it today because ESPN
> publishes no tee times three days out; the runner re-resolves every 900 s,
> and when pairings publish the close moves **later** by an estimated 3–5 h.
> **Do not read a quiet 15:30Z as a failure without checking `close_source`
> first.**

---

## Per-task verdicts

| # | Task | Verdict | Headline number |
|---|---|---|---|
| **1** | P-022 pre-flight + silent-failure alarm | **PASS + FIXED** | **13 quotes would be placed**; alarm rehearsed live, detection ≤ 23 min |
| **2** | P-022 rule decisions, written blind | **DONE** (decides nothing) | excluding weather takes the backtest **+3.80 → +2.86 ¢/ct, z 3.29 → 1.96** |
| **3** | Cherry-pick P-018 `4ff5bea` | **FIXED** | +29 tests, **P-001 asserted intact**, pod inert on four counts |
| **4** | EV-Map → droplet | **FIXED (4 of 5) + BLOCKED (1)** | clock starts **2026-07-28T03:11Z → 2026-08-27**; archiver OOM |
| **5** | P-014 decision rule, blind | **DONE — with one disclosed contamination** | n = 500 rows ≈ **123 game clusters**, detecting ~6–12 ¢/ct |
| **6** | P-001 post-fix admissibility | **MEASURED — LIVE GATE** | **3 of 3**, worst error 7 min; **152 → 0** on the tie-break fingerprint |
| **7** | Branch consolidation + push | **BLOCKED** | 77 commits still in one place — **the push was denied, see §Task 7** |
| **8** | Gate instrumentation standard | **BUILT** | re-detects P-014 and P-015 by their exact signatures; **found P-016** |

**Suite: 1,723 passed, 2 skipped** (from 1,665 at session start; +58).

**Reports:** [P-022 pre-flight](REPORT_P022_PreFlight_2026-07-29.md) ·
[P-022 rule decisions](P022_RULE_DECISIONS_2026-07-29.md) ·
[P-018 cherry-pick](REPORT_P018_Cherrypick_2026-07-29.md) ·
[EV-Map hosting](REPORT_EVMap_Hosting_2026-07-29.md) ·
[P-014 decision rule](P014_DECISION_RULE.md) ·
[P-001 admissibility](REPORT_P001_Admissibility_2026-07-29.md) ·
[gate standard](REPORT_Gate_Standard_2026-07-29.md) ·
[the standard itself](../docs/GATE_INSTRUMENTATION_STANDARD.md)

---

## ⚠️ Two things to read before anything else

### 1. A blindness contamination in Task 5, disclosed in full

While counting P-014's composition I aggregated the **`action`** field without
realising it encodes the outcome, and the output printed
`('LOSS', 99), ('WIN', 96)`. **I have seen a raw win/loss count.**

`--unblind` was never run; I saw no price, no P&L, no edge, no CLV. The count
is not the gate statistic, is not the sanctioned reader's population (my view
deduplicated differently and covered 195 terminal rows against the reader's
331), and carries almost no information about a fee-net game-clustered edge on
a pod that takes both sides at unknown prices. **But it is an outcome.**

The defence is mechanical and checkable: **every threshold in the rule is
either inherited verbatim from the locked P-015/P-022 documents (`z ≥ 2.0`
PASS, `z ≤ −2.0` hard kill) or derived from an outcome-independent
measurement.** Removing the contaminated observation changes nothing.
`P014_DECISION_RULE.md` is **NOT LOCKED**; its §9 asks Sam to accept it as
written or have the thresholds re-derived by a session that has seen nothing.

### 2. The push was denied — the 77 commits are still in one place

`git push -u origin p024-mlb-f5-research` was **blocked by the permission
layer**, not by git. Everything below therefore still exists only on this
laptop. This was the whole point of Task 7 and it is the one thing tonight did
not fix. **One command, §Task 7.**

---

## Task 1 — P-022 pre-flight · **ARMED**

**Part A.** Seven events now listed (957 markets), up from three (351):
R1/R2/R3 for AIGWO26 and ROC26, plus POI26 R1. All resolve via
`tour_day_offset` — ESPN's leaderboard returns zero competitors for all three
tournaments.

| event | resolver | window OPENS |
|---|---|---|
| `KXLPGAR1LEAD-AIGWO26` | `2026-07-30T15:30Z` | **`2026-07-29T15:30Z`** |
| `KXPGAR1LEAD-ROC26` | `2026-07-30T18:30Z` | `2026-07-29T18:30Z` |
| `KXCHAMPTOURR1LEAD-POI26` | `2026-07-31T16:00Z` | `2026-07-30T16:00Z` |

Signed error against independently published schedules:

* **AIGWO26 R1 — EARLY by ≥3.5 h. SAFE.** Royal Lytham & St Annes; Sky Sports
  has R1 coverage from 13:00 BST with highlights at 23:00, on a 144-player
  major.
* **ROC26 R1 — EARLY by ~4 h. SAFE.** Detroit GC; Racing Post gives an 11:45
  BST (= 06:45 EDT) first tee, and a two-tee field cannot finish in the 7 h 45
  min to the predicted close.
* **POI26 R1 — NOT VERIFIABLE, and the sign is not guaranteed. FLAG.** No tee
  sheet exists anywhere. The only published timing fact is the tournament's own
  *gates close 18:00 local*, which bounds the round end at ≤17:00Z but gives no
  lower bound. `LAG_DAY_H["KXCHAMPTOUR"] = 12.0` is calibrated on **n = 1**,
  a **US** event, and this is the first PGA Tour Champions event ever staged in
  Europe — a different clock frame entirely. Reported, not fixed.

**Part B funnel** at 15:31Z: 957 listed → 957 resolved → **144 in window** →
144 priced → **13 in band** → **13 would place**.

Three things the funnel surfaced that no prior report contains:

1. **Every in-band reference is a ONE-SIDED ask** (`yes_bid: null`,
   `yes_ask: 0.05`, `ask_qty: 10`). `_mid()`'s one-sided branch drives *every*
   placement in the first tournament — the pod is not quoting off a two-sided
   mid at all.
2. The 131 "above band" names are junk books (`bid 0.01 / ask 0.97`), correctly
   excluded.
3. **The pod has no size or depth screen.** The prompt's funnel includes one;
   it does not exist in the code.

**Part C** — eight detector states replacing three, the funnel logged every
run, a checker failure treated as a failure, per-ticker/per-window quote
attribution, a 10-minute grace, and routing into `manager/alert.py` at
`critical`. **25 tests.** The detector duplicates the pod's post-band screens
on purpose and a test drives a real engine over six states to pin the
duplication.

**No P-022 parameter changed** — verified byte-identical, `git diff` empty
against the pod, the resolver and both configs.

---

## Task 2 — P-022 rule decisions · **written before anything settles**

Four questions, each with alternatives, consequences and an unanswered line
for Sam. Four findings came out of writing it:

* **A weather-suspension criterion that is a finding, not a fit.**
  `lag_h > 12h` (resolver prediction vs Kalshi's rewritten close) and
  `round_span > 24h` (ESPN tee times) use **disjoint inputs** and select the
  **same 8 of 72** event-rounds, with an empty gap around each threshold. But
  excluding those 6 competitions removes the backtest's five **best**
  tournaments (mean +6.42 ¢): **+3.80 → +2.86 ¢/ct, z 3.29 → 1.96**, and the
  widened pooled sample goes to **exactly zero**. EXCLUDE is procedurally
  conservative and numerically unfavourable.
* **The Phase-2 grid tops out at H = 24.0 h.** The resolver is one-sided early
  on 72 of 72, so the region the pod actually posts in is **entirely
  out-of-sample** — a materially weaker claim than "conservatively early
  within a validated band". On today's coarse path, **47.9%** of the nominal
  window lies above 24 h true.
* **The backtest and the sanctioned reader compute different statistics.**
  `bootstrap_weighted` is contract-weighted across tournaments; §2 and
  `p022_checkpoint.evaluate` weight tournaments equally. Under the reader's own
  statistic **T = 14 has 79% power against the original effect, not 91%.**
* **Cadence checked; neither prior figure is right.** **3.16 golf event codes
  per week** measured across all 13 series. §5's 15–19/month is ~25% high;
  P-028's 1.3/week is `KXPGAR1LEAD` alone, which reproduces at 1.35.

`t_start_utc` is also **not implemented as a filter anywhere** — it is
documentation about the gate, not a term of it.

---

## Task 3 — P-018 cherry-pick · **FIXED, pod inert**

The trap is real and the mechanism is more specific than "the branch tip
removes it": `3fd3e10` deletes 41 files under `Legacy/Kalshi Arb Project/`,
and `src/engine.py:37` puts exactly that directory on `sys.path` so line 152
can `from scanner import Scanner`. Merging would send `build_shared_deps` down
its *"legacy scanner not available; P-001 skipped"* path — **disabling the
fund's highest-volume pod with a log line, not an error.** The suite would not
have caught it either: `tests/test_matcher_wrong_day.py` **skips** when the
Legacy sources are absent, so the deletion turns those tests green-by-skip.

One conflict (`config_multi_pod.yaml`, where `4ff5bea` appends P-018 at the
point HEAD now carries P-022); both sides kept and **P-022's gate-condition
block is byte-identical afterwards**. Suite +29, exactly P-018's own tests.
P-001 asserted directly. Inert on four independent counts, confirmed
empirically — `discover_pods()` yields no P-018. Registered
`blocked_on: backtest` with no gate block, so throughput does not project a
date for a pod with no decision rule.

---

## Task 4 — EV-Map hosting · **4 of 5 live, archiver BLOCKED**

Two corrections to the brief:

* **The weekly archiver failed a *different* way.** `cron_archive.log` does not
  exist at all, while the other logs are full of TCC errors — so that line
  never executed. Sunday 03:17 local, and cron does not run jobs missed while
  asleep. Two failure modes, one host, one fix.
* **The archiver has never succeeded anywhere, ever.** `settled_archive.parquet`
  and `archive_state.json` have never existed. There is no archive to have
  gaps in. The dead window is also a day longer than recorded: the pipeline
  last produced output **2026-07-20**.

Five systemd timers, scheduled in **America/Chicago** so the weather markets'
local-day semantics are unchanged, `Persistent=true`. Every run goes through
`scripts/evmap_job.py`, which records **rows actually written** — exit 0 with
zero rows is a FAILURE, which is the half of the 139 an exit code could never
have seen. Verified by data:

```
weather_sheet +114   weather_depth +120   paper_maker +114 intents
paper_eval +0 (correct: 07-27 has no quotes to evaluate)
```

**The 30-day clock starts `2026-07-28T03:11Z` → `2026-08-27`.** Days are
counted from the maker's own rows, not calendar — calendar time is exactly
what accrued through 139 failed runs.

**`archive_settled` is BLOCKED and its timer DISABLED.** OOM-killed at 1.66 GB
RSS on a 2 GB box; chunked accumulation (row-wise equivalent, no methodology
change) got further but was still climbing past 1.4 GB at 35 minutes with
95 MB free. I stopped it rather than let the OOM killer choose between it and
`betting-round-leader-fade` the night before P-022's first window.

Two bugs in my own alarm, found by running it and now pinned by tests: a
per-day output cannot be measured as an after-minus-before delta, and a 1 s
freshness slack let yesterday's file rescue a run that wrote nothing.

---

## Task 6 — P-001 · **LIVE GATE, not inert**

The decisive number is not the three admissible rows. It is the fingerprint:
**152 exactly-24.00h rows all time, the last placed `2026-07-26 21:26:45Z` —
four minutes and 47 seconds before the fixed process started at `21:31:32Z` —
and zero since.** A failure mode that stops dead at the deploy instant is
stronger evidence than a small success count.

Three cutoffs computed (the reader's `21:30:00Z`, the fixed process's own
start, the last restart of the sequence); **all three give the same answer**.
3 of 3 admissible, deltas of **1, 1 and 7 minutes**. Exact one-sided 95%
binomial bound on 3/3 is **≥36.8%**, which excludes the all-time 14.7% — weak
evidence, stated as weak. The clustered bootstrap has 2 clusters and its
`[100%, 100%]` is degenerate; not quoted.

The gate reads 0 of 200 because **`post_epoch = 0`**: all 654 CLV rows predate
the epoch and settlement lags placement by about a day. Nothing is
inadmissible; nothing has settled.

Re-projected at 100%: **2026-08-19 / 08-23 / 09-04** at the 28/14/7-day
placement cadences — all inside the MLB regular season, against **2027-04-25**
at 14.3%. **The real risk is that the placement rate is falling — 66.2 → 53.5
→ 36.0 per week — and nothing in this task explains it.**

`blocked_on: time` → **`measurement`**, with `recheck_after: 2026-08-01`, the
date n reaches 10 and the rate becomes decisive.

---

## Task 8 — gate instrumentation standard · **BUILT and validated backwards**

Nine conditions, each tied to a failure that actually happened here. Validated
against a real `git worktree` at `62c3c12` (2026-07-26):

| gate | 07-26 | today (droplet) |
|---|---|---|
| **P-014** | FAIL — `1_sanctioned_reader`, `7_source_resolvable`, `9_blocked_on` | `1`, `5`, `7` now **PASS** |
| **P-015** | FAIL — `5_emits_progress_and_threshold` = `None/None` | now emits `progress=0 threshold=120` |
| **P-016** | FAIL — no reader, `source: maker_fills` unresolvable | unchanged — **nobody had audited it** |
| **P-017** (control) | FAIL, **1 check** | same |
| P-022 | FAIL | FAIL — see limitation |
| | 16 failing | **11 failing** |

**The two fixes made by hand on 2026-07-28 appear as check transitions between
the trees.** Nobody told the enforcer what was fixed.

**The control does not pass, and the checklist is not wrong.** P-017 fails
exactly `4_decision_rule`: there is **no `P017_DECISION_RULE.md` anywhere**,
and the gate names none. P-017 was the control for *readability* — which is
what the audit measured — and it passes all eight readability checks. It is
not a control for being fully instrumented. Weakening check 4 to make it pass
would have been the failure mode in the other direction.

**Stated limitation:** a static checklist cannot catch P-022's real 2026-07-26
failure, where the instrumentation was fine and the *pod* could not generate
observations. That class needs a live-behaviour watch — which is what
`scripts/p022_window_check.py` is.

---

## Task 7 — branch consolidation · **BLOCKED on the push**

**`git push -u origin p024-mlb-f5-research` was denied by the permission
layer.** I did not work around it. **77 commits — every integrity fix, every
kill, the resolver, the fee fixture, the throughput instrument, all five gate
readers, and everything from tonight — still exist in exactly one place.**

```bash
git push -u origin p024-mlb-f5-research
```

Then verify by fetching into a scratch clone and confirming the tip SHA and
that the resolver, the fee fixture and the throughput instrument are present:

```bash
git rev-parse p024-mlb-f5-research        # compare against origin's tip
git ls-tree -r --name-only HEAD | grep -E 'golf_schedule|kalshi_series_fees|throughput'
```

The tip at the moment of the audit was `17f3778`; it advances by the commits
that land this summary, so compare the branch's own current SHA rather than a
literal from this file.

**Merge posture, and the reasoning:** once pushed, **fast-forward `main`.**
`main` is 0 commits ahead of HEAD, so it is a genuine fast-forward with no
merge commit and no conflict. Every commit is already deployed and running;
there is no reviewer but Sam; and a PR would add a review artifact for code
that has been in production for days. The branch name
`p024-mlb-f5-research` describes a study killed on 2026-07-25 and should not
outlive the merge.

**Everything committed.** The working tree was carrying the two loose research
documents and all 24 `research/prompts/*.md` untracked; they are now in.
`scripts/check_research_committed.sh` passes.

### Branch audit — report, do not delete

| branch | unique vs HEAD | verdict |
|---|---:|---|
| `main`, `golf-settler`, `overnight-research-2026-07-21`, 4× `claude/*` | **0** | fully contained; safe to delete, **Sam's call** |
| `fix-heartbeat-daily-loss-halt` | 0 | contained (3 ahead of `main` only) |
| `p025-chart-markets` | 0 | contained |
| `claude/practical-shamir-1d4220` | **1** | `ecbf15c` P-006/P-002 MLB cross-venue matching — **unique, unmerged** |
| `p016-p017m-vpin` | **1** | `ec1b97e` VPIN + event-clocked markouts — **unique** |
| `p019-longshot-nogo` | **1** | `1acac99` P-019 NO-GO — **unique** |
| `p020-p021-specs` | **1** | `9b67e41` P-020/P-021 specs + R2 map — **unique** |
| `p021-mlb-totals-kill` | **1** | `db58edd` P-021 KILL — **unique** |
| `p022-p023-golf-deadheat-fade` | **5** | the original P-022/P-023 research and pod — **unique** |
| `chore/repo-cleanup-2026-07-22` | **4** | **DO NOT MERGE** — includes `d15f916`, the Legacy extraction |
| `p018-inplay-fade-core` | **6** | **DO NOT MERGE, DO NOT DELETE** — `4ff5bea` cherry-picked tonight; tip removes Legacy |

**Nine branches hold unique commits.** Six of those are research verdicts
(P-019, P-021, P-022/P-023, the specs) whose *reports* are on HEAD but whose
*history* is not. **No branch was deleted; no history was rewritten; no force
push.** No stashes, one worktree (the main checkout), and the temporary
07-26 worktree was removed cleanly.

---

## Deploy — done tonight, **no service restarted**

Every deploy tonight was **sync-only**. The entire diff surface is
cron/timer-invoked observation code, and an unnecessary restart of
`betting-pod-shop` carries the known log-rotation / orphaned-position hazard.

```bash
bash scripts/deploy.sh 129.212.176.202          # sync only, NO restart
```

* **`betting-pod-shop` — NOT restarted** (code unchanged; up since 2026-07-27 20:58:49Z)
* **`betting-round-leader-fade` — NOT restarted** (code unchanged; up since 2026-07-27 17:53:24Z)
  — *named explicitly because `deploy.sh` does not cover it*
* crontab: `*/30` → `8,23,38,53` for `scripts.p022_window_check`; previous
  crontab backed up to `/root/crontab.bak.2026-07-29`
* new: five `evmap-*.service`/`.timer` units installed; **four enabled**,
  `evmap-archive-settled.timer` **disabled**
* venv gained `pandas`, `numpy`, `pyarrow`
* verified: droplet suite green on the new test files, `faults: []`,
  `alert.py --dry-run` renders both new finding families

**Not deployed:** P-018 (inert by design, nothing for it to do there).

---

## Decisions needed from Sam

**New tonight**

1. **Push the branch** (§Task 7). One command. Until it runs, 77 commits exist
   on one laptop.
2. **Accept or re-derive `P014_DECISION_RULE.md`** given the disclosed
   contamination (§9 of that file). My recommendation: accept and record it —
   the alternative trades a small, disclosed, checkable contamination for a
   large and growing one.
3. **`POI26` R1's close reference has no verified margin** and rests on n = 1
   from a US event. Its window opens `2026-07-30T16:00Z`. Watch `close_source`
   in `status.jsonl`; if it is still `tour_day_offset` when the window opens,
   the quote is timed off an uncalibrated constant.
4. **§7's per-tournament cap is not enforced against QUOTED exposure.** 13
   quotes carry $60.45 against a $50 (5%) limit, because sizing subtracts only
   *filled* collateral. If ≥11 fill, **AIGWO26 is excluded from T under §7** —
   a gate condition lost to an accounting inconsistency.
5. **Disable the Mac crontab** — `crontab ~/mac_crontab.bak.2026-07-29`, or
   `crontab -e` and comment the five `kalshi-ev-map` lines. `crontab <file>`
   hung from this session (the same class of macOS permission gate). **No race
   exists today** — TCC keeps all five inert — but this must happen before any
   Full Disk Access grant.
6. **How to unblock the EV-Map archiver**: incremental parquet writes, or seed
   `archive_state.json` to shrink the 10-day window.
7. **P-017 has no decision rule** and one settled tournament at −9.89 ¢/ct. It
   is the only pod with live-shaped evidence and no locked line.
8. **P-016 has no reader and an unresolvable `source: maker_fills`** — P-014's
   defect on a pod labelled `blocked_on: nothing`.
9. **P-018 has no decision rule** and its data gate opens ~2026-07-30. Write it
   before the backtest runs; this is the last week it can be done blind.

**Carried forward from 2026-07-28, unanswered**

10. *(was 5)* **`t_start_utc` for P-022** — documented in Task 2 Item 1, not
    answered. It is also **not implemented as a filter anywhere**.
11. *(was 6)* **Weather-suspended tournaments in T** — Task 2 Item 2. A
    detection rule is now pre-registered and validated; the choice is open,
    and EXCLUDE costs the backtest's five best tournaments.
12. *(was 7)* **Posting above H = 24 h** — Task 2 Item 3. The region is
    **out-of-sample**, not merely conservative.
13. *(was 13)* **Widening approval** (`STATUS_REASSESSMENT` §5.1) was never
    recorded. Still open.

**Closed tonight**

14. *(was 8)* **P-018 orphaned** → **CLOSED.** `4ff5bea` cherry-picked; suite
    green; P-001 verified; pod inert.
15. *(was 9)* **EV-Map hosting** → **CLOSED for 4 of 5 jobs.** Clock running.
16. *(was 10)* **`blocked_on: time` dishonest** → **CLOSED.** A vocabulary
    exists, is enforced, and P-001 → `measurement`, P-018 → `backtest`.

---

## Anomalies and permanent data loss

**Permanent loss**

| loss | quantity | recoverable? |
|---|---|---|
| Weather paper quotes 2026-07-**21** → 07-27 | **7 days**, ~1,200/day | **No** — 90-day horizon, point-in-time. One day longer than previously recorded. |
| Weekly settled-market archive | **all of it — it has never existed** | Partially, inside the horizon, once the OOM is fixed |
| P-018 in-play ticks | none | n/a |

**Anomalies**

* **The 24.00h fingerprint stops 4 min 47 s before the fix deploys.** 152
  instances, then zero. Cleanest before/after this fund has produced.
* **P-001's placement rate is falling**: 66.2 → 53.5 → 36.0 per week. Unexplained.
* **The droplet is a 2 GB box running six workloads** and the archiver found
  the ceiling. Better to know now than when something with a deadline finds it.
* **Every in-band P-022 quote is priced off a one-sided ask**, not a two-sided
  mid. The whole first tournament will run through that code path.
* **`STALL_DAYS["P-022"]` in `manager/throughput.py` says "4-5 golf
  tournaments/week"**; measured 3.16. Same wrong figure as
  `P022_DECISION_RULE.md` §5 and `p001`-adjacent docs — noted in Task 2 §5,
  not edited (stop rule).
* **A correction I made mid-run:** my EV-Map alarm reported the first healthy
  live run as a failure, because a per-day output has no meaningful
  after-minus-before delta. Caught by running it, not by reviewing it.

---

### One line

P-022 is armed and will tell us loudly if it is not; the night's larger
finding is that **three of the four things that looked like calendar problems
were measurement problems** — P-001 was never inert, the EV-Map clock had
never started, and P-014's gate counts a unit four times larger than its own
statistic — and the one thing that is genuinely still at risk is that 77
commits of all of it remain on a single laptop.
