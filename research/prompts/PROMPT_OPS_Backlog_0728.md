# PROMPT — ops backlog, 2026-07-28

Four small items. None is research; all of them are things that bite later if
left. Do them in this order.

## 1. Push the two unpushed commits, and tidy the branch state

`origin/main..HEAD` currently holds **2 commits**, including
`fix(P-014): record fill_price` — a four-month data defect fix that exists in
one place again. Push first, tidy second; a safe ugly state beats a clean loss.

Also outstanding: a merge commit onto `claude/admiring-cohen-eeca92` sits on top
of `main`'s history, and `p024-mlb-f5-research` still points at the same commit
as `main` while naming a study killed on 2026-07-25.

- Push. Verify by fetching into a scratch clone and checking the tip SHA.
- **Report the branch topology before changing it.** If `main` and the session
  branch have diverged, say how, and do not resolve it by force.
- Deleting `p024-mlb-f5-research` is Sam's call — **report, do not delete.**
- `.claude/settings.local.json` is modified and uncommitted; decide whether it
  belongs in the repo at all and say why.

## 2. Disable the Mac crontab

`crontab ~/mac_crontab.bak.2026-07-29`, or `crontab -e` and comment the five
`kalshi-ev-map` lines. Note that `crontab <file>` **hung from a Cowork session**
— the same class of macOS permission gate that produced the original 139
failures — so this may need Sam at a terminal. **Say so plainly rather than
retrying.**

**No race exists today** — TCC keeps all five inert — but this must land before
any Full Disk Access grant, or two collectors race for the same output.

## 3. The EV-Map archiver OOM

The archiver is the **1 of 5** EV-Map jobs still blocked, and the weekly
settled-market archive **has never existed** — that is not a lapsed job, it is a
job that never ran. The droplet is a **2 GB box running six workloads** and the
archiver found the ceiling.

Two candidate fixes from the 07-29 report: **incremental parquet writes**, or
**seeding `archive_state.json` to shrink the 10-day window**. Evaluate both,
implement one, and state the memory headroom afterwards.

**While you are there: measure what the box actually has.** Six workloads on 2 GB
with one known OOM is a capacity question, not an archiver question. Report free
memory under normal load and say whether anything with a deadline — P-022's
runner, the detector, the in-play collector — is at risk. **That finding is worth
more than the archiver fix.**

## 4. Fix the cadence figure in three places

`STALL_DAYS["P-022"]` in `manager/throughput.py` says **"4–5 golf
tournaments/week"**; measured is **3.16**. The same wrong figure appears in
`P022_DECISION_RULE.md` §5 and in P-001-adjacent docs. It was noted on 07-29 and
deliberately not edited under that run's stop rule.

Fix all three to the measured 3.16, cite the measurement, and **re-derive
anything that consumed the wrong number** — in particular the projected
resolution date for P-022 at T = 24. A stall alarm keyed to a cadence 40% too
fast will fire on healthy silence, which is how alarms get ignored.

## Stop rule

No pod behaviour changes. No deletions without Sam. If item 2 needs a human at a
terminal, report that and move on rather than retrying a hanging command.

## Deliverable

A section in the day's run summary — not a standalone report — covering: push
verification, branch topology, crontab status (including "needs Sam" if so), the
archiver fix and the droplet's real memory headroom, and the three cadence
corrections with the re-derived P-022 date.
