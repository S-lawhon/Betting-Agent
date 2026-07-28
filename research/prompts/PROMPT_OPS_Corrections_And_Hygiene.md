# Claude Code Task — OPS: Corrections Backlog & Droplet Hygiene (small items, several load-bearing)

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**). Six owed items from the 2026-07-26 run. Individually small; two of them are wrong facts currently sitting in documents that future research will read as true.

## Task

### 1. Re-book the JDAY scalar row
`KXPGATOP20-3MO26-JDAY` settled `result="scalar"` at `settlement_value_dollars` 0.24 and was booked VOID at $0.00 by the pre-fix settler. Correct: gross +$0.43, net −$0.10 (not −$0.53), and the row moves from **excluded to counted** in the P-017 gate sample. `scripts/backfill_golf_scalar_corrections.py` has already generated the audit record in `research/corrections/`; the **live row still reads VOID**. Apply the correction to the droplet's log with the same discipline as `undo_p017_spurious_voids.py` — backup first, narrow allow-list, refuse to run if the match count is not exactly 1, service stopped for the rewrite. Verified scope: this is the **only** row carrying the buggy signature across the active log and all nine archives.

### 2. Fix two documented anchor errors in `REPORT_Golf_Quirks_2026-07.md` §4.2
Found by exact replication and currently uncorrected:
- Round-based top-N cells are anchored at **H = 12h, not 48h** as the report states.
- The full-tournament "48h, before R1" anchor is **post-R1 for 58–71%** of the control cohort — i.e. it is not a pre-event anchor at all for most of the sample.

Correct the report and add a note that any figure derived from those cells inherits the error. These matter because P-023c's conclusions were drawn against them.

### 3. Correct the KXLEADER split clause — it is conditional
Every prior write-up (project memory, the R4 research report, the P-026 study) states the $1/n dead-heat split **unconditionally**. The actual clause applies only where the league "does not declare a single winner through official tiebreaker procedures." Correct every occurrence you can find and add the conditional text verbatim. Also record: the claim that "~Oct 15 is the first-ever KXLEADER settlement" is **wrong** — `KXWCGOALLEADER` (54 markets) and `KXLEADERUCLGOALS` (6) have already settled, both **outright at $1.0000**. The split has still never been observed in the wild.

### 4. Record the RANKLIST / awards distinction
`GOLDENGLOBESNOM.pdf` is **not** an award template — it serves RANKLIST, whose ties pay **pro-rata $1/n**, the opposite regime from the award templates where a tied winner pays **zero**. Two opposite dead-heat regimes sit one PDF apart under similar tickers. Write this into the golf/quirks reference notes prominently enough that a future census cannot conflate them.

### 5. Re-scope H2 in `kalshi-ev-map/02_edge_hypotheses.md`
H2 currently asserts the Kalshi↔Polymarket basis is fee-bounded. P-020 showed that is true **in the liquid sports head** and false outside it — the Brier-minimising weight on Polymarket was **0.0 on a monotone sweep**, i.e. Kalshi is both the deeper *and* the sharper venue there. Restate H2 with the scope, and cross-link `crossvenue_research/REPORT_CrossVenue_2026-07.md`.

### 6. Droplet hygiene — the deploy exclusions do not clean what is already there
`scripts/deploy.sh` gained `--exclude='.claude/worktrees/'` and `--exclude='manager/state/'`, but **rsync runs without `--delete`**, so:
- **~20 MB of stale detached-HEAD source sits in `/opt/betting-pod-shop/.claude/`**, containing old versions of every source file. A future debugging session can grep it as if it were live code. Remove it on the droplet, and prune the three stale worktrees locally.
- Confirm `manager/state/status.json` on the droplet is the droplet's own live state and was not clobbered by an earlier deploy.

### 7. Deploy remnant C
`manager/` daily-brief work (`brief.py`, `checks.py`, `collect.py`, README, `tests/test_manager_collect.py` — commits `e4768c8`, `f90cfb2`) plus `mlb_f5_research/` are committed and still not deployed. **Report the exact diff and stop.** Deploys are Sam's call.

### 8. P-002 / P-006 phantom exposure — surface it, don't resolve it
The orphan recovery surfaced **~$1,311 of P-002/P-006 exposure with no settler that will ever drain it**. These pods are shelved for lack of Polymarket execution access, so nothing will ever resolve those rows and they sit as permanent phantom exposure in any portfolio-level view. Quantify it precisely, state which views it distorts (`AggregateRiskGuard`, the dashboard, capital allocation), and propose two options — explicit void, or explicit pod retirement. **Do not act.** This is Sam's bookkeeping decision.

## Definition of done
Items 1–6 done and committed; item 7 reported as a diff with no deploy; item 8 quantified with options and no action taken. One short `research/REPORT_Corrections_2026-07-27.md` listing what changed and what is still owed.
