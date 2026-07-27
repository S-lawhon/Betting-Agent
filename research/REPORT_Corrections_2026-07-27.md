# Corrections Backlog & Droplet Hygiene

**Task:** `research/prompts/PROMPT_OPS_Corrections_And_Hygiene.md`
**Run:** 2026-07-28 · items 1–6 done · item 7 verified · item 8 quantified, no action

---

## Summary

| # | item | status |
|---|---|---|
| 1 | JDAY scalar re-book | **DONE** — applied to the live log; gate edge −10.08 → **−9.89 ¢/ct** |
| 2 | Two anchor errors in `REPORT_Golf_Quirks_2026-07.md` §4.2 | **DONE** |
| 3 | `KXLEADER` split clause is conditional | **DONE** — corrected in `CLAUDE.md` + the P-026 report |
| 4 | RANKLIST vs award dead-heat regimes | **DONE** — recorded in `CLAUDE.md` |
| 5 | Re-scope H2 | **DONE** |
| 6 | Droplet hygiene | **DONE** — 20 MB → 28 KB; 3 local worktrees pruned |
| 7 | Deploy remnant C | **ALREADY DEPLOYED** — verified by content hash, nothing owed |
| 8 | P-002/P-006 phantom exposure | **VOIDED 2026-07-27 20:58 UTC** on Sam's decision — 282 rows, $4,642.06 → **$0.00** |

Two findings emerged that the brief did not anticipate, and both change what is
owed: **the phantom exposure is 3.5× the cited figure but does NOT reach the
risk guard**, and **an orphaned worktree contained an independent, uncommitted
audit that had already found today's fifth fee drift.**

---

## 1 · JDAY scalar re-book — **applied**

`scripts/apply_golf_scalar_correction.py`, run on the droplet with the engine
stopped. Discipline as specified: narrow allow-list (one `market_id`), refuses
unless exactly one row matches, refuses unless the row carries a known source
state, non-matching lines written back byte-identical, backup + temp file +
fsync.

```
rows           : 54,802
rows matching KXPGATOP20-3MO26-JDAY: 1 (expected 1)
  before : action=VOID outcome=VOID pnl=-0.53 source=kalshi_withdrawn
  after  : action=WIN  outcome=WIN  pnl=-0.10 gross=0.43 source=kalshi_scalar_corrected
backup         : trade_log.jsonl.bak_scalar_correction_20260727T203146Z
```

### It took two passes, and the first one was wrong

**The first pass set `outcome` only — and the gate did not move.** The row read
`WIN` everywhere except the one place that decides P-017's gate:
`p017_checkpoint.tournament_stat` keys off **`action`**, while `trade_store` and
the dashboard key off `outcome`. I checked the gate afterwards rather than
assuming, which is the only reason it was caught:

| | before | after pass 1 | after pass 2 |
|---|---:|---:|---:|
| tournament edge | −10.075 ¢/ct | −10.08 ¢/ct | **−9.89 ¢/ct** |
| positions | 37 | 37 | **38** |
| contracts | 2,276 | 2,276 | **2,319** |
| voids | 1 | **1** | **0** |

This is the repo's signature failure — *right field, wrong call path* — and I
reproduced it while fixing an instance of it. Both fields are now set, the
script accepts the half-corrected state so the job can be finished, and
`tests/test_scalar_correction.py` (8 tests) asserts that `CORRECTED` sets
**both**. Re-running is now an explicit no-op.

Row count preserved exactly (54,802 → 54,802). Delta **+$0.43** as the committed
audit record specifies.

## 2 · §4.2 anchor errors — **corrected**

An inline correction block now sits above the table in
`golf_quirks_research/REPORT_Golf_Quirks_2026-07.md` §4.2, stating:

1. the round-based top-N cells are anchored at **H = 12h, not 48h**;
2. the full-tournament "48h, before R1" anchor is **post-R1 for 58–71%** of the
   control cohort — it is a mid-tournament price for most of the sample.

It explicitly says any figure derived from those cells inherits the error
(including the −3.2¢ control reading, against which **P-023c's conclusions were
drawn**), and that the numbers must be **re-run at a stated anchor rather than
offset-corrected** — the make-cut precedent being that a stale anchor is simply
wrong, not conservatively wrong, and P-023c found the bias runs the other way on
its own cohort.

## 3 · `KXLEADER` split is conditional — **corrected**

Read verbatim from `LEAGUELEADER.pdf` (governs the whole `KXLEADER*` family):

