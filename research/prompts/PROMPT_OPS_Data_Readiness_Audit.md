# Claude Code Task — OPS: Data-Readiness Audit on Everything "Blocked on Time"

> In one day this project found a dead settler, a dead pod, and a truncating log rotation — each invisible, each running for days. **"It's collecting" is a claim to test, not a state to assume.**

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

Three workstreams are parked on `blocked_on: time`, meaning they are deliberately silent until their gate fires. That is correct design — but it also means **nobody has verified any of them is actually accumulating.** Every one has a plausible silent-failure mode, and two have hard deadlines inside three weeks.

## Task — audit each, report a real number, do not fix anything you cannot fix safely

### 1. P-018 in-play tick sample (was due ~2026-08-04)
The gate logic (`src/inplay_surprise.py`) and tests are built. The blocker was that `betting-book-capture` had **zero in-play ticks**. Capture started 2026-07-21 with a ~2–3 week estimate.
- How many genuine **in-play** ticks exist now? Not rows — in-play ticks, on markets that were live during a game.
- The unit runs REST at a rate that **caps it at 45 markets and truncates ~23 of ~68 discovered**, lowest-volume-first. Each truncation is written into the data as a DISCOVERY record — **use those to measure the real coverage gap** rather than assuming full coverage.
- The rate was set conservatively during a 429 investigation that has since been root-caused (`kalshi.rate_limit` 5→2). Report whether it can now be raised toward 5.0 req/s — check the unit's 429 count first — and what that would do to the Aug 4 date.
- **Verdict: will the surprise-bucket kill gate be runnable on Aug 4, or not?** Give a date, not a vibe.

### 2. MLB props execution test (needs 27 game-days by ~2026-08-17)
- How many **distinct ET game-days** are actually collected? Re-bucket from each record's UTC `ts` — do not trust filenames.
- The collector had a timezone truncation bug (fixed 2026-07-21 ~20:15 UTC) that quit before first pitch for 5 of 11 start slots, every West Coast game among them. **Treat 07-22 onward as the first clean full-slate days** and report clean vs contaminated day counts separately.
- Days are calendar-bound: a missed day never comes back. Report any gaps and whether the timer's `Persistent=true` actually recovered them.
- **Verdict: does 27 clean game-days land by Aug 17, or has the date slipped?**

### 3. EV-Map Build 2 weather maker (30 days live across ≥10 cities)
This is the one most likely to be quietly broken. Both jobs run on **Mac cron** and **silently skip whenever the machine sleeps**, against a **90-day API horizon where every missed week is calibration data lost forever**. launchd was blocked by root-owned `~/Library/LaunchAgents`.
- `weather_paper_maker` (`13,43 7-19 * * *`) → `kalshi-ev-map/data/paper_quotes.parquet`: how many days actually have quotes? How many distinct cities?
- `weather_archiver` (weekly, `17 3 * * 0`): how many weeks were archived vs elapsed? **Any missing week is permanent data loss** — quantify it.
- **Verdict: how many of the 30 days are genuinely banked, and is this workstream alive or has it been dead for weeks?** If dead, say how much was lost and propose a hosting fix (moving to the droplet is the obvious one).

## Guardrails
- **Read-only.** Do not restart services, do not change cron, do not deploy. If a job is dead, report it with the fix ready to apply.
- Do not "estimate" a count you can compute. Every number in the report must come from data on disk, and say which file it came from.
- If a gate date has slipped, **say so explicitly and give the new date.** A silently slipping deadline is the same failure class as a silently dead settler.

## Definition of done
`research/REPORT_Data_Readiness_2026-07-27.md` with a table: workstream · claimed gate · measured progress · source file · alive/dead · true expected date. Any dead job diagnosed with a proposed fix, uncommitted to production. Explicitly list every permanent data loss found.