> "In the event of a tie where multiple participants have exactly the same
> `<statistic>` total, **and `<league>` does not declare a single winner through
> official tiebreaker procedures**, the markets for all tied `<participant>`s
> will resolve so "Yes" holders receive $1/[the number of tied `<participant>`s]
> rounded down to the nearest cent…"

The emphasised condition is **absent from every prior write-up**. Most leagues
publish official tiebreakers, so the split may almost never fire, and P-026's
`E[1/n | tie] = 0.43` is an **upper bound on the haircut, not its expectation**.
P-026's KILL verdict is unaffected — it was decided at the $0 pre-trade gate —
but its effect size is overstated. Corrected in `CLAUDE.md` and in
`research/REPORT_P026_Leader_DeadHeat_2026-07-26.md`.

**Verified, and the "first-ever settlement" claim is wrong:** `KXWCGOALLEADER`
(54 settled markets) and `KXLEADERUCLGOALS` (6) have **already settled, both
outright at `$1.0000`**. No scalar. **The split has still never been observed in
the wild.**

## 4 · RANKLIST vs awards — **recorded**

`GOLDENGLOBESNOM.pdf` serves **RANKLIST**, whose ties pay **pro-rata $1/n** —
the *opposite* regime from the award templates, where a tied winner pays
**zero**. Two opposite dead-heat regimes one PDF apart under similar-looking
tickers. Written into `CLAUDE.md` with the instruction to read each series'
`contract_terms_url`, never the ticker name.

## 5 · H2 re-scoped — **done**

`kalshi-ev-map/02_edge_hypotheses.md` H2 asserted the cross-venue basis is
fee-bounded, unconditionally. Now scoped to **the liquid sports head**, with
P-020's finding that the **Brier-minimising weight on Polymarket was 0.0 on a
monotone sweep** — Kalshi is both deeper *and* sharper outside the head, so the
"free fair-value oracle" implication imports noise rather than information there.
Cross-linked to `crossvenue_research/REPORT_CrossVenue_2026-07.md`.

## 6 · Droplet hygiene — **done**

**Droplet:** `/opt/betting-pod-shop/.claude/` held **20 MB / 1,011 `.py` files**
of stale detached-HEAD source, including second copies of `matcher.py` and
`kalshi_fees.py` — exactly the "grep it as if it were live code" hazard. Backed
up to `/root/droplet_claude_worktrees_20260727.tar.gz` (4.7 MB) and removed.

```
before: 20M   after: 28K
find . -name matcher.py  ->  ./Legacy/Kalshi Arb Project/src/matcher.py    (one file)
```

Both services healthy afterwards.

**`manager/state/status.json` was NOT clobbered** — `collected_at
2026-07-27T20:30Z`, `host: Betting-Agent-Project`. It is the droplet's own live
state.

**Local:** three worktrees pruned. `git worktree prune` was a no-op (it only
removes entries whose directories are already gone); `git worktree remove` was
needed.

> **One of them was not safe to delete, and checking is the only reason it
> survived.** `jolly-wilson-864c2e` held **three uncommitted files** —
> `CLAUDE.md`, `src/kalshi_fees.py`, `tests/test_kalshi_fees.py` — containing an
> **independent 2026-07-26 fee-table audit that had already found today's fifth
> drift**: it identifies `KXLPGATOUR`, `KXLIVTOUR` and `KXCHAMPTOUR` as wrongly
> marked charging, with the note *"the LPGA / LIV / Champions tours are NOT
> priced like the PGA Tour, despite the parallel naming."*
>
> That work was never committed and would have been destroyed. The fix itself is
> superseded — today's generated fixture removes the hand table entirely — but
> the finding is **independent corroboration** of today's, arrived at by a
> different route. Preserved as
> `research/corrections/orphaned_fee_audit_worktree_20260726.patch` (287 lines)
> before removal.

## 7 · Deploy remnant C — **already deployed, nothing owed**

Verified by content hash rather than by presence:

| file | droplet md5 | local HEAD md5 |
|---|---|---|
| `manager/brief.py` | `6cf65d1f…` | `6cf65d1f…` |
| `tests/test_manager_collect.py` | `801fdaa0…` | `801fdaa0…` |

`manager/README.md` and `mlb_f5_research/` also present. The queue's carryover
note was right: it shipped in the 2026-07-27 16:00 UTC rsync. **No diff to
report, no deploy needed.**

## 8 · P-002 / P-006 phantom exposure — **quantified, not resolved**

Measured over the full trade log and all archives, counting positions with a
`PLACED` row and no terminal outcome:

| pod | positions | collateral |
|---|---:|---:|
| **P-002** | 204 | **$1,135.36** |
| **P-006** | 78 | **$3,506.70** |
| **TOTAL** | **282** | **$4,642.06** |

Placed between **2026-03-01 and 2026-07-21**.

> **This is 3.5× the ~$1,311 the brief cited.** The brief's figure is close to
> P-002's share alone ($1,135); the full picture includes P-006's $3,507, which
> is the larger half. Whatever produced ~$1,311 was looking at a narrower slice
> — most likely the truncated live log rather than the archives.

### Which views it actually distorts — **not the one the brief expected**

**`AggregateRiskGuard` does NOT see it.** Its bootstrap tracks paper positions
only for pods that have a settler (`settled_pod_ids`), and P-002/P-006 have
none, so they are skipped by design — the behaviour `CLAUDE.md` already
documents as *"Pods with no settler are still skipped — they never drain."*
Confirmed live: `_pod_exposure = {}`, `_venue_exposure = {}`.

So the exposure distorts **log-derived views only** — anything that reconstructs
open positions by walking the trade log (the dashboard's open-position list,
any portfolio-level P&L that nets unsettled rows, and any future reader that
assumes "no terminal outcome" means "still live"). **It does not consume risk
limits and cannot block a trade.** That materially lowers the urgency from what
the brief assumed, and it is the honest correction to make.

### RESOLVED — option 1 (explicit void), applied 2026-07-27 20:58 UTC

Sam chose the explicit void. Applied with `scripts/void_unsettleable_positions.py`,
engine stopped, backup taken.

**It APPENDS terminal rows; no existing line was modified.** That is how
settlement normally works (a settler appends a terminal row after the PLACED
row) and it is the least destructive option — the original history is
byte-identical and the change is undone by deleting the appended block.

```
open (PLACED, no terminal row) across ('P-002', 'P-006'):
  P-002:  204 positions   $  1,135.36
  P-006:   78 positions   $  3,506.70
  TOTAL: 282 positions  $4,642.06
backup   : trade_log.jsonl.bak_void_unsettleable_20260727T205807Z
appended : 282 VOID rows (no existing line was modified)
```

| check | result |
|---|---|
| row count | 55,405 → **55,687** (exactly +282) |
| phantom exposure re-measured | **0 positions, $0.00** |
| re-run | **idempotent no-op** — "nothing open — already voided" |
| all five gates | **unchanged** (P-001 0, P-014 331, P-015 5, P-017 1, P-022 0) |
| services | `betting-pod-shop` and `betting-round-leader-fade` active |

Each row carries `resolution_source: bookkeeping_void_unsettleable` and a
`void_reason` naming the cause, so a future reader can tell it from a real
settlement. VOID is set on **both** `action` and `outcome` — setting one and not
the other is exactly how the JDAY correction silently failed its first pass, and
`tests/test_void_unsettleable.py` asserts it.

P&L is unaffected: a void books $0.00, and every gate statistic in the repo
already excludes VOIDs as "no risk taken".

### The options as they stood before the decision

1. **Explicit void.** Write `VOID` terminal rows for all 282 positions with a
   `resolution_source` naming the reason (no execution venue, never resolvable).
   Log-derived views go clean immediately; P&L is unaffected (VOIDs are excluded
   from every gate statistic as "no risk taken"). Reversible via backup, and the
   same discipline as item 1 applies. **Cost:** rewrites 282 rows of history.
2. **Explicit pod retirement.** Leave the rows and mark P-002/P-006 `retired` in
   `registry.yaml`, with readers taught to exclude retired pods' unsettled rows.
   History untouched; the distortion is handled at read time rather than erased.
   **Cost:** every current and future log-derived reader must honour the flag —
   which is precisely the class of thing this repo has repeatedly failed to do
   in one call path or another.

My read at the time, labelled as opinion: **option 2 is safer in principle and
option 1 is safer in practice for this codebase**, because "teach every reader
to honour a flag" is the exact pattern that produced the settler-scoping bug,
`_close_epoch`, the P-015 reader path and the P-014 missing reader. Sam chose
option 1.

---

## Still owed

* The §4.2 cells (item 2) are **flagged, not re-run.** Re-deriving them at a
  stated, verified anchor is a separate piece of work, and P-023c's conclusions
  stay provisional until it happens.
